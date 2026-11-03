"""Tests for owner stamping, adoption of existing objects and owner-scoped loading."""

import logging
from unittest.mock import MagicMock

import mongomock
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.testing import TestCase
from nautobot.dcim.models import Manufacturer
from nautobot.extras.models.metadata import MetadataType, ObjectMetadata

from nautobot_ssot_mongo.diff import CustomOrderingDiff
from nautobot_ssot_mongo.diffsync.adapters import MongoNautobotAdapter, MongoRemoteAdapter, NDPMongoAdapter
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME, job_logger, stamp_owner
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient


def _owner_for(obj):
    """Return the owner metadata value attached to `obj`, or None."""
    entry = ObjectMetadata.objects.filter(
        metadata_type__name=METADATA_TYPE_NAME,
        assigned_object_type=ContentType.objects.get_for_model(obj),
        assigned_object_id=obj.pk,
    ).first()
    return entry.value if entry else None


def _stamp(obj, owner, transferred=True):
    metadata_type = MetadataType.objects.get(name=METADATA_TYPE_NAME)
    entry = ObjectMetadata(metadata_type=metadata_type, assigned_object=obj, scoped_fields=[])
    entry.value = {"owner": owner, "ownership_transferred": transferred}
    entry.validated_save()


def _mongo_adapter(instance="NDP", read_instances=None):
    client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
    client.ensure_indexes()
    job = MagicMock()
    job.debug = False
    return NDPMongoAdapter(job=job, sync=None, client=client, nautobot_instance=instance, read_instances=read_instances)


def _nautobot_adapter(nautobot_instances=None):
    job = MagicMock()
    job.debug = False
    adapter = MongoNautobotAdapter(job=job, sync=None, nautobot_instances=nautobot_instances)
    adapter.top_level = ["manufacturer"]
    return adapter


class StampOwnerTestCase(TestCase):
    """`stamp_owner` creates, updates and never duplicates the owner metadata."""

    def test_stamp_creates_metadata(self):
        cisco = Manufacturer.objects.create(name="Cisco")

        stamp_owner(cisco, "NDP")

        self.assertEqual(_owner_for(cisco), {"owner": "NDP", "ownership_transferred": True})

    def test_stamp_updates_existing_owner_and_is_idempotent(self):
        cisco = Manufacturer.objects.create(name="Cisco")
        _stamp(cisco, "NAP-Austin", transferred=False)

        stamp_owner(cisco, "NDP")
        stamp_owner(cisco, "NDP")

        self.assertEqual(_owner_for(cisco), {"owner": "NDP", "ownership_transferred": True})
        self.assertEqual(ObjectMetadata.objects.filter(assigned_object_id=cisco.pk).count(), 1)

    def test_empty_owner_is_a_noop(self):
        cisco = Manufacturer.objects.create(name="Cisco")

        stamp_owner(cisco, "")

        self.assertIsNone(_owner_for(cisco))

    def test_local_create_does_not_stamp(self):
        cisco = Manufacturer.objects.create(name="Cisco")

        self.assertIsNone(_owner_for(cisco))

    def test_disallowed_content_type_is_logged_and_skipped(self):
        metadata_type = MetadataType.objects.get(name=METADATA_TYPE_NAME)
        metadata_type.content_types.remove(ContentType.objects.get_for_model(Manufacturer))
        cisco = Manufacturer.objects.create(name="Cisco")

        with self.assertLogs("nautobot_ssot_mongo.owner_metadata", level="WARNING") as logs:
            stamp_owner(cisco, "NDP")

        self.assertIn("Could not stamp", logs.output[0])
        self.assertIsNone(_owner_for(cisco))

    def test_warnings_go_to_the_logger_passed_in(self):
        metadata_type = MetadataType.objects.get(name=METADATA_TYPE_NAME)
        metadata_type.content_types.remove(ContentType.objects.get_for_model(Manufacturer))
        cisco = Manufacturer.objects.create(name="Cisco")
        log = MagicMock()

        stamp_owner(cisco, "NDP", log=log)

        log.warning.assert_called_once()
        self.assertIn("Could not stamp", log.warning.call_args.args[0])

    def test_job_logger_falls_back_to_the_module_logger_outside_a_job(self):
        adapter = MagicMock()
        adapter.job = None

        self.assertIs(job_logger(adapter), logging.getLogger("nautobot_ssot_mongo.owner_metadata"))
        with_job = MagicMock()
        self.assertIs(job_logger(with_job), with_job.job.logger)


