"""The Administrator's coverage decisions on a report email, with fakes (#1872).

An earlier report email that was once unknown and then cancelled can never
settle by itself, so a later send of its report would wait on it for good.
Its Mail message page offers **Count as delivered** and **Count as not
delivered**, recorded as an evidence note plus a coverage decision of the
same id. These tests cover the offered actions, the command's intent and
replay, and the page's notices; the database's guards are covered by
``database/test_report_coverage_decisions_postgresql.py``.
"""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.jobs import delivery_resolution
from parishkit.stewardship.jobs.delivery_reads import (
    COUNT_ACTIONS,
    offered_actions,
    question_choices,
    with_coverage_decisions,
)
from parishkit.stewardship.storage import StaleRecordError


def offers(state, task_state="cancelled", **options):
    """The page's offers for a report email in ``state``."""
    values = dict(campaign_state="active", can_resolve=True, can_retry=False)
    return offered_actions(state, task_state, **(values | options))


def test_a_stuck_report_email_is_offered_both_decisions():
    """Whatever its task's state: neither decision sends."""
    for task_state in (None, "cancelled", "succeeded", "failed"):
        assert offers("cancelled", task_state, countable=True) == [
            "note",
            "count_delivered",
            "count_unsent",
        ]
    # Not countable, it is only an evidence note, as before.
    assert offers("cancelled") == ["note"]
    # Never while the campaign accepts no change, nor once archived.
    assert offers("cancelled", countable=True, can_resolve=False) == []
    assert offers("cancelled", countable=True, campaign_state="archived") == []
    # A message that is not cancelled keeps its usual offers.
    assert offers("delivery_unknown", "failed", countable=True) == [
        "note",
        "accept",
        "confirm_unsent",
    ]


def test_a_decisions_note_is_named_by_its_decision(monkeypatch):
    """Only a report email's notes are looked up; others are left as they are."""
    from parishkit.stewardship.reports import models

    decided, plain = uuid4(), uuid4()
    calls = []

    class Decisions:
        """A fake manager returning one recorded decision."""

        @staticmethod
        def filter(**query):
            """Record the query and answer one decision."""
            calls.append(query)
            return SimpleNamespace(values_list=lambda *names: [(decided, "unsent")])

    monkeypatch.setattr(models.ReportEmailCoverageDecision, "objects", Decisions)
    notes = [dict(id=decided, action="note"), dict(id=plain, action="note")]
    report = dict(purpose="admin_report")
    assert [note["action"] for note in with_coverage_decisions(report, notes)] == [
        "count_unsent",
        "note",
    ]
    assert with_coverage_decisions(dict(purpose="daily_digest"), notes) is notes
    assert len(calls) == 1


class Resolutions:
    """A fake resolution journal: the rows it holds, by id."""

    def __init__(self):
        self.rows = {}

    def filter(self, pk):
        """The row of one id, or none."""
        return SimpleNamespace(first=lambda: self.rows.get(pk))

    def create(self, **fields):
        """Record one row, as the insert would."""
        row = SimpleNamespace(**fields)
        self.rows[fields["id"]] = row
        return row


class Decided:
    """A fake coverage decision table."""

    def __init__(self):
        self.rows = {}

    def filter(self, pk):
        """The decision of one id, as ``values_list(..., flat=True)`` reads it."""
        value = self.rows.get(pk)
        return SimpleNamespace(
            values_list=lambda *names, flat: SimpleNamespace(first=lambda: value)
        )

    def create(self, **fields):
        """Record one decision."""
        self.rows[fields["id"]] = fields["decision"]
        return SimpleNamespace(**fields)


@pytest.fixture
def journal(monkeypatch):
    """resolve_delivery over fakes: a cancelled report email, no live task."""
    resolutions, decided = Resolutions(), Decided()
    message = SimpleNamespace(
        pk=uuid4(),
        purpose="admin_report",
        state="cancelled",
        version=4,
        task_id=uuid4(),
    )
    countable = []
    monkeypatch.setattr(delivery_resolution, "work_transaction", nullcontext)
    monkeypatch.setattr(delivery_resolution, "authorize", lambda store, user: None)
    monkeypatch.setattr(
        delivery_resolution,
        "DeliveryResolution",
        SimpleNamespace(objects=resolutions),
    )
    monkeypatch.setattr(
        delivery_resolution,
        "ReportEmailCoverageDecision",
        SimpleNamespace(objects=decided),
    )
    monkeypatch.setattr(
        delivery_resolution,
        "OutboxMessage",
        SimpleNamespace(
            objects=SimpleNamespace(
                only=lambda *fields: SimpleNamespace(get=lambda **query: message)
            )
        ),
    )
    # The latest task ended any way at all; a decision does not look.
    monkeypatch.setattr(
        delivery_resolution,
        "TaskRun",
        SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **query: SimpleNamespace(
                    order_by=lambda *keys: SimpleNamespace(first=lambda: None)
                )
            )
        ),
    )

    def undecided(row):
        """Whether the page would offer the decisions now."""
        countable.append(row)
        return row["state"] == "cancelled"

    monkeypatch.setattr(delivery_resolution, "coverage_undecided", undecided)
    return SimpleNamespace(
        message=message, resolutions=resolutions, decided=decided, countable=countable
    )


def resolve(journal, action, command=None, **overrides):
    """One resolution of the journal's message."""
    values = dict(
        message_id=journal.message.pk,
        command_id=command or uuid4(),
        expected_version=journal.message.version,
        action=action,
        note="Sent folder shows it went out.",
    )
    return delivery_resolution.resolve_delivery(None, USER, **(values | overrides))


