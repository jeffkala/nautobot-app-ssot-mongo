"""Seed the "SSoT MongoDB Owner" MetadataType used to stamp ownership on synced objects."""

from django.db import migrations

# (app_label, model_name) for every synced model. Kept self-contained so the migration does not import the
# diffsync layer; `nautobot_ssot_mongo.tests.test_models` asserts it stays in step with the models.
TRACKED_MODELS = [
    # NDP-owned catalog
    ("dcim", "manufacturer"),
    ("dcim", "platform"),
    ("dcim", "locationtype"),
    ("dcim", "devicetype"),
    ("tenancy", "tenantgroup"),
    ("tenancy", "tenant"),
    ("ipam", "namespace"),
    ("ipam", "vrf"),
    ("extras", "role"),
    ("ipam", "prefix"),
    # NAP-owned inventory
    ("dcim", "location"),
    ("dcim", "rackgroup"),
    ("dcim", "rack"),
    ("dcim", "device"),
    ("dcim", "interface"),
    ("ipam", "vlan"),
    ("ipam", "ipaddress"),
    ("dcim", "cable"),
]

METADATA_TYPE_NAME = "SSoT MongoDB Owner"
METADATA_TYPE_DESCRIPTION = (
    "Identifies the Nautobot instance that owns a synced object. A JSON value with an 'owner' key (the owning "
    "nautobot_instance) and an 'ownership_transferred' key (true once the MongoDB SSoT app synced the object in)."
)


def create_owner_metadatatype(apps, schema_editor):
    """Create the MetadataType and attach every tracked content type.

    Content types are fetched with `get_or_create`: on a fresh database Django only creates `ContentType` rows in
    the `post_migrate` signal, so a plain `get` would skip most models here and stamping them would later fail.
    """
    MetadataType = apps.get_model("extras", "MetadataType")
    ContentType = apps.get_model("contenttypes", "ContentType")

    metadata_type, _ = MetadataType.objects.get_or_create(
        name=METADATA_TYPE_NAME,
        defaults={"data_type": "json", "description": METADATA_TYPE_DESCRIPTION},
    )
    for app_label, model in TRACKED_MODELS:
        content_type, _ = ContentType.objects.get_or_create(app_label=app_label, model=model)
        metadata_type.content_types.add(content_type)


def delete_owner_metadatatype(apps, schema_editor):
    """Remove the MetadataType (and its ObjectMetadata rows) on rollback."""
    MetadataType = apps.get_model("extras", "MetadataType")
    MetadataType.objects.filter(name=METADATA_TYPE_NAME).delete()


class Migration(migrations.Migration):
    """Seed the SSoT MongoDB Owner MetadataType."""

    initial = True

    dependencies = [
        ("extras", "0111_metadata"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(create_owner_metadatatype, delete_owner_metadatatype),
    ]
