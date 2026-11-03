"""Create a large, deterministic data set on one Nautobot instance to stress test the MongoDB sync jobs.

Run it through `invoke stress-data` on each stack; it reads the sizes from `SSOT_MONGO_STRESS_*` environment variables
and the role from `PLUGINS_CONFIG`:

- On the hub it creates the catalog: 2 location types (Stress Region > Stress Site), 10 manufacturers,
  10 platforms, `SSOT_MONGO_STRESS_DEVICE_TYPES` device types, tenants, tenant groups, VRFs, roles and prefixes.
- On a spoke it creates the inventory on top of the catalog pulled from the hub: `SSOT_MONGO_STRESS_LOCATIONS` locations
  in two levels, rack groups and racks, `SSOT_MONGO_STRESS_DEVICES` devices with `SSOT_MONGO_STRESS_INTERFACES` interfaces
  each, VLANs, one IP address per device and one cable per pair of devices.

Every object is named `Stress ...` or `stress-...`, and `SSOT_MONGO_STRESS_RESET=1` removes them before creating
anything: the inventory on both roles (the hub pulled it), the catalog only on the hub. The task executes the file inside `nautobot-server shell`, so Django is set up
and the script body runs at module level.
"""

import os
import time

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from nautobot.dcim.models import (
    Cable,
    Device,
    DeviceType,
    Interface,
    Location,
    LocationType,
    Manufacturer,
    Platform,
    Rack,
    RackGroup,
)
from nautobot.extras.models import Role, Status
from nautobot.ipam.models import VLAN, VRF, IPAddress, Namespace, Prefix
from nautobot.tenancy.models import Tenant, TenantGroup

ROLE = settings.PLUGINS_CONFIG["nautobot_ssot_mongo"]["nautobot_role"]
DEVICES = int(os.getenv("SSOT_MONGO_STRESS_DEVICES", "3000"))
LOCATIONS = int(os.getenv("SSOT_MONGO_STRESS_LOCATIONS", "100"))
INTERFACES = int(os.getenv("SSOT_MONGO_STRESS_INTERFACES", "8"))
DEVICE_TYPES = int(os.getenv("SSOT_MONGO_STRESS_DEVICE_TYPES", "400"))
RESET = os.getenv("SSOT_MONGO_STRESS_RESET", "0") == "1"

MANUFACTURERS = 10
REGIONS = 10
TENANTS = 5
VRFS = 20
PREFIXES = 200
VLANS_PER_SITE = 5
RACKS_PER_SITE = 2

REGION_TYPE = "Stress Region"
SITE_TYPE = "Stress Site"
DEVICE_ROLE = "Stress Network Device"


def log(message):
    """Print a timestamped progress line."""
    print(f"[stress-data] {time.strftime('%H:%M:%S')} {message}", flush=True)


def content_types(*labels):
    """Return the ContentType rows for `app.model` labels."""
    return [ContentType.objects.get_by_natural_key(*label.split(".")) for label in labels]


def reset():
    """Delete the stress objects this role owns or pulled, children before parents.

    Both roles remove the inventory: a spoke created it and the hub pulled it. Only the hub removes the
    catalog, because on a spoke it is the pulled copy the next inventory run needs.
    """
    log("removing existing stress objects")
    stress_devices = Device.objects.filter(name__startswith="stress-")
    Cable.objects.filter(interfaces__device__in=stress_devices).delete()
    stress_devices.delete()
    IPAddress.objects.filter(dns_name__startswith="stress-").delete()
    VLAN.objects.filter(name__startswith="Stress").delete()
    Rack.objects.filter(name__startswith="Stress").delete()
    RackGroup.objects.filter(name__startswith="Stress").delete()
    for location in Location.objects.filter(name__startswith="Stress Site"):
        location.delete()
    for location in Location.objects.filter(name__startswith="Stress Region"):
        location.delete()
    if ROLE != "HUB":
        return
    Prefix.objects.filter(description__startswith="Stress").delete()
    VRF.objects.filter(name__startswith="Stress").delete()
    Namespace.objects.filter(name__startswith="Stress").delete()
    Role.objects.filter(name__startswith="Stress").delete()
    Tenant.objects.filter(name__startswith="Stress").delete()
    TenantGroup.objects.filter(name__startswith="Stress").delete()
    DeviceType.objects.filter(model__startswith="Stress").delete()
    Platform.objects.filter(name__startswith="Stress").delete()
    Manufacturer.objects.filter(name__startswith="Stress").delete()
    LocationType.objects.filter(name=SITE_TYPE).delete()
    LocationType.objects.filter(name=REGION_TYPE).delete()


