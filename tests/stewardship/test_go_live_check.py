"""The Go live page's Check step: its pure state and step bar (#1585)."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import go_live_check
from parishkit.stewardship.accounts.go_live_check import Progress


@pytest.mark.parametrize(
    "problems, target, verified, state",
    [
        ((), "closed", True, "closed"),
        (("campaign_closed",), "closed", None, "closed"),
        (("full_refresh_required",), "active", True, "not_ready"),
        ((), "scheduled", None, "unchecked"),
        ((), "scheduled", False, "unchecked"),
        ((), "active", True, "passed"),
    ],
)
def test_readiness_state_needs_no_problem_and_a_kept_passing_check(
    problems, target, verified, state
):
    """Only a passing check for these exact inputs offers Start go-live."""
    assert go_live_check.check_state(problems, target, verified) == state


def test_bar_before_start_names_why_later_steps_wait():
    """Four steps, Check current and not done, each later one unavailable."""
    bar = go_live_check.bar("passed")
    assert [str(step.label) for step in bar.steps] == [
        "Check",
        "Prepare",
        "Confirm Production",
        "Activation",
    ]
    assert bar.current.key == "check" and bar.current.url == reverse("admin:go_live")
    assert bar.completed == 0 and bar.total == 4 and str(bar.next.label) == "Prepare"
    for step in bar.steps[1:]:
        assert step.url is None and step.state == "blocked"
        assert str(step.status) == (
            "Not available yet: Start go-live on the Check step first."
        )


@pytest.mark.parametrize("state", ["cleanup_running", "cleanup_complete"])
def test_bar_once_started_opens_the_step_in_progress(state):
    """Check is completed and Prepare opens the Prepare step (#1585 slice 3)."""
    row = SimpleNamespace(pk=UUID(int=7), state=state)
    stage = Progress("started", row)
    assert stage.in_progress_url == reverse("admin:go_live_prepare")
    bar = go_live_check.bar("started")
    assert bar.completed == 1 and bar.steps[0].state == "done"
    assert bar.steps[1].url == stage.in_progress_url
    assert str(bar.steps[1].status) == "In progress"
    assert bar.steps[2].url is None and bar.steps[2].state == "blocked"
    assert str(bar.steps[3].status) == "Not available yet: Confirm Production first."
    assert Progress("idle").in_progress_url is None


def test_bar_on_prepare_opens_confirm_production_once_the_links_are_ready():
    """Prepare is the current step; ready links complete it and open Confirm."""
    waiting = go_live_check.bar("started", current="prepare")
    assert waiting.current.key == "prepare" and waiting.current.number == 2
    assert str(waiting.next.label) == "Confirm Production"
    assert waiting.completed == 1 and waiting.step("confirm").url is None
    ready = go_live_check.bar("started", current="prepare", prepared=True)
    assert ready.completed == 2 and ready.step("prepare").state == "done"
    assert ready.step("confirm").url == "/admin/campaign/go-live/confirmation/"
    # On Confirm Production itself: Activation is next and still waits.
    confirm = go_live_check.bar("started", current="confirm", prepared=True)
    assert confirm.current.number == 3 and str(confirm.next.label) == "Activation"
    assert confirm.step("activate").state == "blocked"
    # Not started: Prepare is current but unavailable, with Check's reason.
    idle = go_live_check.bar("idle", current="prepare")
    assert idle.current.state == "blocked" and idle.current.url is None
    assert str(idle.current.status) == (
        "Not available yet: Start go-live on the Check step first."
    )


@pytest.mark.parametrize("current", ["prepare", "confirm"])
def test_bar_once_production_is_confirmed_completes_three_steps(current):
    """Prepare and Confirm Production after the confirmation: Check, Prepare
    and Confirm Production completed, Activation in progress and linked; no
    step says to start go-live first."""
    shown = SimpleNamespace(state="confirmed", started=False)
    bar = go_live_check.bar(go_live_check.bar_state(shown), current=current)
    assert bar.completed == 3 and bar.total == 4
    assert [step.state for step in bar.steps] == ["done", "done", "done", "todo"]
    assert bar.step("check").url is None
    assert bar.step("activate").url == reverse("admin:production_progress")
    assert str(bar.step("activate").status) == "In progress"
    assert all("Start go-live" not in str(step.status) for step in bar.steps)
    stopped = SimpleNamespace(state="stopped", started=False)
    assert go_live_check.bar_state(stopped) == "idle"


def test_retry_control_is_the_same_on_every_read_of_one_failure():
    """Unchanged between polls, so the live region is not replaced each time.

    A timestamped control would differ on every read and keep the Check
    step's watcher from ever reaching its give-up limit; another task
    version (a retry that failed again) is a different control.
    """
    actor = SimpleNamespace(identity=UUID(int=1))
    row = SimpleNamespace(pk=UUID(int=2))
    preparation = SimpleNamespace(pk=UUID(int=3))
    task = SimpleNamespace(pk=UUID(int=4), version=5)
    first = go_live_check.retry_control(actor, row, preparation, task)
    assert first == go_live_check.retry_control(actor, row, preparation, task)
    again = SimpleNamespace(pk=task.pk, version=6)
    assert first != go_live_check.retry_control(actor, row, preparation, again)


def test_kept_check_is_out_of_date_once_anything_it_checked_changes(monkeypatch):
    """Another campaign, Administrator, inputs or public address: not checked."""
    from parishkit.stewardship.accounts import go_live_commands
    from parishkit.stewardship.deployment import DeploymentProfile

    monkeypatch.setattr(
        go_live_commands,
        "configured_origin",
        lambda: ("https://example.org", DeploymentProfile("test")),
    )
    request = SimpleNamespace(session={})
    inputs = SimpleNamespace(digest="d1")
    assert go_live_check.kept_check(request, "c", "a", inputs) is None
    go_live_check.remember_check(
        request, "c", "a", inputs, True, "https://example.org", "test"
    )
    assert go_live_check.kept_check(request, "c", "a", inputs) is True
    assert go_live_check.kept_check(request, "c", "b", inputs) is None
    assert go_live_check.kept_check(request, "x", "a", inputs) is None
    assert (
        go_live_check.kept_check(request, "c", "a", SimpleNamespace(digest="d2"))
        is None
    )
    go_live_check.remember_check(
        request, "c", "a", inputs, False, "https://example.org", "test"
    )
    assert go_live_check.kept_check(request, "c", "a", inputs) is False
    go_live_check.remember_check(
        request, "c", "a", inputs, True, "https://old.example.org", "test"
    )
    assert go_live_check.kept_check(request, "c", "a", inputs) is None
