"""Background task pages say what a refresh is doing in plain words."""

from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.jobs.task_wording import (
    REFRESH_LABELS,
    RETRY_REASONS,
    phase_words,
    refresh_label,
)


def test_refresh_phases_read_as_steps_and_others_fall_back():
    """Refresh phases name ParishSoft steps; other tasks get general words."""
    assert phase_words("source_refresh", "fetching") == "Downloading from ParishSoft"
    assert phase_words("source_refresh", "staging") == "Saving the downloaded records"
    assert phase_words("report_export", "fetching") == "Downloading"
    assert phase_words("report_export", "new_phase") == "New phase"


def test_refresh_label_needs_a_refresh_task_and_a_known_kind():
    """Only refresh tasks are labeled, by their request kind."""
    task = {"type": "source_refresh", "root_id": "r"}
    assert refresh_label(task, {"r": "full"}) == REFRESH_LABELS["full"]
    assert refresh_label(task, {"r": "delta"}) == "15-minute update"
    assert refresh_label(task, {}) is None
    assert (
        refresh_label({"type": "report_export", "root_id": "r"}, {"r": "full"}) is None
    )


def refresh_task(state, *, phase="fetching", current=0, total=0, attempt=1):
    """A refresh task row with the fields the status template reads."""
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        name="ParishSoft data refresh",
        type="source_refresh",
        state=state,
        active=True,
        attempt=attempt,
        retry_sequence=0,
        initiator_id=None,
        created_at="2026-09-28T19:32:20+00:00",
        updated_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
        progress=SimpleNamespace(phase=phase, total=total, current=current),
    )


def render(task, **context):
    """Render the task page the way task_page supplies its wording."""
    return render_to_string(
        "stewardship/background-task.html",
        {
            "task": task,
            "is_refresh": True,
            "refresh_label": REFRESH_LABELS["full"],
            "phase_text": phase_words("source_refresh", task.progress.phase),
            **context,
        },
    )


def test_downloading_explains_why_there_is_no_count_yet():
    """The download step names itself instead of 'Waiting for progress details'."""
    html = render(refresh_task("running"))
    assert "Full refresh" in html
    assert "Downloading from ParishSoft…" in html
    assert "This step shows no count yet" in html
    assert "Waiting for progress details" not in html


def test_saving_counts_records_checked_not_changed():
    """The saving step's count is labeled as records checked."""
    html = render(refresh_task("running", phase="staging", current=7187, total=30639))
    assert "Saving the downloaded records…" in html
    assert "7,187 of 30,639 records checked" in html
    assert "not records changed" in html


def test_a_restarted_run_says_why_and_which_attempt():
    """An automatic restart is explained, with the attempt number."""
    html = render(
        refresh_task("running", attempt=2),
        retry_text=RETRY_REASONS["lease_expired"],
    )
    assert "server restarted" in html and "This is attempt 2." in html


def test_waiting_to_retry_says_so():
    """Between attempts the page says it is waiting and how many ran so far."""
    html = render(
        refresh_task("retry_wait"), retry_text=RETRY_REASONS["retryable_failure"]
    )
    assert "Waiting to try again" in html and "Attempts so far: 1." in html


@pytest.mark.parametrize(
    ("phase", "current", "total"),
    [
        # Before the download: retention runs first and reports "starting".
        ("starting", 0, 0),
        # A 15-minute update that loaded nothing saves, checks and promotes
        # with no count at all.
        ("staging", 0, 0),
        ("validating", 0, 0),
        ("promoting", 0, 0),
    ],
)
def test_only_the_download_step_claims_to_be_downloading(phase, current, total):
    """A countless step other than the download gets the general message."""
    html = render(refresh_task("running", phase=phase, current=current, total=total))
    assert "This step shows no count yet" not in html
    assert "Waiting for progress details" in html
