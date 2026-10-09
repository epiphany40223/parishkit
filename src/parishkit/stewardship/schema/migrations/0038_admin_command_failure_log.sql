-- Frozen forward migration file 0038 (the repository-wide file sequence):
-- an Admin command-line command that fails unexpectedly leaves one durable
-- operational log entry (#617). This file is installed by its Django
-- migration in the jobs app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copies of the replaced functions equal the fresh-install baseline's
-- (schema/functions.sql). A later change gets its own numbered file. A fresh
-- install runs the baseline, the earlier files and then this; the baseline
-- already carries every change below, so the install ends in the same catalog
-- as an upgraded database.
--
-- pk-admin runs inside the web container on the web's own login
-- (pk_stewardship_web). An unexpected failure (error code internal or
-- outcome_unknown) printed only a standard-error line, which through the
-- host wrapper reaches the operator's terminal and nothing durable. The
-- command now records one ERROR entry, admin_command_failed, with schema
-- 'failure' and only closed values: what failed ('admin_command', or
-- 'admin_command_outcome_unknown' when a change may have been made), the
-- failure category and the command's catalog name. Never exception text,
-- arguments, the session or any personal data; no actor. Three objects
-- change, each copied whole from its latest definition with only this change:
--   operational_event_safe               0036's list plus admin_command_failed
--   stewardship_safe_context_v1          0032's body plus the 'command' key of
--                                        the failure schema and its two
--                                        failure words
--   stewardship_operational_log_writer_v1  0029's body plus one web tuple,
--                                        ('failure','admin_command_failed',
--                                        'ERROR'); web still never writes
--                                        CRITICAL
-- Both functions keep their identity, attributes and trigger; neither is
-- SECURITY DEFINER. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The full event list: 0036's plus the new name. ADD CONSTRAINT validates
-- the existing rows, which the old list already admitted.
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
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy',
        'go_live_refresh_held','go_live_step_refused','go_live_sequencing_stopped',
        'admin_command_failed'));

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
            'report_mode','report_filter','talent_option_id','snapshot_id']
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
                'admin_command','admin_command_outcome_unknown') THEN RETURN false; END IF;
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
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe','source_load_budget') THEN RETURN false; END IF;
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
        -- An on-request report page or download (#556): the system mode it
        -- showed and its closed filter choice (audit.schemas.REPORT_FILTERS).
        ELSIF key='report_mode' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('production','testing') THEN RETURN false; END IF;
        ELSIF key='report_filter' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('all','invited','uninvited','progressed',
                'opened','followed','unfollowed','mailing-name','envelope','any','cannot_serve',
                'cannot_attend','option') THEN RETURN false; END IF;
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

CREATE OR REPLACE FUNCTION public.stewardship_operational_log_writer_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Each runtime login that writes directly may record only the entries
    -- its own code writes (#293 for mail dispatch and backup, #389 L2 for
    -- web, worker and scheduler): a closed (schema, event, level) list, no
    -- actor to impersonate, and the database's own time. Web never writes
    -- CRITICAL, which pages and opens incidents. The schema owner, and so
    -- every SECURITY DEFINER writer, is not limited here; no other login
    -- holds INSERT.
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF NEW.actor_id IS NOT NULL OR NOT (
        -- Work stopped by a time limit, from any runtime login.
        (current_user IN ('pk_stewardship_web','pk_stewardship_worker',
                'pk_stewardship_scheduler','pk_stewardship_mail_dispatch',
                'pk_stewardship_backup_worker')
         AND NEW.schema='timeout'
         AND NEW.event IN ('task_timed_out','helper_timed_out',
                'work_budget_reached','task_lease_lost')
         AND NEW.level IN ('INFO','WARNING','ERROR'))
        -- Web: an unusable source Member on a Family form, a Family
        -- engagement record that could not be written, and an Admin
        -- command-line command that failed unexpectedly (#617).
        OR (current_user='pk_stewardship_web' AND (
            (NEW.schema,NEW.event,NEW.level) IN (
                ('member_source','source_member_unusable','WARNING'),
                ('failure','family_engagement_failed','ERROR'),
                ('failure','admin_command_failed','ERROR'))))
        -- Worker: source refresh, setup load and finalize failures; source
        -- retention; operational collection; fact verification and export
        -- cleanup failures.
        OR (current_user='pk_stewardship_worker' AND (
            (NEW.schema='failure'
             AND NEW.event IN ('source_tenant_mismatch','source_destructive_change',
                    'source_provider_failed','source_refresh_invalid',
                    'source_refresh_held','source_credential_failed')
             AND NEW.level IN ('INFO','WARNING','CRITICAL'))
            OR (NEW.schema,NEW.event,NEW.level) IN (
                ('failure','source_retention_skipped','ERROR'),
                ('failure','task_failed','ERROR'),
                ('failure','task_failed','CRITICAL'),
                ('member_source','source_member_unusable','WARNING'),
                ('exception','configuration_digest_mismatch','WARNING'),
                ('task','fact_drift','CRITICAL'))))
        -- Scheduler: a held slot production, boundary lag, due-work lag
        -- (whose health trigger runs as the scheduler) and the web health
        -- alert (#787's trigger, which also runs as the scheduler).
        OR (current_user='pk_stewardship_scheduler' AND (
            (NEW.schema,NEW.event,NEW.level) IN (
                ('schedule','source_refresh_held','INFO'),
                ('due_work','campaign_boundary_lag','WARNING'),
                ('due_work','due_work_lag','CRITICAL'),
                ('failure','web_unhealthy','CRITICAL'))))) THEN
        RAISE EXCEPTION 'This login may not record this operational entry'
            USING ERRCODE = '42501';
    END IF;
    NEW.created_at := statement_timestamp();
    RETURN NEW;
END;
$$;

-- Refuse to commit unless every change above is installed, so an upgrade
-- cannot report success with one lost. No temporary objects: the migration
-- login has no TEMP grant.
DO $check$
DECLARE definition text; missing text;
BEGIN
    -- The event constraint is validated and lists every name, old and new.
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_operational_log'::regclass
       AND conname='operational_event_safe' AND contype='c' AND convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'operational_event_safe is not installed and validated';
    END IF;
    SELECT string_agg(name, ', ') INTO missing
      FROM unnest(ARRAY[

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
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy',
        'go_live_refresh_held','go_live_step_refused','go_live_sequencing_stopped',
        'admin_command_failed']) AS name
     WHERE position(quote_literal(name) IN definition)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'operational_event_safe does not admit: %', missing;
    END IF;
    -- The context admits the new entry's keys and words, and still refuses
    -- free text, an unknown word and a malformed command name.
    IF NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "admin_command", "failure_kind": "unexpected_failure",
                        "command": "export create"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "admin_command_outcome_unknown",
                        "command": "config request show"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "web_unresponsive", "count": 3}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'failure', '{"failure": "admin_command_crashed"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'failure', '{"command": "export create --family 12"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'failure', '{"command": "Export Create"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'failure', '{"command": 7}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'failure', '{"message": "boom"}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit the admin command failure as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>1 THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 has the wrong attributes';
    END IF;
    -- The writer's new body is installed behind the same enabled BEFORE
    -- INSERT row trigger, still an invoker.
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgname='stewardship_operational_log_writer_v1'
          AND t.tgrelid='public.stewardship_operational_log'::regclass
          AND t.tgenabled='O' AND t.tgtype=7
          AND p.oid='public.stewardship_operational_log_writer_v1()'::regprocedure
          AND NOT p.prosecdef
          AND position('(''failure'',''admin_command_failed'',''ERROR'')' IN p.prosrc)>0
          AND position('This login may not record this operational entry' IN p.prosrc)>0) THEN
        RAISE EXCEPTION 'Migration 0038 (admin command failure log) is not installed as declared';
    END IF;
END
$check$;
