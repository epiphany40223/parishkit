"""Acknowledgments and typed reasons say why their action waits (#563).

These checks pin the markup the page script reads: which forms use the
complete gate (data-require-complete) so a reason or note of only spaces
counts as empty, the hint each control gives, and where each hint sits
(after the button, so it never moves a control above it). The typed
"Production" pattern must accept exactly what the server accepts. The
browser behavior itself is in browser/test_acknowledgment_hints.py.
"""

import re
from datetime import UTC, datetime
from types import SimpleNamespace as Value
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from .browser.delivery_components import components as delivery_components

NOW = datetime(2054, 10, 1, 12, tzinfo=UTC)
CAMPAIGN = Value(
    pk=uuid4(),
    active_configuration=Value(name="Annual census", starts_at=NOW, ends_at=NOW),
)


def delivery_page(path):
    """Render one synthetic Mail delivery component page by its path."""
    for name, template, context in delivery_components(NOW):
        if name == path:
            return render_to_string(f"stewardship/{template}.html", context)
    raise LookupError(path)


def tag(html, pattern):
    """The single opening tag matching ``pattern``."""
    (found,) = re.findall(pattern, html)
    return found


def hint_follows(html, button_label, hint_id):
    """Assert the button names the hint and the hint comes right after it."""
    button = tag(html, rf"<button[^>]*>{re.escape(button_label)}</button>")
    assert f'aria-describedby="{hint_id}"' in button
    after = html.split(button, 1)[1]
    assert after.startswith(
        f'<p class="help" id="{hint_id}" data-complete-hint hidden></p>'
    )


def test_cancel_go_live_waits_for_a_reason_and_its_tick():
    """The reason and the tick each carry the hint the gate shows."""
    html = render_to_string(
        "stewardship/production-withdrawal.html",
        {"campaign": CAMPAIGN, "available": True, "fresh": True},
    )
    form = tag(html, r'<form method="post" data-require-complete>')
    assert form
    reason = tag(html, r'<textarea id="withdrawal-reason"[^>]*>')
    assert "required" in reason
    assert 'data-missing-hint="Enter the reason for cancelling go-live."' in reason
    box = tag(html, r'<input type="checkbox" name="acknowledged"[^>]*>')
    assert "data-acknowledgment" in box
    assert 'data-missing-hint="Tick the box to confirm' in box
    hint_follows(html, "Preview cancellation", "withdrawal-complete-hint")


def test_delivery_resolutions_wait_for_a_note():
    """Every resolution form uses the gate, with a hint per form."""
    html = delivery_page("/delivery")
    forms = re.findall(r'<form method="post" action="[^"]*resolution[^"]*"[^>]*>', html)
    assert forms and all("data-require-complete" in form for form in forms)
    notes = re.findall(r'<textarea id="evidence-[^>]*>', html)
    assert len(notes) == len(forms)
    assert all(
        'data-missing-hint="Enter the evidence or reason for this decision."' in note
        for note in notes
    )
    hint_follows(html, "Save evidence note", "complete-hint-note")
    hint_follows(
        html, "Confirm delivery using external evidence", "complete-hint-accept"
    )


def test_refusal_removal_waits_for_a_note_and_its_tick():
    """Clear verified refusal uses the gate, with its hint after it."""
    html = delivery_page("/delivery-refusal")
    assert tag(html, r'<form method="post" action="[^"]*"[^>]*data-require-complete>')
    note = tag(html, r'<textarea id="verification-note"[^>]*>')
    assert 'data-missing-hint="Describe how you verified the mailbox."' in note
    box = tag(html, r'<input type="checkbox" name="verified"[^>]*>')
    assert 'data-missing-hint="Tick the box to confirm that you verified' in box
    hint_follows(html, "Clear verified refusal", "refusal-complete-hint")


def confirmation_page():
    """Render Confirm Production with a fresh confirmation to type."""
    return render_to_string(
        "stewardship/production-confirmation.html",
        {
            "campaign": CAMPAIGN,
            "transition": Value(pk=uuid4()),
            "state": Value(target_state="active", observed_at=NOW),
            "confirmation_token": "synthetic",
            "fresh": True,
        },
    )


def test_confirm_production_waits_for_the_typed_word():
    """The typed field is under the gate, with its hint after Confirm."""
    html = confirmation_page()
    assert tag(html, r'<form method="post" data-require-complete>')
    typed = tag(html, r'<input id="production-confirmation"[^>]*>')
    assert 'data-missing-hint="Type Production exactly as shown to confirm."' in typed
    hint_follows(html, "Confirm Production", "production-complete-hint")


@pytest.mark.parametrize(
    "typed",
    [
        "Production",
        " Production",
        "Production  ",
        "\tProduction\n",
        "production",
        "Productio",
        "Production!",
        "Pro duction",
        "",
        "  ",
    ],
)
def test_the_typed_pattern_accepts_what_the_server_accepts(typed):
    """The browser's pattern (anchored, as HTML anchors it) agrees with the
    server's ``typed.strip() == "Production"`` for ordinary input."""
    (pattern,) = re.findall(r'pattern="([^"]+)"', confirmation_page())
    assert bool(re.fullmatch(pattern, typed)) == (typed.strip() == "Production")


def test_setup_reset_is_an_acknowledgment():
    """Reset all pages and emails waits for its tick, with its own hint."""
    html = render_to_string(
        "stewardship/setup-content.html",
        {"draft": {"status": {"version": 3}}, "groups": []},
    )
    box = tag(html, r'<input type="checkbox" name="confirm"[^>]*>')
    assert "data-acknowledgment" in box
    assert (
        'data-missing-hint="Tick the box to confirm replacing every page and email."'
        in box
    )
