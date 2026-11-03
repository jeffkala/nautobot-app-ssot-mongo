"""FilterSets for nautobot_ssot_mongo."""

import django_filters
from nautobot.apps.filters import ContentTypeMultipleChoiceFilter, NautobotFilterSet, SearchFilter

from nautobot_ssot_mongo.choices import OwnerRoleChoices
from nautobot_ssot_mongo.constants import DEFAULT_OWNERSHIP_RULES
from nautobot_ssot_mongo.models import OwnershipRule


def supported_content_type_choices():
    """Return the `app_label.model` choices for every content type the app can federate."""
    return [(f"{app_label}.{model}", f"{app_label}.{model}") for app_label, model, _ in DEFAULT_OWNERSHIP_RULES]


class OwnershipRuleFilterSet(NautobotFilterSet):
    """Filter ownership rules by content type, owner and state."""

    q = SearchFilter(
        filter_predicates={
            "content_type__app_label": "icontains",
            "content_type__model": "icontains",
        },
    )
    content_type = ContentTypeMultipleChoiceFilter(choices=supported_content_type_choices, conjoined=False)
    owner_role = django_filters.MultipleChoiceFilter(choices=OwnerRoleChoices)

    class Meta:
        """Meta options for OwnershipRuleFilterSet."""

        model = OwnershipRule
        fields = "__all__"
