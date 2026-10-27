"""Past campaigns (#1018, slice 4) in a browser.

The pages and POST answers are ``past_campaign_components``'. Review waits
for a file no larger than the limit; it posts the file with the page's
token in the X-CSRFToken header and opens the shared review dialog with the
Review card (#750); a replacement holds Apply for its acknowledgement;
Apply closes the dialog and redraws the table and a fresh upload form in
place, with the status line under the form; a file already imported shows
Apply greyed with its reason; a refused file lists its problems in the
dialog, and one the server finds too large is the file field's error;
Remove redraws the table in place. Nothing reloads
the page or moves the form.
"""

import re

import pytest

from .past_campaign_components import MAX_BYTES, PAGE
from .test_components import axe_violations
from .waits import eventually, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
APPLIED = re.compile(r"^Replaced 2025\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$")


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def years(page):
    """The years the imported-campaigns table lists, top to bottom."""
    table = page.locator("table", has=page.locator("#past-campaigns-caption"))
    return table.locator("tbody th[scope=row]").all_inner_texts()


def review_button(page):
    """The Add an archive file form's Review button."""
    return page.locator("#archive-upload").get_by_role("button", name="Review")


def places(page):
    """The Review button's and its hint line's places within their form.

    Within the form, since Apply's redrawn table above may change height.
    """
    return page.evaluate(
        """["#archive-upload [type=submit]", "#archive-review-hint"].map((s) => {
            const form = document.querySelector("#archive-upload");
            const box = document.querySelector(s).getBoundingClientRect();
            return [box.top - form.getBoundingClientRect().top, box.height];
        })"""
    )


def choose(page, tmp_path, size=100):
    """Choose a small stand-in workbook of ``size`` bytes.

    Each size gets its own file name: a browser fires no change event when
    the same file is chosen again.
    """
    path = tmp_path / f"stewardship-2025-{size}.xlsx"
    path.write_bytes(b"PK" + b"x" * (size - 2))
    page.locator("#archive-file").set_input_files(str(path))


@pytest.mark.parametrize(
    "path",
    [
        PAGE,
        f"{PAGE}-done",
        f"{PAGE}-empty",
        f"{PAGE}-year",
        f"{PAGE}-archive-closed",
        f"{PAGE}-archive-unavailable",
    ],
)
@pytest.mark.parametrize("width", [320, 1280])
def test_page_is_accessible_and_fits(page, component_origin, axe_source, path, width):
    """No axe violation and no sideways page scroll; tables scroll in place."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_review_waits_for_a_file_within_the_limit(page, component_origin, tmp_path):
    """Review is unavailable, with the reason after it, until a file no larger
    than the limit is chosen; the reason's line keeps its height."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    failures = watch(page)
    button = review_button(page)
    hint = page.locator("#archive-review-hint")
    assert button.is_disabled()
    assert "Choose an archive file" in hint.inner_text()
    height = hint.bounding_box()["height"]
    choose(page, tmp_path, size=MAX_BYTES + 1)
    eventually(
        page,
        "document.querySelector('#archive-review-hint').textContent",
        "Choose a file no larger than 20 MB.",
    )
    assert button.is_disabled()
    choose(page, tmp_path, size=MAX_BYTES)
    eventually(
        page, "!document.querySelector('#archive-upload [type=submit]').disabled"
    )
    expect(hint).to_have_text("")
    assert hint.bounding_box()["height"] == height
    assert not failures


def dialog_of(page, name):
    """The review dialog, once it is open, named ``name``."""
    dialog = page.get_by_role("dialog", name=name)
    visible(dialog)
    return dialog


def form_place(page):
    """The Review button's and the status line's tops within their form.

    Within the form, since Apply's redrawn table above may change height.
    """
    return page.evaluate(
        """["#archive-upload [type=submit]", "#archive-outcome"].map((s) => {
            const form = document.querySelector("#archive-upload");
            const box = document.querySelector(s).getBoundingClientRect();
            return box.top - form.getBoundingClientRect().top;
        })"""
    )


