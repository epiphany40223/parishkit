"""The shared Basic / Advanced editor-mode toggle (#1121), on a component page.

The page renders the real ``components/editor-mode.html`` partial (and the
shared confirm dialog it includes) with ui-v1.js and ui-v1.css, around a
small editor whose adapter maps two presets to a times list. The form uses
the real complete-before-submit and change gates, so the tests also check
that the Basic panel's required choice holds Review and that switching
modes alone is not a change. No real editor adopts the toggle yet (#1122,
#1062 and #1129 will).
"""

import pytest
from django.template import engines

from .keyboard import tab_to_next, tab_to_previous
from .waits import focused, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

AXE_TAGS = "['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']"
ADDRESS = "/editor-mode-fixture"
NIGHTLY = "02:00"
EVERY_4 = "00:00, 04:00, 08:00, 12:00, 16:00, 20:00"
CUSTOM = "03:15"
NO_BASIC = "Your custom schedule has no basic equivalent."
TEXT = {
    "no_basic_title": NO_BASIC,
    "no_basic_body": "Basic can't show it. If you switch, choose a basic schedule"
    " and review it; nothing changes until you apply it.",
    # Every editing page has an editor's review in its context
    # (admin_editing.py); the toggle's dialog must not inherit it.
    "review": {"title": "Review refresh schedule"},
}
# A Django tag cannot span lines, so the include is put in whole.
INCLUDE = (
    '{% include "stewardship/components/editor-mode.html" with'
    ' mode_id="schedule-mode" mode=mode no_basic_title=no_basic_title'
    " no_basic_body=no_basic_body %}"
)
PAGE = (
    """{% load static %}<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Editor mode</title>
<link rel="stylesheet" href="{% static 'stewardship/ui-v1.css' %}">
<script src="{% static 'stewardship/ui-v1.js' %}" defer></script>
<script>
document.addEventListener("DOMContentLoaded", () => {
  const presets = {nightly: "NIGHTLY", every4: "EVERY_4"};
  const times = (root) => root.querySelector("textarea[name=times]");
  window.ParishEditorMode.register("schedule", {
    toAdvanced(root) {
      const chosen = root.querySelector("input[name=preset]:checked");
      if (chosen) times(root).value = presets[chosen.value];
    },
    toBasic(root) {
      const value = times(root).value.trim();
      const match = Object.keys(presets).find((key) => presets[key] === value);
      if (!match) return false;
      root.querySelector(`input[name=preset][value=${match}]`).checked = true;
      return true;
    },
  });
});
</script></head><body><main>
<h1>Refresh schedule</h1>
<form id="schedule" method="post" action="/nowhere" data-require-complete
 data-require-change="version">
<input type="hidden" name="version" value="1">
<div id="editor" data-editor-modes="schedule">
INCLUDE
<fieldset class="editor-mode-panel" data-editor-mode-panel="basic" data-not-a-change
 data-missing-hint="Choose a basic schedule to review it."
 {% if mode != "basic" %}hidden disabled{% endif %}>
<legend>Basic schedule</legend>
<label for="preset-nightly"><input type="radio" id="preset-nightly" name="preset"
 value="nightly" required{% if preset == "nightly" %} checked{% endif %}>
Nightly at 2:00 AM</label>
<label for="preset-every4"><input type="radio" id="preset-every4" name="preset"
 value="every4" required{% if preset == "every4" %} checked{% endif %}>
Every 4 hours</label>
</fieldset>
<div data-editor-mode-panel="advanced"{% if mode != "advanced" %} hidden{% endif %}>
<label for="times">Refresh times</label>
<textarea id="times" name="times"{% if times_error %} aria-invalid="true"
 aria-describedby="times-error"{% endif %}>{{ times }}</textarea>
{% if times_error %}<ul class="errorlist" id="times-error">
<li>Enter times such as 02:00.</li></ul>{% endif %}
</div>
</div>
<button type="submit" aria-describedby="schedule-hint">Review changes</button>
<p id="schedule-hint" data-complete-hint="reserve"></p>
<span data-unchanged-hint data-idle>Change a setting to review it.</span>
</form>
<p>Below the editor.</p>
</main></body></html>""".replace("INCLUDE", INCLUDE)
    .replace("NIGHTLY", NIGHTLY)
    .replace("EVERY_4", EVERY_4)
)


