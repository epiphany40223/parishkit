"""Durable Family engagement (#477): real hooks, monotonic guards, cleanup, backfill."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.cleanup_catalog import (
    CleanupCategory,
    iter_inventory,
)
from parishkit.stewardship.campaigns.credential_models import FamilySession
from parishkit.stewardship.campaigns.engagement import record_engagement
from parishkit.stewardship.campaigns.engagement_models import FamilyEngagement
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.baselines import issue_baseline

from .auth_builders import unguarded
from .test_background_grants_postgresql import task_login
from .test_cleanup_tasks_postgresql import queued, run
from .test_family_auth_postgresql import login
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)
FAMILY = "/family/presence"


def beat(browser, section):
    """One heartbeat with the real CSRF cookie, as the form's JavaScript sends it."""
    return browser.post(
        FAMILY,
        {"section": section},
        HTTP_X_CSRFTOKEN=browser.cookies["pk_family_csrf"].value,
    )


def age_presence(seconds):
    """Make the one session's last heartbeat old enough for the next one to count.

    The whole session ages together, as backdate_session does, so the
    presence-shape constraint (presence_at >= authenticated_at) still holds.
    """
    shift = timedelta(seconds=seconds)
    with unguarded():
        FamilySession.objects.update(
            authenticated_at=F("authenticated_at") - shift,
            last_activity_at=F("last_activity_at") - shift,
            expires_at=F("expires_at") - shift,
            presence_at=F("presence_at") - shift,
        )


def row():
    return FamilyEngagement.objects.get()


def test_sign_in_form_and_heartbeats_build_one_monotonic_row(response_service):
    """Link, form and the furthest step land on one row per Family and mode."""
    first = row()
    session = FamilySession.objects.get()
    assert (first.mode, first.rehearsal_epoch_id) == (
        "test",
        session.rehearsal_epoch_id,
    )
    assert first.family_id == session.family_id and first.actor_id == session.family_id
    assert first.first_link_at is not None and first.first_form_at is None
    assert first.last_seen_at == first.first_link_at and first.version == 1
    assert (first.furthest_section, first.furthest_at) == ("", None)

    issue_baseline(response_service.request, response_service.service)
    opened = row()
    assert opened.first_link_at == first.first_link_at
    assert (
        opened.first_form_at is not None
        and opened.first_form_at >= opened.first_link_at
    )
    assert opened.last_seen_at == opened.first_form_at and opened.version == 2

    browser = response_service.client
    assert beat(browser, "welcome").status_code == 200
    welcome = row()
    assert (welcome.furthest_section, welcome.first_progress_at) == ("welcome", None)
    assert welcome.furthest_at is not None and welcome.version == 3
    age_presence(31)
    assert beat(browser, "ministry").status_code == 200
    progressed = row()
    assert progressed.furthest_section == "ministry"
    assert progressed.first_progress_at == progressed.furthest_at
    assert progressed.furthest_at > welcome.furthest_at
    # Going back to an earlier step is presence, not regress.
    age_presence(31)
    assert beat(browser, "census").status_code == 200
    back = row()
    assert (back.furthest_section, back.furthest_at, back.first_progress_at) == (
        "ministry",
        progressed.furthest_at,
        progressed.first_progress_at,
    )
    assert back.last_seen_at > progressed.last_seen_at and back.version == 5
    assert FamilySession.objects.get().presence_section == "census"
    # Within the 30-second bound a heartbeat writes neither record.
    assert beat(browser, "review").status_code == 200
    assert row().version == 5
    # A second sign-in of the same Family keeps the earliest link instant.
    _, response = login(response_service.code)
    assert response.status_code == 302
    again = row()
    assert again.first_link_at == first.first_link_at
    assert again.last_seen_at > back.last_seen_at and again.version == 6
    assert FamilyEngagement.objects.count() == 1


