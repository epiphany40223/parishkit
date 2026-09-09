"""Freeze task transitions, retry identity and atomic attempt/audit history.

These guards enforce storage consistency, not authentication or proof of safe
external completion. Runtime roles/admission and domain reconciliation are later
prerequisites. All new functions resolve trusted public tables, not caller paths.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0001_taskrun_storage"),
        ("stewardship_audit", "0006_parish_ownership"),
    ]
    operations = [
        immutable_guard_v1("stewardship_task_event"),
        mutable_guard_v1(
            "stewardship_task_run",
            frozen_fields=(
                "task_type",
                "idempotency_key",
                "root_id",
                "parent_id",
                "retry_sequence",
                "retry_command_id",
                "domain_request_id",
                "initiated_by_id",
            ),
        ),
        migrations.RunSQL(
            sql="""
    CREATE FUNCTION stewardship_task_state_v1()
    RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp AS $$
    DECLARE
        root_record public.stewardship_task_run%ROWTYPE;
        previous public.stewardship_task_run%ROWTYPE;
        instant timestamptz := statement_timestamp();
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Task history cannot be deleted' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            NEW.created_at := instant;
            NEW.updated_at := instant;
            IF NEW.version <> 1 OR NEW.state <> 'queued' OR NEW.attempt <> 0
               OR NEW.fence <> 0 OR NEW.worker_id IS NOT NULL
               OR NEW.heartbeat_at IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
               OR NEW.progress_current <> 0 OR NEW.progress_total <> 0
               OR NEW.actor_id IS DISTINCT FROM NEW.initiated_by_id THEN
                RAISE EXCEPTION 'Invalid initial task state' USING ERRCODE = '23514';
            END IF;
            IF NEW.retry_sequence = 0 THEN
                IF NEW.root_id IS DISTINCT FROM NEW.id OR NEW.parent_id IS NOT NULL
                   OR NEW.retry_command_id IS NOT NULL OR NEW.action <> 'created'
                   OR (NEW.idempotency_key IS NOT NULL AND NEW.idempotency_key !~
                       '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
                THEN
                    RAISE EXCEPTION 'Invalid task root identity'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT * INTO root_record FROM public.stewardship_task_run
                WHERE id = NEW.root_id FOR UPDATE;
                SELECT * INTO previous FROM public.stewardship_task_run
                WHERE root_id = NEW.root_id ORDER BY retry_sequence DESC LIMIT 1;
                IF root_record.id IS NULL OR root_record.retry_sequence <> 0
                   OR previous.state <> 'failed'
                   OR NEW.parent_id IS DISTINCT FROM previous.id
                   OR NEW.retry_sequence <> previous.retry_sequence + 1
                   OR NEW.task_type IS DISTINCT FROM root_record.task_type
                   OR NEW.domain_request_id IS DISTINCT FROM
                      root_record.domain_request_id
                   OR NEW.initiated_by_id IS NULL OR NEW.retry_command_id IS NULL
                   OR NEW.action <> 'explicit_retry'
                   OR NEW.idempotency_key IS DISTINCT FROM
                       'retry:' || NEW.root_id::text || ':' || NEW.retry_sequence::text
                THEN
                    RAISE EXCEPTION 'Invalid explicit task retry'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.state IN ('succeeded', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'Terminal tasks cannot be reopened' USING ERRCODE = '23514';
        END IF;
        -- Every action names its precise permitted edge. Codes carry no raw
        -- provider errors, arguments, output text or private values.
        IF NOT (
            (NEW.action = 'claim' AND OLD.state IN ('queued', 'retry_wait')
                AND NEW.state = 'running' AND OLD.not_before <= instant)
            OR (OLD.state = 'running' AND (
                (NEW.action IN ('heartbeat', 'progress') AND NEW.state = 'running')
                OR (NEW.action = 'complete' AND NEW.state = 'succeeded')
                OR (NEW.action = 'retryable_failure' AND NEW.state = 'retry_wait')
                OR (NEW.action = 'permanent_failure' AND NEW.state = 'failed')
                OR (NEW.action = 'lease_expired' AND NEW.state = 'abandoned'
                    AND OLD.lease_expires_at <= instant)))
            OR (NEW.action = 'safe_cancel' AND NEW.state = 'cancelled'
                AND OLD.state IN ('queued', 'running', 'retry_wait'))
            OR (OLD.state = 'abandoned' AND (
                (NEW.action = 'recovery_retry' AND NEW.state = 'retry_wait')
                OR (NEW.action = 'recovery_complete' AND NEW.state = 'succeeded')
                OR (NEW.action = 'recovery_fail' AND NEW.state = 'failed')
                OR (NEW.action = 'recovery_cancel' AND NEW.state = 'cancelled')))
        ) THEN
            RAISE EXCEPTION 'Invalid task transition or timing' USING ERRCODE = '23514';
        END IF;
        IF NEW.action = 'claim' THEN
            IF NEW.worker_id IS NULL OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
               OR NEW.fence <> OLD.fence + 1 OR NEW.attempt <> OLD.attempt + 1 THEN
                RAISE EXCEPTION 'Invalid task claim' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.worker_id IS DISTINCT FROM OLD.worker_id
               OR NEW.attempt <> OLD.attempt
               OR NEW.fence <> OLD.fence + (CASE WHEN NEW.action = 'lease_expired'
                                               THEN 1 ELSE 0 END) THEN
                RAISE EXCEPTION 'Invalid task claim binding' USING ERRCODE = '23514';
            END IF;
        END IF;
        IF OLD.state = 'running' AND NEW.action <> 'lease_expired'
           AND (OLD.lease_expires_at <= instant
                OR NEW.actor_id IS DISTINCT FROM OLD.worker_id) THEN
            RAISE EXCEPTION 'Task lease is expired or not owned'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.action = 'lease_expired' AND NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Lease expiry has no worker actor' USING ERRCODE = '23514';
        END IF;
        IF NEW.action IN ('claim', 'heartbeat') THEN
            IF NEW.heartbeat_at IS NULL OR NEW.heartbeat_at > instant
               OR NEW.heartbeat_at < NEW.created_at
               OR NEW.lease_expires_at IS NULL OR NEW.lease_expires_at <= instant
               OR NEW.lease_expires_at > instant + interval '300 seconds' THEN
                RAISE EXCEPTION 'Invalid task lease interval' USING ERRCODE = '23514';
            END IF;
            NEW.heartbeat_at := instant;
        ELSIF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
              OR (NEW.state = 'running'
                  AND NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at)
              OR (NEW.state <> 'running' AND NEW.lease_expires_at IS NOT NULL) THEN
            RAISE EXCEPTION 'Task lease fields require claim or heartbeat'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.action IN ('retryable_failure', 'recovery_retry') THEN
            IF NEW.not_before <= instant
               OR NEW.not_before > instant + interval '86400 seconds' THEN
                RAISE EXCEPTION 'Invalid task retry time' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.not_before IS DISTINCT FROM OLD.not_before THEN
            RAISE EXCEPTION 'Task retry time is bound to retry transitions'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.action = 'claim' THEN
            IF NEW.progress_current <> 0 OR NEW.progress_total <> 0 THEN
                RAISE EXCEPTION 'Task claims must reset attempt progress'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.action = 'progress' THEN
            IF NEW.progress_current < OLD.progress_current
               OR NEW.progress_total < OLD.progress_total THEN
                RAISE EXCEPTION 'Task progress cannot move backwards'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.progress_current <> OLD.progress_current
              OR NEW.progress_total <> OLD.progress_total THEN
            RAISE EXCEPTION 'Task progress requires its own action'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_task_run_state_v1
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_task_run
    FOR EACH ROW EXECUTE FUNCTION stewardship_task_state_v1();

    CREATE FUNCTION stewardship_task_history_v1()
    RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
        INSERT INTO public.stewardship_task_event
            (id, created_at, actor_id, correlation_id, run_id, version,
             previous_state, state, action, attempt, fence, worker_id,
             heartbeat_at, lease_expires_at, not_before,
             progress_current, progress_total)
        VALUES (gen_random_uuid(), NEW.updated_at, NEW.actor_id, NEW.correlation_id,
                NEW.id, NEW.version,
                CASE WHEN TG_OP = 'INSERT' THEN '' ELSE OLD.state END,
                NEW.state, NEW.action, NEW.attempt, NEW.fence, NEW.worker_id,
                NEW.heartbeat_at, NEW.lease_expires_at, NEW.not_before,
                NEW.progress_current, NEW.progress_total);
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
                'task_' || NEW.action, NEW.id);
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_task_history_v1
    AFTER INSERT OR UPDATE ON public.stewardship_task_run
    FOR EACH ROW EXECUTE FUNCTION stewardship_task_history_v1();

    CREATE FUNCTION stewardship_task_event_binding_v1()
    RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp AS $$
    DECLARE
        run_record public.stewardship_task_run%ROWTYPE;
        previous public.stewardship_task_event%ROWTYPE;
    BEGIN
        SELECT * INTO run_record FROM public.stewardship_task_run WHERE id = NEW.run_id;
        SELECT * INTO previous FROM public.stewardship_task_event
        WHERE run_id = NEW.run_id ORDER BY version DESC LIMIT 1;
        IF run_record.id IS NULL OR NEW.version IS DISTINCT FROM run_record.version
           OR NEW.created_at IS DISTINCT FROM run_record.updated_at
           OR NEW.actor_id IS DISTINCT FROM run_record.actor_id
           OR NEW.correlation_id IS DISTINCT FROM run_record.correlation_id
           OR NEW.state IS DISTINCT FROM run_record.state
           OR NEW.action IS DISTINCT FROM run_record.action
           OR NEW.attempt IS DISTINCT FROM run_record.attempt
           OR NEW.fence IS DISTINCT FROM run_record.fence
           OR NEW.worker_id IS DISTINCT FROM run_record.worker_id
           OR NEW.heartbeat_at IS DISTINCT FROM run_record.heartbeat_at
           OR NEW.lease_expires_at IS DISTINCT FROM run_record.lease_expires_at
           OR NEW.not_before IS DISTINCT FROM run_record.not_before
           OR NEW.progress_current IS DISTINCT FROM run_record.progress_current
           OR NEW.progress_total IS DISTINCT FROM run_record.progress_total
           OR NEW.version <> COALESCE(previous.version, 0) + 1
           OR NEW.previous_state IS DISTINCT FROM COALESCE(previous.state, '') THEN
            RAISE EXCEPTION 'Invalid task transition evidence' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_task_event_binding_v1
    BEFORE INSERT ON public.stewardship_task_event
    FOR EACH ROW EXECUTE FUNCTION stewardship_task_event_binding_v1();
            """,
            reverse_sql="""
    LOCK TABLE public.stewardship_task_run IN ACCESS EXCLUSIVE MODE;
    DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.stewardship_task_run) THEN
            RAISE EXCEPTION 'Task history prevents schema downgrade'
                USING ERRCODE = '23514';
        END IF;
    END $$;
    DROP TRIGGER stewardship_task_event_binding_v1 ON public.stewardship_task_event;
    DROP FUNCTION stewardship_task_event_binding_v1();
    DROP TRIGGER stewardship_task_history_v1 ON public.stewardship_task_run;
    DROP FUNCTION stewardship_task_history_v1();
    DROP TRIGGER stewardship_task_run_state_v1 ON public.stewardship_task_run;
    DROP FUNCTION stewardship_task_state_v1();
            """,
        ),
    ]
