"""Forms for nautobot_ssot_mongo."""

from django import forms
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.forms import (
    BulkEditNullBooleanSelect,
    NautobotBulkEditForm,
    NautobotFilterForm,
    NautobotModelForm,
    StaticSelect2,
    StaticSelect2Multiple,
    add_blank_choice,
)

from nautobot_ssot_mongo.choices import RoleChoices
from nautobot_ssot_mongo.filters import supported_content_type_choices
from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule, supported_content_types

BOOLEAN_CHOICES = ((True, "Yes"), (False, "No"))


class OwnershipRuleForm(NautobotModelForm):  # pylint: disable=too-many-ancestors
    """Create or edit one ownership rule."""

    content_type = forms.ModelChoiceField(
        queryset=ContentType.objects.filter(supported_content_types()).order_by("app_label", "model"),
        widget=StaticSelect2(),
    )

    class Meta:
        """Meta options for OwnershipRuleForm."""

        model = OwnershipRule
        fields = "__all__"


class OwnershipRuleBulkEditForm(NautobotBulkEditForm):  # pylint: disable=too-many-ancestors
    """Enable or disable several rules at once; owners change only through the Transfer Ownership job."""

    pk = forms.ModelMultipleChoiceField(queryset=OwnershipRule.objects.all(), widget=forms.MultipleHiddenInput)
    enabled = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())

    class Meta:
        """Meta options for OwnershipRuleBulkEditForm."""

        nullable_fields = []


class OwnershipRuleFilterForm(NautobotFilterForm):  # pylint: disable=too-many-ancestors
    """Filter the ownership rule list."""

    model = OwnershipRule
    q = forms.CharField(required=False, label="Search")
    content_type = forms.MultipleChoiceField(
        choices=supported_content_type_choices, required=False, widget=StaticSelect2Multiple()
    )
    owner_role = forms.MultipleChoiceField(choices=RoleChoices, required=False, widget=StaticSelect2Multiple())
    enabled = forms.NullBooleanField(required=False, widget=StaticSelect2(choices=add_blank_choice(BOOLEAN_CHOICES)))


class FederatedInstanceFilterForm(NautobotFilterForm):  # pylint: disable=too-many-ancestors
    """Filter the federated instance list."""

    model = FederatedInstance
    q = forms.CharField(required=False, label="Search")
    role = forms.MultipleChoiceField(choices=RoleChoices, required=False, widget=StaticSelect2Multiple())
