"""Menu items for nautobot_ssot_mongo."""

from nautobot.apps.ui import NavMenuAddButton, NavMenuGroup, NavMenuItem, NavMenuTab

menu_items = (
    NavMenuTab(
        name="Apps",
        groups=(
            NavMenuGroup(
                name="MongoDB SSoT",
                weight=100,
                items=(
                    NavMenuItem(
                        link="plugins:nautobot_ssot_mongo:federatedinstance_list",
                        name="Federated Instances",
                        permissions=["nautobot_ssot_mongo.view_federatedinstance"],
                    ),
                    NavMenuItem(
                        link="plugins:nautobot_ssot_mongo:ownershiprule_list",
                        name="Ownership Rules",
                        permissions=["nautobot_ssot_mongo.view_ownershiprule"],
                        buttons=(
                            NavMenuAddButton(
                                link="plugins:nautobot_ssot_mongo:ownershiprule_add",
                                permissions=["nautobot_ssot_mongo.add_ownershiprule"],
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
)
