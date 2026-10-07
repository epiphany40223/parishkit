-- Frozen forward migration file 0008 (the repository-wide file sequence;
-- Django's stewardship_jobs.0008): every WARNING-or-above operational log
-- entry says what went wrong, and recovery is logged (#633). This file is
-- installed by jobs/migrations/0008_log_detail.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copies of the replaced functions still equal the
-- fresh-install baseline's (schema/functions.sql, mail_health.sql,
-- operational_dispatch.sql, operational_slack.sql, security_dispatch.sql and
-- due_work_health.sql).
-- It follows 0007 (#622) and keeps that file's service_status_failed event.
-- A fresh install runs the baseline, 0002 to 0007 and
-- then this; the baseline files already carry the replaced bodies and the
-- widened constraint, so the install ends in the same catalog as an upgraded
-- database.
--
-- The operational context allowlist (stewardship_safe_context_v1) gains two
-- closed schemas: 'failure' (what failed, as a closed word, the exception's
-- category, the task, message and attempt, a closed provider reason or HTTP
-- status, and whether it will be retried) and 'recovery' (the incident that
-- ended, its kind, the entry that opened it, how long it lasted and how often
-- it was seen). 'due_work' also names a late campaign boundary's occurrence
-- and task, and 'reason' admits the closed outbox and Slack results. Counts,
-- durations, closed words and ids only: no names, addresses or text.
--
-- The due-work checkpoint trigger falls back to the per-task limit rather
-- than an empty context. The SQL producers that logged an empty context now
-- say what failed: an
-- Administrator alert or security notice that could not be delivered or
-- prepared, a Slack alert that was not sent, and the mail provider failing.
-- When an operational incident resolves, a new trigger writes an INFO
-- incident_recovered entry that shares the correlation of the entry that
-- opened the incident, so System logs links the two; the operational event
-- allowlist gains that one event. No table or column changes. Reversing
-- needs its own forward migration that restores the old bodies.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The one new event, written only by the recovery trigger below.
ALTER TABLE public.stewardship_operational_log DROP CONSTRAINT operational_event_safe;
ALTER TABLE public.stewardship_operational_log ADD CONSTRAINT operational_event_safe CHECK (event IN (
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed','service_status_failed','incident_recovered'));

-- The allowlist, exactly as the fresh-install functions.sql defines it.
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
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint']
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
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome']
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
                'smtp_systemic','smtp_unavailable') THEN RETURN false; END IF;
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
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker') THEN RETURN false; END IF;
        -- An Administrator's decision on a suspended Chairperson seed: a closed
        -- word, and the reason entered for it, bounded text refused when it
        -- carries an address-like token, since this context holds no
        -- personal data.
        ELSIF key='decision' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('keep_role','restore','remove') THEN RETURN false; END IF;
        ELSIF key='review_reason' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value) NOT BETWEEN 1 AND 500
               OR position('@' in text_value)>0 THEN RETURN false; END IF;
        -- A hosted file (#346): its placeholder name and its stored type.
        ELSIF key IN ('file_slug','previous_file_slug') THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z0-9]+(-[a-z0-9]+)*$' THEN RETURN false; END IF;
        ELSIF key='file_kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('pdf','docx','xlsx','pptx','png','jpeg') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key IN ('directory_phone','directory_response') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

-- The producers that said nothing, as their fresh-install files define them.
CREATE OR REPLACE FUNCTION public.stewardship_mail_health_result_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    result jsonb; previous_result jsonb; candidate record;
    message public.stewardship_outbox_message%ROWTYPE;
    unavailable integer:=0; critical boolean:=false;
BEGIN
    IF NEW.previous_state<>'submitting' OR NEW.submitted_at IS NULL
       OR NEW.reason NOT IN ('smtp_accepted','smtp_transient','smtp_unavailable',
           'smtp_permanent','smtp_delivery_unknown','smtp_systemic') THEN RETURN NULL; END IF;
    SELECT * INTO message FROM public.stewardship_outbox_message WHERE id=NEW.message_id;
    -- Operational sends can break a streak, but can never originate an alert.
    IF message.purpose='operational' THEN RETURN NULL; END IF;
    result:=public.stewardship_family_smtp_result_v1(NEW.id);
    IF result IS NULL THEN RETURN NULL; END IF;
    critical:=result->>'health'='systemic';
    IF result->>'health'='unavailable' THEN
        IF NEW.provider_identity='' THEN RETURN NULL; END IF;
        -- Seek directly into this provider's ordered outcomes, including after
        -- a provider change. LIMIT also bounds complete evidence validation.
        FOR candidate IN
            SELECT e.id FROM public.stewardship_outbox_event e
            WHERE e.provider_identity=NEW.provider_identity
              AND e.previous_state='submitting' AND e.submitted_at IS NOT NULL
              AND e.reason IN ('smtp_accepted','smtp_transient','smtp_unavailable',
                  'smtp_permanent','smtp_delivery_unknown','smtp_systemic')
              AND (e.evidence_note LIKE '{"health":"healthy",%'
                   OR e.evidence_note LIKE '{"health":"unavailable",%'
                   OR e.evidence_note LIKE '{"health":"systemic",%')
            ORDER BY e.created_at DESC,e.id DESC LIMIT 3
        LOOP
            previous_result:=public.stewardship_family_smtp_result_v1(candidate.id);
            -- Unverifiable historical evidence breaks continuity; it must not
            -- roll back a newly settled definitive provider outcome.
            IF previous_result IS NULL OR previous_result->>'health'<>'unavailable' THEN
                RETURN NULL;
            END IF;
            unavailable:=unavailable+1;
        END LOOP;
        critical:=unavailable=3;
    END IF;
    IF critical THEN
        -- What failed (#633): one systemic provider answer, or three
        -- unavailable answers in a row, with this message, its task and
        -- its closed provider reason.
        INSERT INTO public.stewardship_operational_log
            (id,actor_id,correlation_id,level,event,schema,context)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'CRITICAL',
            'mail_provider_failed','failure',jsonb_strip_nulls(jsonb_build_object(
                'failure',CASE WHEN result->>'health'='systemic'
                    THEN 'smtp_systemic' ELSE 'smtp_unavailable' END,
                'count',CASE WHEN result->>'health'='systemic' THEN 1 ELSE unavailable END,
                'task_id',message.task_id,'message_id',NEW.message_id,
                'reason',CASE WHEN NEW.reason<>'smtp_accepted' THEN NEW.reason END)));
    END IF;
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION public.stewardship_ops_delivery_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE failed boolean:=false; detail jsonb;
BEGIN
    IF TG_TABLE_NAME='stewardship_outbox_event' THEN
      IF NOT ((NEW.state IN ('retry_wait','permanent_failure') AND NEW.reason LIKE 'smtp_%')
        OR (NEW.state='cancelled' AND NEW.reason='preparation_failed'))
        THEN RETURN NULL; END IF;
      failed:=EXISTS(SELECT 1 FROM stewardship_outbox_message
        WHERE id=NEW.message_id AND purpose='operational');
      detail:=jsonb_build_object('failure','alert_mail','message_id',NEW.message_id,
        'outcome',CASE WHEN NEW.state='retry_wait' THEN 'retry' ELSE 'failed' END);
      IF NEW.reason IN ('smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic',
          'smtp_delivery_unknown','preparation_failed') THEN
        detail:=detail||jsonb_build_object('reason',NEW.reason);
      END IF;
    ELSIF TG_TABLE_NAME='stewardship_task_event' THEN
      -- Most Task events are claims/progress/heartbeats. Do not plan or execute
      -- a delivery lookup for each of those unrelated high-volume events.
      IF NEW.action NOT IN ('permanent_failure','recovery_fail') THEN RETURN NULL; END IF;
      failed:=EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
          AND task_type='operational_prepare') OR EXISTS(SELECT 1 FROM stewardship_task_run t
          JOIN stewardship_outbox_message m ON m.id=t.domain_request_id AND m.task_id=t.root_id
          WHERE t.id=NEW.run_id AND t.task_type='outbox_delivery' AND m.purpose='operational'
            AND m.state IN ('pending','retry_wait'));
      detail:=jsonb_strip_nulls(jsonb_build_object('failure','alert_mail','task_id',NEW.run_id,
        'task_type',(SELECT task_type FROM stewardship_task_run WHERE id=NEW.run_id),
        'attempt',NEW.attempt,'outcome','failed'));
    END IF;
    IF failed THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed','ERROR','failure',detail);
    END IF;
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION public.stewardship_security_delivery_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE failed boolean:=false; detail jsonb;
BEGIN
    IF TG_TABLE_NAME='stewardship_outbox_event' THEN
      IF NOT ((NEW.state IN ('retry_wait','permanent_failure') AND NEW.reason LIKE 'smtp_%')
        OR (NEW.state='cancelled' AND NEW.reason='preparation_failed'))
        THEN RETURN NULL; END IF;
      failed:=EXISTS(SELECT 1 FROM stewardship_outbox_message
        WHERE id=NEW.message_id AND purpose='security_event');
      detail:=jsonb_build_object('failure','security_mail','message_id',NEW.message_id,
        'outcome',CASE WHEN NEW.state='retry_wait' THEN 'retry' ELSE 'failed' END);
      IF NEW.reason IN ('smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic',
          'smtp_delivery_unknown','preparation_failed') THEN
        detail:=detail||jsonb_build_object('reason',NEW.reason);
      END IF;
    ELSIF TG_TABLE_NAME='stewardship_task_event' THEN
      IF NEW.action NOT IN ('permanent_failure','recovery_fail') THEN RETURN NULL; END IF;
      failed:=EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.run_id
          AND task_type='security_prepare') OR EXISTS(SELECT 1 FROM stewardship_task_run t
          JOIN stewardship_outbox_message m ON m.id=t.domain_request_id AND m.task_id=t.root_id
          WHERE t.id=NEW.run_id AND t.task_type='outbox_delivery' AND m.purpose='security_event'
            AND m.state IN ('pending','retry_wait'));
      detail:=jsonb_strip_nulls(jsonb_build_object('failure','security_mail','task_id',NEW.run_id,
        'task_type',(SELECT task_type FROM stewardship_task_run WHERE id=NEW.run_id),
        'attempt',NEW.attempt,'outcome','failed'));
    END IF;
    IF failed THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed','ERROR','failure',detail);
    END IF;
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION public.stewardship_ops_slack_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.outcome<>'accepted' THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed',
        CASE WHEN NEW.outcome='delivery_unknown' THEN 'WARNING' ELSE 'ERROR' END,
        'failure',jsonb_strip_nulls(jsonb_build_object('failure','slack_alert',
          'task_id',(SELECT run_id FROM stewardship_ops_slack_attempt WHERE id=NEW.attempt_id),
          'reason',CASE WHEN NEW.outcome='delivery_unknown' THEN 'slack_delivery_unknown'
              ELSE 'slack_not_sent' END,
          'outcome','failed')));
    END IF;
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION public.stewardship_ops_slack_task_error_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Failures before provider submission have no result row to log them.
    -- Alert failure stays ERROR, never recursively creates another CRITICAL.
    -- It names the task, its type and its attempt (#633).
    IF NEW.task_type='operational_slack' AND NEW.state='failed'
      AND OLD.state<>'failed' AND NOT EXISTS(
        SELECT 1 FROM stewardship_ops_slack_attempt a
        JOIN stewardship_ops_slack_result r ON r.attempt_id=a.id
        WHERE a.run_id=NEW.id AND r.outcome<>'accepted') THEN
      INSERT INTO stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
      VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'task_failed',
        'ERROR','failure',jsonb_build_object('failure','slack_alert','task_id',NEW.id,
          'task_type',NEW.task_type,'attempt',NEW.attempt,'outcome','failed'));
    END IF;
    RETURN NULL;
