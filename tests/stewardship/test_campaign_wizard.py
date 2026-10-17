"""The Campaign setup stepper's steps, completion reading and template (#142).

The admin-portal spec's "Create the campaign" section, Guided steps: the
stepper runs over the existing campaign pages while the current campaign is
a Testing draft, completes a page step once a change made there is applied
or its Continue was pressed with nothing changed (#1404), and its Back and
Continue never save anything on the page.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard as wizard
from parishkit.stewardship.accounts.admin_navigation import PAGES as NAMES

CAMPAIGN = str(uuid4())
TEST_URL = "/admin/campaign/content/test/00000000-0000-0000-0000-000000000001/"


def campaign(state="draft"):
    """A stand-in campaign row with only what ``shown`` reads."""
    return SimpleNamespace(state=state)


def keys(stepper):
    """Each shown step key, in order."""
    return [step.key for step in stepper.steps]


def test_steps_are_the_campaign_pages_under_their_own_names():
    """A step is named after the page it opens, and opens that page."""
    stepper = wizard.build(True, "settings", done={"settings"}, mail_test_url=TEST_URL)
    assert [str(step.label) for step in stepper.steps] == [
        "Campaign settings",
        "Load ParishSoft data",
        "Share options",
        "Pages and emails",
        "Dates and mail schedules",
        "Campaign images",
        "Preview and test email",
        "Go-live readiness",
    ]
    for step, page in zip(stepper.steps, wizard.PAGES, strict=True):
        # Load ParishSoft data has its own name for Refresh from ParishSoft.
        if page.key != "load":
            assert step.label == NAMES[page.route].label
        expected = (
            TEST_URL if page.key == "mail_test" else reverse(f"admin:{page.route}")
        )
        assert step.url == expected


def test_share_options_applies_only_with_financial_stewardship():
    """Share options is a step only when the campaign's modules use it."""
    stepper = wizard.build(False, "content", done=set(), mail_test_url=TEST_URL)
    assert "share_options" not in keys(stepper)
    assert stepper.previous.key == "load"
    assert stepper.total == 7


def test_back_and_next_are_the_neighbouring_steps():
    """Back and Next link the pages either side; the ends have only one."""
    first = wizard.build(True, "settings", done=set(), mail_test_url=TEST_URL)
    assert first.previous is None and first.next.key == "load"
    load = wizard.build(True, "load", done=set(), mail_test_url=TEST_URL)
    assert (load.previous.key, load.next.key) == ("settings", "share_options")
    middle = wizard.build(True, "schedules", done=set(), mail_test_url=TEST_URL)
    assert (middle.previous.key, middle.next.key) == ("content", "images")
    last = wizard.build(True, "go_live", done=set(), mail_test_url=TEST_URL)
    assert last.previous.key == "mail_test" and last.next is None
    assert last.current.key == "go_live"


def test_states_and_counts():
    """Completed steps read Completed; every other step stays a link."""
    stepper = wizard.build(
        True, "images", done={"settings", "content"}, mail_test_url=TEST_URL
    )
    states = {step.key: step.state for step in stepper.steps}
    assert states["settings"] == states["content"] == "done"
    assert {states[key] for key in states if key not in {"settings", "content"}} == {
        "todo"
    }
    assert all(step.url for step in stepper.steps)
    assert stepper.completed == 2


def test_the_test_step_waits_for_an_invitation():
    """With no invitation revision the test step is listed but not linked."""
    stepper = wizard.build(True, "images", done=set(), mail_test_url=None)
    step = next(step for step in stepper.steps if step.key == "mail_test")
    assert step.url is None and step.state == "blocked"
    assert "invitation" in str(step.status)
    # Back and Next skip it, so neither dead-ends on a step that cannot open.
    assert stepper.next.key == "go_live"
    last = wizard.build(True, "go_live", done=set(), mail_test_url=None)
    assert last.previous.key == "images"


