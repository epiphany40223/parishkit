"""Application incident ownership, atomic notice intent and restart-safe policy."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.db.models import F

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.operational_content import IncidentKind, IncidentLevel
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
)
from parishkit.stewardship.jobs.operational_policy import (
    IncidentPolicy,
    IncidentState,
    observe_incident,
    resolve_incident,
)
from parishkit.stewardship.jobs.operational_storage import (
    record_observation,
    record_recovery,
)
from parishkit.stewardship.jobs.ownership import database_now

from .test_background_grants_postgresql import task_login

KIND = IncidentKind.LIMITER_UNAVAILABLE
POLICY = IncidentPolicy()


def observe(level=IncidentLevel.CRITICAL):
    """Use the application entry point with a bounded internal policy."""
    return record_observation(KIND, level, policy=POLICY)


def state(row):
    """Compare SQL-derived state to the independent pure transition contract."""
    return IncidentState(
        IncidentLevel(row.level),
        row.first_seen,
        row.last_seen,
        row.occurrences,
        row.last_notice_at,
        row.resolved_at,
    )


@pytest.mark.django_db
def test_critical_episode_suppresses_survives_reload_and_recovers_once():
    """Database state, not a process cache, owns counts, suppression and history."""
    assert record_recovery(KIND) is None
    first = observe()
    initial = OperationalNotice.objects.get(incident=first)
    assert (initial.phase, initial.incident_version, initial.occurrences) == (
        "opened",
        1,
        1,
    )
    assert (
        state(first)
        == observe_incident(
            None, IncidentLevel.CRITICAL, first.first_seen, POLICY
        ).state
    )
    second = observe(IncidentLevel.WARNING)
    assert second.pk == first.pk
    assert (
        state(second)
        == observe_incident(
            state(first), IncidentLevel.WARNING, second.last_seen, POLICY
        ).state
    )
    assert OperationalNotice.objects.count() == 1
    recovered = record_recovery(KIND)
    assert (
        state(recovered) == resolve_incident(state(second), recovered.resolved_at).state
    )
    assert list(
        OperationalNotice.objects.order_by("incident_version").values_list(
            "phase", flat=True
        )
    ) == ["opened", "resolved"]
    assert record_recovery(KIND) is None
    later = observe()
    assert later.pk != first.pk
    assert later.occurrences == 1
    assert OperationalNotice.objects.count() == 3


@pytest.mark.django_db
@pytest.mark.parametrize(
    "level,phase",
    [(IncidentLevel.WARNING, "escalated"), (IncidentLevel.CRITICAL, "repeated")],
)
def test_due_policy_matches_pure_decision_without_wall_clock_sleep(level, phase):
    """A valid earlier observation exercises current SQL timing, not an upgrade."""
    earlier = database_now() - timedelta(minutes=16)
    row = OperationalIncident.objects.create(
        kind=KIND.value,
        signal_level=level.value,
        suppression_seconds=900,
        escalation_seconds=900,
        first_seen=earlier,
        last_seen=earlier,
    )
    row.refresh_from_db()
    updated = observe(IncidentLevel.WARNING)
    expected = observe_incident(
        state(row), IncidentLevel.WARNING, updated.last_seen, POLICY
    )
    assert expected.notification.value == phase
    assert state(updated) == expected.state
    notice = OperationalNotice.objects.get(incident=row, incident_version=2)
    assert (notice.phase, notice.occurrences, notice.observed_at) == (
        phase,
        2,
        updated.last_seen,
    )


@pytest.mark.django_db
def test_explicit_escalation_and_silent_warning_recovery():
    """Critical signals bypass the window, while an unnotified recovery is silent."""
    observe(IncidentLevel.WARNING)
    assert not OperationalNotice.objects.exists()
    record_recovery(KIND)
    assert not OperationalNotice.objects.exists()
    warning = observe(IncidentLevel.WARNING)
    critical = observe()
    assert warning.pk == critical.pk
    assert OperationalNotice.objects.get().phase == "escalated"


@pytest.mark.django_db
def test_notice_failure_rolls_back_the_episode_itself():
    """Notification intent cannot be best effort after an incident has committed."""
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_ops_notice "
            "ADD CONSTRAINT test_reject_notice CHECK (false)"
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        observe()
    assert not OperationalIncident.objects.exists()
    assert not OperationalNotice.objects.exists()


@pytest.mark.django_db
def test_episode_derived_fields_and_resolved_history_are_not_writable():
    """One shared episode covers rejected edits without repeatedly flushing schema."""
    row = observe()
    changes = (
        {"level": "WARNING"},
        {"occurrences": 0},
        {"last_seen": row.last_seen - timedelta(seconds=1)},
        {"first_seen": row.first_seen - timedelta(seconds=1)},
        {"last_notice_at": None},
        {"resolved_at": row.last_seen},
        {"kind": "source_stale"},
        {"suppression_seconds": 60},
        {"escalation_seconds": 60},
        {"action": "invented"},
        {"signal_level": "INVENTED"},
    )
    for values in changes:
        with pytest.raises(IntegrityError), transaction.atomic():
            OperationalIncident.objects.filter(pk=row.pk).update(version=2, **values)
    with pytest.raises(IntegrityError), transaction.atomic():
        OperationalIncident.objects.filter(pk=row.pk).update(action="observe")
    record_recovery(KIND)
    with pytest.raises(IntegrityError), transaction.atomic():
        OperationalIncident.objects.filter(pk=row.pk).update(version=F("version") + 1)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_ops_incident WHERE id=%s", [row.pk])
    assert OperationalNotice.objects.count() == 2


@pytest.mark.django_db
def test_forged_notice_or_historical_notice_edit_is_rejected():
    """Even structurally plausible snapshots require their own atomic transition."""
    row = observe()
    with pytest.raises(IntegrityError), transaction.atomic():
        OperationalNotice.objects.create(
            incident=row,
            incident_version=2,
            phase="repeated",
            level="CRITICAL",
            first_seen=row.first_seen,
            observed_at=row.last_seen,
            occurrences=1,
        )
    for statement in (
        "UPDATE stewardship_ops_notice SET phase='resolved'",
        "DELETE FROM stewardship_ops_notice",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
    assert OperationalNotice.objects.get().phase == "opened"


@pytest.mark.django_db
def test_policy_is_pinned_and_invalid_initial_shapes_are_rejected():
    """Changing configuration never rewrites an active episode's past decisions."""
    first = observe()
    second = record_observation(
        KIND, IncidentLevel.CRITICAL, policy=IncidentPolicy(60, 60)
    )
    assert (second.pk, second.suppression_seconds, second.escalation_seconds) == (
        first.pk,
        900,
        900,
    )
    record_recovery(KIND)
    new = record_observation(
        KIND, IncidentLevel.CRITICAL, policy=IncidentPolicy(60, 60)
    )
    assert (new.suppression_seconds, new.escalation_seconds) == (60, 60)
    for values in (
        {"kind": "private@example.test"},
        {"suppression_seconds": 0},
        {"action": "resolve"},
        {"version": 2},
        {"occurrences": 7},
    ):
        kwargs = dict(
            kind=IncidentKind.SOURCE_STALE.value,
            signal_level="WARNING",
            suppression_seconds=900,
            escalation_seconds=900,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            OperationalIncident.objects.create(**(kwargs | values))


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_observations_allocate_one_episode_and_notice():
    """Two processes racing an absent row count twice without duplicate alerts."""
    barrier = Barrier(2)

    def contender():
        """Use a separate connection and finite timeouts for the actual write race."""
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='10s'; SET lock_timeout='5s'")
            barrier.wait(timeout=5)
            return observe().pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        ids = [future.result(timeout=15) for future in futures]
    assert ids[0] == ids[1]
    assert OperationalIncident.objects.get().occurrences == 2
    assert OperationalNotice.objects.count() == 1


def test_untyped_inputs_cannot_reach_the_database():
    """Reject accidental free text or configuration maps before storage admission."""
    with pytest.raises(TypeError):
        record_observation(
            "private@example.test", IncidentLevel.CRITICAL, policy=POLICY
        )
    with pytest.raises(TypeError):
        record_observation(KIND, "CRITICAL", policy=POLICY)
    with pytest.raises(TypeError):
        record_observation(KIND, IncidentLevel.CRITICAL, policy={})
    with pytest.raises(TypeError):
        record_recovery(uuid4())


@pytest.mark.django_db(transaction=True)
def test_worker_can_observe_but_cannot_forge_notices_or_policy_edits():
    """Exercise exact runtime privileges as well as schema-owner shape guards."""
    with task_login(ServiceRole.WORKER, exact=True):
        row = observe()
        assert OperationalNotice.objects.get(incident=row).phase == "opened"
        for statement in (
            "INSERT INTO stewardship_ops_notice DEFAULT VALUES",
            "UPDATE stewardship_ops_incident SET suppression_seconds=60",
            "UPDATE stewardship_ops_incident SET occurrences=99",
            "DELETE FROM stewardship_ops_incident",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert error.value.__cause__.sqlstate == "42501", statement
        assert observe().occurrences == 2
        record_recovery(KIND)
        assert OperationalNotice.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_other_runtime_roles_have_no_incident_mutation_authority():
    """A role name, queue hint or general DB login cannot manufacture alert work."""
    for role in (ServiceRole.WEB, ServiceRole.SCHEDULER, ServiceRole.MAIL_DISPATCH):
        with task_login(role, exact=True):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute("INSERT INTO stewardship_ops_incident DEFAULT VALUES")
            assert error.value.__cause__.sqlstate == "42501", role
