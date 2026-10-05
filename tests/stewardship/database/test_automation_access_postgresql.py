"""Automation access, revocation and dashboard notices in the browser (ADM-11).

Automation access lists the Administrator's own sessions and every live
session; any Administrator revokes any of them with an in-place form whose
post redirects back to the page. The dashboard always renders the notices
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


def test_access_lists_own_and_every_live_session(two_admins, google):  # noqa: F811
    """Each Administrator sees their own sessions and everyone's live ones."""
    _, _, mine = paired(two_admins)
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    other, _ = signed_in(Client(enforce_csrf_checks=True))
    page = other.get(ACCESS)
    assert page.status_code == 200
    assert b"You have no automation sessions." in page.content
    assert b"admin@example.org" in page.content
    assert b"launch &lt;assistant&gt;" in page.content
    # Another Administrator sees it in the every-live-session region.
    action = f'action="{ACCESS}sessions/{mine.pk}/#automation-live"'
    assert action.encode() in page.content
    assert b"data-in-place-region" in page.content
    assert b'data-in-place-message="Session revoked."' in page.content
    assert b'href="/admin/users/automation/approval/"' in page.content


def test_revoking_returns_to_the_session_list(two_admins, google):  # noqa: F811
    """Own: revoked_by_owner; another Administrator's: revoked_by_administrator."""
    browser, _, mine = paired(two_admins)
    response = post(browser, f"{ACCESS}sessions/{mine.pk}/")
    assert response.status_code == 302
    assert response["Location"] == ACCESS + "#automation-sessions"
    mine.refresh_from_db()
    assert mine.end_reason == "revoked_by_owner"
    _, _, second = paired(two_admins)
    claims, _ = google
    claims.update(sub="other-subject", email="other@example.org")
    other, _ = signed_in(Client(enforce_csrf_checks=True))
    assert post(other, f"{ACCESS}sessions/{second.pk}/").status_code == 302
    second.refresh_from_db()
    assert second.end_reason == "revoked_by_administrator"


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