@pytest.mark.parametrize(
    ("row", "mode", "admin", "go_live", "expected"),
    [
        (campaign(), "testing", True, False, True),
        (campaign(), "testing", False, False, False),
        (campaign(), "testing", True, True, False),
        (campaign(), "production", True, False, False),
        (campaign("active"), "testing", True, False, False),
        (campaign("scheduled"), "testing", True, False, False),
        (None, "testing", True, False, False),
    ],
)
def test_shown_only_for_an_administrator_preparing_a_testing_draft(
    row, mode, admin, go_live, expected
):
    """A started go-live, a live campaign or another role never sees it."""
    assert wizard.shown(row, mode, admin=admin, go_live=go_live) is expected


def update(**values):
    """The campaign update a settings page records."""
    return {
        "operation": "update",
        "section": "campaigns",
        "id": CAMPAIGN,
        "values": values,
    }


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ([update(share_options=[{"amount": 5}])], {"share_options"}),
        ([update(artwork={"images": {"banner": "x"}})], {"images"}),
        ([update(artwork=None)], {"images"}),
        # Campaign settings' own changes (and its Ministries) are no step's.
        ([update(name="New")], set()),
        ([update(ministry_duids=[1, 2])], set()),
        # Turning Financial stewardship on or off on Campaign settings writes
        # (default) share options with the modules: not Share options' change.
        ([update(modules=["financial"], share_options=[{"amount": 5}])], set()),
        ([update(modules=["ministry"], share_options=[])], set()),
        ([update(name="New", artwork=None)], set()),
        # Dates and mail schedules' dates or time zone alone.
        ([update(start_date="2054-10-01", end_date="2054-11-01")], {"schedules"}),
        ([update(timezone="America/Chicago")], {"schedules"}),
        ([update(end_date="2054-11-02", name="New")], set()),
        # Pages and emails: a new revision, re-pointing the schedules that
        # send it, or deleting unused emails.
        (
            [
                {"operation": "remove", "section": "content", "id": "a"},
                {"operation": "add", "section": "content", "id": "b", "values": {}},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": "s",
                    "values": {"template_version": "b", "subject": "Hi"},
                },
            ],
            {"content"},
        ),
        ([{"operation": "remove", "section": "content", "id": "a"}], {"content"}),
        # Dates and mail schedules, even when it adds the email it sends.
        (
            [
                {"operation": "add", "section": "content", "id": "b", "values": {}},
                {
                    "operation": "add",
                    "section": "schedules",
                    "id": "s",
                    "values": {"kind": "initial", "template_version": "b"},
                },
            ],
            {"schedules"},
        ),
        (
            [
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": "s",
                    "values": {"kind": "reminder", "template_version": "b"},
                }
            ],
            {"schedules"},
        ),
        ([{"operation": "remove", "section": "schedules", "id": "s"}], {"schedules"}),
        # Another campaign's update, and unexpected shapes, complete nothing.
        (
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": "other",
                    "values": {"share_options": []},
                }
            ],
            set(),
        ),
        (None, set()),
        (["junk", {"section": "content"}], {"content"}),
    ],
)
def test_step_keys_recognize_each_pages_change(patch, expected):
    """Each settings page's change completes its own step, and only that."""
    assert wizard.step_keys(patch, CAMPAIGN) == expected


def test_the_creation_request_completes_no_step():
    """Create the campaign's defaults (text, share options) are not choices."""
    patch = [
        {"operation": "add", "section": "campaigns", "id": CAMPAIGN, "values": {}},
        {"operation": "add", "section": "content", "id": "c", "values": {}},
    ]
    assert wizard.step_keys(patch, CAMPAIGN) == set()


