"""The Backup encryption key change, in place and in the review dialog (#750).

Its two proof steps act in place: Continue swaps the change region to the
challenge, whose heading then takes focus, and Start over swaps it back; a
refused paste is swapped in with its error summary focused. The code's
Review opens the shared review dialog with a compact review of the old and
new fingerprints; a wrong or expired code stays on the form, said in the
change region's status line with no jump to the top. Apply closes the dialog,
redraws the key in use and the change region from the page's address and
says what was applied in the status line under the step, with no reload.
Pages and answers are ``integration_dialog_components``'.
"""

import re

import pytest

from .integration_dialog_components import (
    BACKUP_KEY,
    BACKUP_KEY_EXPIRED,
    BACKUP_KEY_PROOF,
    BACKUP_KEY_REFUSED,
    BACKUP_KEY_REVIEW,
    BACKUP_KEY_SETTLED,
    BACKUP_KEY_WRONG,
    NEW_FINGERPRINT,
    OLD_FINGERPRINT,
)
from .test_in_place import MARK, MARKED, count_requests
from .test_integration_review_dialog import answer, dialog_of
from .waits import eventually, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
FOCUSED = "document.activeElement && document.activeElement.id"
# A canonical X25519 public key's shape: 43 base64 characters and "=".
PUBLIC_KEY = "A" * 43 + "="
CODE = "ABCD-2345-EFGH"


