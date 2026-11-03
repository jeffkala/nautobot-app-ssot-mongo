"""API serializers for nautobot_ssot_mongo."""

from django.contrib.contenttypes.models import ContentType
from nautobot.apps.api import ContentTypeField, NautobotModelSerializer

from nautobot_ssot_mongo.models import OwnershipRule, supported_content_types


class OwnershipRuleSerializer(NautobotModelSerializer):  # pylint: disable=too-many-ancestors
    """Serialize ownership rules, with the content type as `app_label.model`."""

    content_type = ContentTypeField(queryset=ContentType.objects.filter(supported_content_types()))

    class Meta:
        """Meta options for OwnershipRuleSerializer."""

        model = OwnershipRule
        fields = "__all__"
