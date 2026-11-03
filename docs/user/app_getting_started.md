# Getting Started with the App

This document provides a step-by-step tutorial on how to get the App going and how to use it.

## Install the App

To install the App, please follow the instructions detailed in the [Installation Guide](../admin/install.md).

## First steps with the App

1. Configure the MongoDB connection and the role of the instance (`nautobot_role`, `nautobot_instance` and, on a
   spoke, `hub_instance`) in `PLUGINS_CONFIG` as described in the
   [Installation Guide](../admin/install.md#app-configuration) and restart Nautobot.
2. Navigate to **Jobs > Jobs**, open **MongoDB SSoT**, and enable the `MongoDB to Nautobot` and
   `Nautobot to MongoDB` jobs (jobs are disabled by default). On the hub, also enable `Transfer Ownership` if you
   intend to move a type between roles, and review **Apps > MongoDB SSoT > Ownership Rules**: the default rules
   give the hub the catalog and the spokes the inventory.
3. On the hub, run **Nautobot to MongoDB** with *Dry run* checked. The job loads the catalog from Nautobot and from
   MongoDB and shows the resulting diff without changing anything. Run it again with *Dry run* unchecked to publish
   the documents; the database, collection and indexes are created automatically on first use.
4. On each spoke, run **MongoDB to Nautobot** to import the catalog (objects the spoke already has are adopted and stamped
   with the hub as owner), then **Nautobot to MongoDB** to publish the spoke's inventory.
5. Back on the hub, run **MongoDB to Nautobot**, selecting the spokes to mirror in the *Nautobot instances* field (all
   of them when left empty). Each synced object shows its owning spoke under *Object Metadata*.
6. Open **Plugins > Single Source of Truth > Dashboard** to review the sync history and the per-object diff.

### Trying both roles in the development environment

The repository ships a helper script that seeds a collection with the hub's reference data (under the instance
`HUB`) and the inventory of two sample spokes (`SPOKE-Austin` and `SPOKE-Boston`). It only depends on
`pymongo`. In the Docker development environment, which bundles a MongoDB service, run:

```shell
invoke seed-mongo --reset
```

Against any other server, run the script directly (it also honours the `NAUTOBOT_SSOT_MONGO_URI`,
`NAUTOBOT_SSOT_MONGO_USERNAME` and `NAUTOBOT_SSOT_MONGO_PASSWORD` environment variables):

```shell
python development/bin/seed_mongo_sample_data.py --uri mongodb://localhost:27017/ --reset
```

The development instance is configured as a spoke named `SPOKE-Dev` by default, so running **MongoDB to Nautobot**
imports the seeded manufacturers, platforms, location types, device types, tenants, namespaces, VRFs, roles,
prefixes and IP addresses. Create a location and a device and run **Nautobot to MongoDB** to publish them under
`SPOKE-Dev`.

To act as the hub instead, set `NAUTOBOT_SSOT_MONGO_ROLE=HUB` and `NAUTOBOT_SSOT_MONGO_INSTANCE=HUB` in
`development/creds.env`, restart the containers and run **MongoDB to Nautobot**: the job mirrors the locations and
devices published by `SPOKE-Austin`, `SPOKE-Boston` and `SPOKE-Dev`. The reference data imported in the previous step is
what makes those devices resolvable.

## What are the next steps?

- Schedule **Nautobot to MongoDB** on the hub and on every spoke, and **MongoDB to Nautobot** on every instance,
  through **Jobs > Scheduled Jobs**. Run the hub's publish job before the spokes' import jobs, and the spokes' publish
  jobs before the hub's import job.
- Add a spoke by installing the app with a new, unique `nautobot_instance`; the hub picks it up automatically.

You can check out the [Use Cases](app_use_cases.md) section for more examples.
