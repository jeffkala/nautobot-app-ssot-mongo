"""Test the MongoDB remote adapter and remote models against an in-memory MongoDB."""

from unittest.mock import MagicMock, patch

import mongomock
from nautobot.apps.testing import TestCase
from pymongo.errors import BulkWriteError

from nautobot_ssot_mongo.constants import MONGO_BATCH_LIMIT
from nautobot_ssot_mongo.diffsync.adapters import (
    TOP_LEVEL,
    HubMongoAdapter,
    HubNautobotAdapter,
    MongoRemoteAdapter,
    SpokeMongoAdapter,
    SpokeNautobotAdapter,
    owned_model_names,
    parents_first,
    sort_related_dicts,
)
from nautobot_ssot_mongo.models import OwnershipRule, ownership_rules_writable
from nautobot_ssot_mongo.roles import get_role
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient

INSTANCE = "Nautobot-A"


def build_adapter(instance=INSTANCE, adapter_class=MongoRemoteAdapter, **kwargs):
    """Return a MongoDB adapter backed by mongomock."""
    client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
    client.ensure_indexes()
    job = MagicMock()
    job.debug = False
    return adapter_class(job=job, sync=None, client=client, nautobot_instance=instance, **kwargs)


class MongoRemoteAdapterTestCase(TestCase):
    """Test MongoRemoteAdapter.load()."""

    def setUp(self):
        super().setUp()
        self.adapter = build_adapter()
        self.client = self.adapter.client

    def test_top_level_models_all_declare_a_content_type(self):
        for model_name in TOP_LEVEL:
            with self.subTest(model=model_name):
                self.assertTrue(getattr(self.adapter, model_name)._content_type)  # pylint: disable=protected-access

    def test_load_reads_every_content_type(self):
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": "Cisco Systems"})
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Arista", {"name": "Arista", "description": ""})
        self.client.upsert(
            INSTANCE,
            "dcim.devicetype",
            "C9300-48P__Cisco",
            {"model": "C9300-48P", "manufacturer__name": "Cisco", "part_number": "C9300-48P", "u_height": 1},
        )
        self.client.upsert(
            INSTANCE,
            "ipam.ipaddress",
            "10.0.0.1__Global",
            {"host": "10.0.0.1", "parent__namespace__name": "Global", "mask_length": 32, "dns_name": "r1"},
        )

        self.adapter.load()

        self.assertEqual({m.get_unique_id() for m in self.adapter.get_all("manufacturer")}, {"Cisco", "Arista"})
        self.assertEqual(self.adapter.get("manufacturer", "Cisco").description, "Cisco Systems")
        devicetype = self.adapter.get("devicetype", "C9300-48P__Cisco")
        self.assertEqual(devicetype.part_number, "C9300-48P")
        self.assertTrue(devicetype.is_full_depth)
        self.assertEqual(self.adapter.get("ipaddress", "10.0.0.1__Global").dns_name, "r1")
        self.assertEqual(self.adapter.get_all("platform"), [])

    def test_load_ignores_unknown_fields_in_documents(self):
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco", "legacy_field": "ignored"})

        self.adapter.load()

        self.assertEqual(self.adapter.get("manufacturer", "Cisco").description, "")

    def test_load_only_reads_documents_for_the_configured_instance(self):
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco"})
        self.client.upsert("Nautobot-B", "dcim.manufacturer", "Juniper", {"name": "Juniper"})

        self.adapter.load()

        self.assertEqual([m.name for m in self.adapter.get_all("manufacturer")], ["Cisco"])

    def test_load_reads_every_requested_instance_but_writes_only_its_own(self):
        adapter = build_adapter(instance="HUB", read_instances=["SPOKE-Austin", "SPOKE-Boston"])
        client = adapter.client
        client.upsert("HUB", "dcim.manufacturer", "Cisco", {"name": "Cisco"})
        client.upsert("SPOKE-Austin", "dcim.manufacturer", "Arista", {"name": "Arista"})
        client.upsert("SPOKE-Boston", "dcim.manufacturer", "Juniper", {"name": "Juniper"})

        adapter.load()
        adapter.manufacturer.create(adapter, ids={"name": "Nokia"}, attrs={"description": ""})
        adapter.flush()

        self.assertEqual({m.name for m in adapter.get_all("manufacturer")}, {"Arista", "Juniper"})
        self.assertIsNotNone(client.get("HUB", "dcim.manufacturer", "Nokia"))
        self.assertIsNone(client.get("SPOKE-Austin", "dcim.manufacturer", "Nokia"))

    def test_load_skips_tombstones(self):
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco"})
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Arista", {"name": "Arista"})
        self.client.delete(INSTANCE, "dcim.manufacturer", "Arista")

        self.adapter.load()

        self.assertEqual([m.name for m in self.adapter.get_all("manufacturer")], ["Cisco"])

    def test_load_records_the_origin_owner_of_every_object(self):
        adapter = build_adapter(instance="HUB", read_instances=["SPOKE-Austin", "SPOKE-Boston"])
        adapter.client.upsert("SPOKE-Austin", "dcim.manufacturer", "Arista", {"name": "Arista"})
        adapter.client.upsert(
            "SPOKE-Boston", "dcim.devicetype", "X__Juniper", {"model": "X", "manufacturer__name": "Juniper"}
        )

        adapter.load()

        self.assertEqual(adapter.origin_owners[("manufacturer", (("name", "Arista"),))], "SPOKE-Austin")
        self.assertEqual(
            adapter.origin_owners[("devicetype", (("manufacturer__name", "Juniper"), ("model", "X")))], "SPOKE-Boston"
        )

    def test_sync_to_exposes_the_source_on_the_target_only_during_the_sync(self):
        target = build_adapter()
        seen = []
        original = target.sync_from

        def spy(*args, **kwargs):
            seen.append(getattr(target, "_mongo_source", None))
            return original(*args, **kwargs)

        target.sync_from = spy

        self.adapter.sync_to(target)

        self.assertIs(seen[0], self.adapter)
        self.assertIsNone(target._mongo_source)  # pylint: disable=protected-access,no-member

    def test_load_orders_children_after_their_parent(self):
        self.client.upsert(INSTANCE, "dcim.locationtype", "Building", {"name": "Building", "parent__name": "Site"})
        self.client.upsert(INSTANCE, "dcim.locationtype", "Site", {"name": "Site", "parent__name": "Region"})
        self.client.upsert(INSTANCE, "dcim.locationtype", "Region", {"name": "Region", "parent__name": None})

        self.adapter.load()

        self.assertEqual([m.name for m in self.adapter.get_all("locationtype")], ["Region", "Site", "Building"])

    def test_load_sorts_to_many_relations_the_way_the_nautobot_adapter_presents_them(self):
        content_types = [{"app_label": "ipam", "model": "prefix"}, {"app_label": "dcim", "model": "device"}]
        self.client.upsert(INSTANCE, "extras.role", "Edge", {"name": "Edge", "content_types": content_types})

        self.adapter.load()

        self.assertEqual(self.adapter.get("role", "Edge").content_types, sort_related_dicts(content_types))
        self.assertEqual(
            sort_related_dicts(content_types),
            [{"app_label": "dcim", "model": "device"}, {"app_label": "ipam", "model": "prefix"}],
        )

    def test_owner_adapters_only_process_their_owners_models(self):
        hub_models = owned_model_names("HUB")
        spoke_models = owned_model_names("SPOKE")

        self.assertEqual(build_adapter(adapter_class=HubMongoAdapter).top_level, hub_models)
        self.assertEqual(build_adapter(adapter_class=SpokeMongoAdapter).top_level, spoke_models)
        self.assertEqual(HubNautobotAdapter(job=MagicMock(), sync=None).top_level, hub_models)
        self.assertEqual(SpokeNautobotAdapter(job=MagicMock(), sync=None).top_level, spoke_models)
        self.assertEqual(set(hub_models) & set(spoke_models), set())
        self.assertEqual(hub_models + spoke_models, TOP_LEVEL)

    def test_owner_adapters_follow_the_rules_as_they_change(self):
        with ownership_rules_writable():
            rule = OwnershipRule.objects.get(content_type__model="cable")
            rule.enabled = False
            rule.validated_save()
            rule = OwnershipRule.objects.get(content_type__model="prefix")
            rule.owner_role = "SPOKE"
            rule.sync_order = 50
            rule.validated_save()

        self.assertNotIn("cable", build_adapter(adapter_class=SpokeMongoAdapter).top_level)
        self.assertEqual(build_adapter(adapter_class=SpokeMongoAdapter).top_level[-1], "prefix")
        self.assertNotIn("prefix", build_adapter(adapter_class=HubMongoAdapter).top_level)


