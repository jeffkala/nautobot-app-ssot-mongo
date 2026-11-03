"""Constants shared by the sync jobs and the ownership model.

This module imports nothing from Django or Nautobot, so `models.py` and the data migrations can read the default
ownership table at import time.
"""

# Default Celery limits for the two sync jobs, in seconds. A full sync of a large spoke is far longer than Nautobot's
# 5 and 10 minute defaults; both can be overridden per job in the Job's edit view.
JOB_SOFT_TIME_LIMIT = 3600
JOB_TIME_LIMIT = 3900

# Documents written to MongoDB in one `bulk_write` call by the push job.
MONGO_BATCH_LIMIT = 100

# The content type the hub's ownership rules are published under in MongoDB, next to the objects they govern.
OWNERSHIP_RULES_CONTENT_TYPE = "nautobot_ssot_mongo.ownershiprule"

# Every content type the app can federate, with the role that owns it by default. The position in the list is the
# default sync order: each type comes after every type it references. The data migration seeds one `OwnershipRule`
# per entry, and the model only accepts content types listed here.
DEFAULT_OWNERSHIP_RULES = (
    # hub-owned catalog
    ("dcim", "manufacturer", "HUB"),
    ("dcim", "platform", "HUB"),
    ("dcim", "locationtype", "HUB"),
    ("dcim", "devicetype", "HUB"),
    ("tenancy", "tenantgroup", "HUB"),
    ("tenancy", "tenant", "HUB"),
    ("ipam", "namespace", "HUB"),
    ("ipam", "vrf", "HUB"),
    ("extras", "role", "HUB"),
    ("ipam", "prefix", "HUB"),
    # spoke-owned inventory
    ("dcim", "location", "SPOKE"),
    ("dcim", "rackgroup", "SPOKE"),
    ("dcim", "rack", "SPOKE"),
    ("dcim", "device", "SPOKE"),
    ("dcim", "interface", "SPOKE"),
    ("ipam", "vlan", "SPOKE"),
    ("ipam", "ipaddress", "SPOKE"),
    ("dcim", "cable", "SPOKE"),
)

# The content type of the heartbeat document every instance writes in its own partition after a successful sync.
INSTANCE_HEARTBEAT_CONTENT_TYPE = "nautobot_ssot_mongo.federatedinstance"

# Documents the app writes about itself rather than about a synced Nautobot object.
CONTROL_CONTENT_TYPES = (OWNERSHIP_RULES_CONTENT_TYPE, INSTANCE_HEARTBEAT_CONTENT_TYPE)
