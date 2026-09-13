"""Exercise general task ownership with actual restricted producer/consumer SQL."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
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
from parishkit.stewardship.runtime_grants import admit_columns, runtime_grants

from .campaign_builders import draft_campaign
from .credential_builders import family_campaign
from .test_work_admission_postgresql import ordinary

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def task_login(service):
    """Create/remove only a fresh UUID-named fixture login; never alter real roles."""
    role = sql.Identifier("test_background_" + uuid4().hex)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(role))
    try:
        tables, columns = runtime_grants(service)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            for table, privileges in tables.items():
                cursor.execute(
                    sql.SQL("GRANT {} ON public.{} TO {}").format(
                        sql.SQL(", ").join(
                            sql.SQL(value) for value in sorted(privileges)
                        ),
                        sql.Identifier(table),
                        role,
                    )
                )
            for table, privileges in columns.items():
                for privilege, names in privileges.items():
                    cursor.execute(
                        sql.SQL("GRANT {} ({}) ON public.{} TO {}").format(
                            sql.SQL(privilege),
                            sql.SQL(", ").join(
                                sql.Identifier(name) for name in sorted(names)
                            ),
                            sql.Identifier(table),
                            role,
                        )
                    )
            cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(role))
        admit_columns(connection, tables, columns)
        yield
    finally:
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
            task_type="grant_probe",
            domain_request_id=uuid4(),
            actor_id=None,
            correlation_id=uuid4(),
            admit=handler.admit,
        )


def test_scheduler_can_enqueue_and_scan_but_cannot_claim(tmp_path):
    """A producer can retain singleton/row locks without execution-write authority."""
    _, campaign, _ = draft_campaign(tmp_path)
    handler = source_handler(campaign)
    handlers = {"grant_probe": handler}
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        task = create_task(handler)
        guard.check()
        hints, _ = collect_hints(handlers=handlers)
        assert [hint.run_id for hint in hints] == [task.run_id]
        with pytest.raises(DatabaseError):
            claim_hint(
                task.run_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers=handlers,
            )


def test_worker_can_claim_progress_complete_and_record_private_safe_audit(tmp_path):
    """Verified metadata effects and their triggers need no broader web identity."""
    _, campaign, _ = draft_campaign(tmp_path)
    handler = source_handler(campaign)
    with task_login(ServiceRole.WORKER):
        task = create_task(handler)
        execution = claim_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"grant_probe": handler},
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
        "SELECT * FROM stewardship_family_token",
        "SELECT * FROM stewardship_portal_session",
        "SELECT * FROM stewardship_audit_context",
        "UPDATE stewardship_campaign SET state='active'",
        "UPDATE stewardship_campaign_credentials SET go_live_gate=false",
        "INSERT INTO stewardship_domain_rule DEFAULT VALUES",
        "DELETE FROM stewardship_task_event",
    ],
)
def test_background_sql_cannot_read_private_payloads_or_expand_authority(
    service, statement
):
    """Exercise the real server, not merely equality against the grant registry."""
    with (
        task_login(service),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
