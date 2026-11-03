# Architecture Decision Records

The intention is to document deviations from a standard Model View Controller (MVC) design.

## Object ownership follows the Nautobot role, and the assignment is data

Two kinds of Nautobot instance share one MongoDB collection: a single federated NDP (Nautobot Data Platform) and
any number of local NAPs (Nautobot Automation Platforms).
Rather than giving every instance a full bidirectional sync, each object type has exactly one owning role. By
default the NDP owns the global reference data (manufacturers, platforms, device types, location types, tenancy,
namespaces, VRFs, roles, prefixes) and a NAP owns its inventory (locations, rack groups, racks, devices,
interfaces, VLANs, IP addresses, cables). An instance only loads the objects it owns into MongoDB and only syncs
in the objects the other role owns.

The assignment is stored, one `OwnershipRule` per content type (`nautobot_ssot_mongo.models`), rather than
hard-coded. The rule names the owning role, whether the type is synced at all and its position in the sync order,
and the model's one-to-one link to the content type keeps the one-owner-per-type invariant as a database
constraint. The DiffSync model pairs stay in code (`nautobot_ssot_mongo.diffsync.models`) as the catalog of types
the app *can* sync; `DEFAULT_OWNERSHIP_RULES` in `nautobot_ssot_mongo.constants` lists them with their default
owner and seeds the rules on install. The owner-specific adapters (`NDPMongoAdapter`, `NAPMongoAdapter` and their
Nautobot counterparts in `nautobot_ssot_mongo.diffsync.adapters`) build `top_level` from the enabled rules of
their role, mirroring the "SSoT NDP adapter" and "SSoT NAP adapter" of the original sequence diagram.

## The rules are defined on the NDP and travel through MongoDB

Every instance must apply the same rules, or a NAP would publish a type the NDP also publishes and the two would
overwrite each other. The rules are therefore edited on the NDP only (`OwnershipRule.clean()` rejects writes on a
NAP), published by the NDP's "Nautobot to MongoDB" run as documents of the `nautobot_ssot_mongo.ownershiprule`
content type in its own partition, and mirrored by every NAP at the start of each of its sync jobs, before any
adapter reads them. The alternative, a configuration setting per instance, would have to be kept in step by hand
across the whole fleet.

Changing the owner of a type moves objects between instances, so it is not an edit but a job: **Transfer
Ownership**, on the NDP, flips the rule, releases the copies the NDP had pulled (or retires the documents it had
published), and publishes the rules. Each NAP settles its side when it next mirrors the rules: it releases its
pulled copies when its role becomes the owner, or retires its published documents when it stops being the owner.
The objects themselves then move through the ordinary publish and pull runs, with the adoption path matching the
existing copies.

## The NDP discovers NAPs from the collection

The NDP reads NAP-owned objects from the `nautobot_instance` partitions whose documents carry `nautobot_role: NAP`
(every one of them when the job's *Nautobot instances* field is left empty) instead of from a configured list of
NAP names. A new NAP therefore appears on the NDP as soon as it publishes, and the collection is the only place
where the set of instances has to be maintained. A NAP, in turn, reads NDP-owned objects from the single partition
named by `ndp_instance`. Documents written before the role field existed carry no role and still count as NAPs.

## Pulls reconcile owner-stamped objects instead of deleting everything unowned

Every object synced in is stamped with an `ObjectMetadata` of the **SSoT MongoDB Owner** type naming the publishing
`nautobot_instance`, and a pull loads only the local objects stamped with one of the instances it reads. The
alternative, loading every local object of the synced types, made the NDP delete any location it created itself and
made a NAP delete the default `Global` namespace. The stamp also lets a pull *adopt* a matching local object (update
it and stamp it) instead of failing on a uniqueness constraint. The cost is one metadata row per synced object and
the rule that the metadata type must not be edited by hand.

## Deletions are tombstones with a TTL

A deleted object's document is kept with `deleted: true` and `deleted_at` instead of being removed. Readers skip
tombstones, so the consumer's diff is the same as for a hard delete, but the document keeps its identity: a
re-created object revives it in place (the unique index would otherwise reject a second insert), and operators can
see what was retired and when. A TTL index on `deleted_at`, scoped to tombstones, bounds the collection's growth;
`tombstone_ttl_days` controls it and `0` disables expiry.

## Only major versions are enforced across instances

Every document records the publisher's Nautobot, `nautobot-ssot` and app versions. A pull fails when a document's
major version differs from the local one, because the document shape is only guaranteed within a major. Mixed minor
versions across documents only log a warning: the NDP would otherwise fail whenever two NAPs are on different patch
releases, which is the normal state of a fleet mid-upgrade.
