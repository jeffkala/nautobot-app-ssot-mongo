"""UI view tests for the ownership rules."""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from nautobot.apps.testing import ViewTestCases

from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule

HUB_SETTINGS = {
    **settings.PLUGINS_CONFIG,
    "nautobot_ssot_mongo": {"mongo_uri": "mongodb://unused/", "nautobot_role": "HUB", "nautobot_instance": "HUB"},
}


@override_settings(PLUGINS_CONFIG=HUB_SETTINGS)
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
    """Every standard view of the ownership rules, exercised on the hub, where they are editable."""

    model = OwnershipRule

    @classmethod
    def setUpTestData(cls):
        # The migration seeds a rule for every supported type, so free one up for the create and edit forms.
        cable = ContentType.objects.get(app_label="dcim", model="cable")
        OwnershipRule.objects.get(content_type=cable).delete()
        cls.form_data = {"content_type": cable.pk, "owner_role": "HUB", "enabled": True, "sync_order": 30}
        cls.bulk_edit_data = {"enabled": False}


class FederatedInstanceUIViewTestCase(  # pylint: disable=too-many-ancestors
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.GetObjectNotesViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
):
    """The read-only views of the federated instance registry."""

    model = FederatedInstance

    @classmethod
    def setUpTestData(cls):
        FederatedInstance(
            name="HUB",
            role="HUB",
            nautobot_version="3.2.8",
            app_versions={"nautobot_ssot_mongo": "0.1.0a0"},
            document_counts={"dcim.manufacturer": 3, "dcim.devicetype": 12},
        ).validated_save()
        FederatedInstance(name="SPOKE-Austin", role="SPOKE", document_counts={"dcim.device": 200}).validated_save()
        FederatedInstance(name="SPOKE-Boston", role="SPOKE").validated_save()
