"""Diffsync adapters for nautobot_ssot_mongo.

Every adapter knows all synced models. The NDP and NAP subclasses narrow `top_level` to the objects the enabled
`OwnershipRule`s assign to that role, in their sync order, so each sync only moves one owner's objects: the
"SSoT NDP adapter" pair moves NDP-owned objects between MongoDB and Nautobot, the "SSoT NAP adapter" pair moves
NAP-owned objects.
"""

import logging
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Union

import pydantic
from diffsync import Adapter
from django.contrib.contenttypes.models import ContentType
from nautobot.extras.models.metadata import ObjectMetadata
from nautobot_ssot.contrib import NautobotAdapter
from pymongo.errors import BulkWriteError, PyMongoError

from nautobot_ssot_mongo.constants import DEFAULT_OWNERSHIP_RULES, MONGO_BATCH_LIMIT
from nautobot_ssot_mongo.diffsync.models import (
    CableSSoTModel,
    DeviceSSoTModel,
    DeviceTypeSSoTModel,
    InterfaceSSoTModel,
    IPAddressSSoTModel,
    LocationSSoTModel,
    LocationTypeSSoTModel,
    ManufacturerSSoTModel,
    NamespaceSSoTModel,
    PlatformSSoTModel,
    PrefixSSoTModel,
    RackGroupSSoTModel,
    RackSSoTModel,
    RemoteCableSSoTModel,
    RemoteDeviceSSoTModel,
    RemoteDeviceTypeSSoTModel,
    RemoteInterfaceSSoTModel,
    RemoteIPAddressSSoTModel,
    RemoteLocationSSoTModel,
    RemoteLocationTypeSSoTModel,
    RemoteManufacturerSSoTModel,
    RemoteNamespaceSSoTModel,
    RemotePlatformSSoTModel,
    RemotePrefixSSoTModel,
    RemoteRackGroupSSoTModel,
    RemoteRackSSoTModel,
    RemoteRoleSSoTModel,
    RemoteTenantGroupSSoTModel,
    RemoteTenantSSoTModel,
    RemoteVLANSSoTModel,
    RemoteVRFSSoTModel,
    RoleSSoTModel,
    TenantGroupSSoTModel,
    TenantSSoTModel,
    VLANSSoTModel,
    VRFSSoTModel,
)
from nautobot_ssot_mongo.models import OwnershipRule
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME
from nautobot_ssot_mongo.roles import ROLE_NAP, ROLE_NDP
from nautobot_ssot_mongo.utils.mongo import get_nautobot_instance
from nautobot_ssot_mongo.utils.nautobot import check_version_compatibility, get_status_metadata

logger = logging.getLogger(__name__)

LOCAL_MODELS = (
    ManufacturerSSoTModel,
    PlatformSSoTModel,
    LocationTypeSSoTModel,
    DeviceTypeSSoTModel,
    TenantGroupSSoTModel,
    TenantSSoTModel,
    NamespaceSSoTModel,
    VRFSSoTModel,
    RoleSSoTModel,
    PrefixSSoTModel,
    LocationSSoTModel,
    RackGroupSSoTModel,
    RackSSoTModel,
    DeviceSSoTModel,
    InterfaceSSoTModel,
    VLANSSoTModel,
    IPAddressSSoTModel,
    CableSSoTModel,
)
# Content type label (`dcim.device`) -> DiffSync model name (`device`), for every model the app can sync.
MODEL_NAMES_BY_CONTENT_TYPE = {
    model._model._meta.label_lower: model._modelname  # pylint: disable=protected-access
    for model in LOCAL_MODELS
}
# Every model in the default sync order; the base adapters use it, the owner adapters narrow it per role.
TOP_LEVEL = [MODEL_NAMES_BY_CONTENT_TYPE[f"{app_label}.{model}"] for app_label, model, _ in DEFAULT_OWNERSHIP_RULES]


def owned_model_names(role: str) -> List[str]:
    """Return the DiffSync model names of the types the enabled ownership rules assign to `role`, in sync order."""
    labels = (
        OwnershipRule.objects.filter(owner_role=role, enabled=True)
        .order_by("sync_order", "content_type__app_label", "content_type__model")
        .values_list("content_type__app_label", "content_type__model")
    )
    names = []
    for app_label, model in labels:
        name = MODEL_NAMES_BY_CONTENT_TYPE.get(f"{app_label}.{model}")
        if name is None:
            logger.warning(
                "Ownership rule for %s.%s names a type this app version cannot sync; skipped.", app_label, model
            )
            continue
        names.append(name)
    return names