def in_view(page, selector):
    """Whether the element is wholly inside the window."""
    return page.evaluate(
        """(s) => {
            const box = document.querySelector(s).getBoundingClientRect();
            return box.top >= 0 && box.bottom <= window.innerHeight;
        }""",
        selector,
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_review_and_apply_in_the_dialog(page, component_origin, tmp_path, width):
    """Review sends the file multipart with the header token and opens the
    dialog on its title with the card; the replacement holds Apply until its
    box is ticked; Apply closes the dialog, redraws the table, and the status
    line under the form says what was applied and takes focus, in view. The
    page never reloads, and the Review button keeps its place in the form."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + f"{PAGE}-apply")
    failures = watch(page)
    assert years(page) == ["2026", "2025"]
    page.locator("#archive-outcome").scroll_into_view_if_needed()
    choose(page, tmp_path)
    before = form_place(page)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        review_button(page).click()
    request = sent.value
    assert request.headers.get("x-csrftoken") == "a" * 64
    assert request.headers["content-type"].startswith("multipart/form-data")
    dialog = dialog_of(page, "Replace 2025?")
    eventually(page, f"{FOCUSED} === 'archive-review-dialog-title'")
    assert "corrected.xlsx" in dialog.inner_text()
    # The flagged note is marked for a look before Apply.
    assert "Check" in dialog.locator("dt", has_text="note.02").inner_text()
    apply = dialog.get_by_role("button", name="Apply")
    assert apply.is_disabled()
    assert "Tick the box" in dialog.locator("[data-confirm-status]").inner_text()
    assert dialog.evaluate("(d) => d.scrollWidth <= d.clientWidth + 1")
    dialog.get_by_role(
        "checkbox", name="This replaces the 2025 figures already imported"
    ).check()
    eventually(
        page,
        "!document.querySelector("
        "'#archive-review-dialog [data-confirm-accept]').disabled",
    )
    with page.expect_request(lambda request: request.method == "POST") as posted:
        apply.click()
    body = posted.value.post_data or ""
    assert "action=confirm" in body and "replace=yes" in body
    assert "preview=00000000-0000-0000-0000-000000000001" in body
    hidden(dialog)
    line = page.locator("#archive-outcome")
    eventually(page, "document.getElementById('archive-outcome').textContent !== ''")
    assert APPLIED.search(line.inner_text())
    assert page.evaluate(FOCUSED) == "archive-outcome"
    # The table redrew: 2025 is now the final, replacing file.
    table = page.locator("table", has=page.locator("#past-campaigns-caption"))
    row = table.locator("tbody tr").nth(1)
    assert row.locator("th").inner_text() == "2025"
    assert "Final" in row.inner_text()
    # The Review button keeps its place in the form; the status line is said
    # where the reader is, without a jump to the top or the foot.
    assert same_place(form_place(page)[0], before[0])
    assert in_view(page, "#archive-outcome")
    assert in_view(page, "#archive-upload [type=submit]")
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_apply_clears_the_file_without_moving_the_form(
    page, component_origin, tmp_path
):
    """After Apply the file choice is empty and Review is held with its hint,
    as on a fresh page; Keep Family rows is ticked again, and the Review
    button and its hint line do not move within the form."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{PAGE}-apply")
    failures = watch(page)
    hint = page.locator("#archive-review-hint")
    choose(page, tmp_path)
    page.get_by_role("checkbox", name="Keep Family rows").uncheck()
    review_button(page).click()
    dialog = dialog_of(page, "Replace 2025?")
    dialog.get_by_role(
        "checkbox", name="This replaces the 2025 figures already imported"
    ).check()
    before = places(page)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.querySelector('#archive-file').files.length === 0")
    assert review_button(page).is_disabled()
    assert "Choose an archive file" in hint.inner_text()
    assert page.get_by_role("checkbox", name="Keep Family rows").is_checked()
    assert places(page) == before
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_cancel_keeps_the_file_and_changes_nothing(page, component_origin, tmp_path):
    """Cancel closes the dialog with nothing posted; the chosen file and Keep
    Family rows stay, so Review can be chosen again at once."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{PAGE}-apply")
    failures = watch(page)
    choose(page, tmp_path)
    page.get_by_role("checkbox", name="Keep Family rows").uncheck()
    review_button(page).click()
    dialog = dialog_of(page, "Replace 2025?")
    posts = []
    page.on("request", lambda r: posts.append(r) if r.method == "POST" else None)
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert posts == []
    assert page.evaluate("document.querySelector('#archive-file').files.length") == 1
    assert not page.get_by_role("checkbox", name="Keep Family rows").is_checked()
    assert review_button(page).is_enabled()
    assert years(page) == ["2026", "2025"]
    assert page.locator("#archive-outcome").inner_text() == ""
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_a_file_already_imported_shows_apply_unavailable(
    page, component_origin, tmp_path, width
):
    """Apply stays in view, greyed (aria-disabled), described by the dialog's
    status line, which says who imported the file and when (Administrator
    decision 12); selecting it posts nothing and the dialog stays open."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + f"{PAGE}-already")
    failures = watch(page)
    choose(page, tmp_path)
    review_button(page).click()
    dialog = dialog_of(page, "2026 is already imported")
    apply = dialog.get_by_role("button", name="Apply")
    visible(apply)
    assert apply.get_attribute("aria-disabled") == "true"
    status = dialog.locator("[data-confirm-status]")
    reason = status.inner_text()
    assert reason.startswith("Unavailable: imported by admin@example.org on ")
    assert apply.get_attribute("aria-describedby") == status.get_attribute("id")
    assert status.get_attribute("role") == "status"
    # The reason is in the status line only, not repeated in the card.
    assert "Unavailable:" not in dialog.locator("[data-review-body]").inner_text()
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    apply.click(force=True)
    page.wait_for_timeout(300)
    assert posts == [] and dialog.evaluate("(node) => node.open")
    assert status.inner_text() == reason
    visible(dialog.get_by_role("button", name="Cancel"))
    assert page.evaluate("window.notReloaded") is True
    assert not failures


