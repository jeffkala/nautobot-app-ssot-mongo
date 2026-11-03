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

Which types make up the catalog and the inventory is data: one `OwnershipRule` per type, defined on the NDP. The
NDP's "Nautobot to MongoDB" run publishes the rules next to the catalog, and a NAP refreshes its copy of them
before either of its jobs loads anything. The Transfer Ownership job, on the NDP, moves a type to the other role.
"""

import logging
from contextlib import nullcontext

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse
from nautobot.apps.jobs import BooleanVar, DryRunVar, Job, MultiChoiceVar, ObjectVar, register_jobs
from nautobot.apps.utils import get_route_for_model
from nautobot_ssot.jobs.base import DataMapping, DataSource, DataTarget
from pymongo.errors import PyMongoError

from nautobot_ssot_mongo.changelog import changelog_disabled
from nautobot_ssot_mongo.constants import JOB_SOFT_TIME_LIMIT, JOB_TIME_LIMIT
from nautobot_ssot_mongo.diff import CustomOrderingDiff, sync_flags
from nautobot_ssot_mongo.diffsync.adapters import (
    MONGO_ADAPTERS,
    NAUTOBOT_ADAPTERS,
    MongoNautobotAdapter,
    owned_model_names,
)
from nautobot_ssot_mongo.models import OwnershipRule, model_display_name, ownership_rules_writable
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME
from nautobot_ssot_mongo.ownership import apply_owner_change, apply_rules, publish_rules, pull_rules
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


def model_label(model_name):
    """Return the display name of a DiffSync model's Nautobot model, for example `IP Address`."""
    return model_display_name(getattr(MongoNautobotAdapter, model_name)._model)  # pylint: disable=protected-access


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
            "Synced objects": ", ".join(model_label(model) for model in owned_model_names(owner)) or "none enabled",
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
        for model in owned_model_names(cls.owner_role()):
            label = model_label(model)
            model_class = getattr(MongoNautobotAdapter, model)._model  # pylint: disable=protected-access
            route_name = get_route_for_model(model_class, "list")
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
        if get_role() == ROLE_NAP:
            self.refresh_ownership_rules()
        super().run(dryrun=self.dryrun, memory_profiling=self.memory_profiling, *args, **kwargs)

    def refresh_ownership_rules(self):
        """Make this NAP's ownership rules match the ones the NDP published, before any adapter reads them.

        The rules decide which types each job moves, so they are refreshed even on a dry run. A NAP whose NDP has
        not published rules yet keeps the rules it has, which are the app defaults on a fresh install.
        """
        app_settings = get_app_settings()
        ndp_instance = get_ndp_instance(app_settings)
        client = self.get_mongo_client()
        payloads = pull_rules(client, ndp_instance)
        if not payloads:
            self.logger.warning(
                "NDP instance '%s' has not published ownership rules; using the rules stored on this NAP.",
                ndp_instance,
            )
            return
        apply_rules(
            payloads,
            client,
            get_nautobot_instance(app_settings),
            ROLE_NAP,
            metadata=get_status_metadata(),
            log=self.logger,
        )

    def before_sync(self):
        """Hook run just before the diff is applied; the NDP's push publishes the ownership rules here."""

    def execute_sync(self):
        """Perform the CRUD operations that sync `self.diff` into the target, deferring deletions until last.

        The objects are written without a change log when `record_changelog` is off.
        """
        if self.source_adapter is None or self.target_adapter is None:
            self.logger.warning("One of the adapters was not properly initialized prior to synchronization.")
            return
        owner = self.owner_role()
        self.logger.info("Performing CRUD operations to sync %s-owned objects into %s.", owner, self.Meta.data_target)
        self.before_sync()
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

    def before_sync(self):
        """Publish the ownership rules from the NDP, so every NAP refreshes them before its next job."""
        if get_role() == ROLE_NDP:
            publish_rules(
                self.target_adapter.client,
                get_nautobot_instance(),
                metadata=get_status_metadata(),
                log=self.logger,
            )

    def load_target_adapter(self):
        """Load this instance's MongoDB partition, first stamping role and version fields on legacy documents."""
        client = self.get_mongo_client()
        backfilled = client.backfill_metadata(get_nautobot_instance(), get_status_metadata())
        if backfilled:
            self.logger.info("Stamped role and version fields on %s pre-existing document(s).", backfilled)
        self.target_adapter = self.get_mongo_adapter(client)
        self.target_adapter.load()


class TransferOwnership(Job):
    """Move one content type to the other role, on the NDP."""

    rule = ObjectVar(
        model=OwnershipRule,
        label="Ownership rule",
        description="The type to hand to the other role.",
    )
    dryrun = DryRunVar(description="Report what the transfer would do without changing anything.")

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta data for TransferOwnership."""

        name = "Transfer Ownership"
        description = (
            "Hand one federated type to the other role. On the NDP this releases or retires its own copies and "
            "publishes the new rules; every NAP applies its side when it next runs a sync job. Afterwards, run "
            "Nautobot to MongoDB on the new owners and MongoDB to Nautobot on the others."
        )
        has_sensitive_variables = False

    def run(self, rule, dryrun):  # pylint: disable=arguments-differ
        """Flip the rule's owner, settle the NDP's side of the move and publish the rules."""
        app_settings = get_app_settings()
        role = get_role(app_settings)
        if role != ROLE_NDP:
            raise ValueError("Ownership is transferred on the NDP; this instance is a NAP.")
        new_role = other_role(rule.owner_role)
        self.logger.info("Transferring %s from the %s to the %s.", rule.content_type_label, rule.owner_role, new_role)
        if dryrun:
            self.logger.info(
                "Dry run: the rule would be updated, the NDP would %s its %s objects and the rules would be published.",
                "release" if new_role == ROLE_NDP else "retire the documents of",
                rule.content_type_label,
            )
            return
        with ownership_rules_writable():
            rule.owner_role = new_role
            rule.validated_save()
        client = MongoSyncClient.from_settings(app_settings)
        client.ensure_indexes(tombstone_ttl_seconds=get_tombstone_ttl_seconds(app_settings))
        instance = get_nautobot_instance(app_settings)
        metadata = get_status_metadata()
        apply_owner_change(rule.content_type, new_role, client, instance, role, metadata=metadata, log=self.logger)
        publish_rules(client, instance, metadata=metadata, log=self.logger)
        self.logger.info(
            "Done. Run Nautobot to MongoDB on every %s instance, then MongoDB to Nautobot on the %s side, to move "
            "the objects.",
            new_role,
            other_role(new_role),
        )


jobs = [MongoDataSource, MongoDataTarget, TransferOwnership]
register_jobs(*jobs)
