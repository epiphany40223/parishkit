"""One stage-box component for every stage-progress display (#1579).

ui-conventions "Stage boxes": every page that follows a long piece of work
through the stages the server reports draws ``components/stage-boxes.html``.
The guard keeps pages from drawing the boxes by hand, and from naming the
work's phase in a live status region without the boxes; the templates it
still allows are listed, each with why, and the list fails on a stale entry
too. The rest checks a ParishSoft refresh's Background task status, the
display #1579 moved onto the component: its boxes by task state, its one
announced sentence and its unchanged wording for other tasks.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.campaign_wizard import task_stages

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENT = "stewardship/components/stage-boxes.html"
# Hand-drawn stage-box markup: the component's list or state classes, in
# either quote style.
HAND_DRAWN = re.compile(
    r"""class=(["'])[^"']*\bstage-(?:boxes|done|active|waiting|failed|operator)\b"""
)
# A live status region that names the work's current phase or walks a list
# of stages, the sign of a stage-progress display (phase_text,
# progress.phase, a loop over stages). A variable merely named ``stage``
# (Go live's step state) is not one, nor is a wizard stepper's "Step 1 of
# 6", which is navigation through the Administrator's own steps.
NAMES_A_STAGE = re.compile(r"\bphase|\{% for \w+ in [\w.]*stages\b")
# Live status templates that name a phase without drawing the boxes, each
# with why. A template leaves this list when it draws the component; the
# list may only shrink.
STILL_WITHOUT_BOXES = frozenset()


def templates():
    """Every Admin and Family template, by its path under the templates dir."""
    return sorted(TEMPLATES.rglob("*.html"))


def test_only_the_component_draws_stage_box_markup():
    """A second hand-drawn box pattern would drift from the shared one."""
    found = {
        path.relative_to(TEMPLATES).as_posix()
        for path in templates()
        if HAND_DRAWN.search(path.read_text())
    }
    assert found == {"components/stage-boxes.html"}


def test_every_live_region_naming_a_stage_draws_the_component():
    """A live status that follows phases draws them as the shared boxes."""
    found = {
        path.relative_to(TEMPLATES).as_posix()
        for path in templates()
        if "data-live-status" in (text := path.read_text())
        and NAMES_A_STAGE.search(text)
        and COMPONENT not in text
    }
    assert found == set(STILL_WITHOUT_BOXES)


@pytest.mark.parametrize(
    "markup",
    [
        '<ol class="stage-boxes">',
        "<li class='step stage-active'>",
        '<li class="stage-failed">',
    ],
)
def test_the_markup_guard_matches_both_quote_styles_and_each_state(markup):
    """Single quotes, and a state class used alone, are hand-drawn too."""
    assert HAND_DRAWN.search(markup)


@pytest.mark.parametrize(
    "text",
    [
        "{{ phase_text }}",
        "{{ task.progress.phase }}",
        "{% for stage in stages %}",
        "{% for step in load.stages %}",
    ],
)
def test_the_live_region_guard_matches_each_way_of_naming_a_stage(text):
    """Each form a hand-written stage line could take is caught."""
    assert NAMES_A_STAGE.search(text)


@pytest.mark.parametrize(
    "text",
    ["{{ stage.stopped_at }}", "{% if stage.total %}", "the next stage", "Step 1 of"],
)
def test_the_live_region_guard_ignores_a_value_merely_named_stage(text):
    """Go live's ``stage`` (its step state) draws no stage list, and a
    wizard stepper's step count is navigation, not work progress."""
    assert not NAMES_A_STAGE.search(text)


def refresh(state, phase="unspecified", current=0, total=0):
    """A refresh run as the Background task read names it."""
    return SimpleNamespace(
        id=UUID(int=1579),
        name="ParishSoft refresh",
        type="source_refresh",
        state=state,
        active=True,
        attempt=1,
        retry_sequence=0,
        initiator_id=None,
        created_at="2026-10-18T02:00:00+00:00",
        updated_at="2026-10-18T02:05:00+00:00",
        heartbeat_at=None,
        lease_expires_at=None,
        progress=SimpleNamespace(
            phase=phase, current=current, total=total, display=None
        ),
    )


def states(task):
    """The five boxes' states for ``task``, in order."""
    as_dict = {"state": task.state, "progress": {"phase": task.progress.phase}}
    return [box["state"] for box in task_stages(as_dict)]


@pytest.mark.parametrize(
    ("state", "phase", "expected"),
    [
        # Start the load is In progress until a worker takes the run (#1916).
        ("queued", "unspecified", ["active", *["waiting"] * 4]),
        ("running", "unspecified", ["active", *["waiting"] * 4]),
        # A worker's claim (phase "starting") ends the wait: Start the load is
        # Done and the download In progress before its first report (#1947).
        ("running", "starting", ["done", "active", *["waiting"] * 3]),
        ("running", "fetching", ["done", "active", *["waiting"] * 3]),
        ("running", "validating", ["done", "done", "done", "active", "waiting"]),
        # Each attempt starts again from the download, after a worker's claim.
        ("retry_wait", "staging", ["active", *["waiting"] * 4]),
        ("abandoned", "promoting", ["active", *["waiting"] * 4]),
        ("succeeded", "promoting", ["done"] * 5),
        ("failed", "staging", ["done", "done", "failed", "waiting", "waiting"]),
        ("failed", "unspecified", ["failed", *["waiting"] * 4]),
        ("cancelled", "validating", ["done", "done", "done", "waiting", "waiting"]),
    ],
)
def test_a_refresh_marks_its_stages_by_the_task_state(state, phase, expected):
    """The boxes say only what the run has done, from its state and phase."""
    assert states(refresh(state, phase)) == expected


def status(task, **extra):
    """The task status region rendered for ``task`` as _task_read builds it."""
    as_dict = {"state": task.state, "progress": {"phase": task.progress.phase}}
    is_refresh = task.type == "source_refresh"
    return render_to_string(
        "stewardship/background-task-status.html",
        {
            "task": task,
            "is_refresh": is_refresh,
            "stages": task_stages(as_dict) if is_refresh else None,
            "phase_text": "Saving the downloaded records",
            **extra,
        },
    )


def opening(html):
    """The opening tag of the task's live status region."""
    return re.search(r'<section[^>]*data-live-status="task"[^>]*>', html).group(0)


def test_a_running_refresh_draws_the_boxes_and_announces_its_stage_once():
    """The boxes replace the Step line, and the region is not aria-live:
    its count changes every poll, so one sentence is announced instead."""
    html = status(refresh("running", "staging", 10, 100))
    assert 'aria-label="Parts of the refresh"' in html
    assert 'data-refresh-stage="fetching" data-stage-state="done"' in html
    assert 'data-refresh-stage="staging" data-stage-state="active"' in html
    assert "Step:" not in html
    tag = opening(html)
    assert "aria-live" not in tag
    assert (
        'data-live-announce="Running. Save the downloaded records: In progress."' in tag
    )
    # The count stays out of the sentence, so a poll that only moves it
    # announces nothing.
    assert "10" not in re.search(r'data-live-announce="[^"]*"', tag).group(0)


@pytest.mark.parametrize(
    ("state", "phase", "sentence"),
    [
        ("queued", "unspecified", "Waiting to start. Start the load: In progress."),
        ("succeeded", "promoting", "Finished successfully."),
        ("failed", "validating", "Failed. Check the new data: Failed."),
        ("cancelled", "validating", "Cancelled."),
    ],
)
def test_each_refresh_state_announces_its_own_sentence(state, phase, sentence):
    """The sentence names the state, and the stage that is running or failed."""
    tag = opening(status(refresh(state, phase)))
    assert f'data-live-announce="{sentence}"' in tag


@pytest.mark.parametrize(
    ("state", "phase", "current", "total"),
    [
        ("queued", "unspecified", 0, 0),
        ("retry_wait", "unspecified", 0, 0),
        # A failed attempt that reached staging keeps its count until the
        # next claim resets it; the queued sentence still wins.
        ("retry_wait", "staging", 10, 40),
    ],
)
def test_a_refresh_waiting_for_a_worker_says_the_queued_sentence(
    state, phase, current, total
):
    """While Start the load is In progress it carries the spinner, and the
    line under the boxes says the one queued sentence every page with a
    queued job says (ui-conventions "Stage boxes", #1916, #1947)."""
    html = status(refresh(state, phase, current, total))
    start = re.search(r'<li[^>]*data-refresh-stage="start"[^>]*>.*?</li>', html, re.S)
    assert 'data-stage-state="active"' in start.group(0)
    assert 'class="spinner' in start.group(0)
    assert html.count('class="spinner') == 2  # the box and the running line
    assert (
        "<p data-refresh-queued>Waiting for the background service to pick up "
        "the job. This can take a minute or two.</p>"
    ) in html
    assert "Waiting for progress details." not in html
    assert "records checked" not in html
    assert "<progress" not in html


def test_a_claimed_refresh_no_longer_says_it_is_queued():
    """Once a worker has the run, Start the load is Done with no spinner and
    the queued sentence gives way to the step's own line."""
    html = status(refresh("running", "fetching"))
    start = re.search(r'<li[^>]*data-refresh-stage="start"[^>]*>.*?</li>', html, re.S)
    assert 'data-stage-state="done"' in start.group(0)
    assert "spinner" not in start.group(0)
    assert "data-refresh-queued" not in html
    assert "Downloading from ParishSoft. This step shows no count yet" in html


def test_every_refresh_state_draws_the_same_five_boxes():
    """Start the load and the four phases are drawn in every state."""
    for state in ("queued", "running", "succeeded", "failed", "cancelled"):
        html = status(refresh(state, "staging"))
        assert html.count("data-refresh-stage=") == 5, state


def test_other_tasks_keep_their_step_line_and_aria_live():
    """A task with no reported stages has no boxes and keeps its wording."""
    task = refresh("running", "rendering")
    task.type = "report_export"
    html = status(task)
    assert "stage-boxes" not in html and "Step:" in html
    assert 'aria-live="polite"' in opening(html)
    assert "data-live-announce" not in opening(html)


@pytest.mark.parametrize(
    ("page", "region"),
    [
        ("background-task.html", '<div id="task-status" data-in-place-region>'),
        ("source-refresh.html", '<div id="refresh-run" data-in-place-region>'),
    ],
)
def test_each_page_drawing_a_refresh_has_its_announcer_before_its_region(page, region):
    """The announcer sits just before the in-place region it speaks for, not
    inside it, so an in-place re-read never replaces it."""
    text = (TEMPLATES / page).read_text()
    announcer = (
        '<p class="visually-hidden" role="status" data-live-announcer="task"></p>'
    )
    assert f"{announcer}\n{region}" in text
