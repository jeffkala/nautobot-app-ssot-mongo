# Architecture Decision Records

The intention is to document deviations from a standard Model View Controller (MVC) design.

## Object ownership follows the Nautobot role

Two kinds of Nautobot instance share one MongoDB collection: a single federated NDP (Nautobot Data Platform) and
any number of local NAPs (Nautobot Automation Platforms).
Rather than giving every instance a full bidirectional sync, each object type has exactly one owning role. The
NDP owns the global reference data (manufacturers, platforms, device types, location types, tenancy, namespaces,
VRFs, roles, prefixes); a NAP owns its inventory (locations, rack groups, racks, devices, interfaces, VLANs, IP
addresses, cables). An instance only loads the objects it
owns into MongoDB and only syncs in the objects the other role owns.

This keeps the two jobs generic: `nautobot_role` selects the owning role per direction (`nautobot_ssot_mongo.roles`)
and the owner-specific adapters (`NDPMongoAdapter`, `NAPMongoAdapter` and their Nautobot counterparts in
`nautobot_ssot_mongo.diffsync.adapters`) narrow `top_level` accordingly, mirroring the "SSoT NDP adapter" and
"SSoT NAP adapter" of the original sequence diagram. It also avoids write
conflicts, because no two instances ever upsert the same `(nautobot_instance, content_type, key)` document.

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
