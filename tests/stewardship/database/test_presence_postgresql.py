"""Real Family/Admin sessions prove presence is not activity or credential evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.presence import visible_sessions
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import FAMILY_ABSOLUTE, database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilySession
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in, unguarded
from .campaign_builders import campaign_clock, change
from .credential_builders import populate
from .test_background_grants_postgresql import task_login
from .test_current_chair_postgresql import publish
from .test_family_auth_postgresql import family_service as family_service
from .test_family_auth_postgresql import login
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
ADMIN = "/admin/presence"
FAMILY = "/family/presence"


def beat(browser, **values):
    """One bounded section name, with actual CSRF protection and isolated cookies."""
    return browser.post(
        FAMILY,
        {"section": "welcome", **values},
        HTTP_X_CSRFTOKEN=browser.cookies["pk_family_csrf"].value,
    )


def test_presence_updates_only_observation_and_is_rate_bounded(family_service):
    """Thirty-second rate limiting does not change idle activity or absolute expiry."""
    browser, _ = login(family_service.code)
    before = FamilySession.objects.get()
    assert beat(browser).status_code == 200
    first = FamilySession.objects.get()
    assert first.presence_at is not None and first.presence_section == "welcome"
    assert first.last_activity_at == before.last_activity_at
    assert first.last_keepalive_at == before.last_keepalive_at
    assert first.expires_at == before.expires_at
    assert beat(browser, section="census").status_code == 200
    after = FamilySession.objects.get()
    assert (after.presence_at, after.presence_section, after.version) == (
        first.presence_at,
        first.presence_section,
        first.version,
    )


def test_optional_help_sql_failure_preserves_session_revocation(
    family_service, monkeypatch
):
    """Real aborted SQL in optional content cannot undo presence's security audit."""
    from parishkit.stewardship.responses import availability

    browser, response = login(family_service.code)
    assert response.status_code == 302
    calls = []

    def fail(service, slot):
        """Abort PostgreSQL's current transaction, not just raise a Python error."""
        calls.append(slot)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 / 0")

    monkeypatch.setattr(availability, "public_help", fail)
    before = AuditEvent.objects.filter(event_type="family_session_ended").count()
    with campaign_clock(family_service.campaign.active_configuration.ends_at):
        denied = beat(browser)
    assert denied.status_code == 403
    assert denied["Cache-Control"] == "no-store"
    assert b"division by zero" not in denied.content
    assert calls == ["access_denied"]
    assert FamilySession.objects.get().revoked_at is not None
    assert (
        AuditEvent.objects.filter(event_type="family_session_ended").count()
        == before + 1
    )


def test_admin_detail_has_names_and_no_answers_or_credentials(family_service, google):
    """The same session appears in the visible count and bounded authorized detail."""
    publish(source())
    family, _ = login(family_service.code)
    assert beat(family).status_code == 200
    browser, _ = signed_in()
    response = browser.get(ADMIN + "?format=json")
    assert response.status_code == 200, response.content
    result = response.json()
    assert result["count"] == 1 and result["sessions"][0]["duid"] == 1
    assert result["sessions"][0]["name"] != "Name unavailable"
    assert set(result["sessions"][0]) == {
        "name",
        "duid",
        "started_at",
        "last_activity_at",
        "presence_at",
        "section",
    }
    assert family_service.code.encode() not in response.content
    assert family_service.token.encode() not in response.content
    page = browser.get(ADMIN).content
    assert b"Recently visible Family sessions" in page
    assert b'class="table-nav"' in page and b'class="data-table"' in page
    assert browser.get(ADMIN + "?size=25").status_code == 200
    assert browser.get(ADMIN + "?size=500").status_code == 400
    assert b"Families on the form now:" in browser.get("/admin/").content


