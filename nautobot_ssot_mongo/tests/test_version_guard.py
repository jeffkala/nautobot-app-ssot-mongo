"""Tests for the version guard applied to documents loaded from MongoDB."""

from unittest.mock import MagicMock

from nautobot.apps.testing import TestCase

from nautobot_ssot_mongo.utils.nautobot import check_version_compatibility, get_status_metadata

LOCAL = {
    "nautobot_role": "NDP",
    "nautobot_version": "3.2.7",
    "nautobot_apps": {"nautobot_ssot": "4.7.0", "nautobot_ssot_mongo": "0.2.0"},
}


def _doc(nautobot="3.2.7", ssot="4.7.0", app="0.2.0"):
    return {
        "data": {},
        "nautobot_version": nautobot,
        "nautobot_apps": {"nautobot_ssot": ssot, "nautobot_ssot_mongo": app},
    }


class VersionGuardTestCase(TestCase):
    """Majors must match the local instance; anything finer only warns."""

    def test_matching_versions_pass_silently(self):
        logger = MagicMock()
        check_version_compatibility([_doc(), _doc()], LOCAL, "dcim.device", logger_override=logger)
        logger.warning.assert_not_called()

    def test_minor_and_patch_differences_pass(self):
        check_version_compatibility([_doc(nautobot="3.9.1", ssot="4.0.0", app="0.9.9")], LOCAL, "dcim.device")

    def test_versions_with_more_than_three_components_pass(self):
        check_version_compatibility([_doc(nautobot="3.2.7.post1", app="0.2.0.dev3")], LOCAL, "dcim.device")

    def test_legacy_documents_without_versions_are_tolerated(self):
        check_version_compatibility([{"data": {}}, {"data": {}, "nautobot_version": "3.2.7"}], LOCAL, "dcim.device")

    def test_mixed_versions_across_documents_only_warn(self):
        logger = MagicMock()
        check_version_compatibility([_doc(nautobot="3.2.7"), _doc(nautobot="3.3.0")], LOCAL, "dcim.device", logger)
        logger.warning.assert_called_once()
        self.assertIn("dcim.device", logger.warning.call_args.args[1])

    def test_major_mismatch_fails(self):
        for doc in (_doc(nautobot="4.0.0"), _doc(ssot="5.0.0"), _doc(app="1.0.0")):
            with self.subTest(doc=doc), self.assertRaises(ValueError) as ctx:
                check_version_compatibility([doc], LOCAL, "dcim.device")
            self.assertIn("incompatible", str(ctx.exception))

    def test_status_metadata_describes_this_instance(self):
        metadata = get_status_metadata()

        self.assertIn(metadata["nautobot_role"], ("NDP", "NAP"))
        self.assertTrue(metadata["nautobot_version"])
        self.assertIn("nautobot_ssot_mongo", metadata["nautobot_apps"])
        self.assertIn("nautobot_ssot", metadata["nautobot_apps"])
