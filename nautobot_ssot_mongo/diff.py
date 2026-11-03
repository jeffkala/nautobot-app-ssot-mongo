"""Custom Diff object and sync flags for the MongoDB SSoT integration."""

from diffsync.diff import Diff
from diffsync.enum import DiffSyncActions, DiffSyncFlags


def sync_flags(debug: bool) -> DiffSyncFlags:
    """Return the DiffSync flags for a sync job.

    The sync continues past a single failed object. Unchanged objects are written to the sync log only when
    `debug` is on; otherwise a no-change pull of a large inventory would insert one log row per object for nothing.
    """
    flags = DiffSyncFlags.CONTINUE_ON_FAILURE
    if debug:
        flags |= DiffSyncFlags.LOG_UNCHANGED_RECORDS
    return flags


def _delete_depth(elements):
    """Return a sort key giving each element its nesting depth among the elements queued for deletion.

    The depth is the number of ancestors, by `parent__name`, that are themselves in the delete set. A parent that
    stays in Nautobot does not count, so the deepest elements are the ones whose whole chain of parents is being
    deleted with them.
    """
    parents = {element.keys.get("name"): (element.dest_attrs or {}).get("parent__name") for element in elements}

    def depth(element):
        level = 0
        parent = parents.get(element.keys.get("name"))
        while parent in parents and level < len(parents):
            level += 1
            parent = parents[parent]
        return level

    return depth


class CustomOrderingDiff(Diff):
    """Diff that applies creates and updates first and deletes last, children before parents.

    Locations are protected by their children, so their deletes are ordered deepest first; every other group is
    deleted in the reverse of its creation order.
    """

    def get_children(self):
        """Yield creates and updates group by group, then every delete, deepest location first."""
        deferred_deletes = []

        for group in self.groups():
            group_deletes = []

            for child in self.children[group].values():
                if child.action == DiffSyncActions.DELETE:
                    group_deletes.append(child)
                else:
                    yield child

            if group == "location":
                group_deletes.sort(key=_delete_depth(group_deletes))
            deferred_deletes.extend(group_deletes)

        yield from reversed(deferred_deletes)
