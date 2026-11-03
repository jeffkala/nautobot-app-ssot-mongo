"""Round-trip sync tests between the Nautobot ORM and an in-memory MongoDB."""

from unittest.mock import MagicMock

import mongomock
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.testing import TestCase
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
from nautobot.extras.models.metadata import ObjectMetadata
from nautobot.ipam.models import VLAN, VRF, IPAddress, Namespace, Prefix
from nautobot.tenancy.models import Tenant, TenantGroup

from nautobot_ssot_mongo.diff import CustomOrderingDiff
from nautobot_ssot_mongo.diffsync.adapters import (
    MongoNautobotAdapter,
    MongoRemoteAdapter,
    NAPMongoAdapter,
    NAPNautobotAdapter,
)
from nautobot_ssot_mongo.owner_metadata import METADATA_TYPE_NAME
from nautobot_ssot_mongo.roles import get_role
from nautobot_ssot_mongo.utils.mongo import MongoSyncClient

INSTANCE = "Nautobot-A"


def _owner_for(obj):
    """Return the owner metadata value attached to `obj`, or None."""
    entry = ObjectMetadata.objects.filter(
        metadata_type__name=METADATA_TYPE_NAME,
        assigned_object_type=ContentType.objects.get_for_model(obj),
        assigned_object_id=obj.pk,
    ).first()
    return entry.value if entry else None


