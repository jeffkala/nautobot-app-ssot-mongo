"""Test the MongoSyncClient wrapper against an in-memory MongoDB."""

import mongomock
from nautobot.apps.testing import TestCase
from pymongo.errors import DuplicateKeyError

from nautobot_ssot_mongo.utils.mongo import TOMBSTONE_TTL_INDEX_NAME, UNIQUE_INDEX_NAME, MongoSyncClient

INSTANCE = "Nautobot-A"
CONTENT_TYPE = "dcim.manufacturer"


class MongoSyncClientTestCase(TestCase):
    """Test MongoSyncClient."""

    def setUp(self):
        super().setUp()
        self.client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
        self.client.ensure_indexes()

    def test_ensure_indexes_creates_unique_index(self):
        self.assertIn(UNIQUE_INDEX_NAME, self.client.collection.index_information())
        self.client.collection.insert_one({"nautobot_instance": INSTANCE, "content_type": CONTENT_TYPE, "key": "a"})
        with self.assertRaises(DuplicateKeyError):
            self.client.collection.insert_one({"nautobot_instance": INSTANCE, "content_type": CONTENT_TYPE, "key": "a"})

    def test_upsert_inserts_then_replaces(self):
        first = self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco", "description": "old"})
        second = self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco", "description": "new"})

        self.assertEqual(first["_id"], second["_id"])
        self.assertEqual(second["data"], {"name": "Cisco", "description": "new"})
        self.assertEqual(self.client.collection.count_documents({}), 1)

    def test_find_data_is_scoped_to_instance_and_content_type(self):
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"})
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Arista", {"name": "Arista"})
        self.client.upsert(INSTANCE, "dcim.platform", "IOS", {"name": "IOS"})
        self.client.upsert("Nautobot-B", CONTENT_TYPE, "Juniper", {"name": "Juniper"})

        self.assertEqual(self.client.find_data(INSTANCE, CONTENT_TYPE), [{"name": "Arista"}, {"name": "Cisco"}])
        self.assertEqual(self.client.find_data("Nautobot-B", CONTENT_TYPE), [{"name": "Juniper"}])
        self.assertEqual(self.client.find_data(INSTANCE, "dcim.devicetype"), [])

    def test_find_data_can_span_several_instances(self):
        self.client.upsert("SPOKE-Boston", CONTENT_TYPE, "Juniper", {"name": "Juniper"})
        self.client.upsert("SPOKE-Austin", CONTENT_TYPE, "Cisco", {"name": "Cisco"})
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Arista", {"name": "Arista"})

        self.assertEqual(
            self.client.find_data(["SPOKE-Austin", "SPOKE-Boston"], CONTENT_TYPE),
            [{"name": "Cisco"}, {"name": "Juniper"}],
        )
        self.assertEqual(self.client.find_data([], CONTENT_TYPE), [])

    def test_instances_lists_distinct_partitions(self):
        self.client.upsert("SPOKE-Boston", CONTENT_TYPE, "Juniper", {"name": "Juniper"})
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"})
        self.client.upsert(INSTANCE, "dcim.platform", "IOS", {"name": "IOS"})

        self.assertEqual(self.client.instances(), [INSTANCE, "SPOKE-Boston"])

    def test_instances_can_exclude_a_role_and_an_instance(self):
        self.client.upsert("HUB", CONTENT_TYPE, "Cisco", {"name": "Cisco"}, metadata={"nautobot_role": "HUB"})
        self.client.upsert(
            "SPOKE-Austin", "dcim.device", "d1__Austin", {"name": "d1"}, metadata={"nautobot_role": "SPOKE"}
        )
        # A legacy document without a role is never excluded by the role filter.
        self.client.collection.insert_one(
            {"nautobot_instance": "SPOKE-Legacy", "content_type": "dcim.device", "key": "d2__X", "data": {"name": "d2"}}
        )

        self.assertEqual(self.client.instances(exclude_role="HUB"), ["SPOKE-Austin", "SPOKE-Legacy"])
        self.assertEqual(self.client.instances(exclude_role="SPOKE"), ["HUB", "SPOKE-Legacy"])
        self.assertEqual(self.client.instances(exclude_instance="HUB"), ["SPOKE-Austin", "SPOKE-Legacy"])

    def test_delete_turns_the_document_into_a_hidden_tombstone(self):
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"}, metadata={"nautobot_role": "HUB"})
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Arista", {"name": "Arista"})

        self.assertEqual(self.client.delete(INSTANCE, CONTENT_TYPE, "Cisco"), 1)
        self.assertEqual(self.client.delete(INSTANCE, CONTENT_TYPE, "Cisco"), 0)
        self.assertIsNone(self.client.get(INSTANCE, CONTENT_TYPE, "Cisco"))
        self.assertEqual(self.client.find_data(INSTANCE, CONTENT_TYPE), [{"name": "Arista"}])
        tombstone = self.client.get(INSTANCE, CONTENT_TYPE, "Cisco", include_deleted=True)
        self.assertTrue(tombstone["deleted"])
        self.assertIsNotNone(tombstone["deleted_at"])
        self.assertEqual(self.client.collection.count_documents({}), 2)

    def test_upsert_revives_a_tombstone_in_place(self):
        first = self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"})
        self.client.delete(INSTANCE, CONTENT_TYPE, "Cisco")

        revived = self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco", "description": "back"})

        self.assertEqual(revived["_id"], first["_id"])
        self.assertFalse(revived["deleted"])
        self.assertNotIn("deleted_at", revived)
        self.assertEqual(revived["data"]["description"], "back")
        self.assertEqual(self.client.collection.count_documents({}), 1)

    def test_upsert_stamps_metadata_and_treats_legacy_documents_as_live(self):
        self.client.collection.insert_one(
            {"nautobot_instance": INSTANCE, "content_type": CONTENT_TYPE, "key": "Legacy", "data": {"name": "Legacy"}}
        )
        self.client.upsert(
            INSTANCE,
            CONTENT_TYPE,
            "Cisco",
            {"name": "Cisco"},
            metadata={"nautobot_role": "HUB", "nautobot_version": "3.2.7"},
        )

        self.assertEqual(self.client.find_data(INSTANCE, CONTENT_TYPE), [{"name": "Cisco"}, {"name": "Legacy"}])
        stored = self.client.get(INSTANCE, CONTENT_TYPE, "Cisco")
        self.assertEqual(stored["nautobot_role"], "HUB")
        self.assertEqual(stored["nautobot_version"], "3.2.7")

    def test_purge_removes_tombstones_too(self):
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"})
        self.client.delete(INSTANCE, CONTENT_TYPE, "Cisco")

        self.assertEqual(self.client.purge(INSTANCE, CONTENT_TYPE, "Cisco"), 1)
        self.assertEqual(self.client.collection.count_documents({}), 0)

    def test_backfill_metadata_only_touches_documents_without_a_role(self):
        self.client.collection.insert_one(
            {"nautobot_instance": INSTANCE, "content_type": CONTENT_TYPE, "key": "Legacy", "data": {"name": "Legacy"}}
        )
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"}, metadata={"nautobot_role": "SPOKE"})
        self.client.collection.insert_one(
            {"nautobot_instance": "Other", "content_type": CONTENT_TYPE, "key": "X", "data": {"name": "X"}}
        )

        self.assertEqual(self.client.backfill_metadata(INSTANCE, {"nautobot_role": "HUB"}), 1)
        self.assertEqual(self.client.get(INSTANCE, CONTENT_TYPE, "Legacy")["nautobot_role"], "HUB")
        self.assertFalse(self.client.get(INSTANCE, CONTENT_TYPE, "Legacy")["deleted"])
        self.assertEqual(self.client.get(INSTANCE, CONTENT_TYPE, "Cisco")["nautobot_role"], "SPOKE")
        self.assertNotIn("nautobot_role", self.client.get("Other", CONTENT_TYPE, "X"))

    def test_ensure_indexes_manages_the_tombstone_ttl_index(self):
        self.client.ensure_indexes(tombstone_ttl_seconds=3600)
        info = self.client.collection.index_information()[TOMBSTONE_TTL_INDEX_NAME]
        self.assertEqual(info["expireAfterSeconds"], 3600)
        self.assertEqual(info["partialFilterExpression"], {"deleted": True})

        self.client.ensure_indexes(tombstone_ttl_seconds=7200)
        self.assertEqual(
            self.client.collection.index_information()[TOMBSTONE_TTL_INDEX_NAME]["expireAfterSeconds"], 7200
        )

        self.client.ensure_indexes(tombstone_ttl_seconds=0)
        self.assertNotIn(TOMBSTONE_TTL_INDEX_NAME, self.client.collection.index_information())

    def test_delete_all_can_be_limited_to_an_instance(self):
        self.client.upsert(INSTANCE, CONTENT_TYPE, "Cisco", {"name": "Cisco"})
        self.client.upsert("Nautobot-B", CONTENT_TYPE, "Juniper", {"name": "Juniper"})

        self.assertEqual(self.client.delete_all(INSTANCE), 1)
        self.assertEqual(self.client.collection.count_documents({}), 1)
        self.assertEqual(self.client.delete_all(), 1)
        self.assertEqual(self.client.collection.count_documents({}), 0)

    def test_from_settings_uses_app_settings(self):
        client = MongoSyncClient.from_settings(
            {
                "mongo_uri": "mongodb://mongo.example.com:27017/",
                "mongo_database": "custom_db",
                "mongo_collection": "custom_collection",
                "mongo_username": "",
                "mongo_password": "",
            }
        )
        self.addCleanup(client.close)

        self.assertEqual(client.database_name, "custom_db")
        self.assertEqual(client.collection_name, "custom_collection")
        self.assertEqual(client.collection.full_name, "custom_db.custom_collection")
