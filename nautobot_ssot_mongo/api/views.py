"""API views for nautobot_ssot_mongo."""

from nautobot.apps.api import NautobotModelViewSet

from nautobot_ssot_mongo import filters
from nautobot_ssot_mongo.api import serializers
from nautobot_ssot_mongo.models import OwnershipRule


class OwnershipRuleViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API endpoint for ownership rules."""

    queryset = OwnershipRule.objects.select_related("content_type")
    serializer_class = serializers.OwnershipRuleSerializer
    filterset_class = filters.OwnershipRuleFilterSet
