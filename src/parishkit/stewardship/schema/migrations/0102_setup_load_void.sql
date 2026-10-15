-- Frozen forward migration file 0102 (the repository-wide file sequence):
-- a finished setup load can be discarded and loaded again (#1353). This
-- file is installed by its Django migration in the accounts app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the replaced functions equal
-- the fresh-install baseline's (schema/functions.sql). A later change gets
-- its own numbered file. A fresh install runs the baseline, the earlier files
-- and then this; the baseline already carries every change below, so the
-- install ends in the same catalog as an upgraded database.
--
-- Until now a setup attempt was bound to one ParishSoft load forever. A
-- failed load (a wrong key or organization) left the attempt loading until
-- the two-hour watchdog or a cancel ended it, and setup had to start over.
-- Now a load that fails for good (also one a crashed worker left, which
-- recovery fails) returns the attempt to collecting with the
-- failed load still bound, and the owning live Admin can discard a finished
-- (succeeded or failed) load: the binding and renewal time are cleared, so a
-- new load can bind. The application discards one when the ParishSoft key or
-- organization changes, or for Load again after a failure or once a
-- successful load's staged result is no longer valid (its credential
-- version or fingerprint changed, its exchange was scrubbed, or its snapshot
-- or fences no longer match; the predicate review uses). Each discard is
-- audited as setup_source_voided with closed words for why and how the load
-- had ended, never the key. A discarded load's staging stays inert (every
-- later step binds to the current load) and the setup source cleanup now
-- disposes of every load root of the attempt when setup ends. Seven
-- functions change, each copied whole from its latest definition with only
-- this change:
--   stewardship_setup_attempt_guard_v1        the worker may also return a
--                                             failed load to collecting; the
--                                             owner may discard a finished one
--   stewardship_setup_attempt_audit_v1        records setup_source_voided
--                                             with its reason (reload only
--                                             for a failed or no longer valid
--                                             load), and a failed load's end
--                                             as outcome 'failed'
--   stewardship_setup_attempt_mutable_v1      source_task_id is no longer
--                                             write-once (the model's
--                                             write_once_fields drops it)
--   stewardship_setup_disposable_snapshot_v1  admits every load root of the
--                                             attempt, not only the bound one
--   stewardship_safe_context_v1               0089's body plus the action
--                                             keys void_reason and task_state
--   stewardship_setup_mail_live_v1 and        0055's bodies, requiring the
--   stewardship_setup_slack_live_v1           bound load to have succeeded,
--                                             since a failed one is now bound
--                                             while setup collects
-- None is SECURITY DEFINER; each keeps its identity, attributes, triggers and
-- grants. No table or function is created. One runtime grant changes, outside
-- this file: the Slack installer target's column grant on
-- stewardship_task_run gains state, for the Slack live check's join
-- (accounts/setup_notification_grants.py, applied by the grants command).
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE stamp timestamptz:=clock_timestamp(); login stewardship_portal_session%ROWTYPE;
        task stewardship_task_run%ROWTYPE; selected uuid; live boolean; reason text;
        previous text;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup history cannot be deleted' USING ERRCODE='23514';
    END IF;
    -- The end state of the load bound before this change, if any (#1353).
    IF TG_OP='UPDATE' AND OLD.source_task_id IS NOT NULL THEN
        SELECT state INTO previous FROM stewardship_task_run WHERE id=OLD.source_task_id;
    END IF;

    IF TG_OP='UPDATE' AND NEW.state='completed' THEN
        IF current_user<>'pk_stewardship_worker' OR OLD.state<>'frozen'
            OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
            OR (to_jsonb(NEW)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
            OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
                WHERE intent.attempt_id=OLD.id AND intent.attempt_version=OLD.version
                    AND prepared.id=public.stewardship_setup_completion_context_v1()
            ) THEN
            RAISE EXCEPTION 'Setup completion requires its atomic finalization receipt'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_worker' THEN
        IF TG_OP<>'UPDATE' OR OLD.state<>'loading' OR NEW.state<>'collecting'
           OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
           OR NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
           -- A failed load also returns setup to collecting (#1353), so the
           -- Admin can correct the ParishSoft connection and load again.
           OR (previous IS DISTINCT FROM 'failed' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange
                    ON exchange.id=result.exchange_id AND exchange.attempt_id=OLD.id
                    AND exchange.scrubbed_at IS NULL
                JOIN public.stewardship_task_run finished
                    ON finished.id=exchange.task_id AND finished.state='succeeded'
                    AND finished.root_id=OLD.source_task_id
                    AND finished.fence=exchange.task_fence
                JOIN public.stewardship_source_snapshot snapshot
                    ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
                    AND snapshot.task_id=finished.id
                    AND snapshot.source_fence=exchange.source_fence
            )) THEN
            RAISE EXCEPTION 'Worker may only acknowledge validated setup staging'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF current_user='pk_stewardship_scheduler' AND
       (TG_OP<>'UPDATE' OR NEW.state<>'expired' OR NEW.actor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Scheduler may only expire setup metadata'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup mutation requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT active_configuration_id INTO selected FROM stewardship_system_configuration
        WHERE mode='testing' AND NOT restore_review_required;
    SELECT s.id,s.principal_id,s.revoked_at,s.expires_at,s.last_activity_at
        INTO login.id,login.principal_id,login.revoked_at,
             login.expires_at,login.last_activity_at
        FROM stewardship_portal_session s
        JOIN stewardship_portal_user u ON u.id=s.principal_id AND NOT u.disabled
        JOIN stewardship_address_rule rule ON rule.configuration_id=selected
            AND rule.email=u.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE s.id=NEW.session_id AND s.principal_id=NEW.owner_id;
    live=login.id IS NOT NULL AND login.revoked_at IS NULL
        AND login.expires_at>stamp AND login.last_activity_at>stamp-interval '60 minutes';
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'collecting' OR NEW.source_task_id IS NOT NULL
           OR NEW.renewed_at IS NOT NULL OR NEW.version<>1 OR NOT live
           OR NEW.actor_id IS DISTINCT FROM NEW.owner_id
           OR selected IS DISTINCT FROM NEW.base_id
           OR NOT EXISTS (SELECT 1 FROM stewardship_configuration_version
                WHERE id=selected AND validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')) THEN
            RAISE EXCEPTION 'Setup requires the original live bootstrap Admin session'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at=stamp; NEW.updated_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('expired','completed') OR NEW.state='completed' THEN
        RAISE EXCEPTION 'Setup completion requires its configured-marker owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.source_task_id IS NOT NULL THEN
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.source_task_id;
        IF task.task_type IS DISTINCT FROM 'setup_source_load'
           OR task.domain_request_id IS DISTINCT FROM NEW.id
           OR task.initiated_by_id IS DISTINCT FROM NEW.owner_id
           OR task.created_at<NEW.created_at OR task.root_id<>task.id THEN
            RAISE EXCEPTION 'Setup source identity is not the original bound load'
                USING ERRCODE='23514';
        END IF;
    END IF;
    reason=CASE
        WHEN login.id IS NULL OR login.revoked_at IS NOT NULL THEN 'session'
        WHEN stamp>=login.expires_at THEN 'absolute'
        WHEN OLD.state='loading' AND stamp>=task.created_at+interval '2 hours' THEN 'watchdog'
        WHEN stamp>=login.last_activity_at+interval '60 minutes' THEN 'idle'
        ELSE NULL END;
    IF NEW.state='expired' THEN
        IF reason IS NULL THEN
            IF NOT live OR NEW.actor_id IS DISTINCT FROM NEW.owner_id THEN
                RAISE EXCEPTION 'Only the owning live Admin can cancel setup'
                    USING ERRCODE='23514';
            END IF;
            reason='cancelled';
        END IF;
        NEW.expiry_reason=reason; NEW.expired_at=stamp;
        IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id THEN
            RAISE EXCEPTION 'Setup expiry cannot change its retained bindings'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF reason IS NOT NULL OR NOT live THEN
        RAISE EXCEPTION 'Expired setup cannot accept later work' USING ERRCODE='23514';
    END IF;
    IF OLD.state<>'frozen' AND (selected IS DISTINCT FROM NEW.base_id
        OR NEW.actor_id IS DISTINCT FROM NEW.owner_id) THEN
        RAISE EXCEPTION 'Setup no longer has its original authorized base'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='frozen' OR (
        (OLD.state=NEW.state AND NEW.state IN ('collecting','loading'))
        OR (OLD.state='collecting' AND NEW.state='loading' AND OLD.source_task_id IS NULL)
        -- Only the worker returns a failed load (#1353), in its failure transaction.
        OR (OLD.state='loading' AND NEW.state='collecting' AND (task.state='succeeded'
            OR (task.state='failed' AND current_user='pk_stewardship_worker')))
        OR (OLD.state='collecting' AND NEW.state='frozen' AND task.state='succeeded')) IS NOT TRUE THEN
        RAISE EXCEPTION 'Setup transition has no completed owning work'
            USING ERRCODE='23514';
    END IF;
    -- Discarding a finished load (#1353): the owning live Admin (checked
    -- above) clears the binding of a load that succeeded or failed, never of
    -- one still running, and its renewal time with it, so the next load
    -- starts a fresh watchdog and renewal clock. The audit trigger records
    -- why. Only an unbound collecting attempt can bind a new load.
    IF OLD.state='collecting' AND NEW.state='collecting'
       AND OLD.source_task_id IS NOT NULL AND NEW.source_task_id IS NULL THEN
        IF previous IS NULL OR previous NOT IN ('succeeded','failed')
           OR NEW.renewed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Only a finished setup load can be discarded'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
       AND NOT (OLD.state='collecting' AND NEW.state='loading') THEN
        RAISE EXCEPTION 'Setup load binding requires its loading transition'
            USING ERRCODE='23514';
    END IF;
    IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at THEN
        IF OLD.state<>'loading' OR NEW.state<>'loading' OR task.state<>'running'
           OR task.lease_expires_at<=stamp OR task.heartbeat_at IS NULL
           OR task.heartbeat_at<=stamp-interval '90 seconds'
           OR stamp<coalesce(OLD.renewed_at,task.created_at)+interval '5 minutes'
           OR stamp>=task.created_at+interval '2 hours'
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease lease
               WHERE lease.owner_id=task.id AND lease.task_fence=task.fence
                 AND lease.worker_id=task.worker_id AND lease.expires_at>stamp
                 AND lease.heartbeat_at>stamp-interval '90 seconds') THEN
            RAISE EXCEPTION 'Setup renewal requires live correlated source ownership'
                USING ERRCODE='23514';
        END IF;
        NEW.renewed_at=stamp;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid(); kind text; why text; previous text;
BEGIN
    -- A discarded load (#1353): the caller names why in the transaction-local
    -- setting stewardship.setup_void_reason, which is consumed here so each
    -- discard names its own. The context never holds the key or organization.
    IF TG_OP='UPDATE' AND OLD.source_task_id IS NOT NULL AND NEW.source_task_id IS NULL THEN
        why=current_setting('stewardship.setup_void_reason', true);
        SELECT state INTO previous FROM stewardship_task_run WHERE id=OLD.source_task_id;
        IF why IS NULL OR why NOT IN ('key_changed','organization_changed','reload')
           OR previous IS NULL OR previous NOT IN ('succeeded','failed') THEN
            RAISE EXCEPTION 'A discarded setup load requires its closed reason'
                USING ERRCODE='23514';
        END IF;
        -- Load again needs a load that failed, or one that succeeded but whose
        -- staged result is no longer valid (accounts.setup_drafts.staged_load,
        -- the predicate review uses): its credential version or fingerprint
        -- changed, its exchange was scrubbed, or its snapshot or fences no
        -- longer match.
        IF why='reload' AND previous='succeeded' AND EXISTS (
            SELECT 1 FROM stewardship_setup_source_result result
            JOIN stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=NEW.id
                AND exchange.scrubbed_at IS NULL
            JOIN stewardship_task_run task ON task.id=exchange.task_id
                AND task.root_id=OLD.source_task_id AND task.state='succeeded'
                AND task.fence=exchange.task_fence
            JOIN stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
                AND snapshot.task_id=exchange.task_id
                AND snapshot.source_fence=exchange.source_fence) THEN
            RAISE EXCEPTION 'A still-valid setup load cannot be loaded again'
                USING ERRCODE='23514';
        END IF;
        PERFORM set_config('stewardship.setup_void_reason', '', true);
        INSERT INTO stewardship_audit_event(id,created_at,actor_id,correlation_id,
            event_type,subject_id,ownership_scope)
        VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
            'setup_source_voided',NEW.id,'deployment');
        INSERT INTO stewardship_audit_context(id,created_at,actor_id,correlation_id,
            event_id,actor_kind,schema,context)
        VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
            'portal_user','action',jsonb_build_object('version',NEW.version,
            'outcome','cancelled','void_reason',why,'task_state',previous));
        RETURN NULL;
    END IF;
    IF TG_OP='UPDATE' AND NEW.state=OLD.state THEN RETURN NULL; END IF;
    -- A load that ended returns setup to collecting either way; its outcome
    -- says whether the load succeeded or failed (#1353).
    IF TG_OP='UPDATE' AND OLD.state='loading' AND NEW.state='collecting' THEN
        SELECT state INTO previous FROM stewardship_task_run WHERE id=NEW.source_task_id;
    END IF;
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
            CASE WHEN NEW.state='completed' THEN 'succeeded' WHEN NEW.state='expired' THEN 'cancelled'
                WHEN previous='failed' THEN 'failed' ELSE 'changed' END));
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."owner_id" IS DISTINCT FROM OLD."owner_id" OR NEW."base_id" IS DISTINCT FROM OLD."base_id" OR (OLD."expired_at" IS NOT NULL AND NEW."expired_at" IS DISTINCT FROM OLD."expired_at") THEN
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
                -- Every load root of this attempt, also one discarded
                -- before setup ended (#1353), not only the bound one.
                (original.task_type='setup_source_load'
                 AND original.domain_request_id=attempt.id
                 AND EXISTS (
                     SELECT 1 FROM public.stewardship_task_run root
                     WHERE root.id=original.root_id AND root.root_id=root.id
                         AND root.task_type='setup_source_load'
                         AND root.domain_request_id=attempt.id
                         AND root.initiated_by_id=attempt.owner_id))
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

CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
        ministry jsonb; previous_ministry bigint;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome','reason']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'member_source' THEN ARRAY['family_duid','member_duid','field']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count',
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','directory_reach','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint',
            'report_mode','report_filter','talent_option_id','snapshot_id',
            'report_sort','directory_data_check','directory_response_columns',
            'archive_year','archive_days','archive_family_rows','void_reason','task_state']
        WHEN 'boundary' THEN ARRAY['occurrence_id','kind','intended_unix_microseconds','actual_unix_microseconds','lag_microseconds','before_state','after_state']
        WHEN 'schedule' THEN ARRAY['definition_id','previous_revision_id','selected_revision_id','cancelled_messages','skipped_occurrences','failed_occurrences','delivered_slots']
        WHEN 'timeout' THEN ARRAY['task_id','task_type','attempt','limit_seconds','elapsed_seconds','what','helper','count','outcome']
        -- What made scheduled work late (#634): the task type, how many tasks
        -- and how late against which limit, or the Family send that stalled
        -- or overran, with its counts, how long since its last progress and
        -- how many other tasks were late in the same check.
        WHEN 'due_work' THEN ARRAY['task_type','count','lag_seconds','limit_seconds','definition_id','revision_id',
            'remaining_count','done_count','stall_seconds','elapsed_seconds','other_late_count',
            'occurrence_id','task_id']
        -- What failed and what happens next (#633): a closed word for what
        -- failed, the exception's category, the task, message and attempt,
        -- a closed provider reason or HTTP status, and whether it retries.
        WHEN 'failure' THEN ARRAY['failure','failure_kind','task_id','task_type','message_id','version',
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome','command']
        -- An operational incident that ended (#633): the incident, its kind,
        -- the entry that opened it, how long it lasted and how often it was seen.
        WHEN 'recovery' THEN ARRAY['incident_id','incident_kind','log_id','elapsed_seconds','count']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        IF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('no_deliverable_recipient',
                'smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic','smtp_delivery_unknown',
                'preparation_failed','slack_not_sent','slack_delivery_unknown') THEN RETURN false; END IF;
        -- What failed (#633): a closed word (audit.schemas.FAILURES).
        ELSIF key='failure' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('organization_mismatch','destructive_change',
                'shifted_scan','invalid_payload','incomplete_collection','invalid_response','lease_unavailable',
                'configuration_activating','configuration_busy','scope_changed','credential_unreadable',
                'credential_changed','provider_status','provider_timeout','provider_unreachable',
                'source_configuration','organization_changed','source_health_check','mail_health_check',
                'due_work_health_check','backup_health_check','export_cleanup','fact_verification',
                'source_retention','family_engagement','alert_mail','security_mail','slack_alert',
                'smtp_systemic','smtp_unavailable','web_health_check','web_unresponsive',
                'admin_command','admin_command_outcome_unknown','digest_funnel_chart',
                'digest_activity_chart','digest_comparison_chart',
                'retry_refusal') THEN RETURN false; END IF;
        -- An Admin command-line command (#617): its catalog name, lower-case
        -- words such as 'export create' (Python checks the catalog itself).
        ELSIF key='command' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
        -- A failure's category and an incident's kind: identifier words whose
        -- closed sets Python owns (observability.FailureKind, IncidentKind).
        ELSIF key IN ('failure_kind','incident_kind') THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('start','close') THEN RETURN false; END IF;
        ELSIF key IN ('before_state','after_state') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('draft','scheduled','active','closed','archived',
                'purging','purge_cleanup_failed','purged') THEN RETURN false; END IF;
        ELSIF key='field' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname',
                'maiden_name','birth_date','gender','email','home_phone',
                'mobile_phone','work_phone','marital_status','language','death_date'
            ) THEN RETURN false; END IF;
        ELSIF key IN ('family_duid','member_duid','ministry_duid') THEN
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,10}$' THEN RETURN false; END IF;
            IF text_value::numeric NOT BETWEEN 1 AND 2147483647 THEN RETURN false; END IF;
        ELSIF key IN ('ministry_duids','previous_ministry_duids','added_ministry_duids','removed_ministry_duids') THEN
            IF jsonb_typeof(value)<>'array' THEN RETURN false; END IF;
            previous_ministry:=0;
            FOR ministry IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(ministry)<>'number' OR ministry#>>'{}'!~'^[0-9]{1,10}$'
                THEN RETURN false; END IF;
                IF (ministry#>>'{}')::bigint NOT BETWEEN previous_ministry+1 AND 2147483647
                THEN RETURN false; END IF;
                previous_ministry:=(ministry#>>'{}')::bigint;
            END LOOP;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        -- Work stopped by a time limit (#293): the task type and the limit.
        ELSIF key='task_type' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='what' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('read_guard','lease','retention_budget','drive_copy_budget',
                'drive_retry_budget','drive_request','drive_probe_wait',
                'statement_timeout','lock_timeout','transaction_timeout','mail_helper','source_helper','provider_check',
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe','source_load_budget',
                'archive_check','digest_chart') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker',
                'archive_check_worker','chart_render_worker') THEN RETURN false; END IF;
        -- An Administrator's decision on a suspended Chairperson seed: a closed
        -- word, and the reason entered for it, bounded text refused when it
        -- carries an address-like token, since this context holds no
        -- personal data.
        ELSIF key='decision' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('keep_role','restore','remove') THEN RETURN false; END IF;
        -- A discarded setup load (#1353): why it was discarded and how the
        -- load had ended (audit.schemas.VOID_REASONS, VOIDED_TASK_STATES).
        ELSIF key='void_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('key_changed','organization_changed','reload') THEN RETURN false; END IF;
        ELSIF key='task_state' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('succeeded','failed') THEN RETURN false; END IF;
        ELSIF key='review_reason' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value) NOT BETWEEN 1 AND 500
               OR position('@' in text_value)>0 THEN RETURN false; END IF;
        -- A hosted file (#346): its placeholder name and its stored type.
        ELSIF key IN ('file_slug','previous_file_slug') THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z0-9]+(-[a-z0-9]+)*$' THEN RETURN false; END IF;
        ELSIF key='file_kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('pdf','docx','xlsx','pptx','png','jpeg') THEN RETURN false; END IF;
        -- An on-request report page or download (#556): the system mode it
        -- showed and its closed filter choice (audit.schemas.REPORT_FILTERS).
        ELSIF key='report_mode' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('production','testing') THEN RETURN false; END IF;
        ELSIF key='report_filter' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('all','invited','uninvited','progressed',
                'opened','followed','unfollowed','mailing-name','envelope','any','cannot_serve',
                'cannot_attend','option') THEN RETURN false; END IF;
        -- A response list's sort order (#851): its closed sort token, a column
        -- key ascending or '-key' descending (audit.schemas.REPORT_SORTS).
        ELSIF key='report_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('family','-family',
                'duid','-duid','envelope','-envelope','submitted','-submitted',
                'submissions','-submissions','opened','-opened','progressed','-progressed',
                'invited','-invited','link','-link','last','-last','mailing','-mailing',
                'problem','-problem','staff_entered','-staff_entered') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational','directory_response_columns') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key='directory_phone' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        -- The directory's Response filter (#933): its closed choices, plus the
        -- older yes and no that earlier entries recorded.
        ELSIF key='directory_response' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no','submitted',
                'more-than-once','started','progressed','opened-only','never-opened',
                'link-followed','link-not-followed','not-invited','not-submitted') THEN RETURN false; END IF;
        -- The directory's ParishSoft data to check filter (#933).
        ELSIF key='directory_data_check' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','anything','mailing-name','envelope') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid','duid_desc',
                'invited','invited_desc','link','link_desc','opened','opened_desc',
                'progressed','progressed_desc','submitted','submitted_desc','last','last_desc',
                'submissions','submissions_desc') THEN RETURN false; END IF;
        -- How campaign mail can reach the listed Families (#388 L1).
        ELSIF key='directory_reach' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','email','mail','neither') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_mail_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        -- Only a load that succeeded (#1353): a failed one is bound while
        -- setup collects again.
        JOIN public.stewardship_task_run original ON original.id=attempt.source_task_id
            AND original.state='succeeded'
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='google_workspace' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section mail
            ON mail.attempt_id=attempt.id AND mail.step='mail'
            AND mail.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        WHERE attempt.id=$1 AND attempt.version=$2 AND attempt.state='collecting'
            AND mail.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'sender',candidate.settings->>'sender',
                'reply_to',candidate.settings->>'reply_to',
                'delegated_email',candidate.settings->>'delegated_email'))),'hex')
            AND testing.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'testing_recipient',candidate.settings->>'recipient'))),'hex')
    );