class ParentsFirstTestCase(TestCase):
    """Test the parent-first ordering helper."""

    def test_records_without_a_parent_field_keep_their_order(self):
        records = [{"name": "b"}, {"name": "a"}]

        self.assertEqual(parents_first(records), records)

    def test_records_with_an_unknown_parent_are_kept_last(self):
        records = [{"name": "child", "parent__name": "missing"}, {"name": "root", "parent__name": None}]

        self.assertEqual([r["name"] for r in parents_first(records)], ["root", "child"])


class MongoRemoteModelTestCase(TestCase):
    """Test create/update/delete of remote models write through to MongoDB once the buffer is flushed."""

    def setUp(self):
        super().setUp()
        self.adapter = build_adapter()
        self.client = self.adapter.client

    def test_create_writes_document(self):
        self.adapter.manufacturer.create(self.adapter, ids={"name": "Cisco"}, attrs={"description": "Cisco Systems"})

        self.assertIsNone(self.client.get(INSTANCE, "dcim.manufacturer", "Cisco"))
        self.adapter.flush()
        document = self.client.get(INSTANCE, "dcim.manufacturer", "Cisco")
        self.assertEqual(document["data"], {"name": "Cisco", "description": "Cisco Systems"})

    def test_create_with_multiple_identifiers_builds_composite_key(self):
        self.adapter.devicetype.create(
            self.adapter,
            ids={"model": "C9300-48P", "manufacturer__name": "Cisco"},
            attrs={"part_number": "C9300-48P", "u_height": 1, "is_full_depth": True},
        )
        self.adapter.flush()

        self.assertIsNotNone(self.client.get(INSTANCE, "dcim.devicetype", "C9300-48P__Cisco"))

    def test_update_replaces_document_with_merged_values(self):
        model = self.adapter.manufacturer(name="Cisco", description="old")
        self.adapter.add(model)
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": "old"})

        model.update({"description": "new"})
        self.adapter.flush()

        document = self.client.get(INSTANCE, "dcim.manufacturer", "Cisco")
        self.assertEqual(document["data"], {"name": "Cisco", "description": "new"})
        self.assertEqual(model.description, "new")

    def test_delete_tombstones_document(self):
        model = self.adapter.manufacturer(name="Cisco", description="")
        self.adapter.add(model)
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": ""})

        model.delete()
        self.adapter.flush()

        self.assertIsNone(self.client.get(INSTANCE, "dcim.manufacturer", "Cisco"))
        tombstone = self.client.get(INSTANCE, "dcim.manufacturer", "Cisco", include_deleted=True)
        self.assertTrue(tombstone["deleted"])
        self.assertEqual(tombstone["nautobot_role"], self.adapter.status_metadata["nautobot_role"])

    def test_create_stamps_role_and_version_metadata(self):
        self.adapter.manufacturer.create(self.adapter, ids={"name": "Cisco"}, attrs={"description": ""})
        self.adapter.flush()

        document = self.client.get(INSTANCE, "dcim.manufacturer", "Cisco")
        self.assertEqual(document["nautobot_role"], get_role())
        self.assertIn("nautobot_version", document)
        self.assertIn("nautobot_ssot_mongo", document["nautobot_apps"])

    def test_sync_between_two_mongo_adapters_applies_create_update_delete(self):
        source = build_adapter()
        source.add(source.manufacturer(name="Cisco", description="Cisco Systems"))
        source.add(source.manufacturer(name="Arista", description="Arista Networks"))
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": "stale"})
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Juniper", {"name": "Juniper", "description": ""})
        self.adapter.load()

        source.sync_to(self.adapter)

        self.assertEqual(
            self.client.find_data(INSTANCE, "dcim.manufacturer"),
            [
                {"name": "Arista", "description": "Arista Networks"},
                {"name": "Cisco", "description": "Cisco Systems"},
            ],
        )


