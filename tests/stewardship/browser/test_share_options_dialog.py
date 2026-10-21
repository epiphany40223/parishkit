"""Share options reviewed and applied in the shared review dialog (#750).

Save waits for a real change to the list (#921); Save opens the dialog
with a compact review marking only what changed (#1356); once Apply is
applied, the dialog closes, the form's region is redrawn in place with the
applied list and a fresh blank row, and the status line under the form says
what was applied, with no reload and no jump to the top. A row's error stays
on the form; any other refusal shows in the dialog. Pages and POST answers
are ``settings_components``' (``SHARE`` and the names after it); a route
answers each Review POST.
"""

import json
import re

import pytest

from .review_helpers import RESTORE, answer_reviews, expect_review
from .settings_components import SHARE, SHARE_REFUSED, SHARE_REVIEW
from .test_in_place import MARK, MARKED
from .waits import eventually, has_attribute, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
OPTION_HINT = "Change an option to save it."
APPLIED_LINE = re.compile(
    r"^Share options changed\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$"
)
DIGEST = "#settings-form input[name=base_digest]"
# Where an element sits on the page (not in the window, which may scroll).
PLACE = """(id) => {
    const box = document.getElementById(id).getBoundingClientRect();
    return [box.left + window.scrollX, box.top + window.scrollY];
}"""


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate(MARK)
    return failures


def dialog_of(page):
    """The review dialog, once it is open."""
    dialog = page.get_by_role("dialog", name="Save the share options?")
    visible(dialog)
    return dialog


def test_a_change_is_reviewed_and_applied_in_the_dialog(page, component_origin):
    """Save opens the dialog on its title with a compact review of the one
    change; Apply closes it, redraws the form in place with the applied list
    at the applied version, says what was applied in the status line and
    leaves Review waiting for the next change. Nothing reloads, the window
    does not jump to the top, the form and the status line do not move, and
    no line claims that typing was lost (what was typed was reviewed)."""
    from playwright.sync_api import expect

    reviews = answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    label = page.locator("#id_options-0-label")
    label.fill("Renamed option")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    assert page.evaluate(FOCUSED) == "share-review-title"
    # A compact review (#1356): one line for the one relabelled option,
    # which describes the dialog, never both whole lists.
    lines = dialog.locator("#share-review-summary li")
    assert lines.count() == 1
    text = lines.first.inner_text()
    assert text.startswith("Label: ") and text.endswith(" Renamed option")
    assert "→" in text
    assert dialog.get_by_role("heading").count() == 1
    assert dialog.locator("ol").count() == 0
    assert dialog.get_attribute("aria-describedby") == "share-review-summary"
    expect(dialog).to_have_accessible_description(
        re.compile(r"^Label: .+ ?, becomes Renamed option$")
    )
    assert len(reviews) == 1 and "options-0-label=Renamed+option" in reviews[0]
    places = [page.evaluate(PLACE, id) for id in ("settings-form", "share-outcome")]
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('share-outcome').textContent !== ''")
    line = page.locator("#share-outcome")
    assert APPLIED_LINE.search(line.inner_text()), line.inner_text()
    assert page.evaluate(FOCUSED) == "share-outcome"
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    assert label.input_value() == "Renamed option"
    assert page.locator("[data-edits-lost]").count() == 0
    assert same_place(
        [page.evaluate(PLACE, id) for id in ("settings-form", "share-outcome")],
        places,
    )
    # The applied list is now the saved one.
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_review_waits_for_a_change(page, component_origin):
    """A renamed label, a deletion or a new row in the blank last row makes
    Review available, undoing each makes it unavailable again, and a restore
    is noticed on pageshow (#921). The status line under the form never
    moves."""
    page.goto(component_origin + SHARE)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    below = "document.getElementById('share-outcome').offsetTop"
    before = page.evaluate(below)
    label = page.locator("#id_options-0-label")
    saved = label.input_value()
    label.fill("Renamed option")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    label.fill(saved)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    delete = page.locator("#id_options-0-DELETE")
    delete.check()
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    delete.uncheck()
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
    blank = page.locator(f"#id_options-{total - 1}-label")
    assert blank.input_value() == ""
    blank.fill("A new option")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    blank.fill("")
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    label.evaluate("field => { field.value = 'Restored label'; }")
    page.evaluate(RESTORE)
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    assert page.evaluate(below) == before


