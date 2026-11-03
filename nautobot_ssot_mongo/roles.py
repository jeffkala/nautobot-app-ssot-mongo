"""Nautobot instance roles and the objects each role owns.

MongoDB acts as a proxy layer between two kinds of Nautobot instance:

- The **hub** is the single federated instance. It owns the global catalog (manufacturers, platforms, device
  types, location types, tenancy, namespaces, VRFs, roles and prefixes), publishes it to MongoDB and syncs in
  the inventory published by every spoke.
- A **spoke** is a local instance. It owns its inventory (locations, rack groups, racks, devices, interfaces,
  VLANs, IP addresses and cables), publishes it to MongoDB and mirrors the catalog published by the hub.

Every synced object type is owned by exactly one role, and the owning role names the SSoT adapters that move it:
the hub adapters handle hub-owned objects, the spoke adapters handle spoke-owned objects. The `nautobot_role` setting
selects the role of this instance; the "Nautobot to MongoDB" job loads the objects this role owns into MongoDB and
the "MongoDB to Nautobot" job syncs the objects owned by the other role into this instance.
"""

from typing import Any, Dict, Optional

from django.core.exceptions import ImproperlyConfigured

from nautobot_ssot_mongo.utils.mongo import get_app_settings, get_nautobot_instance

ROLE_HUB = "HUB"
ROLE_SPOKE = "SPOKE"
ROLES = (ROLE_HUB, ROLE_SPOKE)
DEFAULT_ROLE = ROLE_HUB
DEFAULT_HUB_INSTANCE = "HUB"


def other_role(role: str) -> str:
    """Return the role that owns the objects `role` syncs in from MongoDB."""
    return ROLE_SPOKE if role == ROLE_HUB else ROLE_HUB


def get_role(app_settings: Optional[Dict[str, Any]] = None) -> str:
    """Return the configured role of this Nautobot instance.

    Raises:
        ImproperlyConfigured: If `nautobot_role` is not one of `ROLES`.
    """
    if app_settings is None:
        app_settings = get_app_settings()
    role = app_settings.get("nautobot_role", DEFAULT_ROLE)
    if role not in ROLES:
        raise ImproperlyConfigured(
            f"nautobot_ssot_mongo: 'nautobot_role' must be one of {', '.join(ROLES)}, not {role!r}."
        )
    return role


def get_hub_instance(app_settings: Optional[Dict[str, Any]] = None) -> str:
    """Return the `nautobot_instance` name of the hub whose objects a spoke mirrors."""
    if app_settings is None:
        app_settings = get_app_settings()
    return app_settings.get("hub_instance", DEFAULT_HUB_INSTANCE)


def validate_role_settings(app_settings: Optional[Dict[str, Any]] = None) -> None:
    """Validate the role-related settings, raising `ImproperlyConfigured` on an unusable combination.

    A spoke publishes the objects it owns under its own `nautobot_instance` and reads hub-owned objects from the
    `hub_instance` partition. If the two names were equal, the hub would never see the spoke's objects (it skips
    its own partition when syncing in spoke-owned data) and the spoke would read its own documents as hub data.
    """
    if app_settings is None:
        app_settings = get_app_settings()
    role = get_role(app_settings)
    if role == ROLE_SPOKE and get_nautobot_instance(app_settings) == get_hub_instance(app_settings):
        raise ImproperlyConfigured(
            "nautobot_ssot_mongo: a spoke instance must use a 'nautobot_instance' different from 'hub_instance'."
        )
