"""Move the ownership rules between instances through MongoDB, and settle an owner change locally.

The hub publishes its rules as documents of the `nautobot_ssot_mongo.ownershiprule` content type in its own
partition, next to the catalog. A spoke reads them before each sync job and makes its local rules match, so the
sync jobs on every instance agree on which types move in which direction and in what order.
"""

import logging
from typing import Any, Dict, List, Optional

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from nautobot.extras.models.metadata import ObjectMetadata

from nautobot_ssot_mongo.constants import OWNERSHIP_RULES_CONTENT_TYPE
from nautobot_ssot_mongo.models import OwnershipRule, ownership_rules_writable
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient

logger = logging.getLogger(__name__)

RULE_FIELDS = ("owner_role", "enabled", "sync_order")


def rule_payload(rule: OwnershipRule) -> Dict[str, Any]:
    """Return the `data` payload that describes `rule` in MongoDB."""
    return {"content_type": rule.content_type_label, **{field: getattr(rule, field) for field in RULE_FIELDS}}


def publish_rules(
    client: MongoSyncClient, nautobot_instance: str, metadata: Optional[Dict[str, Any]] = None, log=None
) -> int:
    """Write every ownership rule into `nautobot_instance`'s partition and tombstone the rules that no longer exist.

    Returns the number of rules published.
    """
    log = log or logger
    rules = list(OwnershipRule.objects.select_related("content_type"))
    published = {rule.content_type_label for rule in rules}
    operations = [
        client.upsert_operation(
            nautobot_instance, OWNERSHIP_RULES_CONTENT_TYPE, rule.content_type_label, rule_payload(rule), metadata
        )
        for rule in rules
    ]
    stale = [
        document["key"]
        for document in client.find(nautobot_instance, OWNERSHIP_RULES_CONTENT_TYPE)
        if document["key"] not in published
    ]
    operations.extend(
        client.delete_operation(nautobot_instance, OWNERSHIP_RULES_CONTENT_TYPE, key, metadata) for key in stale
    )
    client.write_batch(operations)
    log.info("Published %d ownership rule(s) to MongoDB; retired %d.", len(rules), len(stale))
    return len(rules)


def pull_rules(client: MongoSyncClient, hub_instance: str) -> List[Dict[str, Any]]:
    """Return the rule payloads the hub published, in sync order, or `[]` when it has not published any."""
    return sorted(client.find_data(hub_instance, OWNERSHIP_RULES_CONTENT_TYPE), key=lambda data: data["sync_order"])


def apply_rules(  # pylint: disable=too-many-arguments,too-many-locals
    payloads: List[Dict[str, Any]],
    client: MongoSyncClient,
    nautobot_instance: str,
    local_role: str,
    metadata: Optional[Dict[str, Any]] = None,
    log=None,
) -> Dict[str, int]:
    """Make the local ownership rules match `payloads` and settle every owner change they carry.

    Rules missing locally are created, differing ones updated and rules the hub retired are deleted. A rule whose
    owner changed also goes through `apply_owner_change`. A payload naming a content type this version of the app
    does not support is skipped with a warning. Returns the counts of rules created, updated, deleted and
    transferred.
    """
    log = log or logger
    counts = {"created": 0, "updated": 0, "deleted": 0, "transferred": 0}
    with ownership_rules_writable():
        existing = {rule.content_type_label: rule for rule in OwnershipRule.objects.select_related("content_type")}
        seen = set()
        for payload in payloads:
            label = payload["content_type"]
            seen.add(label)
            values = {field: payload[field] for field in RULE_FIELDS}
            rule = existing.get(label)
            if rule is None:
                app_label, model = label.split(".", 1)
                rule = OwnershipRule(content_type=ContentType.objects.get(app_label=app_label, model=model), **values)
                try:
                    rule.validated_save()
                except ValidationError as error:
                    log.warning("Skipped ownership rule for %s, which this app version cannot sync: %s", label, error)
                    continue
                counts["created"] += 1
                continue
            previous_role = rule.owner_role
            if any(getattr(rule, field) != value for field, value in values.items()):
                for field, value in values.items():
                    setattr(rule, field, value)
                rule.validated_save()
                counts["updated"] += 1
            if previous_role != rule.owner_role:
                apply_owner_change(
                    rule.content_type, rule.owner_role, client, nautobot_instance, local_role, metadata, log
                )
                counts["transferred"] += 1
        for label, rule in existing.items():
            if label not in seen:
                rule.delete()
                counts["deleted"] += 1
    log.info(
        "Ownership rules refreshed from the hub: %d created, %d updated, %d deleted, %d transferred.",
        counts["created"],
        counts["updated"],
        counts["deleted"],
        counts["transferred"],
    )
    return counts


def apply_owner_change(  # pylint: disable=too-many-arguments
    content_type: ContentType,
    new_role: str,
    client: MongoSyncClient,
    nautobot_instance: str,
    local_role: str,
    metadata: Optional[Dict[str, Any]] = None,
    log=None,
) -> int:
    """Settle this instance's side of `content_type` passing to `new_role`, and return how many objects it touched.

    When this instance's role becomes the owner, the objects of that type it synced in from the previous owner are
    released: their owner stamps are removed, so they are local objects that the next "Nautobot to MongoDB" run
    publishes. When this instance's role stops being the owner, the documents it published for that type are
    tombstoned, so the new owners' publications replace them; its local objects stay and are adopted by the next
    "MongoDB to Nautobot" run.
    """
    log = log or logger
    label = f"{content_type.app_label}.{content_type.model}"
    if new_role == local_role:
        _, per_model = ObjectMetadata.objects.filter(
            metadata_type__name=METADATA_TYPE_NAME, assigned_object_type=content_type
        ).delete()
        released = per_model.get(ObjectMetadata._meta.label, 0)
        log.info(
            "Released %d %s object(s): now owned here and published by the next Nautobot to MongoDB run.",
            released,
            label,
        )
        return released
    retired = client.tombstone_content_type(nautobot_instance, label, metadata)
    log.info(
        "Retired %d %s document(s) this instance had published; the %s instances now own them.",
        retired,
        label,
        new_role,
    )
    return retired
