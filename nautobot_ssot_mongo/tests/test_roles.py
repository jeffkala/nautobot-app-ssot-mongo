"""Test role resolution and validation."""

from django.core.exceptions import ImproperlyConfigured
from nautobot.apps.testing import TestCase

from nautobot_ssot_mongo.roles import (
    ROLE_NAP,
    ROLE_NDP,
    get_ndp_instance,
    get_role,
    other_role,
    validate_role_settings,
)


class RoleSettingsTestCase(TestCase):
    """Test get_role, get_ndp_instance, other_role and validate_role_settings."""

    def test_role_defaults_to_ndp(self):
        self.assertEqual(get_role({}), ROLE_NDP)
        self.assertEqual(get_ndp_instance({}), "NDP")

    def test_role_is_read_from_settings(self):
        self.assertEqual(get_role({"nautobot_role": "NAP"}), ROLE_NAP)
        self.assertEqual(get_ndp_instance({"ndp_instance": "Global"}), "Global")

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            get_role({"nautobot_role": "ndp"})

    def test_each_role_syncs_in_what_the_other_owns(self):
        self.assertEqual(other_role(ROLE_NDP), ROLE_NAP)
        self.assertEqual(other_role(ROLE_NAP), ROLE_NDP)

    def test_validate_accepts_nap_with_distinct_instance_names(self):
        validate_role_settings({"nautobot_role": "NAP", "nautobot_instance": "NAP-Austin", "ndp_instance": "NDP"})

    def test_validate_rejects_nap_named_like_its_ndp(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_role_settings({"nautobot_role": "NAP", "nautobot_instance": "NDP", "ndp_instance": "NDP"})

    def test_validate_ignores_ndp_instance_on_the_ndp(self):
        validate_role_settings({"nautobot_role": "NDP", "nautobot_instance": "NDP", "ndp_instance": "NDP"})
