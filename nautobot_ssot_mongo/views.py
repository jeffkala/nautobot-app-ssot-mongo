"""Views for nautobot_ssot_mongo."""

from nautobot.apps.ui import ObjectDetailContent, ObjectFieldsPanel, SectionChoices, TextPanel
from nautobot.apps.views import NautobotUIViewSet

from nautobot_ssot_mongo import filters, forms, tables
from nautobot_ssot_mongo.api import serializers
from nautobot_ssot_mongo.models import OwnershipRule
from nautobot_ssot_mongo.roles import ROLE_SPOKE, get_role

SPOKE_NOTICE = (
    "This instance is a spoke. Ownership rules are defined on the hub and refreshed here by the sync jobs; "
    "changes made on this instance are rejected."
)
HUB_NOTICE = (
    "This instance is the hub. The next **Nautobot to MongoDB** run publishes these rules to every spoke. "
    "To hand a type to the other role, run the **Transfer Ownership** job rather than editing its owner."
)


class OwnershipRuleUIViewSet(NautobotUIViewSet):
    """List, view, add, edit and delete ownership rules."""

    queryset = OwnershipRule.objects.select_related("content_type")
    filterset_class = filters.OwnershipRuleFilterSet
    filterset_form_class = forms.OwnershipRuleFilterForm
    form_class = forms.OwnershipRuleForm
    bulk_update_form_class = forms.OwnershipRuleBulkEditForm
    serializer_class = serializers.OwnershipRuleSerializer
    table_class = tables.OwnershipRuleTable
    lookup_field = "pk"

    object_detail_content = ObjectDetailContent(
        panels=(
            ObjectFieldsPanel(
                section=SectionChoices.LEFT_HALF,
                weight=100,
                fields=("content_type", "model_label", "owner_role", "enabled", "sync_order"),
            ),
            TextPanel(
                section=SectionChoices.RIGHT_HALF,
                weight=100,
                label="Where this rule is maintained",
                context_field="ownership_notice",
                render_as=TextPanel.RenderOptions.MARKDOWN,
            ),
        ),
    )

    def get_extra_context(self, request, instance=None):
        """Tell the reader whether this instance defines the rules or mirrors them."""
        context = super().get_extra_context(request, instance=instance)
        context["ownership_notice"] = SPOKE_NOTICE if get_role() == ROLE_SPOKE else HUB_NOTICE
        return context