def opened(page, origin):
    """Load the key page, tall enough that no focus needs to scroll, and
    return the script errors it raises (the page is marked so a reload
    shows)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": 1280, "height": 1600})
    page.goto(origin + BACKUP_KEY)
    page.evaluate(MARK)
    return failures


def answer_reads(page, origin, fixture):
    """Answer every in-place read (a GET fetch, never a page load) of the
    key page with ``fixture``; let everything else through. Returns the
    reads."""
    reads = []

    def handle(route):
        """Serve ``fixture`` for a GET fetch only."""
        request = route.request
        if request.method != "GET" or request.resource_type == "document":
            route.fallback()
            return
        reads.append(request.url)
        route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))

    page.route(lambda url: url.split("#")[0] == origin + BACKUP_KEY, handle)
    return reads


def continue_to_proof(page):
    """Paste the key and select Continue."""
    page.get_by_label("New public key").fill(PUBLIC_KEY)
    page.get_by_role("button", name="Continue").click()


def test_the_proof_steps_act_in_place(page, component_origin):
    """Continue swaps in the challenge without a reload, with focus on its
    heading and the address unchanged; Start over swaps the paste step back
    in place."""
    answer(page, component_origin, BACKUP_KEY, "challenge", BACKUP_KEY_PROOF)
    failures = opened(page, component_origin)
    scrolled = page.evaluate("window.scrollY")
    continue_to_proof(page)
    visible(page.get_by_label("Code from backup-prove"))
    eventually(page, f"({FOCUSED}) === 'prove-key'")
    assert page.locator("#backup-key-change code", has_text="PKBKP1:").count() == 1
    assert page.evaluate("window.scrollY") == scrolled
    assert page.url == component_origin + BACKUP_KEY
    assert page.evaluate(MARKED) == "kept"
    page.get_by_role("link", name="Start over").click()
    visible(page.get_by_label("New public key"))
    assert page.get_by_label("Code from backup-prove").count() == 0
    # The paste step's heading takes focus (the link that was selected is
    # gone), so the reader hears where they are.
    eventually(page, "document.activeElement?.textContent.trim() === 'Replace the key'")
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def test_a_refused_paste_is_shown_in_place(page, component_origin):
    """A refused paste (status 400) is swapped in: the field is marked, the
    error summary takes focus, and nothing reloads."""
    answer(
        page, component_origin, BACKUP_KEY, "challenge", BACKUP_KEY_REFUSED, status=400
    )
    failures = opened(page, component_origin)
    continue_to_proof(page)
    field = page.get_by_label("New public key")
    eventually(
        page,
        "document.getElementById('backup-key-public')"
        ".getAttribute('aria-invalid') === 'true'",
    )
    error = page.locator("#backup-key-public-error")
    assert "not a backup public key" in error.inner_text()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    assert page.evaluate(
        "Boolean(document.activeElement?.closest('[data-error-summary]'))"
    )
    assert field.is_visible()
    assert page.evaluate(MARKED) == "kept"
    assert not failures


def refuse_code(page, origin, fixture):
    """Reach the challenge, scroll its code field into a short window and
    check a code the server refuses with ``fixture`` (status 400). Returns
    the script errors and where the page was scrolled before the check."""
    answer(page, origin, BACKUP_KEY, "challenge", BACKUP_KEY_PROOF)
    answer(page, origin, BACKUP_KEY, "preview", fixture, status=400)
    failures = opened(page, origin)
    continue_to_proof(page)
    code = page.get_by_label("Code from backup-prove")
    code.fill(CODE)
    # A short window, scrolled down to the code: a summary placed above the
    # page heading and focused would scroll the page back to the top.
    page.set_viewport_size({"width": 1280, "height": 500})
    code.evaluate("node => node.scrollIntoView({block: 'center'})")
    scrolled = page.evaluate("window.scrollY")
    assert scrolled > 0
    page.get_by_role("button", name="Check code and continue").click()
    eventually(
        page,
        "document.getElementById('backup-key-code')"
        ".getAttribute('aria-invalid') === 'true'",
    )
    return failures, scrolled


def assert_refused_in_place(page, scrolled, words):
    """The dialog stays shut and the refusal is said in the change region's
    status line, which has focus and describes the field, with no error
    summary anywhere and no jump (UX conventions, "Review dialog")."""
    assert not page.locator("#backup-key-review").evaluate("node => node.open")
    line = page.locator("#backup-key-outcome")
    eventually(page, f"({FOCUSED}) === 'backup-key-outcome'")
    assert words in line.inner_text()
    assert page.locator("[data-error-summary]").count() == 0
    # No line is added at the field to move the button under the pointer.
    assert page.locator("#backup-key-proof .errorlist").count() == 0
    described = page.get_by_label("Code from backup-prove").get_attribute(
        "aria-describedby"
    )
    assert "backup-key-outcome" in described.split()
    assert page.evaluate("window.scrollY") >= scrolled
    assert page.evaluate(MARKED) == "kept"


def test_a_wrong_code_is_refused_in_place(page, component_origin):
    """A wrong code (status 400, the challenge again) keeps the dialog shut
    and is said in the region's status line, where the reader is; the next
    edit of the code clears it, and so does Start over."""
    failures, scrolled = refuse_code(page, component_origin, BACKUP_KEY_WRONG)
    assert_refused_in_place(page, scrolled, "That code does not match.")
    line = page.locator("#backup-key-outcome")
    page.get_by_label("Code from backup-prove").press("End")
    page.keyboard.type("X")
    eventually(page, "document.getElementById('backup-key-outcome').textContent === ''")
    assert (
        page.get_by_label("Code from backup-prove").get_attribute("aria-invalid")
        == "false"
    )
    # Start over swaps in the paste step, with its own empty status line.
    page.get_by_role("link", name="Start over").click()
    visible(page.get_by_label("New public key"))
    assert line.inner_text() == ""
    assert page.locator("[data-error-summary]").count() == 0
    assert not failures


def test_an_expired_code_says_so_in_place(page, component_origin):
    """A code checked after its challenge expired is refused the same way,
    in its own words: the code expired, and Start over gets a new one."""
    failures, scrolled = refuse_code(page, component_origin, BACKUP_KEY_EXPIRED)
    assert_refused_in_place(page, scrolled, "This code has expired")
    text = page.locator("#backup-key-outcome").inner_text()
    assert "Choose Start over and paste the public key again" in text
    assert "did not expect" not in text
    # The whole sentence is shown, wrapped, never cut short.
    assert page.locator("#backup-key-outcome").evaluate(
        "node => node.scrollWidth <= node.clientWidth"
    )
    assert not failures


def test_a_proved_key_is_reviewed_and_applied_in_place(page, component_origin):
    """The code's Review opens the compact review on its question; Apply
    closes it, redraws the key in use and the paste step from the page's
    address and says what was applied, with focus on the status line, no
    reload and no scroll."""
    answer(page, component_origin, BACKUP_KEY, "challenge", BACKUP_KEY_PROOF)
    reviews = answer(page, component_origin, BACKUP_KEY, "preview", BACKUP_KEY_REVIEW)
    failures = opened(page, component_origin)
    posts = count_requests(page, "POST", BACKUP_KEY)
    continue_to_proof(page)
    page.get_by_label("Code from backup-prove").fill(CODE)
    scrolled = page.evaluate("window.scrollY")
    page.get_by_role("button", name="Check code and continue").click()
    dialog = dialog_of(page, "Replace the backup encryption key?")
    assert page.evaluate(FOCUSED) == "backup-key-review-title"
    lines = dialog.locator("#integration-review-lines")
    assert lines.inner_text().startswith(f"Key fingerprint: {OLD_FINGERPRINT}")
    assert NEW_FINGERPRINT in lines.inner_text()
    assert "keep it until they have all been deleted" in dialog.inner_text()
    assert len(reviews) == 1 and "intent=synthetic-intent" in reviews[0]
    # Only the signed preview travels with Apply, never the intent.
    carried = dialog.locator("form[data-review-apply] input[data-review-carried]")
    assert carried.evaluate_all("nodes => nodes.map((node) => node.name)") == [
        "preview"
    ]
    # The redraw once applied reads the page's address: the new key in use.
    reads = answer_reads(page, component_origin, BACKUP_KEY_SETTLED)
    dialog.get_by_role("button", name="Apply").click()
    hidden(dialog)
    eventually(page, "document.getElementById('backup-key-outcome').textContent !== ''")
    line = page.locator("#backup-key-outcome")
    assert re.search(
        r"^Backup encryption key replaced\. Applied at \d{1,2}:\d{2}\s?[AP]M\.$",
        line.inner_text(),
    ), line.inner_text()
    assert page.evaluate(FOCUSED) == "backup-key-outcome"
    assert NEW_FINGERPRINT in page.locator("#backup-key-current").inner_text()
    visible(page.get_by_label("New public key"))
    assert reads
    assert page.evaluate("window.scrollY") == scrolled
    assert page.url == component_origin + BACKUP_KEY
    assert page.evaluate(MARKED) == "kept"
    # Continue, the code's Review and Apply: one POST each, no page loads.
    assert len(posts) == 3
    assert not failures
