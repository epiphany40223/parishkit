"""The Report emails page's row logic, without a database (#1786, item 5).

Plain words for a schedule, recipients in short, a past send's result, a
report's status and the action matrix, and the change entries Pause,
Resume, Delete and Skip are built from (report-emails spec, "Report list",
"Pause and resume", "Command line").
"""

from datetime import UTC, datetime

import pytest

from parishkit.stewardship.reports import report_email_changes as changes
from parishkit.stewardship.reports import report_email_list as rows
from parishkit.stewardship.web.refusals import UserFacingError, UserFacingStale

from .test_report_schedule import NEW_YORK, report

NOW = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
ID = "00000000-0000-4000-8000-000000000001"


def text(segments):
    """The segments' words, with times and dates as the command line writes them."""
    return rows.schedule_text(segments)


def test_a_repeating_schedule_reads_in_plain_words():
    """Its bounds read in words; its time is a send's instant, for the browser."""
    weekly = report(
        schedule={
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [0, 3],
            "from": "campaign_start",
            "until": "2026-11-30",
            "time": "08:00:00",
        }
    )
    segments = rows.schedule_words(weekly, NEW_YORK, NOW)
    assert [kind for kind, _value in segments] == [
        "text",
        "time",
        "text",
        "text",
        "date",
        "text",
    ]
    assert text(segments) == (
        "Every Monday and Thursday at 2026-10-22T12:00:00+00:00, from the "
        "campaign's first day until 2026-11-30."
    )
    daily = report()
    assert text(rows.schedule_words(daily, NEW_YORK, NOW)).endswith(
        ", from the campaign's first day until the campaign's last day."
    )


def test_a_one_time_schedule_names_its_send():
    """Once, at its instant; a paused report still names its time."""
    once = report(schedule={"type": "once", "date": "2026-10-27", "time": "09:30:00"})
    assert text(rows.schedule_words(once, NEW_YORK, NOW)) == (
        "Once, 2026-10-27T13:30:00+00:00"
    )
    paused = report(paused_ranges=[{"start": NOW.isoformat(), "end": None}])
    assert rows.next_send(paused, NEW_YORK, NOW) is None
    assert "at 2026-10-21T11:00:00+00:00" in text(
        rows.schedule_words(paused, NEW_YORK, NOW)
    )
    resumed = rows.resumed_send(paused, NEW_YORK, NOW)
    assert resumed.due_at == datetime(2026, 10, 21, 11, tzinfo=UTC)


def test_recipients_in_short():
    """The first entry by name, then how many others."""
    names = {"u1": "Pat Doe"}
    values = report(
        recipients=[
            {"group": "staff"},
            {"user": "u1"},
            {"address": "council@example.org"},
        ]
    )
    assert str(rows.recipients_short(values, names)) == "All Staff and 2 others"
    single = report(recipients=[{"user": "u1"}])
    assert str(rows.recipients_short(single, names)) == "Pat Doe"
    two = report(recipients=[{"group": "administrators"}, {"user": "u1"}])
    assert str(rows.recipients_short(two, names)) == "All Administrators and 1 other"


@pytest.mark.parametrize(
    ("phase", "task", "skipped", "messages", "result"),
    [
        ("inputs", "running", False, [], rows.IN_PROGRESS),
        ("capture", "failed", False, [], rows.FAILED),
        ("complete", "succeeded", True, [], rows.NOTHING),
        ("complete", "succeeded", False, [], rows.NOBODY),
        ("fanout", "succeeded", False, ["pending", "delivered"], rows.IN_PROGRESS),
        ("complete", "succeeded", False, ["delivery_unknown"], rows.UNCERTAIN),
        (
            "complete",
            "succeeded",
            False,
            ["delivered", "permanent_failure"],
            rows.FAILED,
        ),
        ("complete", "succeeded", False, ["delivered", "cancelled"], rows.SENT),
        ("complete", "succeeded", False, ["cancelled"], rows.NOBODY),
    ],
)
def test_a_sends_result(phase, task, skipped, messages, result):
    """Each send's result, from its phase, task and messages."""
    assert rows._result(phase, task, skipped, messages) == result


