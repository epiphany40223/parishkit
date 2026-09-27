"""The one ordered wizard page list drives stepper state and Back/Continue links."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.setup_policy import SetupState
from parishkit.stewardship.accounts.setup_wizard import PAGES, build

from .test_setup_forms import VALUES

CAMPAIGN = {
    "source_result": str(uuid4()),
    "campaign": {"modules": ["census", "financial"], "share_options": []},
}


def draft(sections=None, *, state=SetupState.COLLECTING, source=None):
    """A detached stand-in carrying only what the stepper reads."""
    return SimpleNamespace(
        status=SimpleNamespace(state=state, attempt_id=uuid4(), version=3),
        sections=sections or {},
        source_task_id=source,
    )


def states(wizard):
    """Map each shown step key to its rendered state."""
    return {step.key: step.state for step in wizard.steps}


def test_every_page_has_a_unique_key_and_resolvable_url():
    """Templates and redirects can rely on each key naming exactly one page."""
    assert len({page.key for page in PAGES}) == len(PAGES)
    assert all(page.url.startswith("/admin/setup") for page in PAGES)
    assert PAGES[0].url == "/admin/setup/credentials/parishsoft"


@pytest.mark.parametrize("state", [SetupState.FROZEN, SetupState.EXPIRED])
def test_no_stepper_outside_editable_or_loading_attempts(state):
    """Frozen and ended attempts have their own progress pages."""
    assert build(None) is None
    assert build(draft(state=state)) is None


def test_prerequisites_block_later_pages_until_saved():
    """Pages needing the catalog, mail settings or campaign are not linked yet."""
    wizard = build(draft(), "parishsoft")
    shown = states(wizard)
    assert "slack_credential" not in shown and "shares" not in shown
    for key in ("source", "google_workspace", "campaign", "schedules", "preview"):
        assert shown[key] == "blocked", key
        assert wizard.step(key).url is None
        assert wizard.step(key).fix_url.startswith("/admin/setup")
    assert shown["parishsoft"] == "todo"
    assert wizard.step("parishsoft").url is not None
    # Later steps wait for the first unfinished one instead of being skipped to.
    assert shown["parish"] == "locked" and shown["mail"] == "locked"
    assert wizard.step("mail").url is None
    assert wizard.step("mail").fix_url == "/admin/setup/credentials/parishsoft"
    assert wizard.step("source").fix_url == "/admin/setup/credentials/parishsoft"
    assert wizard.previous is None
    assert wizard.next.key == "mail"
    assert wizard.step("parishsoft").current
    assert wizard.completed == 0 and wizard.total == len(wizard.steps)


def test_done_state_next_and_previous_follow_saved_choices():
    """Enabling Slack and financial stewardship inserts their dependent pages."""
    sections = VALUES | {
        "slack": {"enabled": True, "channel_id": "CEXAMPLE"},
        "branding": {"bundle_id": str(uuid4())},
        "campaign": CAMPAIGN,
    }
    credentials = {"parishsoft": True, "google_workspace": True, "slack": False}
    wizard = build(draft(sections, source=uuid4()), "slack", credentials=credentials)
    shown = states(wizard)
    assert shown["slack_credential"] == "todo"  # staged settings are stale
    assert shown["shares"] == "done"
    for key in ("parishsoft", "mail", "testing", "google_workspace", "parish"):
        assert shown[key] == "done", key
    assert shown["source"] == "done" and shown["campaign"] == "done"
    # The stale Slack key is the first unfinished step: completed steps after
    # it stay open, unfinished ones wait for it.
    assert wizard.step("campaign").url is not None
    assert shown["schedules"] == "locked" and wizard.step("schedules").url is None
    assert shown["content"] == "locked"
    assert shown["preview"] == "blocked"
    assert wizard.previous.key == "google_workspace"
    assert wizard.next.key == "slack_credential"
    assert wizard.resume.key == "slack_credential"
    ordered = [step.key for step in wizard.steps]
    assert ordered.index("content") < ordered.index("schedules")
    assert ordered.index("schedules") < ordered.index("preview")


def test_review_opens_when_required_pages_are_complete():
    """Optional content does not block review; tests and finish follow it."""
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "campaign": CAMPAIGN,
        "schedules": {"records": []},
    }
    wizard = build(
        draft(sections, source=uuid4()),
        "schedules",
        credentials={"parishsoft": True, "google_workspace": True},
        tests=frozenset({"mail_test"}),
    )
    shown = states(wizard)
    assert shown["content"] == "todo"
    assert wizard.step("content").url is not None
    assert shown["preview"] == "done" and shown["mail_test"] == "done"
    assert shown["finish"] == "todo"
    assert wizard.next.key == "preview"
    assert wizard.resume.key == "finish"


def test_loading_keeps_completed_steps_but_links_only_the_load():
    """While the load runs other pages redirect, yet finished work still counts."""
    sections = {key: VALUES[key] for key in ("parish", "mail", "testing", "slack")}
    wizard = build(
        draft(sections, state=SetupState.LOADING, source=uuid4()),
        "source",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    shown = states(wizard)
    assert wizard.step("source").state == "todo"
    assert wizard.step("source").status == "In progress"
    assert wizard.step("source").url == "/admin/setup/source"
    for key in ("parishsoft", "mail", "testing", "google_workspace", "slack"):
        assert shown[key] == "done", key
        assert wizard.step(key).status == "Completed"
    assert wizard.completed == 6
    assert {shown[key] for key in ("branding", "access", "campaign")} == {"blocked"}
    assert all(step.url is None for step in wizard.steps if step.key != "source")
    assert wizard.next.url is None


def test_optional_steps_never_hold_later_steps_back():
    """Skipping the optional content step leaves the steps after it open."""
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "campaign": {**CAMPAIGN, "campaign": {"modules": ["census"]}},
    }
    wizard = build(
        draft(sections, source=uuid4()),
        "campaign",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    shown = states(wizard)
    assert shown["content"] == "todo" and wizard.step("content").status == "Optional"
    assert shown["schedules"] == "todo" and wizard.step("schedules").url is not None
    assert wizard.resume.key == "schedules"
    assert shown["preview"] == "blocked"


def test_stepper_marks_current_step_with_text_not_color_alone():
    """The ordered list names each state in text and links only open pages."""
    from django.template.loader import render_to_string

    wizard = build(draft({"mail": VALUES["mail"]}), "testing")
    html = render_to_string("stewardship/setup-wizard.html", {"wizard": wizard})
    assert html.count('aria-current="step"') == 1
    assert '<ol class="setup-steps">' in html
    assert "Current step" in html and "Completed" in html
    assert "Not available yet" in html
    assert 'href="/admin/setup/source"' not in html  # blocked: shown as text
    buttons = render_to_string(
        "stewardship/setup-navigation.html",
        {"wizard": wizard, "submit": "Save and continue"},
    )
    assert 'href="/admin/setup/mail"' in buttons and "Back" in buttons
    assert '<button type="submit">Save and continue</button>' in buttons


def test_every_setup_template_compiles():
    """Template syntax errors surface without PostgreSQL-backed view tests."""
    from pathlib import Path

    from django.template.loader import get_template

    import parishkit.stewardship.accounts as accounts

    folder = Path(accounts.__file__).parent / "templates" / "stewardship"
    names = sorted(path.name for path in folder.glob("setup*.html"))
    assert "setup-source.html" in names and "setup-unavailable.html" in names
    for name in names:
        get_template(f"stewardship/{name}")


def test_time_limit_explanations_match_the_enforced_policy():
    """The plain-language limits state the same durations the server enforces."""
    from datetime import UTC, datetime, timedelta

    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.session_policy import ADMIN_ABSOLUTE
    from parishkit.stewardship.accounts.setup_policy import IDLE_LIMIT, SOURCE_WATCHDOG

    assert timedelta(minutes=30) == IDLE_LIMIT
    assert timedelta(hours=12) == ADMIN_ABSOLUTE
    assert timedelta(hours=2) == SOURCE_WATCHDOG
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    limits = SimpleNamespace(
        idle_at=now + IDLE_LIMIT,
        absolute_at=now + ADMIN_ABSOLUTE,
        watchdog_at=now + SOURCE_WATCHDOG,
    )
    html = render_to_string("stewardship/setup-deadlines.html", {"limits": limits})
    assert "30 minutes" in html and "12 hours" in html and "2 hours" in html
    assert "Idle deadline" not in html and "Absolute session deadline" not in html
    assert '<details class="setup-deadlines">' in html
    assert 'datetime="2026-09-10T12:30:00+00:00"' in html
    assert "data-progress-deadline" not in html
