"""Test role resolution and validation."""

from django.core.exceptions import ImproperlyConfigured
from nautobot.apps.testing import TestCase

from nautobot_ssot_mongo.roles import (
    ROLE_HUB,
    ROLE_SPOKE,
    get_hub_instance,
    get_role,
    other_role,
    validate_role_settings,
)


class RoleSettingsTestCase(TestCase):
    """Test get_role, get_hub_instance, other_role and validate_role_settings."""

    def test_role_defaults_to_hub(self):
        self.assertEqual(get_role({}), ROLE_HUB)
        self.assertEqual(get_hub_instance({}), "HUB")

    def test_role_is_read_from_settings(self):
        self.assertEqual(get_role({"nautobot_role": "SPOKE"}), ROLE_SPOKE)
        self.assertEqual(get_hub_instance({"hub_instance": "Global"}), "Global")

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            get_role({"nautobot_role": "hub"})

    def test_each_role_syncs_in_what_the_other_owns(self):
        self.assertEqual(other_role(ROLE_HUB), ROLE_SPOKE)
        self.assertEqual(other_role(ROLE_SPOKE), ROLE_HUB)

    def test_validate_accepts_spoke_with_distinct_instance_names(self):
        validate_role_settings({"nautobot_role": "SPOKE", "nautobot_instance": "SPOKE-Austin", "hub_instance": "HUB"})

    def test_validate_rejects_spoke_named_like_its_hub(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_role_settings({"nautobot_role": "SPOKE", "nautobot_instance": "HUB", "hub_instance": "HUB"})

    def test_validate_ignores_hub_instance_on_the_hub(self):
        validate_role_settings({"nautobot_role": "HUB", "nautobot_instance": "HUB", "hub_instance": "HUB"})
