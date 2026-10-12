"""Signed-in sessions: every live Admin session, each but your own with Sign out (#953).

The page's only query parameter is its sort token; the template marks the
reader's own current session and offers it no action, and every other row's
Sign out is a row action (#879) that confirms in the shared dialog and posts
the session to the page's own address. The database side is in
``database/test_signed_in_sessions_postgresql.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.accounts import signed_in_views as views
from parishkit.stewardship.accounts.signed_in_sessions import SORTING

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
PAGE = "/admin/users/sessions/"
MINE = UUID(int=101)
OTHERS = UUID(int=102)


def _row(identifier, email, *, current=False, active=1, signed=2, expires=10):
    """One row as ``live_sessions`` shapes it; times are minutes or hours ago."""
    return {
        "id": identifier,
        "principal_id": UUID(int=identifier.int + 1000),
        "email": email,
        "authenticated_at": NOW - timedelta(hours=signed),
        "last_activity_at": NOW - timedelta(minutes=active),
        "expires_at": NOW + timedelta(hours=expires),
        "current": current,
    }


ROWS = [
    _row(MINE, "admin@example.org", current=True, active=0, signed=1, expires=11),
    _row(OTHERS, "Zed@example.org", active=30, signed=3, expires=9),
]


@pytest.fixture
def page(monkeypatch):
    """GET the page with admission and the database read stubbed."""
    monkeypatch.setattr(views, "runtime", lambda: None)
    monkeypatch.setattr(
        views,
        "_administrator",
        lambda request, service: SimpleNamespace(identity=UUID(int=1)),
    )
    monkeypatch.setattr(views, "database_now", lambda: NOW)
    monkeypatch.setattr(
        views, "live_sessions", lambda now, current: [dict(row) for row in ROWS]
    )

    def get(query=""):
        """The page's status and HTML for one query string."""
        request = RequestFactory().get(PAGE + query)
        request.portal_session = SimpleNamespace(pk=MINE)
        response = views.signed_in_sessions_view(request)
        return response.status_code, response.content.decode()

    return get


def test_the_parameters_are_a_sort_token_and_one_outcome():
    """Most recent activity first by default; only the page takes the outcome."""
    assert views._state(QueryDict("")) == ("-activity", False)
    for token in ("user", "-user", "signed_in", "-expires", "activity"):
        assert views._state(QueryDict(f"sort={token}")) == (token, False)
    page = QueryDict("sort=user&outcome=already_ended")
    assert views._state(page, outcome=True) == ("user", True)
    for query in (
        "sort=email",
        "sort=last_activity_at",
        "sort=user&sort=user",
        "page=2",
        "outcome=signed_out",
    ):
        with pytest.raises(ValueError):
            views._state(QueryDict(query), outcome=True)
    # A Sign out's own address carries the sort alone.
    with pytest.raises(ValueError):
        views._state(QueryDict("outcome=already_ended"))


def test_an_already_ended_sign_out_lands_on_a_page_that_says_so():
    """The redirect carries the outcome after the sort; the default stays bare."""
    assert views._page_url("-activity") == PAGE
    assert views._page_url("user", "#t", already_ended=True) == (
        PAGE + "?sort=user&outcome=already_ended#t"
    )


def test_times_sort_newest_first_on_their_first_click():
    """Times start descending; the address starts ascending, ignoring case."""
    first = {}
    for token, (column, _descending) in SORTING.tokens.items():
        first.setdefault(column, token)
    assert first == {
        "user": "user",
        "signed_in": "-signed_in",
        "activity": "-activity",
        "expires": "-expires",
    }
    rows = [dict(row) for row in ROWS]
    ordered = [row["email"] for row in SORTING.sort_rows(rows, "user")]
    assert ordered == ["admin@example.org", "Zed@example.org"]
    ordered = [row["id"] for row in SORTING.sort_rows(rows, "-activity")]
    assert ordered == [MINE, OTHERS]
    ordered = [row["id"] for row in SORTING.sort_rows(rows, "-signed_in")]
    assert ordered == [MINE, OTHERS]


def test_page_context_keeps_a_default_page_bare():
    """The default sort leaves the address bare; another one travels."""
    context = views.page_context([dict(row) for row in ROWS], "-activity")
    assert context["refresh_url"] == PAGE
    assert context["state_query"] == ""
    assert [row["label"] for row in context["table"].rows] == [
        "admin@example.org’s session",
        "Zed@example.org’s session",
    ]
    context = views.page_context([dict(row) for row in ROWS], "user")
    assert context["refresh_url"] == PAGE + "?sort=user"
    assert context["state_query"] == "sort=user"


def test_every_row_but_your_own_has_sign_out(page):
    """Your current session is marked and offered nothing; others confirm."""
    status, html = page()
    assert status == 200
    assert "admin@example.org (you, this browser)" in html
    assert f'name="session_id" value="{MINE}"' not in html
    assert f'name="session_id" value="{OTHERS}"' in html
    assert 'aria-label="Sign out Zed@example.org’s session"' in html
    assert 'data-confirm-open="session-sign-out"' in html
    assert '<dialog id="session-sign-out"' in html
    assert f'action="{PAGE}#sessions-table" id="signed-in-actions"' in html
    assert (
        f'<div id="sessions-table" data-table-region data-refresh-url="{PAGE}">' in html
    )
    # Times are browser-local instants, never a server-formatted zone.
    assert html.count("data-local-instant") == 6
    assert "UTC" not in html.split("<table", 1)[1]


def test_a_sorted_page_posts_and_refreshes_with_its_sort(page):
    """Sign out returns to the table as the reader sorted it."""
    status, html = page("?sort=user")
    assert status == 200
    assert f'action="{PAGE}?sort=user#sessions-table"' in html
    assert f'data-refresh-url="{PAGE}?sort=user"' in html
    assert 'aria-sort="ascending" data-sort-column="user"' in html
    assert page("?sort=bogus")[0] == 400


def test_sign_out_uses_revoke_icon_with_its_own_words():
    """The sign_out verb draws Revoke's icon under its own label."""
    html = render_to_string(
        "stewardship/components/table-actions.html",
        {
            "name": "x’s session",
            "delete": OTHERS,
            "confirm": "session-sign-out",
            "field": "session_id",
            "verb": "sign_out",
            "icon": "revoke",
            "confirm_name": "x’s session",
        },
    )
    revoke = render_to_string(
        "stewardship/components/action-icon.html", {"icon": "revoke"}
    )
    assert 'aria-label="Sign out x’s session"' in html
    assert revoke.strip() and revoke.strip() in html


def test_only_the_already_ended_page_carries_the_announcement(page):
    """The dialog's script reads the hidden line; an ordinary page has none."""
    _, plain = page()
    assert "data-confirm-announce" not in plain
    status, html = page("?outcome=already_ended")
    assert status == 200
    assert (
        "<p hidden data-confirm-announce>That session had already ended; "
        "the list is up to date.</p>" in html
    )
