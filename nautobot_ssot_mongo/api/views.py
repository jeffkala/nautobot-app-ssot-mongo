"""API views for nautobot_ssot_mongo."""

from nautobot.apps.api import NautobotModelViewSet, NotesViewSetMixin, ReadOnlyModelViewSet

from nautobot_ssot_mongo import filters
from nautobot_ssot_mongo.api import serializers
from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule


class OwnershipRuleViewSet(NautobotModelViewSet):  # pylint: disable=too-many-ancestors
    """REST API endpoint for ownership rules."""

    queryset = OwnershipRule.objects.select_related("content_type")
    serializer_class = serializers.OwnershipRuleSerializer
    filterset_class = filters.OwnershipRuleFilterSet


class FederatedInstanceViewSet(NotesViewSetMixin, ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """Read-only REST API endpoint for the members of the federation, which the sync jobs maintain."""

    queryset = FederatedInstance.objects.all()
    serializer_class = serializers.FederatedInstanceSerializer
    filterset_class = filters.FederatedInstanceFilterSet
