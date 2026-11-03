"""UI view tests for the ownership rules."""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from nautobot.apps.testing import ViewTestCases

from nautobot_ssot_mongo.models import OwnershipRule

NDP_SETTINGS = {
    **settings.PLUGINS_CONFIG,
    "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", "nautobot_role": "NDP", "nautobot_instance": "NDP"},
}


@override_settings(PLUGINS_CONFIG=NDP_SETTINGS)
class OwnershipRuleUIViewTestCase(  # pylint: disable=too-many-ancestors
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.GetObjectNotesViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkEditObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    """Every standard view of the ownership rules, exercised on the NDP, where they are editable."""

    model = OwnershipRule

    @classmethod
    def setUpTestData(cls):
        # The migration seeds a rule for every supported type, so free one up for the create and edit forms.
        cable = ContentType.objects.get(app_label="dcim", model="cable")
        OwnershipRule.objects.get(content_type=cable).delete()
        cls.form_data = {"content_type": cable.pk, "owner_role": "NDP", "enabled": True, "sync_order": 30}
        cls.bulk_edit_data = {"enabled": False}
