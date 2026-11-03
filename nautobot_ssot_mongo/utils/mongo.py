"""Utility functions for working with MongoDB.

Documents are stored one-per-object in a single collection using this shape:

```json
{
    "_id": "ObjectId(...)",
    "nautobot_instance": "NAP-Austin",
    "nautobot_role": "NAP",
    "content_type": "dcim.device",
    "key": "aus-core-01__Austin",
    "data": {"name": "aus-core-01", "location__name": "Austin", "...": "..."},
    "deleted": false,
    "updated_at": "2026-01-01T00:00:00Z",
    "deleted_at": "2026-02-01T00:00:00Z",
    "nautobot_version": "3.2.7",
    "nautobot_apps": {"nautobot_ssot": "4.7.0", "nautobot_ssot_mongo": "0.2.0"}
}
```

`key` is the DiffSync unique identifier (identifier values joined with `__`) and, together with
`nautobot_instance` and `content_type`, uniquely identifies a document.

`nautobot_instance` names the Nautobot instance that owns (writes) the document and `nautobot_role` its role. Reads
may span several instances: the NDP reads the local objects published by every NAP in one query.

Deleting an object soft-deletes its document (a *tombstone*: `deleted: true` plus `deleted_at`). Reads hide
tombstones by default, an upsert for the same key revives the document in place, and an optional TTL index expires
tombstones after a configurable time. Documents written before these fields existed have no `deleted`,
`nautobot_role` or version fields; every read treats a missing `deleted` as `false`.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Union

from django.conf import settings
from pymongo import ASCENDING, MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

DEFAULT_DATABASE = "nautobot_sync"
DEFAULT_COLLECTION = "objects"
DEFAULT_NAUTOBOT_INSTANCE = "NDP"
DEFAULT_TOMBSTONE_TTL_DAYS = 30
UNIQUE_INDEX_NAME = "instance_content_type_key_unique"
TOMBSTONE_TTL_INDEX_NAME = "tombstone_ttl"
NOT_DELETED: Dict[str, Any] = {"deleted": {"$ne": True}}


class MongoSyncClient:
    """Thin wrapper around a pymongo collection holding synced Nautobot objects."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        uri: str = "mongodb://localhost:27017/",
        database: str = DEFAULT_DATABASE,
        collection: str = DEFAULT_COLLECTION,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_source: str = "admin",
        client: Optional[MongoClient] = None,
        **client_kwargs: Any,
    ):
        """Initialize the client.

        Args:
            uri (str): MongoDB connection URI.
            database (str): Database name holding synced objects.
            collection (str): Collection name holding synced objects.
            username (str, optional): Username, if not already embedded in the URI.
            password (str, optional): Password, if not already embedded in the URI.
            auth_source (str): Authentication database used with `username`/`password`.
            client (MongoClient, optional): Pre-built client (useful for tests with `mongomock`).
            **client_kwargs: Extra keyword arguments passed to `MongoClient`.
        """
        if client is None:
            if username:
                client_kwargs.update({"username": username, "password": password, "authSource": auth_source})
            client = MongoClient(uri, **client_kwargs)
        self.client = client
        self.database_name = database
        self.collection_name = collection
        self.database = self.client[database]
        self.collection: Collection = self.database[collection]

    @classmethod
    def from_settings(cls, app_settings: Optional[Dict[str, Any]] = None) -> "MongoSyncClient":
        """Build a client from the app's `PLUGINS_CONFIG` dictionary."""
        if app_settings is None:
            app_settings = get_app_settings()
        return cls(
            uri=app_settings.get("mongo_uri", "mongodb://localhost:27017/"),
            database=app_settings.get("mongo_database", DEFAULT_DATABASE),
            collection=app_settings.get("mongo_collection", DEFAULT_COLLECTION),
            username=app_settings.get("mongo_username") or None,
            password=app_settings.get("mongo_password") or None,
            auth_source=app_settings.get("mongo_auth_source", "admin"),
            serverSelectionTimeoutMS=int(app_settings.get("mongo_timeout_ms", 5000)),
        )

    def ensure_indexes(self, tombstone_ttl_seconds: Optional[int] = None) -> None:
        """Create the indexes the collection relies on, if they do not already exist.

        Args:
            tombstone_ttl_seconds (int, optional): When greater than zero, keep a TTL index that expires tombstones
                this many seconds after `deleted_at`. Zero or `None` removes the TTL index so tombstones are kept.
                Changing the value recreates the index, because MongoDB refuses to alter an existing index's options.
        """
        self.collection.create_index(
            [("nautobot_instance", ASCENDING), ("content_type", ASCENDING), ("key", ASCENDING)],
            unique=True,
            name=UNIQUE_INDEX_NAME,
        )
        existing = self.collection.index_information().get(TOMBSTONE_TTL_INDEX_NAME)
        if not tombstone_ttl_seconds or tombstone_ttl_seconds <= 0:
            if existing is not None:
                self.collection.drop_index(TOMBSTONE_TTL_INDEX_NAME)
            return
        if existing is not None and existing.get("expireAfterSeconds") != int(tombstone_ttl_seconds):
            self.collection.drop_index(TOMBSTONE_TTL_INDEX_NAME)
            existing = None
        if existing is None:
            self._create_ttl_index(int(tombstone_ttl_seconds))

    def _create_ttl_index(self, tombstone_ttl_seconds: int) -> None:
        self.collection.create_index(
            [("deleted_at", ASCENDING)],
            expireAfterSeconds=tombstone_ttl_seconds,
            partialFilterExpression={"deleted": True},
            name=TOMBSTONE_TTL_INDEX_NAME,
        )

    def ping(self) -> bool:
        """Return True if the server answers a ping."""
        try:
            self.client.admin.command("ping")
        except PyMongoError:
            return False
        return True

    @staticmethod
    def _filter(
        nautobot_instance: Union[str, Iterable[str]],
        content_type: str,
        key: Optional[str] = None,
        include_deleted: bool = False,
    ) -> Dict[str, Any]:
        if isinstance(nautobot_instance, str):
            query: Dict[str, Any] = {"nautobot_instance": nautobot_instance, "content_type": content_type}
        else:
            query = {"nautobot_instance": {"$in": list(nautobot_instance)}, "content_type": content_type}
        if key is not None:
            query["key"] = key
        if not include_deleted:
            query.update(NOT_DELETED)
        return query

    def instances(self, exclude_role: Optional[str] = None, exclude_instance: Optional[str] = None) -> List[str]:
        """Return the sorted, distinct `nautobot_instance` values present in the collection.

        Args:
            exclude_role (str, optional): Leave out partitions whose documents carry this `nautobot_role`. Documents
                written before the role field existed have no role and are never excluded by this filter.
            exclude_instance (str, optional): Leave out this partition (normally the caller's own).
        """
        query: Dict[str, Any] = {}
        if exclude_role:
            query["nautobot_role"] = {"$ne": exclude_role}
        if exclude_instance:
            query["nautobot_instance"] = {"$ne": exclude_instance}
        return sorted(self.collection.distinct("nautobot_instance", query))

    def find(
        self, nautobot_instance: Union[str, Iterable[str]], content_type: str, include_deleted: bool = False
    ) -> Iterable[Dict[str, Any]]:
        """Yield every document of a content type owned by one Nautobot instance, or by any of several.

        Args:
            nautobot_instance (str | Iterable[str]): One instance name, or several to read in one query.
            content_type (str): Nautobot content type label, for example `dcim.device`.
            include_deleted (bool): Also yield tombstones. Hidden by default.

        Documents are ordered by instance and then by key.
        """
        return self.collection.find(
            self._filter(nautobot_instance, content_type, include_deleted=include_deleted)
        ).sort([("nautobot_instance", ASCENDING), ("key", ASCENDING)])

    def find_data(self, nautobot_instance: Union[str, Iterable[str]], content_type: str) -> List[Dict[str, Any]]:
        """Return only the `data` payloads of the live documents matched by `find`."""
        return [document["data"] for document in self.find(nautobot_instance, content_type)]

    def get(
        self, nautobot_instance: str, content_type: str, key: str, include_deleted: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Return a single live document (or a tombstone too, when `include_deleted` is set), or None."""
        return self.collection.find_one(
            self._filter(nautobot_instance, content_type, key, include_deleted=include_deleted)
        )

    def upsert_operation(
        self,
        nautobot_instance: str,
        content_type: str,
        key: str,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UpdateOne:
        """Return the write that inserts or replaces the `data` payload of a document, for `write_batch`.

        A tombstone with the same key is revived in place rather than duplicated, which is why the match includes
        deleted documents. `metadata` holds the owner's role and version fields (see
        `nautobot_ssot_mongo.utils.nautobot.get_status_metadata`).
        """
        update: Dict[str, Any] = {
            "$set": {"data": data, "deleted": False, "updated_at": datetime.now(timezone.utc), **(metadata or {})},
            "$unset": {"deleted_at": ""},
        }
        return UpdateOne(self._filter(nautobot_instance, content_type, key, include_deleted=True), update, upsert=True)

    def delete_operation(
        self, nautobot_instance: str, content_type: str, key: str, metadata: Optional[Dict[str, Any]] = None
    ) -> UpdateOne:
        """Return the write that soft-deletes a document (turns it into a tombstone), for `write_batch`."""
        now = datetime.now(timezone.utc)
        return UpdateOne(
            self._filter(nautobot_instance, content_type, key),
            {"$set": {"deleted": True, "deleted_at": now, "updated_at": now, **(metadata or {})}},
        )

    def write_batch(self, operations: List[UpdateOne]) -> int:
        """Run `operations` in one ordered `bulk_write` and return how many documents they matched or inserted.

        The push job sends its writes this way, a hundred at a time, instead of one round trip per object. A
        write that the server rejects raises `BulkWriteError`; the operations before it have been applied.
        """
        if not operations:
            return 0
        result = self.collection.bulk_write(operations, ordered=True)
        return result.matched_count + result.upserted_count

    def upsert(
        self,
        nautobot_instance: str,
        content_type: str,
        key: str,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert or replace the `data` payload of one document and return the stored document.

        See `upsert_operation` for the write it runs.
        """
        self.write_batch([self.upsert_operation(nautobot_instance, content_type, key, data, metadata=metadata)])
        return self.get(nautobot_instance, content_type, key)

    def delete(
        self, nautobot_instance: str, content_type: str, key: str, metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Soft-delete one document (turn it into a tombstone) and return the number of documents changed."""
        return self.write_batch([self.delete_operation(nautobot_instance, content_type, key, metadata=metadata)])

    def tombstone_content_type(
        self, nautobot_instance: str, content_type: str, metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """Soft-delete every live document of `content_type` in one partition and return how many were changed.

        Used when a type passes to the other role: the documents the previous owner published must stop being
        read, while a re-publication by the new owner can still revive each one in place.
        """
        now = datetime.now(timezone.utc)
        result = self.collection.update_many(
            self._filter(nautobot_instance, content_type),
            {"$set": {"deleted": True, "deleted_at": now, "updated_at": now, **(metadata or {})}},
        )
        return result.modified_count

    def purge(self, nautobot_instance: str, content_type: str, key: str) -> int:
        """Physically remove a document, tombstone or not, and return the number of removed documents."""
        return self.collection.delete_one(
            self._filter(nautobot_instance, content_type, key, include_deleted=True)
        ).deleted_count

    def delete_all(self, nautobot_instance: Optional[str] = None) -> int:
        """Physically remove every document, optionally limited to one Nautobot instance."""
        query = {"nautobot_instance": nautobot_instance} if nautobot_instance else {}
        return self.collection.delete_many(query).deleted_count

    def backfill_metadata(self, nautobot_instance: str, metadata: Dict[str, Any]) -> int:
        """Stamp `metadata` (and `deleted: false`) on an instance's documents that predate the role field.

        An owner calls this before publishing so documents written by an older version of the app gain the
        `nautobot_role` and version fields without waiting for each object to change. Returns the number of
        documents updated.
        """
        result = self.collection.update_many(
            {"nautobot_instance": nautobot_instance, "nautobot_role": {"$exists": False}},
            {"$set": {"deleted": False, **metadata}},
        )
        return result.modified_count

    def close(self) -> None:
        """Close the underlying connection."""
        self.client.close()


def get_app_settings() -> Dict[str, Any]:
    """Return this app's `PLUGINS_CONFIG` dictionary."""
    return settings.PLUGINS_CONFIG.get("nautobot_ssot_mongo", {})


def get_nautobot_instance(app_settings: Optional[Dict[str, Any]] = None) -> str:
    """Return the configured Nautobot instance name used to partition documents."""
    if app_settings is None:
        app_settings = get_app_settings()
    return app_settings.get("nautobot_instance", DEFAULT_NAUTOBOT_INSTANCE)


def get_tombstone_ttl_seconds(app_settings: Optional[Dict[str, Any]] = None) -> int:
    """Return how long tombstones live, in seconds (`0` keeps them forever), from the `tombstone_ttl_days` setting."""
    if app_settings is None:
        app_settings = get_app_settings()
    return int(app_settings.get("tombstone_ttl_days", DEFAULT_TOMBSTONE_TTL_DAYS) or 0) * 86400
