"""Sign-in denials name a fixed category, never the reason within it."""

import pytest

from parishkit.stewardship.web.security import login_denial

TEXTS = {
    "": b"Sign-in is unavailable. Please try again.",
    "code": b"This Family code cannot be found or used.",
    "link": b"This secure Family link cannot be found or used.",
    "unavailable": b"Sign-in is temporarily unavailable. Please try again later.",
}


@pytest.mark.parametrize("kind", sorted(TEXTS))
def test_each_kind_renders_only_its_fixed_text(kind):
    """Every kind keeps no-store, a fixed retry route and exactly one message."""
    response = login_denial(status=403, kind=kind)
    assert response.stewardship_safe_error
    assert response["Cache-Control"] == "no-store"
    assert b'href="/"' in response.content
    assert [text for text in TEXTS.values() if text in response.content] == [
        TEXTS[kind]
    ]


def test_admin_default_text_and_retry_route_are_unchanged():
    """An Admin refusal that is not a rate limit or outage keeps the old text."""
    response = login_denial(admin=True, status=403)
    assert response.status_code == 403
    assert TEXTS[""] in response.content
    assert b'href="/admin/login"' in response.content


def test_unknown_kind_fails_closed():
    """A misspelled kind is a programming error, not a silent fallback."""
    with pytest.raises(ValueError):
        login_denial(kind="expired")


@pytest.mark.parametrize(
    "status,text",
    [
        (429, b"Sign-in is temporarily unavailable. Please try again later."),
        (503, b"Sign-in is temporarily unavailable. Please try again later."),
        (403, b"Sign-in is unavailable. Please try again."),
    ],
)
def test_admin_rate_limits_and_outages_read_as_temporary(status, text):
    """Admin OAuth shares the Family temporary text; other refusals do not."""
    from parishkit.stewardship.accounts.authentication import denial

    response = denial(status=status, retry=5 if status != 403 else None)
    assert response.status_code == status and text in response.content
    assert b'href="/admin/login"' in response.content
    # Retry-After accompanies only the retryable statuses, as before.
    assert (response.get("Retry-After") == "5") is (status != 403)


@pytest.mark.parametrize("status", [429, 503])
def test_access_gate_rate_limits_and_outages_use_the_family_route(status):
    """The /access/ middleware path renders the temporary text, retrying at /."""
    from parishkit.stewardship.accounts.authentication import denial

    response = denial(status=status, retry=5, admin=False)
    assert response.status_code == status and response["Retry-After"] == "5"
    assert TEXTS["unavailable"] in response.content
    assert b'href="/"' in response.content


# A current Admin session whose role lacks the page (#1924): one wording per
# role level, never the sign-in prompt.
ROLE_TEXTS = {
    "administrator": "This page is for Administrators. Your access doesn't include it.",
    "staff": (
        "This page is for Administrators and Staff. Your access doesn't include it."
    ),
}


@pytest.mark.parametrize(
    "capability,level",
    [
        ("configure", "administrator"),
        ("manage_users", "administrator"),
        ("system_logs", "administrator"),
        ("background_work", "administrator"),
        ("publish_census", "administrator"),
        ("campaign_report", "staff"),
        ("financial_detail", "staff"),
        ("family_codes", "staff"),
    ],
)
def test_a_role_refusal_names_the_roles_that_hold_the_page(capability, level):
    """The refusal names the page's roles and links home, as a closed 403."""
    from parishkit.stewardship.accounts.policy import Capability
    from parishkit.stewardship.accounts.role_refusal import RoleDenied, role_denied

    error = role_denied(Capability(capability))
    assert isinstance(error, RoleDenied) and isinstance(error, PermissionError)
    assert str(error.refusal.message) == ROLE_TEXTS[level]
    assert error.refusal.link == "/admin/" and error.reason == "denied"


