"""Jobs for the MongoDB SSoT integration.

Both jobs exist on every instance; the configured `nautobot_role` decides whose objects they move. Each run follows
the same sequence for one owner's objects:

1. The owner loads the objects it owns into MongoDB ("Nautobot to MongoDB" on the owner).
2. The owner's SSoT adapter syncs the stored SSoT models into the DiffSync store ("MongoDB to Nautobot" on the
   other role).
3. CRUD operations sync the objects into the consuming Nautobot instance, which adopts matching objects it already
   has and stamps every synced object with the owner's name.
4. The operation is finalized and its diff recorded on the SSoT dashboard.

| Role  | Nautobot to MongoDB                       | MongoDB to Nautobot                           |
| ----- | ----------------------------------------- | --------------------------------------------- |
| hub   | loads the hub-owned catalog into MongoDB  | syncs in the inventory of the selected spokes |
| spoke | loads this spoke's inventory into MongoDB | syncs in the hub-owned catalog                |

Which types make up the catalog and the inventory is data: one `OwnershipRule` per type, defined on the hub. The
hub's "Nautobot to MongoDB" run publishes the rules next to the catalog, and a spoke refreshes its copy of them
before either of its jobs loads anything. The Transfer Ownership job, on the hub, moves a type to the other role.

Every successful run ends by writing the instance's heartbeat document to MongoDB, and every run starts and ends by
refreshing the `FederatedInstance` registry from the heartbeats of all instances.
"""

import logging
from contextlib import nullcontext

from django.urls import reverse
from nautobot.apps.jobs import BooleanVar, DryRunVar, Job, MultiChoiceVar, ObjectVar, register_jobs
from nautobot.apps.utils import get_route_for_model
from nautobot_ssot.jobs.base import DataMapping, DataSource, DataTarget

from nautobot_ssot_mongo.changelog import changelog_disabled
from nautobot_ssot_mongo.constants import JOB_SOFT_TIME_LIMIT, JOB_TIME_LIMIT
from nautobot_ssot_mongo.diff import CustomOrderingDiff, sync_flags
from nautobot_ssot_mongo.diffsync.adapters import (
    MONGO_ADAPTERS,
    NAUTOBOT_ADAPTERS,
    MongoNautobotAdapter,
    owned_model_names,
)
from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule, model_display_name, ownership_rules_writable
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME
from nautobot_ssot_mongo.ownership import apply_owner_change, apply_rules, publish_rules, pull_rules
from nautobot_ssot_mongo.registry import PUBLISH, PULL, refresh_registry, write_heartbeat
from nautobot_ssot_mongo.roles import ROLE_HUB, ROLE_SPOKE, get_hub_instance, get_role, other_role
from nautobot_ssot_mongo.utils.mongo import (
    MongoSyncClient,
    get_app_settings,
    get_nautobot_instance,
    get_tombstone_ttl_seconds,
)
from nautobot_ssot_mongo.utils.nautobot import get_status_metadata

logger = logging.getLogger(__name__)

name = "MongoDB SSoT"  # pylint: disable=invalid-name


def model_label(model_name):
    """Return the display name of a DiffSync model's Nautobot model, for example `IP Address`."""
    return model_display_name(getattr(MongoNautobotAdapter, model_name)._model)  # pylint: disable=protected-access


def get_remote_instance_choices():
    """`MultiChoiceVar` choices: the registered instances of the other role, from the federated instance registry.

    Evaluated when the job form renders, from the local database only. Every sync job refreshes the registry, so a
    new spoke is offered here once it has published and this instance has run any job since; a pull with nothing
    selected reads every spoke partition in the collection regardless.
    """
    names = FederatedInstance.objects.filter(role=other_role(get_role())).values_list("name", flat=True)
    return [(name, name) for name in names]


