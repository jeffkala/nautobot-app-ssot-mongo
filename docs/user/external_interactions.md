# External Interactions

This document describes external dependencies and prerequisites for this App to operate, including system requirements, API endpoints, interconnection or integrations to other applications or services, and similar topics.

## External System Integrations

### From the App to Other Systems

The app connects to a MongoDB server using [PyMongo](https://pymongo.readthedocs.io/). Connections are opened by
the Celery worker that executes the SSoT jobs, using the URI and credentials from `PLUGINS_CONFIG`.

Operations performed against MongoDB:

- `createIndex` on the configured collection (once, idempotent).
- `distinct` on `nautobot_instance` when the NDP discovers which NAPs have published data.
- `find` filtered by `content_type` and one or more `nautobot_instance` values when loading data.
- `updateOne` with `upsert=True` when creating or updating a document.
- `deleteOne` when deleting a document.

### From Other Systems to the App

No inbound integration is required. Other systems interact with the shared MongoDB collection directly.

## Nautobot REST API endpoints

This app does not add REST API endpoints. The sync jobs can be started through the standard Nautobot Jobs REST API,
for example:

```shell
curl -X POST \
  -H "Authorization: Token $NAUTOBOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"data": {"dryrun": true, "memory_profiling": false, "debug": false}}' \
  https://nautobot.example.com/api/extras/jobs/nautobot_ssot_mongo.jobs.MongoDataSource/run/
```
