"""Parts an audited page could not read come in by themselves (#1863).

Pure tests of ``web.part_status`` (the status query, readiness and the
region it draws), the two parts' notices and status views' admission and
query handling, and the registries. The real reads, audit and sign-in
checks are in database/test_part_status_postgresql.py; the in-place re-read
is in browser/test_part_status.py.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.accounts import admin_navigation, leader_import_views
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.reports import information_views
from parishkit.stewardship.web import part_status
from parishkit.stewardship.web.part_status import (
    is_ready,
    parse_status,
)

LEADERS = leader_import_views.LEADERS
CONTACTS = information_views.CONTACTS
COMPONENT = "stewardship/components/part-status.html"


def query(text):
    """A request's query string as Django parses it."""
    return QueryDict(text)


def test_the_status_query_names_one_generation_and_maybe_a_retry():
    """``shown`` is required and a plain generation; ``retry`` is only 1, and
    only where the page sends it; nothing else is accepted."""
    assert parse_status(query("shown=0")) == (0, False)
    assert parse_status(query("shown=42"), retry=True) == (42, False)
    assert parse_status(query("shown=7&retry=1"), retry=True) == (7, True)
    for text in (
        "",
        "shown=",
        "shown=-1",
        "shown=1.5",
        "shown=١",
        "shown=" + "9" * 20,
        "shown=1&shown=2",
        "shown=1&other=1",
    ):
        with pytest.raises(ValueError):
            parse_status(query(text), retry=True)
    with pytest.raises(ValueError):
        parse_status(query("shown=1&retry=1"))
    for text in ("shown=1&retry=0", "shown=1&retry=", "shown=1&retry=1&retry=1"):
        with pytest.raises(ValueError):
            parse_status(query(text), retry=True)


@pytest.mark.parametrize(
    ("current", "shown", "retry", "ready"),
    [
        (3, 3, False, False),
        (4, 3, False, True),
        # No ParishSoft data in use at all: nothing newer to read.
        (0, 3, False, False),
        (0, 0, False, False),
        # A retry is ready at the first check that works.
        (3, 3, True, True),
        (0, 0, True, True),
    ],
)
def test_ready_once_other_data_is_in_use_or_a_retry_is_due(
    monkeypatch, current, shown, retry, ready
):
    """Readiness compares the generation in use with the page's own."""
    monkeypatch.setattr(part_status, "current_generation", lambda: current)
    assert is_ready(shown, retry) is ready


def test_a_retry_still_reads_the_database(monkeypatch):
    """A retry is ready only once a check works: the generation read runs,
    and its failure is the status's failure."""

    def down():
        """The database is unavailable."""
        raise RuntimeError("down")

    monkeypatch.setattr(part_status, "current_generation", down)
    with pytest.raises(RuntimeError):
        is_ready(0, True)


def render(part, **options):
    """The region ``part`` draws, as the page or its status does."""
    return render_to_string(
        COMPONENT, {"part_status": part_status.part_status(part, **options)}
    )


def test_the_page_draws_a_pending_region_with_its_status_address():
    """A pending region polls its status; Users at a fixed 60 s, the
    Contact details at the usual backoff. Neither says to reload."""
    html = render(LEADERS, shown=5, retry=True)
    assert html.startswith(
        '<div data-live-status="leaders" '
        'data-live-url="/admin/users/leaders/status/?shown=5&amp;retry=1" '
        'data-live-pending data-live-interval="60000" data-live-retry>'
    )
    assert '<p class="notice notice-error" id="leaders-unavailable">' in html
    assert "data-reread" not in html
    html = render(CONTACTS, shown=3)
    assert html.startswith(
        '<div data-live-status="information-contacts" '
        'data-live-url="/admin/reports/information/contacts/status/?shown=3" '
        "data-live-pending>"
    )
    assert "data-live-retry" not in html
    assert 'id="information-contacts-unavailable"' in html
    for part in (LEADERS, CONTACTS):
        assert "reload" not in str(part.notice).lower()
        assert "appear here by themselves" in str(part.notice)


def test_a_ready_status_settles_with_the_follow_up_link():
    """Ready: no longer pending, and the hidden re-read link names the
    region the page redraws."""
    html = render(LEADERS, shown=5, ready=True)
    assert "data-live-pending" not in html
    assert '<a hidden data-live-follow data-reread href="#leaders-table">' in html
    html = render(CONTACTS, shown=3, ready=True)
    assert '<a hidden data-live-follow data-reread href="#information-item">' in html


def test_the_statuses_are_non_pages_covered_by_their_pages_commands():
    """No menu entry or page of their own; parity is the page's command."""
    for name, command in (
        ("information_contacts_status", "followup information-show"),
        ("user_leaders_status", "users import-leaders"),
    ):
        assert name in admin_navigation.NON_PAGES
        assert LEDGER[name].detail == (command,)


