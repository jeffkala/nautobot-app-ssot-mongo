"""Create the app's schema and seed data: the owner metadata type and the default ownership rules."""

import uuid

import django.core.serializers.json
import django.db.models.deletion
import nautobot.extras.models.mixins
from django.db import migrations, models

import nautobot_ssot_mongo.models

# (app_label, model_name, owner_role) for every synced model, in sync order, frozen at the time this migration was
# written. A migration must seed the same rows on every database it runs on, so this list never changes; a later change
# to `nautobot_ssot_mongo.constants.DEFAULT_OWNERSHIP_RULES` ships with a new data migration instead.
# `nautobot_ssot_mongo.tests.test_models` asserts the two stay equal.
DEFAULT_RULES = (
    # hub-owned catalog
    ("dcim", "manufacturer", "HUB"),
    ("dcim", "platform", "HUB"),
    ("dcim", "locationtype", "HUB"),
    ("dcim", "devicetype", "HUB"),
    ("tenancy", "tenantgroup", "HUB"),
    ("tenancy", "tenant", "HUB"),
    ("ipam", "namespace", "HUB"),
    ("ipam", "vrf", "HUB"),
    ("extras", "role", "HUB"),
    ("ipam", "prefix", "HUB"),
    # spoke-owned inventory
    ("dcim", "location", "SPOKE"),
    ("dcim", "rackgroup", "SPOKE"),
    ("dcim", "rack", "SPOKE"),
    ("dcim", "device", "SPOKE"),
    ("dcim", "interface", "SPOKE"),
    ("ipam", "vlan", "SPOKE"),
    ("ipam", "ipaddress", "SPOKE"),
    ("dcim", "cable", "SPOKE"),
)
# (app_label, model_name) for every synced model: the content types the owner metadata type is attached to.
TRACKED_MODELS = [(app_label, model) for app_label, model, _ in DEFAULT_RULES]

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


def seed_ownership_rules(apps, schema_editor):
    """Create one rule per federated content type with the app's default owner and sync order."""
    OwnershipRule = apps.get_model("nautobot_ssot_mongo", "OwnershipRule")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for sync_order, (app_label, model, owner_role) in enumerate(DEFAULT_RULES, start=1):
        content_type, _ = ContentType.objects.get_or_create(app_label=app_label, model=model)
        OwnershipRule.objects.get_or_create(
            content_type=content_type,
            defaults={"owner_role": owner_role, "enabled": True, "sync_order": sync_order},
        )


def delete_ownership_rules(apps, schema_editor):
    """Remove every rule on rollback."""
    OwnershipRule = apps.get_model("nautobot_ssot_mongo", "OwnershipRule")
    OwnershipRule.objects.all().delete()


class Migration(migrations.Migration):
    """Initial schema and seed data."""

    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("extras", "0111_metadata"),
    ]

    operations = [
        migrations.CreateModel(
            name="OwnershipRule",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                (
                    "_custom_field_data",
                    models.JSONField(blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
                ),
                ("owner_role", models.CharField(max_length=5)),
                ("enabled", models.BooleanField(default=True)),
                ("sync_order", models.PositiveSmallIntegerField()),
                (
                    "content_type",
                    models.OneToOneField(
                        limit_choices_to=nautobot_ssot_mongo.models.supported_content_types,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ssot_mongo_ownership_rule",
                        to="contenttypes.contenttype",
                    ),
                ),
            ],
            options={
                "verbose_name": "ownership rule",
                "ordering": ["sync_order", "content_type__app_label", "content_type__model"],
            },
            bases=(
                nautobot.extras.models.mixins.DataComplianceModelMixin,
                nautobot.extras.models.mixins.DynamicGroupMixin,
                nautobot.extras.models.mixins.NotesMixin,
                models.Model,
            ),
        ),
        migrations.RunPython(create_owner_metadatatype, delete_owner_metadatatype),
        migrations.RunPython(seed_ownership_rules, delete_ownership_rules),
    ]
