"""Campaign settings as one editor (#1119), in real browsers.

Every setting shows once, in the same order in every campaign state: open
where it can change, otherwise disabled where it stands, with the reason in
its "i" tip. One Save, unavailable until the form holds a real
change, opens the shared review dialog (#750); once applied, the dialog
closes, the form's region is redrawn in place and the status line says so.
Pages and POST answers are ``campaign_settings_components``'; a route
answers each Review POST.
"""

import re

import pytest
from playwright.sync_api import expect

from parishkit.stewardship.accounts.campaign_views import (
    LOCKED_CLOSED,
    LOCKED_LIVE,
    MINISTRIES_ALONE,
)

from .campaign_settings_components import (
    DRAFT,
    DRAFT_REFUSED,
    DRAFT_REVIEW,
    ELSEWHERE,
    END_REFUSAL,
    LIVE,
    LIVE_MINISTRIES_REVIEW,
    LIVE_MIXED,
    LIVE_REFUSING,
    LIVE_REVIEW,
    LOCKED,
)
from .test_in_place import MARK, MARKED
from .test_live_end_date import answer_checks, enabled
from .test_settings_in_place import answer_reviews, expect_review
from .waits import eventually, has_attribute, hidden, same_place, visible


def expect_save(page, **expected):
    """``expect_review`` for Campaign settings, whose button reads Save and
    whose hint says so (#1356)."""
    expected.setdefault("hint", "Change a setting to save it.")
    expect_review(page, name="Save", **expected)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
APPLIED_LINE = re.compile(
    r"^Campaign settings changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$"
)
RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
# The settings' names, in page order, each once.
NAMES = """() => [...new Set([...document.querySelectorAll(
    '#settings-form :is(input, select, textarea):not([type=hidden])')]
    .map((node) => node.name))]"""
# Where an element sits on the page (not in the window, which may scroll).
PLACE = """(node) => {
    const box = node.getBoundingClientRect();
    return [box.left + window.scrollX, box.top + window.scrollY];
}"""
# Every setting control still enabled on the page.
OPEN = """() => [...document.querySelectorAll(
    '#settings-form :is(input, select, textarea):not([type=hidden])')]
    .filter((node) => !node.matches(':disabled')).map((node) => node.name)"""


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def dialog_of(page, name="Review campaign settings"):
    """The review dialog, once it is open: a full review's title, or a
    compact review's own question (#1356)."""
    dialog = page.get_by_role("dialog", name=name)
    visible(dialog)
    return dialog


def test_every_setting_shows_once_in_the_same_order(page, component_origin):
    """A draft, a live campaign and a closed one draw the same settings in
    the same order, with one Save and no read-only copy; only
    what can change now is open."""
    orders, opened = {}, {}
    for path in (DRAFT, LIVE, LOCKED):
        page.goto(component_origin + path)
        assert page.get_by_role("button", name="Save", exact=True).count() == 1
        assert page.get_by_text("Saved campaign settings").count() == 0
        assert page.get_by_text("read-only").count() == 0
        orders[path] = page.evaluate(NAMES)
        opened[path] = set(page.evaluate(OPEN))
    assert orders[DRAFT] == orders[LIVE] == orders[LOCKED]
    assert {"name", "end_date", "census"} <= opened[DRAFT]
    assert opened[LIVE] == {"end_date", "ministry_leader_roles", "ministry_duids"}
    assert opened[LOCKED] == set()


