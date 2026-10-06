"""Administrator-only combined log screen and its private filters."""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import pytest
from django.http import QueryDict

from parishkit.stewardship.audit.log_rows import LogQuery

from .conftest import no_script_context
from .waits import eventually, has_attribute, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A mark outside the table region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
# Each row's Level cell (the cell right after the Time row header), as its
# icon's kind: a level, or "audit" for an audit record (#601).
LEVEL_CELLS = """() => [...document.querySelectorAll(
    '#table tbody tr > th[scope=row] + td.log-level-cell')].map(cell => {
    const icon = cell.querySelector('svg.level-icon');
    return icon ? icon.getAttribute('class').split('level-icon-')[1]
        : cell.textContent.trim();
})"""
# What Apply says while no kind of entry is ticked (#601).
NONE_HINT = "Tick at least one kind of entry to show."

PAGES = (
    "/logs",
    "/logs-default",
    "/logs-older",
    "/logs-empty",
    "/logs-error-400",
    "/logs-error-503",
    "/logs-dated",
    "/logs-error-zone",
)
# The hint Apply shows while a browser without a known zone has a date.
ZONE_HINT = (
    "Your browser didn't report a time zone, so these dates can't be used. "
    "Clear From and Through, or check your computer's time zone setting."
)


@pytest.mark.parametrize("width", [320, 1280])
def test_logs_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Every state stays readable, escaped and operable at phone width."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/logs")
    # Worker processes are named as such, not as an unknown person.
    visible(page.get_by_role("cell", name="Background worker", exact=False).first)
    # Severity is an icon whose shape, not only its color, tells the levels
    # apart; the icon is hidden from assistive technology and the level's word
    # names the cell instead (#569).
    for word in ("Debug", "Information", "Warning", "Error"):
        assert page.get_by_role("cell", name=word, exact=True).count() == 1
    # Four operational entries and two audit records, each with its icon.
    assert page.locator(".log-level svg.level-icon[aria-hidden=true]").count() == 6
    # The six Show choices carry the same icons beside their words (#601).
    assert page.locator(".log-level-choices svg.level-icon").count() == 6
    # Recorded detail is shown as text, never interpreted as markup.
    assert page.get_by_text("<b>safe</b>", exact=True).count() == 4
    assert page.locator("td b").count() == 0
    entry = page.get_by_role("row", name="dashboard_viewed", exact=False)
    # A resolved actor is named; its identifier moves under Technical details,
    # closed by default, and the entry cross-links by actor and campaign.
    assert entry.get_by_role("cell", name="admin@example.org", exact=False).count()
    details = entry.locator("details.technical-details")
    assert details.get_attribute("open") is None
    assert "00000000-0000-0000-0000-00000000012d" in details.text_content()
    assert entry.get_by_role("button", name="Same actor").count() == 1
    assert entry.get_by_role("button", name="Same campaign").count() == 1
    details.locator("summary").click()
    visible(details.get_by_text("00000000-0000-0000-0000-00000000012d"))
    # An audit record's Level cell is named by its icon's screen-reader text.
    assert entry.get_by_role("cell", name="Audit record", exact=True).count() == 1
    # The chosen filters are kept, including a ticked DEBUG.
    assert page.get_by_label("Debug", exact=True).is_checked()
    assert page.get_by_label("Audit record", exact=True).is_checked()
    # The retired Source field is gone (#601).
    assert page.get_by_label("Source", exact=True).count() == 0
    assert page.get_by_label("Task or request correlation identifier").input_value()
    # The shared navigator, above and below the table, pages by POST forms.
    assert page.get_by_role("button", name="Next", exact=True).count() == 2
    assert page.get_by_role("button", name="Previous", exact=True).count() == 0
    assert page.get_by_text("Page 1 of 2", exact=True).count() == 2
    time = page.get_by_role("columnheader", name="Time", exact=False)
    assert time.get_attribute("aria-sort") == "descending"
    assert time.get_by_role("button", name="sort ascending").count() == 1
    # Audit record is the last of the Show choices; the type field follows.
    page.get_by_label("Audit record", exact=True).focus()
    # The type's help bubble sits between its label and its field. WebKit's
    # default Tab order skips buttons (a macOS setting), so allow either.
    page.keyboard.press("Tab")
    if page.locator(":focus").get_attribute("aria-controls") == "log-event-tip":
        page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "event"

    page.goto(component_origin + "/logs-default")
    # DEBUG is excluded until chosen; audit records are included.
    assert not page.get_by_label("Debug", exact=True).is_checked()
    assert page.get_by_label("Critical", exact=True).is_checked()
    assert page.get_by_label("Audit record", exact=True).is_checked()
    page.goto(component_origin + "/logs-older")
    assert page.get_by_role("button", name="Previous", exact=True).count() == 2
    assert page.get_by_text("Page 2 of 2", exact=True).count() == 2
    # A CRITICAL entry stands out: its own icon on a highlighted row.
    critical = page.locator("tr.log-row-critical")
    assert critical.count() == 1
    assert critical.locator("svg.level-icon-critical").count() == 1
    departed = page.get_by_role("row", name="admin_login", exact=False)
    assert (
        departed.get_by_text("Service, or a former portal user", exact=False).count()
        == 1
    )
    assert departed.get_by_text(
        "A portal user (Administrator, Staff or Ministry leader) signed in"
    ).count()
    assert departed.get_by_role("button", name="Show related entries").count() == 1
    page.goto(component_origin + "/logs-empty")
    visible(page.get_by_text("No matching entries."))
    page.goto(component_origin + "/logs-error-400")
    assert page.get_by_role("alert").count() == 1
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 1
    assert page.get_by_role("link", name="Return to the newest log entries").count()
    assert page.get_by_text("never in a web address", exact=False).count() == 0
    # A query string is refused for where it was sent, not for what it said.
    page.goto(component_origin + "/logs-error-query")
    assert page.get_by_role("alert").count() == 1
    assert page.get_by_text("never in a web address", exact=False).count() == 1
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 0
    # An outage or a denial submitted nothing that could be corrected.
    page.goto(component_origin + "/logs-error-503")
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 0
    assert page.get_by_text("never in a web address", exact=False).count() == 0


