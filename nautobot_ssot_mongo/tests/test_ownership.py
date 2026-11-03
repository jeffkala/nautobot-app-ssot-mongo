"""Ownership rules: the guards on the model, their trip through MongoDB and the transfer of a type."""

from unittest.mock import MagicMock, patch

import mongomock
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import override_settings
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Location, LocationType, Manufacturer
from nautobot.extras.models import Status
from nautobot.extras.models.metadata import ObjectMetadata

from nautobot_ssot_mongo.constants import DEFAULT_OWNERSHIP_RULES, OWNERSHIP_RULES_CONTENT_TYPE
from nautobot_ssot_mongo.jobs import MongoDataSource, MongoDataTarget, TransferOwnership
from nautobot_ssot_mongo.models import OwnershipRule, ownership_rules_writable
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME, stamp_owner
from nautobot_ssot_mongo.ownership import apply_rules, publish_rules, pull_rules
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient


def role_settings(**overrides):
    """Return a PLUGINS_CONFIG with this app's settings replaced by `overrides`."""
    return {**settings.PLUGINS_CONFIG, "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", **overrides}}


NDP_SETTINGS = role_settings(nautobot_role="NDP", nautobot_instance="NDP")
NAP_SETTINGS = role_settings(nautobot_role="NAP", nautobot_instance="NAP-Austin", ndp_instance="NDP")


def rule_for(label):
    """Return the rule of the `app_label.model` content type."""
    app_label, model = label.split(".", 1)
    return OwnershipRule.objects.get(content_type__app_label=app_label, content_type__model=model)


def stamps_on(obj):
    """Return the owner stamps attached to `obj`."""
    return ObjectMetadata.objects.filter(
        metadata_type__name=METADATA_TYPE_NAME,
        assigned_object_type=ContentType.objects.get_for_model(obj),
        assigned_object_id=obj.pk,
    )


def mongo_client():
    """Return a client over an in-memory MongoDB with the indexes created."""
    client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
    client.ensure_indexes()
    return client


class OwnershipRuleModelTestCase(TestCase):
    """The seeded rules and the two guards in `clean()`."""

    def test_default_rules_are_seeded_in_sync_order(self):
        rules = list(OwnershipRule.objects.all())

        self.assertEqual(
            [(rule.content_type_label, rule.owner_role) for rule in rules],
            [(f"{app_label}.{model}", role) for app_label, model, role in DEFAULT_OWNERSHIP_RULES],
        )
        self.assertEqual([rule.sync_order for rule in rules], list(range(1, len(DEFAULT_OWNERSHIP_RULES) + 1)))
        self.assertTrue(all(rule.enabled for rule in rules))

    def test_labels(self):
        rule = rule_for("ipam.ipaddress")

        self.assertEqual(str(rule), "ipam.ipaddress")
        self.assertEqual(rule.model_label, "IP Address")
        self.assertEqual(rule.natural_key(), ["ipam", "ipaddress"])

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_ndp_can_disable_and_reorder_a_rule(self):
        rule = rule_for("ipam.prefix")
        rule.enabled = False
        rule.sync_order = 42

        rule.validated_save()

        rule.refresh_from_db()
        self.assertFalse(rule.enabled)
        self.assertEqual(rule.sync_order, 42)

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_owner_change_is_refused_outside_the_transfer_job(self):
        rule = rule_for("dcim.location")
        rule.owner_role = "NDP"

        with self.assertRaises(ValidationError) as context:
            rule.validated_save()
        self.assertIn("Transfer Ownership", str(context.exception))
        with ownership_rules_writable():
            rule.validated_save()
        self.assertEqual(rule_for("dcim.location").owner_role, "NDP")

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_nap_cannot_edit_rules_except_when_mirroring_the_ndp(self):
        rule = rule_for("ipam.prefix")
        rule.enabled = False

        with self.assertRaises(ValidationError) as context:
            rule.validated_save()
        self.assertIn("Edit this rule on the NDP", str(context.exception))
        with ownership_rules_writable():
            rule.validated_save()
        self.assertFalse(rule_for("ipam.prefix").enabled)

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_only_supported_content_types_are_accepted(self):
        rule = OwnershipRule(content_type=ContentType.objects.get_for_model(Status), owner_role="NDP", sync_order=99)

        with self.assertRaises(ValidationError):
            rule.validated_save()


