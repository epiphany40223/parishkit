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


# A saved invitation email, which schedules need before they can be set up.
INVITATION = {
    "id": str(uuid4()),
    "values": {"kind": "email", "slot": "initial", "subject": "Welcome"},
}


def draft(sections=None, *, state=SetupState.COLLECTING, source=None, reviewed=None):
    """A detached stand-in carrying only what the stepper reads."""
    return SimpleNamespace(
        status=SimpleNamespace(state=state, attempt_id=uuid4(), version=3),
        sections=sections or {},
        source_task_id=source,
        reviewed=reviewed or {},
    )


def states(wizard):
    """Map each shown step key to its rendered state."""
    return {step.key: step.state for step in wizard.steps}


def test_every_page_has_a_unique_key_and_resolvable_url():
    """Templates and redirects can rely on each key naming exactly one page."""
    assert len({page.key for page in PAGES}) == len(PAGES)
    assert all(page.url.startswith("/admin/setup") for page in PAGES)
    assert PAGES[0].url == "/admin/setup/parish"


@pytest.mark.parametrize("state", [SetupState.FROZEN, SetupState.EXPIRED])
def test_no_stepper_outside_editable_or_loading_attempts(state):
    """Frozen and ended attempts have their own progress pages."""
    assert build(None) is None
    assert build(draft(state=state)) is None


def test_prerequisites_block_later_pages_until_saved():
    """Pages needing the catalog, mail settings or campaign are not linked yet."""
    wizard = build(draft(), "parish")
    shown = states(wizard)
    assert "slack_credential" not in shown and "shares" not in shown
    for key in ("source", "google_workspace", "campaign", "schedules", "preview"):
        assert shown[key] == "blocked", key
        assert wizard.step(key).url is None
        assert wizard.step(key).fix_url.startswith("/admin/setup")
    assert shown["parish"] == "todo"
    assert wizard.step("parish").url is not None
    # Later steps wait for the first unfinished one instead of being skipped to.
    assert shown["parishsoft"] == "locked" and shown["mail"] == "locked"
    assert wizard.step("mail").url is None
    assert wizard.step("mail").fix_url == "/admin/setup/parish"
    assert wizard.step("source").fix_url == "/admin/setup/parish"
    assert wizard.previous is None
    assert wizard.next.key == "parishsoft"
    assert wizard.step("parish").current
    assert wizard.completed == 0 and wizard.total == len(wizard.steps)


def test_done_state_next_and_previous_follow_saved_choices():
    """Enabling Slack and financial stewardship inserts their dependent pages."""
    sections = VALUES | {
        "slack": {"enabled": True, "channel_id": "CEXAMPLE"},
        "branding": {"bundle_id": str(uuid4())},
        "campaign": CAMPAIGN,
    }
    credentials = {"parishsoft": True, "google_workspace": True, "slack": False}
    wizard = build(
        draft(sections, source=uuid4(), reviewed={"shares": []}),
        "slack",
        credentials=credentials,
    )
    shown = states(wizard)
    assert shown["slack_credential"] == "todo"  # staged settings are stale
    assert shown["shares"] == "done"
    for key in ("parishsoft", "mail", "testing", "google_workspace", "parish"):
        assert shown[key] == "done", key
    assert shown["source"] == "done" and shown["campaign"] == "done"
    # The stale Slack key is the first unfinished step: completed steps after
    # it stay open, unfinished ones wait for it.
    assert wizard.step("campaign").url is not None
    assert shown["schedules"] == "blocked" and wizard.step("schedules").url is None
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
    options = {
        "credentials": {"parishsoft": True, "google_workspace": True},
        "tests": frozenset({"mail_test"}),
    }
    shared = {"shares": []}  # the (empty) share options were reviewed
    wizard = build(
        draft(sections, source=uuid4(), reviewed=shared), "schedules", **options
    )
    shown = states(wizard)
    assert shown["content"] == "todo"
    assert wizard.step("content").url is not None
    # Review is open but not completed until the admin has actually opened it.
    assert shown["preview"] == "todo" and wizard.step("preview").url is not None
    assert shown["mail_test"] == "done"
    assert shown["finish"] == "locked"
    assert wizard.next.key == "preview"
    assert wizard.resume.key == "preview"
    reviewed = build(
        draft(sections, source=uuid4(), reviewed=shared | {"preview": 3}),
        "preview",
        **options,
    )
    assert states(reviewed)["preview"] == "done"
    assert states(reviewed)["finish"] == "todo"
    assert reviewed.resume.key == "finish"
    # A review of an older draft version does not count.
    stale = build(
        draft(sections, source=uuid4(), reviewed=shared | {"preview": 2}),
        "preview",
        **options,
    )
    assert states(stale)["preview"] == "todo"


def test_share_options_count_only_after_they_are_reviewed():
    """Default options seeded by the campaign page do not complete the step."""
    options = [{"id": str(uuid4()), "label": "Weekly", "free_text": False}]
    campaign = {
        **CAMPAIGN,
        "campaign": {**CAMPAIGN["campaign"], "share_options": options},
    }
    sections = VALUES | {"branding": {"bundle_id": str(uuid4())}, "campaign": campaign}
    credentials = {"parishsoft": True, "google_workspace": True}

    def shares(reviewed):
        """The shares step's state for the given review marks."""
        built = build(
            draft(sections, source=uuid4(), reviewed=reviewed),
            "campaign",
            credentials=credentials,
        )
        return built.step("shares").state, built

    state, wizard = shares({})
    assert state == "todo" and wizard.resume.key == "shares"
    assert shares({"shares": [options[0]["id"]]})[0] == "done"
    # Options replaced since (financial turned off and on again) need a review.
    assert shares({"shares": [str(uuid4())]})[0] == "todo"


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
        "email_initial": INVITATION,
    }
    wizard = build(
        draft(sections, source=uuid4()),
        "campaign",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    shown = states(wizard)
    assert shown["content"] == "done"  # an email is saved, the rest optional
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
    # Compact summary: the current step by number and name, and the count.
    assert "Step 4 of 15:" in html and "<strong>Testing recipient</strong>" in html
    assert "1 of 15 steps completed" in html
    # The full list is collapsed by default; the decorative track is hidden.
    assert '<details class="setup-stepper-list">' in html
    assert html.count('<span class="setup-track-') == len(wizard.steps)
    assert 'class="setup-track" aria-hidden="true"' in html
    opened = render_to_string(
        "stewardship/setup-wizard.html", {"wizard": wizard, "stepper_open": True}
    )
    assert '<details class="setup-stepper-list" open>' in opened
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


def test_schedules_wait_for_an_email_they_can_send():
    """With no saved invitation or reminder there is nothing to schedule."""
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "campaign": {**CAMPAIGN, "campaign": {"modules": ["census"]}},
    }
    credentials = {"parishsoft": True, "google_workspace": True}
    wizard = build(draft(sections, source=uuid4()), "campaign", credentials=credentials)
    step = wizard.step("schedules")
    assert step.state == "blocked" and step.url is None
    assert step.fix_url == "/admin/setup/content"
    assert "invitation or reminder email" in str(step.status)
    ready = build(
        draft(sections | {"email_initial": INVITATION}, source=uuid4()),
        "campaign",
        credentials=credentials,
    )
    assert ready.step("schedules").state == "todo"
    # A page email is not something a schedule can send.
    page = {"id": str(uuid4()), "values": {"kind": "page", "slot": "welcome"}}
    other = build(
        draft(sections | {"page_welcome": page}, source=uuid4()),
        "campaign",
        credentials=credentials,
    )
    assert other.step("schedules").state == "blocked"