def create_catalog():
    """Create the hub-owned catalog."""
    active = Status.objects.get(name="Active")
    region_type, _ = LocationType.objects.get_or_create(name=REGION_TYPE, defaults={"nestable": False})
    site_type, _ = LocationType.objects.get_or_create(name=SITE_TYPE, defaults={"parent": region_type})
    site_type.content_types.set(content_types("dcim.device", "dcim.rack", "dcim.rackgroup", "ipam.vlan", "ipam.prefix"))
    log(f"location types: {REGION_TYPE} > {SITE_TYPE}")

    manufacturers = [
        Manufacturer.objects.get_or_create(name=f"Stress Vendor {index:02d}")[0]
        for index in range(1, MANUFACTURERS + 1)
    ]
    for index, manufacturer in enumerate(manufacturers, start=1):
        Platform.objects.get_or_create(
            name=f"Stress Platform {index:02d}",
            defaults={"manufacturer": manufacturer, "network_driver": "cisco_ios", "napalm_driver": "ios"},
        )
    log(f"{len(manufacturers)} manufacturers and platforms")

    existing = set(DeviceType.objects.filter(model__startswith="Stress Model").values_list("model", flat=True))
    DeviceType.objects.bulk_create(
        [
            DeviceType(
                manufacturer=manufacturers[index % MANUFACTURERS],
                model=f"Stress Model {index:04d}",
                part_number=f"SM-{index:04d}",
                u_height=1 + index % 4,
            )
            for index in range(DEVICE_TYPES)
            if f"Stress Model {index:04d}" not in existing
        ],
        batch_size=500,
    )
    log(f"{DEVICE_TYPES} device types")

    groups = [TenantGroup.objects.get_or_create(name=f"Stress Group {index}")[0] for index in (1, 2)]
    for index in range(1, TENANTS + 1):
        Tenant.objects.get_or_create(name=f"Stress Tenant {index}", defaults={"tenant_group": groups[index % 2]})
    role, _ = Role.objects.get_or_create(name=DEVICE_ROLE, defaults={"color": "ff9800"})
    role.content_types.add(*content_types("dcim.device"))
    namespace = Namespace.objects.get(name="Global")
    Namespace.objects.get_or_create(name="Stress Namespace")
    for index in range(1, VRFS + 1):
        VRF.objects.get_or_create(
            name=f"Stress VRF {index:02d}", namespace=namespace, defaults={"rd": f"65000:{index}"}
        )
    Prefix.objects.get_or_create(
        prefix="10.0.0.0/8",
        namespace=namespace,
        defaults={"status": active, "type": "container", "description": "Stress summary"},
    )
    for index in range(PREFIXES):
        Prefix.objects.get_or_create(
            prefix=f"10.{index // 256}.{index % 256}.0/24",
            namespace=namespace,
            defaults={"status": active, "description": f"Stress prefix {index:03d}"},
        )
    log(f"tenancy, {VRFS} VRFs, {PREFIXES + 1} prefixes, role {DEVICE_ROLE}")


def create_locations(active):
    """Create the two-level location tree with a rack group, racks and VLANs per site; return the sites."""
    region_type = LocationType.objects.get(name=REGION_TYPE)
    site_type = LocationType.objects.get(name=SITE_TYPE)
    regions = [
        Location.objects.get_or_create(
            name=f"Stress Region {index:02d}", defaults={"location_type": region_type, "status": active}
        )[0]
        for index in range(1, REGIONS + 1)
    ]
    sites = [
        Location.objects.get_or_create(
            name=f"Stress Site {index:03d}",
            defaults={"location_type": site_type, "status": active, "parent": regions[index % REGIONS]},
        )[0]
        for index in range(1, LOCATIONS - REGIONS + 1)
    ]
    log(f"{len(regions)} regions and {len(sites)} sites")

    for site in sites:
        group, _ = RackGroup.objects.get_or_create(name=f"Stress RG {site.name[-3:]}", location=site)
        for index in range(1, RACKS_PER_SITE + 1):
            Rack.objects.get_or_create(
                name=f"Stress Rack {site.name[-3:]}-{index}",
                location=site,
                defaults={"status": active, "rack_group": group},
            )
        for offset in range(VLANS_PER_SITE):
            vlan, created = VLAN.objects.get_or_create(
                name=f"Stress VLAN {site.name[-3:]}-{100 + offset}", vid=100 + offset, defaults={"status": active}
            )
            if created:
                vlan.locations.add(site)
    log(f"rack groups, {RACKS_PER_SITE} racks and {VLANS_PER_SITE} VLANs per site")
    return sites


