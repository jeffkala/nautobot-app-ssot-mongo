"""FilterSet tests for the ownership rules."""

from nautobot.apps.testing import FilterTestCases

from nautobot_ssot_mongo import filters
from nautobot_ssot_mongo.models import OwnershipRule


class OwnershipRuleFilterTestCase(FilterTestCases.FilterTestCase):  # pylint: disable=too-many-ancestors
    """The ownership rule filterset."""

    filterset = filters.OwnershipRuleFilterSet
    queryset = OwnershipRule.objects.all()
    generic_filter_tests = (["sync_order"],)

    def test_owner_role(self):
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset({"owner_role": ["NAP"]}, self.queryset).qs,
            self.queryset.filter(owner_role="NAP"),
        )

    def test_content_type(self):
        params = {"content_type": ["dcim.device", "ipam.vlan"]}
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset(params, self.queryset).qs,
            self.queryset.filter(content_type__model__in=["device", "vlan"]),
        )

    def test_search_matches_the_model_name(self):
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset({"q": "tenant"}, self.queryset).qs,
            self.queryset.filter(content_type__app_label="tenancy"),
        )