def test_a_draft_change_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Review opens the dialog on its title; Apply closes it, redraws the
    form in place at the applied version, says what was applied in the
    status line and leaves Review waiting for the next change. The redrawn
    form's module scripts work, nothing reloads, the status line does not
    move, and no line claims that typing was lost (it was reviewed)."""
    reviews = answer_reviews(page, component_origin, DRAFT_REVIEW, address=DRAFT)
    page.goto(component_origin + DRAFT)
    failures = watch(page)
    expect_save(page, enabled=False)
    page.get_by_label("Campaign name").fill("Renamed campaign")
    expect_save(page, enabled=True)
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page, "Save this change?")
    assert page.evaluate(FOCUSED) == "campaign-review-title"
    # A compact review (#1356): one line for the one setting changed, which
    # describes the dialog, and no Now and After table.
    lines = dialog.locator("#campaign-review-summary li")
    assert lines.count() == 1
    text = lines.first.inner_text()
    assert text.startswith("Campaign name: Sample campaign")
    assert text.endswith(" Renamed campaign") and "→" in text
    assert dialog.locator("table").count() == 0
    assert dialog.get_attribute("aria-describedby") == "campaign-review-summary"
    expect(dialog).to_have_accessible_description(
        re.compile(r"^Campaign name: Sample campaign ?, becomes Renamed campaign$")
    )
    assert len(reviews) == 1 and "name=Renamed+campaign" in reviews[0]
    outcome = page.locator("#settings-outcome").evaluate(PLACE)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('settings-outcome').textContent !== ''")
    assert page.locator("[data-edits-lost]").count() == 0
    assert same_place(page.locator("#settings-outcome").evaluate(PLACE), outcome)
    line = page.locator("#settings-outcome")
    assert APPLIED_LINE.search(line.inner_text()), line.inner_text()
    assert page.evaluate(FOCUSED) == "settings-outcome"
    digest = page.locator("#settings-form input[name=base_digest]")
    assert digest.input_value() == "b" * 64
    assert page.get_by_label("Campaign name").input_value() == "Renamed campaign"
    expect_save(page, enabled=False)
    group = page.locator("#ministry-selections")
    hidden(group)
    page.get_by_label("Ministry stewardship").check()
    visible(group)
    expect_save(page, enabled=True)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_review_waits_for_a_change(page, component_origin):
    """A text edit or a module tick makes Review available, undoing it makes
    it unavailable again, and a restored tick is noticed on pageshow (#921)."""
    page.goto(component_origin + DRAFT)
    expect_save(page, enabled=False)
    name = page.get_by_label("Campaign name")
    name.fill("Renamed campaign")
    expect_save(page, enabled=True)
    name.fill("Sample campaign")
    expect_save(page, enabled=False)
    module = page.get_by_label("Ministry stewardship")
    module.check()
    expect_save(page, enabled=True)
    module.uncheck()
    expect_save(page, enabled=False)
    module.evaluate("box => { box.checked = true; }")
    page.evaluate(RESTORE)
    expect_save(page, enabled=True)


def test_a_refused_review_marks_the_field_and_keeps_the_dialog_shut(
    page, component_origin
):
    """A Review refused for a value is shown on the form (#750)."""
    answer_reviews(page, component_origin, DRAFT_REFUSED, 400, address=DRAFT)
    page.goto(component_origin + DRAFT)
    failures = watch(page)
    page.get_by_label("Campaign name").fill("Renamed campaign")
    page.get_by_role("button", name="Save", exact=True).click()
    visible(page.locator("[data-error-summary]"))
    has_attribute(page.get_by_label("Campaign name"), "aria-invalid", "true")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_dates_only_review_leads_to_another_page(page, component_origin):
    """A Review the server answers with another page (a draft's dates alone
    lead to Dates and mail schedules) ends on that page."""

    def elsewhere(route):
        """Send Review's POST where the fixture server redirects it."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.continue_(url=component_origin + ELSEWHERE)

    page.route(lambda url: url.split("#")[0] == component_origin + DRAFT, elsewhere)
    page.goto(component_origin + DRAFT)
    page.get_by_label("Campaign start date").fill("2054-10-02")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url(component_origin + "/in-place-other")


def test_a_live_end_date_and_leader_roles_are_one_review(page, component_origin):
    """A live campaign's open settings are reviewed together: one Review
    posts only them, and the one dialog shows both changes and the end
    date's notes; Apply applies them as one change."""
    reviews = answer_reviews(page, component_origin, LIVE_REVIEW, address=LIVE)
    answer_checks(page, component_origin)
    page.goto(component_origin + LIVE)
    failures = watch(page)
    form = page.locator("#settings-form")
    assert form.get_by_label("Campaign name").is_disabled()
    assert form.get_by_label("Included Ministries").is_enabled()
    roles = page.get_by_role("group", name="Ministry leader roles")
    assert roles.get_by_label("Staff").is_enabled()
    form.get_by_label("Campaign end date").fill("2054-11-15")
    roles.get_by_label("Staff").uncheck()
    review = page.get_by_role("button", name="Save", exact=True)
    enabled(review)
    review.click()
    dialog = dialog_of(page)
    text = dialog.inner_text()
    assert "Campaign end date" in text and "Ministry leader roles" in text
    # The end date's re-planned digests keep the full review (#1356), under
    # the dialog's own title and with no compact description.
    assert dialog.locator("table").count() == 1
    assert dialog.get_attribute("aria-describedby") is None
    assert "Family codes and links already sent keep working" in text
    assert len(reviews) == 1
    assert "end_date=2054-11-15" in reviews[0]
    assert "ministry_leader_roles=Chairperson" in reviews[0]
    assert "ministry_leader_roles=Staff" not in reviews[0]
    assert "name=" not in reviews[0].replace("_name", "")
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('settings-outcome').textContent !== ''")
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_locked_setting_says_why_in_its_tip(page, component_origin):
    """Each locked setting's tip gives its reason. A box beside its label
    keeps to the top of its row, so its tip opening under the label never
    moves it (no layout shift, #736)."""
    page.goto(component_origin + LIVE)
    box = page.locator("#id_additional_information")
    before = box.evaluate(PLACE)
    page.locator("button[aria-controls=id_additional_information_tip]").click()
    visible(page.locator("#id_additional_information_tip"))
    # Linux Firefox may report the same place a ten-thousandth of a pixel apart.
    assert same_place(box.evaluate(PLACE), before)
    page.locator("button[aria-controls=id_census_tip]").click()
    tip = page.locator("#id_census_tip")
    visible(tip)
    assert str(LOCKED_LIVE) in tip.inner_text()


def test_nothing_can_change_on_a_closed_campaign(page, component_origin):
    """A closed campaign's settings are all locked, each saying why, and
    Review changes stays unavailable with its reason."""
    page.goto(component_origin + LOCKED)
    assert page.evaluate(OPEN) == []
    expect_save(
        page,
        enabled=False,
        hint="Nothing on this page can change now; each greyed-out setting's "
        "i button says why.",
    )
    page.locator("button[aria-controls=id_name_tip]").click()
    assert str(LOCKED_CLOSED) in page.locator("#id_name_tip").inner_text()


def test_a_change_the_installer_refuses_is_shown_in_the_dialog(page, component_origin):
    """Applied but refused when it ran: the dialog says why, below its
    buttons, and stays until closed; the page behind is not redrawn."""
    answer_reviews(page, component_origin, LIVE_REVIEW, address=LIVE_REFUSING)
    answer_checks(page, component_origin)
    page.goto(component_origin + LIVE_REFUSING)
    failures = watch(page)
    page.locator("#settings-form").get_by_label("Campaign end date").fill("2054-11-15")
    review = page.get_by_role("button", name="Save", exact=True)
    enabled(review)
    review.click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert END_REFUSAL in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#settings-outcome").inner_text() == ""
    field = page.locator("#settings-form").get_by_label("Campaign end date")
    assert field.input_value() == "2054-11-15"
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_live_ministry_change_is_reviewed_with_its_impact(page, component_origin):
    """A live campaign's Ministry list is edited on the page (#1119): Review
    posts the selections and the dialog shows what the change touches."""
    reviews = answer_reviews(
        page, component_origin, LIVE_MINISTRIES_REVIEW, address=LIVE
    )
    answer_checks(page, component_origin)
    page.goto(component_origin + LIVE)
    failures = watch(page)
    expect_save(page, enabled=False)
    page.get_by_label("Included Ministries").select_option(["4"])
    expect_save(page, enabled=True)
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    text = dialog.inner_text()
    assert "Ministries to remove" in text and "Food pantry" in text
    assert "Families with a form open now" in text
    assert len(reviews) == 1
    assert "ministry_duids=4" in reviews[0] and "ministry_duids=9" not in reviews[0]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('settings-outcome').textContent !== ''")
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_ministry_change_with_another_is_refused_in_the_dialog(
    page, component_origin
):
    """A Ministry change is reviewed on its own: made with another change,
    the server's refusal says so in the dialog, with no Apply."""
    answer_reviews(page, component_origin, LIVE_MIXED, 400, address=LIVE)
    answer_checks(page, component_origin)
    page.goto(component_origin + LIVE)
    page.get_by_label("Included Ministries").select_option(["4"])
    roles = page.get_by_role("group", name="Ministry leader roles")
    roles.get_by_label("Staff").uncheck()
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert str(MINISTRIES_ALONE) in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").count() == 0
