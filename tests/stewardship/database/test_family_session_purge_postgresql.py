"""The hourly maintenance task deletes ended Family and anonymous sessions (#2362).

Frozen migration 0199 adds ``stewardship_family_session_purge_v1`` and
``stewardship_anonymous_session_purge_v1``. The maintenance pass runs here as
the general worker login with exactly the grants ``runtime_grants`` declares,
as in production, so the tests prove the worker can do the cleanup without
ever reading a session key. The DO block's checks are run again against the
state before 0199 and against altered definitions.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.auth_models import OAuthStateConsumption
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilySession
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.source.models import SourceSnapshotPin

from .auth_builders import unguarded
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import login
from .test_response_baselines_postgresql import issue

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0199_family_session_purge.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
FAMILY_PURGE = "public.stewardship_family_session_purge_v1(uuid[])"
ANONYMOUS_PURGE = "public.stewardship_anonymous_session_purge_v1(integer)"
PURGE_CALL = "SELECT public.stewardship_family_session_purge_v1(%s::uuid[])"


def lapse(session_id):
    """Make one Family session idle for two hours, as if left open."""
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_family_session SET "
            "authenticated_at=authenticated_at-interval '2 hours', "
            "last_activity_at=last_activity_at-interval '2 hours' WHERE id=%s",
            [session_id],
        )


def signed_in(code):
    """Sign the Family in from a new browser; return it and the new session row."""
    before = set(FamilySession.objects.values_list("pk", flat=True))
    client, response = login(code)
    assert response.status_code == 302
    return client, FamilySession.objects.exclude(pk__in=before).get()


def expired_anonymous_session():
    """Save a Django session that expired a second ago and names no sign-in."""
    store = SessionStore()
    store["oauth_state"] = "synthetic-state"
    store.set_expiry(timezone.now() - timedelta(seconds=1))
    store.save()
    return store.session_key


def test_the_worker_deletes_ended_family_and_anonymous_sessions(response_service):
    """Lapsed and signed-out sessions, expired anonymous ones and receipts go.

    The live session, an unexpired anonymous session and an unexpired receipt
    stay; the lapsed session's open form is cancelled and its source pin
    released, and no back-dated ending event is recorded for it.
    """
    form = issue(response_service).baseline
    lapsed = response_service.request.family_session
    lapse(lapsed.pk)
    live = signed_in(response_service.code)[1]
    out_client, signed_out = signed_in(response_service.code)
    response = out_client.post(
        "/family/logout",
        {"csrfmiddlewaretoken": out_client.cookies["pk_family_csrf"].value},
    )
    assert response.status_code == 302
    expired = expired_anonymous_session()
    current = SessionStore()
    current["oauth_state"] = "synthetic-state"
    current.save()
    now = timezone.now()
    OAuthStateConsumption.objects.create(
        fingerprint="a" * 64, expires_at=now - timedelta(seconds=1)
    )
    OAuthStateConsumption.objects.create(
        fingerprint="b" * 64, expires_at=now + timedelta(minutes=10)
    )
    events = AuditEvent.objects.count()

    with task_login(ServiceRole.WORKER):
        totals = automation.maintain()

    assert totals["family_sessions_removed"] == 2
    assert totals["anonymous_sessions_removed"] == 1
    assert totals["oauth_receipts_removed"] == 1
    assert set(FamilySession.objects.values_list("pk", flat=True)) == {live.pk}
    assert not Session.objects.filter(
        pk__in=[lapsed.session_id, signed_out.session_id, expired]
    ).exists()
    assert Session.objects.filter(pk=live.session_id).exists()
    assert Session.objects.filter(pk=current.session_key).exists()
    assert list(OAuthStateConsumption.objects.values_list("pk", flat=True)) == [
        "b" * 64
    ]
    form.refresh_from_db()
    assert form.state == "cancelled" and form.ended_at is not None
    assert not SourceSnapshotPin.objects.filter(parent_id=form.pk).exists()
    # Deleting a lapsed session records nothing: no family_session_ended
    # event dated at cleanup, and no other event either.
    assert AuditEvent.objects.count() == events
    assert not AuditEvent.objects.filter(event_type="family_session_ended").exists()

    # A second pass finds nothing more.
    with task_login(ServiceRole.WORKER):
        totals = automation.maintain()
    assert totals["family_sessions_removed"] == 0
    assert totals["anonymous_sessions_removed"] == 0
    assert totals["oauth_receipts_removed"] == 0


def test_the_worker_never_reads_a_session_key(response_service):
    """No grant reaches session_id or django_session; the purges return counts."""
    issue(response_service)
    live = response_service.request.family_session
    with task_login(ServiceRole.WORKER):
        with pytest.raises(DatabaseError), transaction.atomic():
            list(FamilySession.objects.values_list("session_id", flat=True))
        with pytest.raises(DatabaseError), transaction.atomic():
            list(Session.objects.values_list("pk", flat=True))
        with pytest.raises(DatabaseError), transaction.atomic():
            FamilySession.objects.filter(pk=live.pk).delete()
        # A live session is never purged, even when named.
        with work_transaction(), connection.cursor() as cursor:
            cursor.execute(PURGE_CALL, [[live.pk]])
            assert cursor.fetchone() == (0,)
    assert FamilySession.objects.filter(pk=live.pk).exists()
    assert Session.objects.filter(pk=live.session_id).exists()


@pytest.mark.parametrize(
    "call",
    [
        "SELECT public.stewardship_family_session_purge_v1(ARRAY[]::uuid[])",
        "SELECT public.stewardship_anonymous_session_purge_v1(10)",
    ],
)
def test_only_the_worker_may_purge(auth_service, call):
    """Web has no EXECUTE, and the body refuses any other login too."""
    with (
        task_login(ServiceRole.WEB),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(call)


@pytest.mark.parametrize("batch", [0, 1001, None])
def test_the_anonymous_purge_refuses_an_unbounded_batch(auth_service, batch):
    """1 to 1000 sessions per call, as the Python cleanups require."""
    with (
        pytest.raises(DatabaseError, match="bounded batch"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT public.stewardship_anonymous_session_purge_v1(%s)", [batch]
        )


def test_a_sealed_go_live_inventory_keeps_its_testing_session(response_service):
    """The cleanup worker owns those; the purge skips them even when named."""
    from .test_cleanup_tasks_postgresql import queued

    form = issue(response_service).baseline
    session = response_service.request.family_session
    queued(response_service)
    response_service.campaign.refresh_from_db()
    assert response_service.campaign.credential_state.go_live_gate
    lapse(session.pk)
    with work_transaction(), connection.cursor() as cursor:
        cursor.execute(PURGE_CALL, [[session.pk]])
        assert cursor.fetchone() == (0,)
    assert FamilySession.objects.filter(pk=session.pk).exists()
    assert FamilyFormBaseline.objects.get(pk=form.pk).state == "open"


def check_refuses(prepare, message="not installed as declared"):
    """Run the DO block after ``prepare``; it must refuse, and nothing stays."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(prepare)
        cursor.execute(CHECK)


