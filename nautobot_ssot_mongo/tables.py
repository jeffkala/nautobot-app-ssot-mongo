"""Tables for nautobot_ssot_mongo."""

import django_tables2 as tables
from nautobot.apps.tables import BaseTable, BooleanColumn, ButtonsColumn, ChoiceFieldColumn, ToggleColumn

from nautobot_ssot_mongo.models import OwnershipRule


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
