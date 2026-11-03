"""Seed one OwnershipRule per federated content type, with the app's default owner and sync order."""

from django.db import migrations

from nautobot_ssot_mongo.constants import DEFAULT_OWNERSHIP_RULES


def seed_ownership_rules(apps, schema_editor):
    """Create the default rules, leaving any rule that already exists untouched.

    Content types are fetched with `get_or_create` because on a fresh database Django only creates `ContentType`
    rows in the `post_migrate` signal.
    """
    OwnershipRule = apps.get_model("nautobot_ssot_mongo", "OwnershipRule")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for sync_order, (app_label, model, owner_role) in enumerate(DEFAULT_OWNERSHIP_RULES, start=1):
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
    """Seed the default ownership rules."""

    dependencies = [
        ("nautobot_ssot_mongo", "0002_ownershiprule"),
    ]

    operations = [
        migrations.RunPython(seed_ownership_rules, delete_ownership_rules),
    ]
