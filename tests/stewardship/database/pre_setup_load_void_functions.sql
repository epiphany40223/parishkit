-- Test fixture, frozen: the functions migration 0102 (a finished setup
-- load can be discarded, #1353) replaces whose earlier bodies are in no
-- frozen migration file, only in the fresh-install baseline that 0102
-- updated. Copied byte for byte from
-- src/parishkit/stewardship/schema/functions.sql at 1a585debf (the #528
-- head this change is stacked on), as CREATE OR REPLACE. The other
-- functions 0102 replaces come from frozen files: the attempt guard and
-- the mail and Slack live checks from 0055, the safe context from 0089.
-- test_setup_load_void_postgresql.py reinstalls these to prove 0102's
-- closing check refuses each earlier body;
-- test_schema_migration_files.py pins this file's digest.
SET LOCAL check_function_bodies = false;

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid(); kind text;
BEGIN
    IF TG_OP='UPDATE' AND NEW.state=OLD.state THEN RETURN NULL; END IF;
    kind=CASE NEW.state WHEN 'collecting' THEN
            CASE WHEN TG_OP='INSERT' THEN 'setup_started' ELSE 'setup_source_completed' END
        WHEN 'loading' THEN 'setup_source_started'
        WHEN 'frozen' THEN 'setup_frozen'
        WHEN 'completed' THEN 'setup_completed' WHEN 'expired' THEN 'setup_expired'
        ELSE NULL END;
    IF kind IS NULL THEN
        RAISE EXCEPTION 'Setup audit requires an implemented lifecycle state'
            USING ERRCODE='23514';
    END IF;
    INSERT INTO stewardship_audit_event(id,created_at,actor_id,correlation_id,
        event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,kind,NEW.id,'deployment');
    INSERT INTO stewardship_audit_context(id,created_at,actor_id,correlation_id,
        event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',
            CASE WHEN NEW.state='completed' THEN 'succeeded' WHEN NEW.state='expired' THEN 'cancelled' ELSE 'changed' END));
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."owner_id" IS DISTINCT FROM OLD."owner_id" OR NEW."base_id" IS DISTINCT FROM OLD."base_id" OR (OLD."source_task_id" IS NOT NULL AND NEW."source_task_id" IS DISTINCT FROM OLD."source_task_id") OR (OLD."expired_at" IS NOT NULL AND NEW."expired_at" IS DISTINCT FROM OLD."expired_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_disposable_snapshot_v1(identifier uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    SELECT current_user='pk_stewardship_worker' AND EXISTS (
        SELECT 1 FROM public.stewardship_source_lease lease
        JOIN public.stewardship_task_run task ON task.id=lease.owner_id
        JOIN public.stewardship_setup_attempt attempt
            ON attempt.id=task.domain_request_id
        JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=identifier
        JOIN public.stewardship_task_run original ON original.id=snapshot.task_id
        WHERE task.task_type='setup_source_cleanup' AND task.state='running'
            AND task.worker_id=lease.worker_id AND task.fence=lease.task_fence
            AND task.lease_expires_at>clock_timestamp()
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
            AND lease.fence>snapshot.source_fence
            AND (attempt.state='expired' OR (attempt.state='completed' AND EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared
                    ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready
                    ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent
                    ON intent.id=ready.intent_id AND intent.attempt_id=attempt.id
            ))) AND (
                (original.root_id=attempt.source_task_id
                 AND original.task_type='setup_source_load'
                 AND original.domain_request_id=attempt.id)
                OR EXISTS (
                    SELECT 1 FROM public.stewardship_setup_prepared prepared
                    JOIN public.stewardship_setup_readiness_binding readiness
                        ON readiness.id=prepared.readiness_id
                    JOIN public.stewardship_setup_config_intent intent
                        ON intent.id=readiness.intent_id
                        AND intent.attempt_id=attempt.id
                    JOIN public.stewardship_task_run root ON root.id=original.root_id
                        AND root.task_type='setup_finalize'
                        AND root.domain_request_id=prepared.id
                        AND root.idempotency_key=prepared.id::text
                        AND root.initiated_by_id=attempt.owner_id
                    WHERE original.task_type='setup_finalize'
                        AND original.domain_request_id=prepared.id
                        AND original.initiated_by_id=attempt.owner_id
                )
            )
            AND snapshot.state='rejected' AND snapshot.generation IS NULL
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_current current
                WHERE current.snapshot_id=snapshot.id)
            AND EXISTS (SELECT 1 FROM public.stewardship_system_configuration
                WHERE NOT restore_review_required)
    ) AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted);
$$;