def render(**context):
    """The component page drawn by the server in ``context``'s mode."""
    return engines["django"].from_string(PAGE).render(context | TEXT)


def open_editor(page, origin, **context):
    """Serve the page drawn with ``context`` beside ui-v1.js and open it."""
    body = render(**context)
    page.route(
        origin + ADDRESS,
        lambda route: route.fulfill(content_type="text/html", body=body),
    )
    page.goto(origin + ADDRESS)
    return page


def basic_from_saved(page, origin):
    """The editor opened in Basic with Nightly saved."""
    return open_editor(page, origin, mode="basic", preset="nightly", times=NIGHTLY)


def custom_from_saved(page, origin):
    """The editor opened in Advanced with a custom schedule saved."""
    return open_editor(page, origin, mode="advanced", preset="", times=CUSTOM)


def toggle(page, mode):
    """The toggle's radio for ``mode``, by its accessible name."""
    return page.get_by_role("group", name="Mode", exact=True).get_by_role(
        "radio", name=mode
    )


def panel(page, mode):
    """The editor's panel for ``mode``."""
    return page.locator(f"[data-editor-mode-panel={mode}]")


def sent(page):
    """The form's fields as they would be sent."""
    return page.evaluate(
        "Object.fromEntries(new FormData(document.getElementById('schedule')))"
    )


def test_basic_to_advanced_is_lossless_and_not_a_change(page, component_origin):
    """Advanced shows the chosen Basic choice's configuration; the mode
    alone never makes Review available, and is sent with the save."""
    basic_from_saved(page, component_origin)
    review = page.get_by_role("button", name="Review changes")
    assert toggle(page, "Basic").is_checked()
    visible(panel(page, "basic"))
    hidden(panel(page, "advanced"))
    assert sent(page) == {
        "version": "1",
        "editor_mode": "basic",
        "preset": "nightly",
        "times": NIGHTLY,
    }
    toggle(page, "Advanced").check()
    visible(panel(page, "advanced"))
    hidden(panel(page, "basic"))
    assert page.locator("#times").input_value() == NIGHTLY
    assert review.is_disabled()
    # The Basic panel's controls are not sent while Advanced is shown.
    assert sent(page) == {"version": "1", "editor_mode": "advanced", "times": NIGHTLY}
    toggle(page, "Basic").check()
    page.get_by_role("radio", name="Every 4 hours").check()
    # Every Basic edit rewrites the configuration, which is now a change.
    assert page.locator("#times").input_value() == EVERY_4
    assert review.is_enabled()
    toggle(page, "Advanced").check()
    assert page.locator("#times").input_value() == EVERY_4


def test_advanced_to_basic_shows_the_matching_choice(page, component_origin):
    """An Advanced configuration equal to a Basic choice switches at once."""
    open_editor(page, component_origin, mode="advanced", preset="", times=EVERY_4)
    toggle(page, "Basic").check()
    visible(panel(page, "basic"))
    assert page.get_by_role("radio", name="Every 4 hours").is_checked()
    assert not page.locator("dialog").evaluate("dialog => dialog.open")
    assert page.get_by_role("button", name="Review changes").is_disabled()


@pytest.mark.parametrize("dismiss", ["Keep Advanced", "Escape"])
def test_no_basic_equivalent_asks_and_keep_advanced_changes_nothing(
    page, component_origin, dismiss
):
    """The confirm dialog opens with Keep Advanced focused; keeping (or
    Escape) leaves the mode, the configuration and focus on the toggle."""
    custom_from_saved(page, component_origin)
    toggle(page, "Basic").click()
    dialog = page.get_by_role("dialog", name=NO_BASIC)
    visible(dialog)
    keep = dialog.get_by_role("button", name="Keep Advanced")
    switch = dialog.get_by_role("button", name="Switch to Basic")
    focused(keep)
    # Switching removes nothing, so it is a primary button, not a danger one.
    assert "button-danger" not in (switch.get_attribute("class") or "")
    assert toggle(page, "Advanced").is_checked()
    if dismiss == "Escape":
        page.keyboard.press("Escape")
    else:
        keep.click()
    hidden(dialog)
    assert toggle(page, "Advanced").is_checked()
    visible(panel(page, "advanced"))
    hidden(panel(page, "basic"))
    assert page.locator("#times").input_value() == CUSTOM
    # Wait for it: the dialog hides as it closes, before its close event.
    focused(toggle(page, "Advanced"))


