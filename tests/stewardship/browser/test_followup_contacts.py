"""Contact details on a follow-up request and Export these requests (#1157).

The request page shows its Member's contact details, which the retired
Members joining and leaving lists used to show; the queue's Export these
requests posts the applied queue view, privately, and follows an in-place
Apply. It is the shared row of export buttons below the queue (#1679), each
posting its format and the browser's zone unasked.
"""

from urllib.parse import quote

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


def export_row(page):
    """The queue's Export these requests row (#1679), apart from the packet's."""
    row = page.get_by_role("group", name="Export these requests")
    visible(row)
    return row


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
    body, never in the address, with this browser's zone, unasked."""
    page.goto(component_origin + "/followup-queue")
    row = export_row(page)
    assert page.get_by_label("Export timezone").count() == 0
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    page.route(
        "**" + reverse("admin:ministry_followup_export"),
        lambda route: route.fulfill(body="Downloaded"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        row.get_by_role("button", name="Export XLSX").click()
    body = sent.value.post_data
    assert "search=Example" in body and "ministry=9" in body
    assert "format=xlsx" in body and "stage=to_review" in body
    assert "browser_timezone=" + quote(zone, safe="") in body
    assert "page=" not in body and "size=" not in body
    assert "?" not in sent.value.url
    visible(page.get_by_text("Downloaded", exact=True))


def test_export_follows_an_in_place_apply(page, component_origin):
    """An in-place Apply updates the file's filters and count, and nothing on
    the page moves to a new address."""
    page.goto(component_origin + "/followup-queue")
    export_row(page)
    form = page.locator("#table-export")
    assert form.locator("input[name=search]").input_value() == "Example"
    zone = form.locator("input[name=browser_timezone]").input_value()
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
    assert form.locator("input[name=browser_timezone]").input_value() == zone
    # No count line (R37); the PDF limit line stays hidden below the limit.
    assert page.locator("#followup-export-pdf-limit").is_hidden()


def test_pdf_is_unavailable_above_its_limit(page, component_origin):
    """With more requests than one PDF may hold, Export PDF is unavailable
    and the line below the row says why; CSV and XLSX still are not (#1157)."""
    page.goto(component_origin + "/followup-over")
    row = export_row(page)
    pdf = row.get_by_role("button", name="Export PDF")
    assert pdf.is_disabled()
    assert pdf.get_attribute("aria-describedby") == "followup-export-pdf-limit"
    assert row.get_by_role("button", name="Export XLSX").is_enabled()
    visible(page.get_by_text("The PDF is limited to 1,000 requests", exact=False))
