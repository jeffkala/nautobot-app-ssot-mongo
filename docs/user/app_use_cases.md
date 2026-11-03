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

### Publish the catalog from the NDP

On the NDP, run **Nautobot to MongoDB** on a schedule. Each run upserts one document per manufacturer, platform,
location type, device type, tenant group, tenant, namespace, VRF, role and prefix under the NDP's
`nautobot_instance`, and turns the documents of objects that no longer exist into tombstones, so the collection
always mirrors the NDP's catalog.

### Bootstrap or refresh a NAP from the NDP

On a NAP, run **MongoDB to Nautobot**. The job reads the NDP's partition (`ndp_instance`) and creates, updates or
deletes the matching catalog objects in the NAP, in dependency order: manufacturers before platforms and device
types, location types before their children, namespaces before VRFs and prefixes. A catalog object the NAP already
has (for example the default `Global` namespace) is adopted and stamped with the NDP as owner rather than duplicated.

!!! note
    Only objects stamped with the NDP as owner are reconciled, so a manufacturer or role created by hand on the NAP
    is left alone. Run with *Dry run* first to review the diff all the same, and keep the catalog mastered on the NDP.

### Publish a NAP's inventory

On a NAP, run **Nautobot to MongoDB**. The job upserts one document per location, rack group, rack, named device,
interface, VLAN, IP address and interface-to-interface cable under the NAP's `nautobot_instance`. Devices reference
their device type, role, platform, status, tenant and location by name, so the NAP must have mirrored the NDP's
catalog first.

### Consolidate the NAPs on the NDP

On the NDP, run **MongoDB to Nautobot** and pick the NAP instances to sync in from the *Nautobot instances* field
(every NAP that has published appears there; leaving it empty reads them all). The job reads their inventory in one
pass and creates, updates or deletes the matching objects in the NDP, parents first. Each object is stamped with the
NAP that owns it, visible under *Object Metadata* on its detail page.

!!! note
    Only objects previously synced in from the selected NAPs are reconciled. Locations or devices maintained directly
    on the NDP are never removed by the pull.

### Leave a type out of the sync

On the NDP, open **Apps > MongoDB SSoT > Ownership Rules**, edit the rule of the type and uncheck *Enabled*. The
next **Nautobot to MongoDB** run on the NDP publishes the rules, and every NAP picks them up at the start of its
next sync job, so the type stops moving in either direction everywhere. Documents already published stay in
MongoDB untouched; re-enable the rule to resume. The same page lets you change a type's position in the sync
order, which must keep every type after the types its objects reference.

!!! note
    On a NAP the rules are read-only: they are refreshed from the NDP's partition by both sync jobs, even on a dry
    run, and a change made on a NAP is rejected.

### Change which role owns a type

Ownership is changed with the **Transfer Ownership** job on the NDP, never by editing the rule's owner, because
the objects of that type have to move between instances. Run the job with *Dry run* checked first; it names the
type and the direction. Without *Dry run* the job:

1. flips the rule's owner,
2. settles the NDP's own copies: when the NDP becomes the owner, it removes the owner stamps from the objects it
   had pulled, which turns them into local objects; when the NDP stops being the owner, it tombstones the documents
   it had published for that type,
3. publishes the rules to MongoDB.

Every NAP settles its side when it next runs either sync job: it removes the NDP's stamps from its copies when
the NAPs become the owners, or tombstones the documents it had published when the NDP becomes the owner. The
objects then move through the ordinary runs: **Nautobot to MongoDB** on the new owners, then **MongoDB to
Nautobot** on the other side, where the existing copies are adopted and stamped rather than duplicated.

!!! warning
    Moving a catalog type to the NAPs means every NAP publishes its copy of the same objects, and the NDP adopts
    whichever document it reads last for a given key. Make sure the copies agree, or keep such types on the NDP.

### Retire an object

Delete the object on its owner and run **Nautobot to MongoDB** there: its document becomes a tombstone. The next
**MongoDB to Nautobot** run on the other role no longer sees the document and deletes its copy. MongoDB expires the
tombstone after `tombstone_ttl_days`; re-creating the object before then revives the same document.

### Add a new NAP

Install the app on the new instance with `nautobot_role` set to `NAP`, a unique `nautobot_instance` and the
`ndp_instance` of the NDP. Run **MongoDB to Nautobot** to import the catalog (the job first mirrors the ownership
rules the NDP published), then **Nautobot to MongoDB** to publish the NAP's inventory. No change is needed on the
NDP: the new partition appears in the *Nautobot instances* field of its **MongoDB to Nautobot** job.

## Screenshots

Both jobs appear on the Nautobot SSoT dashboard, which shows the last sync status, duration and diff for each
direction.