def test_status_and_the_action_matrix():
    """The spec's matrix: what each status offers, and why the rest wait."""
    values = report()
    sending = [{"result": rows.IN_PROGRESS}]
    failed = [{"result": rows.FAILED}]
    upcoming = object()
    assert rows.status(values, sending, upcoming) == rows.SENDING
    assert rows.status(values, failed, upcoming) == rows.ATTENTION
    paused = report(paused_ranges=[{"start": NOW.isoformat(), "end": None}])
    assert rows.status(paused, [], None) == rows.PAUSED
    assert rows.status(values, [], None) == rows.DONE
    assert rows.status(values, [{"result": rows.SENT}], upcoming) == rows.UPCOMING
    assert rows.actions(rows.UPCOMING, archived=False, editable=True) == {
        "preview": None,
        "test": None,
        "send_now": None,
        "pause": None,
        "delete": None,
        "edit": None,
    }
    assert rows.actions(rows.PAUSED, archived=False, editable=False) == {
        "preview": None,
        "test": None,
        "send_now": None,
        "resume": None,
        "delete": None,
    }
    done = rows.actions(rows.DONE, archived=False, editable=False)
    assert done["pause"] == rows.NOTHING_AHEAD and done["send_now"] is None
    # Preview reads only, so it is offered even while a send is busy (#2035),
    # and so is Send a test to me, which is never a send (#2036).
    busy = rows.actions(rows.SENDING, archived=False, editable=True)
    assert busy.pop("preview") is None and busy.pop("test") is None
    assert set(busy.values()) == {rows.IN_PROGRESS_REASON}
    stuck = rows.actions(rows.ATTENTION, archived=False, editable=True)
    assert stuck.pop("preview") is None and stuck.pop("test") is None
    assert set(stuck.values()) == {rows.ATTENTION_REASON}
    assert rows.actions(rows.UPCOMING, archived=True, editable=True) == {}


@pytest.mark.parametrize(
    "entries",
    [
        [],
        "pause",
        [{"operation": "edit", "id": ID}],
        [{"operation": "pause", "id": "not-an-id"}],
        [{"operation": "pause", "id": ID, "sends": ["2026-10-21"]}],
        [{"operation": "skip", "id": ID}],
        [{"operation": "skip", "id": ID, "send": "2026-10-21"}],
        [{"operation": "skip", "id": ID, "sends": "2026-10-21"}],
        [{"operation": "skip", "id": ID, "sends": []}],
        [{"operation": "skip", "id": ID, "sends": ["21 Oct"]}],
        [{"operation": "skip", "id": ID, "sends": ["2026-10-21", "2026-10-21"]}],
        [{"operation": "unskip", "id": ID, "sends": ["2026-10-1"]}],
        [{"operation": "skip", "id": ID, "sends": ["2026-10-21"] * 51}],
        [{"operation": "pause", "id": ID}, {"operation": "delete", "id": ID}],
    ],
)
def test_malformed_change_entries_are_unknown_requests(entries):
    """Only a crafted or stale request makes one: 400, nothing built."""
    with pytest.raises(UserFacingError):
        changes.parse_entries(entries)


def test_pause_and_resume_stamp_the_review_instant():
    """Pause opens an interval at the review's clock; Resume closes it."""
    values = report()
    opened = changes._pause(values, NOW)
    assert opened == [{"start": NOW.isoformat(), "end": None}]
    with pytest.raises(UserFacingError):
        changes._pause(report(paused_ranges=opened), NOW)
    later = datetime(2026, 10, 21, tzinfo=UTC)
    closed = changes._resume(report(paused_ranges=opened), later)
    assert closed == [{"start": NOW.isoformat(), "end": later.isoformat()}]
    with pytest.raises(UserFacingError):
        changes._resume(values, later)
    # A clock behind the pause's start cannot close it.
    with pytest.raises(UserFacingStale):
        changes._resume(report(paused_ranges=opened), NOW)


def test_change_rows_name_each_report_and_its_values():
    """Delete removes the record; Pause updates only its intervals."""
    records = {ID: report(name="Daily report")}
    patch, due, described = changes.change_rows(
        [{"operation": "delete", "id": ID}], records, NEW_YORK, NOW
    )
    assert patch == [{"operation": "remove", "section": "schedules", "id": ID}]
    assert due == [] and described[0]["name"] == "Daily report"
    patch, _due, _described = changes.change_rows(
        [{"operation": "pause", "id": ID}], records, NEW_YORK, NOW
    )
    assert patch[0]["values"] == {
        "paused_ranges": [{"start": NOW.isoformat(), "end": None}]
    }
    with pytest.raises(UserFacingError):
        changes.change_rows(
            [{"operation": "pause", "id": "00000000-0000-4000-8000-000000000002"}],
            records,
            NEW_YORK,
            NOW,
        )


