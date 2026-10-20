"""Remaining Admin gates from the conditional-fields audit (#563).

Report and log filters hold Apply while a date or amount is out of order or a
value is malformed, saying why in a line kept after the button; System logs
greys the audit-only identifiers until Audit record is ticked. Hosted files
holds Upload until a file that fits is chosen, and shows it unavailable with
why while the library is full. Family portal availability holds Close while
the message holds an "@", and shows both change buttons unavailable until
the sign-in is recent.
"""

import pytest

from .waits import greyed, has_count, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"


def spot(locator):
    """The element's page position in whole pixels, to show it did not move."""
    return tuple(
        round(value)
        for value in locator.evaluate(
            "e => { const box = e.getBoundingClientRect();"
            " return [box.left + window.scrollX, box.top + window.scrollY]; }"
        )
    )


def waits_with(button, hint, text):
    """Assert the button is unavailable and its linked hint says ``text``."""
    assert button.is_disabled()
    assert hint.text_content() == text
    assert hint.get_attribute("id") in button.get_attribute("aria-describedby").split()


def test_log_filters_wait_for_ordered_dates_and_whole_identifiers(
    page, component_origin
):
    """Through before From, or a partial identifier, holds Apply with why;
    the audit-only identifiers are greyed, in place, until Audit is ticked."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/logs")
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator("#log-filter-hint")
    page.locator("#log-start").fill("2026-10-05")
    page.locator("#log-end").fill("2026-10-04")
    waits_with(
        apply, hint, "Enter a whole Through date, no earlier than From, or clear it."
    )
    page.locator("#log-end").fill("2026-10-05")
    assert apply.is_enabled()
    # A later From holds Apply again: the order follows either date.
    page.locator("#log-start").fill("2026-10-06")
    assert apply.is_disabled()
    page.locator("#log-start").fill("")
    assert apply.is_enabled()
    page.locator("#log-identifier-filters").evaluate("d => { d.open = true; }")
    actor = page.get_by_label("Actor identifier")
    actor.fill("1234")
    waits_with(
        apply,
        hint,
        "Paste a whole identifier from the Technical details of an entry, or clear it.",
    )
    actor.fill("01234567-89ab-cdef-0123-456789abcdef")
    assert apply.is_enabled()
    campaign = page.get_by_label("Campaign identifier (audit records only)")
    audit = page.get_by_role("checkbox", name="Audit record")
    where = spot(campaign)
    if audit.is_checked():
        audit.uncheck()
    greyed(campaign)
    assert spot(campaign) == where
    audit.check()
    assert campaign.is_enabled()
    # A restored, out-of-order date is checked again on pageshow.
    page.evaluate(
        "document.getElementById('log-start').value = '2026-10-09';"
        "document.getElementById('log-end').value = '2026-10-08';"
    )
    page.evaluate(RESTORE)
    assert apply.is_disabled()
    assert failures == []


def test_financial_filters_wait_for_valid_ordered_amounts(page, component_origin):
    """A malformed pledge amount, or At most below At least, holds Apply."""
    page.goto(component_origin + "/financial-report")
    apply = page.get_by_role("button", name="Apply filters")
    hint = page.locator("#financial-filter-hint")
    where = spot(apply)
    page.get_by_label("At least").fill("1,000")
    waits_with(
        apply,
        hint,
        "Enter At least in whole dollars or dollars and cents, such as 100 or "
        "100.50, without commas, or clear it.",
    )
    page.get_by_label("At least").fill("1000")
    page.get_by_label("At most").fill("500")
    waits_with(
        apply,
        hint,
        "Enter At most in whole dollars or dollars and cents, no less than At "
        "least, or clear it.",
    )
    assert spot(apply) == where
    page.get_by_label("At most").fill("1000.00")
    assert apply.is_enabled()
    page.locator("#first-start").fill("2026-10-05")
    page.locator("#first-end").fill("2026-10-01")
    assert apply.is_disabled()
    page.locator("#first-end").fill("")
    assert apply.is_enabled()


def test_upload_waits_for_a_file_that_fits(page, component_origin):
    """Upload waits for a file; a file over the room left is refused at once."""
    page.goto(component_origin + "/hosted-files-limits")
    upload = page.get_by_role("button", name="Upload", exact=True)
    hint = page.locator("#hosted-file-upload-hint")
    waits_with(upload, hint, "Choose a file to upload.")
    field = page.locator('input[type="file"]')
    field.set_input_files(
        {"name": "big.pdf", "mimeType": "application/pdf", "buffer": b"x" * 3000}
    )
    waits_with(upload, hint, "This file is larger than the room left in the library.")
    assert field.get_attribute("aria-invalid") == "true"
    field.set_input_files(
        {"name": "small.pdf", "mimeType": "application/pdf", "buffer": b"x" * 100}
    )
    assert upload.is_enabled()
    assert field.get_attribute("aria-invalid") == "false"
    page.locator('input[name="slug"]').fill("Bad Name")
    waits_with(
        upload,
        hint,
        "Use lowercase letters, digits and single hyphens, up to 64 characters.",
    )
    page.goto(component_origin + "/hosted-files-full")
    upload = page.get_by_role("button", name="Upload", exact=True)
    assert upload.is_disabled()
    reason = page.locator("#hosted-file-full")
    visible(reason)
    assert upload.get_attribute("aria-describedby") == "hosted-file-full"


def test_maintenance_message_gates_the_switch(page, component_origin):
    """Close waits while the message holds "@", and no sign-in step ever
    holds it (#1547)."""
    page.goto(component_origin + "/family-portal")
    close = page.get_by_role("button", name="Close the Family portal for maintenance")
    hint = page.locator("#maintenance-close-hint")
    assert close.is_enabled()
    where = spot(close)
    page.get_by_label("Message for Families (optional)").fill("Write to a@b.org")
    waits_with(
        close,
        hint,
        "Leave email addresses out of the message: it cannot contain @.",
    )
    assert spot(close) == where
    page.get_by_label("Message for Families (optional)").fill("Back by 3 PM.")
    assert close.is_enabled()
    assert close.get_attribute("aria-describedby") == "maintenance-close-hint"


@pytest.mark.parametrize(
    ("path", "button", "reason"),
    [
        (
            "/go-live-unchecked",
            "Start Testing cleanup",
            "Cleanup can't start yet. Select Check readiness and public web address "
            "first; cleanup can start once every check passes.",
        ),
        (
            "/go-live-problems",
            "Start Testing cleanup",
            "Cleanup can't start yet. Fix the configuration and data checks above, "
            "then select Check readiness and public web address again.",
        ),
        (
            "/production-confirmation-unchecked",
            "Confirm Production",
            "Select Verify final readiness and mail impact first; you can confirm "
            "once it passes.",
        ),
    ],
)
def test_go_live_steps_show_unavailable_with_why(
    page, component_origin, path, button, reason
):
    """Before a passing check, the next step's button is in view, unavailable,
    and named by the step still needed, rather than left out."""
    page.goto(component_origin + path)
    control = page.get_by_role("button", name=button, exact=True)
    assert control.is_disabled()
    described = control.get_attribute("aria-describedby")
    assert page.locator(f"#{described}").inner_text() == reason


def test_upload_choices_survive_the_table_redrawing(page, component_origin):
    """A chosen file and a typed name stay through an in-place sort and a
    deletion: only Upload's own line redraws with the table, and the gate
    holds or releases the fresh button at once."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/hosted-files")
    page.evaluate("window.notReloaded = true")
    field = page.locator('input[type="file"]')
    slug = page.locator('input[name="slug"]')
    upload = page.get_by_role("button", name="Upload", exact=True)
    field.set_input_files(
        {"name": "guide.pdf", "mimeType": "application/pdf", "buffer": b"x" * 100}
    )
    slug.fill("kept-name")
    assert upload.is_enabled()

    def kept():
        """The reader's choices are still in the form, and Upload is ready."""
        assert field.evaluate("node => node.files.length") == 1
        assert slug.input_value() == "kept-name"
        assert page.get_by_role("button", name="Upload", exact=True).is_enabled()
        assert page.evaluate("window.notReloaded === true")

    # Mark Upload's line, to show the deletion did redraw it.
    page.evaluate(
        "document.getElementById('hosted-file-upload-action').dataset.old = 1"
    )
    page.get_by_role("button", name="Delete parish-picnic").click()
    with page.expect_response(lambda response: "one-deleted" in response.url):
        page.locator("dialog#hosted-file-delete").get_by_role(
            "button", name="Delete", exact=True
        ).click()
    has_count(page.locator("table.hosted-files tbody tr"), 2)
    kept()
    assert page.evaluate(
        "!document.getElementById('hosted-file-upload-action').dataset.old"
    )
    page.get_by_role("link", name="Name (sort ascending)").click()
    has_count(page.locator('table.hosted-files th[aria-sort="ascending"]'), 1)
    kept()
    # With the file cleared, the redrawn button is held again with its hint.
    field.set_input_files([])
    assert page.get_by_role("button", name="Upload", exact=True).is_disabled()
    assert failures == []


def test_rename_waits_for_a_new_valid_name(page, component_origin):
    """Change name waits while the name is the current one, says what to type
    while it is empty, and gives the placeholder rule while it is malformed."""
    page.goto(component_origin + "/hosted-file-rename")
    change = page.get_by_role("button", name="Change name")
    name = page.locator('input[name="slug"]')
    unchanged = page.locator("[data-unchanged-hint]")
    hint = page.locator("#hosted-file-rename-hint")
    assert change.is_disabled()
    assert unchanged.inner_text() == "Type a different placeholder name to change it."
    name.fill("")
    waits_with(change, hint, "Type the new placeholder name.")
    name.fill("Bad Name")
    waits_with(
        change,
        hint,
        "Use lowercase letters, digits and single hyphens, up to 64 characters.",
    )
    name.fill("parish-map-2026")
    assert change.is_enabled()
    name.fill("parish-map")
    assert change.is_disabled()


@pytest.mark.parametrize(
    ("path", "button", "reason"),
    [
        ("/weekly-manual-no-schedule", "Queue new manual report", "manual-no-schedule"),
        # The resolution question's answers are radio buttons (#1563).
        ("/delivery-held", ("radio", "It was sent"), "reason-found-held"),
        ("/delivery-held", ("radio", "It was not sent"), "reason-found-held"),
        (
            "/delivery-failed-responded",
            ("radio", "Yes"),
            "reason-retry-family_responded",
        ),
        (
            "/delivery-refusal-busy",
            "Clear verified refusal",
            "refusal-clear-unavailable",
        ),
        (
            "/restore-review-release-blocked",
            "Release the site",
            "restore-release-reason",
        ),
        ("/timeline-locked", "Open form", "open-form-reason"),
        (
            "/timeline-locked",
            "Export XLSX",
            "timeline-export-unavailable",
        ),
    ],
)
def test_known_refusals_are_shown_unavailable_with_why(
    page, component_origin, path, button, reason
):
    """An action the server would refuse for a reason the page knows is in
    view, unavailable, named by that visible reason, also after a restore
    from the back/forward cache (#563, #1331)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    role, name = button if isinstance(button, tuple) else ("button", button)
    controls = page.get_by_role(role, name=name, exact=True)
    assert controls.count() >= 1
    page.evaluate(RESTORE)
    for index in range(controls.count()):
        control = controls.nth(index)
        assert control.is_disabled()
        assert reason in control.get_attribute("aria-describedby").split()
    named = page.locator(f"#{reason}")
    visible(named)
    assert named.inner_text().strip()
    assert failures == []


def test_locked_ministry_worklist_offers_nothing_and_says_why(page, component_origin):
    """While other campaign work holds Ministry requests, its review
    worklist (#1684) draws no row action, box, bulk bar or Add, and the
    locked notice says why; the rows stay readable."""
    page.goto(component_origin + "/followup-gated")
    visible(page.locator("#followup-locked"))
    assert page.locator("#table table tbody tr").count() == 1
    controls = "[data-bulk-action], input[data-select-row], .table-actions button"
    assert page.locator(controls).count() == 0
    assert page.get_by_role("button", name="Add").count() == 0