class PullStampsAndAdoptsTestCase(TestCase):
    """A MongoDB -> Nautobot sync stamps the publishing instance and adopts matching local objects."""

    def test_synced_objects_are_stamped_with_the_publishing_instance(self):
        source = _mongo_adapter(read_instances=["NDP"])
        source.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": "from NDP"})
        source.load()

        source.sync_to(_nautobot_adapter(nautobot_instances=["NDP"]), diff_class=CustomOrderingDiff)

        cisco = Manufacturer.objects.get(name="Cisco")
        self.assertEqual(cisco.description, "from NDP")
        self.assertEqual(_owner_for(cisco), {"owner": "NDP", "ownership_transferred": True})

    def test_existing_unstamped_object_is_adopted_instead_of_duplicated(self):
        local = Manufacturer.objects.create(name="Cisco", description="created by hand")
        source = _mongo_adapter(read_instances=["NDP"])
        source.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": "from NDP"})
        source.load()
        target = _nautobot_adapter(nautobot_instances=["NDP"])
        target.load()
        self.assertEqual(target.get_all("manufacturer"), [])  # unstamped -> not in the diff -> shows as a create

        source.sync_to(target, diff_class=CustomOrderingDiff)

        self.assertEqual(Manufacturer.objects.filter(name="Cisco").count(), 1)
        local.refresh_from_db()
        self.assertEqual(local.description, "from NDP")
        self.assertEqual(_owner_for(local), {"owner": "NDP", "ownership_transferred": True})
        target.job.logger.info.assert_any_call(
            "Adopted existing %s %s: updated and stamped owner instead of creating", "manufacturer", {"name": "Cisco"}
        )

    def test_conflicting_owner_is_overwritten_by_the_publisher(self):
        local = Manufacturer.objects.create(name="Cisco")
        _stamp(local, "NAP-Austin")
        source = _mongo_adapter(read_instances=["NDP"])
        source.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": ""})
        source.load()

        source.sync_to(_nautobot_adapter(nautobot_instances=["NDP"]), diff_class=CustomOrderingDiff)

        self.assertEqual(_owner_for(local)["owner"], "NDP")

    def test_update_restamps_the_owner(self):
        local = Manufacturer.objects.create(name="Cisco", description="old")
        _stamp(local, "NDP")
        source = _mongo_adapter(read_instances=["NDP"])
        source.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco", "description": "new"})
        source.load()

        source.sync_to(_nautobot_adapter(nautobot_instances=["NDP"]), diff_class=CustomOrderingDiff)

        local.refresh_from_db()
        self.assertEqual(local.description, "new")
        self.assertEqual(_owner_for(local), {"owner": "NDP", "ownership_transferred": True})

    def test_push_never_stamps(self):
        Manufacturer.objects.create(name="Cisco")
        target = _mongo_adapter()
        source = _nautobot_adapter()
        source.load()

        source.sync_to(target, diff_class=CustomOrderingDiff)

        self.assertIsNone(_owner_for(Manufacturer.objects.get(name="Cisco")))
        self.assertIsNotNone(target.client.get("NDP", "dcim.manufacturer", "Cisco"))


class OwnerScopedLoadTestCase(TestCase):
    """`MongoNautobotAdapter` loads only the objects owned by the selected instances."""

    def setUp(self):
        super().setUp()
        self.cisco = Manufacturer.objects.create(name="Cisco")
        _stamp(self.cisco, "NDP")
        self.arista = Manufacturer.objects.create(name="Arista")
        _stamp(self.arista, "NDP-Other")
        self.juniper = Manufacturer.objects.create(name="Juniper")
        _stamp(self.juniper, "NDP", transferred=False)
        Manufacturer.objects.create(name="Local-Vendor")

    def _names(self, nautobot_instances):
        adapter = _nautobot_adapter(nautobot_instances=nautobot_instances)
        adapter.load()
        return {m.name for m in adapter.get_all("manufacturer")}

    def test_none_loads_everything(self):
        self.assertEqual(self._names(None), {"Cisco", "Arista", "Juniper", "Local-Vendor"})

    def test_selected_owner_loads_only_its_transferred_objects(self):
        self.assertEqual(self._names(["NDP"]), {"Cisco"})

    def test_several_owners(self):
        self.assertEqual(self._names(["NDP", "NDP-Other"]), {"Cisco", "Arista"})

    def test_empty_selection_loads_nothing(self):
        self.assertEqual(self._names([]), set())

    def test_remote_adapter_is_unaffected(self):
        adapter = MongoRemoteAdapter(
            job=MagicMock(debug=False),
            sync=None,
            client=MongoSyncClient(client=mongomock.MongoClient(), database="t", collection="o"),
            nautobot_instance="NDP",
        )
        self.assertFalse(hasattr(adapter, "nautobot_instances"))