class RoundTripSyncTestCase(TestCase):
    """Sync Nautobot objects into MongoDB and back again."""

    def setUp(self):
        super().setUp()
        self.client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
        self.client.ensure_indexes()

        active = Status.objects.get(name="Active")
        self.global_namespace = Namespace.objects.get(name="Global")

        cisco = Manufacturer.objects.create(name="Cisco", description="Cisco Systems")
        Platform.objects.create(name="Cisco IOS", manufacturer=cisco, network_driver="cisco_ios", napalm_driver="ios")
        site = LocationType.objects.create(name="Site", nestable=False)
        site.content_types.set([ContentType.objects.get_for_model(Device)])
        DeviceType.objects.create(manufacturer=cisco, model="C9300-48P", part_number="C9300-48P", u_height=1)
        group = TenantGroup.objects.create(name="Customers")
        tenant = Tenant.objects.create(name="ACME Corp", tenant_group=group, description="ACME")
        VRF.objects.create(name="MGMT", rd="65000:100", namespace=self.global_namespace, tenant=tenant)
        loopback = Role.objects.create(name="Sync Loopback", color="2196f3", weight=100)
        loopback.content_types.set(
            [ContentType.objects.get_for_model(IPAddress), ContentType.objects.get_for_model(Prefix)]
        )
        Prefix.objects.create(
            prefix="10.0.0.0/24",
            namespace=self.global_namespace,
            status=active,
            role=loopback,
            tenant=tenant,
            description="Loopbacks",
        )
        IPAddress.objects.create(
            address="10.0.0.1/32",
            namespace=self.global_namespace,
            status=active,
            role=loopback,
            dns_name="core1.example.com",
        )

    def _nautobot_adapter(self, nautobot_instances=None):
        job = MagicMock()
        job.debug = False
        adapter = MongoNautobotAdapter(job=job, sync=None, nautobot_instances=nautobot_instances)
        adapter.load()
        return adapter

    def _mongo_adapter(self):
        job = MagicMock()
        job.debug = False
        adapter = MongoRemoteAdapter(job=job, sync=None, client=self.client, nautobot_instance=INSTANCE)
        adapter.load()
        return adapter

    def _sync_nautobot_to_mongo(self):
        self._nautobot_adapter().sync_to(self._mongo_adapter(), diff_class=CustomOrderingDiff)

    def test_nautobot_to_mongo_writes_one_document_per_object(self):
        self._sync_nautobot_to_mongo()

        self.assertEqual(
            self.client.find_data(INSTANCE, "dcim.manufacturer"),
            [{"name": "Cisco", "description": "Cisco Systems"}],
        )
        self.assertEqual(
            self.client.get(INSTANCE, "ipam.ipaddress", "10.0.0.1__Global")["data"],
            {
                "host": "10.0.0.1",
                "parent__namespace__name": "Global",
                "mask_length": 32,
                "type": "host",
                "status__name": "Active",
                "role__name": "Sync Loopback",
                "tenant__name": None,
                "dns_name": "core1.example.com",
                "description": "",
            },
        )
        prefix = self.client.get(INSTANCE, "ipam.prefix", "10.0.0.0__24__Global")["data"]
        self.assertEqual(prefix["tenant__name"], "ACME Corp")
        self.assertEqual(prefix["role__name"], "Sync Loopback")
        role = self.client.get(INSTANCE, "extras.role", "Sync Loopback")["data"]
        self.assertEqual(
            role["content_types"],
            [{"app_label": "ipam", "model": "ipaddress"}, {"app_label": "ipam", "model": "prefix"}],
        )
        self.assertEqual(self.client.get(INSTANCE, "ipam.vrf", "MGMT__Global")["data"]["rd"], "65000:100")
        self.assertEqual(
            self.client.get(INSTANCE, "tenancy.tenant", "ACME Corp")["data"]["tenant_group__name"], "Customers"
        )

    def test_nautobot_to_mongo_is_idempotent(self):
        self._sync_nautobot_to_mongo()
        count = self.client.collection.count_documents({})

        diff = self._nautobot_adapter().diff_to(self._mongo_adapter(), diff_class=CustomOrderingDiff)

        self.assertFalse(diff.has_diffs())
        self.assertEqual(self.client.collection.count_documents({}), count)

    def test_mongo_to_nautobot_creates_updates_and_deletes(self):
        self._sync_nautobot_to_mongo()
        self.client.upsert(
            INSTANCE, "dcim.manufacturer", "Arista", {"name": "Arista", "description": "Arista Networks"}
        )
        self.client.upsert(
            INSTANCE,
            "dcim.devicetype",
            "DCS-7050SX3__Arista",
            {"model": "DCS-7050SX3", "manufacturer__name": "Arista", "part_number": "7050", "u_height": 1},
        )
        self.client.upsert(
            INSTANCE,
            "ipam.ipaddress",
            "10.0.0.2__Global",
            {
                "host": "10.0.0.2",
                "parent__namespace__name": "Global",
                "mask_length": 32,
                "status__name": "Reserved",
                "role__name": "Sync Loopback",
                "dns_name": "core2.example.com",
            },
        )
        cisco = self.client.get(INSTANCE, "dcim.manufacturer", "Cisco")["data"]
        self.client.upsert(INSTANCE, "dcim.manufacturer", "Cisco", {**cisco, "description": "Updated"})
        self.client.delete(INSTANCE, "ipam.ipaddress", "10.0.0.1__Global")

        self._mongo_adapter().sync_to(self._nautobot_adapter(), diff_class=CustomOrderingDiff)

        self.assertEqual(Manufacturer.objects.get(name="Cisco").description, "Updated")
        self.assertTrue(DeviceType.objects.filter(model="DCS-7050SX3", manufacturer__name="Arista").exists())
        created = IPAddress.objects.get(host="10.0.0.2")
        self.assertEqual(created.parent.namespace, self.global_namespace)
        self.assertEqual(str(created.parent.prefix), "10.0.0.0/24")
        self.assertEqual(created.status.name, "Reserved")
        self.assertEqual(created.role.name, "Sync Loopback")
        self.assertEqual(_owner_for(created), {"owner": INSTANCE, "ownership_transferred": True})
        self.assertFalse(IPAddress.objects.filter(host="10.0.0.1").exists())
        self.assertTrue(
            self.client.get(INSTANCE, "ipam.ipaddress", "10.0.0.1__Global", include_deleted=True)["deleted"]
        )

    def test_nautobot_delete_becomes_a_tombstone_that_a_recreate_revives(self):
        self._sync_nautobot_to_mongo()
        IPAddress.objects.get(host="10.0.0.1").delete()

        self._sync_nautobot_to_mongo()
        tombstone = self.client.get(INSTANCE, "ipam.ipaddress", "10.0.0.1__Global", include_deleted=True)
        self.assertTrue(tombstone["deleted"])
        self.assertIsNone(self.client.get(INSTANCE, "ipam.ipaddress", "10.0.0.1__Global"))

        IPAddress.objects.create(
            address="10.0.0.1/32", namespace=self.global_namespace, status=Status.objects.get(name="Active")
        )
        self._sync_nautobot_to_mongo()
        revived = self.client.get(INSTANCE, "ipam.ipaddress", "10.0.0.1__Global")
        self.assertEqual(revived["_id"], tombstone["_id"])
        self.assertFalse(revived["deleted"])


