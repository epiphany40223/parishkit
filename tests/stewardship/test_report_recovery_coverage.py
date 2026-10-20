"""The report fanout's use of earlier accepted messages, with fakes (#1835).

The database decides (``stewardship_report_email_prior_v1``, checked in
``database/test_report_recovery_coverage_postgresql``); these tests check
that the fanout and its task act on each answer: a covered address gets a
covered row and no message, an uncertain one rolls the page back and the
task waits, and a retry after the earlier message settles sends exactly the
addresses that do not have it, once.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.reports import report_email_fanout as fanout
from parishkit.stewardship.reports import report_email_tasks as tasks

COVERED = "covered@example.org"
WAITING = "waiting@example.org"
FRESH = "fresh@example.org"


class Store:
    """The recipient rows and messages one fake fanout commits."""

    def __init__(self):
        self.rows = []
        self.messages = []

    def attempt(self, claim):
        """Run one fanout page as one transaction: an exception keeps nothing."""
        rows, messages = list(self.rows), list(self.messages)
        try:
            return fanout.fanout_report(claim)
        except Exception:
            self.rows, self.messages = rows, messages
            raise


@pytest.fixture
def store(monkeypatch):
    """A fanout whose database is a Store and whose comparison is a dict."""
    store = Store()
    store.coverage = {}
    snapshot = SimpleNamespace(
        pk=uuid4(),
        recipients=[
            {"address": address, "kind": "outside", "ref": None}
            for address in (COVERED, WAITING, FRESH)
        ],
        subject="Report",
        html="<p>Report</p>",
        text="Report",
    )
    row = SimpleNamespace(
        pk=uuid4(),
        phase="fanout",
        definition_id=uuid4(),
        campaign_id=uuid4(),
        mode="production",
    )

    class Recipients:
        """ReportEmailRecipient's manager over the store."""

        @staticmethod
        def filter(snapshot):
            """The handled addresses of the snapshot."""
            return SimpleNamespace(
                values_list=lambda *names, flat: [r["address"] for r in store.rows]
            )

        @staticmethod
        def create(**fields):
            """Commit one recipient row (the guard's admission is the database's)."""
            store.rows.append(fields)

    def message(**options):
        """Record one outbox message; returns its binding."""
        store.messages.append(options["render"].intended_recipients)
        return SimpleNamespace(message_id=uuid4())

    for name, value in {
        "require_work_order": lambda: None,
        "lock_task_claim": lambda claim: claim,
        "_status": lambda claim: claim,
        "bound_preparation": lambda status: row,
        "current_preparation": lambda row: SimpleNamespace(
            runtime=SimpleNamespace(
                active_configuration=SimpleNamespace(pk=uuid4()),
                testing_recipient=None,
            )
        ),
        "ReportEmailSnapshot": SimpleNamespace(
            objects=SimpleNamespace(get=lambda **lookup: snapshot)
        ),
        "ReportEmailRecipient": SimpleNamespace(objects=Recipients),
        "AppliedIntegration": SimpleNamespace(
            objects=SimpleNamespace(
                get=lambda **lookup: SimpleNamespace(
                    settings={"sender": "parish@example.org", "reply_to": None}
                )
            )
        ),
        "claim_event": lambda claim: uuid4(),
        "still_recipient": lambda definition, member: True,
        "prior_coverage": lambda snapshot_id, address: store.coverage[address],
        "create_message": message,
        "checkpoint_preparation": lambda claim, phase: phase,
    }.items():
        monkeypatch.setattr(fanout, name, value)
    return store


def answers(waiting):
    """The comparison's answers, with the waiting address's earlier message."""
    accepted = str(uuid4())
    return {
        COVERED: {"state": "covered", "messages": [accepted]},
        WAITING: waiting,
        FRESH: {"state": "none"},
    }


