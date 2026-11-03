"""Test that the jobs pick the owning role and MongoDB partitions from the configured role."""

from unittest.mock import MagicMock, patch

import mongomock
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from nautobot.apps.testing import TestCase
from nautobot.extras.context_managers import JobChangeContext, change_logging
from nautobot.extras.signals import change_context_state
from pymongo.errors import PyMongoError

from nautobot_ssot_mongo.constants import JOB_SOFT_TIME_LIMIT, JOB_TIME_LIMIT
from nautobot_ssot_mongo.diff import CustomOrderingDiff
from nautobot_ssot_mongo.diffsync.adapters import owned_model_names
from nautobot_ssot_mongo.jobs import (
    REMOTE_INSTANCES_CACHE_KEY,
    MongoDataSource,
    MongoDataTarget,
    get_remote_instance_choices,
)
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient


def role_settings(**overrides):
    """Return a PLUGINS_CONFIG with this app's settings replaced by `overrides`."""
    return {**settings.PLUGINS_CONFIG, "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", **overrides}}


class RoleDrivenJobTestCase(TestCase):
    """Exercise the adapter selection of both jobs under each role."""

    def setUp(self):
        super().setUp()
        self.client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
        self.client.upsert("NDP", "dcim.manufacturer", "Cisco", {"name": "Cisco"}, metadata={"nautobot_role": "NDP"})
        self.client.upsert(
            "NAP-Austin",
            "dcim.location",
            "Austin__None",
            {"name": "Austin", "parent__name": None, "location_type__name": "Site"},
            metadata={"nautobot_role": "NAP"},
        )
        # A partition written by an older app version carries no role and must still count as a NAP.
        self.client.upsert(
            "NAP-Boston",
            "dcim.location",
            "Boston__None",
            {"name": "Boston", "parent__name": None, "location_type__name": "Site"},
        )
        patcher = patch.object(MongoSyncClient, "from_settings", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        cache.delete(REMOTE_INSTANCES_CACHE_KEY)
        self.addCleanup(cache.delete, REMOTE_INSTANCES_CACHE_KEY)

    @staticmethod
    def _job(job_class):
        job = job_class()
        job.sync = None
        return job

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NAP", nautobot_instance="NAP-Dev"))
    def test_nap_source_job_reads_ndp_owned_objects_from_the_ndp_partition(self):
        job = self._job(MongoDataSource)

        job.load_source_adapter()

        self.assertEqual(job.source_adapter.top_level, owned_model_names("NDP"))
        self.assertEqual(job.source_adapter.read_instances, ["NDP"])
        self.assertEqual([m.name for m in job.source_adapter.get_all("manufacturer")], ["Cisco"])
        self.assertEqual(MongoDataSource.owner_role(), "NDP")
        self.assertEqual(MongoDataSource.config_information()["NDP instance"], "NDP")
        self.assertEqual(MongoDataSource.config_information()["Objects owned by"], "NDP")
        self.assertEqual(MongoDataSource.config_information()["Tombstone TTL (days)"], 30)
        self.assertEqual(MongoDataSource.config_information()["Owner metadata type"], "SSoT MongoDB Owner")

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NAP", nautobot_instance="NAP-Dev"))
    def test_nap_source_job_scopes_its_target_to_the_ndp_partition(self):
        job = self._job(MongoDataSource)

        job.load_source_adapter()
        job.load_target_adapter()

        self.assertEqual(job.target_adapter.nautobot_instances, ["NDP"])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NDP", nautobot_instance="NDP"))
    def test_ndp_source_job_reads_nap_owned_objects_from_every_other_partition(self):
        job = self._job(MongoDataSource)

        job.load_source_adapter()

        self.assertEqual(job.source_adapter.top_level, owned_model_names("NAP"))
        self.assertEqual(job.source_adapter.read_instances, ["NAP-Austin", "NAP-Boston"])
        self.assertEqual({m.name for m in job.source_adapter.get_all("location")}, {"Austin", "Boston"})
        self.assertNotIn("NDP instance", MongoDataSource.config_information())
        self.assertEqual(MongoDataSource.config_information()["NAP instances found"], "NAP-Austin, NAP-Boston")

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NDP", nautobot_instance="NDP"))
    def test_ndp_source_job_reads_only_the_selected_partitions(self):
        job = self._job(MongoDataSource)
        job.nautobot_instances = ["NAP-Boston"]

        job.load_source_adapter()
        job.load_target_adapter()

        self.assertEqual(job.source_adapter.read_instances, ["NAP-Boston"])
        self.assertEqual([m.name for m in job.source_adapter.get_all("location")], ["Boston"])
        self.assertEqual(job.target_adapter.nautobot_instances, ["NAP-Boston"])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NDP", nautobot_instance="NDP"))
    def test_remote_instance_choices_are_the_other_roles_partitions(self):
        self.assertEqual(get_remote_instance_choices(), [("NAP-Austin", "NAP-Austin"), ("NAP-Boston", "NAP-Boston")])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NAP", nautobot_instance="NAP-Austin"))
    def test_remote_instance_choices_on_a_nap_list_the_ndp(self):
        self.assertEqual(get_remote_instance_choices(), [("NAP-Boston", "NAP-Boston"), ("NDP", "NDP")])

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NDP", nautobot_instance="NDP"))
    def test_remote_instance_choices_are_served_from_the_cache_for_a_while(self):
        first = get_remote_instance_choices()
        self.client.upsert("NAP-Dallas", "dcim.location", "Dallas__None", {"name": "Dallas"})

        self.assertEqual(get_remote_instance_choices(), first)
        cache.delete(REMOTE_INSTANCES_CACHE_KEY)
        self.assertIn(("NAP-Dallas", "NAP-Dallas"), get_remote_instance_choices())

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NDP", nautobot_instance="NDP"))
    def test_unreachable_mongodb_yields_no_choices_and_caches_nothing(self):
        with patch.object(MongoSyncClient, "instances", side_effect=PyMongoError("down")):
            self.assertEqual(get_remote_instance_choices(), [])
        self.assertIsNone(cache.get(REMOTE_INSTANCES_CACHE_KEY))

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NDP", nautobot_instance="NDP"))
    def test_ndp_target_job_loads_ndp_owned_objects_under_its_own_partition(self):
        job = self._job(MongoDataTarget)

        job.load_target_adapter()

        self.assertEqual(job.target_adapter.top_level, owned_model_names("NDP"))
        self.assertEqual(job.target_adapter.nautobot_instance, "NDP")
        self.assertEqual(job.target_adapter.read_instances, "NDP")
        self.assertEqual(job.get_nautobot_adapter().top_level, owned_model_names("NDP"))
        self.assertIsNone(job.get_nautobot_adapter().nautobot_instances)

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NAP", nautobot_instance="NAP-Boston"))
    def test_target_job_backfills_role_and_version_on_legacy_documents(self):
        job = self._job(MongoDataTarget)

        job.load_target_adapter()

        boston = self.client.get("NAP-Boston", "dcim.location", "Boston__None")
        self.assertEqual(boston["nautobot_role"], "NAP")
        self.assertIn("nautobot_version", boston)

    @override_settings(PLUGINS_CONFIG=role_settings(nautobot_role="NAP", nautobot_instance="NAP-Dev"))
    def test_nap_target_job_loads_nap_owned_objects_under_its_own_partition(self):
        job = self._job(MongoDataTarget)

        job.load_target_adapter()

        self.assertEqual(job.target_adapter.top_level, owned_model_names("NAP"))
        self.assertEqual(job.target_adapter.nautobot_instance, "NAP-Dev")
        self.assertEqual(job.get_nautobot_adapter().top_level, owned_model_names("NAP"))
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
        self.job = MongoDataSource()
        self.job.sync = None
        self.job.source_adapter = MagicMock()
        self.job.target_adapter = MagicMock()
        self.job.diffsync_flags = 0
        self.job.record_changelog = True
        self.job.logger.info = MagicMock()
        self.job.logger.warning = MagicMock()

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