END $$;

-- The due-work checkpoint trigger, as the fresh-install due_work_health.sql
-- defines it: a missing, empty or refused scan context no longer leaves the
-- CRITICAL due_work_lag entry empty; it says at least the per-task limit.
CREATE OR REPLACE FUNCTION public.stewardship_due_work_health_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE instant timestamptz; detail jsonb;
BEGIN
    IF TG_OP='DELETE' OR NOT EXISTS(SELECT 1 FROM pg_locks
        WHERE locktype='advisory' AND pid=pg_backend_pid()
          AND classid=736229 AND objid=1 AND objsubid=2 AND granted) THEN
        RAISE EXCEPTION 'Due-work observations require the owned scheduler session' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736246,1);
    instant:=clock_timestamp();
    IF NOT isfinite(NEW.scan_started_at) OR NEW.scan_started_at>instant THEN
        RAISE EXCEPTION 'Due-work observation time is invalid' USING ERRCODE='23514';
    END IF;
    IF NEW.signal='clear' AND instant-NEW.scan_started_at>interval '90 seconds' THEN
        NEW.signal:='unknown';
    END IF;
    IF TG_OP='UPDATE' AND NEW.signal='unknown' AND OLD.signal='late' THEN
        -- An inconclusive prefix/interruption invalidates positive proof, not
        -- previous negative evidence. Do not renew its timestamp or emit a log:
        -- only another real late observation may continue/escalate that window.
        RETURN NULL;
    END IF;
    NEW.observed_at:=instant;
    NEW.late_since:=NULL;
    NEW.clear_since:=NULL;
    NEW.last_failure_at:=OLD.last_failure_at;
    IF NEW.signal='late' THEN
        NEW.late_since:=public.stewardship_due_work_since_v1(OLD.signal,OLD.observed_at,
            OLD.scan_started_at,OLD.late_since,NEW.signal,NEW.scan_started_at,instant);
    ELSIF NEW.signal='clear' THEN
        NEW.clear_since:=public.stewardship_due_work_since_v1(OLD.signal,OLD.observed_at,
            OLD.scan_started_at,OLD.clear_since,NEW.signal,NEW.scan_started_at,instant);
    END IF;
    IF public.stewardship_due_work_critical_v1(NEW.signal,NEW.late_since,
        NEW.last_failure_at,NEW.escalation_seconds,instant) THEN
        -- Intake can be unavailable together with the general worker. Retain
        -- immutable negative evidence here before any later scan can recover.
        -- The scheduler's scan says what was late (#634) in a setting local
        -- to this transaction. A missing, empty or refused context is never
        -- allowed to block the failure record itself; the entry then says
        -- at least the per-task limit it broke (#633, mirroring
        -- jobs.due_work_health.FALLBACK_CONTEXT).
        BEGIN
            detail:=NULLIF(current_setting('parishkit.due_work_context',true),'')::jsonb;
        EXCEPTION WHEN invalid_text_representation THEN
            detail:=NULL;
        END;
        IF detail IS NULL OR detail=jsonb_build_object()
           OR NOT public.stewardship_safe_context_v1('due_work',detail) THEN
            detail:=jsonb_build_object('limit_seconds',90);
        END IF;
        INSERT INTO public.stewardship_operational_log
            (id,correlation_id,level,event,schema,context)
        VALUES(gen_random_uuid(),gen_random_uuid(),'CRITICAL','due_work_lag','due_work',detail);
        NEW.last_failure_at:=instant;
    END IF;
    RETURN NEW;
