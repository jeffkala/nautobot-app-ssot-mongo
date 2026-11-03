"""Jobs for the MongoDB SSoT integration.

Both jobs exist on every instance; the configured `nautobot_role` decides whose objects they move. Each run follows
the same sequence for one owner's objects:

1. The owner loads the objects it owns into MongoDB ("Nautobot to MongoDB" on the owner).
2. The owner's SSoT adapter syncs the stored SSoT models into the DiffSync store ("MongoDB to Nautobot" on the
   other role).
3. CRUD operations sync the objects into the consuming Nautobot instance, which adopts matching objects it already
   has and stamps every synced object with the owner's name.
4. The operation is finalized and its diff recorded on the SSoT dashboard.

| Role | Nautobot to MongoDB                      | MongoDB to Nautobot                                |
| ---- | ---------------------------------------- | -------------------------------------------------- |
| NDP  | loads the NDP-owned catalog into MongoDB | syncs in the inventory of the selected NAPs        |
| NAP  | loads this NAP's inventory into MongoDB  | syncs in the NDP-owned catalog                     |

The catalog is manufacturers, platforms, location types, device types, tenancy, namespaces, VRFs, roles and
prefixes; the inventory is locations, rack groups, racks, devices, interfaces, VLANs, IP addresses and cables.
"""

import logging
from contextlib import nullcontext

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse
from nautobot.apps.jobs import BooleanVar, MultiChoiceVar, register_jobs
from nautobot_ssot.jobs.base import DataMapping, DataSource, DataTarget
from pymongo.errors import PyMongoError

from nautobot_ssot_mongo.changelog import changelog_disabled
from nautobot_ssot_mongo.constants import JOB_SOFT_TIME_LIMIT, JOB_TIME_LIMIT
from nautobot_ssot_mongo.diff import CustomOrderingDiff, sync_flags
from nautobot_ssot_mongo.diffsync.adapters import MODELS_BY_OWNER, MONGO_ADAPTERS, NAUTOBOT_ADAPTERS
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME
from nautobot_ssot_mongo.roles import ROLE_NAP, ROLE_NDP, get_ndp_instance, get_role, other_role
from nautobot_ssot_mongo.utils.mongo import (
    MongoSyncClient,
    get_app_settings,
    get_nautobot_instance,
    get_tombstone_ttl_seconds,
)
from nautobot_ssot_mongo.utils.nautobot import get_status_metadata

logger = logging.getLogger(__name__)

name = "MongoDB SSoT"  # pylint: disable=invalid-name

REMOTE_INSTANCES_CACHE_KEY = "nautobot_ssot_mongo.remote_instances"
REMOTE_INSTANCES_CACHE_TIMEOUT = 60

# DiffSync model name -> (label, Nautobot list view route)
DATA_MAPPINGS = {
    "manufacturer": ("Manufacturer", "dcim:manufacturer_list"),
    "platform": ("Platform", "dcim:platform_list"),
    "locationtype": ("Location Type", "dcim:locationtype_list"),
    "devicetype": ("Device Type", "dcim:devicetype_list"),
    "tenantgroup": ("Tenant Group", "tenancy:tenantgroup_list"),
    "tenant": ("Tenant", "tenancy:tenant_list"),
    "namespace": ("Namespace", "ipam:namespace_list"),
    "vrf": ("VRF", "ipam:vrf_list"),
    "role": ("Role", "extras:role_list"),
    "prefix": ("Prefix", "ipam:prefix_list"),
    "location": ("Location", "dcim:location_list"),
    "rackgroup": ("Rack Group", "dcim:rackgroup_list"),
    "rack": ("Rack", "dcim:rack_list"),
    "device": ("Device", "dcim:device_list"),
    "interface": ("Interface", "dcim:interface_list"),
    "vlan": ("VLAN", "ipam:vlan_list"),
    "ipaddress": ("IP Address", "ipam:ipaddress_list"),
    "cable": ("Cable", "dcim:cable_list"),
}


