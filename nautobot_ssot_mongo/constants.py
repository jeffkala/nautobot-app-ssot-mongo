"""Constants shared by the sync jobs."""

# Default Celery limits for the two sync jobs, in seconds. A full sync of a large NAP is far longer than Nautobot's
# 5 and 10 minute defaults; both can be overridden per job in the Job's edit view.
JOB_SOFT_TIME_LIMIT = 3600
JOB_TIME_LIMIT = 3900

# Documents written to MongoDB in one `bulk_write` call by the push job.
MONGO_BATCH_LIMIT = 100
