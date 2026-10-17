"""Mobile, keyboard and accessibility checks for staff follow-up."""

import pytest
from django.urls import reverse

from .information_components import ITEM, UNCONFIRMED, UPDATE
from .test_charts import assert_clean, watch
from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_staff_queue_detail_history_and_accessibility(
    page, component_origin, axe_source, width
):
    """The native workflow exposes safe filters and accessible correction history."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/information")
    search = page.get_by_label(
        "Search Family name, DUID, submitted text or Staff notes"
    )
    search.fill("Private name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "disposition"
    visible(page.get_by_role("button", name="Next", exact=True).first)
    assert page.locator("th[aria-sort=descending]").inner_text().startswith("Submitted")
    # Every form posts, except the empty one Vega adds to each drawn chart
    # for parameter bindings, which this page's Follow-up status chart (#720)
    # has none of: with no field it submits nothing.
    assert page.locator("form:not(.vega-bindings)").evaluate_all(
        "nodes => nodes.every(node => node.method === 'post')"
    )
    assert page.locator("form.vega-bindings").evaluate_all(
        "nodes => nodes.every(node => !node.elements.length)"
    )
    page.route("**/information/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    assert "Private" not in sent.value.url and "?" not in sent.value.url
    # Let the submission finish (in place or not) before the next navigation,
    # which it would otherwise interrupt.
    visible(page.get_by_text("Filtered"))
    for path in (
        "/information",
        "/information-item",
        "/information-gated",
        "/information-conflict",
    ):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/information-item")
    assert (
        page.get_by_label("Staff notes", exact=True).input_value()
        == "Called; awaiting a response."
    )
    assert page.get_by_label("Follow-up completed", exact=True).is_checked()
    # The reopen confirmation applies only once completion is unticked (#519).
    confirm = page.get_by_label("If clearing completion", exact=False)
    hidden(confirm)
    page.get_by_label("Follow-up completed", exact=True).uncheck()
    visible(confirm)
    visible(page.get_by_role("link", name="Open the replacement request"))
    visible(page.get_by_role("heading", name="Staff edit history"))
    visible(
        page.get_by_text(
            "This correction is resolved in the weekly digest workflow. "
            "Resolution does not necessarily mean an email was delivered.",
            exact=True,
        )
    )
    assert page.get_by_text("A correction has been delivered", exact=False).count() == 0
    assert page.locator("our").count() == 0
    page.goto(component_origin + "/information-unresolved")
    visible(
        page.get_by_text(
            "This change is visible to the weekly digest correction workflow; "
            "the old request is no longer actionable.",
            exact=True,
        )
    )
    page.goto(component_origin + "/information-gated")
    assert page.get_by_role("button", name="Save follow-up").is_disabled()
    page.goto(component_origin + "/information-queue-gated")
    page.get_by_text("Export complete results").click()
    assert page.get_by_role("button", name="Queue complete export").is_disabled()


def export_post(page, component_origin):
    """The complete-result form posts its private filters in the body."""
    page.goto(component_origin + "/information")
    # The export starts closed (#227).
    page.get_by_text("Export complete results").click()
    page.get_by_label("Export format", exact=True).select_option("xlsx")
    page.get_by_label("Export timezone", exact=True).select_option("UTC")
    page.get_by_label("Include complete Staff workflow history").check()
    page.route(
        "**" + reverse("admin:information_export"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Queue complete export").click()
    assert "search=Sample" in sent.value.post_data
    assert "format=xlsx" in sent.value.post_data
    assert "history=yes" in sent.value.post_data
    assert "browser_timezone=UTC" in sent.value.post_data
    assert "Sample" not in sent.value.url and "?" not in sent.value.url


def test_staff_complete_export_native_post(page, component_origin):
    """Private filters are submitted as a body, never serialized into an export URL."""
    export_post(page, component_origin)


def test_staff_follow_up_edit_posts_privately(page, component_origin):
    """Reopening a completed follow-up posts its notes and confirmation privately."""
    page.goto(component_origin + "/information-item")
    page.get_by_label("Follow-up completed", exact=True).uncheck()
    page.get_by_label("If clearing completion", exact=False).check()
    page.get_by_label("Staff notes", exact=True).fill("Reopened")
    assert page.get_by_role("button", name="Save follow-up").is_enabled()
    assert page.locator("input[name=expected_version]").input_value() == "2"
    page.route("**/record/", lambda route: route.fulfill(body="Saved"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Save follow-up").click()
    assert "notes=Reopened" in sent.value.post_data
    assert "confirm_clear=yes" in sent.value.post_data
    assert "followed_up=" not in sent.value.post_data
    assert "Reopened" not in sent.value.url


ORDER = (
    "Enter a Submitted on or before date no earlier than Submitted on or "
    "after, or clear it."
)
# Apply's top in page coordinates.
APPLY_BOX = (
    "document.querySelector('#table-filters [type=submit]')"
    ".getBoundingClientRect().y + scrollY"
)


def assert_refused_at_the_dates(page):
    """Both dates are marked and share one message; the summary links the end.

    Each date keeps its zone note before the message (#1047), in one
    aria-describedby.
    """
    from playwright.sync_api import expect

    for end in ("#start", "#end"):
        field = page.locator(end)
        expect(field).to_have_attribute("aria-invalid", "true")
        described = field.get_attribute("aria-describedby").split()
        assert described == ["information-zone-help", "submitted-dates_error"]
    expect(page.locator("#submitted-dates_error")).to_have_text(ORDER)
    expect(page.locator("#submitted-dates_error")).to_be_visible()
    summary = page.locator("[data-error-summary]")
    expect(summary).to_have_count(1)
    expect(summary.get_by_role("link", name=ORDER)).to_have_attribute("href", "#end")
    assert page.locator("[data-filter-panel]").evaluate("node => node.open")
    # The gate's hint says the same words; it is held hidden while the
    # message shows, so the form says it once (#1469).
    assert (
        page.locator("#information-filter-hint").evaluate(
            "node => getComputedStyle(node).visibility"
        )
        == "hidden"
    )
    assert page.locator("#table-filters").inner_text().count(ORDER) == 1


def assert_dates_cleared(page):
    """No mark, message or summary item is left on either date."""
    from playwright.sync_api import expect

    for end in ("#start", "#end"):
        expect(page.locator(end)).not_to_have_attribute("aria-invalid", "true")
        assert "submitted-dates_error" not in (
            page.locator(end).get_attribute("aria-describedby") or ""
        )
    expect(page.locator("#submitted-dates_error")).to_be_hidden()
    expect(page.locator("[data-error-summary]")).to_have_count(0)


@pytest.mark.parametrize("end", ["#end", "#start"])
def test_refused_dates_are_shown_at_their_fields(page, component_origin, end):
    """Dates the server refused are drawn again with the error at both (#1332).

    The dates keep the values sent, the summary takes focus and its link
    moves focus to Submitted on or before, and an edit of either date
    clears the mark from both.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/information-refused-dates")
    assert_refused_at_the_dates(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    expect(page.locator("#start")).to_have_value("2026-11-05")
    expect(page.locator("#end")).to_have_value("2026-11-01")
    page.locator("[data-error-summary] a").click()
    expect(page.locator("#end")).to_be_focused()
    page.locator(end).fill("2026-10-01" if end == "#start" else "2026-11-30")
    assert_dates_cleared(page)
    # The zone note still describes both dates.
    for date in ("#start", "#end"):
        assert page.locator(date).get_attribute("aria-describedby") == (
            "information-zone-help"
        )
    assert_clean(page, errors)


# The offsets of Apply, the dates' message line, the zone note and the hint
# row from the filter form's top, and the hint row's height (#1469). The
# error summary above the form comes and goes with the mark, so places are
# measured within the form.
FILTER_PLACES = """() => {
  const form = document.getElementById("table-filters");
  const top = (node) =>
    node.getBoundingClientRect().top - form.getBoundingClientRect().top;
  const hint = document.getElementById("information-filter-hint");
  return [
    top(form.querySelector("[type=submit]")),
    top(document.getElementById("submitted-dates_error").parentElement),
    top(document.getElementById("information-zone-help")),
    top(hint),
    hint.getBoundingClientRect().height,
  ];
}"""


def test_refused_dates_say_it_once_and_hand_back_the_hint(page, component_origin):
    """The refused order is said once, at the dates' line (#1469).

    While the server's message shows, the gate's hint in the same words is
    held hidden; an edit that leaves the dates out of order clears the
    server's mark, and the gate's own hint shows the reason again, still
    once. Neither moves Apply, the line, the zone note or the hint row.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/information-refused-dates")
    assert_refused_at_the_dates(page)
    before = page.evaluate(FILTER_PLACES)
    page.locator("#end").fill("2026-11-02")
    assert_dates_cleared(page)
    hint = page.locator("#information-filter-hint")
    expect(hint).to_have_text(ORDER)
    expect(hint).to_be_visible()
    assert page.locator("#table-filters").inner_text().count(ORDER) == 1
    assert page.get_by_role("button", name="Apply filters").is_disabled()
    assert page.evaluate(FILTER_PLACES) == pytest.approx(before, abs=0.5)
    assert_clean(page, errors)


def test_fixing_the_other_date_and_applying_clears_the_mark(page, component_origin):
    """A successful in-place Apply clears the server marks it does not repeat.

    The reader fixes the order at Submitted on or after, the other end
    (whose edit clears the shared mark at once), and Apply's answer marks
    nothing: no mark, message or summary is left (#1332).
    """
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/information-withdrawn").text()
    page.goto(component_origin + "/information-refused-dates")
    page.route(
        "**/information/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    page.locator("#start").fill("2026-10-01")
    # A mark a later answer must clear, set as an earlier answer would have
    # left it had the edit not cleared it (on the date not touched).
    page.evaluate("""() => {
        const field = document.getElementById("end");
        field.setAttribute("aria-invalid", "true");
        field.setAttribute("data-field-error", "");
        field.setAttribute(
            "aria-describedby", "information-zone-help submitted-dates_error");
        const line = document.getElementById("submitted-dates_error");
        line.hidden = false;
        line.querySelector("li").textContent = "stale";
    }""")
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("#information-matching")).to_have_text("Matching requests: 3.")
    assert_dates_cleared(page)
    assert_clean(page, errors)


def test_in_place_apply_refused_marks_the_kept_dates(page, component_origin):
    """A refused in-place Apply marks the dates on the form it kept (#1332).

    No reload and no shift: the form keeps what was typed and takes the
    marks and message from the 400 answer into its reserved line, so Apply
    stays under the pointer, and focus goes straight from Apply to the
    summary in the results, never through a date.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/information-refused-dates").text()
    page.goto(component_origin + "/information")
    page.evaluate("window.__informationMarker = 1")
    page.route(
        "**/information/",
        lambda route: route.fulfill(status=400, body=answer, content_type="text/html"),
    )
    # The dates typed are in order: the complete gate holds Apply while they
    # are out of order, so the out-of-order refusal can only be the answer's
    # here, standing in for a hand-built request that reached the server.
    page.locator("#start").fill("2026-11-01")
    page.locator("#end").fill("2026-11-02")
    before = page.evaluate(APPLY_BOX)
    page.evaluate("""() => {
        window.__focused = [];
        document.addEventListener("focusin", (event) => {
            window.__focused.push(event.target.id || event.target.tagName);
        });
    }""")
    page.get_by_role("button", name="Apply filters").click()
    assert_refused_at_the_dates(page)
    summary = page.locator("[data-table-region] [data-error-summary]")
    expect(summary).to_be_focused()
    focused = page.evaluate("window.__focused")
    assert "start" not in focused and "end" not in focused
    assert abs(page.evaluate(APPLY_BOX) - before) < 1
    assert page.evaluate("window.__informationMarker") == 1
    # What was typed stays; the answer's own values are not written over it.
    expect(page.locator("#start")).to_have_value("2026-11-01")
    expect(page.locator("#end")).to_have_value("2026-11-02")
    page.locator("#end").fill("2026-11-03")
    assert_dates_cleared(page)
    # The browser reports the answer's 400 itself; nothing else may appear.
    assert_clean(page, [error for error in errors if "status of 400" not in error])


def test_refused_hostile_date_stays_text(page, component_origin):
    """A hostile value sent back into a refused date is never markup (#1332)."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/information-refused-hostile")
    expect(page.locator("#start")).to_have_attribute("aria-invalid", "true")
    assert page.locator("#start").get_attribute("value") == (
        '"><script>window.__pwned = 1</script>'
    )
    assert page.evaluate("window.__pwned") is None
    assert page.locator("#table-filters script").count() == 0
    assert_clean(page, errors)


# The Save hint row, the Save button's offset from the form's top, and the
# row's height, in page coordinates (#1332).
SAVE_PLACES = """() => {
  const top = (node) => node.getBoundingClientRect().top + scrollY;
  const form = document.getElementById("information-save").form;
  const row = document.getElementById("information-save-hint").parentElement;
  return [
    top(document.getElementById("information-save")) - top(form),
    top(row) - top(form),
    row.getBoundingClientRect().height,
  ];
}"""


def assert_refused_at_the_confirmation(page):
    """The box is marked and described by the one message in the Save hint
    row, over the gate's hint; the summary links to the box."""
    from playwright.sync_api import expect

    from parishkit.stewardship.reports.information_views import CONFIRM_MESSAGE

    message = str(CONFIRM_MESSAGE)
    box = page.locator("#information-confirm-clear")
    expect(box).to_have_attribute("aria-invalid", "true")
    expect(box).to_have_attribute("aria-describedby", "information-save_error")
    expect(box).not_to_be_checked()
    line = page.locator("#information-save_error")
    expect(line).to_have_text(message)
    expect(line).to_be_visible()
    # The gate holds Save for the same reason, in the same words: its hint
    # is held under the message, so the form says it once.
    assert page.get_by_role("button", name="Save follow-up").is_disabled()
    hint = page.locator("#information-save-hint")
    assert hint.evaluate(
        "node => node.hidden || getComputedStyle(node).visibility === 'hidden'"
    )
    form = page.locator("#information-save").locator("xpath=ancestor::form")
    assert form.inner_text().count(message) == 1
    summary = page.locator("[data-error-summary]")
    expect(summary).to_have_count(1)
    expect(summary.get_by_role("link", name=message)).to_have_attribute(
        "href", "#information-confirm-clear"
    )
    return message


def assert_confirmation_cleared(page, before):
    """Ticking the box clears its mark, message and summary; nothing in the
    form moves, and Save is available again."""
    from playwright.sync_api import expect

    box = page.locator("#information-confirm-clear")
    box.check()
    expect(box).not_to_have_attribute("aria-invalid", "true")
    assert box.get_attribute("aria-describedby") is None
    expect(page.locator("#information-save_error")).to_be_hidden()
    expect(page.locator("[data-error-summary]")).to_have_count(0)
    assert page.get_by_role("button", name="Save follow-up").is_enabled()
    assert page.evaluate(SAVE_PLACES) == pytest.approx(before, abs=0.5)


def test_refused_confirmation_is_shown_at_the_box(page, component_origin, axe_source):
    """A Save refused for clearing completion unconfirmed (#1332) is drawn
    again with what was sent and the error at the confirmation box."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + UNCONFIRMED)
    message = assert_refused_at_the_confirmation(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    expect(page.get_by_label("Follow-up completed", exact=True)).not_to_be_checked()
    expect(page.get_by_label("Follow-up needed", exact=True)).to_be_checked()
    expect(page.get_by_label("Staff notes", exact=True)).to_have_value("Reopen <this>")
    assert page.locator("input[name=expected_version]").input_value() == "2"
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    page.locator("[data-error-summary]").get_by_role("link", name=message).click()
    expect(page.locator("#information-confirm-clear")).to_be_focused()
    assert_confirmation_cleared(page, page.evaluate(SAVE_PLACES))
    assert_clean(page, errors)


def test_in_place_save_refused_at_the_confirmation(page, component_origin):
    """A Save answered with that refusal is swapped in place (no reload):
    the error is at the box, the summary takes focus, and Save stays where
    it was in the form."""
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + UNCONFIRMED).text()
    page.goto(component_origin + ITEM)
    page.evaluate("window.__itemMarker = 1")
    page.route(
        "**" + UPDATE,
        lambda route: route.fulfill(status=400, body=answer, content_type="text/html"),
    )
    # The gate holds Save until the box is ticked, so the refusal can only
    # be the answer's here, standing in for a request built by hand.
    page.get_by_label("Follow-up completed", exact=True).uncheck()
    page.get_by_label("If clearing completion", exact=False).check()
    save = page.evaluate(SAVE_PLACES)[0]
    page.get_by_role("button", name="Save follow-up").click()
    assert_refused_at_the_confirmation(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    assert page.evaluate("window.__itemMarker") == 1
    assert page.evaluate(SAVE_PLACES)[0] == pytest.approx(save, abs=0.5)
    assert_confirmation_cleared(page, page.evaluate(SAVE_PLACES))
    # The browser reports the answer's 400 itself; nothing else may appear.
    assert_clean(page, [error for error in errors if "status of 400" not in error])


def test_unrefused_save_hint_row_takes_no_room(page, component_origin):
    """With no refusal and nothing held, the Save hint row is empty (#1332)."""
    page.goto(component_origin + ITEM)
    height = page.evaluate(SAVE_PLACES)[2]
    assert height == 0
    assert page.locator("#information-save_error").is_hidden()