def test_template_summary_names_the_next_open_step_and_leave():
    """The summary says what's next as text and offers Leave campaign setup.

    The moving controls are the action row at the bottom (#1404), so the
    region itself has no Back or Continue link and no form.
    """
    stepper = wizard.build(True, "content", done={"settings"}, mail_test_url=TEST_URL)
    html = render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": stepper}},
    )
    region = html[: html.index("</nav>")]
    assert 'id="campaign-stepper"' in region and "data-in-place-region" in region
    assert "Step 4 of 8:" in region and "<strong>Pages and emails</strong>" in region
    assert "1 of 8 steps completed" in region
    assert "<span data-campaign-step-next>Next: Dates and mail schedules</span>" in (
        region
    )
    assert f'<a href="{reverse("admin:index")}" data-campaign-leave>' in region
    assert "Leave campaign setup" in region
    assert "data-campaign-step-back" not in region
    # Once in the step bar and once in each copy of the list (#1402): the
    # collapsed one for narrow screens and the open one for wide ones, of
    # which CSS shows one (#1404).
    assert region.count('aria-current="step"') == 3
    assert region.count('<ol class="setup-steps">') == 2
    assert '<div class="setup-stepper-wide" role="group"' in region
    assert f'<a href="{reverse("admin:share_settings")}" class="setup-track-' in region
    assert "<form" not in region
    # The status line Create the campaign's Apply writes in is reserved.
    assert 'id="campaign-step-outcome"' in html


def test_upcoming_skips_completed_steps():
    """Continue never lands on a completed step (#1404)."""
    stepper = wizard.build(
        True,
        "settings",
        done={"settings", "load", "share_options", "content"},
        mail_test_url=TEST_URL,
    )
    assert stepper.next.key == "load"
    assert stepper.upcoming.key == "schedules"
    # With every later step done, it is simply the next step.
    every = {page.key for page in wizard.PAGES}
    stepper = wizard.build(True, "images", done=every, mail_test_url=TEST_URL)
    assert stepper.upcoming.key == "mail_test"
    assert (
        wizard.build(True, "go_live", done=set(), mail_test_url=None).upcoming is None
    )


def test_resume_is_the_first_open_step():
    """Home's Continue campaign setup opens the first step not done yet."""
    stepper = wizard.build(
        False, None, done={"settings", "content"}, mail_test_url=None
    )
    assert wizard.resume(stepper).key == "load"
    stepper = wizard.build(
        False, None, done={"settings", "load", "content"}, mail_test_url=None
    )
    assert wizard.resume(stepper).key == "schedules"
    every = {page.key for page in wizard.PAGES}
    stepper = wizard.build(True, None, done=every, mail_test_url=TEST_URL)
    assert wizard.resume(stepper).key == "go_live"


def actions(stepper):
    """The action row the Admin layout draws under a page with ``stepper``."""
    return render_to_string(
        "stewardship/campaign-step-actions.html",
        {"admin_chrome": {"campaign_wizard": stepper}},
    )


def test_action_row_continues_an_unchanged_step():
    """Back and a Continue form that records the step, naming where it goes."""
    html = actions(
        wizard.build(
            True, "share_options", done={"settings", "load"}, mail_test_url=None
        )
    )
    assert f'href="{reverse("admin:source_refresh")}"' in html
    assert "data-campaign-step-back" in html and ">Back<" in html
    assert f'action="{reverse("admin:campaign_step_continue")}"' in html
    assert '<input type="hidden" name="step" value="share_options">' in html
    assert "Continue to Pages and emails" in html
    # Its hint keeps its line, hidden until a change holds Continue.
    assert 'id="campaign-continue-hint" data-continue-hint data-idle' in html


def test_dates_with_no_schedules_continue_without_them():
    """Dates and mail schedules can be continued with no schedules (#1404)."""
    stepper = wizard.build(
        True, "schedules", done={"settings"}, mail_test_url=None, no_schedules=True
    )
    html = actions(stepper)
    assert "Continue without schedules for now" in html
    assert '<input type="hidden" name="step" value="schedules">' in html
    stepper = wizard.build(True, "schedules", done={"settings"}, mail_test_url=None)
    assert "Continue to Campaign images" in actions(stepper)


def test_test_email_continues_only_after_an_accepted_test():
    """Preview and test email's purpose is the test, so Continue waits for it."""
    before = actions(
        wizard.build(True, "mail_test", done=set(), mail_test_url=TEST_URL)
    )
    assert "Continue to" not in before and "<form" not in before
    after = actions(
        wizard.build(True, "mail_test", done={"mail_test"}, mail_test_url=TEST_URL)
    )
    assert f'href="{reverse("admin:go_live")}"' in after
    assert "Continue to Go-live readiness" in after and "<form" not in after