def test_a_skip_names_sends_still_ahead(monkeypatch):
    """Each send of the report ahead is skipped and its time signed; one
    refused send refuses the whole skip."""
    monkeypatch.setattr(changes, "send_worked", lambda identifier, slot: False)
    records = {ID: report()}
    patch, due, described = changes.change_rows(
        [{"operation": "skip", "id": ID, "sends": ["2026-10-23", "2026-10-22"]}],
        records,
        NEW_YORK,
        NOW,
    )
    assert patch[0]["values"] == {"skip_dates": ["2026-10-22", "2026-10-23"]}
    assert sorted(due) == [
        datetime(2026, 10, 22, 11, tzinfo=UTC),
        datetime(2026, 10, 23, 11, tzinfo=UTC),
    ]
    assert [send["send"] for send in described[0]["sends"]] == [
        "2026-10-22",
        "2026-10-23",
    ]
    past = [{"operation": "skip", "id": ID, "sends": ["2026-10-22", "2026-10-19"]}]
    with pytest.raises(PermissionError):
        changes.change_rows(past, records, NEW_YORK, NOW)
    beyond = [{"operation": "skip", "id": ID, "sends": ["2026-12-01"]}]
    with pytest.raises(UserFacingError):
        changes.change_rows(beyond, records, NEW_YORK, NOW)
    skipped = {ID: report(skip_dates=["2026-10-22"])}
    with pytest.raises(UserFacingStale):
        changes.change_rows(
            [{"operation": "skip", "id": ID, "sends": ["2026-10-23", "2026-10-22"]}],
            skipped,
            NEW_YORK,
            NOW,
        )


def test_an_unskip_sends_skipped_sends_again(monkeypatch):
    """Unskip removes the skips of sends still ahead, keeping every other
    skip, past ones included; a send not skipped is a stale page (409), a
    past one or one with Production work is refused (403)."""
    worked = set()
    monkeypatch.setattr(changes, "send_worked", lambda identifier, slot: slot in worked)
    records = {ID: report(skip_dates=["2026-10-19", "2026-10-22", "2026-10-23"])}
    patch, due, described = changes.change_rows(
        [{"operation": "unskip", "id": ID, "sends": ["2026-10-22"]}],
        records,
        NEW_YORK,
        NOW,
    )
    assert patch[0]["values"] == {"skip_dates": ["2026-10-19", "2026-10-23"]}
    assert due == [datetime(2026, 10, 22, 11, tzinfo=UTC)]
    assert described[0]["operation"] == "unskip"
    with pytest.raises(UserFacingStale):
        changes.change_rows(
            [{"operation": "unskip", "id": ID, "sends": ["2026-10-24"]}],
            records,
            NEW_YORK,
            NOW,
        )
    with pytest.raises(PermissionError):
        changes.change_rows(
            [{"operation": "unskip", "id": ID, "sends": ["2026-10-19"]}],
            records,
            NEW_YORK,
            NOW,
        )
    worked.add("2026-10-23")
    with pytest.raises(PermissionError):
        changes.change_rows(
            [{"operation": "unskip", "id": ID, "sends": ["2026-10-22", "2026-10-23"]}],
            records,
            NEW_YORK,
            NOW,
        )


def test_a_resume_signs_the_first_send_it_makes():
    """Reviewed at 6:59 for a 7:00 send, Resume signs 7:00, so an Apply at
    7:05 is refused rather than sending late (#1464)."""
    from parishkit.stewardship.accounts.admin_editing import refuse_passed_sends

    seven = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)  # 7:00 AM in New York
    reviewed = datetime(2026, 10, 21, 10, 59, tzinfo=UTC)
    paused = report(paused_ranges=[{"start": "2026-10-20T16:00:00+00:00", "end": None}])
    _patch, due, described = changes.change_rows(
        [{"operation": "resume", "id": ID}], {ID: paused}, NEW_YORK, reviewed
    )
    assert due == [seven] and described[0]["next_send"] == seven
    import parishkit.stewardship.accounts.admin_editing as editing

    original = editing.database_now
    editing.database_now = lambda: datetime(2026, 10, 21, 11, 5, tzinfo=UTC)
    try:
        with pytest.raises(UserFacingStale):
            refuse_passed_sends([moment.isoformat() for moment in due], None)
    finally:
        editing.database_now = original


def test_the_after_campaign_question_names_only_a_send_still_ahead():
    """Every morning until the day after the campaign's last day sends once
    after it ends; once that send is past, there is nothing to ask (A8)."""
    from parishkit.stewardship.reports.report_email_editor import (
        PRESETS,
        after_campaign,
    )

    values = report(schedule={"type": "repeat", **PRESETS["daily"], "time": "07:00:00"})
    late = after_campaign(values, NEW_YORK, NOW)
    # November 7, 2026, 7:00 AM in New York.
    assert late.due_at == datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    assert after_campaign(values, NEW_YORK, late.due_at) is None
