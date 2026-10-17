"""Contact details on a follow-up request and Export these requests (#1157).

The request page shows its Member's contact details, which the retired
Members joining and leaving lists used to show; the queue's Export these
requests posts the applied queue view, privately, and follows an in-place
Apply without losing the reader's format and time zone. It is a closed
export disclosure (#227), so each check opens it first.
"""

import pytest
from django.urls import reverse

from .test_table_sorting import fulfil_post_with
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""


def contact_value(page, label):
    """The text of one Contact details entry, by its term."""
    section = page.locator("#followup-contact")
    return section.locator(f"dt:text-is('{label}') + dd").inner_text()


def open_export(page):
    """Open the queue's closed Export these requests disclosure (#227),
    checking that it starts closed with its form hidden."""
    summary = page.get_by_text("Export these requests (CSV, XLSX or PDF)", exact=True)
    visible(summary)
    assert not page.get_by_label("Export format").is_visible()
    summary.click()
    visible(page.get_by_label("Export format"))


@pytest.mark.parametrize("width", [320, 1280])
def test_a_request_page_shows_its_members_contact_details(
    page, component_origin, axe_source, width
):
    """A join request shows email, phones, address, gender and age, with an
    unpublished value withheld; a leave shows the Member's current role.
    Both fit a phone's width and pass the accessibility checks."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/followup-item")
    visible(page.get_by_role("heading", name="Contact details"))
    assert contact_value(page, "Email") == "member<one>@example.org"
    assert contact_value(page, "Phones") == "Not published"
    assert "1 Example <Street>" in contact_value(page, "Known primary Family address")
    assert contact_value(page, "Age").startswith("66")
    assert (
        page.locator("#followup-contact dt", has_text="Current Ministry role").count()
        == 0
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.goto(component_origin + "/followup-item-leave")
    assert contact_value(page, "Current Ministry role") == "Chairperson"
    assert contact_value(page, "Email") == "Not published"
    assert contact_value(page, "Phones").startswith("mobile: ")
    assert page.locator("#followup-contact dt", has_text="Gender").count() == 0
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_export_these_requests_posts_the_applied_view(page, component_origin):
    """The file's form carries the queue's filters and search in its POST
    body, never in the address; this browser's zone is offered first."""
    page.goto(component_origin + "/followup-queue")
    open_export(page)
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    timezone = page.get_by_label("Export timezone")
    if zone != "UTC":
        assert timezone.input_value() == zone
    page.get_by_label("Export format").select_option("xlsx")
    page.route(
        "**" + reverse("admin:ministry_followup_export"),
        lambda route: route.fulfill(body="Downloaded"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Download these requests").click()
    body = sent.value.post_data
    assert "search=Example" in body and "ministry=9" in body
    assert "format=xlsx" in body and "state=unresolved" in body
    assert "page=" not in body and "size=" not in body
    assert "?" not in sent.value.url
    visible(page.get_by_text("Downloaded", exact=True))


def test_export_follows_an_in_place_apply(page, component_origin):
    """An in-place Apply updates the file's filters and count; the reader's
    chosen format stays, and nothing on the page moves to a new address."""
    page.goto(component_origin + "/followup-queue")
    open_export(page)
    page.get_by_label("Export format").select_option("pdf")
    form = page.locator("#table-export")
    assert form.locator("input[name=search]").input_value() == "Example"
    fulfil_post_with(
        page,
        "**" + reverse("admin:ministry_report"),
        component_origin,
        lambda request: "/followup-all",
    )
    page.get_by_label("Search Member or Ministry name").fill("")
    page.get_by_role("button", name="Apply filters").click()
    # The hidden field's value is set by the in-place refresh, not its
    # attribute, so it is read as the page sees it.
    eventually(
        page, "document.querySelector('#table-export input[name=search]').value", ""
    )
    assert form.locator("input[name=ministry]").input_value() == ""
    assert page.get_by_label("Export format").input_value() == "pdf"
    assert "51" in page.locator("#followup-export-count").inner_text()


def test_closed_export_follows_an_in_place_apply(page, component_origin):
    """With the Export disclosure closed, an in-place Apply still updates the
    form's hidden filters and the count, and the disclosure stays closed."""
    page.goto(component_origin + "/followup-queue")
    panel = page.locator("details.export-panel")
    assert panel.get_attribute("open") is None
    fulfil_post_with(
        page,
        "**" + reverse("admin:ministry_report"),
        component_origin,
        lambda request: "/followup-all",
    )
    page.get_by_label("Search Member or Ministry name").fill("")
    page.get_by_role("button", name="Apply filters").click()
    eventually(
        page, "document.querySelector('#table-export input[name=search]').value", ""
    )
    assert page.locator("#table-export input[name=ministry]").input_value() == ""
    # The count is hidden while closed, so read its text, not its rendering.
    assert "51" in page.locator("#followup-export-count").text_content()
    assert panel.get_attribute("open") is None


def test_pdf_is_unavailable_above_its_limit(page, component_origin):
    """With more requests than one PDF may hold, PDF cannot be chosen and
    the export line says why; CSV and XLSX still can (#1157)."""
    page.goto(component_origin + "/followup-over")
    open_export(page)
    menu = page.get_by_label("Export format")
    assert menu.locator("option[value=pdf]").is_disabled()
    assert not menu.locator("option[value=xlsx]").is_disabled()
    visible(page.get_by_text("The PDF is limited to 1,000 requests", exact=False))