def test_the_last_step_and_sub_pages_have_back_only():
    """Go-live readiness has Back; a sub-page has Return to its step."""
    last = actions(wizard.build(True, "go_live", done=set(), mail_test_url=TEST_URL))
    assert "data-campaign-step-back" in last and "Continue" not in last
    sub = actions(
        wizard.build(True, "content", done={"settings"}, mail_test_url=None, sub=True)
    )
    assert f'href="{reverse("admin:content_catalog")}"' in sub
    assert "Return to Pages and emails" in sub
    assert "Continue" not in sub and "<form" not in sub


def test_template_draws_nothing_without_a_stepper():
    """Every other Admin page leaves the region and the action row out."""
    for name in ("campaign-stepper.html", "campaign-step-actions.html"):
        html = render_to_string(
            f"stewardship/{name}", {"admin_chrome": {"campaign_wizard": None}}
        )
        assert html.strip() == ""


def test_load_notice_offers_the_load_until_families_exist():
    """Each step offers Load the campaign's ParishSoft data, then says it loads."""
    offer = wizard.build(
        True, "content", done={"settings"}, mail_test_url=None, load="offer"
    )
    html = render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": offer}},
    )
    assert f'action="{reverse("admin:source_refresh")}"' in html
    assert '<input type="hidden" name="return_step" value="content">' in html
    assert f'name="request_key" value="{offer.refresh_key}"' in html
    loading = wizard.build(
        True, "content", done={"settings"}, mail_test_url=None, load="loading"
    )
    html = render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": loading}},
    )
    assert "ParishSoft data is loading; Go-live readiness waits for it." in html
    assert "source_refresh" not in html and "<form" not in html


@pytest.mark.parametrize(
    ("name", "step", "sub"),
    [
        ("content_catalog", "content", False),
        ("content_edit", "content", True),
        ("content_revision", "content", True),
        ("content_history", "settings", True),
        ("content_history_revision", "settings", True),
        ("schedule_new", "schedules", True),
        ("schedule_edit", "schedules", True),
        ("schedule_calendar", "schedules", True),
        ("artwork_upload", "images", False),
        ("users", None, None),
    ],
)
def test_sub_pages_draw_their_steps_stepper(monkeypatch, name, step, sub):
    """Each sub-page draws the stepper with its step current (#1404)."""
    monkeypatch.setattr(wizard, "invitation_revision", lambda *_: None)
    monkeypatch.setattr(wizard, "completed", lambda *_: {"settings"})
    monkeypatch.setattr(wizard, "_load_state", lambda *_: (None, None, None))
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.schedule_reads.campaign_schedules",
        lambda *_: [{}],
    )
    row = SimpleNamespace(
        pk=CAMPAIGN, active_configuration=SimpleNamespace(values={"modules": []})
    )
    match = SimpleNamespace(url_name=name, namespace="admin", kwargs={})
    stepper = wizard.for_request(match, row, SimpleNamespace())
    if step is None:
        assert stepper is None
        return
    assert stepper.current.key == step and stepper.sub is sub


def test_status_offers_the_next_step_once_applied():
    """Change status names the next step only after the change is applied."""
    step = wizard.StatusLink(
        "/admin/campaign/schedules/", "Dates and mail schedules", back=False
    )
    for state, shown in (("applied", True), ("staged", False)):
        html = render_to_string(
            "stewardship/configuration-request-status.html",
            {
                "receipt": SimpleNamespace(state=state, request_id=uuid4()),
                "continue_step": step,
            },
        )
        assert ("Continue to the next step: Dates and mail schedules" in html) is shown


