"""Shared Stewardship test hygiene."""

import sys
import threading
from threading import Thread
from time import monotonic

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


@pytest.fixture(autouse=True)
def _connection_reuse_off(monkeypatch):
    """Undo a mail-dispatch configure's process-wide keep_connections() (#365).

    Tests that assemble a mail-dispatch runtime turn connection reuse on for
    the whole pytest process; without this, later tests would keep their
    database connections between tasks. Like the fixture above, it touches the
    module only when it is already imported.
    """
    module = sys.modules.get("parishkit.stewardship.jobs.connection_reuse")
    if module is not None:
        monkeypatch.setattr(module, "_enabled", False)
    yield
    module = sys.modules.get("parishkit.stewardship.jobs.connection_reuse")
    if module is not None:
        module._enabled = False


# Background threads that record timeouts through the lazily imported
# ``audit.timeouts.record_timeout`` (#549): a worker's lease renewal
# (jobs/lifetime.py) and a web worker's authentication-health liveness
# observer (runtime_auth_health.py, started by runtime_process.py). One left
# running after its test can write into a later test's process-wide patch.
# Family mail reapers are covered by the session fixture above.
WATCHED_THREADS = frozenset({"stewardship-lease-renewal", "stewardship-auth-health"})
# How long a thread that is already stopping gets to finish after its test.
THREAD_GRACE_SECONDS = 2


def _watched_threads():
    """The live lease-renewal and liveness threads, by object."""
    return {
        thread for thread in threading.enumerate() if thread.name in WATCHED_THREADS
    }


@pytest.fixture(autouse=True)
def _no_leaked_background_threads():
    """Error a test that leaves a lease-renewal or liveness thread running.

    Compares the watched threads alive before and after the test, so a
    thread an earlier test leaked is blamed on that test only. Threads that
    are already stopping get ``THREAD_GRACE_SECONDS`` in total to finish.
    Autouse with no dependencies, this is set up before and torn down after
    the test's own fixtures, so their cleanup has run when it judges.
    """
    before = _watched_threads()
    yield
    deadline = monotonic() + THREAD_GRACE_SECONDS
    leaked = []
    for thread in _watched_threads() - before:
        thread.join(max(0, deadline - monotonic()))
        if thread.is_alive():
            leaked.append(thread.name)
    if leaked:
        pytest.fail(
            f"Test left background thread(s) running: {', '.join(sorted(leaked))}."
        )
