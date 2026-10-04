"""Shared Stewardship test hygiene."""

import sys
from threading import Thread

import pytest

FAMILY_PROCESS = "parishkit.stewardship.family_delivery_process"
CLOSE_SECONDS = 10


@pytest.fixture(autouse=True)
def _close_family_mail_sessions(monkeypatch):
    """Close every FamilyMailSession a test opened, when the test ends.

    An unclosed session keeps its ``family-mail-reaper`` daemon thread, which
    can kill an idle helper many seconds later and record that timeout into
    whatever a later test has patched ``record_timeout`` with (#542). Closing
    here ends its helpers while this test's patches still apply: this fixture
    takes ``monkeypatch`` first, so it is torn down before the patches are
    undone. A session still running a helper after close() errors the test.

    The class is only hooked when its module is already imported; the test
    modules that build sessions import it at collection, so every session
    they build is tracked. Importing it here for every test would be wasted
    work for the many tests that never touch Family mail.
    """
    module = sys.modules.get(FAMILY_PROCESS)
    if module is None:
        yield
        return
    sessions = []
    cls = module.FamilyMailSession
    original = cls.__init__

    def tracking_init(self, *args, **kwargs):
        """Build the session as usual and remember it for teardown."""
        original(self, *args, **kwargs)
        sessions.append(self)

    monkeypatch.setattr(cls, "__init__", tracking_init)
    yield
    leaked = 0
    # Close every session before judging any, so one leak does not leave the
    # others' helpers running.
    for session in sessions:
        # close() takes the session lock; run it on a daemon thread so a test
        # that left the lock held errors instead of hanging the whole suite.
        # A close still blocked after CLOSE_SECONDS is left behind on its
        # daemon thread; that only happens once this test has already failed.
        closer = Thread(target=session.close, name="test-session-close", daemon=True)
        closer.start()
        closer.join(CLOSE_SECONDS)
        # A helper fake that ignores EOF (and whose wait() does not wait)
        # stays "retiring"; the reaper would kill and record it later.
        if closer.is_alive() or session.process is not None or session.retiring:
            leaked += 1
    if leaked:
        pytest.fail(
            f"{leaked} FamilyMailSession(s) left a helper running after close()."
        )
