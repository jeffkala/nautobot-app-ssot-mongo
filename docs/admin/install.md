# Installing the App in Nautobot

Here you will find detailed instructions on how to **install** and **configure** the App within your Nautobot environment.

## Prerequisites

- The app is compatible with Nautobot 3.2.0 and higher.
- Databases supported: PostgreSQL, MySQL
- The [Nautobot SSoT app](https://docs.nautobot.com/projects/ssot/en/latest/) (`nautobot-ssot`) 4.0.0 or higher.
- A reachable MongoDB server (MongoDB 4.4 or higher; tested with MongoDB 8).

!!! note
    Please check the [dedicated page](compatibility_matrix.md) for a full compatibility matrix and the deprecation policy.

### Access Requirements

The Nautobot web and Celery worker processes need network access to the MongoDB server. The MongoDB user needs
read and write access to the configured database and collection, including permission to create an index on the
collection (`readWrite` on the database is sufficient).

## Install Guide

!!! note
    Apps can be installed manually or using Python's `pip`. See the [nautobot documentation](https://docs.nautobot.com/projects/core/en/stable/plugins/#install-the-package) for more details. The pip package name for this app is [`nautobot-ssot-mongo`](https://pypi.org/project/nautobot-ssot-mongo/).

The app is available as a Python package via PyPI and can be installed with `pip`:

```shell
pip install nautobot-ssot-mongo
```

To ensure Nautobot SSoT MongoDB is automatically re-installed during future upgrades, create a file named `local_requirements.txt` (if not already existing) in the Nautobot root directory (alongside `requirements.txt`) and list the `nautobot-ssot-mongo` package:

```shell
echo nautobot-ssot-mongo >> local_requirements.txt
```

Once installed, the app needs to be enabled in your Nautobot configuration. The following block of code below shows the additional configuration required to be added to your `nautobot_config.py` file:

- Append `"nautobot_ssot_mongo"` to the `PLUGINS` list.
- Append the `"nautobot_ssot_mongo"` dictionary to the `PLUGINS_CONFIG` dictionary and override any defaults.

```python
# In your nautobot_config.py
import os

PLUGINS = [
    "nautobot_ssot",
    "nautobot_ssot_mongo",
]

PLUGINS_CONFIG = {
    "nautobot_ssot": {
        "hide_example_jobs": True,
    },
    "nautobot_ssot_mongo": {
        "mongo_uri": os.getenv("NAUTOBOT_SSOT_MONGO_URI", "mongodb://localhost:27017/"),
        "mongo_database": os.getenv("NAUTOBOT_SSOT_MONGO_DATABASE", "nautobot_sync"),
        "mongo_collection": os.getenv("NAUTOBOT_SSOT_MONGO_COLLECTION", "objects"),
        "mongo_username": os.getenv("NAUTOBOT_SSOT_MONGO_USERNAME", ""),
        "mongo_password": os.getenv("NAUTOBOT_SSOT_MONGO_PASSWORD", ""),
        "mongo_auth_source": os.getenv("NAUTOBOT_SSOT_MONGO_AUTH_SOURCE", "admin"),
        # "HUB" on the data platform, "SPOKE" on each automation platform.
        "nautobot_role": os.getenv("NAUTOBOT_SSOT_MONGO_ROLE", "HUB"),
        "nautobot_instance": os.getenv("NAUTOBOT_SSOT_MONGO_INSTANCE", "HUB"),
        "hub_instance": os.getenv("NAUTOBOT_SSOT_MONGO_HUB_INSTANCE", "HUB"),
    },
}
```

A typical spoke configuration differs only in the last three settings:

```python
        "nautobot_role": "SPOKE",
        "nautobot_instance": "SPOKE-Austin",
        "hub_instance": "HUB",
```

Once the Nautobot configuration is updated, run the Post Upgrade command (`nautobot-server post_upgrade`) to run migrations and clear any cache:

```shell
nautobot-server post_upgrade
```

Then restart (if necessary) the Nautobot services which may include:

- Nautobot
- Nautobot Workers
- Nautobot Scheduler

```shell
sudo systemctl restart nautobot nautobot-worker nautobot-scheduler
```

## App Configuration

The app behavior can be controlled with the following list of settings:

| Key | Example | Default | Description |
| --- | --- | --- | --- |
| `mongo_uri` | `"mongodb://mongo.example.com:27017/"` | `"mongodb://localhost:27017/"` | MongoDB connection URI. Credentials may be embedded in the URI instead of using the username/password settings below. |
| `mongo_database` | `"nautobot_sync"` | `"nautobot_sync"` | Database that holds the synced documents. |
| `mongo_collection` | `"objects"` | `"objects"` | Collection that holds the synced documents. |
| `mongo_username` | `"nautobot"` | `""` | Username used to authenticate. Leave empty when the server does not enforce authentication or the URI already carries credentials. |
| `mongo_password` | `"secret"` | `""` | Password used with `mongo_username`. Load it from an environment variable or a Nautobot Secret, never from source. |
| `mongo_auth_source` | `"admin"` | `"admin"` | Authentication database used with `mongo_username`/`mongo_password`. |
| `mongo_timeout_ms` | `5000` | `5000` | Server selection timeout in milliseconds. |
| `nautobot_role` | `"SPOKE"` | `"HUB"` | Role of this instance. `HUB` is the single federated instance: it owns and publishes the global catalog and syncs in every spoke's inventory. `SPOKE` owns and publishes its inventory (locations, racks, devices, interfaces, VLANs, IP addresses and cables) and mirrors the hub's catalog. Any other value stops Nautobot from starting. |
| `nautobot_instance` | `"SPOKE-Austin"` | `"HUB"` | Name of this instance, stored in every document it publishes. Must be unique across all instances sharing the collection. On a spoke it must differ from `hub_instance`. |
| `hub_instance` | `"HUB"` | `"HUB"` | `nautobot_instance` of the hub. A spoke reads the catalog from this partition; the hub ignores the setting. |
| `tombstone_ttl_days` | `30` | `30` | Days a soft-deleted (tombstone) document is kept before MongoDB expires it through a TTL index. `0` keeps tombstones forever. |

### Document Layout

Every synced object is stored as its own document, tagged with the `nautobot_instance` and `nautobot_role` that own
it. The unique index `instance_content_type_key_unique` on `(nautobot_instance, content_type, key)` and the
`tombstone_ttl` TTL index are created automatically the first time a job runs. The hub reads spoke-owned objects from
the partitions selected in the job (every partition published by a spoke when none is selected), so a new spoke only has
to publish once to appear on the hub; no hub configuration change is needed.

```json
{
    "nautobot_instance": "HUB",
    "nautobot_role": "HUB",
    "content_type": "dcim.devicetype",
    "key": "C9300-48P__Cisco",
    "data": {
        "model": "C9300-48P",
        "manufacturer__name": "Cisco",
        "part_number": "C9300-48P",
        "u_height": 1,
        "is_full_depth": false
    },
    "deleted": false,
    "updated_at": "2026-10-23T20:00:00Z",
    "nautobot_version": "3.2.7",
    "nautobot_apps": {"nautobot_ssot": "4.7.0", "nautobot_ssot_mongo": "0.2.0"}
}
```

- `content_type` is the Nautobot content type label (`app_label.model`).
- `key` is the DiffSync unique identifier: the object's identifier values joined with `__`.
- `data` holds the identifier and attribute fields of the matching DiffSync model. Related objects are referenced
  by name using Django's double-underscore notation (for example `manufacturer__name`).
- `deleted` marks a tombstone; a tombstone also carries `deleted_at`, which the TTL index expires. Readers skip
  tombstones, and an upsert for the same key revives the document in place.
- `nautobot_role`, `nautobot_version` and `nautobot_apps` describe the publisher. A pull refuses documents whose
  major version of Nautobot, `nautobot-ssot` or this app differs from its own, so upgrade all instances together.

## Running at Scale

A sync job loads every object of the models that it syncs into memory on both sides. It then computes the diff and applies it. The push job writes to MongoDB in batches of 100 operations per content type, so a full push of a spoke with 3,000 devices and 24,000 interfaces is a few hundred round trips, not one per object.

The pull job still writes one object at a time into Nautobot, through full model validation. Each object also gets an `SSoT MongoDB Owner` metadata row and an SSoT sync log entry. The database therefore bounds a pull. A first pull of a spoke with 3,000 devices, 24,000 interfaces and 32,000 objects in all takes minutes, not seconds. A re-pull with nothing changed is bounded by reading MongoDB and the local objects.

A sync job is single-threaded. To pull several spokes faster, schedule one pull job per spoke instead of a selection of every spoke in one job. The owner scoping keeps the jobs from touching each other's objects, and Celery runs them on separate workers in parallel.

Nautobot's change log is the largest share of the per-object cost. Every save serializes the object twice for its `ObjectChange` entry. When the job ends, Nautobot walks every entry that the job wrote to drive webhooks, job hooks and events. The pull job has a **Record change log** option, on by default, for exactly that case. Turn it off for the first pull of a large inventory, where the change log only repeats what the SSoT sync log already records. Objects written without a change log get their first entry on their next change, and the pre-change snapshot is captured then. While the option is off, no webhooks or job hooks fire for the objects that a sync writes. Therefore, leave the option on for steady-state syncs that other systems watch.

| Setting | Where | Guidance |
| ------- | ----- | -------- |
| Job time limits | Each job's edit view, **Soft time limit** and **Time limit** overrides | The two jobs default to a 60 minute soft limit and a 65 minute hard limit, instead of Nautobot's 5 and 10 minutes. Raise the overrides when a full pull of a large spoke needs more time. The job is killed at the hard limit. A re-run starts from the current state, so only time is lost. |
| Batched writes | Push job | Writes go to MongoDB 100 at a time. A write the server rejects fails the job; the writes before it in that batch are kept, and a re-run resumes from the current state because the diff is computed again. |
| `tombstone_ttl_days` | `PLUGINS_CONFIG` | Deleted objects stay in MongoDB as tombstones until the TTL index expires them. For an inventory with churn, keep a TTL so that old tombstones do not accumulate. |
| Worker memory | Celery worker | Budget roughly 1 GiB of worker memory per 100,000 synced objects, on top of Nautobot's baseline. |
| Change log | Pull job's **Record change log** checkbox | On by default. Turn it off for the first pull of a large inventory. This skips one `ObjectChange` per written object and the post-sync processing of those entries. See above for what is lost. |
| Log volume | Job **debug** checkbox | Unchanged objects are written to the sync log only when debug is on. Leave debug off for large syncs. The job log still records one line per written batch. |

One limit comes from the data model, not from the settings. Locations are identified by name alone. Therefore, every spoke that feeds one hub must use location names that are unique across all of these spokes.
