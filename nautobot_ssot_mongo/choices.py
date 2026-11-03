"""Choice sets for nautobot_ssot_mongo."""

from nautobot.apps.choices import ChoiceSet

from nautobot_ssot_mongo.roles import ROLE_HUB, ROLE_SPOKE


class RoleChoices(ChoiceSet):
    """A Nautobot instance's role in the federation: the hub, or one of the spokes."""

    CHOICES = (
        (ROLE_HUB, "Hub"),
        (ROLE_SPOKE, "Spoke"),
    )
    CSS_CLASSES = {
        ROLE_HUB: "primary",
        ROLE_SPOKE: "info",
    }