def test_admin_roster_sorts_every_column_on_the_server(family_service, google):
    """Each whitelisted token orders the whole visible set before paging."""
    publish(source())
    for _ in range(2):
        family, _ = login(family_service.code)
        assert beat(family).status_code == 200
    browser, _ = signed_in()

    def sessions(token):
        """The JSON roster under one sort token."""
        response = browser.get(ADMIN, {"format": "json", "sort": token})
        assert response.status_code == 200, response.content
        return response.json()["sessions"]

    oldest = sessions("started")
    assert len(oldest) == 2 and oldest[0]["started_at"] <= oldest[1]["started_at"]
    assert sessions("-started") == oldest[::-1]
    for token in ("name", "-name", "duid", "-activity", "section", "-heartbeat"):
        assert len(sessions(token)) == 2
    page = browser.get(ADMIN, {"sort": "-started", "size": 25}).content
    assert b'aria-sort="descending"' in page and b"Page 1 of 1" in page
    assert b"Showing 1\xe2\x80\x932 of 2" in page
    first = browser.get(ADMIN, {"format": "json", "sort": "started", "size": 1})
    assert first.json()["has_next"]
    assert first.json()["sessions"][0]["started_at"] == oldest[0]["started_at"]


def test_visibility_expires_independently_of_logged_in_session(family_service, google):
    """Abandoned visible tabs disappear at 90 seconds without changing login state."""
    browser, _ = login(family_service.code)
    assert beat(browser).status_code == 200
    row = FamilySession.objects.get()
    config = SystemConfiguration.objects.get()
    assert (
        visible_sessions(config, row.presence_at + timedelta(seconds=89)).count() == 1
    )
    assert (
        visible_sessions(config, row.presence_at + timedelta(seconds=90)).count() == 0
    )
    row.refresh_from_db()
    assert row.revoked_at is None


def test_closed_or_ineligible_family_disappears_and_cannot_heartbeat(
    family_service, google
):
    """Presence never overrides campaign boundaries or current Family eligibility."""
    family, _ = login(family_service.code)
    assert beat(family).status_code == 200
    browser, _ = signed_in()
    assert browser.get(ADMIN + "?format=json").json()["count"] == 1
    with campaign_clock(family_service.campaign.active_configuration.ends_at):
        assert browser.get(ADMIN + "?format=json").json()["count"] == 0
        assert beat(family).status_code == 403
    family, _ = login(family_service.code)
    assert beat(family).status_code == 200
    assert browser.get(ADMIN + "?format=json").json()["count"] == 1
    populate(
        family_service.campaign,
        family_service.rings,
        [FamilyStatus(1, False, False, False, False)],
        generation=2,
    )
    assert browser.get(ADMIN + "?format=json").json()["count"] == 0
    assert beat(family).status_code == 403


def test_admin_poll_does_not_refresh_admin_idle(family_service, google):
    """Polling in an untouched Admin tab cannot keep its authorization alive."""
    from parishkit.stewardship.accounts.models import PortalSession

    browser, _ = signed_in()
    row = PortalSession.objects.get(revoked_at__isnull=True)
    before = row.last_activity_at
    for _ in range(2):
        assert browser.get(ADMIN + "?format=json").status_code == 200
    row.refresh_from_db()
    assert row.last_activity_at == before


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_presence_is_admin_only_even_for_direct_json(
    family_service, google, auth_service, role
):
    """Staff access to codes does not imply presence or observation access."""
    store = auth_service.store
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("observer@example.org", roles=(role,)),
            }
        ],
    )
    assert result.state == "applied"
    google[0]["email"] = "observer@example.org"
    google[0]["sub"] = f"observer-{role}"
    browser, _ = signed_in()
    assert browser.get("/admin/").status_code == 200
    assert browser.get(ADMIN).status_code == 403
    assert browser.get(ADMIN + "?format=json").status_code == 403
    assert b"data-presence-indicator" not in browser.get("/admin/").content


@pytest.mark.parametrize(
    "values",
    [
        {"section": "unknown"},
        {"answer": "private"},
        {"section": ["welcome", "financial"]},
        {"timestamp": "2030-01-01"},
    ],
)
def test_heartbeat_rejects_answers_and_browser_owned_timestamps(family_service, values):
    """Only one known section can cross this public observation boundary."""
    browser, _ = login(family_service.code)
    assert beat(browser, **values).status_code == 400
    assert FamilySession.objects.get().presence_at is None


