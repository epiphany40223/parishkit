"""Actions the server would refuse are shown unavailable, with why (#563).

Slice 3 of the conditional-fields audit: the setup credential steps, Finish
setup, the campaign test email, Send to chosen Families and Prepare Family
links render an action the server would refuse as unavailable, named by the
visible reason (``aria-describedby``), instead of leaving it enabled or out.
These checks pin that markup and the server's required-key rule. The browser
behavior is in browser/test_prerequisite_gates.py.
"""

import re
from types import SimpleNamespace as Value
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.campaign_family_test_views import (
    FamilyTestForm,
    _unavailable,
)
from parishkit.stewardship.accounts.setup_confirmation_views import (
    SetupConfirmationForm,
)
from parishkit.stewardship.accounts.setup_credential_views import (
    KEEP_ERRORS,
    MISSING,
    SetupCredentialForm,
)
from parishkit.stewardship.accounts.setup_wizard import BY_KEY
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import window_table

from .test_setup_final_steps import actions, page, wizard

BLOCKED = ("Save the outgoing email settings first.", "/admin/setup/mail")


def credential_page(target="google_workspace", *, prerequisite=None, **form):
    """Render one setup credential step, with its form built as the view does."""
    saved = form.get("saved", False)
    # Not page(), whose "current" is the step key: here "current" says the
    # saved key is still current, which a stale or blocked step's is not.
    return render_to_string(
        "stewardship/setup-credential.html",
        {
            "draft": {"status": {"version": 3}},
            "wizard": wizard(target),
            "current": saved and not form.get("stale") and not prerequisite,
            "form": SetupCredentialForm(target, **form),
            "target": target,
            "label": "Google Workspace",
            "step_label": BY_KEY[target].label,
            "saved": saved,
            "organization": form.get("organization"),
            "prerequisite": prerequisite,
        },
    )


def submit(html):
    """The Save and continue button's opening tag."""
    (tag,) = re.findall(r"<button[^>]*>(?=Save and continue)", actions(html))
    return tag


def field(html, name):
    """The opening tag of the form control called ``name``."""
    (tag,) = re.findall(rf'<(?:input|textarea)[^>]*name="{name}"[^>]*>', html)
    return tag


@pytest.mark.parametrize("saved", [False, True])
def test_a_blocked_credential_step_shows_save_unavailable_with_its_reason(saved):
    """Save is disabled and names the prerequisite; no gate or hint is added."""
    html = credential_page(prerequisite=BLOCKED, saved=saved)
    (reason,) = re.findall(r'<p[^>]*id="credential-prerequisite"[^>]*>([^<]*)', html)
    assert reason.strip() == BLOCKED[0]
    button = submit(html)
    assert " disabled" in button
    assert 'aria-describedby="credential-prerequisite"' in button
    assert "data-require-complete" not in html
    assert "data-complete-hint" not in html


def test_an_unsaved_credential_waits_for_its_key_with_a_hint_after_save():
    """The key is required, the form is gated and its hint follows the row."""
    html = credential_page()
    (form,) = re.findall(r'<form method="post" class="panel"[^>]*>', html)
    assert "data-require-complete" in form
    key = field(html, "candidate")
    assert " required" in key
    assert f'data-missing-hint="{MISSING["google_workspace"]}"' in key
    button = submit(html)
    assert " disabled" not in button
    assert 'aria-describedby="setup-complete-hint"' in button
    tail = html.split('<div class="setup-actions">', 1)[1].split("</div>", 1)[1]
    assert '<p class="help" id="setup-complete-hint" data-complete-hint hidden>' in tail


def test_a_stale_saved_credential_must_be_entered_again():
    """A key saved for changed settings is required, with the keep message."""
    form = SetupCredentialForm("slack", {}, saved=True, stale=True)
    assert form.fields["candidate"].required
    assert not form.is_valid()
    assert form.errors["candidate"] == [str(KEEP_ERRORS["stale"])]
    html = credential_page("slack", saved=True, stale=True)
    assert " required" in field(html, "candidate")


def test_a_current_saved_credential_may_be_kept():
    """A current key is optional; only an organization change needs the key."""
    form = SetupCredentialForm("slack", {}, saved=True)
    assert not form.fields["candidate"].required
    assert "data-required-when" not in form.fields["candidate"].widget.attrs
    html = credential_page("parishsoft", saved=True, organization=4321)
    key = field(html, "candidate")
    assert " required" not in key
    assert 'data-required-when="organization_id!=4321"' in key
    assert "enter the ParishSoft API key again" in key
    assert "data-missing-hint=" in field(html, "organization_id")


def test_finish_setup_names_the_readiness_problem_on_its_unavailable_button():
    """Finish is disabled and described by the problem; ready, it is plain."""
    problem = "Send the required email test for these settings first."
    html = page(
        "setup-confirmation.html",
        "confirmation",
        form=SetupConfirmationForm(),
        candidate_digest="a" * 64,
        readiness_problem=problem,
    )
    assert f'<p role="status" id="setup-readiness">{problem}</p>' in html
    (button,) = re.findall(r"<button[^>]*>(?=Check readiness)", actions(html))
    assert " disabled" in button
    assert 'aria-describedby="setup-readiness"' in button
    ready = page(
        "setup-confirmation.html",
        "confirmation",
        form=SetupConfirmationForm(),
        candidate_digest="a" * 64,
    )
    assert "aria-describedby" not in actions(ready)
    assert 'id="setup-readiness"' not in ready


