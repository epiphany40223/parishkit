"""The Mail message page's question-led resolution in every engine (#1563).

The page (delivery_components) asks "What did you find?" for an email that
may not have arrived and "Send it again?" for a failed one. These check the
progressive disclosure, that choosing or changing an answer moves neither
the answer's button nor anything after the panel (no layout shift), that
an unavailable answer stays in view with its reason, and the radio groups'
names. The in-place answers themselves are in test_export_resolve_in_place.py.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each answer of "What did you find?", the radio buttons that reveal it,
# and its form's button.
ANSWERS = [
    (("It was sent",), "sent", "Mark as delivered"),
    (("It was not sent", "Yes"), "resend", "Send it again"),
    (("It was not sent", "No"), "not_sent", "Record as not sent"),
    (("I can't tell yet",), "unsure", "Save note"),
]
# The document position of an element: scrolling never changes it.
PLACE = (
    "node => { const box = node.getBoundingClientRect();"
    " return [box.left, box.top + scrollY, box.width, box.height]; }"
)


def choose(page, radios):
    """Tick each named radio button in turn."""
    for name in radios:
        page.get_by_role("radio", name=name, exact=True).check()


def test_choosing_an_answer_reveals_only_its_fields(page, component_origin):
    """Nothing to fill in shows until an answer is chosen; then only that
    answer's note and button, which waits for the note (#1563)."""
    page.goto(component_origin + "/delivery")
    for _, choice, _ in ANSWERS:
        hidden(page.locator(f"#answer-{choice}"))
    visible(page.get_by_text("Choose what you found to see what to enter."))
    for radios, choice, label in ANSWERS:
        choose(page, radios)
        form = page.locator(f"#answer-{choice}")
        visible(form)
        for _, other, _ in ANSWERS:
            if other != choice:
                hidden(page.locator(f"#answer-{other}"))
        button = form.get_by_role("button", name=label, exact=True)
        assert button.is_disabled()
        form.locator("textarea").fill("Checked the Sent folder")
        if choice == "resend":
            # The duplicate-risk tick still holds the resend.
            assert button.is_disabled()
            form.get_by_role("checkbox").check()
        assert button.is_enabled()


def test_changing_the_answer_moves_nothing(page, component_origin):
    """Every answer's button sits in the same place, and the question, the
    lines after the panel and the panel itself keep their places and sizes
    whichever answer is chosen (#736, #1563)."""
    page.goto(component_origin + "/delivery")
    fixed = [
        page.get_by_role("radio", name="I can't tell yet", exact=True),
        page.locator("#outcome-heading"),
        page.locator("#delivery-state > p").first,
        page.locator(".outcome-panel"),
    ]

    def places():
        """Every fixed element's place and size, as one flat list."""
        return [value for node in fixed for value in node.evaluate(PLACE)]

    before = places()
    buttons = []
    for radios, choice, label in ANSWERS:
        choose(page, radios)
        button = page.locator(f"#answer-{choice}").get_by_role("button", name=label)
        visible(button)
        buttons.append(button.evaluate(PLACE)[:2])
        assert places() == pytest.approx(before, abs=1), choice
    for place in buttons[1:]:
        assert place == pytest.approx(buttons[0], abs=1)


def test_an_unavailable_answer_stays_with_its_reason(page, component_origin):
    """A failed email whose Family has responded keeps Yes in view,
    unavailable and named by why; No still works (#563, #1563)."""
    page.goto(component_origin + "/delivery-failed-responded")
    yes = page.get_by_role("radio", name="Yes", exact=True)
    assert yes.is_disabled()
    reason = page.locator("#" + yes.get_attribute("aria-describedby"))
    visible(reason)
    assert "already responded" in reason.inner_text()
    choose(page, ("No",))
    visible(page.locator("#answer-keep_failed"))
    assert page.locator("#answer-send_again").count() == 0


def test_question_groups_are_named_by_their_question(page, component_origin):
    """Each radio group is a fieldset named by its question, so a screen
    reader says the question with each answer; a nested question is named
    the same way once its answer reveals it."""
    page.goto(component_origin + "/delivery")
    found = page.get_by_role("group", name="What did you find?")
    assert found.get_by_role("radio").count() == 3
    choose(page, ("It was not sent",))
    again = page.get_by_role("group", name="Send it again?")
    visible(again)
    assert again.get_by_role("radio").count() == 2


def test_a_hidden_answer_cannot_be_reached_by_keyboard(page, component_origin):
    """An answer not chosen is inert: Tab from the last radio button goes to
    the chosen answer's note, never into another answer's fields."""
    page.goto(component_origin + "/delivery")
    choose(page, ("I can't tell yet",))
    page.get_by_role("radio", name="I can't tell yet", exact=True).focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "note-unsure"
