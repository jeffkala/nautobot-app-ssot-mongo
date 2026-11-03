"""Helpers for stamping the owner metadata on synced records.

Ownership is stamped only during a MongoDB -> Nautobot sync, at the DiffSync create/update write path (see
`LocalOwnerMixin`). The originating `nautobot_instance` is resolved from the MongoDB source adapter and written via
`stamp_owner`. Ordinary local saves (GUI, Design Builder, direct ORM) do not stamp anything, which is what lets the
pull jobs tell synced objects apart from objects an instance created itself.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata

logger = logging.getLogger(__name__)

METADATA_TYPE_NAME = "SSoT MongoDB Owner"


def job_logger(adapter):
    """Return the logger of the job running `adapter`, or this module's logger outside a job."""
    return getattr(getattr(adapter, "job", None), "logger", None) or logger


def stamp_owner(instance, owner_value, log=None) -> None:
    """Upsert the owner ObjectMetadata for `instance` to `owner_value`.

    The metadata is a JSON value with two keys:

    * `owner`: the originating Nautobot instance name.
    * `ownership_transferred`: `True`, because reaching this helper means SSoT wrote (transferred) the record during
      a MongoDB -> Nautobot sync.

    Creates the metadata row if missing, or updates it when the value changed. No-ops when `owner_value` is empty or
    the MetadataType is not present, and logs a warning instead of raising when the MetadataType does not allow the
    record's content type, so one misconfigured type cannot abort a whole sync. Pass the job's logger as `log` so
    those warnings reach the job result.
    """
    if not owner_value:
        return
    log = log or logger

    try:
        metadata_type = MetadataType.objects.get(name=METADATA_TYPE_NAME)
    except MetadataType.DoesNotExist:
        log.warning("MetadataType %r not found; skipping owner stamp on %s", METADATA_TYPE_NAME, instance._meta.label)
        return

    new_value = {"owner": owner_value, "ownership_transferred": True}
    content_type = ContentType.objects.get_for_model(instance)
    existing = ObjectMetadata.objects.filter(
        metadata_type=metadata_type,
        assigned_object_type=content_type,
        assigned_object_id=instance.pk,
    ).first()
    try:
        if existing is None:
            obj_metadata = ObjectMetadata(metadata_type=metadata_type, assigned_object=instance, scoped_fields=[])
            obj_metadata.value = new_value
            obj_metadata.validated_save()
        elif existing.value != new_value:
            existing.value = new_value
            existing.validated_save()
    except ValidationError as error:
        log.warning(
            "Could not stamp %r owner on %s %s: %s", METADATA_TYPE_NAME, instance._meta.label, instance.pk, error
        )
