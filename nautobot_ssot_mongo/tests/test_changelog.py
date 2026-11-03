"""Tests for switching change logging off around a block."""

from django.test import SimpleTestCase
from nautobot.extras.signals import change_context_state

from nautobot_ssot_mongo.changelog import changelog_disabled


class ChangelogDisabledTestCase(SimpleTestCase):
    """`changelog_disabled` clears Nautobot's change context for the block and restores it afterwards."""

    def setUp(self):
        super().setUp()
        self.token = change_context_state.set("active-context")
        self.addCleanup(change_context_state.reset, self.token)

    def test_clears_the_change_context_inside_the_block(self):
        with changelog_disabled():
            self.assertIsNone(change_context_state.get())

    def test_restores_the_change_context_on_exit(self):
        with changelog_disabled():
            pass
        self.assertEqual(change_context_state.get(), "active-context")

    def test_restores_the_change_context_after_an_error(self):
        with self.assertRaises(RuntimeError):
            with changelog_disabled():
                raise RuntimeError("sync failed")
        self.assertEqual(change_context_state.get(), "active-context")

    def test_is_a_no_op_without_a_change_context(self):
        token = change_context_state.set(None)
        self.addCleanup(change_context_state.reset, token)
        with changelog_disabled():
            self.assertIsNone(change_context_state.get())
        self.assertIsNone(change_context_state.get())