def test_heartbeat_requires_csrf_and_does_not_accept_admin_login(
    family_service, google
):
    """Portal cookie namespaces are not interchangeable even on the same origin."""
    family, _ = login(family_service.code)
    assert family.post(FAMILY, {"section": "welcome"}).status_code == 403
    admin, _ = signed_in()
    # A valid Family-namespace CSRF token isolates the refusal to the session.
    assert admin.get("/").status_code == 200
    assert beat(admin).status_code == 403
    assert family.get(FAMILY).status_code == 405


def test_presence_sql_rejects_idle_renewal_and_clamps_timestamp(family_service):
    """Even a direct ORM writer cannot combine passive observation with activity."""
    login(family_service.code)
    row = FamilySession.objects.get()
    now = database_now()
    with pytest.raises(IntegrityError, match="Presence"), transaction.atomic():
        FamilySession.objects.filter(pk=row.pk).update(
            presence_at=now,
            presence_section="review",
            last_activity_at=now,
            version=F("version") + 1,
        )
    FamilySession.objects.filter(pk=row.pk).update(
        presence_at=now + timedelta(days=1),
        presence_section="review",
        version=F("version") + 1,
    )
    row.refresh_from_db()
    assert now <= row.presence_at <= database_now()
    with pytest.raises(IntegrityError, match="Presence"), transaction.atomic():
        FamilySession.objects.filter(pk=row.pk).update(
            presence_at=database_now(),
            presence_section="census",
            version=F("version") + 1,
        )


@pytest.mark.parametrize("idle_minutes", [45, 61])
def test_presence_uses_the_family_idle_deadline(family_service, idle_minutes):
    """The real SQL clock accepts 45-minute idle sessions but refuses expired ones."""
    instant = database_now() - timedelta(minutes=idle_minutes)
    browser, _ = login(family_service.code)
    # SQL stamps a new session with its own clock (#306 M3), so back-date
    # the whole session as the owner to model one idle since `instant`.
    with unguarded():
        FamilySession.objects.update(
            authenticated_at=instant,
            last_activity_at=instant,
            expires_at=instant + FAMILY_ABSOLUTE,
        )
    row = FamilySession.objects.get()
    assert row.last_activity_at == instant
    with task_login(ServiceRole.WEB):
        assert beat(browser).status_code == (200 if idle_minutes == 45 else 403)
    row.refresh_from_db()
    assert (row.presence_at is not None) == (idle_minutes == 45)
    assert row.last_activity_at == instant
    if idle_minutes == 61:
        with pytest.raises(IntegrityError, match="Presence"), transaction.atomic():
            FamilySession.objects.filter(pk=row.pk).update(
                presence_at=database_now(),
                presence_section="review",
                version=F("version") + 1,
            )


def test_real_web_grants_support_family_presence_and_admin_names(
    family_service, google
):
    """The actual web SQL role can observe presence without decrypting any token."""
    publish(source())
    family, _ = login(family_service.code)
    admin, _ = signed_in()
    with task_login(ServiceRole.WEB):
        assert beat(family).status_code == 200
        response = admin.get(ADMIN + "?format=json")
        assert response.status_code == 200 and response.json()["count"] == 1
        # The web role also reads the heads, so the Family is named as on the
        # Family codes directory: "Surname, Heads" (#232).
        assert response.json()["sessions"][0]["name"] == "Example, Member"
        assert b"Example, Member" in admin.get(ADMIN).content