@pytest.mark.parametrize(
    "capability", ["ministry_report", "ministry_followup", "report_export"]
)
def test_a_leader_capability_keeps_the_generic_denial(capability):
    """A Ministry leader's refusals are about scope, so no role wording."""
    from parishkit.stewardship.accounts.policy import Capability
    from parishkit.stewardship.accounts.role_refusal import role_denied
    from parishkit.stewardship.web.refusals import AccessDenied

    error = role_denied(Capability(capability))
    assert type(error) is AccessDenied and getattr(error, "refusal", None) is None


def test_require_tells_an_ended_session_apart_from_a_role():
    """No current session gets the signed-out refusal (#2058); a role its own."""
    from uuid import uuid4

    from parishkit.stewardship.accounts.policy import Capability, Principal
    from parishkit.stewardship.accounts.role_refusal import RoleDenied, require
    from parishkit.stewardship.web.refusals import UserFacingSignedOut

    with pytest.raises(UserFacingSignedOut) as ended:
        require(None, Capability.SYSTEM_LOGS)
    assert ended.value.refusal.signed_out is True
    staff = Principal(uuid4(), frozenset({"staff"}))
    with pytest.raises(RoleDenied):
        require(staff, Capability.SYSTEM_LOGS)
    assert require(staff, Capability.CAMPAIGN_REPORT) is staff


@pytest.mark.parametrize("level", ["administrator", "staff"])
def test_a_role_level_refusal_names_that_level(level):
    """``role_denied_for`` serves pages gated by role, not one capability."""
    from parishkit.stewardship.accounts.role_refusal import RoleDenied, role_denied_for

    error = role_denied_for(level)
    assert type(error) is RoleDenied and str(error.refusal.message) == ROLE_TEXTS[level]
    assert error.refusal.sign_in is False
    with pytest.raises(ValueError):
        role_denied_for("ministry_leader")


def test_require_role_admits_the_level_and_those_above_it():
    """Administrators pass both levels; Staff only ``staff``; ``None`` is signed out."""
    from uuid import uuid4

    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.accounts.role_refusal import RoleDenied, require_role
    from parishkit.stewardship.web.refusals import UserFacingSignedOut

    admin = Principal(uuid4(), frozenset({"administrator"}))
    staff = Principal(uuid4(), frozenset({"staff"}))
    leader = Principal(uuid4(), frozenset({"ministry_leader"}))
    assert require_role(admin, "administrator") is admin
    assert require_role(admin, "staff") is admin
    assert require_role(staff, "staff") is staff
    with pytest.raises(RoleDenied):
        require_role(staff, "administrator")
    with pytest.raises(RoleDenied):
        require_role(leader, "staff")
    with pytest.raises(UserFacingSignedOut):
        require_role(None, "staff")


def test_only_the_generic_admin_sign_in_refusal_carries_the_google_account_guidance():
    """Every Admin sign-in refusal alike explains a missing Google Account (#1593).

    The guidance is the same for every reason, so it reveals no check; the
    temporary-unavailability page and Family refusals do not show it.
    """
    marker = b"data-google-account-help"
    assert marker in login_denial(admin=True, status=403, sign_in=True).content
    unavailable = login_denial(admin=True, status=503, kind="unavailable", sign_in=True)
    assert marker not in unavailable.content
    for kind in TEXTS:
        assert marker not in login_denial(status=403, kind=kind).content


def test_guidance_shows_for_sign_in_refusals_only():
    """A signed-in person's refusal gets no Google Account advice (#1593).

    A report view's ``denial()`` reaches a person who is already signed in,
    so the advice would be wrong; each sign-in refusal kind renders
    byte-identical content, so it still reveals no check.
    """
    from parishkit.stewardship.accounts.authentication import denial

    marker = b"data-google-account-help"
    try:
        raise PermissionError
    except PermissionError:
        report_refusal = denial()
        sign_in_refusals = [
            denial(sign_in=True),
            denial(status=403, retry=0, sign_in=True),
            login_denial(admin=True, status=403, sign_in=True),
        ]
    assert marker not in report_refusal.content
    assert marker not in login_denial(admin=True, status=403).content
    assert len({r.content for r in sign_in_refusals}) == 1
    assert marker in sign_in_refusals[0].content
