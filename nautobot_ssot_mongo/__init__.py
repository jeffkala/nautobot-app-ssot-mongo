"""App declaration for nautobot_ssot_mongo."""

# Metadata is inherited from Nautobot. If not including Nautobot in the environment, this should be added
from importlib import metadata

from nautobot.apps import NautobotAppConfig

from nautobot_ssot_mongo.roles import validate_role_settings

__version__ = metadata.version(__name__)


class NautobotSsotMongoConfig(NautobotAppConfig):
    """App configuration for the nautobot_ssot_mongo app."""

    name = "nautobot_ssot_mongo"
    verbose_name = "Nautobot SSoT MongoDB"
    version = __version__
    author = "Network to Code, LLC"
    description = "Nautobot SSoT integration that syncs objects to and from a MongoDB collection."
    base_url = "ssot-mongo"
    required_settings = []
    default_settings = {
        "mongo_uri": "mongodb://localhost:27017/",
        "mongo_database": "nautobot_sync",
        "mongo_collection": "objects",
        "mongo_username": "",
        "mongo_password": "",
        "mongo_auth_source": "admin",
        "mongo_timeout_ms": 5000,
        "nautobot_role": "NDP",
        "nautobot_instance": "NDP",
        "ndp_instance": "NDP",
        "tombstone_ttl_days": 30,
    }
    docs_view_name = "plugins:nautobot_ssot_mongo:docs"
    searchable_models = []

    def ready(self):
        """Validate the role settings once the app is loaded, so a misconfiguration fails at startup."""
        super().ready()
        validate_role_settings()


config = NautobotSsotMongoConfig  # pylint:disable=invalid-name
