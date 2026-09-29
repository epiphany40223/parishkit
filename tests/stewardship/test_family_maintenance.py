"""The maintenance switch's pure rules: which routes close and what note is allowed."""

import json
from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts import family_maintenance


@pytest.mark.parametrize(
    ("path", "closed"),
    [
        ("/", True),
        ("/access/abc", True),
        ("/family/", True),
        ("/family/form", True),
        ("/family/submit", True),
        ("/family/presence", True),
        ("/family/keepalive", False),
        ("/family/logout", False),
    ],
)
def test_the_switch_closes_family_routes_but_not_the_session_endpoints(path, closed):
    """Keepalive and logout touch only the Family's own session."""
    assert family_maintenance.gates(SimpleNamespace(path_info=path)) is closed


def test_the_note_is_trimmed_bounded_and_address_free():
    """Whitespace is collapsed; overlong text and email addresses are refused."""
    assert family_maintenance.clean_message("  Back \n by   3 PM ") == "Back by 3 PM"
    assert family_maintenance.clean_message(None) == ""
    assert family_maintenance.clean_message("x" * 500) == "x" * 500
    with pytest.raises(ValueError):
        family_maintenance.clean_message("x" * 501)
    with pytest.raises(ValueError):
        family_maintenance.clean_message("Write to office@example.org")


def test_the_form_script_gets_a_clean_json_503():
    """JSON endpoints answer the form script with the note and a retry hint."""
    state = family_maintenance.MaintenanceState(closed=True, message="Back soon")
    response = family_maintenance.family_response(
        SimpleNamespace(path_info="/family/submit"), state
    )
    assert response.status_code == 503
    assert response["Retry-After"] == str(family_maintenance.RETRY_AFTER_SECONDS)
    assert response["Cache-Control"] == "no-store"
    assert json.loads(response.content) == {"maintenance": True, "message": "Back soon"}