END $$;

-- Recovery is logged: when an operational incident resolves, one INFO entry
-- names it, its kind, how long it lasted and how often it was observed, and
-- the CRITICAL entry that opened it (its first receipt), whose correlation it
-- shares. An incident opened without a log entry (backup, sign-in and
-- automation health) keeps its own correlation and has no log_id. The
-- automation notices (operational_content.AUTOMATION_KINDS) get no entry:
-- they end after an hour without events, which is not a recovery, and the
-- automation notices on the dashboard already say what happened. Definer
-- rights, like the notice trigger, since the logins that resolve incidents
-- have no log INSERT of their own to rely on.
CREATE FUNCTION public.stewardship_ops_incident_recovered_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE opened_id uuid; opened_correlation uuid;
BEGIN
    SELECT l.id,l.correlation_id INTO opened_id,opened_correlation
        FROM public.stewardship_ops_log_receipt r
        JOIN public.stewardship_operational_log l ON l.id=r.log_id
        WHERE r.incident_id=NEW.id ORDER BY l.created_at,l.id LIMIT 1;
    INSERT INTO public.stewardship_operational_log
        (id,actor_id,correlation_id,level,event,schema,context)
    VALUES(gen_random_uuid(),NEW.actor_id,COALESCE(opened_correlation,NEW.correlation_id),
        'INFO','incident_recovered','recovery',jsonb_strip_nulls(jsonb_build_object(
            'incident_id',NEW.id,'incident_kind',NEW.kind,'log_id',opened_id,
            'elapsed_seconds',GREATEST(0,floor(extract(epoch FROM NEW.resolved_at-NEW.first_seen)))::bigint,
            'count',NEW.occurrences)));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_ops_incident_recovered
