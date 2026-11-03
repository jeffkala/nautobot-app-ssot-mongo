"""Test that the jobs pick the owning role and MongoDB partitions from the configured role."""

from unittest.mock import MagicMock, patch

import mongomock
from django.conf import settings
from django.test import override_settings
from nautobot.apps.testing import TestCase
from nautobot.extras.context_managers import JobChangeContext, change_logging
from nautobot.extras.signals import change_context_state

from nautobot_ssot_mongo.constants import INSTANCE_HEARTBEAT_CONTENT_TYPE, JOB_SOFT_TIME_LIMIT, JOB_TIME_LIMIT
from nautobot_ssot_mongo.diff import CustomOrderingDiff
from nautobot_ssot_mongo.diffsync.adapters import owned_model_names
from nautobot_ssot_mongo.jobs import MongoDataSource, MongoDataTarget, get_remote_instance_choices
from nautobot_ssot_mongo.models import FederatedInstance
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient


def role_settings(**overrides):
    """Return a PLUGINS_CONFIG with this app's settings replaced by `overrides`."""
    return {**settings.PLUGINS_CONFIG, "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", **overrides}}


class RoleDrivenJobTestCase(TestCase):
    """Exercise the adapter selection of both jobs under each role."""

    def setUp(self):
        super().setUp()
        self.client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
        self.client.upsert("HUB", "dcim.manufacturer", "Cisco", {"name": "Cisco"}, metadata={"nautobot_role": "HUB"})
        self.client.upsert(
            "SPOKE-Austin",
            "dcim.location",
            "Austin__None",
            {"name": "Austin", "parent__name": None, "location_type__name": "Site"},
            metadata={"nautobot_role": "SPOKE"},
        )
        # A partition written by an older app version carries no role and must still count as a spoke.
        self.client.upsert(
            "SPOKE-Boston",
            "dcim.location",
            "Boston__None",
            {"name": "Boston", "parent__name": None, "location_type__name": "Site"},
        )
        patcher = patch.object(MongoSyncClient, "from_settings", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, role in (("HUB", "HUB"), ("SPOKE-Austin", "SPOKE"), ("SPOKE-Boston", "SPOKE")):
            FederatedInstance(name=name, role=role).validated_save()

    @staticmethod
    def _job(job_class):
        job = job_class()
        job.sync = None
        return job

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="SPOKE", nautobot_instance="SPOKE-Dev"))
    def test_spoke_source_job_reads_hub_owned_objects_from_the_hub_partition(self):
        job = self._job(MongoDataSource)

        job.load_source_adapter()

        self.assertEqual(job.source_adapter.top_level, owned_model_names("HUB"))
        self.assertEqual(job.source_adapter.read_instances, ["HUB"])
        self.assertEqual([m.name for m in job.source_adapter.get_all("manufacturer")], ["Cisco"])
        self.assertEqual(MongoDataSource.owner_role(), "HUB")
        self.assertEqual(MongoDataSource.config_information()["Hub instance"], "HUB")
        self.assertEqual(MongoDataSource.config_information()["Objects owned by"], "HUB")
        self.assertEqual(MongoDataSource.config_information()["Tombstone TTL (days)"], 30)
        self.assertEqual(MongoDataSource.config_information()["Owner metadata type"], "SSoT MongoDB Owner")

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="SPOKE", nautobot_instance="SPOKE-Dev"))
    def test_spoke_source_job_scopes_its_target_to_the_hub_partition(self):
        job = self._job(MongoDataSource)

        job.load_source_adapter()
        job.load_target_adapter()

        self.assertEqual(job.target_adapter.nautobot_instances, ["HUB"])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="HUB", nautobot_instance="HUB"))
    def test_hub_source_job_reads_spoke_owned_objects_from_every_other_partition(self):
        job = self._job(MongoDataSource)

        job.load_source_adapter()

        self.assertEqual(job.source_adapter.top_level, owned_model_names("SPOKE"))
        self.assertEqual(job.source_adapter.read_instances, ["SPOKE-Austin", "SPOKE-Boston"])
        self.assertEqual({m.name for m in job.source_adapter.get_all("location")}, {"Austin", "Boston"})
        self.assertNotIn("Hub instance", MongoDataSource.config_information())
        self.assertEqual(MongoDataSource.config_information()["Spoke instances found"], "SPOKE-Austin, SPOKE-Boston")

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="HUB", nautobot_instance="HUB"))
    def test_hub_source_job_reads_only_the_selected_partitions(self):
        job = self._job(MongoDataSource)
        job.nautobot_instances = ["SPOKE-Boston"]

        job.load_source_adapter()
        job.load_target_adapter()

        self.assertEqual(job.source_adapter.read_instances, ["SPOKE-Boston"])
        self.assertEqual([m.name for m in job.source_adapter.get_all("location")], ["Boston"])
        self.assertEqual(job.target_adapter.nautobot_instances, ["SPOKE-Boston"])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="HUB", nautobot_instance="HUB"))
    def test_remote_instance_choices_are_the_registered_spokes(self):
        self.assertEqual(
            get_remote_instance_choices(), [("SPOKE-Austin", "SPOKE-Austin"), ("SPOKE-Boston", "SPOKE-Boston")]
        )

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="SPOKE", nautobot_instance="SPOKE-Austin"))
    def test_remote_instance_choices_on_a_spoke_are_the_registered_hub(self):
        self.assertEqual(get_remote_instance_choices(), [("HUB", "HUB")])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="HUB", nautobot_instance="HUB"))
    def test_remote_instance_choices_come_from_the_registry_not_the_collection(self):
        self.client.upsert("SPOKE-Dallas", "dcim.location", "Dallas__None", {"name": "Dallas"})

        self.assertNotIn(("SPOKE-Dallas", "SPOKE-Dallas"), get_remote_instance_choices())

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="HUB", nautobot_instance="HUB"))
    def test_hub_target_job_loads_hub_owned_objects_under_its_own_partition(self):
        job = self._job(MongoDataTarget)

        job.load_target_adapter()

        self.assertEqual(job.target_adapter.top_level, owned_model_names("HUB"))
        self.assertEqual(job.target_adapter.nautobot_instance, "HUB")
        self.assertEqual(job.target_adapter.read_instances, "HUB")
        self.assertEqual(job.get_nautobot_adapter().top_level, owned_model_names("HUB"))
        self.assertIsNone(job.get_nautobot_adapter().nautobot_instances)

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="SPOKE", nautobot_instance="SPOKE-Boston"))
    def test_target_job_backfills_role_and_version_on_legacy_documents(self):
        job = self._job(MongoDataTarget)

        job.load_target_adapter()

        boston = self.client.get("SPOKE-Boston", "dcim.location", "Boston__None")
        self.assertEqual(boston["nautobot_role"], "SPOKE")
        self.assertIn("nautobot_version", boston)

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="SPOKE", nautobot_instance="SPOKE-Dev"))
    def test_spoke_target_job_loads_spoke_owned_objects_under_its_own_partition(self):
        job = self._job(MongoDataTarget)

        job.load_target_adapter()

        self.assertEqual(job.target_adapter.top_level, owned_model_names("SPOKE"))
        self.assertEqual(job.target_adapter.nautobot_instance, "SPOKE-Dev")
        self.assertEqual(job.get_nautobot_adapter().top_level, owned_model_names("SPOKE"))
        self.assertEqual(
            [mapping.target_name for mapping in MongoDataTarget.data_mappings()],
            ["Location", "Rack Group", "Rack", "Device", "Interface", "VLAN", "IP Address", "Cable"],
        )


