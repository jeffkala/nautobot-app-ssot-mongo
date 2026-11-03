"""Utility functions for working with Nautobot: status metadata and the version guard.

Both live here rather than in `utils.mongo` because they need `roles.get_role`, and `roles` imports `utils.mongo`.
"""

import importlib.metadata
import logging
from typing import Any, Dict, Iterable, Optional

from django.conf import settings

from nautobot_ssot_mongo.roles import get_role

logger = logging.getLogger(__name__)

APP_NAME = "nautobot_ssot_mongo"


def _major(version: str) -> str:
    """Return the major component of a version string such as `3.2.7` or `3.2.7.post1`."""
    return str(version).split(".", maxsplit=1)[0]


def get_status_metadata() -> Dict[str, Any]:
    """Build the role and version fields stamped on every document this instance writes.

    Mirrors the `nautobot-version` and `nautobot-apps` fields of Nautobot's `/api/status` endpoint, computed locally
    so writes do not pay an HTTP round-trip. Consumers use them to refuse documents from an incompatible major
    version (see `check_version_compatibility`) and to tell which role published a partition.
    """
    nautobot_apps = {}
    for plugin in getattr(settings, "PLUGINS", []) or []:
        try:
            nautobot_apps[plugin] = importlib.metadata.version(plugin)
        except importlib.metadata.PackageNotFoundError:
            continue
    return {
        "nautobot_role": get_role(),
        "nautobot_version": importlib.metadata.version("nautobot"),
        "nautobot_apps": nautobot_apps,
    }


def _document_versions(document: Dict[str, Any]) -> Optional[tuple]:
    """Return the `(nautobot, nautobot_ssot, nautobot_ssot_mongo)` versions a document carries, or None if absent."""
    version = document.get("nautobot_version")
    apps = document.get("nautobot_apps") or {}
    if not version or "nautobot_ssot" not in apps or APP_NAME not in apps:
        return None
    return (version, apps["nautobot_ssot"], apps[APP_NAME])


def check_version_compatibility(
    documents: Iterable[Dict[str, Any]],
    local_metadata: Dict[str, Any],
    content_type: str,
    logger_override: Optional[logging.Logger] = None,
) -> None:
    """Fail the load when documents come from an incompatible major version; warn on mixed versions.

    Args:
        documents: The MongoDB documents about to be loaded.
        local_metadata: This instance's `get_status_metadata()`.
        content_type: The content type being loaded, for messages.
        logger_override: Logger for the mixed-version warning (defaults to the module logger).

    Raises:
        ValueError: When any document's Nautobot, `nautobot_ssot` or `nautobot_ssot_mongo` major version differs
            from the local one. Documents written before version stamping existed carry no versions and are skipped.
    """
    log = logger_override or logger
    local_apps = local_metadata.get("nautobot_apps") or {}
    local = (
        local_metadata.get("nautobot_version", ""),
        local_apps.get("nautobot_ssot", ""),
        local_apps.get(APP_NAME, ""),
    )
    local_majors = tuple(_major(version) for version in local)

    remote_versions = {versions for versions in (_document_versions(doc) for doc in documents) if versions}
    if not remote_versions:
        return
    if len(remote_versions) > 1:
        log.warning(
            "Documents of %s were published by Nautobot instances running different versions: %s",
            content_type,
            ", ".join("/".join(versions) for versions in sorted(remote_versions)),
        )
    for versions in remote_versions:
        if tuple(_major(version) for version in versions) != local_majors:
            raise ValueError(
                f"Documents of {content_type} were published by an incompatible Nautobot "
                f"(nautobot/nautobot_ssot/{APP_NAME} {'/'.join(versions)}); this instance runs {'/'.join(local)}. "
                "Upgrade both instances to the same major versions before syncing."
            )
