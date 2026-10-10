"""Exercise general task ownership with actual restricted producer/consumer SQL."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.backends.signals import connection_created
from psycopg import sql

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, claim_hint
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.runtime_grants import (
    admit_columns,
    runtime_functions,
    runtime_grants,
)

from .campaign_builders import draft_campaign
from .credential_builders import family_campaign
from .response_builders import live_response_service  # noqa: F401
from .role_grants import grant_runtime
from .test_work_admission_postgresql import ordinary

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def task_login(service, *, reconnect=False, exact=True):
    """Create a fresh fixture role; exact-name probes never adopt existing roles.

    SQL binds task claims and transitions to the provisioned login names
    (stewardship_task_type_login_v1), so the exact name is the default. A
    random name (exact=False) models an unrecognized login. The "download"
    stream login writes no tasks and keeps its random name.
    """
    name = (
        ("pk_stewardship_" + service.value.replace("-", "_"))
        if exact and isinstance(service, ServiceRole)
        else "test_background_" + uuid4().hex
    )
    role = sql.Identifier(name)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(role))

    def restrict_connection(sender, connection, **kwargs):
        """Keep provider socket closure and renewal threads on the same test role."""
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(role))

    try:
        tables, columns = runtime_grants(service)
        with connection.cursor() as cursor:
            grant_runtime(cursor, name, tables, columns, runtime_functions(service))
            cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(role))
        if reconnect:
            connection_created.connect(restrict_connection, weak=False)
        admit_columns(connection, tables, columns)
        yield
    finally:
        connection_created.disconnect(restrict_connection)
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(sql.SQL("DROP OWNED BY {}").format(role))
            cursor.execute(sql.SQL("DROP ROLE {}").format(role))


def source_handler(campaign):
    """Synthetic fixed binding exercises real admission without provider I/O."""

    def admit(action, status):
        """Always reload the campaign gates instead of retaining a prior permit."""
        require_source_refresh(campaign_id=campaign.pk)
        return True

    return Handler(WorkQueue.GENERAL, admit, lambda _: None, scope=work_transaction)


def create_task(handler):
    """The creation path enters owning scope before enqueue's retry-root lock."""
    with handler.scope():
        return enqueue(
            task_type="branding_cleanup",
            domain_request_id=uuid4(),
            actor_id=None,
            correlation_id=uuid4(),
            admit=handler.admit,
        )


def test_scheduler_can_enqueue_and_scan_but_cannot_claim(tmp_path):
    """A producer can retain singleton/row locks without execution-write authority."""
    _, campaign, _ = draft_campaign(tmp_path)
    handler = source_handler(campaign)
    handlers = {"branding_cleanup": handler}
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        task = create_task(handler)
        guard.check()
        hints, _ = collect_hints(handlers=handlers)
        assert [hint.run_id for hint in hints] == [task.run_id]
        with pytest.raises(DatabaseError) as error:
            claim_hint(
                task.run_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers=handlers,
            )
        assert error.value.__cause__.sqlstate == "42501"


def test_worker_can_claim_progress_complete_and_record_private_safe_audit(tmp_path):
    """Verified metadata effects and their triggers need no broader web identity."""
    _, campaign, _ = draft_campaign(tmp_path)
    handler = source_handler(campaign)
    # Only the scheduler creates branding cleanup (#389), as the producer
    # test above does; the worker claims and runs what it queued.
    with task_login(ServiceRole.SCHEDULER, exact=True):
        task = create_task(handler)
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"branding_cleanup": handler},
        )
        execution.heartbeat()
        execution.progress(1, 1, phase=TaskPhase.VERIFYING)
        with execution.effect():
            record_action(
                Action.BACKGROUND_VIEWED,
                actor_kind=ActorKind.SYSTEM,
                subject_id=task.run_id,
                context={"outcome": Outcome.SUCCEEDED},
            )
        assert execution.transition("complete").state == "succeeded"


@pytest.mark.parametrize("service", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
def test_both_background_roles_can_check_current_credential_gate_without_mutation(
    tmp_path, service
):
    """Locking the go-live record does not require credential-value write access."""
    _, campaign, _, _ = family_campaign(tmp_path)
    with task_login(service), work_transaction():
        ordinary(campaign)


@pytest.mark.parametrize("service", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT ciphertext FROM stewardship_family_token",
        "SELECT session_id FROM stewardship_portal_session",
        "SELECT context FROM stewardship_audit_context",
        "UPDATE stewardship_campaign_credentials SET go_live_gate=false",
        "INSERT INTO stewardship_domain_rule DEFAULT VALUES",
        "DELETE FROM stewardship_task_event",
        "INSERT INTO stewardship_chair_seed_evidence DEFAULT VALUES",
        "UPDATE stewardship_family_campaign SET last_activity_at=now()",
        "UPDATE stewardship_source_family SET canonical='{}'",
    ],
)
@pytest.mark.sql_rules
def test_background_sql_cannot_read_private_payloads_or_expand_authority(
    service, statement
):
    """Exercise the real server, not merely equality against the grant registry."""
    with (
        task_login(service),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert error.value.__cause__.sqlstate == "42501"


@pytest.mark.parametrize("service", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE stewardship_campaign SET state='active'",
        "UPDATE stewardship_family_token SET digest=NULL",
    ],
)
@pytest.mark.sql_rules
def test_empty_boundary_updates_match_guarded_worker_authority(service, statement):
    """Empty updates have no row effects; populated denials live in boundary tests."""
    with task_login(service), transaction.atomic(), connection.cursor() as cursor:
        if service is ServiceRole.SCHEDULER:
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(statement)
        else:
            cursor.execute(statement)
            assert cursor.rowcount == 0


@pytest.mark.parametrize("service", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
@pytest.mark.sql_rules
def test_empty_source_delete_matches_cleanup_authority(service):
    """Worker cleanup grants allow a zero-row DELETE, not unrestricted row deletion.

    PostgreSQL does not invoke row guards for an empty table. The populated
    live/expired/shared-source denial cases belong to test_setup_disposal_postgresql.
    The scheduler has no deletion grant at all.
    """
    with task_login(service), transaction.atomic(), connection.cursor() as cursor:
        if service is ServiceRole.SCHEDULER:
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute("DELETE FROM stewardship_source_family")
        else:
            cursor.execute("DELETE FROM stewardship_source_family")
            assert cursor.rowcount == 0


@pytest.mark.parametrize("service", [ServiceRole.WORKER, ServiceRole.WEB])
def test_the_response_funnel_reads_with_each_logins_registry_grants(
    live_response_service,  # noqa: F811
    service,
):
    """The funnel runs as the worker (daily digest) and the web (dashboard) (#477).

    Each login gets exactly its registry's grants, so a change to the funnel's
    statements that reads a table or column only one of them may read fails
    here, before every Production digest compile or dashboard view would.
    """
    from datetime import UTC, datetime

    from parishkit.stewardship.reports.digest_funnel import digest_funnel

    from .test_fact_materialization_postgresql import respond

    harness = live_response_service
    respond(harness)
    # After the fixture campaign's clock, so the response is counted.
    instant = datetime(2100, 1, 1, tzinfo=UTC)
    with task_login(service, exact=True, reconnect=True), transaction.atomic():
        funnel = digest_funnel(harness.campaign.pk, "production", instant)
    assert funnel.stage("submitted") == 1
    assert funnel.stage("link_followed") >= 1
