"""Constants shared by the sync jobs and the ownership model.

This module imports nothing from Django or Nautobot, so `models.py` and the data migrations can read the default
ownership table at import time.
"""

# Default Celery limits for the two sync jobs, in seconds. A full sync of a large NAP is far longer than Nautobot's
# 5 and 10 minute defaults; both can be overridden per job in the Job's edit view.
JOB_SOFT_TIME_LIMIT = 3600
JOB_TIME_LIMIT = 3900

# Documents written to MongoDB in one `bulk_write` call by the push job.
MONGO_BATCH_LIMIT = 100

# The content type the NDP's ownership rules are published under in MongoDB, next to the objects they govern.
OWNERSHIP_RULES_CONTENT_TYPE = "nautobot_ssot_mongo.ownershiprule"

# Every content type the app can federate, with the role that owns it by default. The position in the list is the
# default sync order: each type comes after every type it references. The data migration seeds one `OwnershipRule`
# per entry, and the model only accepts content types listed here.
DEFAULT_OWNERSHIP_RULES = (
    # NDP-owned catalog
    ("dcim", "manufacturer", "NDP"),
    ("dcim", "platform", "NDP"),
    ("dcim", "locationtype", "NDP"),
    ("dcim", "devicetype", "NDP"),
    ("tenancy", "tenantgroup", "NDP"),
    ("tenancy", "tenant", "NDP"),
    ("ipam", "namespace", "NDP"),
    ("ipam", "vrf", "NDP"),
    ("extras", "role", "NDP"),
    ("ipam", "prefix", "NDP"),
    # NAP-owned inventory
    ("dcim", "location", "NAP"),
    ("dcim", "rackgroup", "NAP"),
    ("dcim", "rack", "NAP"),
    ("dcim", "device", "NAP"),
    ("dcim", "interface", "NAP"),
    ("ipam", "vlan", "NAP"),
    ("ipam", "ipaddress", "NAP"),
    ("dcim", "cable", "NAP"),
)
