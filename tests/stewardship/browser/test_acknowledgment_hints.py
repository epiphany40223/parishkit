"""Acknowledgments and typed reasons say why their action waits (#563).

A form with a required acknowledgment box keeps its button unavailable, and
a hint after the button (named by its aria-describedby) says to tick the box.
Mail delivery and delivery refusal use the complete gate instead: a note
of only spaces is still empty, as the server trims it. (Confirm
Production's typed word and Cancel go-live's reason are gated inside their
review dialogs: test_go_live_confirm.py and test_production_activation.py.)
Each hint sits after its button, so a line coming or
going never moves the control just used, or the button. Every state is
worked out again on pageshow and for a form an in-place swap brings in.
"""

import pytest

from .test_campaign_setup_gates import ready, restore, spot, waits_with

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

TICK = "Tick the confirmation above to continue."
FINISH = "Check readiness and finish setup"


def hint_for(button):
    """The acknowledgment hint the script made for ``button``."""
    return button.page.locator("[data-acknowledgment-hint]")


def follows(button, hint):
    """Assert the hint comes after the button in the page and below it."""
    assert button.evaluate(
        "(b, h) => Boolean(b.compareDocumentPosition(h)"
        " & Node.DOCUMENT_POSITION_FOLLOWING)",
        hint.element_handle(),
    )
    assert hint.bounding_box()["y"] >= button.bounding_box()["y"]


def test_finish_setup_hint_follows_the_button_and_moves_nothing(page, component_origin):
    """The hint shows while the box is unticked; ticking never moves the
    box or the button, and a restored tick is applied on pageshow."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-confirmation")
    finish = page.get_by_role("button", name=FINISH)
    box = page.locator("input[data-acknowledgment]")
    hint = hint_for(finish)
    waits_with(finish, hint, TICK)
    follows(finish, hint)
    where = (spot(box), spot(finish))
    box.check()
    ready(finish, hint)
    assert (spot(box), spot(finish)) == where
    box.uncheck()
    waits_with(finish, hint, TICK)
    assert (spot(box), spot(finish)) == where
    # A page restored from history gets its tick back without a change event.
    restore(box, "node => { node.checked = true; }")
    ready(finish, hint)
    restore(box, "node => { node.checked = false; }")
    waits_with(finish, hint, TICK)
    assert not failures


def test_a_button_the_server_disabled_gets_no_hint(page, component_origin):
    """Setup that is not ready keeps its own reason; no tick hint is added."""
    page.goto(component_origin + "/setup-confirmation-unready")
    finish = page.get_by_role("button", name=FINISH)
    assert finish.is_disabled()
    assert page.locator("[data-acknowledgment-hint]:not([hidden])").count() == 0


def test_a_shown_may_have_arrived_prompt_holds_send_with_a_hint(page, component_origin):
    """The test-email prompt is shown, so Send waits for it and says why."""
    page.goto(component_origin + "/campaign-mail-unknown")
    send = page.get_by_role("button", name="Send this test email")
    hint = hint_for(send)
    waits_with(send, hint, TICK)
    page.locator("input[data-acknowledgment]").check()
    ready(send, hint)


def test_a_swapped_in_form_is_gated(page, component_origin):
    """A form an in-place update brings in waits for its box like any other."""
    page.goto(component_origin + "/campaign-mail")
    page.evaluate(
        """() => {
          const holder = document.createElement("div");
          holder.innerHTML = '<form id="swapped"><label><input type="checkbox"'
            + ' data-acknowledgment> Sure</label><button type="submit">Go</button>'
            + '</form>';
          document.querySelector("main").append(holder);
          holder.dispatchEvent(new CustomEvent("parishkit:swap", {bubbles: true}));
        }"""
    )
    go = page.get_by_role("button", name="Go")
    hint = page.locator("#swapped [data-acknowledgment-hint]")
    waits_with(go, hint, TICK)
    page.locator("#swapped input").check()
    ready(go, hint)


def test_delivery_evidence_notes_need_more_than_spaces(page, component_origin):
    """Each answer waits for a note; a resend also for its tick. The hint's
    line is reserved, so it only empties when the answer is ready (#1563)."""

    def answered(button, hint):
        """The button is available and its reserved hint line is empty."""
        assert button.is_enabled()
        assert hint.text_content() == ""

    page.goto(component_origin + "/delivery")
    for name in ("It was not sent", "Yes"):
        page.get_by_role("radio", name=name, exact=True).check()
    form = page.locator("#answer-resend")
    button = form.get_by_role("button")
    hint = form.locator("[data-complete-hint]")
    note = "Enter the note."
    waits_with(button, hint, note)
    follows(button, hint)
    form.locator("textarea").fill("  \n ")
    waits_with(button, hint, note)
    form.locator("textarea").fill("Confirmed with the Family")
    waits_with(button, hint, "Tick the box to accept the duplicate risk.")
    form.get_by_role("checkbox").check()
    answered(button, hint)
    # Another answer waits for its own note only.
    page.get_by_role("radio", name="It was sent", exact=True).check()
    other = page.locator("#answer-sent")
    waits_with(other.get_by_role("button"), other.locator("[data-complete-hint]"), note)
    other.locator("textarea").fill("Provider log shows acceptance")
    answered(other.get_by_role("button"), other.locator("[data-complete-hint]"))


def test_refusal_removal_needs_a_note_and_the_tick(page, component_origin):
    """Clear waits for a note of more than spaces, then the verification."""
    page.goto(component_origin + "/delivery-refusal")
    clear = page.get_by_role("button", name="Clear verified refusal")
    hint = page.locator("#refusal-complete-hint")
    note = page.locator("#verification-note")
    note.fill("   ")
    waits_with(clear, hint, "Describe how you verified the mailbox.")
    note.fill("The Family replied from it")
    waits_with(clear, hint, "Tick the box to confirm that you verified this address.")
    page.get_by_role("checkbox").check()
    ready(clear, hint)