def test_header_count_poll_never_fetches_family_names(family_service, google):
    """The always-on indicator needs only a count, not recurring private detail."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    family, _ = login(family_service.code)
    beat(family)
    browser, _ = signed_in()
    with CaptureQueriesContext(connection) as queries:
        response = browser.get(ADMIN + "?format=count")
    assert response.status_code == 200 and response.json()["count"] == 1
    assert set(response.json()) == {"count", "as_of"}
    assert not any(
        table in query["sql"]
        for query in queries
        for table in ("stewardship_source_family", "stewardship_source_member")
    )
    assert not any("pg_advisory_xact_lock" in query["sql"] for query in queries)
    assert not AuditEvent.objects.filter(event_type="family_presence_viewed").exists()
    response = browser.get(ADMIN + "?format=json")
    assert response.status_code == 200
    event = AuditEvent.objects.get(event_type="family_presence_viewed")
    assert event.auditcontext.context["count"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "?format=xml",
        "?format=json&format=count",
        "?page=0",
        "?page=not-a-number",
        "?unknown=value",
        "?sort=presence_at",
        "?sort=name%3B",
        "?sort=-id",
    ],
)
def test_presence_invalid_navigation_is_rejected(family_service, google, query):
    """Presence is a bounded read endpoint, not an open-ended ORM query interface."""
    browser, _ = signed_in()
    assert browser.get(ADMIN + query).status_code == 400


def test_roster_never_waits_behind_the_work_lock_or_another_reader(
    family_service, google
):
    """A promotion holding the work lock, or another open reader, delays no view.

    Another session holds the exclusive work-order lock for the whole check,
    as a source promotion or installer does, and a second reader thread sits
    inside its own snapshot. The roster, its JSON form and the header count
    all still render under a short statement timeout, and the roster still
    records its access audit. A regression back to the work lock fails fast
    with 503 instead of hanging.
    """
    from threading import Event, Thread

    from django.db import connections

    from parishkit.stewardship.campaigns.work_locks import (
        WORK_ORDER_LOCK,
        read_transaction,
    )

    from .test_schedule_views_postgresql import other_session

    publish(source())
    family, _ = login(family_service.code)
    assert beat(family).status_code == 200
    browser, _ = signed_in()
    entered, done = Event(), Event()

    def reader():
        """Hold one snapshot open, as a concurrent Admin page view does."""
        try:
            with read_transaction():
                FamilySession.objects.count()
                entered.set()
                done.wait(30)
        finally:
            connections.close_all()

    thread = Thread(target=reader)
    with other_session() as holder:
        holder.execute("SELECT pg_advisory_lock(%s,%s)", WORK_ORDER_LOCK)
        thread.start()
        try:
            assert entered.wait(30)
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '3s'")
            for query in ("", "?format=json", "?format=count"):
                response = browser.get(ADMIN + query)
                assert response.status_code == 200, query
            assert response.json()["count"] == 1
        finally:
            done.set()
            thread.join(30)
            with connection.cursor() as cursor:
                cursor.execute("RESET statement_timeout")
    assert AuditEvent.objects.filter(event_type="family_presence_viewed").count() == 2


def test_roster_access_revoked_during_its_snapshot_is_refused(
    family_service, google, monkeypatch
):
    """The snapshot cannot hide a revocation committed while the roster was read.

    Another session disables the Administrator after the snapshot began. The
    access recheck runs after the snapshot ends, refuses, and records no
    successful roster disclosure.
    """
    from parishkit.stewardship.accounts import presence
    from parishkit.stewardship.accounts.policy_models import PortalUser

    from .test_schedule_views_postgresql import other_session

    publish(source())
    family, _ = login(family_service.code)
    assert beat(family).status_code == 200
    browser, _ = signed_in()
    admin = PortalUser.objects.get(email="admin@example.org")
    genuine = presence._names

    def revoking(*args, **kwargs):
        """Name the Families as usual while a concurrent session disables us."""
        with other_session() as other:
            other.execute(
                "UPDATE stewardship_portal_user SET disabled=true, "
                "version=version+1 WHERE id=%s",
                [admin.pk],
            )
        return genuine(*args, **kwargs)

    monkeypatch.setattr(presence, "_names", revoking)
    assert browser.get(ADMIN + "?format=json").status_code == 403
    assert not AuditEvent.objects.filter(event_type="family_presence_viewed").exists()