class NAPOwnedSyncTestCase(TestCase):
    """Sync the objects owned by NAPs (locations and devices) through MongoDB with the SSoT NAP adapters."""

    def setUp(self):
        super().setUp()
        self.client = MongoSyncClient(client=mongomock.MongoClient(), database="testdb", collection="objects")
        self.client.ensure_indexes()

        active = Status.objects.get(name="Active")
        cisco = Manufacturer.objects.create(name="Cisco")
        self.platform = Platform.objects.create(name="Cisco IOS", manufacturer=cisco)
        self.device_type = DeviceType.objects.create(manufacturer=cisco, model="C9300-48P")
        self.role = Role.objects.create(name="Network Device")
        self.role.content_types.set([ContentType.objects.get_for_model(Device)])
        region_type = LocationType.objects.create(name="Region", nestable=True)
        self.site_type = LocationType.objects.create(name="Site", parent=region_type)
        self.site_type.content_types.set([ContentType.objects.get_for_model(Device)])
        self.tenant = Tenant.objects.create(name="Internal")
        self.site_type.content_types.add(
            ContentType.objects.get_for_model(Rack),
            ContentType.objects.get_for_model(RackGroup),
            ContentType.objects.get_for_model(VLAN),
            ContentType.objects.get_for_model(Prefix),
        )
        region = Location.objects.create(name="US-South", location_type=region_type, status=active)
        self.site = Location.objects.create(
            name="Austin", location_type=self.site_type, parent=region, status=active, tenant=self.tenant
        )

    def _nautobot_adapter(self, nautobot_instances=None):
        job = MagicMock()
        job.debug = False
        adapter = NAPNautobotAdapter(job=job, sync=None, nautobot_instances=nautobot_instances)
        adapter.load()
        return adapter

    def _mongo_adapter(self, instance, read_instances=None):
        job = MagicMock()
        job.debug = False
        adapter = NAPMongoAdapter(
            job=job, sync=None, client=self.client, nautobot_instance=instance, read_instances=read_instances
        )
        adapter.load()
        return adapter

    def _seed_nap(self, instance, region, site, device_name):
        self.client.upsert(
            instance,
            "dcim.location",
            f"{region}__None",
            {"name": region, "parent__name": None, "location_type__name": "Region", "status__name": "Active"},
        )
        self.client.upsert(
            instance,
            "dcim.location",
            f"{site}__{region}",
            {
                "name": site,
                "parent__name": region,
                "location_type__name": "Site",
                "status__name": "Active",
                "tenant__name": "Internal",
                "facility": f"{site[:3].upper()}1",
            },
        )
        self.client.upsert(
            instance,
            "dcim.device",
            f"{device_name}__{site}",
            {
                "name": device_name,
                "location__name": site,
                "device_type__model": "C9300-48P",
                "device_type__manufacturer__name": "Cisco",
                "role__name": "Network Device",
                "platform__name": "Cisco IOS",
                "status__name": "Active",
                "serial": "SN1",
            },
        )

    def test_nap_loads_its_inventory_into_mongo(self):
        active = Status.objects.get(name="Active")
        device = Device.objects.create(
            name="aus-core-01",
            location=self.site,
            device_type=self.device_type,
            role=self.role,
            platform=self.platform,
            status=active,
            serial="JPE1",
        )
        Device.objects.create(location=self.site, device_type=self.device_type, role=self.role, status=active)
        edge = Device.objects.create(
            name="aus-edge-01", location=self.site, device_type=self.device_type, role=self.role, status=active
        )
        group = RackGroup.objects.create(name="Row A", location=self.site)
        Rack.objects.create(name="A1", location=self.site, rack_group=group, status=active, u_height=42)
        iface_a = Interface.objects.create(device=device, name="Ethernet1", type="1000base-t", status=active)
        iface_b = Interface.objects.create(device=edge, name="Ethernet1", type="1000base-t", status=active)
        Cable.objects.create(termination_a=iface_b, termination_b=iface_a, status=Status.objects.get(name="Connected"))
        vlan = VLAN.objects.create(vid=100, name="Users", status=active)
        vlan.locations.set([self.site])
        global_namespace = Namespace.objects.get(name="Global")
        Prefix.objects.create(prefix="10.0.0.0/24", namespace=global_namespace, status=active)
        IPAddress.objects.create(address="10.0.0.1/32", namespace=global_namespace, status=active)

        self._nautobot_adapter().sync_to(self._mongo_adapter("NAP-Austin"), diff_class=CustomOrderingDiff)

        self.assertEqual(self.client.get("NAP-Austin", "dcim.rack", "Austin__A1")["data"]["rack_group__name"], "Row A")
        self.assertEqual(
            self.client.get("NAP-Austin", "dcim.interface", "aus-core-01__Austin__Ethernet1")["data"]["type"],
            "1000base-t",
        )
        cable = self.client.get(
            "NAP-Austin", "dcim.cable", "aus-core-01__Austin__Ethernet1__aus-edge-01__Austin__Ethernet1"
        )
        self.assertEqual(cable["data"]["status__name"], "Connected")
        self.assertEqual(self.client.get("NAP-Austin", "ipam.vlan", "Austin__100")["data"]["name"], "Users")
        self.assertIsNotNone(self.client.get("NAP-Austin", "ipam.ipaddress", "10.0.0.1__Global"))
        self.assertEqual(
            self.client.get("NAP-Austin", "dcim.device", "aus-core-01__Austin")["nautobot_role"], get_role()
        )

        self.assertEqual(
            [document["key"] for document in self.client.find("NAP-Austin", "dcim.location")],
            ["Austin__US-South", "US-South__None"],
        )
        self.assertEqual(
            self.client.get("NAP-Austin", "dcim.device", "aus-core-01__Austin")["data"],
            {
                "name": "aus-core-01",
                "location__name": "Austin",
                "device_type__model": "C9300-48P",
                "device_type__manufacturer__name": "Cisco",
                "role__name": "Network Device",
                "platform__name": "Cisco IOS",
                "status__name": "Active",
                "tenant__name": None,
                "serial": "JPE1",
            },
        )
        self.assertEqual(self.client.collection.count_documents({"content_type": "dcim.device"}), 2)
        self.assertEqual(self.client.collection.count_documents({"content_type": "dcim.manufacturer"}), 0)

    def test_ndp_syncs_in_the_objects_of_every_nap(self):
        self._seed_nap("NAP-Austin", "US-South", "Austin", "aus-core-01")
        self._seed_nap("NAP-Boston", "US-East", "Boston", "bos-core-01")
        self.site.name = "Dublin"
        self.site.validated_save()

        naps = ["NAP-Austin", "NAP-Boston"]
        source = self._mongo_adapter("NDP", read_instances=naps)
        source.sync_to(self._nautobot_adapter(nautobot_instances=naps), diff_class=CustomOrderingDiff)

        austin = Location.objects.get(name="Austin")
        self.assertEqual(austin.parent.name, "US-South")
        self.assertEqual(austin.tenant, self.tenant)
        self.assertEqual(austin.facility, "AUS1")
        self.assertEqual(_owner_for(austin), {"owner": "NAP-Austin", "ownership_transferred": True})
        self.assertEqual(Location.objects.get(name="Boston").parent.name, "US-East")
        device = Device.objects.get(name="bos-core-01")
        self.assertEqual(device.location.name, "Boston")
        self.assertEqual(device.device_type, self.device_type)
        self.assertEqual(device.platform, self.platform)
        self.assertEqual(device.role, self.role)
        self.assertEqual(_owner_for(device), {"owner": "NAP-Boston", "ownership_transferred": True})
        self.assertEqual(Device.objects.count(), 2)
        # The NDP's own hand-made location is not owned by any NAP, so the scoped pull leaves it alone.
        self.assertTrue(Location.objects.filter(name="Dublin").exists())
        self.assertFalse(
            self._mongo_adapter("NDP", read_instances=naps)
            .diff_to(self._nautobot_adapter(nautobot_instances=naps), diff_class=CustomOrderingDiff)
            .has_diffs()
        )

    def test_ndp_drops_what_a_nap_tombstoned_but_keeps_its_own_objects(self):
        self._seed_nap("NAP-Austin", "US-South", "Austin", "aus-core-01")
        naps = ["NAP-Austin"]
        self._mongo_adapter("NDP", read_instances=naps).sync_to(
            self._nautobot_adapter(nautobot_instances=naps), diff_class=CustomOrderingDiff
        )
        self.assertTrue(Device.objects.filter(name="aus-core-01").exists())
        self.client.delete("NAP-Austin", "dcim.device", "aus-core-01__Austin")
        Device.objects.create(
            name="ndp-local-01",
            location=self.site,
            device_type=self.device_type,
            role=self.role,
            status=Status.objects.get(name="Active"),
        )

        self._mongo_adapter("NDP", read_instances=naps).sync_to(
            self._nautobot_adapter(nautobot_instances=naps), diff_class=CustomOrderingDiff
        )

        self.assertFalse(Device.objects.filter(name="aus-core-01").exists())
        self.assertTrue(Device.objects.filter(name="ndp-local-01").exists())
