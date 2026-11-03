"""Home page panel for nautobot_ssot_mongo."""

from nautobot.apps.ui import HomePageItem, HomePagePanel

from nautobot_ssot_mongo.models import FederatedInstance, OwnershipRule

layout = (
    HomePagePanel(
        name="MongoDB SSoT",
        weight=1000,
        items=(
            HomePageItem(
                name="Federated Instances",
                link="plugins:nautobot_ssot_mongo:federatedinstance_list",
                model=FederatedInstance,
                description="Nautobot instances sharing the MongoDB collection, with their last activity",
                permissions=["nautobot_ssot_mongo.view_federatedinstance"],
                weight=100,
            ),
            HomePageItem(
                name="Ownership Rules",
                link="plugins:nautobot_ssot_mongo:ownershiprule_list",
                model=OwnershipRule,
                description="Which role owns each synced object type",
                permissions=["nautobot_ssot_mongo.view_ownershiprule"],
                weight=200,
            ),
        ),
    ),
)