def test_status_leads_back_to_a_sub_pages_step():
    """A sub-page's applied change offers Return to its step (#1404)."""
    html = render_to_string(
        "stewardship/configuration-request-status.html",
        {
            "receipt": SimpleNamespace(state="applied", request_id=uuid4()),
            "continue_step": wizard.StatusLink(
                "/admin/campaign/content/", "Pages and emails", back=True
            ),
        },
    )
    assert '<a href="/admin/campaign/content/">Return to Pages and emails</a>' in html


@pytest.mark.parametrize(
    ("step", "shown", "expected"),
    [
        # A step the stepper offers: back to that step's page.
        ("content", True, "/admin/campaign/content/"),
        # Preview and test email's address carries its revision.
        ("mail_test", True, TEST_URL),
        # An unknown key, no key, or no stepper at all: the refresh page.
        ("users", True, None),
        ("", True, None),
        ("content", False, None),
    ],
)
def test_a_load_returns_only_to_a_step_the_stepper_offers(
    monkeypatch, step, shown, expected
):
    """Load the campaign's ParishSoft data returns to its step (#1404).

    The posted ``return_step`` picks the redirect, so only a step of the
    live stepper is accepted; anything else lands on Refresh from
    ParishSoft as before.
    """
    from django.test import RequestFactory

    from parishkit.stewardship.accounts import refresh_views

    stepper = wizard.build(False, None, done={"settings"}, mail_test_url=TEST_URL)
    monkeypatch.setattr(wizard, "current", lambda: stepper if shown else None)
    key = uuid4()
    monkeypatch.setattr(
        refresh_views,
        "request_manual_refresh",
        lambda *_: SimpleNamespace(task_root_id=uuid4()),
    )
    request = RequestFactory().post(
        "/admin/parish/parishsoft-refresh/",
        {"request_key": str(key), "return_step": step},
    )
    request.session = {}
    response = refresh_views._request(request, None, None)
    assert response.status_code == 302
    assert response["Location"] == (
        expected or reverse("admin:source_refresh") + "#refresh-run"
    )
    assert refresh_views.RUN_KEY in request.session


def test_create_the_campaign_is_step_one_with_every_later_step_unavailable():
    """Before the campaign exists, Create the campaign is step 1 (#1404 PR 3).

    Step 1 is Campaign settings on Create the campaign's own address; every
    later step, Share options included (the modules are not chosen yet), is
    unavailable for the one reason, and none of them links anywhere.
    """
    stepper = wizard.creation()
    assert keys(stepper) == [page.key for page in wizard.PAGES]
    first, *later = stepper.steps
    assert first.current and first.state == "todo"
    assert str(first.label) == "Campaign settings"
    assert first.url == reverse("admin:campaign_create")
    for step in later:
        assert step.url is None and step.state == "blocked"
        assert str(step.status) == "Not available yet: Create the campaign first."
    assert stepper.previous is None and stepper.next is None
    assert (stepper.completed, stepper.total) == (0, 8)
    assert stepper.upcoming.key == "load" and stepper.creating
    html = render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": stepper}},
    )
    region = html[: html.index("</nav>")]
    assert "Step 1 of 8:" in region and "<strong>Campaign settings</strong>" in region
    assert "0 of 8 steps completed" in region
    assert "Next: Load ParishSoft data" in region and "Leave campaign setup" in region
    # Seven unavailable segments in the bar, none a link to a later page.
    assert region.count("data-step-unavailable") == 7
    assert reverse("admin:source_refresh") not in region
    assert reverse("admin:share_settings") not in region
    assert "data-campaign-load" not in html
    # The page draws its own action row inside its form, so the shared row
    # draws nothing: no Back and no Continue.
    assert actions(stepper).strip() == ""


@pytest.mark.parametrize(
    ("name", "mode", "admin", "drawn"),
    [
        ("campaign_create", "testing", True, True),
        ("campaign_create", "production", True, False),
        ("campaign_create", "testing", False, False),
        ("index", "testing", True, False),
        ("campaign_settings", "testing", True, False),
    ],
)
def test_only_create_the_campaign_draws_the_stepper_before_a_campaign(
    name, mode, admin, drawn
):
    """Without a campaign only an Administrator's Create page in Testing mode."""
    match = SimpleNamespace(namespace="admin", url_name=name)
    stepper = wizard.for_creation(match, mode, admin=admin)
    assert (stepper is not None) is drawn
    if drawn:
        assert stepper.creating and stepper.current.key == "settings"