ADMIN = Principal(uuid4(), frozenset({"administrator", "staff", "ministry_leader"}))
STAFF = Principal(uuid4(), frozenset({"staff"}))


def _leaders_status(monkeypatch, actor, text="shown=1&retry=1"):
    """Call the leaders status as ``actor``, recording how it admitted them."""
    seen = []

    def admit(*args, **kwargs):
        """Record the admission's options; admit ``actor``."""
        seen.append(kwargs)
        return actor

    monkeypatch.setattr(
        leader_import_views, "runtime", lambda: SimpleNamespace(store=None)
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.admin_editing.authenticated_admin", admit
    )
    monkeypatch.setattr(part_status, "current_generation", lambda: 1)
    monkeypatch.setattr(
        leader_import_views, "editable_configuration", lambda service: None
    )
    request = RequestFactory().get("/admin/users/leaders/status/?" + text)
    return leader_import_views.user_leaders_status(request), seen


def test_the_leaders_status_is_read_only_and_for_administrators(monkeypatch):
    """Admitted read-only (no activity), Administrators only, and a bad query
    is the typed 400."""
    response, seen = _leaders_status(monkeypatch, ADMIN)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert "data-reread" in response.content.decode()
    assert seen == [{"store": None, "activity": False, "read_only": True}]
    response, _seen = _leaders_status(monkeypatch, STAFF)
    assert response.status_code == 403
    response, _seen = _leaders_status(monkeypatch, ADMIN, "shown=1&other=1")
    assert response.status_code == 400


def test_the_leaders_status_waits_for_newer_data_without_a_retry(monkeypatch):
    """After the page's automatic re-read (no retry) the same generation is
    still pending."""
    response, _seen = _leaders_status(monkeypatch, ADMIN, "shown=1")
    text = response.content.decode()
    assert response.status_code == 200
    assert "data-live-pending" in text and "data-reread" not in text


def _contacts_status(
    monkeypatch, actor, text="shown=3", current=4, campaign_admit=None
):
    """Call the Contact details' status as ``actor``; ``campaign_admit`` stands in
    for the campaign admission (admitted by default)."""
    seen = []

    def admit(*args, **kwargs):
        """Record the admission's options; admit ``actor``."""
        seen.append(kwargs)
        return actor

    monkeypatch.setattr(
        information_views, "runtime", lambda: SimpleNamespace(store=None)
    )
    monkeypatch.setattr(information_views, "authenticated_admin", admit)
    monkeypatch.setattr(
        information_views,
        "admit_report_read",
        campaign_admit or (lambda campaign: None),
    )
    monkeypatch.setattr(part_status, "current_generation", lambda: current)
    request = RequestFactory().get(
        "/admin/reports/information/contacts/status/?" + text
    )
    return information_views.contacts_status(request, uuid4()), seen


def test_the_contacts_status_is_read_only_and_for_staff(monkeypatch):
    """Admitted read-only; ready once other data is in use; a Ministry leader
    is refused and a bad query is a 400."""
    response, seen = _contacts_status(monkeypatch, STAFF)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert "data-reread" in response.content.decode()
    assert seen == [{"store": None, "activity": False, "read_only": True}]
    response, _seen = _contacts_status(monkeypatch, STAFF, current=3)
    assert "data-live-pending" in response.content.decode()
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4}))
    response, _seen = _contacts_status(monkeypatch, leader)
    assert response.status_code == 403
    for text in ("shown=3&retry=1", "shown=x", ""):
        response, _seen = _contacts_status(monkeypatch, STAFF, text)
        assert response.status_code == 400
    # No current campaign (or a restore review): the report pages' refusal.

    def refuse(campaign):
        """No current campaign to read."""
        raise PermissionError("no campaign")

    response, _seen = _contacts_status(monkeypatch, STAFF, campaign_admit=refuse)
    assert response.status_code == 403


def test_the_leaders_status_refuses_while_users_cannot_be_edited(monkeypatch):
    """In a restore review, or before setup, the Users page refuses; its
    status does too, rather than sending the page to that refusal."""
    from parishkit.config import ConfigError

    def refuse(service):
        """No ordinary edit is possible now."""
        raise ConfigError("Configuration is unavailable.")

    response, _seen = _leaders_status(monkeypatch, ADMIN)
    assert response.status_code == 200
    monkeypatch.setattr(leader_import_views, "editable_configuration", refuse)
    request = RequestFactory().get("/admin/users/leaders/status/?shown=1")
    assert leader_import_views.user_leaders_status(request).status_code == 503


def test_the_pages_no_longer_ask_for_a_reload():
    """Neither template keeps its old reload sentence."""
    from pathlib import Path

    templates = Path(information_views.__file__).parents[1] / "accounts/templates"
    for name in ("information.html", "users.html"):
        text = (templates / "stewardship" / name).read_text()
        assert "Reload the page to see them" not in text
        assert "Reload the page to try again" not in text
