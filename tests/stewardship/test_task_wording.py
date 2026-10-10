"""Background task pages say what a refresh is doing in plain words."""

from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.jobs.task_wording import (
    CHANGE_NAMES,
    REFRESH_LABELS,
    RETRY_REASONS,
    phase_words,
    refresh_label,
    refresh_summary,
    result_text,
)
from parishkit.stewardship.source.snapshots import manifest_changes
from parishkit.stewardship.source.version_models import ENTITY_MODELS


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
    assert refresh_label(task, {"r": "delta"}) == "Quick update"
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
    assert "a quick update a minute or two" in html
    assert "15-minute" not in html
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
        # A quick update that loaded nothing saves, checks and promotes
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


def test_changes_count_added_changed_and_removed_identities():
    """A record changed when it is new, gone or has a different digest (#242)."""
    before = {"family": {"1": "a", "2": "b", "3": "c"}, "fund": {"9": "f"}}
    after = {"family": {"1": "a", "2": "B", "4": "d"}, "fund": {"9": "f"}}
    assert manifest_changes(before, after) == {"family": 3, "fund": 0}
    # A first load compares with nothing, so every record is new.
    assert manifest_changes({}, after) == {"family": 3, "fund": 1}


def test_summary_says_checked_and_changed_by_collection():
    """Changed records are totalled and named per collection, in a fixed order."""
    changes = {"contact": 9, "family": 3, "member": 0, "roster": 1}
    assert refresh_summary(30639, changes) == (
        "Checked 30,639 records from ParishSoft; 13 changed "
        "(3 Families, 9 contacts, 1 Ministry roster entry)."
    )


def test_summary_says_zero_changed_when_nothing_changed():
    """The acceptance case: a refresh with no ParishSoft changes says 0 changed."""
    assert refresh_summary(1, {"family": 0}) == (
        "Checked 1 record from ParishSoft; 0 changed."
    )
    # Before changes were recorded, only the checked count is known.
    assert refresh_summary(2, None) == "Checked 2 records from ParishSoft."


def test_a_finished_refresh_shows_its_result():
    """The succeeded page carries the checked/changed sentence."""
    html = render(
        refresh_task("succeeded", phase="promoting", current=9, total=9),
        refresh_result=refresh_summary(9, {"family": 1}),
    )
    assert "Finished successfully." in html
    assert "Checked 9 records from ParishSoft; 1 changed (1 Family)." in html


def test_every_source_collection_has_a_changed_name():
    """A new source collection must be named here, or its changes go unshown."""
    assert set(CHANGE_NAMES) == set(ENTITY_MODELS)


def test_an_unknown_collection_still_says_how_many_were_checked():
    """Changes naming an unknown collection fall back to the checked count."""
    counts = {"family": 2, "newthing": 3}
    assert result_text(counts, {"changes": {"family": 1}}) == (
        "Checked 5 records from ParishSoft; 1 changed (1 Family)."
    )
    assert result_text(counts, {"changes": {"family": 1, "newthing": 1}}) == (
        "Checked 5 records from ParishSoft."
    )
    assert result_text(counts, {}) == "Checked 5 records from ParishSoft."
    assert result_text({"family": -1}, {}) is None
