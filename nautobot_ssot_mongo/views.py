"""Views for nautobot_ssot_mongo."""

from django.contrib.contenttypes.models import ContentType
from nautobot.apps.ui import KeyValueTablePanel, ObjectDetailContent, ObjectFieldsPanel, SectionChoices, TextPanel
from nautobot.apps.views import (
    NautobotUIViewSet,
    ObjectChangeLogViewMixin,
    ObjectDataComplianceViewMixin,
    ObjectDetailViewMixin,
    ObjectListViewMixin,
    ObjectNotesViewMixin,
)

from nautobot_ssot_mongo import filters, forms, tables
from nautobot_ssot_mongo.api import serializers
from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule, model_display_name
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


def content_type_display(label: str) -> str:
    """Return the display name of the `app_label.model` content type, or the label when the model is not installed."""
    app_label, model = label.split(".", 1)
    try:
        model_class = ContentType.objects.get_by_natural_key(app_label, model).model_class()
    except ContentType.DoesNotExist:
        model_class = None
    return model_display_name(model_class) if model_class else label


class FederatedInstanceUIViewSet(  # pylint: disable=too-many-ancestors,abstract-method
    ObjectListViewMixin,
    ObjectDetailViewMixin,
    ObjectChangeLogViewMixin,
    ObjectNotesViewMixin,
    ObjectDataComplianceViewMixin,
):
    """List and view the members of the federation; the sync jobs maintain the rows."""

    queryset = FederatedInstance.objects.all()
    filterset_class = filters.FederatedInstanceFilterSet
    filterset_form_class = forms.FederatedInstanceFilterForm
    table_class = tables.FederatedInstanceTable
    serializer_class = serializers.FederatedInstanceSerializer
    lookup_field = "pk"
    action_buttons = ("export",)

    object_detail_content = ObjectDetailContent(
        panels=(
            ObjectFieldsPanel(
                section=SectionChoices.LEFT_HALF,
                weight=100,
                fields=("name", "role", "last_publish", "last_pull", "last_seen", "nautobot_version", "app_version"),
            ),
            KeyValueTablePanel(
                section=SectionChoices.RIGHT_HALF,
                weight=100,
                label="Published Documents",
                context_data_key="published_documents",
            ),
            KeyValueTablePanel(
                section=SectionChoices.RIGHT_HALF,
                weight=200,
                label="Installed Apps",
                context_data_key="installed_apps",
            ),
        ),
    )

    def get_extra_context(self, request, instance=None):
        """Label the published document counts by model and list the instance's apps."""
        context = super().get_extra_context(request, instance=instance)
        if instance is not None:
            counts = {content_type_display(label): count for label, count in instance.document_counts.items()}
            context["published_documents"] = dict(sorted(counts.items()))
            context["installed_apps"] = dict(sorted(instance.app_versions.items()))
        return context
