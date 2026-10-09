"""Saved content the sanitizer now cleans is flagged for a re-save (#832)."""

import pytest

from parishkit.stewardship.accounts.content_forms import stale_markup


@pytest.mark.parametrize("html", ["<p>Hi {{ family_name }}</p>", "<h2>Welcome</h2>"])
def test_clean_saved_html_needs_no_resave(html):
    """HTML already in today's cleaned form is not flagged."""
    assert stale_markup({"html": html}) is None


def test_missing_content_needs_no_resave():
    """An empty slot has nothing saved to re-save."""
    assert stale_markup(None) is None


def test_removed_markup_is_named():
    """The flag names, in plain words, what sending already removes."""
    stale = stale_markup(
        {"html": '<p onclick="x()">Hi</p><!-- old --><script>x()</script>'}
    )
    assert stale.removed == (
        "<script> element and its content",
        "HTML comments",
        "onclick attribute",
    )
    assert stale.lost == () and not stale.blocked


def test_a_structure_only_rewrite_is_flagged_without_removals():
    """<b> becoming <strong> changes the stored form but removes nothing."""
    stale = stale_markup({"html": "<p><b>Hi</b></p>"})
    assert stale.removed == () and stale.lost == () and not stale.blocked


def test_a_placeholder_only_in_removed_markup_is_lost():
    """Families never see a placeholder that only a removed comment held."""
    stale = stale_markup(
        {
            "kind": "page",
            "slot": "welcome",
            "html": "<p>Hi</p><!-- {{ family_name }} -->",
        }
    )
    assert stale.lost == ("{{ family_name }}",) and not stale.blocked


@pytest.mark.parametrize("slot", ["initial", "reminder"])
def test_a_family_email_that_lost_its_code_cannot_be_sent(slot):
    """Sending refuses an invitation or reminder without its code or link."""
    stale = stale_markup(
        {
            "kind": "email",
            "slot": slot,
            "html": '<p data-x="{{ family_code }}">Open {{ family_url }}</p>',
        }
    )
    assert stale.lost == ("{{ family_code }}",) and stale.blocked


def test_other_emails_are_never_blocked_by_a_lost_placeholder():
    """Only invitations and reminders need the Family code and link."""
    stale = stale_markup(
        {
            "kind": "email",
            "slot": "confirmation",
            "html": "<p>Thanks</p><!-- {{ family_code }} -->",
        }
    )
    assert stale.lost == ("{{ family_code }}",) and not stale.blocked
