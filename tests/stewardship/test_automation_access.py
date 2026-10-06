"""Automation access: live sessions first, ended ones on request, sortable (#621).

The view's page state (the "Include ended sessions" box and each table's
sort) is a closed allowlist; the template shows the live table first, the
ended table only on request and never with Revoke, and every heading,
Revoke form and the box's form keep the rest of the state. The database
side is in ``database/test_automation_access_postgresql.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from django.http import QueryDict
from django.test import RequestFactory

from parishkit.stewardship.accounts import automation_sessions as sessions
from parishkit.stewardship.accounts import automation_views as views

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
ME = UUID(int=1)
OTHER = UUID(int=2)


def _row(number, label, *, principal=ME, live=True, created=1, **extra):
    """One session row as ``session_row`` shapes it."""
    return {
        "id": UUID(int=100 + number),
        "label": label,
        "scope": "full",
        "created_at": NOW - timedelta(days=created),
        "expires_at": NOW + timedelta(days=10),
        "last_used_at": None,
        "revoked_at": None,
        "end_reason": None,
        "host": "0123456789ab",
        "live": live,
        "principal_id": principal,
        "principal_email": "admin@example.org" if principal == ME else "b@example.org",
        "version": 1,
    } | extra


LIVE_ROWS = [
    _row(1, "alpha", created=3),
    _row(2, "Beta", principal=OTHER, created=1),
]
OWN_ROWS = [
    LIVE_ROWS[0],
    _row(
        3,
        "gamma",
        live=False,
        created=5,
        revoked_at=NOW - timedelta(days=1),
        end_reason="logout",
    ),
    _row(4, "delta", live=False, created=20, expires_at=NOW - timedelta(days=2)),
]


@pytest.fixture
def page(monkeypatch):
    """GET Automation access with the database reads stubbed; returns the HTML."""
    monkeypatch.setattr(views, "runtime", lambda: None)
    monkeypatch.setattr(
        views,
        "_administrator",
        lambda request, service: SimpleNamespace(identity=ME, roles={"administrator"}),
    )
    monkeypatch.setattr(views, "_fresh", lambda request: True)
    monkeypatch.setattr(views, "database_now", lambda: NOW)
    monkeypatch.setattr(views, "live_sessions", lambda: [dict(r) for r in LIVE_ROWS])
    monkeypatch.setattr(
        sessions, "sessions_of", lambda principal, now: [dict(r) for r in OWN_ROWS]
    )

    def get(query="", rows=None):
        """The page's response for one query string."""
        if rows is not None:
            monkeypatch.setattr(views, "live_sessions", lambda: list(rows))
        request = RequestFactory().get("/admin/users/automation/" + query)
        request.portal_session = None
        response = views.access_view(request)
        return response.status_code, response.content.decode()

    return get


def test_the_page_state_is_a_closed_allowlist():
    """Only ended=yes and each table's own sort tokens are accepted."""
    state = views._access_state(QueryDict(""))
    assert state == (False, "-created", "-created")
    state = views._access_state(
        QueryDict("ended=yes&live_sort=administrator&ended_sort=-ended")
    )
    assert state == (True, "administrator", "-ended")
    for query in (
        "ended=no",
        "ended=yes&ended=yes",
        "live_sort=ended",
        "ended_sort=administrator",
        "live_sort=created_at",
        "sort=label",
        "dir=asc",
        "live_size=all",
    ):
        with pytest.raises(ValueError):
            views._access_state(QueryDict(query))


def test_state_fields_leave_defaults_out():
    """A default page keeps a bare address; choices travel by name."""
    assert views._state_fields(False, "-created", "-created") == []
    assert views._state_fields(True, "label", "-created") == [
        ("ended", "yes"),
        ("live_sort", "label"),
    ]


def test_ended_at_is_the_revocation_else_a_passed_deadline():
    """Expired sessions ended at their deadline; a lapse not yet recorded has none."""
    live = _row(1, "a")
    assert sessions.ended_at(live, NOW) is None
    revoked = _row(2, "b", live=False, revoked_at=NOW - timedelta(hours=1))
    assert sessions.ended_at(revoked, NOW) == NOW - timedelta(hours=1)
    expired = _row(3, "c", live=False, expires_at=NOW - timedelta(days=1))
    assert sessions.ended_at(expired, NOW) == NOW - timedelta(days=1)
    lapsed = _row(4, "d", live=False)
    assert sessions.ended_at(lapsed, NOW) is None


def test_own_sessions_are_live_only_unless_ended_are_included(monkeypatch):
    """The page's box and ``pk-admin sessions --include-ended`` share this."""
    monkeypatch.setattr(sessions, "sessions_of", lambda principal, now: OWN_ROWS)
    live = sessions.own_sessions(ME, NOW, include_ended=False)
    assert [row["label"] for row in live] == ["alpha"]
    every = sessions.own_sessions(ME, NOW, include_ended=True)
    assert [row["label"] for row in every] == ["alpha", "gamma", "delta"]
    assert every[2]["ended_at"] == NOW - timedelta(days=2)