def create_devices(active, sites):
    """Bulk-create the devices across the sites, device types and tenants; return them ordered by name."""
    device_types = list(DeviceType.objects.filter(model__startswith="Stress Model").order_by("model"))
    tenants = list(Tenant.objects.filter(name__startswith="Stress Tenant").order_by("name"))
    role = Role.objects.get(name=DEVICE_ROLE)
    existing = set(Device.objects.filter(name__startswith="stress-dev-").values_list("name", flat=True))
    Device.objects.bulk_create(
        [
            Device(
                name=f"stress-dev-{index:05d}",
                device_type=device_types[index % len(device_types)],
                role=role,
                status=active,
                location=sites[index % len(sites)],
                tenant=tenants[index % len(tenants)],
                serial=f"STRESS{index:08d}",
            )
            for index in range(1, DEVICES + 1)
            if f"stress-dev-{index:05d}" not in existing
        ],
        batch_size=500,
    )
    devices = list(Device.objects.filter(name__startswith="stress-dev-").order_by("name"))
    log(f"{len(devices)} devices")
    return devices


def create_interfaces(active, devices):
    """Bulk-create the interfaces of every device that has none yet."""
    with_interfaces = set(
        Interface.objects.filter(device__in=devices, name="Ethernet1/1").values_list("device_id", flat=True)
    )
    Interface.objects.bulk_create(
        [
            Interface(
                device=device,
                name=f"Ethernet1/{index}",
                type="1000base-t",
                status=active,
                enabled=True,
                mtu=1500,
                description=f"stress port {index}",
            )
            for device in devices
            if device.pk not in with_interfaces
            for index in range(1, INTERFACES + 1)
        ],
        batch_size=2000,
    )
    log(f"{INTERFACES} interfaces per device")


def create_addresses(active, devices):
    """Create one loopback address per device under the 10.0.0.0/8 prefix of the catalog."""
    namespace = Namespace.objects.get(name="Global")
    for index, device in enumerate(devices, start=1):
        if not IPAddress.objects.filter(dns_name=f"{device.name}.stress.example").exists():
            IPAddress.objects.create(
                address=f"10.200.{index // 256}.{index % 256}/32",
                namespace=namespace,
                status=active,
                dns_name=f"{device.name}.stress.example",
            )
    log("one loopback address per device")


def create_cables(devices):
    """Cable the first port of every pair of devices together."""
    connected = Status.objects.get(name="Connected")
    ports = {
        interface.device_id: interface for interface in Interface.objects.filter(device__in=devices, name="Ethernet1/1")
    }
    cabled = set(
        Interface.objects.filter(
            pk__in=[port.pk for port in ports.values()], cable_termination__isnull=False
        ).values_list("pk", flat=True)
    )
    for left, right in zip(devices[0::2], devices[1::2]):
        if ports[left.pk].pk in cabled or ports[right.pk].pk in cabled:
            continue
        Cable(termination_a=ports[left.pk], termination_b=ports[right.pk], status=connected).validated_save()
    log(f"{len(devices) // 2} cables")


def create_inventory():
    """Create the spoke-owned inventory, referencing the catalog pulled from the hub."""
    if not DeviceType.objects.filter(model__startswith="Stress Model").exists():
        raise SystemExit("pull the stress catalog from the hub first (job 'MongoDB to Nautobot')")
    if LOCATIONS <= REGIONS:
        raise SystemExit(f"SSOT_MONGO_STRESS_LOCATIONS must exceed the {REGIONS} regions; got {LOCATIONS}")
    active = Status.objects.get(name="Active")
    sites = create_locations(active)
    devices = create_devices(active, sites)
    create_interfaces(active, devices)
    create_addresses(active, devices)
    create_cables(devices)


started = time.monotonic()
if RESET:
    reset()
if ROLE == "HUB":
    create_catalog()
else:
    create_inventory()
log(f"done in {time.monotonic() - started:.0f}s")