USER = uuid4()
NOW = datetime(2054, 10, 5, 14, tzinfo=UTC)


@pytest.mark.parametrize("action", sorted(COUNT_ACTIONS))
def test_a_decision_is_a_note_and_a_decision_of_the_same_id(journal, action):
    """The journal row is a bare note; the decision carries what was decided."""
    command = uuid4()
    receipt = resolve(journal, action, command)
    assert receipt.action == "note" and receipt.id == command
    assert receipt.previous_task_id is None and receipt.retry_task_id is None
    assert journal.decided.rows == {command: COUNT_ACTIONS[action]}
    # The same command again returns the same note and records nothing new.
    assert resolve(journal, action, command) is receipt
    assert journal.decided.rows == {command: COUNT_ACTIONS[action]}
    # The same key with another decision, or as a bare note, is refused.
    other = "count_unsent" if action == "count_delivered" else "count_delivered"
    for again in (other, "note"):
        with pytest.raises(ValueError, match="already bound"):
            resolve(journal, again, command)


def test_a_decision_is_refused_when_none_is_offered(journal):
    """Not a stuck report email (or already decided): the page's stale answer."""
    journal.message.state = "delivered"
    with pytest.raises(StaleRecordError, match="current delivery state"):
        resolve(journal, "count_delivered")
    assert journal.decided.rows == {} and journal.resolutions.rows == {}
    assert journal.countable[-1]["state"] == "delivered"


def test_sent_mail_links_each_email_a_report_send_waits_on():
    """One link per earlier email, by when it was created; none when nothing waits."""

    first, second = uuid4(), uuid4()
    when = NOW
    html = render_to_string(
        "stewardship/delivery-report-waiting.html",
        {
            "report_waiting": [
                dict(id=first, created_at=when, sends=1),
                dict(id=second, created_at=when, sends=2),
            ]
        },
    )
    assert 'id="delivery-report-waiting"' in html
    assert f"/mail/sent/{first}/" in html and f"/mail/sent/{second}/" in html
    assert html.count("Report email created") == 2
    assert "(2 sends wait on it)" in html and "(1 sends" not in html
    assert (
        render_to_string(
            "stewardship/delivery-report-waiting.html", {"report_waiting": []}
        ).strip()
        == ""
    )


def test_a_stuck_report_email_asks_whether_it_reached_its_recipient():
    """Two answers, never waiting for a task; any other cancelled email asks nothing."""
    actions = offers("cancelled", countable=True)
    rows = question_choices(
        "cancelled", "cancelled", actions, blocked=None, refusal=None, countable=True
    )
    assert rows == [
        dict(choice="reached", action="count_delivered", unavailable=None),
        dict(choice="not_reached", action="count_unsent", unavailable=None),
    ]
    assert (
        question_choices("cancelled", None, ["note"], blocked=None, refusal=None) == []
    )
    # While other campaign work holds the campaign, both stay, unavailable.
    held = question_choices(
        "cancelled", "succeeded", [], blocked="held", refusal=None, countable=True
    )
    assert [row["unavailable"] for row in held] == ["held", "held"]


def page(**context):
    """The Mail message page of a cancelled report email, with ``context``."""
    rows = [
        dict(choice=choice, action=action, unavailable=None, id=uuid4(), reason=None)
        for choice, action in (
            ("reached", "count_delivered"),
            ("not_reached", "count_unsent"),
        )
    ]
    values = dict(
        delivery={
            "id": uuid4(),
            "state": "cancelled",
            "version": 4,
            "purpose": "admin_report",
            "mode": "production",
        },
        outcome="cancelled",
        choices={row["choice"]: row for row in rows},
        choice_rows=rows,
        notes=[{"action": "count_unsent", "created_at": NOW, "evidence_note": "x"}],
    )
    return render_to_string("stewardship/delivery.html", values | context)


def test_the_mail_message_page_asks_and_explains_the_decisions():
    """The question, both answers' forms and help, and the waiting notice."""
    html = page(report_waiting=True, answerable=True)
    assert "Did it reach its recipient?" in html
    for choice, action in (
        ("reached", "count_delivered"),
        ("not_reached", "count_unsent"),
    ):
        assert f'id="answer-{choice}"' in html
        assert f'name="action" value="{action}"' in html
    assert "Count as delivered" in html and "Count as not delivered" in html
    assert "including any before a resend" in html
    assert "A later send of this report is waiting" in html
    assert "Answer the question above." in html
    # The decision's note is named by its decision under Admin evidence.
    assert "Counted as not delivered ·" in html
    # Not countable: nothing is asked, and nothing waits.
    plain = page(choices={}, choice_rows=[])
    assert "nothing is left to do" in plain
    assert "Did it reach its recipient?" not in plain
    assert "is waiting for this email" not in plain


def test_the_notice_says_nothing_needs_doing_when_nothing_is_asked():
    """An email still on its way, which a send waits on, asks nothing yet."""
    html = render_to_string(
        "stewardship/delivery.html",
        {
            "delivery": {"id": uuid4(), "state": "retry_wait", "version": 4},
            "outcome": "still_sending",
            "choices": {},
            "choice_rows": [],
            "report_waiting": True,
        },
    )
    assert "Nothing needs doing yet" in html
    assert "Answer the question above." not in html


def test_the_notice_names_a_question_that_cannot_be_answered_yet():
    """Every answer unavailable (campaign work holds it): no call to answer."""
    html = page(report_waiting=True, answerable=False)
    assert "can't be answered yet" in html
    assert "Answer the question above." not in html
