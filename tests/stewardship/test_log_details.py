"""System logs says in words what each serious entry recorded (#633)."""

from uuid import UUID

import pytest

from parishkit.stewardship.audit.log_details import duration, explain
from parishkit.stewardship.audit.log_rows import operational_row
from parishkit.stewardship.audit.schemas import FAILURES, ContextKind, sanitize
from parishkit.stewardship.jobs.operational_content import AUTOMATION_KINDS
from parishkit.stewardship.observability import Event

from .log_samples import SAMPLES

SEND = UUID(int=7)


def stored(event):
    """A sample entry's context as the log stores it (JSON scalars only)."""
    schema, context = SAMPLES[event]
    return schema.value, sanitize(schema, dict(context))


@pytest.mark.parametrize(
    "seconds,text",
    [(0, "0 s"), (90, "90 s"), (1020, "17 min"), (7200, "2 h"), (7500, "2 h 5 min")],
)
def test_durations_read_naturally(seconds, text):
    assert duration(seconds) == text


def test_late_scheduled_work_names_the_type_count_and_worst_lateness():
    text = explain(*stored(Event.DUE_WORK_LAG))
    assert text.startswith(
        "3 outbox delivery tasks started late; the longest waited 17 min "
        "(the limit is 90 s)."
    )
    assert "back on time" in text


def test_a_stalled_bulk_send_says_how_far_it_got():
    context = {
        "task_type": "outbox_delivery",
        "definition_id": str(SEND),
        "revision_id": str(SEND),
        "remaining_count": 977,
        "done_count": 23,
        "stall_seconds": 660,
        "elapsed_seconds": 1020,
        "limit_seconds": 600,
        "other_late_count": 2,
    }
    text = explain("due_work", context)
    assert "977 still to send and 23 done" in text
    assert "nothing finished for 11 min" in text
    assert "17 min after it fell due (the limit is 10 min)" in text
    assert "2 other tasks were also late." in text


def test_a_late_campaign_boundary_says_how_late():
    text = explain(*stored(Event.BOUNDARY_LAG))
    assert text.startswith("A campaign start or close ran 2 min after it was due")


def test_a_retried_provider_failure_says_which_call_and_what_next():
    text = explain(*stored(Event.SOURCE_PROVIDER_FAILED))
    assert text.startswith(
        "ParishSoft answered with an error. Details: HTTP status 503."
    )
    assert "tried again in 60 s (attempt 2 of 5 failed)" in text


def test_a_failure_that_gave_up_says_so_with_its_category():
    context = {
        "failure": "due_work_health_check",
        "failure_kind": "database_unavailable",
        "outcome": "failed",
    }
    text = explain("failure", context)
    assert "scheduled-work health check could not run" in text
    assert "category: database unavailable" in text
    assert text.endswith("It will not be retried automatically.")


def test_alert_mail_names_the_provider_answer():
    context = {"failure": "alert_mail", "reason": "smtp_transient", "outcome": "retry"}
    text = explain("failure", context)
    assert "Details: a temporary refusal." in text and text.endswith("tried again.")


def test_every_failure_word_has_a_sentence():
    for failure in FAILURES:
        assert explain("failure", {"failure": failure})


def test_a_recovery_names_the_problem_and_how_long_it_lasted():
    context = {
        "incident_id": str(UUID(int=1)),
        "incident_kind": "scheduler_lag",
        "log_id": str(UUID(int=2)),
        "elapsed_seconds": 1800,
        "count": 3,
    }
    text = explain("recovery", context)
    assert text.startswith("Recovered: “Scheduled work is overdue” has ended after")
    assert "30 min (seen 3 times)." in text
    assert "lists the entry that opened it" in text


def test_a_backup_key_change_ends_without_reading_as_recovered():
    """Its end needs follow-up: "Ended", with the operator's check (#633)."""
    context = {
        "incident_id": str(UUID(int=1)),
        "incident_kind": "backup_key_changed",
        "elapsed_seconds": 86400,
        "count": 1,
    }
    text = explain("recovery", context)
    assert text.startswith("Ended: “Backup encryption key changed” after 24 h")
    assert "Recovered" not in text
    assert "kept copy of the private key opens a new backup" in text


def test_automation_notices_get_no_recovery_sentence():
    """The trigger writes none; an entry from elsewhere is not explained."""
    for kind in AUTOMATION_KINDS:
        assert explain("recovery", {"incident_kind": kind.value}) is None


def test_missing_durations_are_left_out_not_shown_as_zero():
    text = explain("due_work", {"task_type": "outbox_delivery", "count": 2})
    assert text.startswith("2 outbox delivery tasks started late.")
    assert "0 s" not in text
    text = explain("due_work", {"limit_seconds": 90})
    assert text.startswith("Scheduled work was later than its limit")
    assert "(the limit is 90 s)." in text
    send = {"definition_id": str(SEND), "remaining_count": 5, "done_count": 1}
    assert explain("due_work", send).startswith(
        "A bulk Family send fell behind: 5 still to send and 1 done."
    )
    # A done count that was not recorded is left out, not shown as 0.
    del send["done_count"]
    text = explain("due_work", send)
    assert text.startswith("A bulk Family send fell behind: 5 still to send.")
    assert "0 done" not in text


@pytest.mark.parametrize("value", [["provider_status"], {"x": 1}, 7])
def test_non_text_words_get_no_sentence_and_never_raise(value):
    assert explain("failure", {"failure": value}) is None
    text = explain("failure", {"failure": "alert_mail", "reason": value})
    assert text.startswith("An Administrator alert email could not be sent.")


def test_a_serious_entry_without_detail_says_so():
    """An older or refused entry says it was recorded without detail."""
    row = dict(
        id=UUID(int=1),
        created_at=None,
        event="due_work_lag",
        actor_id=None,
        correlation_id=UUID(int=2),
        schema="exception",
        context={},
    )
    assert operational_row(row | {"level": "CRITICAL"})["summary"] == (
        "This entry was recorded without detail."
    )
    assert operational_row(row | {"level": "INFO"})["summary"] is None
    # Fields of an older schema are listed instead of the note.
    older = row | {"level": "WARNING", "schema": "task", "context": {"count": 120}}
    assert operational_row(older)["summary"] is None


@pytest.mark.parametrize(
    "schema,context",
    [
        ("task", {"task_id": str(UUID(int=1)), "count": 120}),
        ("due_work", {}),
        ("failure", {"failure": "not-a-word"}),
        ("recovery", {"incident_kind": "not_a_kind"}),
        ("failure", "text"),
    ],
)
def test_unknown_or_older_contexts_get_no_sentence(schema, context):
    """Entries from before #633 still list their fields, without a sentence."""
    assert explain(schema, context) is None


def test_the_page_row_carries_the_sentence():
    schema, context = stored(Event.SOURCE_PROVIDER_FAILED)
    row = operational_row(
        {
            "id": UUID(int=1),
            "created_at": None,
            "level": "WARNING",
            "event": Event.SOURCE_PROVIDER_FAILED.value,
            "actor_id": None,
            "correlation_id": UUID(int=2),
            "schema": schema,
            "context": context,
        }
    )
    assert row["summary"].startswith("ParishSoft answered with an error.")
    assert ("Status", "503") in row["detail_rows"]
    assert ContextKind.FAILURE.value == schema
