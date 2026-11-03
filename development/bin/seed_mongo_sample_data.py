#!/usr/bin/env python
"""Seed a MongoDB collection with sample documents in the shape the MongoDB SSoT app expects.

The catalog is written under the hub instance (`--hub-instance`, default `HUB`) and the inventory (locations, rack
groups, racks, devices, interfaces, VLANs, IP addresses and cables) under two sample spoke instances, `SPOKE-Austin`
and `SPOKE-Boston`. Every document carries the role and version fields the app stamps, taken from the installed
packages when available, so the loader's version guard accepts them.

This script only depends on `pymongo` so it can run outside of a Nautobot environment, for example:

    poetry run python development/bin/seed_mongo_sample_data.py --uri mongodb://localhost:27017/ --reset

Use `--username`/`--password` (or embed credentials in the URI) when the server enforces authentication.
"""

import argparse
import importlib.metadata
import os
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient

HUB_INSTANCE = "HUB"
TOMBSTONE_TTL_SECONDS = 30 * 86400

# (content_type, identifier field names, list of data payloads): the catalog owned by the hub
GLOBAL_SAMPLE_DATA = [
    (
        "dcim.manufacturer",
        ("name",),
        [
            {"name": "Arista", "description": "Arista Networks"},
            {"name": "Cisco", "description": "Cisco Systems"},
            {"name": "Juniper", "description": "Juniper Networks"},
        ],
    ),
    (
        "dcim.platform",
        ("name",),
        [
            {
                "name": "Arista EOS",
                "manufacturer__name": "Arista",
                "description": "",
                "network_driver": "arista_eos",
                "napalm_driver": "eos",
                "napalm_args": None,
            },
            {
                "name": "Cisco IOS",
                "manufacturer__name": "Cisco",
                "description": "",
                "network_driver": "cisco_ios",
                "napalm_driver": "ios",
                "napalm_args": None,
            },
            {
                "name": "Juniper Junos",
                "manufacturer__name": "Juniper",
                "description": "",
                "network_driver": "juniper_junos",
                "napalm_driver": "junos",
                "napalm_args": None,
            },
        ],
    ),
    (
        "dcim.locationtype",
        ("name",),
        [
            {"name": "Region", "parent__name": None, "description": "", "nestable": True, "content_types": []},
            {
                "name": "Site",
                "parent__name": "Region",
                "description": "",
                "nestable": False,
                "content_types": [
                    {"app_label": "dcim", "model": "device"},
                    {"app_label": "ipam", "model": "prefix"},
                ],
            },
        ],
    ),
    (
        "dcim.devicetype",
        ("model", "manufacturer__name"),
        [
            {
                "model": "DCS-7050SX3-48YC8",
                "manufacturer__name": "Arista",
                "part_number": "DCS-7050SX3-48YC8",
                "u_height": 1,
                "is_full_depth": True,
            },
            {
                "model": "C9300-48P",
                "manufacturer__name": "Cisco",
                "part_number": "C9300-48P",
                "u_height": 1,
                "is_full_depth": False,
            },
            {
                "model": "MX204",
                "manufacturer__name": "Juniper",
                "part_number": "MX204",
                "u_height": 1,
                "is_full_depth": True,
            },
        ],
    ),
    (
        "tenancy.tenantgroup",
        ("name",),
        [
            {"name": "Customers", "parent__name": None, "description": "External customers"},
            {"name": "Enterprise", "parent__name": "Customers", "description": "Enterprise accounts"},
        ],
    ),
    (
        "tenancy.tenant",
        ("name",),
        [
            {"name": "ACME Corp", "tenant_group__name": "Enterprise", "description": "ACME Corporation"},
            {"name": "Internal", "tenant_group__name": None, "description": "Internal use"},
        ],
    ),
    (
        "ipam.namespace",
        ("name",),
        [
            {"name": "Global", "description": "Default Global namespace. Created by Nautobot."},
            {"name": "ACME", "description": "ACME overlapping address space"},
        ],
    ),
    (
        "ipam.vrf",
        ("name", "namespace__name"),
        [
            {
                "name": "MGMT",
                "namespace__name": "Global",
                "rd": "65000:100",
                "description": "Management",
                "tenant__name": "Internal",
            },
            {
                "name": "ACME-PROD",
                "namespace__name": "ACME",
                "rd": "65000:200",
                "description": "",
                "tenant__name": "ACME Corp",
            },
        ],
    ),
    (
        "extras.role",
        ("name",),
        [
            {
                "name": "Loopback",
                "description": "Loopback addressing",
                "color": "2196f3",
                "weight": 100,
                "content_types": [
                    {"app_label": "ipam", "model": "ipaddress"},
                    {"app_label": "ipam", "model": "prefix"},
                ],
            },
            {
                "name": "Point-to-Point",
                "description": "Point-to-point links",
                "color": "4caf50",
                "weight": 200,
                "content_types": [{"app_label": "ipam", "model": "prefix"}],
            },
            {
                "name": "Network Device",
                "description": "Switches and routers",
                "color": "ff9800",
                "weight": 300,
                "content_types": [{"app_label": "dcim", "model": "device"}],
            },
        ],
    ),
    (
        "ipam.prefix",
        ("network", "prefix_length", "namespace__name"),
        [
            {
                "network": "10.0.0.0",
                "prefix_length": 8,
                "namespace__name": "Global",
                "type": "container",
                "status__name": "Active",
                "role__name": None,
                "tenant__name": "Internal",
                "description": "RFC1918 summary",
            },
            {
                "network": "10.0.0.0",
                "prefix_length": 24,
                "namespace__name": "Global",
                "type": "network",
                "status__name": "Active",
                "role__name": "Loopback",
                "tenant__name": "Internal",
                "description": "Loopbacks",
            },
            {
                "network": "10.1.0.0",
                "prefix_length": 31,
                "namespace__name": "Global",
                "type": "network",
                "status__name": "Reserved",
                "role__name": "Point-to-Point",
                "tenant__name": None,
                "description": "Core link",
            },
            {
                "network": "192.168.0.0",
                "prefix_length": 24,
                "namespace__name": "ACME",
                "type": "network",
                "status__name": "Active",
                "role__name": None,
                "tenant__name": "ACME Corp",
                "description": "ACME office",
            },
        ],
    ),
]


