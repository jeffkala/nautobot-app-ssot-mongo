"""Tests for the inventory models that need custom ORM handling: cables and VLANs."""

from unittest.mock import MagicMock

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
    PowerOutlet,
    PowerPort,
)
from nautobot.extras.models import Role, Status
from nautobot.ipam.models import VLAN

from nautobot_ssot_mongo.diffsync.adapters import NAPNautobotAdapter
from nautobot_ssot_mongo.diffsync.models import CableSSoTModel, VLANSSoTModel


class InventoryFixturesMixin:  # pylint: disable=too-few-public-methods
    """Two devices with one interface each, in two locations."""

    def setUp(self):  # pylint: disable=invalid-name
        """Create the shared fixtures."""
        super().setUp()
        active = Status.objects.get(name="Active")
        cisco = Manufacturer.objects.create(name="Cisco")
        device_type = DeviceType.objects.create(manufacturer=cisco, model="C9300-48P")
        role = Role.objects.create(name="Network Device")
        role.content_types.set([ContentType.objects.get_for_model(Device)])
        site_type = LocationType.objects.create(name="Site")
        site_type.content_types.set(
            [ContentType.objects.get_for_model(Device), ContentType.objects.get_for_model(VLAN)]
        )
        self.austin = Location.objects.create(name="Austin", location_type=site_type, status=active)
        self.boston = Location.objects.create(name="Boston", location_type=site_type, status=active)
        self.dev_a = Device.objects.create(
            name="aus-core-01", location=self.austin, device_type=device_type, role=role, status=active
        )
        self.dev_b = Device.objects.create(
            name="bos-core-01", location=self.boston, device_type=device_type, role=role, status=active
        )
        self.iface_a = Interface.objects.create(device=self.dev_a, name="Ethernet1", type="1000base-t", status=active)
        self.iface_b = Interface.objects.create(device=self.dev_b, name="Ethernet1", type="1000base-t", status=active)
        self.connected = Status.objects.get(name="Connected")

    def _cable(self, a=None, b=None):
        a = a or self.iface_a
        b = b or self.iface_b
        return Cable.objects.create(termination_a=a, termination_b=b, status=self.connected, label="trunk")

    def _adapter(self):
        job = MagicMock()
        job.debug = False
        adapter = NAPNautobotAdapter(job=job, sync=None)
        adapter.top_level = ["cable", "vlan"]
        return adapter

    def _ids(self):
        return {
            "src_device": "aus-core-01",
            "src_location": "Austin",
            "src_port": "Ethernet1",
            "dst_device": "bos-core-01",
            "dst_location": "Boston",
            "dst_port": "Ethernet1",
        }