def test_a_refused_row_is_marked_and_keeps_the_dialog_shut(page, component_origin):
    """A Review refused for a row's value is shown on the form: the row's
    label is marked, the summary is shown, and no dialog opens."""
    answer_reviews(page, component_origin, SHARE_REFUSED, 400, address=SHARE)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    # A label the page admits, which this server answer refuses anyway (a
    # private placeholder is now held by the label rule before Save, #1577).
    page.locator("#id_options-0-label").fill("Edited label")
    page.get_by_role("button", name="Save", exact=True).click()
    visible(page.locator("[data-error-summary]"))
    has_attribute(page.locator("#id_options-0-label"), "aria-invalid", "true")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_stale_page_is_refused_in_the_dialog(page, component_origin):
    """A Review refused because the list changed elsewhere shows the
    server's explanation in the dialog, with no Apply; the form keeps the
    reader's rows at the version they came from."""

    def stale(route):
        """Answer Review with the closed refusal of a stale page."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [{"code": "stale"}],
                    "refusal": {
                        "message": "This page changed in another tab or session.",
                        "fix": "Reload the page to start from the current settings.",
                    },
                }
            ),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + SHARE, stale)
    page.goto(component_origin + SHARE)
    page.locator("#id_options-0-label").fill("Renamed option")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "changed in another tab" in error.inner_text()
    assert dialog.get_by_role("button", name="Apply").count() == 0
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#id_options-0-label").input_value() == "Renamed option"
    assert page.locator(DIGEST).input_value() == "a" * 64


@pytest.mark.parametrize("edit", ["add", "delete"])
def test_review_is_unavailable_after_applying_a_row_change(
    page, component_origin, edit
):
    """Applying a new row or a deletion redraws Share options with the
    applied list and a fresh blank row; that redrawn list is the saved one,
    so Save is unavailable (#921)."""
    answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)
    page.goto(component_origin + SHARE)
    if edit == "add":
        total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
        page.locator(f"#id_options-{total - 1}-label").fill("A new option")
    else:
        page.locator("#id_options-1-DELETE").check()
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    has_attribute(page.locator(DIGEST), "value", "b" * 64)
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")


def test_a_refused_apply_shows_in_the_dialog(page, component_origin):
    """An Apply refused because the reviewed preview is out of date (the
    list changed elsewhere) says why in the dialog, below its buttons, and
    stays until closed; the page behind is not redrawn and keeps the
    reader's rows at their version."""
    answer_reviews(page, component_origin, SHARE_REVIEW, address=SHARE)

    def refuse(route):
        """Refuse Apply as the server does; Review falls back above."""
        if "action=confirm" not in (route.request.post_data or ""):
            route.fallback()
            return
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [{"code": "stale"}],
                    "refusal": {
                        "message": "This preview is out of date.",
                        "fix": "Review the changes again, then confirm.",
                    },
                }
            ),
        )

    page.route(lambda url: url.split("#")[0] == component_origin + SHARE, refuse)
    page.goto(component_origin + SHARE)
    failures = watch(page)
    page.locator("#id_options-0-label").fill("Renamed option")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = dialog_of(page)
    dialog.get_by_role("button", name="Apply").click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert "This preview is out of date." in error.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.locator("#share-outcome").inner_text() == ""
    assert page.locator("#id_options-0-label").input_value() == "Renamed option"
    assert page.locator(DIGEST).input_value() == "a" * 64
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_the_short_label_is_offered_and_greys_with_delete(page, component_origin):
    """Each option has a Short label field (#1569) with its help, limited to
    12 characters; changing it makes Save available, and ticking the row's
    Delete greys it in place without moving anything below."""
    from playwright.sync_api import expect

    page.goto(component_origin + SHARE)
    failures = watch(page)
    short = page.locator("#id_options-0-short_label")
    assert short.input_value() == "Bank check"
    assert short.get_attribute("maxlength") == "12"
    expect(short).to_have_accessible_name(re.compile(r"^Short label"))
    expect(short).to_have_accessible_description(
        re.compile(r"Families still see the full label\.")
    )
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    short.fill("Check")
    expect_review(page, enabled=True, hint=OPTION_HINT, name="Save")
    short.fill("Bank check")
    expect_review(page, enabled=False, hint=OPTION_HINT, name="Save")
    places = [page.evaluate(PLACE, id) for id in ("settings-form", "share-outcome")]
    page.locator("#id_options-0-DELETE").check()
    expect(short).to_be_disabled()
    page.locator("#id_options-0-DELETE").uncheck()
    expect(short).to_be_enabled()
    assert same_place(
        [page.evaluate(PLACE, id) for id in ("settings-form", "share-outcome")],
        places,
    )
    assert not failures


