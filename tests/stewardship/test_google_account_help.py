"""The shared Google Account guidance and the never-signed-in count (#1593)."""

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import google_account_help
from parishkit.stewardship.accounts.user_views import never_signed_in


def test_the_login_page_shows_the_guidance_once_from_the_shared_wording():
    """The sign-in page renders the one copy of the wording, with Google's help."""
    html = render_to_string("stewardship/login.html", {})
    assert html.count("data-google-account-help") == 1
    assert str(google_account_help.NO_ACCOUNT).replace("'", "&#x27;") in html
    assert f'href="{google_account_help.HELP_URL}"' in html
    assert 'rel="noopener noreferrer"' in html


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