AFTER UPDATE ON public.stewardship_ops_incident
FOR EACH ROW WHEN (OLD.resolved_at IS NULL AND NEW.resolved_at IS NOT NULL
    AND NEW.kind NOT IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused'))
EXECUTE FUNCTION public.stewardship_ops_incident_recovered_v1();
REVOKE ALL ON FUNCTION public.stewardship_ops_incident_recovered_v1() FROM PUBLIC;

-- Refuse to commit unless everything above is installed. CREATE OR REPLACE
-- resets every attribute it does not state; each statement above states its
-- own (the allowlist IMMUTABLE and invoker-rights, the producers SECURITY
-- DEFINER), all with the fixed search_path, so check them here as well.
DO $check$
BEGIN
    IF NOT public.stewardship_safe_context_v1('failure',
           '{"failure":"provider_status","status":503,"attempt":2,"outcome":"retry"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('recovery',
           '{"incident_kind":"scheduler_lag","elapsed_seconds":1800,"count":3}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('due_work',
           '{"occurrence_id":"00000000-0000-0000-0000-000000000001","lag_seconds":120}'::jsonb)
       OR public.stewardship_safe_context_v1('failure','{"failure":"anything else"}'::jsonb)
       OR public.stewardship_safe_context_v1('failure','{"message":"x"}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit exactly the #633 contexts';
    END IF;
    IF position('incident_recovered' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint WHERE conname='operational_event_safe')))=0
       OR position('service_status_failed' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint WHERE conname='operational_event_safe')))=0 THEN
        RAISE EXCEPTION 'operational_event_safe does not admit incident_recovered and service_status_failed';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.proname IN ('stewardship_mail_health_result_v1','stewardship_ops_delivery_error_v1',
              'stewardship_security_delivery_error_v1','stewardship_ops_slack_error_v1',
              'stewardship_ops_slack_task_error_v1','stewardship_ops_incident_recovered_v1')
          AND p.prosrc NOT LIKE '%''{}''::jsonb%'
          AND (p.prosrc LIKE '%''failure''%' OR p.prosrc LIKE '%incident_recovered%'))<>6 THEN
        RAISE EXCEPTION 'A #633 producer was not replaced with its definer rights and context';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
                     AND NOT p.prosecdef AND p.provolatile='i'
                     AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 has the wrong volatility, security or search_path';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid='public.stewardship_ops_incident'::regclass
                     AND tgname='stewardship_ops_incident_recovered' AND NOT tgisinternal
                     AND pg_get_triggerdef(oid) LIKE '%automation_approved%'
                     AND pg_get_triggerdef(oid) LIKE '%automation_irreversible%'
                     AND pg_get_triggerdef(oid) LIKE '%automation_policy_change%'
                     AND pg_get_triggerdef(oid) LIKE '%automation_refused%') THEN
        RAISE EXCEPTION 'The incident recovery trigger is not installed without the automation kinds';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_due_work_health_v1'
                     AND NOT p.prosecdef AND p.provolatile='v'
                     AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND p.prosrc LIKE '%jsonb_build_object(''limit_seconds'',90)%'
                     AND p.prosrc NOT LIKE '%detail:=''{}''%') THEN
        RAISE EXCEPTION 'stewardship_due_work_health_v1 was not replaced with its limit fallback';
    END IF;
END
$check$;
