"""Models for nautobot_ssot_mongo.

The app federates Nautobot objects by content type: every type the app can sync has exactly one owning role, and
that assignment is data, an `OwnershipRule` per type. The rules are defined on the hub, published to MongoDB by its
"Nautobot to MongoDB" job and refreshed on every spoke before each of its sync jobs, so every instance syncs the same
types in the same order.

The members of the federation are data too: a `FederatedInstance` per instance, kept in step with the heartbeat
document each instance writes to MongoDB at the end of every successful sync job.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import reduce
from operator import or_

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.models import OrganizationalModel, extras_features

from nautobot_ssot_mongo.choices import RoleChoices
from nautobot_ssot_mongo.constants import DEFAULT_OWNERSHIP_RULES
from nautobot_ssot_mongo.roles import ROLE_SPOKE, get_role

# True while a spoke mirrors the rules the hub published, and while the Transfer Ownership job moves a type between
# roles: the two writes `OwnershipRule.clean()` refuses from the UI and the API.
_rules_write_allowed: ContextVar[bool] = ContextVar("nautobot_ssot_mongo_rules_write_allowed", default=False)


@contextmanager
def ownership_rules_writable():
    """Allow the writes `OwnershipRule.clean()` otherwise rejects, for the duration of the block."""
    token = _rules_write_allowed.set(True)
    try:
        yield
    finally:
        _rules_write_allowed.reset(token)


def model_display_name(model_class) -> str:
    """Return a model's verbose name with each word capitalized and acronyms kept, for example `IP Address`."""
    return " ".join(word[0].upper() + word[1:] for word in str(model_class._meta.verbose_name).split())


def supported_content_types() -> Q:
    """Return the `ContentType` filter matching only the models the app can federate."""
    return reduce(or_, (Q(app_label=app_label, model=model) for app_label, model, _ in DEFAULT_OWNERSHIP_RULES))


@extras_features("graphql")
class OwnershipRule(OrganizationalModel):  # pylint: disable=too-many-ancestors
    """Which role owns the objects of one content type, and where that type sits in a sync."""

    content_type = models.OneToOneField(
        to=ContentType,
        on_delete=models.PROTECT,
        related_name="ssot_mongo_ownership_rule",
        limit_choices_to=supported_content_types,
        help_text="The federated object type. Only the types this app can sync are offered.",
    )
    owner_role = models.CharField(
        max_length=5,
        choices=RoleChoices,
        help_text=(
            "Role whose instances own the objects of this type and publish them to MongoDB. "
            "Instances of the other role sync them in and treat them as read-only."
        ),
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Uncheck to leave this type out of both sync jobs on every instance.",
    )
    sync_order = models.PositiveSmallIntegerField(
        help_text=(
            "Position of this type in a sync. A type must come after every type its objects reference, "
            "for example device types after manufacturers."
        ),
    )

    is_dynamic_group_associable_model = False
    natural_key_field_names = ["content_type"]

    class Meta:
        """Meta options for OwnershipRule."""

        ordering = ["sync_order", "content_type__app_label", "content_type__model"]
        verbose_name = "ownership rule"

    def __str__(self):
        """Return the content type label, for example `dcim.device`."""
        return self.content_type_label

    def get_owner_role_class(self) -> str:
        """Return the Bootstrap label class the tables colour the owner with."""
        return RoleChoices.CSS_CLASSES[self.owner_role]

    @property
    def content_type_label(self) -> str:
        """Return the content type as `app_label.model`, the form MongoDB documents and the sync jobs use."""
        return f"{self.content_type.app_label}.{self.content_type.model}"

    @property
    def model_label(self) -> str:
        """Return the model's display name, for example `Device Type`."""
        model_class = self.content_type.model_class()
        return model_display_name(model_class) if model_class else self.content_type_label

    def clean(self):
        """Keep the rules in the hub's hands and keep owner changes on the transfer job.

        A spoke only mirrors the rules the hub published, so editing them there would be undone by the next sync and
        would meanwhile make the spoke publish or pull the wrong types. Changing the owner of a type on the hub moves
        objects between instances; the Transfer Ownership job does that in the right order.
        """
        super().clean()
        if _rules_write_allowed.get():
            return
        if get_role() == ROLE_SPOKE:
            raise ValidationError(
                "Ownership rules are defined on the hub and synced to every spoke by the sync jobs. "
                "Edit this rule on the hub."
            )
        if self.present_in_database:
            saved_role = OwnershipRule.objects.filter(pk=self.pk).values_list("owner_role", flat=True).first()
            if saved_role and saved_role != self.owner_role:
                raise ValidationError(
                    {
                        "owner_role": (
                            "Changing the owner of a type moves its objects between instances. "
                            "Run the Transfer Ownership job instead of editing the rule."
                        )
                    }
                )


@extras_features("graphql")
class FederatedInstance(OrganizationalModel):  # pylint: disable=too-many-ancestors
    """A Nautobot instance taking part in the federation, as last reported by its own sync jobs.

    Rows are not created by hand. Every instance writes a heartbeat document to MongoDB at the end of each successful
    sync job, and every sync job refreshes this registry from the heartbeats it finds, so each instance holds the same
    view of the federation: who is in it, when each member last published or pulled, which versions it runs and how
    many documents it has published per type.
    """

    name = models.CharField(
        max_length=CHARFIELD_MAX_LENGTH,
        unique=True,
        help_text="The instance's `nautobot_instance` setting, which names its partition in MongoDB.",
    )
    role = models.CharField(max_length=5, choices=RoleChoices, help_text="The instance's `nautobot_role` setting.")
    last_publish = models.DateTimeField(
        null=True, blank=True, help_text="When the instance last finished a Nautobot to MongoDB run."
    )
    last_pull = models.DateTimeField(
        null=True, blank=True, help_text="When the instance last finished a MongoDB to Nautobot run."
    )
    last_seen = models.DateTimeField(null=True, blank=True, help_text="When the instance last wrote its heartbeat.")
    nautobot_version = models.CharField(
        max_length=CHARFIELD_MAX_LENGTH, blank=True, help_text="The Nautobot version the instance last reported."
    )
    app_versions = models.JSONField(
        default=dict, blank=True, help_text="Versions of the Nautobot apps installed on the instance, by package."
    )
    document_counts = models.JSONField(
        default=dict, blank=True, help_text="Live documents the instance has published to MongoDB, per content type."
    )

    is_dynamic_group_associable_model = False
    natural_key_field_names = ["name"]

    class Meta:
        """Meta options for FederatedInstance."""

        ordering = ["name"]
        verbose_name = "federated instance"

    def __str__(self):
        """Return the instance name."""
        return self.name

    def get_role_class(self) -> str:
        """Return the Bootstrap label class the tables colour the role with."""
        return RoleChoices.CSS_CLASSES[self.role]

    @property
    def app_version(self) -> str:
        """Return the version of this app the instance last reported, or an empty string."""
        return self.app_versions.get(self._meta.app_label, "")

    @property
    def document_total(self) -> int:
        """Return how many live documents the instance has published in total."""
        return sum(self.document_counts.values())