@pytest.mark.parametrize("choice", ["Keep Advanced", "Switch to Basic"])
def test_focus_never_rests_on_the_unchosen_mode_as_the_dialog_closes(
    page, component_origin, choice
):
    """In the very task that closes the dialog, before its queued close
    event, focus is not on the Basic choice Keep Advanced put back, and
    Switch to Basic has already moved it to Basic (#1202).

    Against the old code, the Keep Advanced case fails in Chromium (focus
    restored to Basic). The Switch to Basic case fails only in WebKit,
    which does not restore focus on close: Chromium's restoration happened
    to land on Basic, that case's final focus, anyway.
    """
    custom_from_saved(page, component_origin)
    toggle(page, "Basic").click()
    dialog = page.get_by_role("dialog", name=NO_BASIC)
    visible(dialog)
    # One script click and one read, with no task between them.
    now = dialog.get_by_role("button", name=choice).evaluate(
        """(button) => {
            button.click();
            const active = document.activeElement;
            return [button.closest("dialog").open, active.value || active.tagName];
        }"""
    )
    if choice == "Keep Advanced":
        # Advanced where the browser restores focus as the dialog closes;
        # otherwise still the closed dialog's button until the close event.
        assert now[0] is False and now[1] in {"advanced", "BUTTON"}
        focused(toggle(page, "Advanced"))
    else:
        assert now == [False, "basic"]
        focused(toggle(page, "Basic"))


@pytest.mark.parametrize("answer", ["Escape", "Keep Advanced", "Switch to Basic"])
def test_keyboard_alone_opens_and_answers_the_dialog(page, component_origin, answer):
    """With the keyboard alone: an arrow key on the toggle asks; Keep Advanced
    has focus and the two buttons trap Tab and Shift+Tab. Escape, or Enter on
    Keep Advanced, changes nothing and returns focus to Advanced; Enter on
    Switch to Basic shows Basic with focus on it (#1202)."""
    custom_from_saved(page, component_origin)
    toggle(page, "Advanced").focus()
    page.keyboard.press("ArrowLeft")
    dialog = page.get_by_role("dialog", name=NO_BASIC)
    visible(dialog)
    keep = dialog.get_by_role("button", name="Keep Advanced")
    switch = dialog.get_by_role("button", name="Switch to Basic")
    focused(keep)
    assert toggle(page, "Advanced").is_checked()
    if answer == "Escape":
        page.keyboard.press("Escape")
    elif answer == "Keep Advanced":
        # Shift+Tab reaches Switch to Basic, and again wraps back to Keep.
        tab_to_previous(page)
        focused(switch)
        tab_to_previous(page)
        focused(keep)
        page.keyboard.press("Enter")
    else:
        tab_to_next(page)
        focused(switch)
        page.keyboard.press("Enter")
    hidden(dialog)
    mode = "Basic" if answer == "Switch to Basic" else "Advanced"
    focused(toggle(page, mode))
    assert toggle(page, mode).is_checked()
    visible(panel(page, mode.lower()))
    assert page.locator("#times").input_value() == CUSTOM


def test_switch_to_basic_needs_a_choice_and_back_is_lossless(page, component_origin):
    """Switch to Basic shows Basic with no choice, holding Review with the
    panel's reason; going back to Advanced before choosing keeps the custom
    configuration."""
    from playwright.sync_api import expect

    custom_from_saved(page, component_origin)
    toggle(page, "Basic").click()
    page.get_by_role("button", name="Switch to Basic").click()
    hidden(page.locator("dialog"))
    visible(panel(page, "basic"))
    assert toggle(page, "Basic").is_checked()
    focused(toggle(page, "Basic"))
    assert not page.locator("input[name=preset]:checked").count()
    review = page.get_by_role("button", name="Review changes")
    assert review.is_disabled()
    assert (
        page.locator("#schedule-hint").inner_text()
        == "Choose a basic schedule to review it."
    )
    # The switch is announced in the shared live region.
    expect(
        page.locator("main > [role=status]").filter(has_text="Basic mode")
    ).to_have_count(1)
    toggle(page, "Advanced").check()
    assert page.locator("#times").input_value() == CUSTOM
    # The custom schedule still has no Basic equivalent: it asks again.
    toggle(page, "Basic").click()
    visible(page.get_by_role("dialog"))
    page.get_by_role("button", name="Switch to Basic").click()
    page.get_by_role("radio", name="Nightly at 2:00 AM").check()
    assert page.locator("#times").input_value() == NIGHTLY
    assert review.is_enabled()


