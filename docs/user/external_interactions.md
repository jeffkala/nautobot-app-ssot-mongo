# External Interactions

This document describes external dependencies and prerequisites for this App to operate, including system requirements, API endpoints, interconnection or integrations to other applications or services, and similar topics.

## External System Integrations

### From the App to Other Systems

The app connects to a MongoDB server using [PyMongo](https://pymongo.readthedocs.io/). Connections are opened by
the Celery worker that executes the SSoT jobs, using the URI and credentials from `PLUGINS_CONFIG`.

Operations performed against MongoDB:

- `createIndex` on the configured collection (once, idempotent).
- `distinct` on `nautobot_instance` when the hub discovers which spokes have published data.
- `find` filtered by `content_type` and one or more `nautobot_instance` values when loading data.
- `bulkWrite` of `updateOne` operations, `upsert=True` for created or updated documents and a soft-delete update
  for deleted ones, a hundred at a time.
- `updateMany` when a type passes to the other role, to tombstone the documents this instance had published for it.
- `aggregate` (`$match` and `$group`) on this instance's partition to count its live documents per content type for
  the heartbeat written at the end of every successful sync job.

Besides the object documents, the app keeps two kinds of document of its own in the collection, in the writer's
partition: the ownership rules published by the hub (`content_type: "nautobot_ssot_mongo.ownershiprule"`, one per
rule) and one heartbeat per instance (`content_type: "nautobot_ssot_mongo.federatedinstance"`, keyed by the
instance name), written at the end of every successful sync job:

```json
{
    "nautobot_instance": "SPOKE-Austin",
    "nautobot_role": "SPOKE",
    "content_type": "nautobot_ssot_mongo.federatedinstance",
    "key": "SPOKE-Austin",
    "data": {
        "name": "SPOKE-Austin",
        "role": "SPOKE",
        "last_publish": "2026-11-03T20:05:05.939Z",
        "last_pull": "2026-11-03T19:58:41.120Z",
        "nautobot_version": "3.2.8",
        "app_versions": {"nautobot_ssot": "4.0.0", "nautobot_ssot_mongo": "0.1.0a0"},
        "document_counts": {"dcim.location": 20, "dcim.device": 200, "dcim.interface": 400}
    },
    "updated_at": "2026-11-03T20:05:05.939Z"
}
```

Other systems may read the heartbeats to monitor the federation; they should not write them.

### From Other Systems to the App

No inbound integration is required. Other systems interact with the shared MongoDB collection directly.

## Nautobot REST API endpoints

The app adds two endpoints: `/api/plugins/ssot-mongo/ownership-rules/` for the ownership rules (read-only in
practice on a spoke, where writes are rejected) and `/api/plugins/ssot-mongo/federated-instances/`, read-only, for
the instance registry. The sync jobs can be started through the standard Nautobot Jobs REST API, for example:

```shell
curl -X POST \
  -H "Authorization: Token $NAUTOBOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": {"dryrun": true, "memory_profiling": false, "debug": false}}' \
  https://nautobot.example.com/api/extras/jobs/nautobot_ssot_mongo.jobs.MongoDataSource/run/
```
