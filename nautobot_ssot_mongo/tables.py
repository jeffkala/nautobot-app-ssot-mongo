"""Tables for nautobot_ssot_mongo."""

import django_tables2 as tables
from nautobot.apps.tables import BaseTable, BooleanColumn, ButtonsColumn, ChoiceFieldColumn, ToggleColumn

from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule


class OwnershipRuleTable(BaseTable):
    """List the ownership rules in sync order."""

    pk = ToggleColumn()
    content_type = tables.Column(linkify=lambda record: record.get_absolute_url(), verbose_name="Content Type")
    model_label = tables.Column(verbose_name="Model", orderable=False)
    owner_role = ChoiceFieldColumn(verbose_name="Owner")
    enabled = BooleanColumn()
    sync_order = tables.Column(verbose_name="Sync Order")
    actions = ButtonsColumn(OwnershipRule)

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta options for OwnershipRuleTable."""

        model = OwnershipRule
        fields = ("pk", "content_type", "model_label", "owner_role", "enabled", "sync_order", "actions")
        default_columns = fields


class FederatedInstanceTable(BaseTable):
    """List the members of the federation with their last activity and versions."""

    name = tables.Column(linkify=True)
    role = ChoiceFieldColumn()
    last_publish = tables.DateTimeColumn(verbose_name="Last Publish")
    last_pull = tables.DateTimeColumn(verbose_name="Last Pull")
    last_seen = tables.DateTimeColumn(verbose_name="Last Seen")
    nautobot_version = tables.Column(verbose_name="Nautobot")
    app_version = tables.Column(verbose_name="App Version", orderable=False)
    document_total = tables.Column(verbose_name="Documents", orderable=False)

    class Meta(BaseTable.Meta):  # pylint: disable=too-few-public-methods
        """Meta options for FederatedInstanceTable."""

        model = FederatedInstance
        fields = (
            "name",
            "role",
            "last_publish",
            "last_pull",
            "last_seen",
            "nautobot_version",
            "app_version",
            "document_total",
        )
        default_columns = fields