class RulesThroughMongoTestCase(TestCase):
    """The NDP publishes the rules as documents; a NAP mirrors them and settles owner changes."""

    def setUp(self):
        super().setUp()
        self.client = mongo_client()
        self.log = MagicMock()

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_publish_writes_one_document_per_rule_and_retires_removed_rules(self):
        self.assertEqual(publish_rules(self.client, "NDP", log=self.log), len(DEFAULT_OWNERSHIP_RULES))
        payloads = pull_rules(self.client, "NDP")
        self.assertEqual([payload["content_type"] for payload in payloads][:2], ["dcim.manufacturer", "dcim.platform"])
        self.assertEqual(
            payloads[0], {"content_type": "dcim.manufacturer", "owner_role": "NDP", "enabled": True, "sync_order": 1}
        )

        rule_for("dcim.cable").delete()
        publish_rules(self.client, "NDP", log=self.log)

        self.assertEqual(len(pull_rules(self.client, "NDP")), len(DEFAULT_OWNERSHIP_RULES) - 1)
        cable = self.client.get("NDP", OWNERSHIP_RULES_CONTENT_TYPE, "dcim.cable", include_deleted=True)
        self.assertTrue(cable["deleted"])

    def test_pull_returns_nothing_before_the_ndp_published(self):
        self.assertEqual(pull_rules(self.client, "NDP"), [])

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_apply_makes_the_local_rules_match_the_payloads(self):
        payloads = [
            {"content_type": f"{app_label}.{model}", "owner_role": role, "enabled": True, "sync_order": order}
            for order, (app_label, model, role) in enumerate(DEFAULT_OWNERSHIP_RULES, start=1)
            if model != "cable"
        ]
        payloads[-1]["enabled"] = False  # ipam.ipaddress
        rule_for("ipam.prefix").delete()

        counts = apply_rules(payloads, self.client, "NAP-Austin", "NAP", log=self.log)

        self.assertEqual(counts, {"created": 1, "updated": 1, "deleted": 1, "transferred": 0})
        self.assertFalse(OwnershipRule.objects.filter(content_type__model="cable").exists())
        self.assertEqual(rule_for("ipam.prefix").sync_order, 10)
        self.assertFalse(rule_for("ipam.ipaddress").enabled)

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_apply_skips_a_type_this_version_cannot_sync(self):
        payloads = [{"content_type": "extras.status", "owner_role": "NDP", "enabled": True, "sync_order": 1}]

        counts = apply_rules(payloads, self.client, "NAP-Austin", "NAP", log=self.log)

        self.assertEqual(counts["created"], 0)
        self.assertFalse(OwnershipRule.objects.filter(content_type__model="status").exists())
        self.assertEqual(OwnershipRule.objects.count(), 0)
        self.log.warning.assert_called_once()

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_nap_releases_the_objects_of_a_type_it_now_owns(self):
        cisco = Manufacturer.objects.create(name="Cisco")
        stamp_owner(cisco, "NDP")
        payloads = [{"content_type": "dcim.manufacturer", "owner_role": "NAP", "enabled": True, "sync_order": 1}]

        counts = apply_rules(payloads, self.client, "NAP-Austin", "NAP", log=self.log)

        self.assertEqual(counts["transferred"], 1)
        self.assertEqual(rule_for("dcim.manufacturer").owner_role, "NAP")
        self.assertFalse(stamps_on(cisco).exists())
        self.assertTrue(Manufacturer.objects.filter(pk=cisco.pk).exists())

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_nap_retires_its_documents_of_a_type_it_no_longer_owns(self):
        site = LocationType.objects.create(name="Site")
        austin = Location.objects.create(name="Austin", location_type=site, status=Status.objects.get(name="Active"))
        self.client.upsert("NAP-Austin", "dcim.location", "Austin__None", {"name": "Austin"})
        self.client.upsert("NAP-Boston", "dcim.location", "Boston__None", {"name": "Boston"})
        payloads = [{"content_type": "dcim.location", "owner_role": "NDP", "enabled": True, "sync_order": 1}]

        apply_rules(payloads, self.client, "NAP-Austin", "NAP", log=self.log)

        self.assertTrue(self.client.get("NAP-Austin", "dcim.location", "Austin__None", include_deleted=True)["deleted"])
        self.assertFalse(self.client.get("NAP-Boston", "dcim.location", "Boston__None")["deleted"])
        self.assertTrue(Location.objects.filter(pk=austin.pk).exists())


