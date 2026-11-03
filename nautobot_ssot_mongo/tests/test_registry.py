"""The heartbeat documents and the federated instance registry built from them."""

from datetime import timezone

import mongomock
from django.conf import settings
from django.test import override_settings
from nautobot.apps.testing import TestCase

from nautobot_ssot_mongo.constants import INSTANCE_HEARTBEAT_CONTENT_TYPE, OWNERSHIP_RULES_CONTENT_TYPE
from nautobot_ssot_mongo.jobs import MongoDataTarget
from nautobot_ssot_mongo.models import FederatedInstance
from nautobot_ssot_mongo.registry import PUBLISH, PULL, refresh_registry, write_heartbeat
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient

HUB_METADATA = {
    "nautobot_role": "HUB",
    "nautobot_version": "3.2.8",
    "nautobot_apps": {"nautobot_ssot": "4.0.0", "nautobot_ssot_mongo": "0.1.0a0"},
}
SPOKE_METADATA = {**HUB_METADATA, "nautobot_role": "SPOKE"}


def mongo_client():
    """Return a client over an in-memory MongoDB with the indexes created."""
    client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
    client.ensure_indexes()
    return client


class HeartbeatTestCase(TestCase):
    """What a heartbeat records about the instance that writes it."""

    def setUp(self):
        super().setUp()
        self.client = mongo_client()

    def test_heartbeat_counts_the_live_documents_of_each_type(self):
        self.client.upsert("SPOKE-Austin", "dcim.device", "r1__Austin", {"name": "r1"})
        self.client.upsert("SPOKE-Austin", "dcim.device", "r2__Austin", {"name": "r2"})
        self.client.upsert("SPOKE-Austin", "dcim.location", "Austin__None", {"name": "Austin"})
        self.client.upsert("SPOKE-Austin", "dcim.device", "old__Austin", {"name": "old"})
        self.client.delete("SPOKE-Austin", "dcim.device", "old__Austin")
        self.client.upsert("SPOKE-Austin", OWNERSHIP_RULES_CONTENT_TYPE, "dcim.device", {"owner_role": "SPOKE"})
        self.client.upsert("SPOKE-Boston", "dcim.device", "b1__Boston", {"name": "b1"})

        payload = write_heartbeat(self.client, "SPOKE-Austin", "SPOKE", PUBLISH, SPOKE_METADATA)

        self.assertEqual(payload["document_counts"], {"dcim.device": 2, "dcim.location": 1})
        self.assertEqual(payload["role"], "SPOKE")
        self.assertEqual(payload["nautobot_version"], "3.2.8")
        self.assertEqual(payload["app_versions"]["nautobot_ssot_mongo"], "0.1.0a0")
        self.assertIsNotNone(payload[PUBLISH])
        self.assertIsNone(payload[PULL])
        stored = self.client.get("SPOKE-Austin", INSTANCE_HEARTBEAT_CONTENT_TYPE, "SPOKE-Austin")
        self.assertEqual(stored["nautobot_role"], "SPOKE")

    def test_heartbeat_keeps_the_timestamp_of_the_other_direction(self):
        write_heartbeat(self.client, "HUB", "HUB", PUBLISH, HUB_METADATA)
        published = self.client.get("HUB", INSTANCE_HEARTBEAT_CONTENT_TYPE, "HUB")["data"][PUBLISH]

        second = write_heartbeat(self.client, "HUB", "HUB", PULL, HUB_METADATA)

        self.assertIsNotNone(second[PULL])
        self.assertEqual(second[PUBLISH], published)


class RegistryRefreshTestCase(TestCase):
    """The registry mirrors the heartbeats: one row per instance, rows gone when the heartbeat is gone."""

    def setUp(self):
        super().setUp()
        self.client = mongo_client()
        self.client.upsert("HUB", "dcim.manufacturer", "Cisco", {"name": "Cisco"})
        self.client.upsert("SPOKE-Austin", "dcim.device", "r1__Austin", {"name": "r1"})
        write_heartbeat(self.client, "HUB", "HUB", PUBLISH, HUB_METADATA)
        write_heartbeat(self.client, "SPOKE-Austin", "SPOKE", PUBLISH, SPOKE_METADATA)

    def test_refresh_creates_a_row_per_heartbeat(self):
        counts = refresh_registry(self.client)

        self.assertEqual(counts, {"created": 2, "updated": 0, "deleted": 0})
        self.assertEqual(list(FederatedInstance.objects.values_list("name", flat=True)), ["HUB", "SPOKE-Austin"])
        austin = FederatedInstance.objects.get(name="SPOKE-Austin")
        self.assertEqual(austin.role, "SPOKE")
        self.assertEqual(austin.document_counts, {"dcim.device": 1})
        self.assertEqual(austin.document_total, 1)
        self.assertEqual(austin.nautobot_version, "3.2.8")
        self.assertEqual(austin.app_version, "0.1.0a0")
        self.assertEqual(austin.last_publish.tzinfo, timezone.utc)
        self.assertIsNone(austin.last_pull)
        self.assertIsNotNone(austin.last_seen)

    def test_refresh_updates_only_the_rows_whose_heartbeat_changed(self):
        refresh_registry(self.client)
        self.client.upsert("SPOKE-Austin", "dcim.device", "r2__Austin", {"name": "r2"})
        write_heartbeat(self.client, "SPOKE-Austin", "SPOKE", PULL, SPOKE_METADATA)

        counts = refresh_registry(self.client)

        self.assertEqual(counts, {"created": 0, "updated": 1, "deleted": 0})
        austin = FederatedInstance.objects.get(name="SPOKE-Austin")
        self.assertEqual(austin.document_counts, {"dcim.device": 2})
        self.assertIsNotNone(austin.last_pull)

    def test_refresh_deletes_the_row_of_a_purged_partition(self):
        refresh_registry(self.client)
        self.client.delete_all("SPOKE-Austin")

        counts = refresh_registry(self.client)

        self.assertEqual(counts, {"created": 0, "updated": 0, "deleted": 1})
        self.assertFalse(FederatedInstance.objects.filter(name="SPOKE-Austin").exists())


class JobRegistryTestCase(TestCase):
    """Both jobs refresh the registry before they load anything."""

    @override_settings(
        PLUGINS_CONFIG={
            **settings.PLUGINS_CONFIG,
            "nautobot_ssot_mongo": {
                "mongo_uri": "mongodb://unused/",
                "nautobot_role": "HUB",
                "nautobot_instance": "HUB",
            },
        }
    )
    def test_job_refreshes_the_registry_from_the_heartbeats(self):
        client = mongo_client()
        write_heartbeat(client, "SPOKE-Austin", "SPOKE", PUBLISH, SPOKE_METADATA)
        job = MongoDataTarget()
        job._mongo_client = client  # pylint: disable=protected-access

        job.refresh_registry()

        self.assertEqual(FederatedInstance.objects.get(name="SPOKE-Austin").role, "SPOKE")
