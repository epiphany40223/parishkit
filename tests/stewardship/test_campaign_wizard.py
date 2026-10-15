"""The Campaign setup stepper's steps, completion reading and template (#142).

The admin-portal spec's "Create the campaign" section, Guided steps: the
stepper runs over the existing campaign pages while the current campaign is
a Testing draft, completes a page step once a change made there is applied,
and its Back and Next are plain links that never save anything.
"""

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
        "Share options",
        "Pages and emails",
        "Dates and mail schedules",
        "Campaign images",
        "Preview and test email",
        "Go-live readiness",
    ]
    for step, page in zip(stepper.steps, wizard.PAGES, strict=True):
        assert step.label == NAMES[page.route].label
        expected = (
            TEST_URL if page.key == "mail_test" else reverse(f"admin:{page.route}")
        )
        assert step.url == expected


def test_share_options_applies_only_with_financial_stewardship():
    """Share options is a step only when the campaign's modules use it."""
    stepper = wizard.build(False, "content", done=set(), mail_test_url=TEST_URL)
    assert "share_options" not in keys(stepper)
    assert stepper.previous.key == "settings"
    assert stepper.total == 6


def test_back_and_next_are_the_neighbouring_steps():
    """Back and Next link the pages either side; the ends have only one."""
    first = wizard.build(True, "settings", done=set(), mail_test_url=TEST_URL)
    assert first.previous is None and first.next.key == "share_options"
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


def test_template_draws_the_stepper_with_back_and_next_links():
    """The region carries the summary, plain Back/Next links and the list."""
    stepper = wizard.build(True, "content", done={"settings"}, mail_test_url=TEST_URL)
    html = render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": stepper}},
    )
    assert 'id="campaign-stepper"' in html and "data-in-place-region" in html
    assert "Step 3 of 7:" in html and "<strong>Pages and emails</strong>" in html
    assert "1 of 7 steps completed" in html
    assert f'href="{reverse("admin:share_settings")}" data-campaign-step-back' in html
    assert "Back: Share options" in html
    assert (
        f'href="{reverse("admin:schedule_settings")}" data-campaign-step-next' in html
    )
    assert "Next: Dates and mail schedules" in html
    # Once in the step bar and once in each copy of the list (#1402): the
    # collapsed one for narrow screens and the open one for wide ones, of
    # which CSS shows one (#1404).
    assert html.count('aria-current="step"') == 3
    assert html.count('<ol class="setup-steps">') == 2
    assert '<div class="setup-stepper-wide" role="group"' in html
    assert f'<a href="{reverse("admin:share_settings")}" class="setup-track-' in html
    assert "<form" not in html


def test_template_draws_nothing_without_a_stepper():
    """Every other Admin page leaves the region out."""
    html = render_to_string(
        "stewardship/campaign-stepper.html", {"admin_chrome": {"campaign_wizard": None}}
    )
    assert html.strip() == ""


def test_status_offers_the_next_step_once_applied():
    """Change status names the next step only after the change is applied."""
    step = SimpleNamespace(
        url="/admin/campaign/schedules/", label="Dates and mail schedules"
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


def test_a_completed_step_offers_continue_in_place_of_next():
    """Once the current step is completed, Next reads Continue to the next step."""
    stepper = wizard.build(
        True, "share_options", done={"settings", "share_options"}, mail_test_url=None
    )
    html = render_to_string(
        "stewardship/campaign-stepper.html",
        {"admin_chrome": {"campaign_wizard": stepper}},
    )
    assert "data-campaign-continue>Continue to the next step: Pages and emails" in html
    assert "Next: Pages and emails" not in html