def test_session_sorts_order_text_without_case_and_times_newest_first():
    """Label ignores case; times sort newest first on the first choice."""
    rows = [_row(1, "beta", created=1), _row(2, "Alpha", created=2)]
    by_label = sessions.LIVE_SORTING.sort_rows(rows, "label")
    assert [row["label"] for row in by_label] == ["Alpha", "beta"]
    assert sessions.LIVE_SORTING.tokens["-created"] == ("created", True)
    assert next(iter(sessions.OWN_SORTING.tokens)) == "label"
    first = [
        token
        for token, (column, _) in sessions.OWN_SORTING.tokens.items()
        if column == "ended"
    ][0]
    assert first == "-ended"
    assert "administrator" not in sessions.OWN_SORTING.columns()
    assert "ended" not in sessions.LIVE_SORTING.columns()


def test_by_default_live_sessions_come_first_and_ended_ones_are_hidden(page):
    """The live table is the first table; the box is unticked; no ended table."""
    status, html = page()
    assert status == 200
    first = html.split("<table", 1)[1]
    assert first.startswith('><caption id="automation-live-caption">')
    assert html.index("Live sessions") < html.index("Approve a session")
    box = html.split('id="include-ended"')[1].split(">")[0]
    assert 'name="ended" value="yes"' in html and "checked" not in box
    assert '<div id="ended-table" data-table-region></div>' in html
    assert "gamma" not in html and "Your ended sessions" not in html
    # Every live session is offered Revoke, in place, with a bare address.
    assert html.count('data-in-place-message="Session revoked."') == 2
    assert (
        f'action="/admin/users/automation/sessions/{LIVE_ROWS[0]["id"]}/#live-table"'
        in (html)
    )


def test_the_box_shows_ended_sessions_without_revoke(page):
    """ended=yes adds the ended table; its rows never offer Revoke."""
    status, html = page("?ended=yes")
    assert status == 200
    box = html.split('id="include-ended"')[1].split(">")[0]
    assert 'aria-controls="ended-table"' in box and 'data-applied="true"' in box
    assert box.endswith(" checked")
    ended = html.split('<div id="ended-table" data-table-region>')[1]
    assert "Your ended sessions" in ended
    assert "gamma" in ended and "delta" in ended and "alpha" not in ended
    assert ">Revoke<" not in ended
    assert "Ended from the command line" in ended
    # Revoke forms in the live table keep the box's choice.
    live = html.split('<div id="live-table" data-table-region>')[1].split(
        '<div id="ended-table"'
    )[0]
    assert "?ended=yes#live-table" in live


def test_both_tables_carry_sortable_headings_with_the_rest_of_the_state(page):
    """Shared sort headings on each table's columns; status and actions never sort."""
    _, html = page("?ended=yes&live_sort=label")
    live = html.split('<div id="live-table" data-table-region>')[1].split(
        '<div id="ended-table"'
    )[0]
    ended = html.split('<div id="ended-table" data-table-region>')[1]
    columns = ("administrator", "label", "scope", "created", "expires", "used")
    for column in columns:
        assert f'data-sort-column="{column}"' in live
    assert 'aria-sort="ascending" data-sort-column="label"' in live
    for column in (*columns[1:], "ended"):
        assert f'data-sort-column="{column}"' in ended
    assert 'data-sort-column="administrator"' not in ended
    assert ended.count("data-sort-column") == 6 and live.count("data-sort-column") == 6
    assert '<th scope="col">Revoke</th>' in live
    assert '<th scope="col">How it ended</th>' in ended
    # An ended heading keeps the box and the live table's sort, and no size.
    link = ended.split('data-sort-column="ended"')[1].split('href="')[1].split('"')[0]
    query = parse_qs(urlsplit(link.replace("&amp;", "&")).query)
    assert query == {"ended": ["yes"], "live_sort": ["label"], "ended_sort": ["-ended"]}
    assert link.endswith("#ended-table")
    # The box's form keeps the sorts as hidden fields, never the box itself.
    form = html.split('id="automation-filter"')[1].split("</form>")[0]
    assert '<input type="hidden" name="live_sort" value="label">' in form
    assert 'type="hidden" name="ended"' not in form


def test_sorting_reorders_the_live_table(page):
    """Administrator ascending puts admin@ before b@; the default is newest first."""
    _, html = page()
    assert html.index("Beta") < html.index("alpha")
    _, html = page("?live_sort=administrator")
    assert html.index("alpha") < html.index("Beta")


def test_no_live_sessions_says_how_to_see_past_ones(page):
    """An empty live table is a sentence, not an empty table."""
    _, html = page(rows=[])
    assert (
        "No live sessions. Tick “Include ended sessions” to see your past ones." in html
    )
    assert "automation-live-caption" not in html
    _, html = page("?ended=yes", rows=[])
    assert "No live sessions.</p>" in html


@pytest.mark.parametrize(
    "query", ["?ended=1", "?live_sort=bogus", "?ended_sort=-administrator", "?x=1"]
)
def test_unknown_state_is_refused(page, query):
    """Anything outside the allowlist is a client error, like the logs page."""
    status, _ = page(query)
    assert status == 400