def test_the_check_passes_on_the_installed_definers(auth_service):
    """The block commits on exactly what 0199 installed."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "prepare, message",
    [
        # The state before 0199: neither definer exists.
        (
            f"DROP FUNCTION {FAMILY_PURGE}; DROP FUNCTION {ANONYMOUS_PURGE}",
            "not installed as declared",
        ),
        (f"DROP FUNCTION {ANONYMOUS_PURGE}", "not installed as declared"),
        # What CREATE OR REPLACE without SECURITY DEFINER would leave.
        (
            f"ALTER FUNCTION {FAMILY_PURGE} SECURITY INVOKER",
            "not installed as declared",
        ),
        (
            f"ALTER FUNCTION {ANONYMOUS_PURGE} SECURITY INVOKER",
            "not installed as declared",
        ),
        (f"ALTER FUNCTION {FAMILY_PURGE} RESET search_path", "not installed"),
        (
            f"ALTER FUNCTION {ANONYMOUS_PURGE} SET search_path TO public",
            "not installed",
        ),
        (f"GRANT EXECUTE ON FUNCTION {FAMILY_PURGE} TO PUBLIC", "owner or grants"),
        (
            f"GRANT EXECUTE ON FUNCTION {ANONYMOUS_PURGE} TO pg_read_all_data",
            "owner or grants",
        ),
    ],
)
def test_the_check_refuses_a_missing_or_altered_definer(auth_service, prepare, message):
    """Absent (pre-0199), invoker rights, another search_path or a grant."""
    check_refuses(prepare, message)
