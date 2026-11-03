"""REST API tests for the ownership rules."""

from django.conf import settings
from django.test import override_settings
from nautobot.apps.testing import APIViewTestCases

from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule

HUB_SETTINGS = {
    **settings.PLUGINS_CONFIG,
    "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", "nautobot_role": "HUB", "nautobot_instance": "HUB"},
}


@override_settings(PLUGINS_CONFIG=HUB_SETTINGS)
class OwnershipRuleAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Every standard REST API operation on the ownership rules, on the hub."""

    model = OwnershipRule
    choices_fields = ["content_type", "owner_role"]

    @classmethod
    def setUpTestData(cls):
        # The migration seeds a rule for every supported type, so free three up for the create tests.
        for model in ("cable", "ipaddress", "vlan"):
            OwnershipRule.objects.get(content_type__model=model).delete()
        cls.create_data = [
            {"content_type": "dcim.cable", "owner_role": "SPOKE", "enabled": True, "sync_order": 40},
            {"content_type": "ipam.ipaddress", "owner_role": "SPOKE", "enabled": True, "sync_order": 41},
            {"content_type": "ipam.vlan", "owner_role": "SPOKE", "enabled": False, "sync_order": 42},
        ]
        cls.update_data = {"enabled": False, "sync_order": 50}
        cls.bulk_update_data = {"enabled": False}


class FederatedInstanceAPITestCase(  # pylint: disable=too-many-ancestors
    APIViewTestCases.GetObjectViewTestCase,
    APIViewTestCases.ListObjectsViewTestCase,
    APIViewTestCases.NotesURLViewTestCase,
):
    """The read-only REST API of the federated instance registry."""

    model = FederatedInstance

    @classmethod
    def setUpTestData(cls):
        FederatedInstance(name="HUB", role="HUB", nautobot_version="3.2.8").validated_save()
        FederatedInstance(name="SPOKE-Austin", role="SPOKE", document_counts={"dcim.device": 200}).validated_save()
        FederatedInstance(name="SPOKE-Boston", role="SPOKE").validated_save()

    def test_options_returns_expected_choices(self):
        """The endpoint is read-only, so OPTIONS offers no write actions at all."""
        self.user.is_superuser = True
        self.user.save()

        response = self.client.options(self._get_list_url(), **self.header)

        self.assertHttpStatus(response, 200)
        self.assertNotIn("POST", response.json().get("actions", {}))

    def test_writes_are_not_offered(self):
        self.add_permissions(
            "nautobot_ssot_mongo.add_federatedinstance", "nautobot_ssot_mongo.delete_federatedinstance"
        )

        created = self.client.post(self._get_list_url(), {"name": "SPOKE-Dallas", "role": "SPOKE"}, **self.header)
        deleted = self.client.delete(self._get_detail_url(FederatedInstance.objects.first()), **self.header)

        self.assertHttpStatus(created, 405)
        self.assertHttpStatus(deleted, 405)
