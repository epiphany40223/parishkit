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
    """Admin denials still use the original generic text and Admin retry route."""
    response = login_denial(admin=True, status=503)
    assert response.status_code == 503
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