def test_a_file_too_large_for_the_server_is_a_field_error(
    page, component_origin, tmp_path
):
    """The server's own size refusal (400, the page again) leaves the dialog
    shut and marks the file field, its message placed before the field and
    in the error summary (UX conventions, "Review dialog", Uploads)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{PAGE}-too-large")
    failures = watch(page)
    choose(page, tmp_path)
    review_button(page).click()
    field = page.locator("#archive-file")
    eventually(page, "document.querySelector('#archive-file').ariaInvalid", "true")
    error = page.locator("#archive-file_error")
    visible(error)
    assert error.inner_text() == (
        "This file is larger than 20 MB. Choose a file no larger than 20 MB."
    )
    assert "archive-file_error" in field.get_attribute("aria-describedby").split()
    assert not page.locator("#archive-review-dialog").evaluate("(node) => node.open")
    summary = page.locator("[data-error-summary]")
    visible(summary)
    assert "This file is larger than 20 MB." in summary.inner_text()
    assert page.evaluate("window.notReloaded") is True
    assert not failures


def test_a_refused_file_lists_its_problems_in_the_dialog(
    page, component_origin, tmp_path
):
    """The problems name sheets and cells, with how many more there are, in
    the dialog, which offers no Apply; the form keeps the chosen file."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{PAGE}-refused")
    failures = watch(page)
    choose(page, tmp_path)
    review_button(page).click()
    dialog = dialog_of(page, "Review archive file")
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    text = error.inner_text()
    assert "This file can't be imported" in text
    assert "families!J2: a formula is not allowed" in text
    assert "3 more problems are not shown." in text
    assert not dialog.get_by_role("button", name="Apply").is_visible()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.evaluate("document.querySelector('#archive-file').files.length") == 1
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_refused_apply_shows_in_the_dialog(page, component_origin, tmp_path):
    """An expired preview's Apply is said in the dialog; the table is not
    redrawn and the status line stays empty."""

    def refuse(route):
        """Refuse Apply as the view does, with its refusal fragment."""
        route.fulfill(
            status=409,
            content_type="text/html",
            body=(
                "<div data-review-refusal><p>This was not applied.</p><ul><li>"
                "This review expired or was already applied; choose Review "
                "again</li></ul></div>"
            ),
        )

    page.route("**/admin/parish/past-campaigns/imports/", refuse)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{PAGE}-apply")
    failures = watch(page)
    choose(page, tmp_path)
    review_button(page).click()
    dialog = dialog_of(page, "Replace 2025?")
    dialog.get_by_role(
        "checkbox", name="This replaces the 2025 figures already imported"
    ).check()
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "This review expired or was already applied" in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#archive-outcome").inner_text() == ""
    assert years(page) == ["2026", "2025"]
    assert page.evaluate("window.notReloaded === true")
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_the_open_dialog_is_accessible(
    page, component_origin, axe_source, tmp_path, width
):
    """No axe violation with the Review card open, at phone and desktop width."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + f"{PAGE}-apply")
    failures = watch(page)
    choose(page, tmp_path)
    review_button(page).click()
    dialog_of(page, "Replace 2025?")
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not failures


def test_remove_asks_then_redraws_the_table(page, component_origin):
    """Remove names the year in the dialog; confirming redraws in place."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    failures = watch(page)
    remove = page.get_by_role("button", name="Remove 2025")
    assert remove.get_attribute("value") == "2025:2"
    remove.click()
    dialog = page.locator("dialog#past-campaign-remove")
    visible(dialog)
    assert dialog.locator("h2").inner_text() == "Remove 2025?"
    assert "can be imported again later" in dialog.inner_text()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog.get_by_role("button", name="Remove", exact=True).click()
    assert "archive=2025%3A2" in (sent.value.post_data or "")
    hidden(dialog)
    eventually(page, "document.querySelectorAll('#table tbody tr').length", 1)
    assert years(page) == ["2026"]
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_review_is_unavailable_without_a_campaign_year(page, component_origin):
    """The reason, with its link to Campaign settings, names the button."""
    page.goto(component_origin + f"{PAGE}-year")
    button = review_button(page)
    assert button.is_disabled()
    assert button.get_attribute("aria-describedby") == "archive-year-reason"
    reason = page.locator("#archive-year-reason")
    assert "can't be worked out" in reason.inner_text()
    link = reason.get_by_role("link", name="Set the year label in Campaign settings")
    assert link.get_attribute("href") == "/admin/campaign/settings/"


