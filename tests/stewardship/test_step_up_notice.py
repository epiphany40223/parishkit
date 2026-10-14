"""The one shared step-up ("For security") notice (#1192).

Every Admin page that needs a sign-in from the last five minutes draws the
same notice from ``components/step-up-notice.html``, so the wording and
markup cannot drift between pages (ui-conventions "Page help"). These tests
render the component's variants and guard that no template words its own.
"""

import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENT = "stewardship/components/step-up-notice.html"
# Step-up phrasing only the component may use: its lead-in and the ways
# pages used to word their own hint (#1192, #1297 review).
STEP_UP_PHRASING = re.compile(
    r"For (your )?security"
    r"|sign-in from the last"
    r"|needs a (Google )?sign-in"
    r"|confirm(ing)? your (Google )?sign-in"
    r"|confirm it's you"
    r"|sign-in is too old"
    r"|last few minutes",
    re.IGNORECASE,
)
# The idle sign-out banner says "For security, you were signed out ..." and is
# not a step-up (ui-conventions "Page help"): the one exact sentence exempt.
NOT_A_STEP_UP = "For security, you were signed out after a period of inactivity."
# The hand-worded step-ups this guard replaced; each must still be caught.
OLD_STEP_UPS = [
    "For your security, confirm your sign-in before you close or reopen the"
    " Family portal.",
    "For security, saving or removing settings here needs a sign-in from the"
    " last five minutes.",
    "Changing this requires you to confirm your sign-in with Google, and every"
    " change is recorded in the audit log.",
    "Cancelling go-live needs a Google sign-in from the last five minutes.",
    "Going live needs a sign-in from the last five minutes.",
    "Sending tests to real Families requires confirming your sign-in with"
    " Google (you signed in 7 minutes ago).",
    "Your Google sign-in is too old for installing credentials.",
    "Saving a credential needs a sign-in from the last five minutes.",
    "Confirming needs a Google sign-in from the last few minutes; if yours is"
    " older, you are asked to confirm it's you with Google first.",
    "Approving a session first asks you to confirm your sign-in, then takes"
    " you to the approval page.",
]


def step_up_wording(text):
    """Whether template text words a step-up itself, outside the exemption."""
    return bool(STEP_UP_PHRASING.search(text.replace(NOT_A_STEP_UP, "")))


def notice(local=False, **values):
    """Render the component, in LOCAL or elsewhere, with the given parameters."""
    context = {"action": "closing the portal", "csrf_token": "a" * 64} | values
    if local:
        context["local_environment"] = True
    return render_to_string(COMPONENT, context)


def test_no_template_words_its_own_step_up_notice():
    """Every step-up sentence comes from the shared component."""
    offenders = [
        str(path.relative_to(TEMPLATES))
        for path in sorted(TEMPLATES.rglob("*.html"))
        if path != TEMPLATES / "components/step-up-notice.html"
        and step_up_wording(path.read_text())
    ]
    assert offenders == []


@pytest.mark.parametrize("sentence", OLD_STEP_UPS)
def test_the_guard_catches_each_old_hand_worded_step_up(sentence):
    """A page that brought back its own wording would fail the guard."""
    assert step_up_wording(f"<p>{{% translate {sentence!r} %}}</p>")


def test_the_idle_sign_out_banner_is_the_only_exemption():
    """The exemption is one exact sentence, so it cannot hide a step-up."""
    assert not step_up_wording(NOT_A_STEP_UP)
    assert step_up_wording(NOT_A_STEP_UP + " For security, confirm your sign-in.")


def test_with_a_return_page_it_offers_confirm_with_google():
    """The canonical sentence, the follow-up sentence, then the shared control."""
    html = notice(next="/admin/mail/family-portal/", id="why", marker="x-hook")
    assert html.startswith('<div class="notice" data-step-up-notice data-x-hook="">')
    assert '<p id="why">' in html
    assert (
        "For security, closing the portal needs a Google sign-in from the last"
        " five minutes. Confirm it's you with Google; you will come back to this"
        " page.</p>"
    ) in html
    assert 'action="/admin/login"' in html
    assert 'name="next" value="/admin/mail/family-portal/"' in html
    assert "Confirm with Google" in html
    assert 'role="alert"' not in html


def test_the_outcome_and_note_follow_the_lead_in():
    """A refused attempt says what happened before what to do next."""
    html = notice(
        next="/admin/",
        next_label="Home",
        outcome="Nothing was changed.",
        note="Then try again.",
        role="alert",
        marker="done",
        marker_value="reauthenticate",
    )
    # The live role covers the sentence only, never the Confirm control.
    assert '<div class="notice" data-step-up-notice data-done="reauthenticate">' in html
    assert '<p role="alert">' in html
    assert (
        "last five minutes. Nothing was changed. Confirm it's you with Google;"
        " you will then return to Home. Then try again.</p>"
    ) in html


@pytest.mark.parametrize("local", [False, True])
def test_inside_a_form_it_explains_without_a_control(local):
    """With no return page there is no nested form; the action asks on send."""
    html = notice(local=local)
    assert "<form" not in html
    if local:
        assert "Google" not in html
        assert "When you continue, you are asked to confirm your sign-in first." in (
            html
        )
    else:
        assert (
            "When you continue, you are asked to confirm it's you with Google first."
        ) in html


def test_local_never_mentions_google():
    """LOCAL keeps the sentence and the laptop sign-in, without Google (#619)."""
    html = notice(local=True, next="/admin/", outcome="Nothing was changed.")
    assert "Google" not in html
    assert (
        "For security, closing the portal needs a sign-in from the last five"
        " minutes. Nothing was changed.</p>"
    ) in html
    assert 'data-local-step-up="/admin/"' in html