class MongoSyncMixin:
    """Behaviour shared by both sync directions."""

    debug = BooleanVar(description="Enable for more verbose debug logging", default=False)

    # `PUBLISH` or `PULL`: the heartbeat timestamp a finished run of this job sets.
    heartbeat_field = None
    _mongo_client = None

    @classmethod
    def owner_role(cls):
        """Role (hub or spoke) that owns the objects this job moves, given the configured role."""
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
        if role == ROLE_SPOKE:
            information["Hub instance"] = get_hub_instance(app_settings)
        else:
            information["Spoke instances found"] = ", ".join(i for i, _ in get_remote_instance_choices()) or "none"
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
        """Return the job's MongoDB client, connecting and making sure the indexes exist on first use."""
        if self._mongo_client is None:
            client = MongoSyncClient.from_settings()
            client.ensure_indexes(tombstone_ttl_seconds=get_tombstone_ttl_seconds())
            self._mongo_client = client
        return self._mongo_client

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
        if get_role() == ROLE_SPOKE:
            self.refresh_ownership_rules()
        self.refresh_registry()
        super().run(dryrun=self.dryrun, memory_profiling=self.memory_profiling, *args, **kwargs)

    def refresh_registry(self):
        """Mirror the heartbeat documents in MongoDB into the federated instance registry, dry run included."""
        refresh_registry(self.get_mongo_client(), log=self.logger)

    def record_heartbeat(self):
        """Write this instance's heartbeat after a successful sync, then refresh the registry from every heartbeat."""
        app_settings = get_app_settings()
        client = self.get_mongo_client()
        write_heartbeat(
            client,
            get_nautobot_instance(app_settings),
            get_role(app_settings),
            self.heartbeat_field,
            metadata=get_status_metadata(),
            log=self.logger,
        )
        refresh_registry(client, log=self.logger)

    def refresh_ownership_rules(self):
        """Make this spoke's ownership rules match the ones the hub published, before any adapter reads them.

        The rules decide which types each job moves, so they are refreshed even on a dry run. A spoke whose hub has
        not published rules yet keeps the rules it has, which are the app defaults on a fresh install.
        """
        app_settings = get_app_settings()
        hub_instance = get_hub_instance(app_settings)
        client = self.get_mongo_client()
        payloads = pull_rules(client, hub_instance)
        if not payloads:
            self.logger.warning(
                "The hub instance '%s' has not published ownership rules; using the rules stored on this spoke.",
                hub_instance,
            )
            return
        apply_rules(
            payloads,
            client,
            get_nautobot_instance(app_settings),
            ROLE_SPOKE,
            metadata=get_status_metadata(),
            log=self.logger,
        )

    def before_sync(self):
        """Hook run just before the diff is applied; the hub's push publishes the ownership rules here."""

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
        self.record_heartbeat()


class MongoDataSource(MongoSyncMixin, DataSource):
    """Sync the objects owned by the other role from MongoDB into this Nautobot instance."""

    heartbeat_field = PULL
    nautobot_instances = MultiChoiceVar(
        choices=get_remote_instance_choices,
        required=False,
        label="Nautobot instances",
        description=(
            "Partitions to sync in. On the hub, the spoke instances to read; every spoke found when left empty. "
            "Ignored on a spoke, which always reads the configured hub instance."
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
            "Sync the objects owned by the other role from MongoDB into this instance: the hub-owned catalog on a "
            "spoke, or the inventory (locations, racks, devices, interfaces, VLANs, IP addresses, cables) of the "
            "selected spokes on the hub. Only objects this job synced in before are reconciled; objects created "
            "locally are left alone."
        )

    @classmethod
    def owner_role(cls):
        """The other role owns the objects this instance syncs in."""
        return other_role(get_role())

    def get_source_instances(self, client):
        """Return the partitions to read: the hub's on a spoke, the selected (or every) spoke's on the hub."""
        app_settings = get_app_settings()
        selected = self.selected_instances()
        if get_role(app_settings) == ROLE_SPOKE:
            hub_instance = get_hub_instance(app_settings)
            if selected:
                self.logger.info("Ignoring the selected instances; a spoke always reads the hub partition.")
            self.logger.info("Reading hub-owned objects published by the hub instance '%s'.", hub_instance)
            return [hub_instance]
        own_instance = get_nautobot_instance(app_settings)
        spoke_instances = selected or client.instances(exclude_role=ROLE_HUB, exclude_instance=own_instance)
        self.logger.info(
            "Reading spoke-owned objects published by %s spoke instance(s): %s",
            len(spoke_instances),
            ", ".join(spoke_instances) or "none found",
        )
        return list(spoke_instances)

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

    heartbeat_field = PUBLISH

    class Meta:  # pylint: disable=too-few-public-methods
        """Meta data for MongoDataTarget."""

        soft_time_limit = JOB_SOFT_TIME_LIMIT
        time_limit = JOB_TIME_LIMIT
        name = "Nautobot to MongoDB"
        data_source = "Nautobot"
        data_target = "MongoDB"
        description = (
            "Load the objects this instance owns into MongoDB: the catalog from the hub, or the inventory "
            "(locations, racks, devices, interfaces, VLANs, IP addresses, cables) from a spoke. Removed objects "
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
        """Publish the ownership rules from the hub, so every spoke refreshes them before its next job."""
        if get_role() == ROLE_HUB:
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
    """Move one content type to the other role, on the hub."""

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
            "Hand one federated type to the other role. On the hub this releases or retires its own copies and "
            "publishes the new rules; every spoke applies its side when it next runs a sync job. Afterwards, run "
            "Nautobot to MongoDB on the new owners and MongoDB to Nautobot on the others."
        )
        has_sensitive_variables = False

    def run(self, rule, dryrun):  # pylint: disable=arguments-differ
        """Flip the rule's owner, settle the hub's side of the move and publish the rules."""
        app_settings = get_app_settings()
        role = get_role(app_settings)
        if role != ROLE_HUB:
            raise ValueError("Ownership is transferred on the hub; this instance is a spoke.")
        new_role = other_role(rule.owner_role)
        self.logger.info("Transferring %s from the %s to the %s.", rule.content_type_label, rule.owner_role, new_role)
        if dryrun:
            self.logger.info(
                "Dry run: the rule would be updated, the hub would %s its %s objects and the rules would be published.",
                "release" if new_role == ROLE_HUB else "retire the documents of",
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