def test_sql_refuses_every_backward_move(response_service):
    """The guard, not only the upsert, keeps instants and steps monotonic."""
    issue_baseline(response_service.request, response_service.service)
    assert beat(response_service.client, "census").status_code == 200
    current = row()
    later = current.first_link_at + timedelta(minutes=5)
    for change in (
        {"first_link_at": later},
        {"first_link_at": None},
        {"first_form_at": later},
        {"first_progress_at": None},
        {"furthest_section": "welcome", "furthest_at": current.furthest_at},
        {"furthest_at": current.furthest_at + timedelta(seconds=1)},
        {"last_seen_at": current.last_seen_at - timedelta(seconds=1)},
    ):
        with (
            pytest.raises(IntegrityError, match="only moves forward"),
            transaction.atomic(),
        ):
            FamilyEngagement.objects.filter(pk=current.pk).update(
                **change, version=F("version") + 1
            )
    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        FamilyEngagement.objects.filter(pk=current.pk).update(
            mode="live", rehearsal_epoch_id=None, version=F("version") + 1
        )
    with (
        pytest.raises(IntegrityError, match="advance the record version"),
        transaction.atomic(),
    ):
        FamilyEngagement.objects.filter(pk=current.pk).update(actor_id=uuid4())
    with pytest.raises(IntegrityError, match="future"), transaction.atomic():
        FamilyEngagement.objects.filter(pk=current.pk).update(
            last_seen_at=current.last_seen_at + timedelta(hours=1),
            version=F("version") + 1,
        )
    assert row().version == current.version


def test_only_the_web_login_writes_and_nobody_deletes_outside_cleanup(
    response_service,
):
    """The worker holds no grant and the guard refuses it anyway; web cannot delete."""
    current = row()
    observation = {
        "family_id": current.family_id,
        "mode": "test",
        "rehearsal_epoch_id": current.rehearsal_epoch_id,
        "seen_at": database_now(),
        "link_at": current.first_link_at,
    }
    with web_login():
        with transaction.atomic():
            assert record_engagement(**observation)
        assert row().version == current.version + 1
        # Nothing new: no update, no version, no trigger.
        with transaction.atomic():
            assert not record_engagement(
                **observation | {"seen_at": row().last_seen_at}
            )
        assert row().version == current.version + 1
        with (
            pytest.raises(ProgrammingError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM stewardship_family_engagement WHERE id=%s", [current.pk]
            )
    with (
        task_login(ServiceRole.WORKER),
        pytest.raises(ProgrammingError),
        transaction.atomic(),
    ):
        record_engagement(**observation)
    with connection.cursor() as cursor:
        cursor.execute("CREATE ROLE pk_engagement_probe LOGIN NOINHERIT")
        cursor.execute("GRANT USAGE ON SCHEMA public TO pk_engagement_probe")
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON stewardship_family_engagement, "
            "stewardship_rehearsal_epoch, stewardship_family_campaign "
            "TO pk_engagement_probe"
        )
        cursor.execute("SET SESSION AUTHORIZATION pk_engagement_probe")
    try:
        with pytest.raises(IntegrityError, match="web login"), transaction.atomic():
            record_engagement(**observation)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute("DROP OWNED BY pk_engagement_probe")
            cursor.execute("DROP ROLE pk_engagement_probe")
    # The owner itself cannot delete a row the cleanup does not own.
    with pytest.raises(IntegrityError, match="retained"), transaction.atomic():
        FamilyEngagement.objects.filter(pk=current.pk).delete()
    assert FamilyEngagement.objects.filter(pk=current.pk).exists()


def test_testing_rows_are_inventoried_and_deleted_by_production_cleanup(
    response_service,
):
    """Engagement is Testing detail: selected exactly, deleted only by its worker."""
    issue_baseline(response_service.request, response_service.service)
    current = row()
    with work_transaction():
        targets = {
            (t.category, t.identifier)
            for t in iter_inventory(response_service.campaign.pk)
        }
    assert (CleanupCategory.ENGAGEMENT, current.pk) in targets
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT target_id FROM stewardship_cleanup_inventory_v1(%s) "
            "WHERE category='engagement'",
            [response_service.campaign.pk],
        )
        assert cursor.fetchall() == [(current.pk,)]
    status = queued(response_service)
    # Inventoried rows belong to the worker: even the invalidated epoch does
    # not let anyone else delete them before the batch that owns them.
    with (
        pytest.raises(IntegrityError, match="belongs to its cleanup worker"),
        work_transaction(),
    ):
        FamilyEngagement.objects.filter(pk=current.pk).delete()
    assert run(status)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == "cleanup_complete"
    assert not FamilyEngagement.objects.exists()


