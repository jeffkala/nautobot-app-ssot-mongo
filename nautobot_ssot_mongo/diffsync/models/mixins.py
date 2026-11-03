"""Mixins shared by the DiffSync models.

`LocalOwnerMixin` is mixed into every Nautobot-side model, `MongoRemoteMixin` into every MongoDB-side model.
"""

from typing import ClassVar

from nautobot_ssot_mongo.owner_metadata import job_logger, stamp_owner


class LocalOwnerMixin:
    """Adopt pre-existing rows and stamp the owner metadata when objects are written by a MongoDB -> Nautobot sync.

    Mixed in ahead of `NautobotModel`. On every `create()` and `update()` the adapter is checked for a reference
    back to the MongoDB source adapter (`adapter._mongo_source`); when present, the originating `nautobot_instance`
    is looked up by `(modelname, identifier-tuple)` in its `origin_owners` and stamped on the saved ORM object. A
    push (Nautobot -> MongoDB) never sets `_mongo_source`, so local writes outside a pull stamp nothing.

    The pull target only loads objects that already carry owner metadata, so an object that exists in the database
    but was never stamped shows up as a DiffSync *create*. Creating it again would violate the model's uniqueness
    constraint, so `create()` adopts the existing row instead: updates it in place and stamps ownership.
    """

    @classmethod
    def _origin_owner_lookup(cls, adapter, ids):
        """Resolve the originating Nautobot instance for `ids`, or None outside a pull."""
        source = getattr(adapter, "_mongo_source", None)
        owners = getattr(source, "origin_owners", None) if source is not None else None
        if not owners:
            return None
        return owners.get((cls._modelname, tuple(sorted(ids.items()))))

    @classmethod
    def _stamp(cls, adapter, obj, ids):
        """Stamp the origin owner (if any) on `obj`."""
        owner = cls._origin_owner_lookup(adapter, ids)
        if owner is not None and obj is not None:
            stamp_owner(obj, owner, log=job_logger(adapter))

    @classmethod
    def _update_obj_with_parameters(cls, obj, parameters, adapter):
        """Stash the saved ORM object so create()/update() can stamp its owner."""
        super()._update_obj_with_parameters(obj, parameters, adapter)
        adapter._mongo_saved_obj = obj  # pylint: disable=protected-access

    @classmethod
    def _find_existing(cls, ids):
        """Return the ORM row matching DiffSync `ids` for adoption, or None.

        Works for models whose identifiers are valid ORM lookups. Models with synthetic identifiers (for example
        `VLANSSoTModel.locations__name`, backed by an M2M) override this with the equivalent query.
        """
        return cls._model.objects.filter(**ids).first()

    @classmethod
    def create(cls, adapter, ids, attrs):
        """Create via the parent, or adopt a pre-existing ORM row, then stamp its owner."""
        existing = cls._find_existing(ids)
        if existing is not None:
            cls._update_obj_with_parameters(existing, {**ids, **attrs}, adapter)
            cls._stamp(adapter, adapter._mongo_saved_obj, ids)  # pylint: disable=protected-access
            job_logger(adapter).info(
                "Adopted existing %s %s: updated and stamped owner instead of creating", cls._modelname, ids
            )
            instance = cls(**ids, **attrs, pk=existing.pk)
            adapter.add(instance)
            return instance

        instance = super().create(adapter, ids, attrs)
        cls._stamp(adapter, getattr(adapter, "_mongo_saved_obj", None), ids)
        return instance

    def update(self, attrs):
        """Update via the parent, then stamp the origin owner on the saved object."""
        result = super().update(attrs)
        ids = {key: getattr(self, key) for key in self._identifiers}
        self._stamp(self.adapter, getattr(self.adapter, "_mongo_saved_obj", None), ids)
        return result


class MongoRemoteMixin:
    """Mixin implementing create/update/delete against the MongoDB collection for remote DiffSync models.

    Writes are not sent one by one: each is queued on the adapter (`MongoRemoteAdapter.queue_write`), which sends
    them in batches and flushes the rest when the sync completes. The adapter must expose a `client`
    (`MongoSyncClient`), a `nautobot_instance` and, optionally, a `status_metadata` dictionary stamped on every
    write. Subclasses must set `_content_type` to the Nautobot content type label (for example `dcim.manufacturer`).
    """

    _content_type: ClassVar[str] = ""

    @classmethod
    def _metadata(cls, adapter):
        return getattr(adapter, "status_metadata", None)

    @classmethod
    def _upsert(cls, adapter, key, data):
        operation = adapter.client.upsert_operation(
            adapter.nautobot_instance, cls._content_type, key, data, metadata=cls._metadata(adapter)
        )
        adapter.queue_write(cls._content_type, operation)

    @classmethod
    def create(cls, adapter, ids, attrs):
        """Queue a document for this object, reviving a tombstone with the same key if there is one."""
        key = cls.create_unique_id(**ids)
        cls._upsert(adapter, key, {**ids, **attrs})
        return super().create(adapter, ids, attrs)

    def update(self, attrs):
        """Queue the replacement of the stored document with this object's new attribute values."""
        data = {**self.get_identifiers(), **self.get_attrs(), **attrs}
        self._upsert(self.adapter, self.get_unique_id(), data)
        return super().update(attrs)

    def delete(self):
        """Queue the soft-delete of the stored document for this object (turn it into a tombstone)."""
        operation = self.adapter.client.delete_operation(
            self.adapter.nautobot_instance,
            self._content_type,
            self.get_unique_id(),
            metadata=self._metadata(self.adapter),
        )
        self.adapter.queue_write(self._content_type, operation)
        return super().delete()