def test_log_filters_and_paging_without_scripts(browser_engine, component_origin):
    """Identifiers, the snapshot and the sort post natively, never in the URL."""
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
        page.goto(component_origin + "/logs-default")
        page.get_by_label("Debug", exact=True).check()
        page.get_by_label("Information", exact=True).uncheck()
        page.get_by_label("Audit record", exact=True).uncheck()
        # A type written directly by its owner, which no fixed list would offer.
        page.get_by_label("Event or action type", exact=False).fill("admin_login")
        actor = "1abcdef0-0000-4000-8000-000000000000"
        # Identifier filters are folded away until asked for.
        assert not page.get_by_label("Actor identifier").is_visible()
        page.get_by_text("Filter by identifier", exact=True).click()
        page.get_by_label("Actor identifier").fill(actor)
        # Answer only the form posts; the page itself shares this path, and
        # fallback (not continue_) keeps it passing through no_script_context.
        page.route(
            "**/logs",
            lambda route: (
                route.fulfill(body="Filtered")
                if route.request.method == "POST"
                else route.fallback()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        body = sent.value.post_data
        # A submitted form is marked, so no tick can mean none rather than default.
        assert "applied=yes" in body and "debug=yes" in body and "info=" not in body
        assert "audit=" not in body and "source=" not in body
        assert "event=admin_login" in body
        assert f"actor={actor}" in body
        assert actor not in sent.value.url and "?" not in sent.value.url
        # Let the routed answer finish loading before the next navigation,
        # which it would otherwise interrupt (#623).
        visible(page.get_by_text("Filtered", exact=True))

        page.goto(component_origin + "/logs")
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Next", exact=True).first.click()
        body = sent.value.post_data
        # The snapshot travels with the applied filters, not the edited form.
        assert "through=2026-09-19T15%3A04%3A05.123456%2B00%3A00" in body
        assert "page=2" in body and "sort=newest" in body and "size=25" in body
        assert "debug=yes" in body and "correlation=00000000" in body
        assert "?" not in sent.value.url
        # Let the routed answer finish loading before the next navigation,
        # which it would otherwise interrupt (#623).
        visible(page.get_by_text("Filtered", exact=True))

        page.goto(component_origin + "/logs")
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="sort ascending").click()
        body = sent.value.post_data
        # A heading keeps the filters and snapshot and starts at page one.
        assert "sort=oldest" in body and "through=" in body and "page=" not in body
        assert "correlation=00000000" in body and "?" not in sent.value.url
    finally:
        context.close()


def test_level_icon_column_survives_in_place_sort_and_paging(page, component_origin):
    """The second column shows each entry's level as the level choices' icon,
    named for screen readers and on hover, and keeps doing so after the table
    re-sorts and re-pages in place (#569)."""
    page.goto(component_origin + "/logs")
    headings = page.locator("#table thead th")
    assert "Time" in headings.nth(0).inner_text()
    assert headings.nth(1).inner_text() == "Level"
    newest = ["debug", "audit", "info", "audit", "warning", "error"]
    assert page.evaluate(LEVEL_CELLS) == newest
    warning = page.get_by_role("cell", name="Warning", exact=True)
    assert warning.locator(".log-level").get_attribute("title") == "Warning"
    # The icon is drawn at a readable size in a column no wider than it needs.
    icon = warning.locator("svg").bounding_box()
    assert icon["width"] >= 16 and icon["height"] >= 16
    assert headings.nth(1).bounding_box()["width"] < 120
    # An audit record is its icon too, named the same ways, not the words
    # that widened the column (#601).
    audit = page.get_by_role("cell", name="Audit record", exact=True).first
    assert audit.locator(".log-level").get_attribute("title") == "Audit record"
    assert audit.locator("svg.level-icon-audit").bounding_box()["width"] >= 16
    assert audit.bounding_box()["width"] == headings.nth(1).bounding_box()["width"]
    assert headings.nth(1).bounding_box()["width"] < 80
    # Narrow, but never broken mid-word: "Level" is one line (main's
    # overflow-wrap: anywhere would split it).
    lines = """element => {
        const range = document.createRange();
        range.selectNodeContents(element);
        return new Set([...range.getClientRects()].map(rect => rect.top)).size;
    }"""
    assert headings.nth(1).evaluate(lines) == 1
    page.evaluate(MARK)

    def answer(route):
        """Serve each table post the fixture its control leads to."""
        if route.request.method != "POST":
            route.continue_()
            return
        path = "/logs-older" if "page=2" in route.request.post_data else "/logs-oldest"
        route.fulfill(response=route.fetch(url=component_origin + path, method="GET"))

    page.route("**/logs", answer)
    page.get_by_role("button", name="sort ascending").click()
    has_attribute(
        page.locator("#table th[data-sort-column='time']"), "aria-sort", "ascending"
    )
    eventually(page, LEVEL_CELLS, newest[::-1])
    assert page.evaluate(MARKED) == "kept"
    page.get_by_role("button", name="Next", exact=True).first.click()
    eventually(page, LEVEL_CELLS, ["critical", "audit"])
    assert page.evaluate(MARKED) == "kept"
    critical = page.get_by_role("cell", name="Critical", exact=True)
    assert critical.locator("svg.level-icon-critical").count() == 1


def posted(request):
    """A captured form POST's fields, as the server's own grammar reads them."""
    fields = parse_qs(request.post_data, keep_blank_values=True)
    fields.pop("csrfmiddlewaretoken", None)
    return fields


@pytest.mark.parametrize(
    ("zone", "lower", "upper"),
    [
        # Fall back in Los Angeles: 1 November 2026 lasts 25 hours.
        (
            "America/Los_Angeles",
            datetime(2026, 11, 1, 7, tzinfo=UTC),
            datetime(2026, 11, 2, 8, tzinfo=UTC),
        ),
        # East of UTC, where the local day starts on the previous UTC day.
        (
            "Asia/Kathmandu",
            datetime(2026, 10, 31, 18, 15, tzinfo=UTC),
            datetime(2026, 11, 1, 18, 15, tzinfo=UTC),
        ),
    ],
)
def test_log_dates_are_days_in_the_browser_zone(
    browser_engine, component_origin, zone, lower, upper
):
    """From and Through are the viewer's local days (#558): the page names the
    browser's zone, Apply sends it with the days, and the server's own grammar
    turns them into that zone's midnight-to-midnight interval."""
    context = browser_engine.new_context(timezone_id=zone)
    try:
        page = context.new_page()
        page.goto(component_origin + "/logs-default")
        # The zone as this engine names it: Chromium still reports the
        # catalog alias "Asia/Katmandu", which the server accepts too.
        zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        # Plain labels, and a note naming this computer's zone.
        assert page.get_by_label("From", exact=True).get_attribute("type") == "date"
        note = page.locator("#log-zone-help")
        visible(note.get_by_text(f"time zone: {zone}.", exact=False))
        assert page.get_by_text("UTC day", exact=False).count() == 0
        start = page.get_by_label("From", exact=True)
        assert start.get_attribute("aria-describedby") == "log-zone-help"
        assert page.locator("#table-filters [name=zone]").input_value() == zone
        start.fill("2026-11-01")
        page.get_by_label("Through", exact=True).fill("2026-11-01")
        apply = page.get_by_role("button", name="Apply filters")
        assert apply.is_enabled()
        page.route(
            "**/logs-default",
            lambda route: (
                route.fulfill(body="Filtered")
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            apply.click()
        fields = posted(sent.value)
        assert fields["zone"] == [zone] and "?" not in sent.value.url
        body = QueryDict(sent.value.post_data, mutable=True)
        body.pop("csrfmiddlewaretoken", None)
        assert LogQuery.parse(body).bounds == (lower, upper)
    finally:
        context.close()


def test_paging_sorting_and_export_keep_the_zone(page, component_origin):
    """Applied local days keep their zone through Next, a re-sort and the
    export, all in POST bodies; an in-place apply keeps the browser's zone in
    the filter form and its note (#558)."""
    page.goto(component_origin + "/logs-dated")
    for control in (
        page.get_by_role("button", name="Next", exact=True).first,
        page.get_by_role("button", name="sort ascending"),
    ):
        page.route(
            "**/logs",
            lambda route: (
                route.fulfill(body="Paged")
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            control.click()
        fields = posted(sent.value)
        assert fields["zone"] == ["America/Los_Angeles"]
        assert fields["start"] == fields["end"] == ["2026-09-19"]
        assert "?" not in sent.value.url
        # The request is only sent here: the page still has to read the
        # routed answer and write it in (it is not a table page, so it is
        # shown as returned). Wait for that before the next navigation, or a
        # late document.open() aborts the goto (NS_BINDING_ABORTED in
        # Firefox, #623).
        visible(page.get_by_text("Paged", exact=True))
        page.unroute("**/logs")
        page.goto(component_origin + "/logs-dated")
    export = page.locator("#table-export")
    assert export.locator("[name=zone]").input_value() == "America/Los_Angeles"
    # Applying again in place: the fetched page's fields are synced into the
    # kept form, and the zone stays this browser's.
    page.evaluate(MARK)
    page.route(
        "**/logs",
        lambda route: (
            route.fulfill(
                response=route.fetch(url=component_origin + "/logs-dated", method="GET")
            )
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert posted(sent.value)["zone"] == ["America/Los_Angeles"]
    eventually(page, "document.querySelector('h1').dataset.mark", "kept")
    form = page.locator("#table-filters")
    assert form.locator("[name=zone]").input_value() == "America/Los_Angeles"
    visible(page.locator("#log-zone-help"))


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_log_dates_wait_for_a_known_browser_zone(page, component_origin, reported):
    """A browser that reports no zone, or ICU's unknown zone, cannot apply a
    date (#558): Apply waits with a plain hint while From or Through holds a
    day, and filtering without dates still works."""
    # Init scripts run before the page's own and are not subject to its CSP.
    page.add_init_script(
        """{
        const original = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            return {...original.call(this), timeZone: ZONE};
        };
    }""".replace("ZONE", json.dumps(reported))
    )
    page.goto(component_origin + "/logs-default")
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator("#log-filter-hint")
    assert apply.is_enabled() and not hint.is_visible()
    assert page.locator("#table-filters [name=zone]").input_value() == ""
    assert not page.locator("#log-zone-help").is_visible()
    # The note is never shown with an empty zone ("time zone: .").
    assert not page.get_by_text("time zone: .", exact=False).is_visible()
    for label in ("From", "Through"):
        field = page.get_by_label(label, exact=True)
        assert field.get_attribute("aria-describedby") == "log-filter-hint"
        field.fill("2026-09-19")
        assert apply.is_disabled()
        has_text(hint, ZONE_HINT)
        field.fill("")
        assert apply.is_enabled() and not hint.is_visible()


def test_a_mistyped_event_type_says_why_apply_waits(page, component_origin):
    """The type field's pattern now gates Apply too, with its own hint."""
    page.goto(component_origin + "/logs-default")
    apply = page.get_by_role("button", name="Apply filters")
    page.get_by_label("Event or action type", exact=False).fill("Task Failed")
    assert apply.is_disabled()
    has_text(
        page.locator("#log-filter-hint"),
        "Type the event or action type exactly as the Type column shows it, "
        "for example task_failed.",
    )
    page.get_by_label("Event or action type", exact=False).fill("task_failed")
    assert apply.is_enabled()


def test_dates_without_a_zone_explain_the_refusal(page, component_origin):
    """The server's refusal names the missing zone, not a typing mistake."""
    page.goto(component_origin + "/logs-error-zone")
    assert page.get_by_role("alert").count() == 1
    visible(page.get_by_text("came without your computer's time zone", exact=False))
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 0
    assert page.get_by_text("UTC", exact=False).count() == 0


def test_an_in_place_re_sort_keeps_the_browser_zone(page, component_origin):
    """A table control on a page no date was applied to carries no zone, so
    the page it fetches renders an empty one; the kept filter form must still
    hold this browser's zone, or the next Apply with a date would be refused
    (#558)."""
    page.goto(component_origin + "/logs-default")
    zone = page.locator("#table-filters [name=zone]")
    assert zone.input_value() == "America/Los_Angeles"
    page.evaluate(MARK)
    page.route(
        "**/logs",
        lambda route: (
            route.fulfill(
                response=route.fetch(
                    url=component_origin + "/logs-oldest", method="GET"
                )
            )
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    page.get_by_role("button", name="sort ascending").click()
    has_attribute(
        page.locator("#table th[data-sort-column='time']"), "aria-sort", "ascending"
    )
    assert page.evaluate(MARKED) == "kept"
    assert zone.input_value() == "America/Los_Angeles"
    assert page.locator("#table-filters [name=zone]").count() == 1
    visible(page.locator("#log-zone-help"))


@pytest.mark.parametrize("label", ["From", "Through"])
def test_a_half_typed_date_says_why_apply_waits(page, component_origin, label):
    """A partly typed date fires no input event; as focus leaves it, Apply
    waits with a filter hint, never the gate's "required fields to save"
    one, and a whole date (or none) lets it apply again.

    Engines differ in what typing does to a date control. Chromium and macOS
    WebKit mark a half-typed date as bad input at once, Firefox only when
    focus leaves, and Linux WebKit ignores typed keys (picker only). Each
    outcome is asserted, never skipped: an empty, valid control is simply
    no date, so Apply stays available with no hint.
    """
    page.goto(component_origin + "/logs-default")
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator("#log-filter-hint")
    field = page.get_by_label(label, exact=True)
    field.click()
    page.keyboard.type("09")
    page.get_by_label("Event or action type", exact=False).focus()
    # Read after focus has left, when every engine has settled the state.
    state = field.evaluate("node => [node.value, node.validity.badInput]")
    if state[1]:
        assert apply.is_disabled()
        has_text(hint, f"Enter a whole {label} date, or clear it.")
        assert "save" not in hint.inner_text()
    else:
        # The keys were ignored: no date, so nothing to wait for.
        assert state == ["", False]
        assert apply.is_enabled() and not hint.is_visible()
    field.fill("2026-09-19")
    assert apply.is_enabled() and not hint.is_visible()
    field.fill("")
    page.get_by_label("Event or action type", exact=False).focus()
    assert apply.is_enabled() and not hint.is_visible()


@pytest.mark.parametrize("reported", [None, "", "Etc/Unknown"])
def test_banner_log_link_sends_its_day_only_with_a_zone(
    page, component_origin, reported
):
    """The critical-events banner's System logs form sends its From day with
    the browser's zone; a browser that reports none sends no day at all, so
    the logs open (less narrowly) instead of refusing the date (#558)."""
    if reported is not None:
        page.add_init_script(
            """{
            const original = Intl.DateTimeFormat.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = function () {
                return {...original.call(this), timeZone: ZONE};
            };
        }""".replace("ZONE", json.dumps(reported))
        )
    page.goto(component_origin + "/logs-critical-banner")
    page.route(
        "**/admin/logs",
        lambda route: (
            route.fulfill(body="Logs")
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="View these events in System logs").click()
    fields = posted(sent.value)
    if reported is None:
        assert fields["start"] == ["2026-09-17"]
        assert fields["zone"] == ["America/Los_Angeles"]
    else:
        assert "start" not in fields and fields["zone"] == [""]
    # Critical operational entries only: no audit tick, no retired Source.
    assert fields["critical"] == ["yes"]
    assert "audit" not in fields and "source" not in fields
    body = QueryDict(sent.value.post_data, mutable=True)
    body.pop("csrfmiddlewaretoken", None)
    query = LogQuery.parse(body)  # The server accepts either form.
    assert query.levels == ("CRITICAL",) and not query.audits


def test_no_ticked_kind_says_why_apply_waits(page, component_origin):
    """Apply is unavailable while none of the six Show choices is ticked,
    and says why; ticking any one, Audit record included, lets it apply
    (#601)."""
    page.goto(component_origin + "/logs-default")
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator("#log-filter-hint")
    choices = page.locator(".log-level-choices input[type=checkbox]")
    assert choices.count() == 6 and apply.is_enabled()
    for index in range(6):
        choices.nth(index).uncheck()
    assert apply.is_disabled()
    has_text(hint, NONE_HINT)
    page.get_by_label("Audit record", exact=True).check()
    assert apply.is_enabled() and not hint.is_visible()
    page.get_by_label("Audit record", exact=True).uncheck()
    has_text(hint, NONE_HINT)
    page.get_by_label("Warning", exact=True).check()
    assert apply.is_enabled() and not hint.is_visible()


@pytest.mark.parametrize(
    ("ticks", "fixture", "cells"),
    [
        # Audit records only: every level unticked.
        (("Audit record",), "/logs-audit", ["audit", "audit"]),
        # Operational only: Audit record unticked.
        (("Warning", "Error"), "/logs-operational", ["warning", "error"]),
    ],
)
def test_show_choices_filter_in_place(page, component_origin, ticks, fixture, cells):
    """Ticking only some of the six kinds applies in place in a POST body,
    with the audit tick in place of the retired Source, and the table then
    lists only those kinds (#601)."""
    page.goto(component_origin + "/logs-default")
    page.evaluate(MARK)
    choices = page.locator(".log-level-choices input[type=checkbox]")
    for index in range(choices.count()):
        choices.nth(index).uncheck()
    for label in ticks:
        page.get_by_label(label, exact=True).check()
    page.route(
        "**/logs",
        lambda route: (
            route.fulfill(
                response=route.fetch(url=component_origin + fixture, method="GET")
            )
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    fields = posted(sent.value)
    assert "source" not in fields and "?" not in sent.value.url
    sent_ticks = {name for name, value in fields.items() if value == ["yes"]}
    names = {"Audit record": "audit", "Warning": "warning", "Error": "error"}
    assert sent_ticks == {"applied", *(names[label] for label in ticks)}
    eventually(page, LEVEL_CELLS, cells)
    assert page.evaluate(MARKED) == "kept"
    # The kept form still shows what was applied.
    for label in ticks:
        assert page.get_by_label(label, exact=True).is_checked()