class CableModelTestCase(InventoryFixturesMixin, TestCase):
    """`CableSSoTModel` reads, creates, adopts, updates and deletes cables between interfaces."""

    def test_from_orm_record_orders_endpoints_canonically(self):
        cable = Cable.objects.create(termination_a=self.iface_b, termination_b=self.iface_a, status=self.connected)

        kwargs = CableSSoTModel.from_orm_record(cable)

        self.assertEqual(kwargs["src_device"], "aus-core-01")
        self.assertEqual(kwargs["src_location"], "Austin")
        self.assertEqual(kwargs["dst_device"], "bos-core-01")
        self.assertEqual(kwargs["status__name"], "Connected")
        self.assertEqual(kwargs["pk"], cable.pk)

    def test_adapter_loads_cables_through_from_orm_record(self):
        self._cable()
        adapter = self._adapter()

        adapter.load()

        cables = adapter.get_all("cable")
        self.assertEqual(len(cables), 1)
        self.assertEqual(cables[0].label, "trunk")

    def test_queryset_only_returns_cables_between_two_interfaces(self):
        interface_cable = self._cable()
        power_port = PowerPort.objects.create(device=self.dev_a, name="PS1")
        outlet = PowerOutlet.objects.create(device=self.dev_b, name="Outlet 1")
        Cable.objects.create(termination_a=power_port, termination_b=outlet, status=self.connected)

        self.assertEqual(list(CableSSoTModel.get_queryset()), [interface_cable])

    def test_loading_a_cable_reads_its_endpoints_from_the_prefetched_rows(self):
        self._cable()
        cable = CableSSoTModel.get_queryset().get()

        with self.assertNumQueries(0):
            kwargs = CableSSoTModel.from_orm_record(cable)

        self.assertEqual((kwargs["src_device"], kwargs["dst_device"]), ("aus-core-01", "bos-core-01"))

    def test_create_builds_a_cable_between_the_resolved_interfaces(self):
        adapter = self._adapter()

        instance = CableSSoTModel.create(adapter, ids=self._ids(), attrs={"status__name": "Connected", "label": "new"})

        self.assertIsNotNone(instance)
        cable = Cable.objects.get()
        self.assertEqual({cable.termination_a.pk, cable.termination_b.pk}, {self.iface_a.pk, self.iface_b.pk})
        self.assertEqual(cable.label, "new")
        self.assertIs(adapter.get("cable", instance.get_unique_id()), instance)

    def test_create_adopts_an_existing_cable_in_either_order(self):
        existing = Cable.objects.create(termination_a=self.iface_b, termination_b=self.iface_a, status=self.connected)
        adapter = self._adapter()

        instance = CableSSoTModel.create(adapter, ids=self._ids(), attrs={"status__name": "Connected", "label": "kept"})

        self.assertEqual(Cable.objects.count(), 1)
        self.assertEqual(instance.pk, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.label, "kept")

    def test_create_with_a_missing_endpoint_is_skipped_with_an_error(self):
        adapter = self._adapter()
        ids = {**self._ids(), "dst_port": "Ethernet99"}

        self.assertIsNone(CableSSoTModel.create(adapter, ids=ids, attrs={"status__name": "Connected"}))
        adapter.job.logger.error.assert_called_once()
        self.assertEqual(Cable.objects.count(), 0)

    def test_update_and_delete_touch_the_matching_cable(self):
        self._cable()
        adapter = self._adapter()
        adapter.load()
        model = adapter.get_all("cable")[0]

        model.update({"label": "renamed"})
        self.assertEqual(Cable.objects.get().label, "renamed")

        model.delete()
        self.assertEqual(Cable.objects.count(), 0)


class VLANModelTestCase(InventoryFixturesMixin, TestCase):
    """`VLANSSoTModel` keys VLANs on their M2M location."""

    def test_from_orm_record_reads_the_location_m2m(self):
        active = Status.objects.get(name="Active")
        vlan = VLAN.objects.create(vid=100, name="Users", status=active)
        vlan.locations.set([self.austin])

        kwargs = VLANSSoTModel.from_orm_record(vlan)

        self.assertEqual(kwargs["locations__name"], "Austin")
        self.assertEqual(kwargs["vid"], 100)
        self.assertEqual(kwargs["status__name"], "Active")

    def test_location_less_vlan_keys_on_an_empty_location(self):
        active = Status.objects.get(name="Active")
        VLAN.objects.create(vid=200, name="Mgmt", status=active)
        adapter = self._adapter()

        adapter.load()

        self.assertEqual([v.get_unique_id() for v in adapter.get_all("vlan")], ["__200"])

    def test_create_attaches_the_location(self):
        adapter = self._adapter()

        VLANSSoTModel.create(
            adapter,
            ids={"locations__name": "Austin", "vid": 300},
            attrs={"name": "Voice", "status__name": "Active", "vlan_group__name": None, "description": ""},
        )

        vlan = VLAN.objects.get(vid=300)
        self.assertEqual([location.name for location in vlan.locations.all()], ["Austin"])

    def test_create_adopts_an_existing_vlan_at_the_same_location(self):
        active = Status.objects.get(name="Active")
        existing = VLAN.objects.create(vid=300, name="Old", status=active)
        existing.locations.set([self.austin])
        adapter = self._adapter()

        instance = VLANSSoTModel.create(
            adapter,
            ids={"locations__name": "Austin", "vid": 300},
            attrs={"name": "Voice", "status__name": "Active", "vlan_group__name": None, "description": ""},
        )

        self.assertEqual(VLAN.objects.filter(vid=300).count(), 1)
        self.assertEqual(instance.pk, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.name, "Voice")
