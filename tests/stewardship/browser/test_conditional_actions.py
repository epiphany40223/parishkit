"""Actions wait for their prerequisites, with a hint (#563).

Pause and resume mail and Portal users: a Preview or Review button stays
unavailable until what the server needs is there (a reason that is not only
spaces, a message type, a role), the hint by the button says what is
missing, and the page offers only the choices the server accepts. Every
check is repeated on pageshow, when a page restored from history gets back
its values without change events.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"


def restore(locator, script):
    """Change a control without events, as a restore does, then show the page."""
    locator.evaluate(script)
    locator.page.evaluate(RESTORE)


def described_by_hint(button, hint):
    """True when the button names the hint element with aria-describedby."""
    return hint.get_attribute("id") in (button.get_attribute("aria-describedby") or "")


@pytest.mark.parametrize(
    "path,label,button,missing",
    [
        (
            "/delivery-pause",
            "Reason for pausing live delivery",
            "Preview pause",
            "Enter a reason for pausing to preview it.",
        ),
        (
            "/delivery-resume",
            "Reason for resuming live delivery",
            "Preview resume",
            "Enter a reason for resuming to preview it.",
        ),
    ],
)
def test_pause_and_resume_wait_for_a_reason(
    page, component_origin, path, label, button, missing
):
    """A blank or space-only reason keeps Preview unavailable, with a hint."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    preview = page.get_by_role("button", name=button)
    reason = page.get_by_label(label, exact=True)
    hint = page.get_by_text(missing)
    # The fixture carries the previous preview's reason.
    assert preview.is_enabled()
    reason.fill("   ")
    assert preview.is_disabled()
    visible(hint)
    assert described_by_hint(preview, hint)
    reason.fill("Sender settings fixed")
    assert preview.is_enabled()
    hidden(hint)
    # A blank reason restored from history holds Preview again.
    restore(reason, "field => { field.value = ''; }")
    assert preview.is_disabled()
    assert not failures


def options(page):
    """The values the Resolution list offers."""
    return (
        page.get_by_label("Resolution", exact=True)
        .locator("option")
        .evaluate_all("options => options.map(option => option.value)")
    )


def test_resolution_before_the_sender_check_offers_cancel(page, component_origin):
    """Before the check only Cancel is offered, for the types it can take."""
    page.goto(component_origin + "/delivery-resolve")
    assert options(page) == ["cancel"]
    visible(page.get_by_text("Releasing held messages becomes available"))
    visible(
        page.get_by_text("These types can't be cancelled yet: Daily Admin reports.")
    )
    preview = page.get_by_role("button", name="Preview held-message resolution")
    types = page.get_by_role("group", name="Message types")
    # Daily reports still have uncertain mail: Cancel cannot take them.
    assert types.locator("input[type=checkbox]").evaluate_all(
        "boxes => boxes.map(box => box.name)"
    ) == ["receipt", "weekly_digest"]
    hint = page.get_by_text("Tick at least one message type to preview.")
    visible(hint)
    assert preview.is_disabled()
    assert described_by_hint(preview, hint)
    weekly = page.get_by_label("Weekly Admin reports", exact=False)
    weekly.check()
    assert preview.is_enabled()
    hidden(hint)
    # A restored unticked box holds Preview again.
    restore(weekly, "box => { box.checked = false; }")
    assert preview.is_disabled()
    visible(hint)
    # A reason of only spaces is not a reason.
    weekly.check()
    page.get_by_label("Reason", exact=True).fill("  ")
    assert preview.is_disabled()
    visible(page.get_by_text("Enter a reason to preview the resolution."))


def test_cancel_hides_types_it_cannot_take(page, component_origin):
    """After the check Release lists every held type; Cancel hides the
    uncertain one, and its tick no longer counts or is sent."""
    page.goto(component_origin + "/delivery-resolve-ready")
    assert options(page) == ["release", "cancel"]
    assert page.get_by_text("Releasing held messages becomes available").count() == 0
    preview = page.get_by_role("button", name="Preview held-message resolution")
    daily = page.get_by_label("Daily Admin reports", exact=False)
    note = page.get_by_text("These types can't be cancelled yet: Daily Admin reports.")
    hidden(note)
    daily.check()
    assert preview.is_enabled()
    decision = page.get_by_label("Resolution", exact=True)
    decision.select_option("cancel")
    hidden(daily)
    visible(note)
    assert preview.is_disabled()
    posted = page.locator("form:has(#resolution-decision)").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "daily_digest" not in posted
    page.get_by_label("Submission receipts", exact=False).check()
    assert preview.is_enabled()
    # Release restored from history shows the daily reports again, ticked.
    restore(decision, "select => { select.value = 'release'; }")
    visible(daily)
    assert daily.is_checked()
    hidden(note)


def test_resolution_with_only_stranded_mail_offers_clear(page, component_origin):
    """With only stranded Family mail held, Clear is the only choice."""
    page.goto(component_origin + "/delivery-resolve-stranded")
    assert options(page) == ["clear"]
    assert page.get_by_role("group", name="Message types").count() == 0
    preview = page.get_by_role("button", name="Preview held-message resolution")
    assert preview.is_enabled()


def unavailable(page):
    """The unavailable Preview control and the reason that describes it."""
    control = page.locator(".disabled-control-link")
    visible(control)
    assert control.inner_text() == "Preview held-message resolution"
    assert control.get_attribute("aria-disabled") == "true"
    assert page.locator("#resolution-decision").count() == 0
    name = "Preview held-message resolution"
    assert page.get_by_role("button", name=name).count() == 0
    reason = page.locator("#" + control.get_attribute("aria-describedby"))
    visible(reason)
    return reason.inner_text()


def test_resolution_with_nothing_resolvable_is_unavailable(page, component_origin):
    """Uncertain mail blocks the clear: the reason says how to clear."""
    page.goto(component_origin + "/delivery-resolve-blocked")
    reason = unavailable(page)
    assert "To clear the pause" in reason
    assert "provider and sender check" not in reason


def test_resolution_stuck_before_the_check_names_every_way_forward(
    page, component_origin
):
    """Held types all with uncertain mail, before the check: the reason
    names both the check (to release) and the types to reconcile (to cancel)."""
    page.goto(component_origin + "/delivery-resolve-stuck")
    reason = unavailable(page)
    assert "To release held messages, pass the provider and sender check" in reason
    assert "Daily Admin reports, Weekly Admin reports" in reason


def test_new_domain_rule_waits_for_a_domain_and_a_role(page, component_origin):
    """Review stays unavailable until a domain is typed and a role ticked."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/portal-users")
    form = page.locator("form:has(#new-domain)")
    review = form.get_by_role("button", name="Review new domain rule")
    hint = form.locator("[data-complete-hint]")
    assert review.is_disabled()
    visible(hint)
    assert hint.inner_text() == "Enter the hosted domain to review the rule."
    assert described_by_hint(review, hint)
    # A domain of only spaces is still missing.
    page.locator("#new-domain").fill("   ")
    assert review.is_disabled()
    assert hint.inner_text() == "Enter the hosted domain to review the rule."
    page.locator("#new-domain").fill("parish.example")
    assert review.is_disabled()
    assert hint.inner_text() == "Tick at least one role for the domain."
    roles = form.locator('input[name="roles"]')
    roles.first.check()
    assert review.is_enabled()
    hidden(hint)
    # A role unticked by a restore holds Review again.
    restore(roles.first, "box => { box.checked = false; }")
    assert review.is_disabled()
    visible(hint)
    assert not failures
