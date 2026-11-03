"""Switch Nautobot's change logging off for part of a job."""

from contextlib import contextmanager

from nautobot.extras.signals import change_context_state


@contextmanager
def changelog_disabled():
    """Run the block without recording an `ObjectChange` for the objects it saves.

    Nautobot's change-log signal handlers read the active change context from a context variable and return at
    once when it is unset. Clearing it for the duration of the block skips the serialisation and insert of an
    `ObjectChange` on every save, and leaves nothing for the job's request context to post-process when the job
    ends. Webhooks, job hooks and events are driven by those entries, so none fire for objects written inside the
    block. The previous context is restored on exit, including after an error.
    """
    token = change_context_state.set(None)
    try:
        yield
    finally:
        change_context_state.reset(token)