class TransferOwnershipJobTestCase(TestCase):
    """The NDP-side job flips the rule, settles its own copies and publishes the rules."""

    def setUp(self):
        super().setUp()
        self.client = mongo_client()
        patcher = patch.object(MongoSyncClient, "from_settings", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.job = TransferOwnership()

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_moving_a_type_to_the_ndp_releases_the_copies_it_pulled(self):
        site = LocationType.objects.create(name="Site")
        austin = Location.objects.create(name="Austin", location_type=site, status=Status.objects.get(name="Active"))
        stamp_owner(austin, "NAP-Austin")

        self.job.run(rule=rule_for("dcim.location"), dryrun=False)

        self.assertEqual(rule_for("dcim.location").owner_role, "NDP")
        self.assertFalse(stamps_on(austin).exists())
        published = {payload["content_type"]: payload for payload in pull_rules(self.client, "NDP")}
        self.assertEqual(published["dcim.location"]["owner_role"], "NDP")
        self.assertEqual(len(published), len(DEFAULT_OWNERSHIP_RULES))

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_moving_a_type_to_the_naps_retires_the_ndp_documents(self):
        self.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco"})

        self.job.run(rule=rule_for("dcim.manufacturer"), dryrun=False)

        self.assertEqual(rule_for("dcim.manufacturer").owner_role, "NAP")
        self.assertTrue(self.client.get("NDP", "dcim.manufacturer", "Cisco", include_deleted=True)["deleted"])

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_dry_run_changes_nothing(self):
        self.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco"})

        self.job.run(rule=rule_for("dcim.manufacturer"), dryrun=True)

        self.assertEqual(rule_for("dcim.manufacturer").owner_role, "NDP")
        self.assertFalse(self.client.get("NDP", "dcim.manufacturer", "Cisco")["deleted"])
        self.assertEqual(pull_rules(self.client, "NDP"), [])

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_transfer_is_refused_on_a_nap(self):
        with self.assertRaises(ValueError):
            self.job.run(rule=rule_for("dcim.manufacturer"), dryrun=False)
        self.assertEqual(rule_for("dcim.manufacturer").owner_role, "NDP")


class SyncJobsAndRulesTestCase(TestCase):
    """The NDP's push publishes the rules; a NAP's jobs refresh them before loading anything."""

    def setUp(self):
        super().setUp()
        self.client = mongo_client()
        patcher = patch.object(MongoSyncClient, "from_settings", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    @override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
    def test_ndp_push_publishes_the_rules_before_applying_the_diff(self):
        job = MongoDataTarget()
        job.sync = None
        job.source_adapter = MagicMock()
        job.target_adapter = MagicMock(client=self.client)
        job.diffsync_flags = 0
        job.record_changelog = True

        job.execute_sync()

        self.assertEqual(len(pull_rules(self.client, "NDP")), len(DEFAULT_OWNERSHIP_RULES))
        job.source_adapter.sync_to.assert_called_once()

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_nap_job_mirrors_the_published_rules_and_loads_accordingly(self):
        publish_rules(self.client, "NDP")
        self.client.upsert(
            "NDP",
            OWNERSHIP_RULES_CONTENT_TYPE,
            "ipam.prefix",
            {"content_type": "ipam.prefix", "owner_role": "NDP", "enabled": False, "sync_order": 10},
        )
        job = MongoDataSource()
        job.sync = None

        job.refresh_ownership_rules()
        job.load_source_adapter()

        self.assertFalse(rule_for("ipam.prefix").enabled)
        self.assertNotIn("prefix", job.source_adapter.top_level)
        self.assertIn("manufacturer", job.source_adapter.top_level)

    @override_settings(PLUGINS_CONFIG=NAP_SETTINGS)
    def test_nap_keeps_its_rules_when_the_ndp_has_not_published_any(self):
        job = MongoDataSource()
        job.sync = None
        job.logger.warning = MagicMock()

        job.refresh_ownership_rules()

        self.assertEqual(OwnershipRule.objects.count(), len(DEFAULT_OWNERSHIP_RULES))
        job.logger.warning.assert_called_once()
