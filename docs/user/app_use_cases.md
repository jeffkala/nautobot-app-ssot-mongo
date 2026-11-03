# Using the App

This document describes common use-cases and scenarios for this App.

## General Usage

Both jobs live under **Jobs > Jobs > MongoDB SSoT** and accept the standard SSoT options:

- **Dry run**: compute and display the diff without writing to the target system.
- **Memory profiling**: record memory usage while the job runs.
- **Debug**: log one line per content type as documents are loaded from MongoDB, and record unchanged objects in the sync log.
- **Record change log** (MongoDB to Nautobot only): on by default. Turn it off for the first pull of a large inventory to skip the change log entry for every written object; see [Running at Scale](../admin/install.md#running-at-scale).

What each job moves depends on the `nautobot_role` setting and on the ownership rules (**Apps > MongoDB SSoT >
Ownership Rules**). The job detail page on the SSoT dashboard lists the role, the instance name and the object
types involved under *Configuration*.

## Use-cases and common workflows

### Publish the catalog from the hub

On the hub, run **Nautobot to MongoDB** on a schedule. Each run upserts one document per manufacturer, platform,
location type, device type, tenant group, tenant, namespace, VRF, role and prefix under the hub's
`nautobot_instance`, and turns the documents of objects that no longer exist into tombstones, so the collection
always mirrors the hub's catalog.

### Bootstrap or refresh a spoke from the hub

On a spoke, run **MongoDB to Nautobot**. The job reads the hub's partition (`hub_instance`) and creates, updates or
deletes the matching catalog objects in the spoke, in dependency order: manufacturers before platforms and device
types, location types before their children, namespaces before VRFs and prefixes. A catalog object the spoke already
has (for example the default `Global` namespace) is adopted and stamped with the hub as owner rather than duplicated.

!!! note
    Only objects stamped with the hub as owner are reconciled, so a manufacturer or role created by hand on the spoke
    is left alone. Run with *Dry run* first to review the diff all the same, and keep the catalog mastered on the hub.

### Publish a spoke's inventory

On a spoke, run **Nautobot to MongoDB**. The job upserts one document per location, rack group, rack, named device,
interface, VLAN, IP address and interface-to-interface cable under the spoke's `nautobot_instance`. Devices reference
their device type, role, platform, status, tenant and location by name, so the spoke must have mirrored the hub's
catalog first.

### Consolidate the spokes on the hub

On the hub, run **MongoDB to Nautobot** and pick the spoke instances to sync in from the *Nautobot instances* field
(every spoke that has published appears there; leaving it empty reads them all). The job reads their inventory in one
pass and creates, updates or deletes the matching objects in the hub, parents first. Each object is stamped with the
spoke that owns it, visible under *Object Metadata* on its detail page.

!!! note
    Only objects previously synced in from the selected spokes are reconciled. Locations or devices maintained directly
    on the hub are never removed by the pull.

### Check on the federation

**Apps > MongoDB SSoT > Federated Instances** lists every instance that has completed a sync job, with its role, the
time of its last publish and last pull, its Nautobot and app versions and the number of documents it has published per
object type. Open an instance for the per-type breakdown. The rows are refreshed by every sync job on this instance,
so run any job (a dry run is enough) to pick up heartbeats written since the last run. A spoke whose *last publish*
stops advancing, or whose document counts drop unexpectedly, is worth a look before the next consolidation on the
hub.

### Leave a type out of the sync

On the hub, open **Apps > MongoDB SSoT > Ownership Rules**, edit the rule of the type and uncheck *Enabled*. The
next **Nautobot to MongoDB** run on the hub publishes the rules, and every spoke picks them up at the start of its
next sync job, so the type stops moving in either direction everywhere. Documents already published stay in
MongoDB untouched; re-enable the rule to resume. The same page lets you change a type's position in the sync
order, which must keep every type after the types its objects reference.

!!! note
    On a spoke the rules are read-only: they are refreshed from the hub's partition by both sync jobs, even on a dry
    run, and a change made on a spoke is rejected.

### Change which role owns a type

Ownership is changed with the **Transfer Ownership** job on the hub, never by editing the rule's owner, because
the objects of that type have to move between instances. Run the job with *Dry run* checked first; it names the
type and the direction. Without *Dry run* the job:

1. flips the rule's owner,
2. settles the hub's own copies: when the hub becomes the owner, it removes the owner stamps from the objects it
   had pulled, which turns them into local objects; when the hub stops being the owner, it tombstones the documents
   it had published for that type,
3. publishes the rules to MongoDB.

Every spoke settles its side when it next runs either sync job: it removes the hub's stamps from its copies when
the spokes become the owners, or tombstones the documents it had published when the hub becomes the owner. The
objects then move through the ordinary runs: **Nautobot to MongoDB** on the new owners, then **MongoDB to
Nautobot** on the other side, where the existing copies are adopted and stamped rather than duplicated.

!!! warning
    Moving a catalog type to the spokes means every spoke publishes its copy of the same objects, and the hub adopts
    whichever document it reads last for a given key. Make sure the copies agree, or keep such types on the hub.

### Retire an object

Delete the object on its owner and run **Nautobot to MongoDB** there: its document becomes a tombstone. The next
**MongoDB to Nautobot** run on the other role no longer sees the document and deletes its copy. MongoDB expires the
tombstone after `tombstone_ttl_days`; re-creating the object before then revives the same document.

### Add a new spoke

Install the app on the new instance with `nautobot_role` set to `SPOKE`, a unique `nautobot_instance` and the
`hub_instance` of the hub. Run **MongoDB to Nautobot** to import the catalog (the job first mirrors the ownership
rules the hub published), then **Nautobot to MongoDB** to publish the spoke's inventory. No change is needed on the
hub: a pull with the *Nautobot instances* field left empty reads every spoke partition, and once any job has run on
the hub the new spoke is listed in that field and under **Federated Instances**.

### Retire a spoke

Remove the spoke's documents from the collection, for example with `db.objects.deleteMany({nautobot_instance:
"SPOKE-Austin"})` in `mongosh`. The next **MongoDB to Nautobot** run on the hub no longer finds the partition, so it
deletes the objects it had synced in from that spoke, and the next sync job on every instance removes the spoke from
**Federated Instances**. The spoke's own Nautobot keeps its data.

## Screenshots

Both jobs appear on the Nautobot SSoT dashboard, which shows the last sync status, duration and diff for each
direction.