class JobOptionsTestCase(TestCase):
    """Both jobs raise the Celery time limits; only the pull job offers the change log toggle."""

    def test_every_job_sets_the_shared_limits(self):
        for job_class in (MongoDataSource, MongoDataTarget):
            with self.subTest(job=job_class.__name__):
                self.assertEqual(job_class.soft_time_limit, JOB_SOFT_TIME_LIMIT)
                self.assertEqual(job_class.time_limit, JOB_TIME_LIMIT)
                self.assertGreater(job_class.time_limit, job_class.soft_time_limit)

    def test_only_the_pull_job_offers_record_changelog(self):
        variables = MongoDataSource._get_vars()  # pylint: disable=protected-access
        self.assertIn("record_changelog", variables)
        self.assertTrue(variables["record_changelog"].field_attrs["initial"])
        self.assertNotIn("record_changelog", MongoDataTarget._get_vars())  # pylint: disable=protected-access


class ExecuteSyncTestCase(TestCase):
    """`execute_sync` applies the diff with the custom ordering and honours the change log toggle."""

    def setUp(self):
        super().setUp()
        self.client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
        self.job = MongoDataSource()
        self.job.sync = None
        self.job.source_adapter = MagicMock()
        self.job.target_adapter = MagicMock()
        self.job.diffsync_flags = 0
        self.job.record_changelog = True
        self.job.logger.info = MagicMock()
        self.job.logger.warning = MagicMock()
        self.job._mongo_client = self.client  # pylint: disable=protected-access

    def test_sync_uses_the_custom_diff_and_the_job_flags(self):
        self.job.execute_sync()

        self.job.source_adapter.sync_to.assert_called_once_with(
            self.job.target_adapter, flags=0, diff_class=CustomOrderingDiff
        )

    def test_sync_warns_when_an_adapter_is_missing(self):
        self.job.target_adapter = None

        self.job.execute_sync()

        self.job.source_adapter.sync_to.assert_not_called()
        self.job.logger.warning.assert_called_once()
        self.assertFalse(FederatedInstance.objects.exists())

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="SPOKE", nautobot_instance="SPOKE-Dev"))
    def test_sync_ends_with_a_heartbeat_and_a_registry_refresh(self):
        self.job.execute_sync()

        heartbeat = self.client.get("SPOKE-Dev", INSTANCE_HEARTBEAT_CONTENT_TYPE, "SPOKE-Dev")
        self.assertIsNotNone(heartbeat["data"]["last_pull"])
        self.assertIsNone(heartbeat["data"]["last_publish"])
        instance = FederatedInstance.objects.get(name="SPOKE-Dev")
        self.assertEqual(instance.role, "SPOKE")
        self.assertIsNotNone(instance.last_pull)
        self.assertEqual(instance.last_seen, instance.last_pull)

    def _sync_change_context(self):
        seen = []
        self.job.source_adapter.sync_to.side_effect = lambda *args, **kwargs: seen.append(change_context_state.get())
        with change_logging(JobChangeContext(user=self.user, context_detail="test")):
            self.job.execute_sync()
            self.assertIsNotNone(change_context_state.get())
        return seen

    def test_sync_keeps_the_change_context_when_recording(self):
        self.job.record_changelog = True

        seen = self._sync_change_context()

        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0])
        self.assertFalse(
            any("Change log recording is off" in call.args[0] for call in self.job.logger.info.call_args_list)
        )

    def test_sync_clears_the_change_context_when_not_recording(self):
        self.job.record_changelog = False

        seen = self._sync_change_context()

        self.assertEqual(seen, [None])
        self.assertTrue(
            any("Change log recording is off" in call.args[0] for call in self.job.logger.info.call_args_list)
        )
