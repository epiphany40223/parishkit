"""Mail schedule rows show, clear and offer only what the chosen mail type uses."""

import pytest

from .test_components import axe_violations

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
    assert page.get_by_role("heading", name="How mail schedules work").is_visible()
    assert "October 1, 2026 – October 31, 2026" in page.inner_text("main")
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
    page.locator(f'[name="{NEW}date"]').fill("2026-10-05")
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
    assert weekday.is_visible()
    assert "leave it at Not weekly" in weekday.inner_text()
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == "0"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.locator(f'[name="{prefix}kind"]').select_option("daily_digest")
    page.locator(f'[name="{prefix}kind"]').select_option("initial")
    assert weekday.is_hidden()
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == ""
    assert shown(page, prefix) == SHOWN["initial"]
