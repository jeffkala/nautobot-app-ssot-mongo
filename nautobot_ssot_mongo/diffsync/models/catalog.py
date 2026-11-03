"""DiffSync models for the catalog objects owned by the NDP.

Each synced object type has two DiffSync models that share identifiers and attributes:

- a Nautobot-side model (`*SSoTModel`) built on `LocalOwnerMixin` and `nautobot_ssot.contrib.NautobotModel`, which
  reads and writes the Nautobot ORM;
- a MongoDB-side model (`Remote*SSoTModel`) built on `MongoRemoteMixin`, which reads and writes documents in the
  MongoDB collection.
"""

from typing import ClassVar, List, Optional

from diffsync import DiffSyncModel
from nautobot.dcim.models import DeviceType, LocationType, Manufacturer, Platform
from nautobot.extras.models import Role
from nautobot.ipam.models import VRF, Namespace, Prefix
from nautobot.tenancy.models import Tenant, TenantGroup
from nautobot_ssot.contrib import NautobotModel
from nautobot_ssot.contrib.typeddicts import ContentTypeDict

from nautobot_ssot_mongo.diffsync.models.mixins import LocalOwnerMixin, MongoRemoteMixin

# ---------------------------------------------------------------------------------------------------------------------
# Manufacturer
# ---------------------------------------------------------------------------------------------------------------------


class ManufacturerSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for manufacturers."""

    _model = Manufacturer
    _modelname = "manufacturer"
    _identifiers = ("name",)
    _attributes = ("description",)

    name: str
    description: Optional[str] = ""


class RemoteManufacturerSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for manufacturers."""

    _content_type: ClassVar[str] = "dcim.manufacturer"
    _modelname = "manufacturer"
    _identifiers = ("name",)
    _attributes = ("description",)

    name: str
    description: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------------------------------------------------


class PlatformSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for platforms."""

    _model = Platform
    _modelname = "platform"
    _identifiers = ("name",)
    _attributes = ("manufacturer__name", "description", "network_driver", "napalm_driver", "napalm_args")

    name: str
    manufacturer__name: Optional[str] = None
    description: Optional[str] = ""
    network_driver: Optional[str] = ""
    napalm_driver: Optional[str] = ""
    napalm_args: Optional[dict] = None


class RemotePlatformSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for platforms."""

    _content_type: ClassVar[str] = "dcim.platform"
    _modelname = "platform"
    _identifiers = ("name",)
    _attributes = ("manufacturer__name", "description", "network_driver", "napalm_driver", "napalm_args")

    name: str
    manufacturer__name: Optional[str] = None
    description: Optional[str] = ""
    network_driver: Optional[str] = ""
    napalm_driver: Optional[str] = ""
    napalm_args: Optional[dict] = None


# ---------------------------------------------------------------------------------------------------------------------
# Location Type
# ---------------------------------------------------------------------------------------------------------------------


class LocationTypeSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for location types."""

    _model = LocationType
    _modelname = "locationtype"
    _identifiers = ("name",)
    _attributes = ("parent__name", "description", "nestable", "content_types")

    name: str
    parent__name: Optional[str] = None
    description: Optional[str] = ""
    nestable: bool = False
    content_types: List[ContentTypeDict] = []


class RemoteLocationTypeSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for location types."""

    _content_type: ClassVar[str] = "dcim.locationtype"
    _modelname = "locationtype"
    _identifiers = ("name",)
    _attributes = ("parent__name", "description", "nestable", "content_types")

    name: str
    parent__name: Optional[str] = None
    description: Optional[str] = ""
    nestable: bool = False
    content_types: List[ContentTypeDict] = []


# ---------------------------------------------------------------------------------------------------------------------
# Device Type
# ---------------------------------------------------------------------------------------------------------------------


class DeviceTypeSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for device types."""

    _model = DeviceType
    _modelname = "devicetype"
    _identifiers = ("model", "manufacturer__name")
    _attributes = ("part_number", "u_height", "is_full_depth")

    model: str
    manufacturer__name: str
    part_number: Optional[str] = ""
    u_height: int = 1
    is_full_depth: bool = True


class RemoteDeviceTypeSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for device types."""

    _content_type: ClassVar[str] = "dcim.devicetype"
    _modelname = "devicetype"
    _identifiers = ("model", "manufacturer__name")
    _attributes = ("part_number", "u_height", "is_full_depth")

    model: str
    manufacturer__name: str
    part_number: Optional[str] = ""
    u_height: int = 1
    is_full_depth: bool = True


