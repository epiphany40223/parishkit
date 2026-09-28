"""The setup load progress page names each situation in plain language."""

import pytest

from parishkit.stewardship.accounts.setup_progress_views import SUMMARIES, summary


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        (
            {"setup_state": "loading", "task_state": "running", "phase": "fetching"},
            "fetching",
        ),
        (
            {"setup_state": "loading", "task_state": "running", "phase": "odd"},
            "working",
        ),
        (
            {"setup_state": "loading", "task_state": "queued", "phase": "unspecified"},
            "queued",
        ),
        (
            {
                "setup_state": "collecting",
                "task_state": "succeeded",
                "phase": "validating",
            },
            "done",
        ),
        (
            {"setup_state": "expired", "task_state": "running", "phase": "fetching"},
            "failed",
        ),
        (
            {"setup_state": "loading", "task_state": "failed", "phase": "staging"},
            "failed",
        ),
    ],
)
def test_progress_summary_names_each_situation(progress, expected):
    """The server and the polling script share one plain-language vocabulary."""
    assert summary(progress) == expected
    assert expected in SUMMARIES


def observation(
    phase="fetching", *, active=True, done=(), finished=None, expected=None
):
    """A decoded progress response with the named collections finished."""
    from parishkit.stewardship.accounts.setup_progress_views import COLLECTIONS

    return {
        "server_now": "2026-09-10T12:00:00+00:00",
        "task_id": "00000000-0000-4000-8000-000000000001",
        "task_state": "running",
        "setup_state": "loading",
        "phase": phase,
        "active": active,
        "current": 0,
        "total": 0,
        "collections": [
            {
                "key": key,
                "count": 2687 if key in done else 0,
                "done": key in done,
                "finished": finished if key == "ministry_roster" else None,
                "expected": expected if key == "ministry_roster" else None,
            }
            for key in COLLECTIONS
        ],
        "idle_at": "2026-09-10T12:30:00+00:00",
        "watchdog_at": "2026-09-10T14:00:00+00:00",
        "absolute_at": "2026-09-11T00:00:00+00:00",
    }


def test_download_marks_done_active_and_waiting_collections_in_words():
    """One collection is in progress; finished ones say how many records came."""
    from parishkit.stewardship.accounts.setup_progress_views import collections

    done = ("families", "family_groups", "members", "member_contactinfos")
    rows = {
        row["key"]: row
        for row in collections(
            observation(done=(*done, "ministry_types"), finished=57, expected=213)
        )
    }
    assert rows["families"]["state"] == "done"
    assert rows["families"]["text"] == "2,687 loaded"
    assert rows["ministry_roster"]["state"] == "active"
    assert rows["ministry_roster"]["text"] == "57 of 213 loaded"
    assert rows["funds"]["state"] == "waiting" and rows["funds"]["text"] == "Waiting"
    first = {row["key"]: row["state"] for row in collections(observation())}
    assert first["families"] == "active" and first["members"] == "waiting"


def test_phases_are_named_in_order_and_complete_when_done():
    """Downloading, saving and checking are shown as done, current or not started."""
    from parishkit.stewardship.accounts.setup_progress_views import phases

    saving = phases(observation("staging"), "staging")
    assert [row["state"] for row in saving] == ["done", "active", "waiting"]
    assert saving[1]["status"] == "In progress"
    finished = phases(observation("validating", active=False), "done")
    assert {row["state"] for row in finished} == {"done"}
    queued = phases(observation("unspecified"), "queued")
    assert {row["state"] for row in queued} == {"waiting"}


@pytest.mark.parametrize("phase", ["fetching", "staging", "done"])
def test_progress_page_shows_the_right_parts_for_each_phase(phase):
    """Downloads list collections; saving shows a determinate bar; done continues."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_progress_views import (
        COLLECTION_TEXT,
        PHASE_STATUS,
        collections,
        phases,
    )

    progress = observation("staging" if phase == "staging" else "validating")
    if phase == "fetching":
        progress = observation(done=("families",))
    if phase == "staging":
        progress |= {"current": 1200, "total": 9000}
    if phase == "done":
        progress |= {"task_state": "succeeded", "setup_state": "collecting"}
        progress |= {"active": False, "current": 9000, "total": 9000}
    status_key = summary(progress)
    html = render_to_string(
        "stewardship/setup-source-progress.html",
        {
            "progress": progress,
            "status_key": status_key,
            "summaries": SUMMARIES,
            "phases": phases(progress, status_key),
            "phase_status": PHASE_STATUS,
            "collections": collections(progress),
            "collection_text": COLLECTION_TEXT,
        },
    )
    # The page lists no time limits; hidden instants only bound its polling.
    assert "Inactivity limit" not in html and "12 hours" not in html
    assert "30 minutes" not in html
    assert 'data-progress-deadline="idle_at"' in html
    assert "Families" in html and "Ministry rosters" in html
    records_hidden = "data-load-records hidden" in html
    assert records_hidden is (phase == "fetching")
    done_hidden = "data-progress-done hidden" in html
    assert done_hidden is (phase != "done")
    if phase == "staging":
        assert 'max="9000" value="1200"' in html and "1,200 of 9,000 (13%)" in html
    if phase == "fetching":
        assert "2,687 loaded" in html and "Downloading…" in html
    if phase == "done":
        assert "Continue to the next step" in html
