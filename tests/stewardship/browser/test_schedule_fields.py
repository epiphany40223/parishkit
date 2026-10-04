"""Mail schedule rows show, clear and offer only what the chosen mail type uses."""

from urllib.parse import parse_qs

import pytest

from .test_components import axe_violations
from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The blank new row follows the one saved initial invitation.
NEW = "schedules-1-"
FIELDS = ("date", "time", "weekday", "template_version")
SHOWN = {
    "": set(),
    "initial": {"date", "time", "template_version"},
    "reminder": {"date", "time", "template_version"},
    "daily_digest": {"time", "template_version"},
    "weekly_digest": {"weekday", "time", "template_version"},
}


def group(page, field, prefix=NEW):
    """The wrapper of one schedule field: label, help, errors and control."""
    control = f'[name="{prefix}{field}"]'
    return page.locator(f'[data-schedule-field="{field}"]:has({control})')


def shown(page, prefix=NEW):
    """The names of the row's optional fields that are currently visible."""
    return {field for field in FIELDS if group(page, field, prefix).is_visible()}


def offered(page):
    """The mail types of the emails the new row's email list offers."""
    return page.locator(f'[name="{NEW}template_version"] option').evaluate_all(
        "options => options.map(option => option.dataset.kind || '')"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_rows_show_only_the_fields_of_their_mail_type(
    page, component_origin, axe_source, width
):
    """Before a type is chosen only the type shows; switching clears stale values."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-schedules-mail")
    # The page's explanation waits in its closed About panel (#227).
    page.locator("details[data-about-page] > summary").click()
    visible(page.get_by_role("heading", name="How mail schedules work"))
    assert "October 1, 2054 – October 31, 2054" in page.inner_text("main")
    # The saved initial invitation keeps its fixed type and its own fields.
    assert page.locator('[name="schedules-0-kind"]').is_disabled()
    assert shown(page, "schedules-0-") == SHOWN["initial"]
    assert shown(page) == set()
    kind = page.locator(f'[name="{NEW}kind"]')
    for choice in ("weekly_digest", "initial", "reminder", "daily_digest"):
        kind.select_option(choice)
        assert shown(page) == SHOWN[choice], choice
        assert offered(page) == ["", choice]
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        assert axe_violations(page, axe_source) == []
    # Values a mail type does not use are cleared when their field is hidden.
    kind.select_option("weekly_digest")
    page.locator(f'[name="{NEW}weekday"]').select_option("0")
    page.locator(f'[name="{NEW}time"]').fill("08:30:00")
    template = page.locator(f'[name="{NEW}template_version"]')
    template.select_option(index=1)
    kind.select_option("reminder")
    assert page.locator(f'[name="{NEW}weekday"]').input_value() == ""
    # The email chosen for another type is no longer offered or selected.
    assert template.input_value() == ""
    assert page.locator(f'[name="{NEW}time"]').input_value() == "08:30:00"
    page.locator(f'[name="{NEW}date"]').fill("2054-10-05")
    kind.select_option("daily_digest")
    assert page.locator(f'[name="{NEW}date"]').input_value() == ""
    kind.select_option("")
    assert shown(page) == set()
    assert page.locator(f'[name="{NEW}time"]').input_value() == ""
    assert not failures


def test_without_script_every_field_shows(browser_engine, component_origin):
    """Progressive enhancement: with JavaScript off nothing is hidden."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/setup-schedules-mail")
        assert shown(page) == set(FIELDS)
        assert offered(page) == [
            "",
            "initial",
            "reminder",
            "daily_digest",
            "weekly_digest",
        ]
    finally:
        context.close()


def test_a_reported_field_stays_visible_until_the_type_changes(
    page, component_origin, axe_source
):
    """The server's weekday error is readable; a new type then hides and clears it."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/setup-schedules-error")
    prefix = "schedules-0-"
    weekday = group(page, "weekday", prefix)
    visible(weekday)
    assert "leave it at Not weekly" in weekday.inner_text()
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == "0"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.locator(f'[name="{prefix}kind"]').select_option("daily_digest")
    page.locator(f'[name="{prefix}kind"]').select_option("initial")
    hidden(weekday)
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == ""
    assert shown(page, prefix) == SHOWN["initial"]


def posted(page, component_origin, path):
    """Submit the page's form and return the posted fields without a server."""
    fields = []

    def capture(route):
        """Record the POST body, then answer with an empty page."""
        if route.request.method == "POST":
            fields.append(parse_qs(route.request.post_data, keep_blank_values=True))
            route.fulfill(status=200, content_type="text/html", body="<p>Saved</p>")
        else:
            route.continue_()

    page.route(component_origin + path, capture)
    with page.expect_response(lambda response: response.request.method == "POST"):
        page.locator("form button[type=submit]").last.click()
    return {name: values[0] for name, values in fields[0].items()}


@pytest.mark.parametrize("path", ["/setup-schedules-mail", "/schedule-settings"])
def test_add_and_remove_new_schedule_rows_before_saving(
    page, component_origin, axe_source, path
):
    """New rows clone the empty form, show only their type's fields and renumber."""
    page.set_viewport_size({"width": 320, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    total = page.locator('[name="schedules-TOTAL_FORMS"]')
    first = int(total.input_value())
    add = page.get_by_role("button", name="Add another schedule")
    add.click()
    one = f"schedules-{first}-"
    assert page.evaluate("document.activeElement.name") == one + "kind"
    assert "added" in page.locator("[data-schedule-status]").inner_text()
    add.click()
    two = f"schedules-{first + 1}-"
    assert page.evaluate("document.activeElement.name") == two + "kind"
    assert total.input_value() == str(first + 2)
    # Mail-type show/hide applies to the added rows too.
    assert shown(page, two) == set()
    page.locator(f'[name="{two}kind"]').select_option("weekly_digest")
    assert shown(page, two) == SHOWN["weekly_digest"]
    page.locator(f'[name="{two}weekday"]').select_option("0")
    page.locator(f'[name="{two}time"]').fill("08:30:00")
    page.locator(f'[name="{one}kind"]').select_option("daily_digest")
    assert shown(page, one) == SHOWN["daily_digest"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Removing the first added row renumbers the second into its place.
    page.get_by_role("button", name="Remove this new schedule").first.click()
    assert page.evaluate("document.activeElement.dataset.scheduleAdd") == ""
    assert total.input_value() == str(first + 1)
    assert page.locator(f'[name="{one}kind"]').input_value() == "weekly_digest"
    assert page.locator(f'[name="{two}kind"]').count() == 0
    assert shown(page, one) == SHOWN["weekly_digest"]
    fields = posted(page, component_origin, path)
    assert fields["schedules-TOTAL_FORMS"] == str(first + 1)
    assert fields[one + "kind"] == "weekly_digest"
    assert fields[one + "weekday"] == "0" and fields[one + "time"] == "08:30:00"
    assert not any(name.startswith(two) for name in fields)
    assert not any("__prefix__" in name for name in fields)
    assert not failures


def test_without_script_there_is_no_add_button(browser_engine, component_origin):
    """Progressive enhancement: each save still offers one blank row."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/setup-schedules-mail")
        assert not page.get_by_role("button", name="Add another schedule").count()
        assert page.locator("[data-schedule-add]").is_hidden()
    finally:
        context.close()
