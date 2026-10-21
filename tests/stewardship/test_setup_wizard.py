"""The one ordered wizard page list drives stepper state and Back/Continue links."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.setup_policy import SetupState
from parishkit.stewardship.accounts.setup_wizard import PAGES, build

from .test_setup_forms import VALUES


def draft(
    sections=None,
    *,
    state=SetupState.COLLECTING,
    source=None,
    reviewed=None,
    task_state=None,
):
    """A detached stand-in carrying only what the stepper reads.

    A bound load (``source``) is running while loading and has succeeded
    otherwise, unless ``task_state`` says how it ended.
    """
    if source is not None and task_state is None:
        task_state = "running" if state == SetupState.LOADING else "succeeded"
    return SimpleNamespace(
        status=SimpleNamespace(state=state, attempt_id=uuid4(), version=3),
        sections=sections or {},
        source_task_id=source,
        source_task_state=task_state,
        source_loaded=task_state == "succeeded",
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


def test_the_tests_follow_the_load_and_review_is_last_before_finish():
    """ParishSoft connection and Load parish data are the last data-entry
    pages, then the tests, then Review and Finish setup.

    The load can take minutes, so every other data-entry page comes first, and
    a failed load is one step after the page that fixes its key (#1353). The
    tests come before Review, so Review is the last step before finishing and
    a failed test's fix never passes through Review first (#1476).
    """
    assert [page.key for page in PAGES] == [
        "parish",
        "mail",
        "testing",
        "google_workspace",
        "slack",
        "slack_credential",
        "branding",
        "access",
        "parishsoft",
        "source",
        "mail_test",
        "slack_test",
        "preview",
        "finish",
    ]


@pytest.mark.parametrize("state", [SetupState.FROZEN, SetupState.EXPIRED])
def test_no_stepper_outside_editable_or_loading_attempts(state):
    """Frozen and ended attempts have their own progress pages."""
    assert build(None) is None
    assert build(draft(state=state)) is None


def test_prerequisites_block_later_pages_until_saved():
    """Pages needing the parish data or mail settings are not linked yet."""
    wizard = build(draft(), "parish")
    shown = states(wizard)
    assert "slack_credential" not in shown
    # Setup collects no campaign (#142).
    assert not {"campaign", "content", "shares", "schedules"} & shown.keys()
    for key in ("source", "google_workspace", "preview"):
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
    assert wizard.next.key == "mail"
    assert wizard.step("parish").current
    assert wizard.completed == 0 and wizard.total == len(wizard.steps)


def test_done_state_next_and_previous_follow_saved_choices():
    """Enabling Slack inserts its dependent pages."""
    sections = VALUES | {
        "slack": {"enabled": True, "channel_id": "CEXAMPLE"},
        "branding": {"bundle_id": str(uuid4())},
    }
    credentials = {"parishsoft": True, "google_workspace": True, "slack": False}
    wizard = build(draft(sections, source=uuid4()), "slack", credentials=credentials)
    shown = states(wizard)
    assert shown["slack_credential"] == "todo"  # staged settings are stale
    for key in ("parishsoft", "mail", "testing", "google_workspace", "parish"):
        assert shown[key] == "done", key
    assert shown["source"] == "done" and shown["branding"] == "done"
    # The stale Slack key is the first unfinished step: completed steps after
    # it stay open, unfinished ones wait for it.
    assert wizard.step("source").url is not None
    assert shown["mail_test"] == "blocked" and shown["preview"] == "blocked"
    assert wizard.previous.key == "google_workspace"
    assert wizard.next.key == "slack_credential"
    assert wizard.resume.key == "slack_credential"
    ordered = [step.key for step in wizard.steps]
    assert ordered.index("access") < ordered.index("parishsoft")
    assert ordered.index("parishsoft") + 1 == ordered.index("source")
    assert ordered[ordered.index("source") :] == [
        "source",
        "mail_test",
        "slack_test",
        "preview",
        "finish",
    ]


def final(current, *, tests=frozenset(), reviewed=None, version=3):
    """The stepper of a draft whose data-entry steps and load are all done."""
    sections = VALUES | {"branding": {"bundle_id": str(uuid4())}}
    completed = draft(sections, source=uuid4(), reviewed=reviewed)
    completed.status.version = version
    return build(
        completed,
        current,
        credentials={"parishsoft": True, "google_workspace": True},
        tests=tests,
    )


def test_the_email_test_opens_after_the_load_without_review():
    """Test email follows the load and opens without Review (#1476); Review
    and Finish setup wait for it."""
    wizard = final("source")
    shown = states(wizard)
    assert shown["mail_test"] == "todo"
    assert wizard.step("mail_test").url == "/admin/setup/mail-test"
    assert shown["preview"] == "locked" and shown["finish"] == "locked"
    assert wizard.step("preview").fix_url == "/admin/setup/mail-test"
    # The load is the last data-entry step: its Continue opens Test email.
    assert wizard.previous.key == "parishsoft"
    assert wizard.next.key == "mail_test" and wizard.onward.key == "mail_test"
    assert wizard.resume.key == "mail_test"
    # Back from Test email is Load parish data; its Continue is Review
    # (Slack is off here).
    on_test = final("mail_test", tests=frozenset({"mail_test"}))
    assert on_test.previous.key == "source" and on_test.next.key == "preview"


def test_review_follows_the_tests_and_counts_only_after_them():
    """Review opens once the tests are accepted and is done only when it was
    shown for this draft version; Finish setup follows it."""
    tests = frozenset({"mail_test"})
    wizard = final("mail_test", tests=tests)
    shown = states(wizard)
    assert shown["mail_test"] == "done"
    # Review is open but not completed until the admin has actually opened it.
    assert shown["preview"] == "todo" and wizard.step("preview").url is not None
    assert shown["finish"] == "locked"
    assert wizard.resume.key == "preview"
    reviewed = final("preview", tests=tests, reviewed={"preview": 3})
    assert states(reviewed)["preview"] == "done"
    assert states(reviewed)["finish"] == "todo"
    assert reviewed.resume.key == "finish"
    # Back from Review is the last test; Back from Finish setup is Review.
    assert reviewed.previous.key == "mail_test" and reviewed.next.key == "finish"
    assert final("finish", tests=tests, reviewed={"preview": 3}).previous.key == (
        "preview"
    )
    # A review of an older draft version does not count.
    stale = final("preview", tests=tests, reviewed={"preview": 2})
    assert states(stale)["preview"] == "todo"
    # Nor does a review mark while a test is not accepted for this version:
    # Review counts only with every earlier step, the tests included, done.
    untested = final("preview", reviewed={"preview": 3})
    assert states(untested)["preview"] == "locked"
    assert states(untested)["finish"] == "locked"


@pytest.mark.parametrize("slack", [False, True])
def test_a_later_save_needs_the_tests_and_review_again(slack):
    """A save after the tests and Review bumps the draft version: the tests
    and Review count only for the version they saw (#1476), so all of them
    are required again and the save opens the first of them, never Review."""
    sections = VALUES | {
        "branding": {"bundle_id": str(uuid4())},
        "slack": {"enabled": slack, "channel_id": "C0123456789" if slack else ""},
    }
    credentials = {"parishsoft": True, "google_workspace": True, "slack": True}
    # Tests and Review were done for version 3; the save made version 4, and
    # receipts are read for the current version only, so none is accepted.
    saved = draft(sections, source=uuid4(), reviewed={"preview": 3})
    saved.status.version = 4
    wizard = build(saved, "access", credentials=credentials)
    shown = states(wizard)
    assert shown["mail_test"] == "todo"
    assert {shown["preview"], shown["finish"]} == {"locked"}
    if slack:
        assert shown["slack_test"] == "locked"
    # Rule 8: the save opens the next step not done yet, Test email.
    assert wizard.next.key == "parishsoft"
    assert wizard.onward.key == "mail_test"


def test_a_changed_mail_setting_opens_the_stale_key_then_the_email_test():
    """A changed mail setting makes the staged Google Workspace key stale and
    the email test of the old version no longer counts: saving it opens the
    key step, and saving that opens Test email, never Review (#1476)."""
    sections = VALUES | {"branding": {"bundle_id": str(uuid4())}}
    stale = build(
        draft(sections, source=uuid4(), reviewed={"preview": 2}),
        "mail",
        credentials={"parishsoft": True, "google_workspace": False},
    )
    assert states(stale)["mail_test"] == "blocked"
    assert stale.next.key == "testing" and stale.onward.key == "google_workspace"
    keyed = build(
        draft(sections, source=uuid4(), reviewed={"preview": 2}),
        "google_workspace",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    assert states(keyed)["mail_test"] == "todo"
    assert keyed.next.key == "slack" and keyed.onward.key == "mail_test"


@pytest.mark.parametrize("current", ["access", "branding", "parishsoft", "source"])
def test_onward_opens_the_step_needing_work_when_the_next_one_is_blocked(current):
    """With the Google Workspace key stale, saving a completed later step must
    not open the blocked Test email: it opens the stale key step instead."""
    sections = VALUES | {"branding": {"bundle_id": str(uuid4())}}
    wizard = build(
        draft(sections, source=uuid4(), reviewed={"preview": 2}),
        current,
        credentials={"parishsoft": True, "google_workspace": False},
    )
    assert wizard.step("mail_test").state == "blocked"
    assert wizard.step("mail_test").url is None
    assert wizard.onward.key == "google_workspace"
    assert wizard.onward.url is not None


def test_onward_is_the_next_step_when_every_later_one_is_done():
    """Rule 8: with nothing later left to do, a save opens the next step."""
    tests = frozenset({"mail_test"})
    wizard = final("source", tests=tests, reviewed={"preview": 3})
    later = [step for step in wizard.steps if step.number > wizard.current.number]
    assert all(step.state == "done" for step in later[:-1])
    # Finish setup is never done, so it is the step a save there opens.
    assert wizard.onward.key == "finish"
    first_pass = build(draft({}), "parish")
    assert first_pass.onward.key == first_pass.next.key == "mail"


def test_loading_keeps_completed_steps_but_links_only_the_load():
    """While the load runs other pages redirect, yet finished work still counts.

    The load is the last data-entry step, so every earlier page is complete
    while it runs and only the tests, Review and Finish setup are still to come.
    """
    sections = {
        key: VALUES[key] for key in ("parish", "mail", "testing", "slack", "access")
    } | {"branding": {"bundle_id": str(uuid4())}}
    wizard = build(
        draft(sections, state=SetupState.LOADING, source=uuid4()),
        "source",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    shown = states(wizard)
    assert wizard.step("source").state == "todo"
    assert wizard.step("source").status == "In progress"
    assert wizard.step("source").url == "/admin/setup/source"
    earlier = ("parish", "mail", "testing", "google_workspace", "slack")
    for key in (*earlier, "branding", "access", "parishsoft"):
        assert shown[key] == "done", key
        assert wizard.step(key).status == "Completed"
    assert wizard.completed == 8
    assert {shown[key] for key in ("preview", "mail_test", "finish")} == {"blocked"}
    assert all(step.url is None for step in wizard.steps if step.key != "source")
    assert wizard.next.key == "mail_test" and wizard.next.url is None


def test_a_failed_load_leaves_the_load_to_do_and_every_page_open():
    """A failed load returns setup to collecting (#1353) but is not done."""
    # Parish logos and Administrative access come before the load (#1389).
    sections = {
        key: VALUES[key] for key in ("parish", "mail", "testing", "slack", "access")
    } | {"branding": {"bundle_id": str(uuid4())}}
    wizard = build(
        draft(sections, source=uuid4(), task_state="failed"),
        "source",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    shown = states(wizard)
    assert shown["source"] == "todo"
    assert wizard.step("source").url == "/admin/setup/source"
    assert shown["parishsoft"] == "done"
    assert wizard.step("parishsoft").url == "/admin/setup/credentials/parishsoft"
    # Unlike while loading, the earlier pages are links again.
    for key in ("parish", "mail", "testing", "google_workspace", "slack"):
        assert wizard.step(key).url is not None, key
    # A discarded load (no binding) is not done either.
    voided = build(
        draft(sections),
        "source",
        credentials={"parishsoft": True, "google_workspace": True},
    )
    assert voided.step("source").state == "todo"


def test_stepper_marks_current_step_with_text_not_color_alone():
    """The ordered list names each state in text and links only open pages."""
    from django.template.loader import render_to_string

    wizard = build(draft({"mail": VALUES["mail"]}), "testing")
    html = render_to_string("stewardship/setup-wizard.html", {"wizard": wizard})
    # Once in the step bar and once in each copy of the list (#1402): the
    # disclosure for narrow screens and the open copy for wide ones (#1404).
    assert html.count('aria-current="step"') == 3
    assert '<div class="setup-stepper-wide" role="group"' in html
    assert '<ol class="setup-steps">' in html
    assert "Current step" in html and "Completed" in html
    assert "Not available yet" in html
    assert 'href="/admin/setup/source"' not in html  # blocked: shown as text
    # Compact summary: the current step by number and name, and the count.
    assert "Step 3 of 12:" in html and "<strong>Testing recipient</strong>" in html
    assert "1 of 12 steps completed" in html
    # On a narrow screen the full list is collapsed by default (and stays as
    # the reader left it across an in-place swap, #519); the step bar is a
    # list of links.
    details = (
        '<details class="setup-stepper-list" id="setup-stepper-list" data-keep-open'
    )
    assert details + ">" in html
    assert '<ol class="setup-track" aria-label="Setup steps">' in html
    assert html.count('class="setup-track-') == len(wizard.steps)
    assert (
        "aria-hidden" not in html.split('<ol class="setup-track"')[1].split("</ol>")[0]
    )
    opened = render_to_string(
        "stewardship/setup-wizard.html", {"wizard": wizard, "stepper_open": True}
    )
    assert details + " open>" in opened
    buttons = render_to_string(
        "stewardship/setup-navigation.html",
        {"wizard": wizard, "submit": "Save and continue"},
    )
    assert 'href="/admin/setup/mail"' in buttons and "Back" in buttons
    assert '<button type="submit">Save and continue</button>' in buttons


def track_segments(html):
    """The step bar's ``<a ...>`` opening tags, in order."""
    import re

    track = html.split('<ol class="setup-track"')[1].split("</ol>")[0]
    return re.findall(r"<a [^>]*>", track)


def test_step_bar_links_each_open_step_and_explains_the_others():
    """Open steps are links; a blocked one is focusable, disabled and says why."""
    from django.template.loader import render_to_string

    wizard = build(draft({"mail": VALUES["mail"]}), "testing")
    html = render_to_string("stewardship/setup-wizard.html", {"wizard": wizard})
    segments = track_segments(html)
    assert len(segments) == len(wizard.steps) == 12
    for step, segment in zip(wizard.steps, segments, strict=True):
        if step.url:
            assert f'href="{step.url}"' in segment, step.key
            assert "aria-disabled" not in segment and "tabindex" not in segment
        else:
            assert "href=" not in segment, step.key
            assert 'role="link" aria-disabled="true" tabindex="0"' in segment
            assert "data-step-unavailable" in segment
            assert "Not available yet: " in segment
        assert ('aria-current="step"' in segment) is step.current
    # Parish profile is the first unfinished required step, so this page,
    # reached out of order, is listed but not linked, as in the list.
    assert [step.key for step in wizard.steps if step.url] == ["parish", "mail"]
    # The tooltip and the accessible name say the same; the current step
    # says so, and Back/Next are untouched (the bar saves nothing).
    current = segments[2]
    assert 'data-tip="Step 3 of 12: Testing recipient — Current step — ' in current
    assert (
        '<span class="setup-sr-only">Step 3 of 12: Testing recipient — Current step'
        in html
    )
    assert 'data-tip="Step 2 of 12: Outgoing email settings — Completed"' in html
    assert " title=" not in html.split('<ol class="setup-track"')[1].split("</ol>")[0]


def test_step_bar_tooltips_anchor_by_third_as_the_family_bar_does():
    """First third starts, middle third centres, last third ends (#1402)."""
    from parishkit.stewardship.accounts.templatetags.stewardship import (
        step_tip_side,
    )

    assert [step_tip_side(number, 12) for number in range(1, 13)] == (
        ["start"] * 4 + ["middle"] * 4 + ["end"] * 4
    )
    assert [step_tip_side(number, 7) for number in range(1, 8)] == (
        ["start"] * 3 + ["middle"] * 2 + ["end"] * 2
    )


def test_both_admin_steppers_draw_the_shared_bar_and_list():
    """The setup wizard and Campaign setup share stepper-steps.html (#1402)."""
    from pathlib import Path

    import parishkit.stewardship.accounts as accounts

    folder = Path(accounts.__file__).parent / "templates" / "stewardship"
    for name in ("setup-wizard.html", "campaign-stepper.html"):
        text = (folder / name).read_text(encoding="utf-8")
        assert "{% include 'stewardship/stepper-steps.html'" in text, name
        assert "setup-track" not in text and "setup-steps" not in text, name


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


def test_setup_limits_follow_the_admin_session_and_are_not_listed():
    """Setup idles out with the Admin session; wizard pages no longer list limits."""
    from datetime import timedelta
    from pathlib import Path

    import parishkit.stewardship.accounts as accounts
    from parishkit.stewardship.accounts.session_policy import ADMIN_ABSOLUTE, ADMIN_IDLE
    from parishkit.stewardship.accounts.setup_policy import IDLE_LIMIT, SOURCE_WATCHDOG

    assert timedelta(minutes=60) == IDLE_LIMIT == ADMIN_IDLE
    assert timedelta(hours=12) == ADMIN_ABSOLUTE
    assert timedelta(hours=2) == SOURCE_WATCHDOG
    folder = Path(accounts.__file__).parent / "templates" / "stewardship"
    for path in folder.glob("setup*.html"):
        text = path.read_text()
        assert "12 hours" not in text and "30 minutes" not in text, path.name


SLACK_ON = {"enabled": True, "channel_id": "C0123456789"}


@pytest.mark.parametrize(
    ("credentials", "source", "task_state"),
    [
        # Slack's token is stale, the load succeeded.
        ({"parishsoft": True, "google_workspace": True, "slack": False}, True, None),
        # The Google Workspace key, an earlier step, is stale.
        ({"parishsoft": True, "google_workspace": False, "slack": True}, True, None),
        # No load yet, and the ParishSoft key is missing.
        ({"google_workspace": True, "slack": True}, False, None),
        # The load failed.
        ({"parishsoft": True, "google_workspace": True, "slack": True}, True, "failed"),
    ],
)
def test_the_slack_step_is_linked_from_every_step_once_saved(
    credentials, source, task_state
):
    """Slack notifications (optional) has no prerequisite, so once saved it is
    a link from every step whatever else is stale or not done (#1499): that
    is where Slack is turned off. A later ordering change must not lock it."""
    sections = VALUES | {"branding": {"bundle_id": str(uuid4())}, "slack": SLACK_ON}
    saved = draft(sections, source=uuid4() if source else None, task_state=task_state)
    for page in PAGES:
        wizard = build(saved, page.key, credentials=credentials)
        step = wizard.step("slack")
        assert step.state == "done", page.key
        assert step.url == "/admin/setup/slack", page.key


def test_turning_slack_off_drops_its_steps_and_review_needs_no_slack_test():
    """With Slack off the Slack connection and Test Slack steps are gone, so
    the save opens Test email when the version bump re-requires it, and
    Review (then Finish setup) needs no Slack test (#1499)."""
    sections = VALUES | {"branding": {"bundle_id": str(uuid4())}}
    credentials = {"parishsoft": True, "google_workspace": True, "slack": True}
    off = draft(sections, source=uuid4(), reviewed={"preview": 3})
    off.status.version = 4
    wizard = build(off, "slack", credentials=credentials)
    assert not {"slack_credential", "slack_test"} & states(wizard).keys()
    assert wizard.onward.key == "mail_test"
    # Every other step done: the save opens Review, which counts once shown
    # for this version without a Slack test.
    tested = build(off, "slack", credentials=credentials, tests={"mail_test"})
    assert tested.onward.key == "preview"
    off.reviewed = {"preview": 4}
    reviewed = build(off, "preview", credentials=credentials, tests={"mail_test"})
    assert states(reviewed)["preview"] == "done"
    assert states(reviewed)["finish"] == "todo"
