"""The report pipeline's database-free pieces (#1786, delivery plan item 3).

A send's recorded inputs and report days, its cohort, its per-recipient
render with the Testing redirect, and the larger render bound reserved for
the report purpose. The database twins of the first two are compared with
these in ``database/test_report_pipeline_postgresql``.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    RenderInput,
    ReportRenderInput,
    WeeklyRenderInput,
    validate_render_purpose,
)
from parishkit.stewardship.reports import report_email_fanout
from parishkit.stewardship.reports.report_email_cohort import resolve_cohort
from parishkit.stewardship.reports.report_email_inputs import (
    recorded_inputs,
    report_days,
)

from .test_report_schedule import report

ZONE = "America/New_York"
START, END = date(2026, 10, 5), date(2026, 11, 6)
FACTS = [
    {"block": "campaign_totals", "version": 1, "options": {}},
    {"block": "day_by_day", "version": 1, "options": {"days": 5}},
]


def daily(**values):
    """The converted Daily's report: previous day's end, keyed by report day."""
    return (
        report(as_of="previous_day_end", slot_basis="report_date", blocks=FACTS)
        | values
    )


def test_a_daily_send_reports_its_slot_day_as_of_its_end():
    """One report day, counted at the end of that day (EDT midnight)."""
    captured = datetime(2026, 10, 9, 11, tzinfo=UTC)
    inputs = recorded_inputs(daily(), ["2026-10-08"], captured, START, END, ZONE)
    assert inputs.report_day == inputs.through_date == date(2026, 10, 8)
    assert inputs.as_of == datetime(2026, 10, 9, 4, tzinfo=UTC)
    assert not inputs.held
    assert report_days(daily(), ["2026-10-08"], inputs, START) == (date(2026, 10, 8),)


def test_a_recovery_send_covers_every_missed_day_held_at_the_last():
    """Covered report days before the campaign drop; later ones are held."""
    captured = datetime(2026, 11, 9, 12, tzinfo=UTC)
    covered = ["2026-10-04", "2026-11-05", "2026-11-06", "2026-11-07"]
    inputs = recorded_inputs(daily(), covered, captured, START, END, ZONE)
    assert inputs.report_day == END and inputs.held
    # The end of November 6 is EST midnight, after daylight saving ended.
    assert inputs.as_of == datetime(2026, 11, 7, 5, tzinfo=UTC)
    assert report_days(daily(), covered, inputs, START) == (
        date(2026, 11, 5),
        END,
    )


def test_a_late_response_moves_the_hold_to_its_own_day():
    """After a response Staff entered on November 8, past the November 6 end
    date, the send passes that day as the campaign's report end
    (``send_inputs``, #2298): a send on November 10 reports November 8,
    not the end date, and is still held there; one on November 9 is not
    held at all."""
    late = date(2026, 11, 8)
    captured = datetime(2026, 11, 10, 12, tzinfo=UTC)
    inputs = recorded_inputs(daily(), ["2026-11-09"], captured, START, late, ZONE)
    assert inputs.report_day == inputs.through_date == late and inputs.held
    assert inputs.as_of == datetime(2026, 11, 9, 5, tzinfo=UTC)
    on_time = recorded_inputs(
        daily(),
        ["2026-11-08"],
        datetime(2026, 11, 9, 12, tzinfo=UTC),
        START,
        late,
        ZONE,
    )
    assert on_time.report_day == late and not on_time.held


def test_before_the_campaign_there_is_no_report_day():
    """A send before the first day has ended reports no day and pins no facts."""
    captured = datetime(2026, 10, 5, 11, tzinfo=UTC)
    values = daily(slot_basis="send_date")
    inputs = recorded_inputs(values, ["2026-10-05"], captured, START, END, ZONE)
    assert inputs.report_day is None and inputs.through_date is None
    assert inputs.as_of == datetime(2026, 10, 5, 4, tzinfo=UTC)
    assert report_days(values, ["2026-10-05"], inputs, START) == ()


def test_as_of_when_made_is_the_capture_and_its_own_day():
    """Every figure at the capture instant; the facts end on the capture day."""
    captured = datetime(2026, 10, 12, 12, 5, tzinfo=UTC)
    values = report(as_of="send_time", blocks=FACTS)
    inputs = recorded_inputs(values, [], captured, START, END, ZONE)
    assert inputs.as_of == captured
    assert inputs.report_day == inputs.through_date == date(2026, 10, 12)
    funnel_only = report(as_of="send_time")
    assert (
        recorded_inputs(funnel_only, [], captured, START, END, ZONE).through_date
        is None
    )


def test_the_cohort_expands_groups_once_per_address_by_first_entry():
    """All Staff is Staff without Administrator; ineligible users add nobody."""
    rules = [
        (
            UUID(int=1),
            "Admin@Example.org",
            ["administrator", "ministry_leader", "staff"],
        ),
        (UUID(int=2), "staff@example.org", ["ministry_leader", "staff"]),
        (UUID(int=3), "leader@example.org", ["ministry_leader"]),
        (UUID(int=4), "old@example.org", ["administrator"]),
    ]
    values = report(
        recipients=[
            {"user": str(UUID(int=2))},
            {"user": str(UUID(int=3))},
            {"group": "staff"},
            {"group": "administrators"},
            {"address": "council@example.org"},
            {"address": "staff@example.org"},
        ]
    )
    assert resolve_cohort(values, rules) == [
        {"address": "admin@example.org", "kind": "group", "ref": "administrators"},
        {"address": "council@example.org", "kind": "outside", "ref": None},
        {"address": "old@example.org", "kind": "group", "ref": "administrators"},
        {"address": "staff@example.org", "kind": "user", "ref": str(UUID(int=2))},
    ]


@dataclass
class Snapshot:
    """The parts of a snapshot a recipient's render reads."""

    subject: str = "Annual campaign — weekly report, November 2, 2026"
    html: str = "<p>Report</p>"
    text: str = "Report"


@dataclass
class Email:
    """An email integration's settings."""

    settings: dict


def test_testing_redirects_with_a_first_line_naming_the_recipient(monkeypatch):
    """Production sends as compiled; Testing routes to the Testing recipient."""
    monkeypatch.setattr(
        report_email_fanout, "testing_label", lambda member: "outside address"
    )
    member = {"address": "council@example.org", "kind": "outside", "ref": None}
    email = Email({"sender": "sender@example.org", "reply_to": "reply@example.org"})
    arguments = dict(
        configuration_id=UUID(int=9), email=email, testing="test@example.org"
    )
    production = report_email_fanout.recipient_render(
        Snapshot(), member, mode="production", **arguments
    )
    assert (production.subject, production.html, production.text) == (
        Snapshot.subject,
        Snapshot.html,
        Snapshot.text,
    )
    assert production.routed_recipients == ("council@example.org",)
    assert production.template_id is None
    testing = report_email_fanout.recipient_render(
        Snapshot(), member, mode="testing", **arguments
    )
    line = (
        "TEST — sent to test@example.org instead of outside address "
        "council@example.org."
    )
    assert testing.subject == "[TEST] " + Snapshot.subject
    assert testing.text == line + "\n\nReport"
    assert testing.html == "<h2>TEST</h2><p>" + line + "</p><p>Report</p>"
    assert testing.routed_recipients == ("test@example.org",)
    assert testing.intended_recipients == ("council@example.org",)


def identity(purpose):
    """A campaign delivery identity with ``purpose``."""
    return DeliveryIdentity(
        scope_id=UUID(int=1),
        campaign_id=UUID(int=1),
        semantic_key=UUID(int=2),
        mode="production",
        routing="production",
        purpose=purpose,
    )


@pytest.mark.parametrize(
    ("render", "purpose", "admitted"),
    [
        (ReportRenderInput, "admin_report", True),
        (ReportRenderInput, "weekly_digest", False),
        (WeeklyRenderInput, "admin_report", False),
        (WeeklyRenderInput, "weekly_digest", True),
        (RenderInput, "admin_report", True),
    ],
)
def test_the_report_render_bound_belongs_to_the_report_purpose(
    render, purpose, admitted
):
    """Only a report message may carry a report render's larger body."""
    value = render(
        configuration_id=UUID(int=3),
        template_id=None,
        sender="sender@example.org",
        intended_recipients=("a@example.org",),
        routed_recipients=("a@example.org",),
        subject="Subject",
        html="<p>x</p>",
        text="x",
    )
    if admitted:
        validate_render_purpose(identity(purpose), value)
    else:
        with pytest.raises(ValueError):
            validate_render_purpose(identity(purpose), value)


@dataclass
class Runtime:
    """The runtime fields the report hold reads."""

    mode: str
    current_campaign_id: UUID = UUID(int=7)
    restore_review_required: bool = False


@dataclass
class Campaign:
    """A campaign in a lifecycle state."""

    state: str


@dataclass
class Scope:
    """A scope: runtime and campaign."""

    runtime: Runtime
    campaign: Campaign


@pytest.mark.parametrize(
    ("mode", "state", "held"),
    [
        ("production", "scheduled", False),
        ("production", "active", False),
        ("production", "closed", False),
        ("production", "archived", True),
        ("production", "draft", True),
        ("testing", "draft", False),
        ("testing", "active", True),
    ],
)
def test_reports_send_by_lifecycle_whatever_the_time(mode, state, held):
    """No campaign-date window: before it starts and after it closes too.

    Only the state, the restore review, the work gate and Production's
    catch-up hold a report (decision 9), as stewardship_report_email_scope_v1.
    """
    from parishkit.stewardship.reports.report_email_ownership import report_held

    scope = Scope(Runtime(mode), Campaign(state))
    never = lambda: False  # noqa: E731
    assert report_held(UUID(int=7), scope, gated=never, catching_up=never) is held
    assert report_held(UUID(int=7), scope, gated=lambda: True, catching_up=never)
    assert report_held(UUID(int=8), scope, gated=never, catching_up=never)
    review = Scope(Runtime(mode, restore_review_required=True), Campaign(state))
    assert report_held(UUID(int=7), review, gated=never, catching_up=never)
    caught = report_held(UUID(int=7), scope, gated=never, catching_up=lambda: True)
    assert caught is (held or mode == "production")
