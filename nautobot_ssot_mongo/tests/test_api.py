"""REST API tests for the ownership rules."""

from django.conf import settings
from django.test import override_settings
from nautobot.apps.testing import APIViewTestCases

from nautobot_ssot_mongo.models import OwnershipRule

NDP_SETTINGS = {
    **settings.PLUGINS_CONFIG,
    "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", "nautobot_role": "NDP", "nautobot_instance": "NDP"},
}


@override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
class OwnershipRuleAPITestCase(APIViewTestCases.APIViewTestCase):  # pylint: disable=too-many-ancestors
    """Every standard REST API operation on the ownership rules, on the NDP."""

    model = OwnershipRule
    choices_fields = ["content_type", "owner_role"]

    @classmethod
    def setUpTestData(cls):
        # The migration seeds a rule for every supported type, so free three up for the create tests.
        for model in ("cable", "ipaddress", "vlan"):
            OwnershipRule.objects.get(content_type__model=model).delete()
        cls.create_data = [
            {"content_type": "dcim.cable", "owner_role": "NAP", "enabled": True, "sync_order": 40},
            {"content_type": "ipam.ipaddress", "owner_role": "NAP", "enabled": True, "sync_order": 41},
            {"content_type": "ipam.vlan", "owner_role": "NAP", "enabled": False, "sync_order": 42},
        ]
        cls.update_data = {"enabled": False, "sync_order": 50}
        cls.bulk_update_data = {"enabled": False}