def get_remote_instance_choices():
    """`MultiChoiceVar` choices: the partitions published by the other role, or `[]` when MongoDB is unreachable.

    Evaluated when the job form renders. The distinct-instance query walks the collection's index, so the result
    is cached for `REMOTE_INSTANCES_CACHE_TIMEOUT` seconds; a newly published partition still shows up without a
    restart. An unreachable MongoDB yields an empty list that is not cached, so the form still renders.
    """
    cached = cache.get(REMOTE_INSTANCES_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        app_settings = get_app_settings()
        client = MongoSyncClient.from_settings(app_settings)
        instances = client.instances(
            exclude_role=get_role(app_settings), exclude_instance=get_nautobot_instance(app_settings)
        )
    except (PyMongoError, ImproperlyConfigured):
        logger.warning("Could not list the remote Nautobot instances from MongoDB", exc_info=True)
        return []
    choices = [(instance, instance) for instance in instances]
    cache.set(REMOTE_INSTANCES_CACHE_KEY, choices, REMOTE_INSTANCES_CACHE_TIMEOUT)
    return choices


class MongoSyncMixin:
    """Behaviour shared by both sync directions."""

    debug = BooleanVar(description="Enable for more verbose debug logging", default=False)

    @classmethod
    def owner_role(cls):
        """Role (NDP or NAP) that owns the objects this job moves, given the configured role."""
        raise NotImplementedError

    @classmethod
    def config_information(cls):
        """Dictionary describing the MongoDB connection and role in use (credentials excluded)."""
        app_settings = get_app_settings()
        role = get_role(app_settings)
        owner = cls.owner_role()
        information = {
            "MongoDB URI": app_settings.get("mongo_uri", ""),
            "Database": app_settings.get("mongo_database", ""),
            "Collection": app_settings.get("mongo_collection", ""),
            "Nautobot role": role,
            "Nautobot instance": get_nautobot_instance(app_settings),
            "Objects owned by": owner,
            "Synced objects": ", ".join(DATA_MAPPINGS[model][0] for model in MODELS_BY_OWNER[owner]),
            "Owner metadata type": METADATA_TYPE_NAME,
            "Tombstone TTL (days)": get_tombstone_ttl_seconds(app_settings) // 86400,
        }
        if role == ROLE_NAP:
            information["NDP instance"] = get_ndp_instance(app_settings)
        else:
            information["NAP instances found"] = ", ".join(i for i, _ in get_remote_instance_choices()) or "none"
        return information

    @classmethod
    def data_mappings(cls):
        """List describing the data mappings involved in this sync."""
        mappings = []
        for model in MODELS_BY_OWNER[cls.owner_role()]:
            label, route_name = DATA_MAPPINGS[model]
            mappings.append(DataMapping(f"{label} document", None, label, reverse(route_name)))
        return tuple(mappings)

    def selected_instances(self):
        """Return the `nautobot_instances` chosen on the job form, or `[]` when the job has no selection."""
        selected = self.__dict__.get("nautobot_instances")
        return list(selected) if isinstance(selected, (list, tuple)) else []

    def get_mongo_client(self):
        """Connect to MongoDB using the app settings and make sure the indexes exist."""
        client = MongoSyncClient.from_settings()
        client.ensure_indexes(tombstone_ttl_seconds=get_tombstone_ttl_seconds())
        return client

    def get_mongo_adapter(self, client, read_instances=None):
        """Build the MongoDB side of the owner's SSoT adapter."""
        return MONGO_ADAPTERS[self.owner_role()](job=self, sync=self.sync, client=client, read_instances=read_instances)

    def get_nautobot_adapter(self, nautobot_instances=None):
        """Build the Nautobot side of the owner's SSoT adapter, scoped to `nautobot_instances` when given."""
        return NAUTOBOT_ADAPTERS[self.owner_role()](job=self, sync=self.sync, nautobot_instances=nautobot_instances)

    def run(self, dryrun, memory_profiling, debug, *args, **kwargs):  # pylint: disable=arguments-differ
        """Perform data synchronization."""
        self.debug = debug
        self.diffsync_flags = sync_flags(debug)
        self.dryrun = dryrun
        self.memory_profiling = memory_profiling
        self.nautobot_instances = list(kwargs.pop("nautobot_instances", None) or [])
        # Only "MongoDB to Nautobot" offers the option; a push never writes Nautobot objects.
        self.record_changelog = bool(kwargs.pop("record_changelog", True))
        super().run(dryrun=self.dryrun, memory_profiling=self.memory_profiling, *args, **kwargs)

    def execute_sync(self):
        """Perform the CRUD operations that sync `self.diff` into the target, deferring deletions until last.

        The objects are written without a change log when `record_changelog` is off.
        """
        if self.source_adapter is None or self.target_adapter is None:
            self.logger.warning("One of the adapters was not properly initialized prior to synchronization.")
            return
        owner = self.owner_role()
        self.logger.info("Performing CRUD operations to sync %s-owned objects into %s.", owner, self.Meta.data_target)
        if self.record_changelog:
            changelog = nullcontext()
        else:
            self.logger.info(
                "Change log recording is off for this sync: no change log entries, webhooks or job hooks "
                "for the objects it writes."
            )
            changelog = changelog_disabled()
        with changelog:
            self.source_adapter.sync_to(self.target_adapter, flags=self.diffsync_flags, diff_class=CustomOrderingDiff)
        self.logger.info("Finalization of operation: %s-owned objects synced into %s.", owner, self.Meta.data_target)


class MongoDataSource(MongoSyncMixin, DataSource):
    """Sync the objects owned by the other role from MongoDB into this Nautobot instance."""

    nautobot_instances = MultiChoiceVar(
        choices=get_remote_instance_choices,
        required=False,
        label="Nautobot instances",
        description=(
            "Partitions to sync in. On the NDP, the NAP instances to read; every NAP found when left empty. "
            "Ignored on a NAP, which always reads the configured NDP instance."
        ),
    )
    record_changelog = BooleanVar(
        label="Record change log",
        description=(
            "Write a change log entry for every object this sync creates, updates or deletes. Turn off for the "
            "first pull of a large inventory: the entries add nothing the sync log does not have, and skipping "
            "them cuts the time per object and the processing after the sync. Webhooks and job hooks do not "
            "fire for objects written without a change log."
        ),
        default=True,
    )

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta data for MongoDataSource."""

        soft_time_limit = JOB_SOFT_TIME_LIMIT
        time_limit = JOB_TIME_LIMIT
        name = "MongoDB to Nautobot"
        data_source = "MongoDB"
        data_target = "Nautobot"
        description = (
            "Sync the objects owned by the other role from MongoDB into this instance: the NDP-owned catalog on a "
            "NAP, or the inventory (locations, racks, devices, interfaces, VLANs, IP addresses, cables) of the "
            "selected NAPs on the NDP. Only objects this job synced in before are reconciled; objects created "
            "locally are left alone."
        )

    @classmethod
    def owner_role(cls):
        """The other role owns the objects this instance syncs in."""
        return other_role(get_role())

    def get_source_instances(self, client):
        """Return the partitions to read: the NDP's on a NAP, the selected (or every) NAP's on the NDP."""
        app_settings = get_app_settings()
        selected = self.selected_instances()
        if get_role(app_settings) == ROLE_NAP:
            ndp_instance = get_ndp_instance(app_settings)
            if selected:
                self.logger.info("Ignoring the selected instances; a NAP always reads the NDP partition.")
            self.logger.info("Reading NDP-owned objects published by NDP instance '%s'.", ndp_instance)
            return [ndp_instance]
        own_instance = get_nautobot_instance(app_settings)
        nap_instances = selected or client.instances(exclude_role=ROLE_NDP, exclude_instance=own_instance)
        self.logger.info(
            "Reading NAP-owned objects published by %s NAP instance(s): %s",
            len(nap_instances),
            ", ".join(nap_instances) or "none found",
        )
        return list(nap_instances)

    def load_source_adapter(self):
        """Sync the SSoT models stored in MongoDB into the DiffSync store."""
        client = self.get_mongo_client()
        self.source_instances = self.get_source_instances(client)  # pylint: disable=attribute-defined-outside-init
        self.source_adapter = self.get_mongo_adapter(client, read_instances=self.source_instances)
        self.source_adapter.load()
        self.logger.info("Synced %s-owned SSoT models from MongoDB into the DiffSync store.", self.owner_role())

    def load_target_adapter(self):
        """Load the objects previously synced in from the selected partitions into DiffSync models."""
        self.target_adapter = self.get_nautobot_adapter(nautobot_instances=getattr(self, "source_instances", []))
        self.target_adapter.load()


class MongoDataTarget(MongoSyncMixin, DataTarget):
    """Load the objects this instance owns into MongoDB."""

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta data for MongoDataTarget."""

        soft_time_limit = JOB_SOFT_TIME_LIMIT
        time_limit = JOB_TIME_LIMIT
        name = "Nautobot to MongoDB"
        data_source = "Nautobot"
        data_target = "MongoDB"
        description = (
            "Load the objects this instance owns into MongoDB: the catalog from the NDP, or the inventory "
            "(locations, racks, devices, interfaces, VLANs, IP addresses, cables) from a NAP. Removed objects "
            "become tombstones that expire after the configured TTL."
        )

    @classmethod
    def owner_role(cls):
        """This instance owns the objects it loads into MongoDB."""
        return get_role()

    def load_source_adapter(self):
        """Load data from Nautobot into DiffSync models."""
        self.source_adapter = self.get_nautobot_adapter()
        self.source_adapter.load()

    def load_target_adapter(self):
        """Load this instance's MongoDB partition, first stamping role and version fields on legacy documents."""
        client = self.get_mongo_client()
        backfilled = client.backfill_metadata(get_nautobot_instance(), get_status_metadata())
        if backfilled:
            self.logger.info("Stamped role and version fields on %s pre-existing document(s).", backfilled)
        self.target_adapter = self.get_mongo_adapter(client)
        self.target_adapter.load()


jobs = [MongoDataSource, MongoDataTarget]
register_jobs(*jobs)