$_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_slack_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        -- Only a load that succeeded (#1353): a failed one is bound while
        -- setup collects again.
        JOIN public.stewardship_task_run original ON original.id=attempt.source_task_id
            AND original.state='succeeded'
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='slack' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL
        WHERE attempt.id=$1 AND attempt.version=$2 AND attempt.state='collecting'
            AND slack.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'enabled',true,'channel_id',candidate.settings->>'channel_id'))),'hex')
    );
$_$;

-- Refuse to commit unless every change above is installed, so an upgrade
-- cannot report success with one lost. No temporary objects: the migration
-- login has no TEMP grant.
DO $check$
BEGIN
    -- Each replaced function carries its new phrase, runs as its caller and
    -- keeps its pinned search_path (the generated mutable guard has none).
    IF (SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
        JOIN (VALUES
            ('stewardship_setup_attempt_guard_v1', '',
             'Only a finished setup load can be discarded', true),
            ('stewardship_setup_attempt_audit_v1', '',
             '''setup_source_voided''', true),
            ('stewardship_setup_attempt_mutable_v1', '',
             'OLD."expired_at" IS NOT NULL', false),
            ('stewardship_setup_disposable_snapshot_v1', 'identifier uuid',
             'root.root_id=root.id', true),
            ('stewardship_safe_context_v1', 'schema_name text, payload jsonb',
             'ELSIF key=''void_reason'' THEN', true),
            ('stewardship_setup_mail_live_v1',
             'attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text',
             'AND original.state=''succeeded''', true),
            ('stewardship_setup_slack_live_v1',
             'attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text',
             'AND original.state=''succeeded''', true)
        ) expected(name, arguments, marker, pinned)
          ON p.proname=expected.name
         AND pg_get_function_identity_arguments(p.oid)=expected.arguments
         AND strpos(p.prosrc, expected.marker)>0
         AND NOT p.prosecdef
         AND (NOT expected.pinned
              OR p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']))<>7 THEN
        RAISE EXCEPTION 'Migration 0102: a replaced function is not installed as declared';
    END IF;
    -- The binding is no longer write-once, the guard still lets only a
    -- running load's worker return setup to collecting after a failure, and
    -- the audit refuses Load again of a still-valid load.
    IF EXISTS (SELECT 1 FROM pg_proc
               WHERE oid='public.stewardship_setup_attempt_mutable_v1()'::regprocedure
                 AND strpos(prosrc, 'source_task_id')>0)
       OR NOT EXISTS (SELECT 1 FROM pg_proc
               WHERE oid='public.stewardship_setup_attempt_guard_v1()'::regprocedure
                 AND strpos(prosrc, 'previous IS DISTINCT FROM ''failed'' AND NOT EXISTS')>0)
       OR NOT EXISTS (SELECT 1 FROM pg_proc
               WHERE oid='public.stewardship_setup_attempt_audit_v1()'::regprocedure
                 AND strpos(prosrc, 'A still-valid setup load cannot be loaded again')>0
                 AND strpos(prosrc, 'credential.version=exchange.credential_version')>0) THEN
        RAISE EXCEPTION 'Migration 0102: the setup load binding guards are not installed as declared';
    END IF;
    -- The three setup attempt triggers still run the replaced functions.
    IF (SELECT count(*) FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_setup_attempt'::regclass
          AND t.tgenabled='O' AND NOT t.tgisinternal
          AND (t.tgname, t.tgfoid) IN (
              ('stewardship_setup_attempt_admission',
               'public.stewardship_setup_attempt_guard_v1()'::regprocedure),
              ('stewardship_setup_attempt_audit',
               'public.stewardship_setup_attempt_audit_v1()'::regprocedure),
              ('stewardship_setup_attempt_mutable_guard_v1',
               'public.stewardship_setup_attempt_mutable_v1()'::regprocedure)))<>3 THEN
        RAISE EXCEPTION 'Migration 0102: a setup attempt trigger lost its function';
    END IF;
    -- The context admits a discard's closed words, keeps 0089's failure word
    -- and still refuses an unknown reason or state.
    IF NOT public.stewardship_safe_context_v1(
           'action', '{"version": 4, "outcome": "cancelled",
                       "void_reason": "organization_changed", "task_state": "succeeded"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "retry_refusal"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"void_reason": "key_shown"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"task_state": "running"}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit a discarded setup load as declared';
    END IF;
END
$check$;
