"""Automation access, revocation and dashboard notices in the browser (ADM-11).

Automation access lists every live session first and, on request
(``ended=yes``), the Administrator's own ended ones, never offered Revoke;
both tables sort by allowlisted tokens only (#621). Any Administrator revokes
any live session from its row's Revoke, which confirms in the shared dialog
(#879) and posts the session to the collection; the post redirects back to
the page as the reader left it. The dashboard always renders the notices
region for an Administrator, so acknowledging the last notice still swaps in
place, and shows each notice in plain words with the label escaped.
"""

import pytest
from django.test import Client

from parishkit.stewardship.accounts.automation_models import (
    AutomationNotice,
    AutomationNoticeAcknowledgement,
)

from .auth_builders import signed_in, stale_sign_in
from .automation_builders import paired, post, two_admins  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

ACCESS = "/admin/users/automation/"
SESSIONS = ACCESS + "sessions/"


def revoke_button(row):
    """The markup of ``row``'s Revoke icon button (its value is the session)."""
    return f'name="session_id" value="{row.pk}"'


def test_access_lists_every_live_session_first(two_admins, google):  # noqa: F811
    """Every Administrator sees every live session, first, each with Revoke."""
    _, _, mine = paired(two_admins)
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    other, _ = signed_in(Client(enforce_csrf_checks=True))
    page = other.get(ACCESS)
    assert page.status_code == 200
    html = page.content.decode()
    # The first table on the page is the live one (#621).
    assert html.split("<table", 1)[1].startswith(
        '><caption id="automation-live-caption">'
    )
    assert "admin@example.org" in html
    assert "launch &lt;assistant&gt;" in html
    # Another Administrator revokes it from the live region: the row's
    # Revoke opens the dialog, which posts with the region's form (#879).
    assert f'action="{SESSIONS}#live-table" id="session-actions"' in html
    assert revoke_button(mine) in html
    assert 'aria-label="Revoke launch &lt;assistant&gt;"' in html
    assert 'data-confirm-open="session-revoke"' in html
    assert '<dialog id="session-revoke"' in html
    assert (
        f'<div id="live-table" data-table-region data-refresh-url="{ACCESS}">' in html
    )
    assert '<div id="ended-table" data-table-region></div>' in html
    assert 'href="/admin/users/automation/approval/"' in html


def test_revoking_returns_to_the_session_list(two_admins, google):  # noqa: F811
    """Own: revoked_by_owner; another Administrator's: revoked_by_administrator."""
    browser, _, mine = paired(two_admins)
    response = post(browser, SESSIONS, session_id=mine.pk)
    assert response.status_code == 302
    assert response["Location"] == ACCESS + "#live-table"
    mine.refresh_from_db()
    assert mine.end_reason == "revoked_by_owner"
    _, _, second = paired(two_admins)
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    other, _ = signed_in(Client(enforce_csrf_checks=True))
    assert post(other, SESSIONS, session_id=second.pk).status_code == 302
    second.refresh_from_db()
    assert second.end_reason == "revoked_by_administrator"


def test_ended_sessions_show_on_request_and_are_never_offered_revoke(
    two_admins,  # noqa: F811
    google,
):
    """ended=yes lists the own ended session, with its reason and no Revoke.

    Revoking keeps the page's filter and sorts in the address it returns to,
    validated as the page validates them.
    """
    browser, _, mine = paired(two_admins)
    _, _, kept = paired(two_admins)
    state = "?ended=yes&live_sort=label&ended_sort=-ended"
    response = post(browser, SESSIONS + state, session_id=mine.pk)
    assert response["Location"] == ACCESS + state + "#live-table"
    hidden = browser.get(ACCESS).content.decode()
    assert "Your ended sessions" not in hidden
    assert revoke_button(mine) not in hidden
    shown = browser.get(ACCESS + "?ended=yes").content.decode()
    live, ended = shown.split('<div id="ended-table" data-table-region>')
    assert f'action="{SESSIONS}?ended=yes#live-table"' in live
    assert f'data-refresh-url="{ACCESS}?ended=yes"' in live
    assert revoke_button(kept) in live
    assert "Your ended sessions" in ended and "Revoked by you" in ended
    assert "launch &lt;assistant&gt;" in ended
    assert 'aria-label="Revoke' not in ended and revoke_button(mine) not in shown
    # Revoking an ended session changes nothing.
    assert post(browser, SESSIONS, session_id=mine.pk).status_code == 302
    mine.refresh_from_db()
    assert mine.end_reason == "revoked_by_owner"


