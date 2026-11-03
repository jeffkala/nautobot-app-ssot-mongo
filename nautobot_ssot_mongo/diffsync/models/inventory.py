"""DiffSync models for the inventory objects owned by each NAP.

Locations, rack groups, racks, devices, interfaces, VLANs, IP addresses and cables. Devices are identified by
`(name, location__name)`, so interfaces and cables are keyed on the device's location as well.
"""

from typing import ClassVar, Optional

from diffsync import DiffSyncModel
from diffsync.exceptions import ObjectCrudException, ObjectNotCreated
from django.core.exceptions import ValidationError
from nautobot.dcim.models import Cable, Device, Interface, Location, Rack, RackGroup
from nautobot.extras.models import Status
from nautobot.ipam.models import VLAN, IPAddress, Namespace
from nautobot_ssot.contrib import NautobotModel

from nautobot_ssot_mongo.diffsync.models.mixins import LocalOwnerMixin, MongoRemoteMixin

# ---------------------------------------------------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------------------------------------------------


class LocationSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for locations.

    A location name is only unique within its parent, so the parent name is part of the identifiers.
    """

    _model = Location
    _modelname = "location"
    _identifiers = ("name", "parent__name")
    _attributes = ("location_type__name", "status__name", "tenant__name", "description", "facility")

    name: str
    parent__name: Optional[str] = None
    location_type__name: str
    status__name: str = "Active"
    tenant__name: Optional[str] = None
    description: Optional[str] = ""
    facility: Optional[str] = ""


class RemoteLocationSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for locations."""

    _content_type: ClassVar[str] = "dcim.location"
    _modelname = "location"
    _identifiers = ("name", "parent__name")
    _attributes = ("location_type__name", "status__name", "tenant__name", "description", "facility")

    name: str
    parent__name: Optional[str] = None
    location_type__name: str
    status__name: str = "Active"
    tenant__name: Optional[str] = None
    description: Optional[str] = ""
    facility: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Rack Group
# ---------------------------------------------------------------------------------------------------------------------


class RackGroupSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for rack groups, identified by their name within a location."""

    _model = RackGroup
    _modelname = "rackgroup"
    _identifiers = ("location__name", "name")
    _attributes = ("description", "parent__name")

    location__name: str
    name: str
    description: Optional[str] = ""
    parent__name: Optional[str] = None


class RemoteRackGroupSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for rack groups."""

    _content_type: ClassVar[str] = "dcim.rackgroup"
    _modelname = "rackgroup"
    _identifiers = ("location__name", "name")
    _attributes = ("description", "parent__name")

    location__name: str
    name: str
    description: Optional[str] = ""
    parent__name: Optional[str] = None


# ---------------------------------------------------------------------------------------------------------------------
# Rack
# ---------------------------------------------------------------------------------------------------------------------


class RackSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for racks, identified by their name within a location.

    `rack_group` is a foreign key whose names are only unique within a location, so the lookup is scoped by both
    `rack_group__name` and `rack_group__location__name`.
    """

    _model = Rack
    _modelname = "rack"
    _identifiers = ("location__name", "name")
    _attributes = (
        "status__name",
        "rack_group__name",
        "rack_group__location__name",
        "u_height",
        "facility_id",
        "serial",
    )

    location__name: str
    name: str
    status__name: str = "Active"
    rack_group__name: Optional[str] = None
    rack_group__location__name: Optional[str] = None
    u_height: int = 42
    facility_id: Optional[str] = ""
    serial: Optional[str] = ""


class RemoteRackSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for racks."""

    _content_type: ClassVar[str] = "dcim.rack"
    _modelname = "rack"
    _identifiers = ("location__name", "name")
    _attributes = (
        "status__name",
        "rack_group__name",
        "rack_group__location__name",
        "u_height",
        "facility_id",
        "serial",
    )

    location__name: str
    name: str
    status__name: str = "Active"
    rack_group__name: Optional[str] = None
    rack_group__location__name: Optional[str] = None
    u_height: int = 42
    facility_id: Optional[str] = ""
    serial: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------------------------------------------------


class DeviceSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for devices."""

    _model = Device
    _modelname = "device"
    _identifiers = ("name", "location__name")
    _attributes = (
        "device_type__model",
        "device_type__manufacturer__name",
        "role__name",
        "platform__name",
        "status__name",
        "tenant__name",
        "serial",
    )

    name: str
    location__name: str
    device_type__model: str
    device_type__manufacturer__name: str
    role__name: str
    platform__name: Optional[str] = None
    status__name: str = "Active"
    tenant__name: Optional[str] = None
    serial: Optional[str] = ""

    @classmethod
    def get_queryset(cls):
        """Leave out devices without a name: they cannot be matched between instances."""
        return Device.objects.exclude(name__isnull=True)


class RemoteDeviceSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for devices."""

    _content_type: ClassVar[str] = "dcim.device"
    _modelname = "device"
    _identifiers = ("name", "location__name")
    _attributes = (
        "device_type__model",
        "device_type__manufacturer__name",
        "role__name",
        "platform__name",
        "status__name",
        "tenant__name",
        "serial",
    )

    name: str
    location__name: str
    device_type__model: str
    device_type__manufacturer__name: str
    role__name: str
    platform__name: Optional[str] = None
    status__name: str = "Active"
    tenant__name: Optional[str] = None
    serial: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------------------------------------------------


class InterfaceSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for interfaces, identified by their name on a device (itself identified by name and location)."""

    _model = Interface
    _modelname = "interface"
    _identifiers = ("device__name", "device__location__name", "name")
    _attributes = ("status__name", "type", "enabled", "description", "mac_address", "mtu", "mode")

    device__name: str
    device__location__name: str
    name: str
    status__name: str = "Active"
    type: str = "other"
    enabled: bool = True
    description: Optional[str] = ""
    mac_address: Optional[str] = None
    mtu: Optional[int] = None
    mode: Optional[str] = ""


class RemoteInterfaceSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for interfaces."""

    _content_type: ClassVar[str] = "dcim.interface"
    _modelname = "interface"
    _identifiers = ("device__name", "device__location__name", "name")
    _attributes = ("status__name", "type", "enabled", "description", "mac_address", "mtu", "mode")

    device__name: str
    device__location__name: str
    name: str
    status__name: str = "Active"
    type: str = "other"
    enabled: bool = True
    description: Optional[str] = ""
    mac_address: Optional[str] = None
    mtu: Optional[int] = None
    mode: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# VLAN
# ---------------------------------------------------------------------------------------------------------------------


class VLANSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for VLANs, identified by their ID within a location.

    `locations` is an M2M, so `locations__name` is not something the field-traversal loader or contrib's FK writer
    can resolve; the three overrides below own reading, matching and writing it.
    """

    _model = VLAN
    _modelname = "vlan"
    _identifiers = ("locations__name", "vid")
    _attributes = ("name", "status__name", "vlan_group__name", "description")

    locations__name: Optional[str] = ""
    vid: int
    name: str
    status__name: str = "Active"
    vlan_group__name: Optional[str] = None
    description: Optional[str] = ""

    @classmethod
    def from_orm_record(cls, record):
        """Build constructor kwargs from a Nautobot `VLAN` ORM record.

        The sole location's name when there is one, the first by name when there are several, or `""` when there are
        none (a location-less VLAN keys as `("", vid)`).
        """
        location = record.locations.order_by("name").first()
        return {
            "locations__name": location.name if location is not None else "",
            "vid": record.vid,
            "name": record.name,
            "status__name": record.status.name if record.status_id else "Active",
            "vlan_group__name": record.vlan_group.name if record.vlan_group_id else None,
            "description": record.description,
            "pk": record.pk,
        }

    @classmethod
    def _find_existing(cls, ids):
        """Find the VLAN matching this `(location, vid)` identity, querying the M2M explicitly."""
        queryset = cls._model.objects.filter(vid=ids["vid"])
        location = ids.get("locations__name")
        if location:
            queryset = queryset.filter(locations__name=location)
        else:
            queryset = queryset.filter(locations__isnull=True)
        return queryset.first()

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Set VLAN fields via contrib, then attach the M2M-backed location.

        `locations__name` is only present when identifiers are in play (create/adopt); on a plain attribute update it
        is absent and the M2M is left untouched.
        """
        has_location = "locations__name" in parameters
        location_name = parameters.pop("locations__name", None)

        super()._update_obj_with_parameters(obj, parameters, adapter)

        if not has_location:
            return
        location = Location.objects.filter(name=location_name).first() if location_name else None
        if location is not None:
            obj.locations.set([location])
        else:
            obj.locations.clear()


class RemoteVLANSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for VLANs."""

    _content_type: ClassVar[str] = "ipam.vlan"
    _modelname = "vlan"
    _identifiers = ("locations__name", "vid")
    _attributes = ("name", "status__name", "vlan_group__name", "description")

    locations__name: Optional[str] = ""
    vid: int
    name: str
    status__name: str = "Active"
    vlan_group__name: Optional[str] = None
    description: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# IP Address
# ---------------------------------------------------------------------------------------------------------------------


class IPAddressSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for IP addresses.

    An IP address has no namespace field of its own; it lives in the namespace of its parent prefix, so the
    namespace is identified through `parent__namespace__name`. Nautobot picks the parent prefix itself.
    """

    _model = IPAddress
    _modelname = "ipaddress"
    _identifiers = ("host", "parent__namespace__name")
    _attributes = ("mask_length", "type", "status__name", "role__name", "tenant__name", "dns_name", "description")

    host: str
    parent__namespace__name: str
    mask_length: int = 32
    type: str = "host"
    status__name: str = "Active"
    role__name: Optional[str] = None
    tenant__name: Optional[str] = None
    dns_name: Optional[str] = ""
    description: Optional[str] = ""

    @classmethod
    def create(cls, adapter, ids, attrs):
        """Create or adopt the IP address, letting Nautobot resolve the parent prefix from the namespace."""
        existing = cls._find_existing(ids)
        if existing is not None:
            cls._update_obj_with_parameters(existing, attrs, adapter)
            cls._stamp(adapter, adapter._mongo_saved_obj, ids)  # pylint: disable=protected-access
            instance = cls(**ids, **attrs, pk=existing.pk)
            adapter.add(instance)
            return instance

        try:
            namespace = adapter.get_from_orm_cache({"name": ids["parent__namespace__name"]}, Namespace)
        except Namespace.DoesNotExist as error:
            raise ObjectNotCreated(f"Namespace '{ids['parent__namespace__name']}' does not exist.") from error
        obj = IPAddress(address=f"{ids['host']}/{attrs['mask_length']}", namespace=namespace)
        try:
            cls._update_obj_with_parameters(obj, attrs, adapter)
        except ObjectCrudException as error:
            raise ObjectNotCreated(error) from error
        cls._stamp(adapter, adapter._mongo_saved_obj, ids)  # pylint: disable=protected-access
        return super(NautobotModel, cls).create(adapter, ids, attrs)


class RemoteIPAddressSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for IP addresses."""

    _content_type: ClassVar[str] = "ipam.ipaddress"
    _modelname = "ipaddress"
    _identifiers = ("host", "parent__namespace__name")
    _attributes = ("mask_length", "type", "status__name", "role__name", "tenant__name", "dns_name", "description")

    host: str
    parent__namespace__name: str
    mask_length: int = 32
    type: str = "host"
    status__name: str = "Active"
    role__name: Optional[str] = None
    tenant__name: Optional[str] = None
    dns_name: Optional[str] = ""
    description: Optional[str] = ""


# ---------------------------------------------------------------------------------------------------------------------
# Cable
# ---------------------------------------------------------------------------------------------------------------------


class CableSSoTModel(LocalOwnerMixin, NautobotModel):
    """SSoT model for cables between two interfaces.

    Cable primary keys are not preserved across Nautobot instances and the terminations live in
    `CableToCableTermination` join rows that cannot be expressed as `__`-traversal identifiers, so a cable is
    identified by the `(device, location, port)` pair on both sides. Endpoints are ordered canonically so A/B is
    represented the same way on every instance, and create/update/delete resolve the `Interface` rows and assemble
    the cable by hand.
    """

    _model = Cable
    _modelname = "cable"
    _identifiers = ("src_device", "src_location", "src_port", "dst_device", "dst_location", "dst_port")
    _attributes = ("status__name", "type", "label", "color", "length", "length_unit")

    src_device: str
    src_location: str
    src_port: str
    dst_device: str
    dst_location: str
    dst_port: str
    status__name: str = "Connected"
    type: Optional[str] = ""
    label: Optional[str] = ""
    color: Optional[str] = ""
    length: Optional[int] = None
    length_unit: Optional[str] = ""

    @classmethod
    def get_queryset(cls):
        """Load only cables with an interface on both ends, with the endpoints and their devices prefetched.

        `from_orm_record` skips every other termination type anyway. The two chained filters each join the
        `terminations` rows once, so a cable qualifies only when connector 1 of its A end and of its B end is an
        interface, which is what `termination_a` and `termination_b` read back from the prefetched rows.
        """
        return (
            Cable.objects.filter(
                terminations__cable_end="A", terminations__connector=1, terminations__interface__isnull=False
            )
            .filter(terminations__cable_end="B", terminations__connector=1, terminations__interface__isnull=False)
            .distinct()
            .select_related("status")
            .prefetch_related("terminations__interface__device__location")
        )

    @staticmethod
    def _get_interface(device_name: str, location_name: str, port_name: str) -> Optional[Interface]:
        """Resolve an Interface by (device, location, port)."""
        return Interface.objects.filter(
            device__name=device_name, device__location__name=location_name, name=port_name
        ).first()

    @staticmethod
    def _find_existing_cable(iface_a: Interface, iface_b: Interface) -> Optional[Cable]:
        """Look up the Cable that terminates on both interfaces, in either A/B order."""
        return Cable.objects.filter(terminations__interface=iface_a).filter(terminations__interface=iface_b).first()

    @classmethod
    def from_orm_record(cls, cable):
        """Build constructor kwargs from a Nautobot `Cable` ORM record, or None when either end is not an Interface."""
        iface_a = cable.termination_a
        iface_b = cable.termination_b
        if not isinstance(iface_a, Interface) or not isinstance(iface_b, Interface):
            return None

        def _sort_key(iface):
            return (iface.device.location.name, iface.device.name, iface.name)

        iface_src, iface_dst = (iface_a, iface_b) if _sort_key(iface_a) <= _sort_key(iface_b) else (iface_b, iface_a)
        return {
            "src_device": iface_src.device.name,
            "src_location": iface_src.device.location.name,
            "src_port": iface_src.name,
            "dst_device": iface_dst.device.name,
            "dst_location": iface_dst.device.location.name,
            "dst_port": iface_dst.name,
            "status__name": cable.status.name if cable.status_id else "Connected",
            "type": cable.type or "",
            "label": cable.label or "",
            "color": cable.color or "",
            "length": cable.length,
            "length_unit": cable.length_unit or "",
            "pk": cable.pk,
        }

    @staticmethod
    def _apply_writable_attrs(cable: Cable, attrs: dict) -> None:
        """Copy the writable (non-termination) attrs from `attrs` onto `cable`."""
        if "status__name" in attrs:
            cable.status = Status.objects.get(name=attrs["status__name"] or "Connected")
        for field in ("type", "label", "color", "length_unit"):
            if field in attrs:
                setattr(cable, field, attrs[field] or "")
        if "length" in attrs:
            cable.length = attrs["length"]

    def _endpoints(self):
        iface_a = self._get_interface(self.src_device, self.src_location, self.src_port)
        iface_b = self._get_interface(self.dst_device, self.dst_location, self.dst_port)
        return iface_a, iface_b

    def _describe(self):
        return f"{self.src_device}:{self.src_port} <-> {self.dst_device}:{self.dst_port}"

    @classmethod
    def create(cls, adapter, ids, attrs):
        """Create a cable, adopting an already-present one matched by both terminations.

        A cable that cannot be saved (for example a port already cabled to a different peer) or whose endpoints are
        missing is logged and skipped so a single conflict never aborts the whole job.
        """
        label = f"{ids['src_device']}:{ids['src_port']} <-> {ids['dst_device']}:{ids['dst_port']}"
        iface_a = cls._get_interface(ids["src_device"], ids["src_location"], ids["src_port"])
        iface_b = cls._get_interface(ids["dst_device"], ids["dst_location"], ids["dst_port"])
        if iface_a is None or iface_b is None:
            adapter.job.logger.error(
                "Cannot create Cable %s: endpoint missing (a=%s, b=%s)", label, iface_a is not None, iface_b is not None
            )
            return None

        existing = cls._find_existing_cable(iface_a, iface_b)
        if existing is not None:
            cls._apply_writable_attrs(existing, attrs)
            cable = existing
        else:
            cable = Cable(
                termination_a=iface_a,
                termination_b=iface_b,
                status=Status.objects.get(name=attrs.get("status__name") or "Connected"),
                type=attrs.get("type") or "",
                label=attrs.get("label") or "",
                color=attrs.get("color") or "",
                length=attrs.get("length"),
                length_unit=attrs.get("length_unit") or "",
            )

        try:
            cable.validated_save()
        except ValidationError as error:
            adapter.job.logger.error("Skipping Cable %s: cannot save: %s", label, "; ".join(error.messages))
            return None

        cls._stamp(adapter, cable, ids)
        if existing is not None:
            adapter.job.logger.info("Adopted existing Cable %s: updated and stamped owner instead of creating", label)
        instance = cls(**ids, **attrs, pk=cable.pk)
        adapter.add(instance)
        return instance

    def update(self, attrs):
        """Update writable fields on the matching cable; never re-terminate."""
        iface_a, iface_b = self._endpoints()
        cable = self._find_existing_cable(iface_a, iface_b) if iface_a and iface_b else None
        if cable is None:
            self.adapter.job.logger.warning("Cable update skipped: no matching Cable for %s", self._describe())
        else:
            self._apply_writable_attrs(cable, attrs)
            cable.validated_save()
            ids = {key: getattr(self, key) for key in self._identifiers}
            self._stamp(self.adapter, cable, ids)
        for attr, value in attrs.items():
            setattr(self, attr, value)
        return self

    def delete(self):
        """Delete the cable that matches both endpoints (no-op if already gone)."""
        iface_a, iface_b = self._endpoints()
        cable = self._find_existing_cable(iface_a, iface_b) if iface_a and iface_b else None
        if cable is None:
            self.adapter.job.logger.warning("Cable delete skipped: no matching Cable for %s", self._describe())
        else:
            cable.delete()
        # The syncer removes the model from the store after delete() returns, so do not remove it here.
        return self.delete_base()


class RemoteCableSSoTModel(MongoRemoteMixin, DiffSyncModel):
    """MongoDB model for cables."""

    _content_type: ClassVar[str] = "dcim.cable"
    _modelname = "cable"
    _identifiers = ("src_device", "src_location", "src_port", "dst_device", "dst_location", "dst_port")
    _attributes = ("status__name", "type", "label", "color", "length", "length_unit")

    src_device: str
    src_location: str
    src_port: str
    dst_device: str
    dst_location: str
    dst_port: str
    status__name: str = "Connected"
    type: Optional[str] = ""
    label: Optional[str] = ""
    color: Optional[str] = ""
    length: Optional[int] = None
    length_unit: Optional[str] = ""
