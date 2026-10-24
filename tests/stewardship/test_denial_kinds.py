"""Sign-in denials name a fixed category, never the reason within it."""

import pytest

from parishkit.stewardship.web.security import login_denial

TEXTS = {
    "": b"Sign-in is unavailable. Please try again.",
    "code": b"This Family code cannot be found or used.",
    "link": b"This secure Family link cannot be found or used.",
    "unavailable": b"Sign-in is temporarily unavailable. Please try again later.",
    "unfinished": b"Signing in with Google didn't complete.",
    "rate_limited": b"Too many sign-in attempts. Try again later.",
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
        (429, b"Too many sign-in attempts. Try again later."),
        (503, b"Sign-in is temporarily unavailable. Please try again later."),
        (403, b"Sign-in is unavailable. Please try again."),
    ],
)
def test_admin_rate_limits_and_outages_read_as_temporary(status, text):
    """An Admin rate limit says so (#2116); an outage reads as temporary."""
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


def test_no_denial_page_carries_the_google_account_guidance():
    """The guidance stays on the Users About panel only (#2116).

    The no-access page has its own wording with the verified address.
    """
    marker = b"data-google-account-help"
    for kind in set(TEXTS) - {"rate_limited", "unfinished"}:
        assert marker not in login_denial(status=403, kind=kind).content
    for kind in ("unfinished", "rate_limited", "unavailable", ""):
        assert marker not in login_denial(admin=True, kind=kind).content


def test_each_sign_in_refusal_says_which_kind_it_is():
    """Sign-in refusals say why; a signed-in refusal keeps the generic text.

    A report view's ``denial()`` reaches a person who is already signed in;
    every unfinished sign-in renders byte-identical content (#2116).
    """
    from parishkit.stewardship.accounts.authentication import denial

    try:
        raise PermissionError
    except PermissionError:
        report_refusal = denial()
        unfinished = [
            denial(sign_in=True),
            denial(status=403, retry=0, sign_in=True),
            login_denial(admin=True, status=403, kind="unfinished"),
        ]
    assert TEXTS[""] in report_refusal.content
    assert len({r.content for r in unfinished}) == 1
    assert b"<h1>Sign-in didn't finish</h1>" in unfinished[0].content
    assert b'<a href="/admin/login">Try signing in again</a>' in unfinished[0].content
    limited = denial(status=429, retry=5, sign_in=True)
    assert limited.status_code == 429 and limited["Retry-After"] == "5"
    assert TEXTS["rate_limited"] in limited.content
    # The minute count is never shown.
    assert b"minute" not in limited.content


def test_no_access_names_only_the_verified_address(rf):
    """The address is escaped into the body; the form carries a CSRF token."""
    from parishkit.stewardship.accounts.authentication import denial

    request = rf.post("/admin/oauth/callback")
    response = denial(sign_in=True, account=(request, "a<b>@example.org"))
    body = response.content.decode()
    assert response.status_code == 403
    assert "<h1>No access with this account</h1>" in body
    assert "<strong>a&lt;b&gt;@example.org</strong>" in body
    assert 'name="csrfmiddlewaretoken"' in body
    assert '<form method="post" action="/admin/login">' in body
    assert "Sign in with a different account" in body
    assert '<a href="/">Family campaign portal</a>' in body
    assert "Try signing in again" not in body
    assert "a<b>" not in str(dict(response.items()))
    # A rate limit wins over the address: the 429 page names no one.
    limited = denial(
        status=429, retry=5, sign_in=True, account=(request, "a@example.org")
    )
    assert b"a@example.org" not in limited.content


def test_only_a_sign_in_refusal_may_name_an_address(rf):
    """A signed-in refusal passing ``account`` is refused, not rendered."""
    from parishkit.stewardship.accounts.authentication import denial

    request = rf.post("/admin/reports/weekly")
    for status in (403, 429, 503):
        with pytest.raises(ValueError):
            denial(status=status, account=(request, "a@example.org"))


def test_no_access_without_its_account_fails_closed():
    """No caller can render the no-access page without a verified address."""
    with pytest.raises(ValueError):
        login_denial(admin=True, kind="no_access")
    with pytest.raises(ValueError):
        login_denial(kind="no_access", account="a@example.org", csrf_token="x")
