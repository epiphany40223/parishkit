-- Frozen forward migration file 0006 (the repository-wide file sequence;
-- Django's stewardship_jobs.0006): due_work_lag says what was late (#634).
-- This file is installed by jobs/migrations/0006_due_work_context.py and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of the two functions still equal
-- the fresh-install baseline's (schema/functions.sql and
-- schema/due_work_health.sql). A later change to either gets its own numbered
-- file. A fresh install runs 0001 through 0005 and then this; the baseline
-- already carries both bodies, so the install ends in the same catalog as an
-- upgraded database.
--
-- Before #634 the due-work checkpoint trigger logged due_work_lag with an
-- empty context, so System logs could not say what was late. The operational
-- context allowlist gains a closed 'due_work' schema (counts, durations in
-- seconds, a task type and the late Family send's definition and revision
-- ids: no names, addresses, text or raw timestamps), and the trigger copies the scheduler scan's context,
-- passed in the transaction-local setting parishkit.due_work_context, into
-- the CRITICAL entry, dropping one the allowlist refuses. No table, column,
-- constraint, grant or credential changes. Reversing needs its own forward
-- migration that restores the old bodies.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

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
            'remaining_count','done_count','stall_seconds','elapsed_seconds','other_late_count']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        IF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value<>'no_deliverable_recipient' THEN RETURN false; END IF;
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
        -- to this transaction. A context the log would refuse is dropped,
        -- never allowed to block the failure record itself.
        BEGIN
            detail:=COALESCE(NULLIF(current_setting('parishkit.due_work_context',true),'')::jsonb,'{}');
        EXCEPTION WHEN invalid_text_representation THEN
            detail:='{}';
        END;
        IF NOT public.stewardship_safe_context_v1('due_work',detail) THEN
            detail:='{}';
        END IF;
        INSERT INTO public.stewardship_operational_log
            (id,correlation_id,level,event,schema,context)
        VALUES(gen_random_uuid(),gen_random_uuid(),'CRITICAL','due_work_lag','due_work',detail);
        NEW.last_failure_at:=instant;
    END IF;
    RETURN NEW;
END $$;

-- Refuse to commit unless both new bodies are installed with the baseline's
-- attributes, so an upgrade cannot report success with the old allowlist or
-- the old empty-context trigger. CREATE OR REPLACE resets every attribute it
-- does not state: both state their fixed search_path, the allowlist is
-- IMMUTABLE (it backs CHECK constraints) and the trigger VOLATILE, and
-- neither is SECURITY DEFINER in the baseline. The trigger function keeps
-- its revoked PUBLIC execute grant.
DO $check$
BEGIN
    IF NOT public.stewardship_safe_context_v1('due_work',
           '{"task_type":"outbox_delivery","stall_seconds":600}'::jsonb)
       OR public.stewardship_safe_context_v1('due_work','{"message":"x"}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit the due_work context';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_due_work_health_v1'
                     AND p.prosrc LIKE '%parishkit.due_work_context%') THEN
        RAISE EXCEPTION 'stewardship_due_work_health_v1 was not replaced';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND ((p.proname='stewardship_safe_context_v1' AND p.provolatile='i')
               OR (p.proname='stewardship_due_work_health_v1' AND p.provolatile='v')))<>2 THEN
        RAISE EXCEPTION 'A due-work function has the wrong volatility, security or search_path';
    END IF;
END
$check$;
