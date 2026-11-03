"""FilterSet tests for the ownership rules."""

from nautobot.apps.testing import FilterTestCases

from nautobot_ssot_mongo import filters
from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule


class OwnershipRuleFilterTestCase(FilterTestCases.FilterTestCase):  # pylint: disable=too-many-ancestors
    """The ownership rule filterset."""

    filterset = filters.OwnershipRuleFilterSet
    queryset = OwnershipRule.objects.all()
    generic_filter_tests = (["sync_order"],)

    def test_owner_role(self):
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset({"owner_role": ["SPOKE"]}, self.queryset).qs,
            self.queryset.filter(owner_role="SPOKE"),
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


class FederatedInstanceFilterTestCase(FilterTestCases.FilterTestCase):  # pylint: disable=too-many-ancestors
    """The federated instance filterset."""

    filterset = filters.FederatedInstanceFilterSet
    queryset = FederatedInstance.objects.all()
    generic_filter_tests = (["name"], ["nautobot_version"])

    @classmethod
    def setUpTestData(cls):
        FederatedInstance(name="HUB", role="HUB", nautobot_version="3.2.8").validated_save()
        FederatedInstance(name="SPOKE-Austin", role="SPOKE", nautobot_version="3.2.7").validated_save()
        FederatedInstance(name="SPOKE-Boston", role="SPOKE", nautobot_version="3.2.6").validated_save()

    def test_role(self):
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset({"role": ["SPOKE"]}, self.queryset).qs, self.queryset.filter(role="SPOKE")
        )

    def test_search_matches_name_and_version(self):
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset({"q": "austin"}, self.queryset).qs, self.queryset.filter(name="SPOKE-Austin")
        )
        self.assertQuerysetEqualAndNotEmpty(
            self.filterset({"q": "3.2.8"}, self.queryset).qs, self.queryset.filter(name="HUB")
        )