@pytest.mark.parametrize(
    ("load", "done", "continues"),
    [
        ("offer", set(), False),
        ("failed", set(), False),
        ("loading", set(), False),
        (None, {"load"}, True),
    ],
)
def test_load_step_continues_once_the_data_is_loaded(load, done, continues):
    """Load ParishSoft data offers Continue only once loaded (#1404 PR 5).

    Before a load the page's own button is the step's primary, and while
    the load runs there is no primary at all (Administrator, 2026-10-17:
    "We're loading the data... ok, now it's done, and we can continue"), so
    the row has Back only; once the campaign has its Families the row
    offers Continue to the next step, a plain link: the step stores no
    review.
    """
    html = actions(
        wizard.build(
            True, "load", done={"settings", *done}, mail_test_url=None, load=load
        )
    )
    assert f'href="{reverse("admin:campaign_settings")}"' in html
    assert ("Continue to Share options" in html) is continues
    assert (f'href="{reverse("admin:share_settings")}"' in html) is continues
    assert "<form" not in html and "campaign_step_continue" not in html


def stepper_html(stepper):
    """The stepper region and the notices after it, as base.html draws them."""
    return render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": stepper}},
    )


def test_failed_load_says_so_and_offers_try_again():
    """After a failed load, every other step says so (#1404 PR 5, L6 on #1407).

    The notice names the failure and links its Background task, and its
    button reads Try again, so a failed load never looks like one never
    started.
    """
    task = uuid4()
    failed = wizard.build(
        True,
        "content",
        done={"settings"},
        mail_test_url=None,
        load="failed",
        load_task=task,
    )
    html = stepper_html(failed)
    assert "The campaign's ParishSoft data did not load." in html
    assert f'href="{reverse("admin:background_task_page", args=[task])}"' in html
    assert ">Try again<" in html
    assert f'name="request_key" value="{failed.refresh_key}"' in html
    assert failed.refresh_key is not None
    assert '<input type="hidden" name="return_step" value="content">' in html


@pytest.mark.parametrize("load", ["offer", "loading", "failed"])
def test_the_load_step_draws_no_load_notice(load):
    """Load ParishSoft data is the load itself, so it draws no notice above."""
    html = stepper_html(
        wizard.build(True, "load", done={"settings"}, mail_test_url=None, load=load)
    )
    assert "data-campaign-load" not in html
    assert "Step 2 of 8:" in html and "<strong>Load ParishSoft data</strong>" in html


def test_families_complete_the_load_step():
    """The step is completed by the Families, so nothing else is read (PR 5)."""
    assert wizard._load_state(SimpleNamespace(), {"settings", "load"}) == (
        None,
        None,
        None,
    )


def states(boxes):
    """Each stage box's (key, state), in order."""
    return [(box["key"], box["state"]) for box in boxes]


KEYS = ["fetching", "staging", "validating", "promoting"]


@pytest.mark.parametrize(
    ("load", "phase", "expected"),
    [
        ("offer", None, ["waiting"] * 4),
        ("loading", "unspecified", ["waiting"] * 4),
        ("loading", "fetching", ["active", "waiting", "waiting", "waiting"]),
        ("loading", "validating", ["done", "done", "active", "waiting"]),
        ("loaded", None, ["done"] * 4),
        ("failed", "staging", ["done", "failed", "waiting", "waiting"]),
        ("failed", "unspecified", ["failed", "waiting", "waiting", "waiting"]),
    ],
)
def test_load_stages_follow_the_refresh_phases(load, phase, expected):
    """One box per refresh phase the worker reports, in order (#1404 PR 5).

    Not loaded: every stage waiting; loading: the stages before the run's
    phase done and its own in progress; loaded: every stage done; failed:
    the stage it stopped in failed. Each box words its state.
    """
    boxes = wizard.load_stages(load, phase)
    assert states(boxes) == list(zip(KEYS, expected, strict=True))
    words = {"done": "Done", "active": "In progress", "waiting": "Not started"}
    words["failed"] = "Failed"
    assert [str(box["status"]) for box in boxes] == [words[x] for x in expected]


