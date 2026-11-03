"""Keep the registry of federated instances in step with the heartbeat documents in MongoDB.

Every instance writes one heartbeat document at the end of each successful sync job: keyed by its own name, stored in
its own partition under the `nautobot_ssot_mongo.federatedinstance` content type, and carrying when it last published
and pulled, the versions it runs and how many live documents it has published per type. Every sync job also refreshes
the local `FederatedInstance` rows from all the heartbeats in the collection, so each instance holds the same view of
the federation without being told about the others.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from nautobot_ssot_mongo.constants import CONTROL_CONTENT_TYPES, INSTANCE_HEARTBEAT_CONTENT_TYPE
from nautobot_ssot_mongo.models import FederatedInstance
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient

logger = logging.getLogger(__name__)

PUBLISH = "last_publish"
PULL = "last_pull"


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """Return `value` as an aware UTC datetime; MongoDB hands back the naive UTC datetimes it stores."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def write_heartbeat(  # pylint: disable=too-many-arguments
    client: MongoSyncClient,
    nautobot_instance: str,
    role: str,
    finished: str,
    metadata: Dict[str, Any],
    log=None,
) -> Dict[str, Any]:
    """Record in MongoDB that this instance just finished a sync job, and return the heartbeat payload written.

    `finished` is `PUBLISH` or `PULL` and names the timestamp set to now; the other one is carried over from the
    previous heartbeat. The payload also carries the versions from `metadata` and a count of the live documents in
    this instance's partition per content type.
    """
    log = log or logger
    previous = client.get(nautobot_instance, INSTANCE_HEARTBEAT_CONTENT_TYPE, nautobot_instance)
    previous_data = previous["data"] if previous else {}
    payload = {
        "name": nautobot_instance,
        "role": role,
        PUBLISH: previous_data.get(PUBLISH),
        PULL: previous_data.get(PULL),
        "nautobot_version": metadata.get("nautobot_version", ""),
        "app_versions": metadata.get("nautobot_apps") or {},
        "document_counts": client.count_by_content_type(nautobot_instance, exclude_content_types=CONTROL_CONTENT_TYPES),
    }
    payload[finished] = datetime.now(timezone.utc)
    client.upsert(nautobot_instance, INSTANCE_HEARTBEAT_CONTENT_TYPE, nautobot_instance, payload, metadata=metadata)
    log.info(
        "Recorded this instance's heartbeat in MongoDB: %d live document(s) published.",
        sum(payload["document_counts"].values()),
    )
    return payload


def refresh_registry(client: MongoSyncClient, log=None) -> Dict[str, int]:
    """Make the `FederatedInstance` rows match the heartbeat documents in MongoDB.

    Instances with a heartbeat but no row are created, rows whose heartbeat changed are updated, and rows whose
    heartbeat is gone (the partition was purged) are deleted. Returns the counts of rows created, updated and deleted.
    """
    log = log or logger
    counts = {"created": 0, "updated": 0, "deleted": 0}
    existing = {instance.name: instance for instance in FederatedInstance.objects.all()}
    seen = set()
    for document in client.find(client.instances(), INSTANCE_HEARTBEAT_CONTENT_TYPE):
        data = document["data"]
        values = {
            "role": data["role"],
            PUBLISH: _aware(data.get(PUBLISH)),
            PULL: _aware(data.get(PULL)),
            "last_seen": _aware(document.get("updated_at")),
            "nautobot_version": data.get("nautobot_version", ""),
            "app_versions": data.get("app_versions") or {},
            "document_counts": data.get("document_counts") or {},
        }
        seen.add(data["name"])
        instance = existing.get(data["name"])
        if instance is None:
            FederatedInstance(name=data["name"], **values).validated_save()
            counts["created"] += 1
        elif any(getattr(instance, field) != value for field, value in values.items()):
            for field, value in values.items():
                setattr(instance, field, value)
            instance.validated_save()
            counts["updated"] += 1
    for name, instance in existing.items():
        if name not in seen:
            instance.delete()
            counts["deleted"] += 1
    log.info(
        "Federated instance registry refreshed: %d created, %d updated, %d deleted.",
        counts["created"],
        counts["updated"],
        counts["deleted"],
    )
    return counts