def test_tables_sort_by_their_allowlisted_tokens(two_admins, google):  # noqa: F811
    """Newest approval first by default; created reverses it; others are refused."""
    browser, _, older = paired(two_admins)
    _, _, newer = paired(two_admins)
    default = browser.get(ACCESS).content.decode()
    assert default.index(revoke_button(newer)) < default.index(revoke_button(older))
    oldest = browser.get(ACCESS + "?live_sort=created").content.decode()
    assert oldest.index(revoke_button(older)) < oldest.index(revoke_button(newer))
    assert 'aria-sort="ascending" data-sort-column="created"' in oldest
    for query in (
        "?live_sort=created_at",
        "?live_sort=ended",
        "?ended_sort=administrator",
        "?ended=true",
        "?sort=-created",
        "?dir=desc",
    ):
        assert browser.get(ACCESS + query).status_code == 400, query
    # A revoke with an unknown sort is refused before anything changes.
    response = post(browser, SESSIONS + "?live_sort=bogus", session_id=older.pk)
    assert response.status_code == 400
    older.refresh_from_db()
    assert older.revoked_at is None


def test_a_revoke_names_exactly_one_session(two_admins, google):  # noqa: F811
    """No session, two, a malformed one or an extra field: refused, unchanged.

    The old per-session address is gone (no legacy URLs, #864).
    """
    browser, _, mine = paired(two_admins)
    _, _, other = paired(two_admins)
    for fields in (
        {},
        {"session_id": [mine.pk, other.pk]},
        {"session_id": "not-a-session"},
        {"session_id": mine.pk, "extra": "x"},
    ):
        assert post(browser, SESSIONS, **fields).status_code == 400, fields
    assert post(browser, f"{SESSIONS}{mine.pk}/").status_code == 404
    mine.refresh_from_db()
    other.refresh_from_db()
    assert mine.revoked_at is None and other.revoked_at is None


def test_approving_from_access_first_asks_for_a_fresh_sign_in(auth_service, google):
    """A stale sign-in sees Confirm with Google and an unavailable button."""
    browser, _ = signed_in()
    stale_sign_in()
    page = browser.get(ACCESS)
    assert b"Confirm with Google" in page.content
    assert b'<button type="button" disabled>Approve a session</button>' in (
        page.content
    )
    assert b'href="/admin/users/automation/approval/"' not in page.content


def test_the_notices_region_is_always_there_and_acknowledges_in_place(
    two_admins,  # noqa: F811
    google,
):
    """Empty or not, the region renders; notices read plainly; ack is per person."""
    browser, _ = signed_in()
    home = browser.get("/admin/")
    assert b'id="automation-notices" data-in-place-region' in home.content
    assert b"Automation notices" not in home.content
    paired(two_admins)
    home = browser.get("/admin/")
    assert b"An automation session was approved" in home.content
    assert b"launch &lt;assistant&gt;" in home.content
    notice = AutomationNotice.objects.get()
    response = post(browser, "/admin/users/automation/notices/", notice=notice.pk)
    assert response.status_code == 302
    assert response["Location"] == "/admin/#automation-notices"
    home = browser.get("/admin/")
    assert b'id="automation-notices" data-in-place-region' in home.content
    assert b"An automation session was approved" not in home.content
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    other, _ = signed_in(Client(enforce_csrf_checks=True))
    assert b"An automation session was approved" in other.get("/admin/").content
    post(other, "/admin/users/automation/notices/", notice="all")
    assert AutomationNoticeAcknowledgement.objects.count() == 2


def test_staff_see_neither_page_nor_notices(two_admins, google):  # noqa: F811
    """Automation is for Administrators only."""
    paired(two_admins)
    claims, _ = google
    claims.update(sub="staff-subject", email="staff@example.org")
    staff, _ = signed_in(Client(enforce_csrf_checks=True))
    assert staff.get(ACCESS).status_code == 403
    assert b"automation-notices" not in staff.get("/admin/").content
