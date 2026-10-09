"""Acknowledgments and typed reasons say why their action waits (#563).

A form with a required acknowledgment box keeps its button unavailable, and
a hint after the button (named by its aria-describedby) says to tick the box.
Cancel go-live, Mail delivery, delivery refusal and Confirm Production use
the complete gate instead: a reason or note of only spaces is still empty,
as the server trims it, and the typed "Production" may have spaces around it
as the server allows. Each hint sits after its button, so a line coming or
going never moves the control just used, or the button. Every state is
worked out again on pageshow and for a form an in-place swap brings in.
"""

import pytest

from .test_campaign_setup_gates import ready, restore, spot, waits_with
from .waits import hidden

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


def test_setup_reset_waits_for_its_confirmation(page, component_origin):
    """Reset all pages and emails is an acknowledgment, with its own hint."""
    page.goto(component_origin + "/setup-content")
    reset = page.get_by_role(
        "button", name="Reset all pages and emails to the default text"
    )
    hint = hint_for(reset)
    waits_with(reset, hint, "Tick the box to confirm replacing every page and email.")
    follows(reset, hint)
    page.get_by_role("checkbox").check()
    ready(reset, hint)


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


def test_cancel_go_live_waits_for_a_real_reason_and_the_tick(page, component_origin):
    """A reason of only spaces is empty, as the server trims it."""
    page.goto(component_origin + "/production-withdrawal")
    preview = page.get_by_role("button", name="Preview cancellation")
    hint = page.locator("#withdrawal-complete-hint")
    reason = page.locator("#withdrawal-reason")
    box = page.locator("input[name=acknowledged]")
    # Only the complete gate's hint is shown, never a second line.
    assert page.locator("[data-acknowledgment-hint]").count() == 0
    reason.fill("   ")
    waits_with(preview, hint, "Enter the reason for cancelling go-live.")
    follows(preview, hint)
    reason.fill("Correct the schedule")
    waits_with(
        preview,
        hint,
        "Tick the box to confirm that deleted Testing data cannot be restored.",
    )
    where = (spot(box), spot(preview))
    box.check()
    ready(preview, hint)
    assert (spot(box), spot(preview)) == where
    restore(reason, "node => { node.value = '  '; }")
    waits_with(preview, hint, "Enter the reason for cancelling go-live.")


def test_delivery_evidence_notes_need_more_than_spaces(page, component_origin):
    """Each resolution waits for a note; a resend also for its tick."""
    page.goto(component_origin + "/delivery")
    form = page.locator("form").filter(
        has=page.locator('[name="action"][value="resend"]')
    )
    button = form.get_by_role("button")
    hint = form.locator("[data-complete-hint]")
    note = "Enter the evidence or reason for this decision."
    waits_with(button, hint, note)
    follows(button, hint)
    form.locator("textarea").fill("  \n ")
    waits_with(button, hint, note)
    form.locator("textarea").fill("Confirmed with the Family")
    waits_with(button, hint, "Tick the box to accept the duplicate risk.")
    form.get_by_role("checkbox").check()
    ready(button, hint)
    # Another resolution on the page waits for its own note only.
    other = page.locator("form").filter(
        has=page.locator('[name="action"][value="accept"]')
    )
    waits_with(other.get_by_role("button"), other.locator("[data-complete-hint]"), note)
    other.locator("textarea").fill("Provider log shows acceptance")
    ready(other.get_by_role("button"), other.locator("[data-complete-hint]"))


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


def test_confirm_production_waits_for_the_typed_word(page, component_origin):
    """Only "Production" (spaces around it allowed, as the server allows)
    makes Confirm available; the hint says what to type."""
    page.goto(component_origin + "/production-confirmation")
    confirm = page.get_by_role("button", name="Confirm Production")
    hint = page.locator("#production-complete-hint")
    typed = page.locator("#production-confirmation")
    text = "Type Production exactly as shown to confirm."
    waits_with(confirm, hint, text)
    follows(confirm, hint)
    where = (spot(typed), spot(confirm))
    for value in ("production", "Productio", "Production!"):
        typed.fill(value)
        waits_with(confirm, hint, text)
    typed.fill(" Production ")
    ready(confirm, hint)
    assert (spot(typed), spot(confirm)) == where
    typed.fill("")
    waits_with(confirm, hint, text)
    hidden(page.locator("[data-acknowledgment-hint]"))