class WriteBufferTestCase(TestCase):
    """The adapter sends its writes in batches and flushes the remainder when the sync completes."""

    def setUp(self):
        super().setUp()
        self.adapter = build_adapter()
        self.client = self.adapter.client

    def _create(self, count, start=0):
        for index in range(start, start + count):
            self.adapter.manufacturer.create(
                self.adapter, ids={"name": f"Vendor {index:04d}"}, attrs={"description": ""}
            )

    def test_a_full_batch_is_written_during_the_sync(self):
        self._create(MONGO_BATCH_LIMIT - 1)
        self.assertEqual(self.client.collection.count_documents({}), 0)

        self._create(1, start=MONGO_BATCH_LIMIT - 1)

        self.assertEqual(self.client.collection.count_documents({}), MONGO_BATCH_LIMIT)
        self.assertEqual(self.adapter.documents_written["dcim.manufacturer"], MONGO_BATCH_LIMIT)

    def test_sync_complete_writes_the_remainder(self):
        self._create(3)

        self.adapter.sync_complete(source=None, diff=None)

        self.assertEqual(self.client.collection.count_documents({}), 3)
        self.assertEqual(self.adapter.documents_written["dcim.manufacturer"], 3)

    def test_flush_is_idempotent(self):
        self._create(2)

        self.adapter.flush()
        self.adapter.flush()

        self.assertEqual(self.client.collection.count_documents({}), 2)
        self.assertEqual(self.adapter.documents_written["dcim.manufacturer"], 2)

    def test_batches_are_kept_per_content_type(self):
        self._create(2)
        self.adapter.platform.create(self.adapter, ids={"name": "IOS"}, attrs={"manufacturer__name": "Vendor 0000"})

        self.adapter.flush("dcim.platform")

        self.assertEqual(self.client.find_data(INSTANCE, "dcim.manufacturer"), [])
        self.assertEqual(len(self.client.find_data(INSTANCE, "dcim.platform")), 1)

    def test_a_delete_that_matches_nothing_is_reported(self):
        model = self.adapter.manufacturer(name="Ghost", description="")
        self.adapter.add(model)

        model.delete()
        self.adapter.flush()

        self.adapter.job.logger.warning.assert_called_once()
        self.assertIn("matched no MongoDB document", self.adapter.job.logger.warning.call_args.args[0])

    def test_a_rejected_batch_is_logged_and_fails_the_sync(self):
        self._create(2)
        error = BulkWriteError({"writeErrors": [{"index": 1, "errmsg": "E11000 duplicate key"}]})

        with patch.object(self.client, "write_batch", side_effect=error):
            with self.assertRaises(BulkWriteError):
                self.adapter.flush()

        message = self.adapter.job.logger.error.call_args.args
        self.assertIn("rejected a batch", message[0])
        self.assertEqual(message[1:4], (2, "dcim.manufacturer", 1))
        self.assertEqual(self.adapter.documents_written["dcim.manufacturer"], 0)
