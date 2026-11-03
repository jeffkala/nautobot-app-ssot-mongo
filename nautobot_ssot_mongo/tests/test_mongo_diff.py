"""Tests for the customised Diff object and the sync flags of the MongoDB SSoT integration."""

from diffsync.diff import DiffElement
from diffsync.enum import DiffSyncActions, DiffSyncFlags
from nautobot.apps.testing import TestCase

from nautobot_ssot_mongo.diff import CustomOrderingDiff, sync_flags


def _element(obj_type, name, action, parent=None):
    # DiffElement.action is computed from source_attrs / dest_attrs:
    #   CREATE -> source set, dest None
    #   DELETE -> source None, dest set
    #   UPDATE -> both set, at least one shared key differs
    # Deleted locations carry their parent in dest_attrs, as the target adapter loaded them.
    element = DiffElement(obj_type=obj_type, name=name, keys={"name": name})
    if action == DiffSyncActions.CREATE:
        element.source_attrs = {"_marker": "src"}
    elif action == DiffSyncActions.DELETE:
        element.dest_attrs = {"_marker": "dst", "parent__name": parent}
    elif action == DiffSyncActions.UPDATE:
        element.source_attrs = {"_marker": "src"}
        element.dest_attrs = {"_marker": "dst"}
    else:
        raise ValueError(f"Unsupported action {action!r}")
    return element


class CustomOrderingDiffTestCase(TestCase):
    """Creates and updates come first, deletes last, nested locations deepest first."""

    def setUp(self):
        super().setUp()
        self.diff = CustomOrderingDiff()

    def test_yields_non_delete_first_then_deletes(self):
        delete_a = _element("group1", "a", DiffSyncActions.DELETE)
        create_b = _element("group1", "b", DiffSyncActions.CREATE)
        delete_c = _element("group1", "c", DiffSyncActions.DELETE)
        update_d = _element("group1", "d", DiffSyncActions.UPDATE)
        self.diff.children = {"group1": {"a": delete_a, "b": create_b, "c": delete_c, "d": update_d}}

        results = list(self.diff.get_children())

        self.assertIn(create_b, results[:2])
        self.assertIn(update_d, results[:2])
        self.assertEqual(results[-1], delete_a)
        self.assertEqual(results[-2], delete_c)

    def test_location_deletes_sorted_by_depth(self):
        shallow = _element("location", "shallow", DiffSyncActions.DELETE, parent="root")
        deep = _element("location", "deep", DiffSyncActions.DELETE, parent="shallow")
        root = _element("location", "root", DiffSyncActions.DELETE)
        self.diff.children = {"location": {"shallow": shallow, "deep": deep, "root": root}}

        results = list(self.diff.get_children())

        self.assertEqual(results, [deep, shallow, root])

    def test_location_child_is_deleted_before_a_parent_that_sorts_later(self):
        parent = _element("location", "Zulu", DiffSyncActions.DELETE)
        child = _element("location", "Alpha", DiffSyncActions.DELETE, parent="Zulu")
        self.diff.children = {"location": {"Alpha": child, "Zulu": parent}}

        results = list(self.diff.get_children())

        self.assertEqual(results, [child, parent])

    def test_location_depth_counts_only_parents_that_are_also_deleted(self):
        kept_parent_child = _element("location", "orphan", DiffSyncActions.DELETE, parent="stays-in-nautobot")
        root = _element("location", "root", DiffSyncActions.DELETE)
        grandchild = _element("location", "grandchild", DiffSyncActions.DELETE, parent="child")
        child = _element("location", "child", DiffSyncActions.DELETE, parent="root")
        self.diff.children = {
            "location": {"orphan": kept_parent_child, "root": root, "grandchild": grandchild, "child": child}
        }

        results = list(self.diff.get_children())

        self.assertEqual(results[0], grandchild)
        self.assertEqual(results[1], child)
        self.assertEqual(results[-2:], [root, kept_parent_child])

    def test_mixed_groups(self):
        non_delete = _element("group1", "n", DiffSyncActions.UPDATE)
        regular_delete = _element("group1", "d", DiffSyncActions.DELETE)
        loc_with_parent = _element("location", "child", DiffSyncActions.DELETE, parent="root")
        loc_root = _element("location", "root", DiffSyncActions.DELETE)
        self.diff.children = {
            "group1": {"n": non_delete, "d": regular_delete},
            "location": {"child": loc_with_parent, "root": loc_root},
        }

        results = list(self.diff.get_children())

        self.assertEqual(results[0], non_delete)
        # Deferred deletes are reversed; locations were deferred last, so they are yielded first.
        self.assertEqual(results[-3:], [loc_with_parent, loc_root, regular_delete])

    def test_only_deletes_reversed(self):
        c1 = _element("group1", "c1", DiffSyncActions.DELETE)
        c2 = _element("group1", "c2", DiffSyncActions.DELETE)
        c3 = _element("group1", "c3", DiffSyncActions.DELETE)
        self.diff.children = {"group1": {"a": c1, "b": c2, "c": c3}}

        self.assertEqual(list(self.diff.get_children()), [c3, c2, c1])

    def test_empty_groups(self):
        self.diff.children = {}

        self.assertEqual(list(self.diff.get_children()), [])


class SyncFlagsTestCase(TestCase):
    """The job flags continue past failures and log unchanged objects only in debug mode."""

    def test_unchanged_records_are_not_logged_by_default(self):
        flags = sync_flags(debug=False)

        self.assertTrue(flags & DiffSyncFlags.CONTINUE_ON_FAILURE)
        self.assertFalse(flags & DiffSyncFlags.LOG_UNCHANGED_RECORDS)

    def test_debug_logs_unchanged_records(self):
        flags = sync_flags(debug=True)

        self.assertTrue(flags & DiffSyncFlags.CONTINUE_ON_FAILURE)
        self.assertTrue(flags & DiffSyncFlags.LOG_UNCHANGED_RECORDS)
