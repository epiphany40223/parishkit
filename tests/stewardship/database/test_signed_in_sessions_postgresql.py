"""Signed-in sessions against PostgreSQL: list, Sign out and its records (#953).

The page lists every live interactive Admin session of any portal role, and
never a command session of the automation command line. An Administrator
signs out any session but their own current one: it ends at once (the
person's next request is signed out), the ending is the ordinary
``admin_revoked`` with that person as actor, and ``admin_session_signed_out``
records the Administrator. Both are in System logs' Sign-in activity.
"""

from datetime import timedelta

import pytest
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.automation_models import AutomationLogin
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import AdminRevocation, PortalUser
from parishkit.stewardship.accounts.runtime_models import ConfigurationActivation
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.web.namespaces import ADMIN_COOKIES

from ..policy_factory import address
from .auth_builders import auth_runtime, signed_in, unguarded
from .automation_builders import (  # noqa: F401
    browser_session,
    command,
    paired,
    post,
    two_admins,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/users/sessions/"


def sign_in_as(google, email, subject):
    """A new browser signed in with ``email``'s Google account."""
    claims, _ = google
    claims.update(sub=subject, email=email)
    browser, _ = signed_in(Client(enforce_csrf_checks=True))
    return browser


def assert_already_ended(browser, row):
    """Posting ``row`` changes nothing and lands on the "already ended" page."""
    response = post(browser, PAGE, session_id=row.pk)
    assert response.status_code == 302
    assert response["Location"] == PAGE + "?outcome=already_ended#sessions-table"
    landed = browser.get(response["Location"].split("#")[0]).content.decode()
    assert "That session had already ended; the list is up to date." in landed
    assert "data-confirm-announce" in landed


def sign_out_button(row):
    """The markup of ``row``'s Sign out icon button (its value is the session)."""
    return f'name="session_id" value="{row.pk}"'


def test_the_page_lists_every_live_session_with_sign_out(two_admins, google):  # noqa: F811
    """Every portal role's session is listed; your own current one has no action."""
    admin = sign_in_as(google, "admin@example.org", "admin-subject")
    sign_in_as(google, "staff@example.org", "staff-subject")
    mine = browser_session("admin@example.org")
    staff = browser_session("staff@example.org")
    page = admin.get(PAGE)
    assert page.status_code == 200
    assert page["Cache-Control"] == "no-store"
    html = page.content.decode()
    assert "admin@example.org (you, this browser)" in html
    assert "staff@example.org" in html
    assert sign_out_button(staff) in html
    assert sign_out_button(mine) not in html
    assert 'aria-label="Sign out staff@example.org’s session"' in html
    assert f'action="{PAGE}#sessions-table" id="signed-in-actions"' in html
    # The menu links the page (Users and access).
    assert f'href="{PAGE}"' in html
    # No session key ever reaches the page: neither a listed row's Django
    # session key nor the reader's own cookie.
    for row in (mine, staff):
        assert row.session_id not in html
    assert admin.cookies[ADMIN_COOKIES[0]].value not in html


def test_signing_out_ends_the_session_at_once_and_records_both(two_admins, google):  # noqa: F811
    """The session is revoked, its person is signed out, and both are audited."""
    admin = sign_in_as(google, "admin@example.org", "admin-subject")
    other = sign_in_as(google, "other@example.org", "other-subject")
    assert other.get(PAGE).status_code == 200
    target = browser_session("other@example.org")
    response = post(admin, PAGE + "?sort=user", session_id=target.pk)
    assert response.status_code == 302
    assert response["Location"] == PAGE + "?sort=user#sessions-table"
    target.refresh_from_db()
    assert target.revoked_at is not None
    # The person's next request is signed out.
    assert other.get(PAGE).status_code in (302, 401, 403)
    actor = PortalUser.objects.get(email="admin@example.org").pk
    ended = AuditEvent.objects.get(event_type="admin_revoked", subject_id=target.pk)
    assert ended.actor_id == target.principal_id
    signed_out = AuditEvent.objects.get(
        event_type="admin_session_signed_out", subject_id=target.pk
    )
    assert signed_out.actor_id == actor
    # It no longer appears, and signing it out again changes nothing and
    # lands on a page that says so, not "Signed out …".
    assert sign_out_button(target) not in admin.get(PAGE).content.decode()
    assert_already_ended(admin, target)
    assert (
        AuditEvent.objects.filter(
            event_type__in=("admin_revoked", "admin_session_signed_out"),
            subject_id=target.pk,
        ).count()
        == 2
    )
    # Sign-in activity on System logs lists both for that session.
    logs = admin.get("/admin/system/logs/?applied=yes&audit=yes&activity=sign_in")
    assert logs.status_code == 200
    text = logs.content.decode()
    assert "signed out someone" in text


def test_your_own_current_session_and_bad_requests_are_refused(two_admins, google):  # noqa: F811
    """Your current session, an unknown id, extra fields or a bad sort: refused."""
    admin = sign_in_as(google, "admin@example.org", "admin-subject")
    sign_in_as(google, "other@example.org", "other-subject")
    mine = browser_session("admin@example.org")
    target = browser_session("other@example.org")
    assert post(admin, PAGE, session_id=mine.pk).status_code >= 400
    assert post(admin, PAGE, session_id="not-a-uuid").status_code >= 400
    assert post(admin, PAGE, session_id=target.pk, extra="1").status_code >= 400
    assert post(admin, PAGE + "?sort=bogus", session_id=target.pk).status_code >= 400
    assert admin.get(PAGE + "?page=2").status_code == 400
    for row in (mine, target):
        row.refresh_from_db()
        assert row.revoked_at is None
    assert not AuditEvent.objects.filter(event_type="admin_session_signed_out")
    # Your own session in another browser can be signed out.
    second = sign_in_as(google, "admin@example.org", "admin-subject")
    assert second.get(PAGE).status_code == 200
    assert post(admin, PAGE, session_id=browser_session().pk).status_code == 302


def test_command_sessions_are_left_to_automation_access(two_admins, google):  # noqa: F811
    """A command session is neither listed nor signed out here."""
    browser, secret, _ = paired(two_admins)
    caller = command(two_admins, secret)
    login = AutomationLogin.objects.get(portal_session_id=caller.portal_session.pk)
    html = browser.get(PAGE).content.decode()
    assert f'value="{login.portal_session_id}"' not in html
    response = post(browser, PAGE, session_id=login.portal_session_id)
    assert response.status_code >= 400
    assert PortalSession.objects.get(pk=login.portal_session_id).revoked_at is None


def test_only_administrators_see_the_page(two_admins, google):  # noqa: F811
    """Staff are refused both the list and Sign out."""
    sign_in_as(google, "admin@example.org", "admin-subject")
    staff = sign_in_as(google, "staff@example.org", "staff-subject")
    target = browser_session("admin@example.org")
    assert staff.get(PAGE).status_code == 403
    assert post(staff, PAGE, session_id=target.pk).status_code == 403
    target.refresh_from_db()
    assert target.revoked_at is None


@pytest.fixture
def with_leader(tmp_path, settings, real_limiter):
    """An Administrator and a Ministry leader."""
    return auth_runtime(
        tmp_path,
        settings,
        real_limiter,
        records=[address(), address("leader@example.org", roles=("ministry_leader",))],
    )


def test_a_ministry_leader_is_refused(with_leader, google):
    """A Ministry leader can neither list sessions nor sign one out."""
    sign_in_as(google, "admin@example.org", "admin-subject")
    leader = sign_in_as(google, "leader@example.org", "leader-subject")
    target = browser_session("admin@example.org")
    assert leader.get(PAGE).status_code == 403
    assert post(leader, PAGE, session_id=target.pk).status_code == 403
    target.refresh_from_db()
    assert target.revoked_at is None


def test_idle_sessions_are_left_out_and_signing_one_out_is_a_no_op(
    two_admins,  # noqa: F811
    google,
):
    """A session unused past the 60-minute idle limit is not listed; posting
    it revokes and records nothing and says it had already ended."""
    sign_in_as(google, "other@example.org", "other-subject")
    target = browser_session("other@example.org")
    with unguarded():
        PortalSession.objects.filter(pk=target.pk).update(
            authenticated_at=F("authenticated_at") - timedelta(minutes=61),
            last_activity_at=F("last_activity_at") - timedelta(minutes=61),
        )
    admin = sign_in_as(google, "admin@example.org", "admin-subject")
    html = admin.get(PAGE).content.decode()
    assert "other@example.org" not in html
    assert_already_ended(admin, target)
    target.refresh_from_db()
    assert target.revoked_at is None
    assert not AuditEvent.objects.filter(
        event_type__in=("admin_revoked", "admin_session_signed_out"),
        subject_id=target.pk,
    )


def test_sessions_issued_before_a_recovery_are_left_out(two_admins, google):  # noqa: F811
    """A session older than the latest offline recovery is already refused by
    admission, so it is not listed, and posting it is a no-op."""
    sign_in_as(google, "other@example.org", "other-subject")
    target = browser_session("other@example.org")
    activation = ConfigurationActivation.objects.order_by("-created_at").first()
    with unguarded():
        AdminRevocation.objects.create(activation=activation)
    # Signed in after the recovery, so this session is current.
    admin = sign_in_as(google, "admin@example.org", "admin-subject")
    html = admin.get(PAGE).content.decode()
    assert "other@example.org" not in html
    assert "admin@example.org (you, this browser)" in html
    assert_already_ended(admin, target)
    assert not AuditEvent.objects.filter(
        event_type="admin_session_signed_out", subject_id=target.pk
    )