def test_seen_last_check_bounds_every_instant(response_service):
    """SQL itself refuses an instant after last_seen_at, with or without guards."""
    current = row()
    with (
        pytest.raises(IntegrityError, match="family_engagement_seen_last"),
        unguarded(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_family_engagement(id,correlation_id,version,"
            "family_id,mode,rehearsal_epoch_id,first_link_at,furthest_section,"
            "last_seen_at) VALUES (%s,%s,1,%s,'test',%s,%s,'',%s)",
            [
                uuid4(),
                uuid4(),
                current.family_id,
                current.rehearsal_epoch_id,
                current.last_seen_at + timedelta(seconds=1),
                current.last_seen_at,
            ],
        )


@pytest.mark.parametrize("path", ["sign_in", "form"])
def test_reporting_failure_never_refuses_the_family(
    response_service, monkeypatch, path
):
    """A refused engagement write rolls back alone; the Family's request goes on."""
    from parishkit.stewardship.campaigns import engagement

    calls = []

    def fail(**observation):
        """Abort PostgreSQL's current statement, not just raise a Python error."""
        calls.append(observation["family_id"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 / 0")

    monkeypatch.setattr(engagement, "record_engagement", fail)
    before = row()
    events = OperationalLog.objects.filter(event="family_engagement_failed")
    assert not events.exists()
    if path == "sign_in":
        client, response = login(response_service.code)
        assert response.status_code == 302
        assert client.get("/family/").status_code == 200
        assert FamilySession.objects.count() == 2
    else:
        form = issue_baseline(response_service.request, response_service.service)
        assert form.baseline.state == "open"
    assert calls and row().version == before.version
    event = events.get()
    assert event.level == "ERROR" and event.schema == "failure"
    # What failed and its category (#633), never the exception's text.
    assert event.context.keys() == {"failure", "failure_kind", "outcome"}
    assert event.context["failure"] == "family_engagement"
    assert event.context["outcome"] == "failed"


def test_first_insert_and_form_issuance_do_not_deadlock(response_service):
    """A first insert and a concurrent form issuance never deadlock.

    Two independent fixes each prevent it: the engagement foreign key is
    checked at insert, so the KEY SHARE on the Family row is taken before
    form issuance could lock that row and wait on this transaction; and form
    issuance locks the Family row FOR NO KEY UPDATE, which does not conflict
    with KEY SHARE at all (the backfill's shape).
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    session = FamilySession.objects.get()
    with unguarded():
        # The Family has no engagement row yet, as before the backfill.
        FamilyEngagement.objects.all().delete()
    inserted, release = threading.Event(), threading.Event()

    def backfill_like_insert():
        """Insert the Family's row and hold the transaction open until released."""
        try:
            with transaction.atomic():
                now = database_now()
                assert record_engagement(
                    family_id=session.family_id,
                    mode="test",
                    rehearsal_epoch_id=session.rehearsal_epoch_id,
                    seen_at=now,
                    link_at=now,
                )
                inserted.set()
                assert release.wait(20)
            return "committed"
        finally:
            connections.close_all()

    def issue():
        """Form issuance locks the Family row and then records the form."""
        try:
            return issue_baseline(
                response_service.request, response_service.service
            ).baseline.state
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        inserter = pool.submit(backfill_like_insert)
        assert inserted.wait(20)
        issuer = pool.submit(issue)
        # Let issuance reach the Family row lock before the insert commits.
        assert not issuer.done()
        threading.Event().wait(0.5)
        release.set()
        assert inserter.result(timeout=20) == "committed"
        assert issuer.result(timeout=20) == "open"
    final = row()
    assert final.first_link_at is not None and final.first_form_at is not None