def test_a_locked_campaign_edits_only_the_short_labels(page, component_origin):
    """On a locked campaign (#1569) every field but each short label is
    disabled with the reason in its tip, there is no blank row, the page
    says why and leaves out the placeholders paragraph, and changing a
    short label makes Save available."""
    from playwright.sync_api import expect

    from .settings_components import SHARE_LIVE

    page.goto(component_origin + SHARE_LIVE)
    failures = watch(page)
    total = int(page.locator("[name=options-TOTAL_FORMS]").input_value())
    initial = int(page.locator("[name=options-INITIAL_FORMS]").input_value())
    assert total == initial == 7
    for index in range(total):
        for name in ("label", "free_text", "ORDER", "DELETE"):
            expect(page.locator(f"#id_options-{index}-{name}")).to_be_disabled()
        expect(page.locator(f"#id_options-{index}-short_label")).to_be_enabled()
    reason = "This campaign is locked, so only the short labels can change."
    assert page.locator("p", has_text=reason).count() == 1
    assert page.get_by_text("placeholders inside double braces").count() == 0
    # A locked field's "i" tip says why, as on Campaign settings.
    label = page.locator("#id_options-0-label")
    expect(label).to_have_accessible_description(re.compile(re.escape(reason)))
    tip = page.locator("#id_options-0-label_tip")
    page.locator("[aria-controls=id_options-0-label_tip]").click()
    expect(tip).to_be_visible()
    assert tip.inner_text() == reason
    page.keyboard.press("Escape")
    hint = "Change a short label to save it."
    expect_review(page, enabled=False, hint=hint, name="Save")
    page.locator("#id_options-0-short_label").fill("Check")
    expect_review(page, enabled=True, hint=hint, name="Save")
    assert not failures


REPEATED = "Another option already has this short label."
# Set a box's exact value as typing would and say, before any timer runs,
# whether it is marked and whether Save is held; the page's input listeners
# run synchronously, so the answer is the check's verdict on this keystroke.
TYPE_NOW = """([id, value]) => {
  const box = document.getElementById(id);
  box.value = value;
  box.dispatchEvent(new Event("input", {bubbles: true}));
  const save = [...box.form.querySelectorAll("button")]
    .find((node) => node.textContent.trim() === "Save");
  return {marked: box.getAttribute("aria-invalid") === "true",
          valid: box.validity.valid, held: save.disabled};
}"""


def short_box(page, row):
    """Option ``row``'s (0-based) Short label box."""
    return page.locator(f"#id_options-{row}-short_label")


def assert_repeated(page, rows, marked=True):
    """Each of ``rows``' short labels is (or is not) marked as repeated, in
    its own reserved line, which describes it."""
    for row in rows:
        box = short_box(page, row)
        line = page.locator(f"#id_options-{row}-short_label_error")
        if marked:
            has_attribute(box, "aria-invalid", "true")
            visible(line)
            assert line.inner_text() == REPEATED
            described = box.get_attribute("aria-describedby").split()
            assert line.get_attribute("id") in described
        else:
            eventually(
                page,
                "(id) => document.getElementById(id)"
                ".getAttribute('aria-invalid') !== 'true'",
                arg=box.get_attribute("id"),
            )
            hidden(line)
            described = (box.get_attribute("aria-describedby") or "").split()
            assert line.get_attribute("id") not in described


