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


def test_only_the_generic_admin_sign_in_refusal_carries_the_google_account_guidance():
    """Every Admin sign-in refusal alike explains a missing Google Account (#1593).

    The guidance is the same for every reason, so it reveals no check; the
    temporary-unavailability page and Family refusals do not show it.
    """
    from parishkit.stewardship.accounts.google_account_help import HELP_URL

    marker = f'href="{HELP_URL}"'.encode()
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
