"""The setup load progress page names each situation in plain language."""

import pytest

from parishkit.stewardship.accounts.setup_progress_views import (
    RUNNING_SUMMARIES,
    SUMMARIES,
    summary,
)


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
        # Setup itself ended, or a load stopped while setup still says
        # loading: only a new setup helps.
        (
            {"setup_state": "expired", "task_state": "running", "phase": "fetching"},
            "ended",
        ),
        (
            {"setup_state": "loading", "task_state": "failed", "phase": "staging"},
            "ended",
        ),
        # A load that failed for good returned setup to collecting (#1353).
        (
            {"setup_state": "collecting", "task_state": "failed", "phase": "staging"},
            "failed",
        ),
        # A discarded load, and a bound one that succeeded but went stale.
        (
            {
                "setup_state": "collecting",
                "task_state": "succeeded",
                "phase": "validating",
                "bound": False,
                "loaded": False,
            },
            "voided",
        ),
        (
            {
                "setup_state": "collecting",
                "task_state": "failed",
                "phase": "validating",
                "bound": False,
                "loaded": False,
            },
            "voided",
        ),
        (
            {
                "setup_state": "collecting",
                "task_state": "succeeded",
                "phase": "validating",
                "bound": True,
                "loaded": False,
            },
            "voided",
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
    assert [row["key"] for row in saving] == [
        "start",
        "fetching",
        "staging",
        "validating",
    ]
    assert [row["state"] for row in saving] == ["done", "done", "active", "waiting"]
    assert saving[2]["status"] == "In progress"
    finished = phases(observation("validating", active=False), "done")
    assert {row["state"] for row in finished} == {"done"}
    # Queued: Start the load is in progress until a worker picks it up
    # (#1916), then the download is at once, before its first collection.
    queued = phases(observation("unspecified"), "queued")
    assert [row["state"] for row in queued] == ["active"] + ["waiting"] * 3
    starting = phases(observation("starting"), "working")
    assert [row["state"] for row in starting] == [
        "done",
        "active",
        "waiting",
        "waiting",
    ]
    # A load that stopped for good fails the box it stopped in; one that
    # ended before any worker took it fails Start the load.
    failed = phases(observation("staging", active=False), "failed")
    assert [row["state"] for row in failed] == ["done", "done", "failed", "waiting"]
    never = phases(observation("unspecified", active=False), "ended")
    assert [row["state"] for row in never] == ["failed"] + ["waiting"] * 3
    voided = phases(observation("staging", active=False), "voided")
    assert [row["state"] for row in voided] == ["done", "done", "waiting", "waiting"]


@pytest.mark.parametrize("phase", ["fetching", "staging", "done"])
def test_progress_page_shows_the_right_parts_for_each_phase(phase):
    """Downloads list collections; saving shows a determinate bar; done continues."""
    from django.template.loader import render_to_string
    from django.utils.html import escape

    from parishkit.stewardship.accounts.setup_progress_views import _status_context

    progress = observation("staging" if phase == "staging" else "validating")
    if phase == "fetching":
        progress = observation(done=("families",))
    if phase == "staging":
        progress |= {"current": 1200, "total": 9000}
    if phase == "done":
        progress |= {"task_state": "succeeded", "setup_state": "collecting"}
        progress |= {"active": False, "current": 9000, "total": 9000}
    progress |= {
        "started_at": "2026-09-10T11:58:30+00:00",
        "heartbeat_at": "2026-09-10T11:59:57+00:00",
    }
    html = render_to_string(
        "stewardship/setup-source-progress.html",
        _status_context(progress) | {"status_url": "/status?format=status"},
    )
    # The page lists no time limits; hidden instants only bound its polling.
    assert "Inactivity limit" not in html and "12 hours" not in html
    assert "30 minutes" not in html
    assert 'data-progress-deadline="idle_at"' in html
    assert "Families" in html and "Ministry rosters" in html
    records_hidden = 'class="setup-progress reserve-ghost" data-load-records' in html
    assert records_hidden is (phase == "fetching")
    assert ("data-progress-done" in html) is (phase == "done")
    # The live status region polls the passive fragment while the load runs
    # and stops once it is done; the two times tick forward meanwhile.
    assert 'data-live-status="setup-load" data-live-url="/status?format=status"' in html
    assert ("data-live-pending" in html) is (phase != "done")
    assert "1 minute ago" in html and "3 seconds ago" in html
    assert ("data-live-since" in html) is (phase != "done")
    assert f'data-live-announce="{escape(SUMMARIES[summary(progress)])}"' in html
    # No manual check or reload fallback remains.
    assert "Check progress now" not in html and "Reload" not in html
    if phase == "staging":
        assert 'max="9000" value="1200"' in html and "1,200 of 9,000 (13%)" in html
    if phase == "fetching":
        assert "2,687 loaded" in html and "Downloading…" in html
    if phase == "done":
        assert "Continue to the next step" in html


@pytest.mark.parametrize("key", ["failed", "voided", "ended"])
def test_each_ending_says_what_to_do_next(key):
    """A failed load links back to the connection and Load again, a discarded
    one to Load parish data, and only a setup that ended says to cancel; none
    offers Continue or keeps watching (#1353)."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_progress_views import _status_context

    progress = observation("validating", active=False) | {
        "task_state": "succeeded" if key == "voided" else "failed",
        "setup_state": "expired" if key == "ended" else "collecting",
        "bound": key != "voided",
        "loaded": False,
        "started_at": "2026-09-10T11:58:30+00:00",
        "heartbeat_at": "2026-09-10T11:59:57+00:00",
    }
    context = _status_context(progress)
    assert context["status_key"] == key
    html = render_to_string("stewardship/setup-source-progress-status.html", context)
    assert f"data-progress-{key}" in html
    assert f'data-progress-state="{key}"' in html
    assert "data-live-recheck-shown" in html and "data-live-pending" not in html
    assert "Continue to the next step" not in html
    assert ('href="/admin/setup/source"' in html) is (key != "ended")
    assert ('href="/admin/setup/credentials/parishsoft"' in html) is (key == "failed")
    assert ("cancel setup" in html) is (key == "ended")


@pytest.mark.parametrize("phase", ["queued", "fetching", "staging"])
def test_summary_and_records_bar_keep_their_space(phase):
    """The summary sits over invisible copies of every other wording and the
    records bar keeps its space until saving starts, so neither moves the
    stage boxes or the lines below (#1903)."""
    from django.template.loader import render_to_string
    from django.utils.html import escape

    from parishkit.stewardship.accounts.setup_progress_views import _status_context

    progress = observation("staging" if phase == "staging" else "fetching")
    if phase == "queued":
        progress |= {"task_state": "queued", "phase": "unspecified"}
    progress |= {"started_at": None, "heartbeat_at": None}
    context = _status_context(progress)
    assert summary(progress) == phase
    assert sorted(map(str, context["summary_reserve"])) == sorted(
        str(SUMMARIES[key]) for key in RUNNING_SUMMARIES if key != phase
    )
    html = render_to_string(
        "stewardship/setup-source-progress-status.html", context | {"wizard": None}
    )
    for key, text in SUMMARIES.items():
        shown = (
            '<p class="setup-progress-summary" data-progress-summary>'
            f"{escape(text)}</p>"
        )
        ghost = f'<p class="reserve-ghost" aria-hidden="true">{escape(text)}</p>'
        assert (shown in html) is (key == phase)
        # Only the wordings shown while the page still updates are reserved.
        assert (ghost in html) is (key in RUNNING_SUMMARIES and key != phase)
    off = 'class="setup-progress reserve-ghost" data-load-records aria-hidden="true"'
    assert (off in html) is (phase != "staging")


def test_collection_rows_reserve_their_longest_wordings():
    """Each downloaded row's status shares a cell with invisible copies of
    its other wordings, so a row never wraps part-way through (#1903)."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_progress_views import (
        _status_context,
        collection_reserve,
    )

    assert "99,999 loaded" in collection_reserve("families")
    assert "999 of 999 loaded" not in collection_reserve("families")
    assert "999 of 999 loaded" in collection_reserve("ministry_roster")
    html = render_to_string(
        "stewardship/setup-source-progress-status.html",
        _status_context(
            observation("fetching") | {"started_at": None, "heartbeat_at": None}
        )
        | {"wizard": None},
    )
    ghost = '<span class="setup-load-status reserve-ghost" aria-hidden="true">'
    # Seven rows, each with Waiting, Downloading and a count (and one with
    # the rosters' form).
    assert html.count(ghost) == 7 * 3 + 1
    assert f"{ghost}999 of 999 loaded</span>" in html


@pytest.mark.parametrize("key", ["fetching", "done", "failed", "voided"])
def test_continue_sits_in_the_bottom_action_row_from_the_start(key):
    """Continue is in the page's step action row in every state, never in the
    result notice: unavailable with its visible reason until the load is done,
    then a link, with the reason's line kept invisible (#1968)."""
    import re
    from uuid import uuid4

    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_progress_views import _status_context
    from parishkit.stewardship.accounts.setup_wizard import build

    from .test_setup_forms import VALUES
    from .test_setup_wizard import draft

    progress = observation("fetching", active=key == "fetching") | {
        "started_at": "2026-09-10T11:58:30+00:00",
        "heartbeat_at": "2026-09-10T11:59:57+00:00",
    }
    if key != "fetching":
        progress |= {
            "task_state": "failed" if key == "failed" else "succeeded",
            "setup_state": "collecting",
            "bound": key != "voided",
            "loaded": key == "done",
        }
    context = _status_context(progress)
    assert context["status_key"] == key
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "slack": {"enabled": False, "channel_id": ""},
    }
    wizard = build(
        draft(sections, source=uuid4(), task_state="succeeded"),
        "source",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    html = render_to_string(
        "stewardship/setup-source-progress.html",
        context | {"status_url": "/status?format=status", "wizard": wizard},
    )
    # The row is the page's last content, an in-place region the settled
    # region's follow-up re-read swaps; the result notice has no Continue.
    row = html[html.index('<div id="setup-load-actions" data-in-place-region>') :]
    assert html.count("Continue to the next step") == 1
    assert "Continue to the next step" in row
    assert '<a class="setup-back" data-nav-slot="back"' in row
    reason = 'id="setup-load-continue-reason"'
    if key == "done":
        assert re.search(
            r'<a class="setup-continue" data-nav-slot="next" href="[^"]+">'
            r"Continue to the next step",
            row,
        )
        assert reason not in row
        assert '<p class="help reserve-ghost" aria-hidden="true">' in row
    else:
        assert 'class="setup-continue"' not in row
        assert re.search(
            r'<button type="button"[^>]* disabled[^>]*'
            r'aria-describedby="setup-load-continue-reason"',
            row,
        )
        assert reason in row
        waiting = "Continue becomes available once the parish data is loaded."
        assert (waiting in row) is (key == "fetching")
        assert ("The notice above says what to do." in row) is (key != "fetching")


@pytest.mark.parametrize(
    "organizations",
    [None, {"reached": 3, "skipped": 9, "organization": "St. Example Parish (12)"}],
)
def test_a_finished_load_counts_the_organizations_a_key_reaches(organizations):
    """#1554: the result notice says how many organizations the key reaches,
    from the load's own evidence, and nothing for a one-organization key."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_progress_views import _status_context

    progress = observation("validating", active=False) | {
        "task_state": "succeeded",
        "setup_state": "collecting",
        "bound": True,
        "loaded": True,
        "organizations": organizations,
        "started_at": "2026-09-10T11:58:30+00:00",
        "heartbeat_at": "2026-09-10T11:59:57+00:00",
    }
    context = _status_context(progress)
    assert context["status_key"] == "done"
    html = render_to_string("stewardship/setup-source-progress-status.html", context)
    assert "Parish data is loaded" in html
    if organizations is None:
        assert "data-parishsoft-organizations" not in html
    else:
        assert (
            "At the last load from ParishSoft, this key reached 3 organizations."
            in html
        )
        assert "left out by that load: 9." in html
        assert "Only the data of St. Example Parish (12) is loaded;" in html


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (
            "Neighboring Parish 1 (1002)",
            "The parish data of Neighboring Parish 1 (1002) could not be loaded "
            "from ParishSoft.",
        ),
        # No organization chosen (a setup from before #1554).
        (None, "The load did not finish."),
        # The name is ParishSoft's text, so it is escaped.
        ("<b>Parish</b> (7)", "The parish data of &lt;b&gt;Parish&lt;/b&gt; (7)"),
    ],
)
def test_a_failed_load_names_its_organization_and_the_way_back(label, expected):
    """The failed panel says which organization could not be loaded and
    offers choosing another organization or key (#1554)."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_progress_views import _status_context

    progress = observation("validating", active=False) | {
        "task_state": "failed",
        "setup_state": "collecting",
        "bound": True,
        "loaded": False,
        "organization_label": label,
        "started_at": "2026-09-10T11:58:30+00:00",
        "heartbeat_at": "2026-09-10T11:59:57+00:00",
    }
    html = render_to_string(
        "stewardship/setup-source-progress-status.html", _status_context(progress)
    )
    assert expected in html
    assert (
        '<a href="/admin/setup/credentials/parishsoft">Choose another organization '
        "or enter a different key</a>"
    ) in html