def test_a_repeated_short_label_is_marked_in_place_and_holds_save(
    page, component_origin
):
    """A short label another option has (in another case, with spaces
    around it) marks both boxes at their own reserved lines once typing
    pauses, or at once when the box is left; Save is unavailable at once,
    with the same words as its reason. Changing either label takes both
    marks off at once and gives Save back. Nothing moves, nothing reloads."""
    page.goto(component_origin + SHARE)
    failures = watch(page)
    save = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#share-complete-hint")
    watched = ("id_options-2-label", "id_options-1-free_text", "share-outcome")
    places = [page.evaluate(PLACE, id) for id in watched]
    # The keystroke itself holds Save; the marks wait for the pause.
    now = page.evaluate(TYPE_NOW, ["id_options-1-short_label", "  bank CHECK "])
    assert now == {"marked": False, "valid": False, "held": True}
    assert_repeated(page, [0, 1])
    assert save.is_disabled()
    has_text(hint, REPEATED)
    assert "share-complete-hint" in save.get_attribute("aria-describedby").split()
    assert same_place([page.evaluate(PLACE, id) for id in watched], places)
    assert_repeated(page, [2, 3, 4, 5, 6], marked=False)
    # Resolving it from the other box clears both at once.
    now = page.evaluate(TYPE_NOW, ["id_options-0-short_label", "Check"])
    assert now == {"marked": False, "valid": True, "held": False}
    assert_repeated(page, [0, 1], marked=False)
    assert save.is_enabled()
    has_text(hint, "")
    # Leaving a box shows a new repeat at once, without waiting.
    short_box(page, 2).fill("check")
    short_box(page, 2).press("Tab")
    assert page.evaluate(
        "() => ['id_options-0-short_label', 'id_options-2-short_label']"
        ".map((id) => document.getElementById(id).getAttribute('aria-invalid'))"
    ) == ["true", "true"]
    assert save.is_disabled()
    assert same_place([page.evaluate(PLACE, id) for id in watched], places)
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_deleting_one_of_the_options_resolves_the_repeat(page, component_origin):
    """A deleted option's short label no longer counts: ticking its Delete
    takes the marks off and gives Save back; unticking brings them back."""
    page.goto(component_origin + SHARE)
    failures = watch(page)
    save = page.get_by_role("button", name="Save", exact=True)
    short_box(page, 1).fill("Bank check")
    short_box(page, 1).press("Tab")
    assert_repeated(page, [0, 1])
    page.locator("#id_options-0-DELETE").check()
    assert_repeated(page, [1], marked=False)
    assert save.is_enabled()
    page.locator("#id_options-0-DELETE").uncheck()
    assert_repeated(page, [0, 1])
    assert save.is_disabled()
    assert not failures


def test_the_page_compares_short_labels_as_the_server_does(page, component_origin):
    """The pairs the server reads as one short label (upper then lower
    case, Python's spaces around it) are a repeat here too, and the pairs it
    keeps apart are not (test_share_forms' table)."""
    from ..test_share_forms import OTHER_SHORT_LABELS, SAME_SHORT_LABELS

    page.goto(component_origin + SHARE)
    failures = watch(page)
    pairs = [(pair, False) for pair in SAME_SHORT_LABELS] + [
        (pair, True) for pair in OTHER_SHORT_LABELS
    ]
    for (first, second), apart in pairs:
        page.evaluate(TYPE_NOW, ["id_options-0-short_label", first])
        now = page.evaluate(TYPE_NOW, ["id_options-1-short_label", second])
        assert now["valid"] is apart, (first, second)
    assert not failures


def test_a_refused_repeat_clears_once_resolved(page, component_origin):
    """The server's refusal marks both options that share the short label;
    changing one takes both marks off, and the summary's links with them."""
    from .settings_components import SHARE_DUPLICATE

    page.goto(component_origin + SHARE_DUPLICATE)
    failures = watch(page)
    assert_repeated(page, [0, 1])
    short_box(page, 1).fill("Bank draft")
    assert_repeated(page, [0, 1], marked=False)
    assert page.locator("[data-error-summary]").count() == 0
    assert not failures


def test_a_repeat_at_phone_width_in_dark(page, component_origin):
    """At 320 px in Dark the repeat's words show in the error colour at the
    box, which has the error border; the page does not scroll sideways and
    the message moves nothing, even where it wraps."""
    from .test_campaign_stepper import scheme

    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + SHARE)
    scheme(page, "dark")
    failures = watch(page)
    # Every field after the marked box stays put. (Save's reason under it
    # wraps to a second line at this width, which moves only the status
    # line below the button, as the prerequisite gates rule allows.)
    watched = ("id_options-1-free_text", "id_options-2-label", "id_options-6-label")
    places = [page.evaluate(PLACE, id) for id in watched]
    short_box(page, 1).fill("BANK CHECK")
    short_box(page, 1).press("Tab")
    assert_repeated(page, [0, 1])
    line = page.locator("#id_options-1-short_label_error")
    colours = line.evaluate(
        """(line) => {
          const probe = document.createElement("span");
          probe.style.color = "var(--error-ink)";
          line.append(probe);
          const ink = getComputedStyle(probe).color;
          probe.remove();
          const box = getComputedStyle(
            document.getElementById("id_options-1-short_label"));
          return {text: getComputedStyle(line).color, ink, shadow: box.boxShadow,
                  wide: document.documentElement.scrollWidth > window.innerWidth};
        }"""
    )
    assert colours["text"] == colours["ink"]
    assert colours["shadow"] not in ("", "none")
    assert colours["wide"] is False
    assert same_place([page.evaluate(PLACE, id) for id in watched], places)
    assert not failures