def sort_related_dicts(items):
    """Order the dictionaries that describe a to-many relation by their values.

    Both sides of a sync must present a many-to-many field in the same order, or the diff reports an update for
    every object whose related rows merely came back from the database in a different sequence.
    """
    return sorted(items, key=lambda item: [str(value) for value in item.values()])


def parents_first(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order records so that each one comes after the record its `parent__name` refers to.

    Records whose parent is not part of `records` keep their relative order at the end, so a missing parent
    surfaces as a creation error for that record rather than being silently reordered.
    """
    remaining = list(records)
    ordered: List[Dict[str, Any]] = []
    seen = set()
    while remaining:
        ready = [record for record in remaining if not record.get("parent__name") or record["parent__name"] in seen]
        if not ready:
            ordered.extend(remaining)
            break
        for record in ready:
            seen.add(record.get("name"))
            ordered.append(record)
        ready_ids = {id(record) for record in ready}
        remaining = [record for record in remaining if id(record) not in ready_ids]
    return ordered


class MongoRemoteAdapter(Adapter):  # pylint: disable=too-many-instance-attributes
    """DiffSync adapter for the MongoDB collection of synced objects."""

    manufacturer = RemoteManufacturerSSoTModel
    platform = RemotePlatformSSoTModel
    locationtype = RemoteLocationTypeSSoTModel
    devicetype = RemoteDeviceTypeSSoTModel
    tenantgroup = RemoteTenantGroupSSoTModel
    tenant = RemoteTenantSSoTModel
    namespace = RemoteNamespaceSSoTModel
    vrf = RemoteVRFSSoTModel
    role = RemoteRoleSSoTModel
    prefix = RemotePrefixSSoTModel
    location = RemoteLocationSSoTModel
    rackgroup = RemoteRackGroupSSoTModel
    rack = RemoteRackSSoTModel
    device = RemoteDeviceSSoTModel
    interface = RemoteInterfaceSSoTModel
    vlan = RemoteVLANSSoTModel
    ipaddress = RemoteIPAddressSSoTModel
    cable = RemoteCableSSoTModel

    top_level = TOP_LEVEL
    # Set on the owner subclasses: the role whose enabled ownership rules select and order `top_level`.
    owner_role: Optional[str] = None

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *args,
        job=None,
        sync=None,
        client=None,
        nautobot_instance: Optional[str] = None,
        read_instances: Optional[Union[str, Iterable[str]]] = None,
        **kwargs,
    ):
        """Initialize the MongoDB adapter.

        Args:
            *args (tuple): Variable length argument list.
            job (object, optional): The SSoT job running this adapter.
            sync (object, optional): The SSoT `Sync` record.
            client (MongoSyncClient): Client wrapping the MongoDB collection.
            nautobot_instance (str, optional): Partition this adapter writes to; defaults to the configured
                `nautobot_instance`.
            read_instances (str | Iterable[str], optional): Partition(s) this adapter loads from; defaults to
                `nautobot_instance`. The NDP passes every NAP instance here to sync in their objects.
            **kwargs (dict): Arbitrary keyword arguments.
        """
        super().__init__(*args, **kwargs)
        if self.owner_role:
            self.top_level = owned_model_names(self.owner_role)
        self.job = job
        self.sync = sync
        self.client = client
        self.nautobot_instance = nautobot_instance or get_nautobot_instance()
        self.read_instances = self.nautobot_instance if read_instances is None else read_instances
        # Role and version fields stamped on every document this adapter writes.
        self.status_metadata = get_status_metadata()
        # (modelname, sorted identifier items) -> nautobot_instance that published the document. Read by
        # `LocalOwnerMixin` on the Nautobot side to stamp the owner of each object it writes.
        self.origin_owners: Dict[tuple, str] = {}
        # Write buffer of the push job: `MongoRemoteMixin` queues one operation per written object and `flush`
        # sends them to MongoDB `MONGO_BATCH_LIMIT` at a time, per content type.
        self._pending: Dict[str, list] = defaultdict(list)
        self.documents_written: Counter = Counter()

    def _log(self):
        """Return the running job's logger, or the module logger outside a job."""
        return getattr(self.job, "logger", None) or logger

    def load_model(self, model_name):
        """Load every live MongoDB document of one model type into the DiffSync store."""
        model_class = getattr(self, model_name)
        content_type = model_class._content_type  # pylint: disable=protected-access
        if self.job and getattr(self.job, "debug", False):
            self.job.logger.debug("Loading %s documents from MongoDB", content_type)
        documents = list(self.client.find(self.read_instances, content_type))
        check_version_compatibility(
            documents, self.status_metadata, content_type, logger_override=getattr(self.job, "logger", None)
        )
        owners = {id(document["data"]): document.get("nautobot_instance") for document in documents}
        records = [document["data"] for document in documents]
        if "parent__name" in model_class.model_fields:
            records = parents_first(records)
        for data in records:
            fields = {
                key: sort_related_dicts(value)
                if isinstance(value, list) and value and isinstance(value[0], dict)
                else value
                for key, value in data.items()
                if key in model_class.model_fields
            }
            instance = model_class(**fields)
            self.add(instance)
            owner = owners[id(data)]
            if owner:
                self.origin_owners[(model_name, tuple(sorted(instance.get_identifiers().items())))] = owner
        if self.job:
            self.job.logger.info("Loaded %s %s object(s) from MongoDB", len(records), model_name)

    def load(self):
        """Sync the SSoT models stored in MongoDB into the DiffSync store."""
        for model_name in self.top_level:
            self.load_model(model_name)

    def sync_to(self, target, *args, **kwargs):  # type: ignore[override]
        """Expose this adapter on `target` so the Nautobot-side models can read `origin_owners`."""
        target._mongo_source = self  # pylint: disable=protected-access
        try:
            return super().sync_to(target, *args, **kwargs)
        finally:
            target._mongo_source = None  # pylint: disable=protected-access

    def queue_write(self, content_type: str, operation) -> None:
        """Buffer one MongoDB write for `content_type` and send the batch once it is full."""
        pending = self._pending[content_type]
        pending.append(operation)
        if len(pending) >= MONGO_BATCH_LIMIT:
            self.flush(content_type)

    def flush(self, content_type: Optional[str] = None) -> None:
        """Send the buffered writes of `content_type`, or of every content type, to MongoDB."""
        names = [content_type] if content_type else [name for name, operations in self._pending.items() if operations]
        for name in names:
            operations = self._pending.pop(name, [])
            for start in range(0, len(operations), MONGO_BATCH_LIMIT):
                self._write_batch(name, operations[start : start + MONGO_BATCH_LIMIT])

    def _write_batch(self, content_type: str, operations: list) -> None:
        """Run one batch of writes; a rejected write fails the job rather than leaving the partition incomplete."""
        try:
            touched = self.client.write_batch(operations)
        except BulkWriteError as error:
            errors = error.details.get("writeErrors") or [{}]
            self._log().error(
                "MongoDB rejected a batch of %d %s document(s) at operation %s: %s. The writes before it were applied.",
                len(operations),
                content_type,
                errors[0].get("index", "?"),
                errors[0].get("errmsg", error),
            )
            raise
        except PyMongoError as error:
            self._log().error("MongoDB batch of %d %s document(s) failed: %s", len(operations), content_type, error)
            raise
        if touched < len(operations):
            self._log().warning(
                "%d of %d %s write(s) matched no MongoDB document.",
                len(operations) - touched,
                len(operations),
                content_type,
            )
        self.documents_written[content_type] += len(operations)
        self._log().info(
            "Wrote %d %s document(s) to MongoDB (%d in this sync)",
            len(operations),
            content_type,
            self.documents_written[content_type],
        )

    def sync_complete(self, *args, **kwargs):
        """Send whatever is still buffered once DiffSync has applied the whole diff."""
        self.flush()
        return super().sync_complete(*args, **kwargs)


class NDPMongoAdapter(MongoRemoteAdapter):
    """SSoT NDP adapter, MongoDB side: the objects owned by the NDP."""

    owner_role = ROLE_NDP


class NAPMongoAdapter(MongoRemoteAdapter):
    """SSoT NAP adapter, MongoDB side: the objects owned by each NAP."""

    owner_role = ROLE_NAP


class MongoNautobotAdapter(NautobotAdapter):
    """DiffSync adapter for Nautobot.

    When `nautobot_instances` is given, the load is scoped to the objects whose owner metadata names one of those
    instances with `ownership_transferred` set, so a pull never touches objects this instance created itself or
    that another instance owns. `None` loads everything, which is what a push (Nautobot -> MongoDB) wants.
    """

    manufacturer = ManufacturerSSoTModel
    platform = PlatformSSoTModel
    locationtype = LocationTypeSSoTModel
    devicetype = DeviceTypeSSoTModel
    tenantgroup = TenantGroupSSoTModel
    tenant = TenantSSoTModel
    namespace = NamespaceSSoTModel
    vrf = VRFSSoTModel
    role = RoleSSoTModel
    prefix = PrefixSSoTModel
    location = LocationSSoTModel
    rackgroup = RackGroupSSoTModel
    rack = RackSSoTModel
    device = DeviceSSoTModel
    interface = InterfaceSSoTModel
    vlan = VLANSSoTModel
    ipaddress = IPAddressSSoTModel
    cable = CableSSoTModel

    top_level = TOP_LEVEL
    # Set on the owner subclasses: the role whose enabled ownership rules select and order `top_level`.
    owner_role: Optional[str] = None

    def __init__(self, *args, nautobot_instances: Optional[Iterable[str]] = None, **kwargs):
        """Capture the remote `nautobot_instances` the load is scoped to (`None` for an unscoped load)."""
        super().__init__(*args, **kwargs)
        if self.owner_role:
            self.top_level = owned_model_names(self.owner_role)
        self.nautobot_instances = None if nautobot_instances is None else list(nautobot_instances)

    def _owned_pks(self, model):
        """Return PKs of `model` owned by the selected instances with transferred ownership, or None to load all."""
        if self.nautobot_instances is None:
            return None
        if not self.nautobot_instances:
            return []
        content_type = ContentType.objects.get_for_model(model)
        return list(
            ObjectMetadata.objects.filter(
                metadata_type__name=METADATA_TYPE_NAME,
                assigned_object_type=content_type,
                _value__owner__in=self.nautobot_instances,
                _value__ownership_transferred=True,
            ).values_list("assigned_object_id", flat=True)
        )

    def _handle_to_many_relationship(self, database_object, diffsync_model, parameter_name):
        """Load a to-many field in the same order the MongoDB adapter presents it."""
        return sort_related_dicts(super()._handle_to_many_relationship(database_object, diffsync_model, parameter_name))

    def _load_objects(self, diffsync_model):
        """Load `diffsync_model` objects, scoped to the selected owners when set."""
        parameter_names = diffsync_model.get_synced_attributes()
        queryset = diffsync_model._get_queryset()  # pylint: disable=protected-access
        allowed_pks = self._owned_pks(diffsync_model._model)  # pylint: disable=protected-access
        if allowed_pks is not None:
            queryset = queryset.filter(pk__in=allowed_pks)
        for database_object in queryset:
            self._load_single_object(database_object, diffsync_model, parameter_names)

    def _load_single_object(self, database_object, diffsync_model, parameter_names):
        """Dispatch to `from_orm_record` when the model defines it (cables, VLANs); else delegate to contrib."""
        custom = getattr(diffsync_model, "from_orm_record", None)
        if callable(custom):
            kwargs = custom(database_object)
            if kwargs is None:
                return None
            kwargs.setdefault("pk", database_object.pk)
            try:
                instance = diffsync_model(**kwargs)
            except pydantic.ValidationError as error:
                raise ValueError(f"Parameters: {kwargs}") from error
            self.add(instance)
            return instance
        return super()._load_single_object(database_object, diffsync_model, parameter_names)


class NDPNautobotAdapter(MongoNautobotAdapter):
    """SSoT NDP adapter, Nautobot side: the objects owned by the NDP."""

    owner_role = ROLE_NDP


class NAPNautobotAdapter(MongoNautobotAdapter):
    """SSoT NAP adapter, Nautobot side: the objects owned by each NAP."""

    owner_role = ROLE_NAP


MONGO_ADAPTERS = {ROLE_NDP: NDPMongoAdapter, ROLE_NAP: NAPMongoAdapter}
NAUTOBOT_ADAPTERS = {ROLE_NDP: NDPNautobotAdapter, ROLE_NAP: NAPNautobotAdapter}
