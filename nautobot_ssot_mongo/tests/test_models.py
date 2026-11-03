"""Contracts between the Nautobot-side and MongoDB-side model pairs, the adapters and the migration."""

# pylint: disable=protected-access

import importlib

from django.contrib.contenttypes.models import ContentType
from nautobot.apps.testing import TestCase
from nautobot.extras.models.metadata import MetadataType

from nautobot_ssot_mongo.diffsync import adapters
from nautobot_ssot_mongo.diffsync.models import catalog, inventory
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME

migration = importlib.import_module("nautobot_ssot_mongo.migrations.0001_ssot_mongo_owner_metadatatype")


def _local_models():
    return [
        obj
        for module in (catalog, inventory)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and not name.startswith("Remote") and getattr(obj, "_model", None) is not None
    ]


def _label(model):
    app_label, model_name = ContentType.objects.get_for_model(model).natural_key()
    return f"{app_label}.{model_name}"


class ModelPairContractTestCase(TestCase):
    """Every Nautobot-side model has a MongoDB-side twin with the same identity, attributes and fields."""

    def test_every_local_model_has_a_matching_remote_model(self):
        for local in _local_models():
            with self.subTest(model=local._modelname):
                remote = getattr(adapters.MongoRemoteAdapter, local._modelname)
                self.assertEqual(remote._modelname, local._modelname)
                self.assertEqual(remote._identifiers, local._identifiers)
                self.assertEqual(remote._attributes, local._attributes)
                self.assertEqual(set(remote.model_fields), set(local.model_fields) - {"pk"})
                self.assertEqual(remote._content_type, _label(local._model))

    def test_adapters_register_every_model_exactly_once(self):
        names = sorted(local._modelname for local in _local_models())
        self.assertEqual(sorted(adapters.TOP_LEVEL), names)
        self.assertEqual(len(names), 18)
        for model_name in adapters.TOP_LEVEL:
            with self.subTest(model=model_name):
                self.assertIs(getattr(adapters.MongoNautobotAdapter, model_name)._modelname, model_name)
                self.assertIs(getattr(adapters.MongoRemoteAdapter, model_name)._modelname, model_name)

    def test_ipaddress_is_owned_by_the_nap(self):
        self.assertIn("ipaddress", adapters.NAP_MODELS)
        self.assertNotIn("ipaddress", adapters.NDP_MODELS)


class OwnerMetadataTypeTestCase(TestCase):
    """The migration seeds the MetadataType for exactly the synced models."""

    def test_migration_tracks_exactly_the_synced_models(self):
        tracked = {ContentType.objects.get_for_model(local._model).natural_key() for local in _local_models()}

        self.assertEqual(set(migration.TRACKED_MODELS), tracked)
        self.assertEqual(migration.METADATA_TYPE_NAME, METADATA_TYPE_NAME)

    def test_metadata_type_allows_every_synced_model(self):
        metadata_type = MetadataType.objects.get(name=METADATA_TYPE_NAME)
        allowed = set(metadata_type.content_types.values_list("app_label", "model"))
        tracked = {ContentType.objects.get_for_model(local._model).natural_key() for local in _local_models()}

        self.assertEqual(metadata_type.data_type, "json")
        self.assertEqual(tracked - allowed, set())
