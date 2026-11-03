"""Unit tests for nautobot_ssot_mongo app."""

from mongomock.collection import BulkOperationBuilder

_add_update = BulkOperationBuilder.add_update


def _add_update_ignoring_sort(self, *args, sort=None, **kwargs):  # pylint: disable=unused-argument
    # mongomock 4.3.0 predates pymongo 4.11, whose `UpdateOne` passes `sort=` to the bulk builder. The app never
    # sets a sort, so dropping the keyword lets `bulk_write` run against the in-memory collection in the tests.
    return _add_update(self, *args, **kwargs)


BulkOperationBuilder.add_update = _add_update_ignoring_sort
