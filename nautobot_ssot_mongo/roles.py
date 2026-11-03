"""Nautobot instance roles and the objects each role owns.

MongoDB acts as a proxy layer between two kinds of Nautobot instance:

- The **NDP** (Nautobot Data Platform) is the single federated instance. It owns the global reference data
  (manufacturers, platforms, device types, location types, tenancy, namespaces, VRFs, roles, prefixes and IP
  addresses), publishes it to MongoDB and syncs in the local data published by every NAP.
- A **NAP** (Nautobot Automation Platform) is a local instance. It owns its locations and devices, publishes them
  to MongoDB and mirrors the reference data published by the NDP.

Every synced object type is owned by exactly one role, and the owning role names the SSoT adapters that move it:
the NDP adapters handle NDP-owned objects, the NAP adapters handle NAP-owned objects. The `nautobot_role` setting
selects the role of this instance; the "Nautobot to MongoDB" job loads the objects this role owns into MongoDB and
the "MongoDB to Nautobot" job syncs the objects owned by the other role into this instance.
"""

from typing import Any, Dict, Optional

from django.core.exceptions import ImproperlyConfigured

from nautobot_ssot_mongo.utils.mongo import get_app_settings, get_nautobot_instance

ROLE_NDP = "NDP"
ROLE_NAP = "NAP"
ROLES = (ROLE_NDP, ROLE_NAP)
DEFAULT_ROLE = ROLE_NDP
DEFAULT_NDP_INSTANCE = "NDP"


def other_role(role: str) -> str:
    """Return the role that owns the objects `role` syncs in from MongoDB."""
    return ROLE_NAP if role == ROLE_NDP else ROLE_NDP


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


def get_ndp_instance(app_settings: Optional[Dict[str, Any]] = None) -> str:
    """Return the `nautobot_instance` name of the NDP whose objects a NAP mirrors."""
    if app_settings is None:
        app_settings = get_app_settings()
    return app_settings.get("ndp_instance", DEFAULT_NDP_INSTANCE)


def validate_role_settings(app_settings: Optional[Dict[str, Any]] = None) -> None:
    """Validate the role-related settings, raising `ImproperlyConfigured` on an unusable combination.

    A NAP publishes the objects it owns under its own `nautobot_instance` and reads NDP-owned objects from the
    `ndp_instance` partition. If the two names were equal, the NDP would never see the NAP's objects (it skips
    its own partition when syncing in NAP-owned data) and the NAP would read its own documents as NDP data.
    """
    if app_settings is None:
        app_settings = get_app_settings()
    role = get_role(app_settings)
    if role == ROLE_NAP and get_nautobot_instance(app_settings) == get_ndp_instance(app_settings):
        raise ImproperlyConfigured(
            "nautobot_ssot_mongo: a NAP instance must use a 'nautobot_instance' different from 'ndp_instance'."
        )
