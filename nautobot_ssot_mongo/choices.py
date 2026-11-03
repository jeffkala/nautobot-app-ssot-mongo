"""Choice sets for nautobot_ssot_mongo."""

from nautobot.apps.choices import ChoiceSet

from nautobot_ssot_mongo.roles import ROLE_NAP, ROLE_NDP


class OwnerRoleChoices(ChoiceSet):
    """The Nautobot role whose instances own a federated content type."""

    CHOICES = (
        (ROLE_NDP, "NDP (Nautobot Data Platform)"),
        (ROLE_NAP, "NAP (Nautobot Automation Platform)"),
    )
    CSS_CLASSES = {
        ROLE_NDP: "primary",
        ROLE_NAP: "info",
    }