# spoke instance name -> list of (content_type, identifier field names, list of data payloads) owned by that spoke
LOCAL_SAMPLE_DATA = {
    "SPOKE-Austin": [
        (
            "dcim.rackgroup",
            ("location__name", "name"),
            [{"location__name": "Austin", "name": "Row A", "description": "", "parent__name": None}],
        ),
        (
            "dcim.rack",
            ("location__name", "name"),
            [
                {
                    "location__name": "Austin",
                    "name": "A1",
                    "status__name": "Active",
                    "rack_group__name": "Row A",
                    "rack_group__location__name": "Austin",
                    "u_height": 42,
                    "facility_id": "",
                    "serial": "",
                }
            ],
        ),
        (
            "dcim.interface",
            ("device__name", "device__location__name", "name"),
            [
                {
                    "device__name": "aus-core-01",
                    "device__location__name": "Austin",
                    "name": "Ethernet1",
                    "status__name": "Active",
                    "type": "1000base-t",
                    "enabled": True,
                    "description": "to aus-edge-01",
                    "mac_address": None,
                    "mtu": 1500,
                    "mode": "",
                },
                {
                    "device__name": "aus-edge-01",
                    "device__location__name": "Austin",
                    "name": "Ethernet1",
                    "status__name": "Active",
                    "type": "1000base-t",
                    "enabled": True,
                    "description": "to aus-core-01",
                    "mac_address": None,
                    "mtu": 1500,
                    "mode": "",
                },
            ],
        ),
        (
            "ipam.vlan",
            ("locations__name", "vid"),
            [
                {
                    "locations__name": "Austin",
                    "vid": 100,
                    "name": "Users",
                    "status__name": "Active",
                    "vlan_group__name": None,
                    "description": "",
                }
            ],
        ),
        (
            "ipam.ipaddress",
            ("host", "parent__namespace__name"),
            [
                {
                    "host": "10.0.0.1",
                    "parent__namespace__name": "Global",
                    "mask_length": 32,
                    "type": "host",
                    "status__name": "Active",
                    "role__name": "Loopback",
                    "tenant__name": "Internal",
                    "dns_name": "aus-core-01.example.com",
                    "description": "aus-core-01 loopback",
                }
            ],
        ),
        (
            "dcim.cable",
            ("src_device", "src_location", "src_port", "dst_device", "dst_location", "dst_port"),
            [
                {
                    "src_device": "aus-core-01",
                    "src_location": "Austin",
                    "src_port": "Ethernet1",
                    "dst_device": "aus-edge-01",
                    "dst_location": "Austin",
                    "dst_port": "Ethernet1",
                    "status__name": "Connected",
                    "type": "",
                    "label": "core-edge",
                    "color": "",
                    "length": None,
                    "length_unit": "",
                }
            ],
        ),
        (
            "dcim.location",
            ("name", "parent__name"),
            [
                {
                    "name": "US-South",
                    "parent__name": None,
                    "location_type__name": "Region",
                    "status__name": "Active",
                    "tenant__name": None,
                    "description": "Southern US",
                    "facility": "",
                },
                {
                    "name": "Austin",
                    "parent__name": "US-South",
                    "location_type__name": "Site",
                    "status__name": "Active",
                    "tenant__name": "Internal",
                    "description": "Austin office",
                    "facility": "AUS1",
                },
            ],
        ),
        (
            "dcim.device",
            ("name", "location__name"),
            [
                {
                    "name": "aus-core-01",
                    "location__name": "Austin",
                    "device_type__model": "DCS-7050SX3-48YC8",
                    "device_type__manufacturer__name": "Arista",
                    "role__name": "Network Device",
                    "platform__name": "Arista EOS",
                    "status__name": "Active",
                    "tenant__name": "Internal",
                    "serial": "JPE00000001",
                },
                {
                    "name": "aus-edge-01",
                    "location__name": "Austin",
                    "device_type__model": "MX204",
                    "device_type__manufacturer__name": "Juniper",
                    "role__name": "Network Device",
                    "platform__name": "Juniper Junos",
                    "status__name": "Active",
                    "tenant__name": "Internal",
                    "serial": "",
                },
            ],
        ),
    ],
    "SPOKE-Boston": [
        (
            "dcim.location",
            ("name", "parent__name"),
            [
                {
                    "name": "US-East",
                    "parent__name": None,
                    "location_type__name": "Region",
                    "status__name": "Active",
                    "tenant__name": None,
                    "description": "Eastern US",
                    "facility": "",
                },
                {
                    "name": "Boston",
                    "parent__name": "US-East",
                    "location_type__name": "Site",
                    "status__name": "Active",
                    "tenant__name": "ACME Corp",
                    "description": "Boston office",
                    "facility": "BOS1",
                },
            ],
        ),
        (
            "dcim.device",
            ("name", "location__name"),
            [
                {
                    "name": "bos-access-01",
                    "location__name": "Boston",
                    "device_type__model": "C9300-48P",
                    "device_type__manufacturer__name": "Cisco",
                    "role__name": "Network Device",
                    "platform__name": "Cisco IOS",
                    "status__name": "Active",
                    "tenant__name": "ACME Corp",
                    "serial": "FOC00000001",
                },
            ],
        ),
    ],
}


