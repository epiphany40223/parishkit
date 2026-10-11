"""Past campaigns (#1018, slice 4) in a browser.

The pages and POST answers are ``past_campaign_components``'. Review waits
for a file no larger than the limit; it posts the file with the page's
token in the X-CSRFToken header and shows the Review card in place; a
replacement waits for its acknowledgement; Apply and Remove redraw the
table in place; a refused file lists its problems in place. Nothing
reloads the page.
"""

import pytest

from .past_campaign_components import CARD, MAX_BYTES, PAGE
from .test_components import axe_violations
from .waits import eventually, has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


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


def choose(page, tmp_path, size=100):
    """Choose a small stand-in workbook of ``size`` bytes.

    Each size gets its own file name: a browser fires no change event when
    the same file is chosen again.
    """
    path = tmp_path / f"stewardship-2025-{size}.xlsx"
    path.write_bytes(b"PK" + b"x" * (size - 2))
    page.locator("#archive-file").set_input_files(str(path))


@pytest.mark.parametrize("path", [PAGE, CARD, f"{PAGE}-empty", f"{PAGE}-year"])
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
    assert hint.inner_text().strip() == ""
    assert hint.bounding_box()["height"] == height
    assert not failures


def test_review_and_apply_act_in_place(page, component_origin, tmp_path):
    """Review sends the file with the header token and shows the card; the
    replacement waits for its box; Apply redraws the table and says what it
    did. The page never reloads."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    failures = watch(page)
    assert years(page) == ["2026", "2025"]
    choose(page, tmp_path)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        review_button(page).click()
    request = sent.value
    assert request.headers.get("x-csrftoken") == "a" * 64
    assert request.headers["content-type"].startswith("multipart/form-data")
    card = page.locator("#archive-review section[data-review-of]")
    visible(card)
    heading = card.locator("h2")
    assert heading.inner_text() == "Replace 2025"
    eventually(page, "document.activeElement.id === 'archive-review-title'")
    assert page.url.endswith(CARD.split("/", 1)[1] + "#archive-review")
    # The flagged note is marked for a look before Apply.
    assert "Check" in card.locator("dt", has_text="note.02").inner_text()
    apply = card.get_by_role("button", name="Apply")
    assert apply.is_disabled()
    box = card.get_by_role(
        "checkbox", name="This replaces the 2025 figures already imported"
    )
    box.check()
    eventually(
        page, "!document.querySelector('#archive-review form [type=submit]').disabled"
    )
    apply.click()
    result = page.locator("#archive-review h2")
    has_attribute(result, "id", "archive-applied-title")
    assert result.inner_text() == "Replaced 2025"
    # The table redrew: 2025 is now the final, replacing file.
    table = page.locator("table", has=page.locator("#past-campaigns-caption"))
    final = table.locator("tbody tr").nth(1)
    assert final.locator("th").inner_text() == "2025"
    assert "Final" in final.inner_text()
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_changing_the_form_withdraws_the_card(page, component_origin, tmp_path):
    """Choosing another file after Review greys the card and removes Apply."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + CARD)
    failures = watch(page)
    card = page.locator("#archive-review section")
    visible(card.get_by_role("button", name="Apply"))
    page.locator("#archive-upload input[name=keep_family_rows]").uncheck()
    has_attribute(card, "data-review-stale", "")
    assert card.get_by_role("button", name="Apply").count() == 0
    assert "Choose Review again" in card.inner_text()
    assert not failures


def test_a_refused_file_lists_its_problems_in_place(page, component_origin, tmp_path):
    """The problems name sheets and cells, with how many more there are."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{PAGE}-refused")
    failures = watch(page)
    choose(page, tmp_path)
    review_button(page).click()
    summary = page.locator("#archive-review [data-error-summary]")
    visible(summary)
    assert "This file can't be imported" in summary.inner_text()
    assert "families!J2: a formula is not allowed" in summary.inner_text()
    assert "3 more problems are not shown." in summary.inner_text()
    eventually(page, "document.activeElement.hasAttribute('data-error-summary')")
    assert page.evaluate("window.notReloaded === true")
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
    eventually(page, "document.querySelectorAll('#table tbody tr').length", 3)
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
    """Each Active Families count explains where it was counted from."""
    page.goto(component_origin + PAGE)
    button = page.locator("#table .toggletip-button").first
    button.focus()
    page.keyboard.press("Enter")
    bubble = page.locator("#provenance-2026")
    visible(bubble)
    assert bubble.inner_text() == (
        "Counted from: Families issued <campaign> codes, from an invented snapshot"
    )