# ---------------------------------------------------------------------------------------------------------------------
# Tenant Group
# ---------------------------------------------------------------------------------------------------------------------


class TenantGroupSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for tenant groups."""

    _model = TenantGroup
    _modelname = "tenantgroup"
    _identifiers = ("name",)
    _attributes = ("parent__name", "description")

    name: str
    parent__name: Optional[str] = None
    description: Optional[str] = ""


class RemoteTenantGroupSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for tenant groups."""

    _content_type: ClassVar[str] = "tenancy.tenantgroup"
    _modelname = "tenantgroup"
    _identifiers = ("name",)
    _attributes = ("parent__name", "description")

    name: str
    parent__name: Optional[str] = None
    description: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------------------------------------------------


class TenantSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for tenants."""

    _model = Tenant
    _modelname = "tenant"
    _identifiers = ("name",)
    _attributes = ("tenant_group__name", "description")

    name: str
    tenant_group__name: Optional[str] = None
    description: Optional[str] = ""


class RemoteTenantSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for tenants."""

    _content_type: ClassVar[str] = "tenancy.tenant"
    _modelname = "tenant"
    _identifiers = ("name",)
    _attributes = ("tenant_group__name", "description")

    name: str
    tenant_group__name: Optional[str] = None
    description: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------------------------------------------------


class NamespaceSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for IPAM namespaces."""

    _model = Namespace
    _modelname = "namespace"
    _identifiers = ("name",)
    _attributes = ("description",)

    name: str
    description: Optional[str] = ""


class RemoteNamespaceSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for IPAM namespaces."""

    _content_type: ClassVar[str] = "ipam.namespace"
    _modelname = "namespace"
    _identifiers = ("name",)
    _attributes = ("description",)

    name: str
    description: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# VRF
# ---------------------------------------------------------------------------------------------------------------------


class VRFSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for VRFs."""

    _model = VRF
    _modelname = "vrf"
    _identifiers = ("name", "namespace__name")
    _attributes = ("rd", "description", "tenant__name")

    name: str
    namespace__name: str
    rd: Optional[str] = None
    description: Optional[str] = ""
    tenant__name: Optional[str] = None


class RemoteVRFSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for VRFs."""

    _content_type: ClassVar[str] = "ipam.vrf"
    _modelname = "vrf"
    _identifiers = ("name", "namespace__name")
    _attributes = ("rd", "description", "tenant__name")

    name: str
    namespace__name: str
    rd: Optional[str] = None
    description: Optional[str] = ""
    tenant__name: Optional[str] = None


# ---------------------------------------------------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------------------------------------------------


class RoleSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for roles."""

    _model = Role
    _modelname = "role"
    _identifiers = ("name",)
    _attributes = ("description", "color", "weight", "content_types")

    name: str
    description: Optional[str] = ""
    color: Optional[str] = "9e9e9e"
    weight: Optional[int] = None
    content_types: List[ContentTypeDict] = []


class RemoteRoleSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for roles."""

    _content_type: ClassVar[str] = "extras.role"
    _modelname = "role"
    _identifiers = ("name",)
    _attributes = ("description", "color", "weight", "content_types")

    name: str
    description: Optional[str] = ""
    color: Optional[str] = "9e9e9e"
    weight: Optional[int] = None
    content_types: List[ContentTypeDict] = []


# ---------------------------------------------------------------------------------------------------------------------
# Prefix
# ---------------------------------------------------------------------------------------------------------------------


class PrefixSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for prefixes."""

    _model = Prefix
    _modelname = "prefix"
    _identifiers = ("network", "prefix_length", "namespace__name")
    _attributes = ("type", "status__name", "role__name", "tenant__name", "description")

    network: str
    prefix_length: int
    namespace__name: str
    type: str = "network"
    status__name: str = "Active"
    role__name: Optional[str] = None
    tenant__name: Optional[str] = None
    description: Optional[str] = ""


class RemotePrefixSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for prefixes."""

    _content_type: ClassVar[str] = "ipam.prefix"
    _modelname = "prefix"
    _identifiers = ("network", "prefix_length", "namespace__name")
    _attributes = ("type", "status__name", "role__name", "tenant__name", "description")

    network: str
    prefix_length: int
    namespace__name: str
    type: str = "network"
    status__name: str = "Active"
    role__name: Optional[str] = None
    tenant__name: Optional[str] = None
    description: Optional[str] = ""