def parse_args(argv):
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--uri",
        default=os.getenv("NAUTOBOT_SSOT_MONGO_URI", "mongodb://localhost:27017/"),
        help="MongoDB connection URI (default: $NAUTOBOT_SSOT_MONGO_URI or mongodb://localhost:27017/)",
    )
    parser.add_argument(
        "--username", default=os.getenv("NAUTOBOT_SSOT_MONGO_USERNAME") or None, help="MongoDB username (optional)"
    )
    parser.add_argument(
        "--password", default=os.getenv("NAUTOBOT_SSOT_MONGO_PASSWORD") or None, help="MongoDB password (optional)"
    )
    parser.add_argument("--auth-source", default="admin", help="Authentication database (default: admin)")
    parser.add_argument("--database", default="nautobot_sync", help="Database name (default: nautobot_sync)")
    parser.add_argument("--collection", default="objects", help="Collection name (default: objects)")
    parser.add_argument(
        "--hub-instance",
        default=os.getenv("NAUTOBOT_SSOT_MONGO_HUB_INSTANCE", HUB_INSTANCE),
        help="Nautobot instance name the global reference data is tagged with (default: HUB)",
    )
    parser.add_argument("--reset", action="store_true", help="Drop the collection before seeding")
    return parser.parse_args(argv)


def status_metadata(role):
    """Role and version fields the app stamps on every document; versions are left out when not installed."""
    metadata = {"nautobot_role": role}
    try:
        metadata["nautobot_version"] = importlib.metadata.version("nautobot")
        metadata["nautobot_apps"] = {
            "nautobot_ssot": importlib.metadata.version("nautobot_ssot"),
            "nautobot_ssot_mongo": importlib.metadata.version("nautobot_ssot_mongo"),
        }
    except importlib.metadata.PackageNotFoundError:
        pass
    return metadata


def seed(collection, nautobot_instance, role, sample_data, now):
    """Upsert one group of sample documents and print a per-content-type summary."""
    metadata = status_metadata(role)
    for content_type, identifiers, items in sample_data:
        for data in items:
            key = "__".join(str(data[identifier]) for identifier in identifiers)
            collection.update_one(
                {"nautobot_instance": nautobot_instance, "content_type": content_type, "key": key},
                {"$set": {"data": data, "deleted": False, "updated_at": now, **metadata}, "$unset": {"deleted_at": ""}},
                upsert=True,
            )
        count = collection.count_documents({"nautobot_instance": nautobot_instance, "content_type": content_type})
        print(f"{nautobot_instance:<12} {role:<4} {content_type:<18} {count:>3} document(s)")


def main(argv=None):
    """Seed the collection with the hub's global data and each sample spoke's local data."""
    args = parse_args(argv)
    client_kwargs = {"serverSelectionTimeoutMS": 5000}
    if args.username:
        client_kwargs.update({"username": args.username, "password": args.password, "authSource": args.auth_source})
    client = MongoClient(args.uri, **client_kwargs)
    collection = client[args.database][args.collection]

    if args.reset:
        collection.drop()
    collection.create_index(
        [("nautobot_instance", ASCENDING), ("content_type", ASCENDING), ("key", ASCENDING)],
        unique=True,
        name="instance_content_type_key_unique",
    )
    if "tombstone_ttl" not in collection.index_information():
        collection.create_index(
            [("deleted_at", ASCENDING)],
            expireAfterSeconds=TOMBSTONE_TTL_SECONDS,
            partialFilterExpression={"deleted": True},
            name="tombstone_ttl",
        )

    now = datetime.now(timezone.utc)
    seed(collection, args.hub_instance, "HUB", GLOBAL_SAMPLE_DATA, now)
    for spoke_instance, sample_data in LOCAL_SAMPLE_DATA.items():
        seed(collection, spoke_instance, "SPOKE", sample_data, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