@pytest.mark.parametrize(
    "outcome,expected",
    [
        ({"state": "covered", "messages": ["m"]}, (FRESH,)),
        ({"state": "none"}, (WAITING, FRESH)),
    ],
    ids=["accepted", "refused"],
)
def test_an_unsettled_earlier_message_waits_then_sends_only_the_owed(
    store, outcome, expected
):
    """Crash between acceptance and record: wait, then follow the real outcome.

    While the waiting address's earlier message is unsettled the page keeps
    nothing. Once reconciliation records that the provider accepted it, the
    address is covered; once it records a refusal, the address is sent.
    Replaying the completed page sends nobody again.
    """
    store.coverage = answers({"state": "uncertain"})
    claim = SimpleNamespace(worker_id=uuid4())
    for _ in range(2):
        with pytest.raises(fanout.CoverageUncertain):
            store.attempt(claim)
        assert store.rows == [] and store.messages == []
    store.coverage[WAITING] = outcome
    assert store.attempt(claim) == "complete"
    assert store.messages == [(address,) for address in expected]
    dispositions = {row["address"]: row["disposition"] for row in store.rows}
    assert dispositions == {
        COVERED: "covered",
        WAITING: "covered" if outcome["state"] == "covered" else "queued",
        FRESH: "queued",
    }
    covered = next(row for row in store.rows if row["address"] == COVERED)
    assert covered["covered_messages"] == store.coverage[COVERED]["messages"]
    assert "outbox_id" not in covered
    assert store.attempt(claim) == "complete"
    assert len(store.rows) == 3 and len(store.messages) == len(expected)


def test_a_revoked_recipient_is_never_compared(store, monkeypatch):
    """The recheck comes first: a removed recipient is revoked, not covered."""
    store.coverage = answers({"state": "uncertain"})
    monkeypatch.setattr(
        fanout, "still_recipient", lambda definition, member: member["address"] == FRESH
    )
    assert store.attempt(SimpleNamespace(worker_id=uuid4())) == "complete"
    assert [row["disposition"] for row in store.rows] == [
        "revoked",
        "revoked",
        "queued",
    ]
    assert store.messages == [(FRESH,)]


@pytest.mark.parametrize(
    "phase,plan", [("fanout", "recovery_retry"), ("capture", "recovery_fail")]
)
def test_a_fanouts_lost_lease_is_always_retried(monkeypatch, phase, plan):
    """Past the last attempt only a fanout is retried, never failed.

    A fanout may have waited many attempts for an earlier message; failing
    it then would leave its cohort unsent.
    """
    monkeypatch.setattr(
        tasks, "bound_preparation", lambda status: SimpleNamespace(phase=phase)
    )
    monkeypatch.setattr(tasks, "disposition", lambda row: None)
    status = SimpleNamespace(state="abandoned", attempt=tasks.FINAL_ATTEMPT + 4)
    assert tasks.recover_report(status).action == plan


class Execution:
    """A fake task execution: one claim, effects and recorded transitions."""

    def __init__(self, attempt):
        self.claim = SimpleNamespace(attempt=attempt)
        self.control = SimpleNamespace(active=True)
        self.transitions = []

    @contextmanager
    def effect(self):
        """An effect with nothing to own."""
        yield

    def transition(self, action, **options):
        """Record a transition."""
        self.transitions.append((action, options))

    def progress(self, *values, phase):
        """Progress is not under test."""


@pytest.mark.parametrize("attempt,delay", [(1, 60), (3, 240), (40, 600)])
def test_the_task_waits_and_never_fails_while_a_message_is_unsettled(
    monkeypatch, attempt, delay
):
    """An uncertain page is a retry, with the usual backoff, at any attempt.

    It is never a permanent failure: the earlier message settles when mail
    dispatch finishes it or an Administrator resolves its unknown outcome.
    """
    row = SimpleNamespace(phase="fanout")

    def uncertain(claim):
        """The fanout found an unsettled earlier message."""
        raise fanout.CoverageUncertain("unsettled")

    for name, value in {
        "lock_task_claim": lambda claim: claim,
        "_status": lambda claim: SimpleNamespace(
            attempt=claim.attempt, phase=tasks.PHASES["fanout"]
        ),
        "bound_preparation": lambda status: row,
        "disposition": lambda row: None,
        "fanout_report": uncertain,
    }.items():
        monkeypatch.setattr(tasks, name, value)
    execution = Execution(attempt)
    tasks._execute(execution, public_origin="https://parish.example")
    assert execution.transitions == [("retryable_failure", {"retry_seconds": delay})]