def mail_page(pending):
    """The campaign test email page with or without a test on its way."""
    return render_to_string(
        "stewardship/campaign-mail.html",
        {
            "campaign": Value(active_configuration=Value(name="Annual")),
            "testing_recipient": "testing@example.org",
            "form": {},
            "items": [],
            "pending": pending,
        },
    )


def test_a_pending_campaign_test_names_its_reason_on_send():
    """Send is disabled and described by the pending notice only while pending."""
    html = mail_page(True)
    (button,) = re.findall(r"<button[^>]*>(?=Send this test email)", html)
    assert " disabled" in button
    assert 'aria-describedby="campaign-test-pending"' in button
    assert 'id="campaign-test-pending"' in html
    (button,) = re.findall(r"<button[^>]*>(?=Send this test email)", mail_page(False))
    assert "disabled" not in button and "aria-describedby" not in button


def preview(*, epoch=True, held=False, eligible=(True,), available=10):
    """A Family test review as campaign_family_test.prepare returns it."""
    return Value(
        epoch_id=UUID(int=1) if epoch else None,
        held=held,
        families=[Value(eligible=value) for value in eligible],
        available=available,
    )


@pytest.mark.parametrize(
    ("review", "words"),
    [
        (preview(epoch=False, held=True, eligible=(False,)), "Test codes"),
        (preview(held=True, eligible=(False,)), "Campaign work is in progress"),
        (preview(eligible=(True, False), available=1), "A chosen Family cannot"),
        (preview(eligible=(True, True), available=1), "Only 1 more Family test may"),
        (preview(eligible=(True,) * 3, available=2), "Only 2 more Family tests may"),
    ],
)
def test_family_tests_name_the_first_reason_they_cannot_be_sent(review, words):
    """Each refusal the confirmation rechecks has its own reason, in order."""
    assert str(_unavailable(review)).startswith(words)


def test_family_tests_that_can_be_sent_have_no_reason():
    """Eligible Families within the allowance and ready codes can be sent."""
    assert _unavailable(preview(eligible=(True, True), available=2)) is None


def family_page(**values):
    """The Send to chosen Families page with one reviewed Family."""
    return render_to_string(
        "stewardship/campaign-mail-families.html",
        {
            "campaign": Value(active_configuration=Value(name="Annual")),
            "form": FamilyTestForm(),
            "families": [{"name": "Sample", "duid": 1234, "label": "Eligible"}],
            "available": 3,
            "epoch_ready": True,
        }
        | values,
    )


def test_family_tests_wait_for_a_duid_and_show_send_unavailable_with_why():
    """Check waits for the DUID box; an unsendable review shows Send greyed."""
    html = family_page(confirm=True, sendable=False, unavailable="Only 1 more.")
    (form,) = re.findall(r"<form [^>]*>(?=[^<]*<input[^>]*value=\"preview\")", html)
    assert "data-require-complete" in form
    box = field(html, "families")
    assert " required" in box and "Enter at least one Family DUID" in box
    check = html.split("Check these Families", 1)[0].rsplit("<button", 1)[1]
    assert 'aria-describedby="family-test-check-hint"' in check
    after = html.split("Check these Families</button>", 1)[1]
    assert after.startswith('<p class="help" id="family-test-check-hint"')
    assert '<p role="status" id="family-test-unavailable">Only 1 more.</p>' in html
    (send,) = re.findall(r"<button[^>]*>(?=Send these Family tests)", html)
    assert 'type="button"' in send and " disabled" in send
    assert 'aria-describedby="family-test-unavailable"' in send


def links_page(**values):
    """Prepare Family links with no earlier preparation listed."""
    return render_to_string(
        "stewardship/go-live-links.html",
        {
            "campaign": Value(active_configuration=Value(name="Annual")),
            "transition": Value(pk=UUID(int=2)),
            "table": window_table(PageWindow(1, 25), [], False),
        }
        | values,
    )


@pytest.mark.parametrize(
    ("values", "words"),
    [
        ({"inputs_unavailable": True}, "Links can't be prepared yet."),
        ({}, "Links are already prepared or being prepared below."),
    ],
)
def test_prepare_is_shown_unavailable_with_its_reason(values, words):
    """Without a prepare control, Prepare is greyed and described by why."""
    html = links_page(**values)
    (reason,) = re.findall(r'<p role="status" id="prepare-unavailable">([^<]*)', html)
    assert reason.startswith(words)
    (button,) = re.findall(r"<button[^>]*>(?=Prepare inactive Family links)", html)
    assert " disabled" in button and 'aria-describedby="prepare-unavailable"' in button


def test_prepare_is_available_while_the_server_offers_it():
    """With a signed prepare control, Prepare submits and gives no reason."""
    html = links_page(prepare="synthetic-prepare")
    assert 'id="prepare-unavailable"' not in html
    (button,) = re.findall(r"<button[^>]*>(?=Prepare inactive Family links)", html)
    assert 'type="submit"' in button and "disabled" not in button
