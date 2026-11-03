"""Choice sets for nautobot_ssot_mongo."""

from nautobot.apps.choices import ChoiceSet

from nautobot_ssot_mongo.roles import ROLE_HUB, ROLE_SPOKE


class OwnerRoleChoices(ChoiceSet):
    """The Nautobot role whose instances own a federated content type."""

    CHOICES = (
        (ROLE_HUB, "Hub"),
        (ROLE_SPOKE, "Spoke"),
    )
    CSS_CLASSES = {
        ROLE_HUB: "primary",
        ROLE_SPOKE: "info",
    }
