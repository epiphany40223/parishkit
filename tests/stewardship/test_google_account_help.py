"""The shared Google Account guidance and the never-signed-in count (#1593)."""

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import google_account_help
from parishkit.stewardship.accounts.user_views import never_signed_in


def test_the_login_page_is_only_the_button_and_the_family_portal_link():
    """The sign-in page carries no Google Account guidance (#2115).

    The guidance is on the not-authorized page, where a refused leader lands,
    and in the Users page About panel; the sign-in page keeps the heading,
    the Sign in with Google button and the Family campaign portal link.
    """
    html = render_to_string("stewardship/login.html", {})
    assert "data-google-account-help" not in html
    assert str(google_account_help.WHICH_ADDRESS) not in html
    assert "Use your authorized Google account" not in html
    assert "Sign in with Google" in html
    assert '<a href="/">Family campaign portal</a>' in html


def test_the_guidance_is_which_address_then_whom_to_ask():
    """No create-an-account paragraph or Google help link remains (#2115)."""
    html = render_to_string(
        "stewardship/components/google-account-help.html", google_account_help.context()
    )
    which = str(google_account_help.WHICH_ADDRESS).replace("'", "&#x27;")
    stuck = str(google_account_help.STILL_STUCK).replace("'", "&#x27;")
    assert html.count("<p>") == 2
    assert html.index(which) < html.index(stuck)
    assert "<a " not in html


def test_never_signed_in_counts_only_ministry_leaders_without_a_sign_in():
    """Staff and Administrators, and leaders who signed in, are not counted."""
    rows = [
        {"role": "ministry_leader", "last_login": None},
        {"role": "ministry_leader", "last_login": None},
        {"role": "ministry_leader", "last_login": "2026-10-01T12:00:00Z"},
        {"role": "staff", "last_login": None},
        {"role": "administrator", "last_login": None},
    ]
    assert never_signed_in(rows) == 2
    assert never_signed_in([]) == 0
