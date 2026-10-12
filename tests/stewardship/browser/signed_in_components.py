"""Signed-in sessions for the browser tests, from the real template (#953).

The page is served at its real address, rendered through the view's own
``page_context``, for its default order and every order its headings lead
to. Sign out is a row action (#879): its dialog posts to the page's own
address, which the fixture server answers with a redirect to the signed-out
state; the dialog then redraws the table from ``data-refresh-url``, which on
the start page is that state's address.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import signed_in_views as views
from parishkit.stewardship.accounts.signed_in_sessions import SORTING

NOW = datetime(2026, 10, 11, 15, tzinfo=UTC)
PAGE = reverse("admin:signed_in_sessions")
SIGNED_OUT = PAGE + "?signed_out=1"
OWN = UUID(int=901)
OTHER = UUID(int=902)
STAFF = UUID(int=903)
# The page sorted by User answers its Sign out as a session that had already
# ended: the redirect's page carries the hidden announcement (#953).
SORTED = PAGE + "?sort=user"
ALREADY_ENDED = SORTED + "&outcome=already_ended"
POSTS = {PAGE: (303, SIGNED_OUT, ""), SORTED: (303, ALREADY_ENDED, "")}


def _session(identifier, email, *, current=False, active, signed):
    """One live session as ``live_sessions`` reads it (minutes and hours ago)."""
    return {
        "id": identifier,
        "principal_id": UUID(int=identifier.int + 100),
        "email": email,
        "authenticated_at": NOW - timedelta(hours=signed),
        "last_activity_at": NOW - timedelta(minutes=active),
        "expires_at": NOW + timedelta(hours=12 - signed),
        "current": current,
    }


def _rows(*, signed_out=False):
    """The reader's own session, plus the others unless one was signed out."""
    rows = [_session(OWN, "admin@example.org", current=True, active=0, signed=1)]
    if not signed_out:
        rows.append(_session(OTHER, "zed@example.org", active=20, signed=4))
    rows.append(_session(STAFF, "staff@example.org", active=45, signed=2))
    return rows


def _page(context, admin, sort, *, signed_out=False, refresh=None, ended=False):
    """The page for one sort, built by the view's own context."""
    page = views.page_context(_rows(signed_out=signed_out), sort)
    page["already_ended"] = ended
    if refresh:
        page["refresh_url"] = refresh
    return render_to_string(
        "stewardship/signed-in-sessions.html",
        context | {"admin_chrome": admin} | page,
    )


def components(context, admin):
    """The pages a browser test opens, keyed by exact path."""
    responses = {
        PAGE: ("text/html", _page(context, admin, SORTING.default, refresh=SIGNED_OUT)),
        SIGNED_OUT: (
            "text/html",
            _page(context, admin, SORTING.default, signed_out=True),
        ),
    }
    for token in SORTING.tokens:
        if token != SORTING.default:
            responses[PAGE + f"?sort={token}"] = (
                "text/html",
                _page(context, admin, token),
            )
    # The already-ended answer: the session is gone from the list, and the
    # page says it had already ended.
    responses[ALREADY_ENDED] = (
        "text/html",
        _page(context, admin, "user", signed_out=True, ended=True),
    )
    return responses