def test_the_loading_stepper_carries_the_run_to_its_stage_boxes():
    """The stepper's facts draw the step's one state and its boxes."""
    task = uuid4()
    stepper = wizard.build(
        True,
        "load",
        done={"settings"},
        mail_test_url=None,
        load="loading",
        load_task=task,
        load_progress=("staging", 10, 40),
    )
    assert stepper.load_view == "loading" and stepper.load_task == task
    assert states(stepper.load_stages)[:2] == [
        ("fetching", "done"),
        ("staging", "active"),
    ]
    loaded = wizard.build(
        True, "load", done={"settings", "load"}, mail_test_url=None, families=12
    )
    assert loaded.load_view == "loaded" and loaded.families == 12
    assert {box["state"] for box in loaded.load_stages} == {"done"}


def load_region(**values):
    """Load ParishSoft data's status region, drawn from ``values``."""
    return render_to_string("stewardship/campaign-load-status.html", values)


def test_load_status_region_draws_one_state_each():
    """Each state's line, its boxes, and polling only while loading (PR 5)."""
    task = uuid4()
    status_url = reverse("admin:background_task_status", args=[task])
    offer = load_region(load="offer", stages=wizard.load_stages("offer", None))
    assert "This campaign's ParishSoft data hasn't been loaded yet." in offer
    assert "data-live-pending" not in offer and "data-live-url" not in offer
    assert offer.count('data-stage-state="waiting"') == 4
    loading = load_region(
        load="loading",
        load_task=task,
        stages=wizard.load_stages("loading", "fetching"),
        progress=("fetching", 1000, 4000),
    )
    assert "Loading the campaign's ParishSoft data…" in loading
    assert f'data-live-url="{status_url}?view=load"' in loading
    assert "data-live-pending" in loading and "1,000 of 4,000 records checked" in (
        loading
    )
    assert "Download from ParishSoft: In progress." in loading
    assert "data-live-follow" not in loading
    failed = load_region(
        load="failed", load_task=task, stages=wizard.load_stages("failed", "staging")
    )
    assert "did not load." in failed and "See what went wrong" in failed
    assert reverse("admin:background_task_page", args=[task]) in failed
    assert "data-live-pending" not in failed
    loaded = load_region(
        load="loaded",
        stages=wizard.load_stages("loaded", None),
        families=1234,
        as_of=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
    )
    assert "is loaded." in loaded and "1,234 Families, as of" in loaded
    assert 'datetime="2026-10-17T12:00:00+00:00" data-local-instant' in loaded
    assert loaded.count('data-stage-state="done"') == 4
    # A poll's settled answer follows up by re-reading the page in place.
    polled = load_region(**wizard.load_status(task_values("succeeded", task)))
    assert "data-live-follow" in polled and 'href="#refresh-run"' in polled
    assert "is loaded." in polled and "data-live-pending" not in polled


def task_values(state, task, phase="promoting"):
    """A refresh run as the Background task read names it."""
    return {
        "id": str(task),
        "state": state,
        "progress": {"phase": phase, "current": 4000, "total": 4000},
    }


@pytest.mark.parametrize(
    ("state", "load"),
    [
        ("queued", "loading"),
        ("running", "loading"),
        ("retry_wait", "loading"),
        ("succeeded", "loaded"),
        ("failed", "failed"),
        ("cancelled", "failed"),
    ],
)
def test_load_status_reads_the_run_state(state, load):
    """The status fragment names the step's state from the run alone."""
    task = uuid4()
    status = wizard.load_status(task_values(state, task, "staging"))
    assert status["load"] == load and status["load_task"] == str(task)
    assert status["polled"] is True