def test_the_provenance_tip_opens_from_the_keyboard(page, component_origin):
    """Each Campaign Families count explains where it was counted from."""
    page.goto(component_origin + PAGE)
    button = page.locator("#table .toggletip-button").first
    button.focus()
    page.keyboard.press("Enter")
    bubble = page.locator("#provenance-2026")
    visible(bubble)
    assert bubble.inner_text() == (
        "Counted from: Families issued <campaign> codes, from an invented snapshot"
    )


def test_the_campaign_archive_section_offers_or_explains_the_download(
    page, component_origin
):
    """Download campaign archive posts its key; unavailable, it says why."""
    page.goto(component_origin + PAGE)
    section = page.locator("#campaign-archive")
    button = section.get_by_role("button", name="Download campaign archive")
    assert button.is_enabled()
    text = page.locator("[aria-labelledby=campaign-archive-heading]").inner_text()
    assert "A partial archive of Stewardship 2027, through day 9" in text
    assert "stewardship-2027-partial.xlsx" in text
    assert section.get_attribute("action") == "/admin/parish/past-campaigns/archive/"
    # Closed before its end date: final at once, through its last full day
    # (Administrator decision, 2026-10-13, #1018), with no wait.
    page.goto(component_origin + f"{PAGE}-archive-closed")
    button = page.locator("#campaign-archive").get_by_role(
        "button", name="Download campaign archive"
    )
    assert button.is_enabled()
    text = page.locator("[aria-labelledby=campaign-archive-heading]").inner_text()
    assert "A final archive of Stewardship 2027, through day 9" in text
    assert "its last full day: the campaign was closed before its end date" in text
    assert "stewardship-2027.xlsx" in text
    page.goto(component_origin + f"{PAGE}-archive-unavailable")
    button = page.locator("#campaign-archive").get_by_role(
        "button", name="Download campaign archive"
    )
    assert button.is_disabled()
    assert button.get_attribute("aria-describedby") == "campaign-archive-reason"
    assert (
        "no Family has responded"
        in page.locator("#campaign-archive-reason").inner_text()
    )
    # Closed today with a response today: unavailable until tomorrow.
    page.goto(component_origin + f"{PAGE}-archive-tomorrow")
    button = page.locator("#campaign-archive").get_by_role(
        "button", name="Download campaign archive"
    )
    assert button.is_disabled()
    assert page.locator("#campaign-archive-reason").inner_text() == (
        "Available tomorrow: the campaign closed today, and today's responses "
        "are not in a complete day yet."
    )