@pytest.mark.parametrize("width", [320, 1280])
def test_toggle_never_moves_and_is_accessible(
    page, component_origin, axe_source, width
):
    """Switching changes only what is below the toggle; the toggle's segments
    keep their size; axe finds no WCAG 2.2 AA violation in either mode."""
    page.set_viewport_size({"width": width, "height": 900})
    basic_from_saved(page, component_origin)

    def boxes():
        """The toggle's and each segment's box."""
        return [
            page.locator(selector).bounding_box()
            for selector in (
                "#schedule-mode",
                "label[for=schedule-mode-basic]",
                "label[for=schedule-mode-advanced]",
            )
        ]

    before = boxes()
    page.evaluate(axe_source)
    axe = (
        "async () => (await axe.run(document, {runOnly: {type: 'tag', values: "
        + AXE_TAGS
        + "}})).violations.map(({id, nodes}) =>"
        + " ({id, targets: nodes.map(n => n.target)}))"
    )
    assert page.evaluate(axe) == []
    toggle(page, "Advanced").check()
    visible(panel(page, "advanced"))
    assert boxes() == before
    assert page.evaluate(axe) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_dialog_ignores_the_page_review_context(page, component_origin):
    """The toggle's dialog is the confirmation dialog even on a page whose
    context holds an editor's review (it is included with ``only``)."""
    custom_from_saved(page, component_origin)
    dialog = page.locator("#schedule-mode-no-basic")
    assert dialog.get_attribute("data-confirm-dialog") is not None
    assert dialog.get_attribute("data-review-dialog") is None


def test_adapter_still_applies_after_an_in_place_redraw(page, component_origin):
    """An in-place answer replaces the editor with fresh elements (as
    swapRegion does, announcing parishkit:swap); Basic edits still write the
    Advanced fields, because the adapter is found by the editor's name."""
    basic_from_saved(page, component_origin)
    page.evaluate(
        """async () => {
          const html = await (await fetch(location.href)).text();
          const fresh = new DOMParser().parseFromString(html, "text/html")
            .getElementById("editor");
          const adopted = document.adoptNode(fresh);
          document.getElementById("editor").replaceWith(adopted);
          adopted.dispatchEvent(new CustomEvent("parishkit:swap", {bubbles: true}));
        }"""
    )
    page.get_by_role("radio", name="Every 4 hours").check()
    assert page.locator("#times").input_value() == EVERY_4
    toggle(page, "Advanced").check()
    visible(panel(page, "advanced"))
    assert page.locator("#times").input_value() == EVERY_4


def test_advanced_errors_open_advanced(page, component_origin):
    """A server answer with an error on an Advanced field is shown in
    Advanced, even when Basic was sent, so the error is never hidden."""
    open_editor(
        page,
        component_origin,
        mode="basic",
        preset="nightly",
        times="2am, 25:00",
        times_error=True,
    )
    assert toggle(page, "Advanced").is_checked()
    visible(panel(page, "advanced"))
    hidden(panel(page, "basic"))
    visible(page.get_by_text("Enter times such as 02:00."))


def test_basic_matches_only_valid_advanced_fields(page, component_origin):
    """An Advanced value in error has no Basic equivalent, even when its
    text equals a Basic choice; a Basic choice then clears that error."""
    from playwright.sync_api import expect

    open_editor(
        page,
        component_origin,
        mode="advanced",
        preset="",
        times=NIGHTLY,
        times_error=True,
    )
    toggle(page, "Basic").click()
    visible(page.get_by_role("dialog", name=NO_BASIC))
    page.get_by_role("button", name="Switch to Basic").click()
    # The switch happens as the dialog closes: wait for it.
    visible(panel(page, "basic"))
    expect(page.locator("input[name=preset]:checked")).to_have_count(0)
    page.get_by_role("radio", name="Every 4 hours").check()
    times = page.locator("#times")
    assert times.input_value() == EVERY_4
    assert times.get_attribute("data-field-error") is None
    assert times.get_attribute("aria-invalid") != "true"
