-- Frozen forward migration file 0106 (the repository-wide file sequence,
-- number assigned centrally): every refused Admin input can leave one
-- durable operational log entry naming the signed-in user it refused
-- (#1403; operations spec, Admin input refusals). This file is installed by
-- its Django migration in the audit app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that its copies of the replaced functions equal the
-- fresh-install baseline's (schema/functions.sql). A later change gets its
-- own numbered file. A fresh install runs the baseline, the earlier files
-- and then this; the baseline already carries every change below (the
-- index is made only if missing), so the install ends in the same catalog
-- as an upgraded database.
--
-- One event, admin_input_refused, with schema 'refusal', covers every kind
-- of refusal; a closed reason code says which, and Python holds that and
-- the other closed sets (audit.refusal_context), as for failure_kind, so a
-- new reason needs no migration. The context holds closed values only:
-- the page or command, the method and status, server-owned form and field
-- names, error codes, the reason, an unclassified refusal's exception class,
-- allow-listed expected and actual setting values, an unexpected-field
-- count, the mode and the cap mark. Never a submitted value, an address or
-- free text. For expected and actual the database checks only the shape;
-- Python alone decides which reasons may carry them (SETTING_REASONS),
-- so a new setting reason needs no migration. Field names are checked by
-- shape too; the caller must take them from the form's own fields, never
-- from submitted keys. It is the one direct entry that must name an actor
-- (the refused user's PortalUser id), so the writer refuses it without
-- one, and every other direct entry still with one. That id is web's
-- word, taken on trust as on web's audit records. Four objects change,
-- each copied whole from its latest definition with only this change:
--   operational_event_safe                 0089's list plus
--                                          admin_input_refused
--   stewardship_safe_context_v1            0104's body (the latest; it
--                                          keeps 0102's and 0104's action
--                                          keys) plus the 'refusal' schema
--   stewardship_operational_log_writer_v1  0089's body plus two web tuples,
--                                          ('refusal','admin_input_refused',
--                                          'INFO') and (...,'WARNING'),
--                                          which alone require an actor
--   operational_refusal_actor_time         a new partial index on
--                                          (actor_id, created_at) of the
--                                          refusal rows, which the writer's
--                                          per-actor cap counts
-- Neither function is SECURITY DEFINER; both keep their identity,
-- attributes and trigger. No table, function or grant is created, so no
-- runtime grant changes: web already inserts and reads the log. Reversing
-- needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The full event list: 0089's plus the new name. ADD CONSTRAINT validates
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
        'admin_command_failed','debug_logging_switched_off',
        'retry_refusal_unrecorded','admin_input_refused'));

CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
        ministry jsonb; previous_ministry bigint; item jsonb;
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
            'archive_year','archive_days','archive_family_rows','void_reason','task_state',
            -- How old the Google sign-in was when a setup credential was
            -- sealed, in whole seconds (#1402; audit.schemas).
            'sign_in_age_seconds']
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
        -- A refused Admin input (#1403): its page or command, server-owned
        -- form and field names, error codes and the reason, never what was
        -- submitted (audit.refusal_context).
        WHEN 'refusal' THEN ARRAY['route','command','method','status','form','fields','codes',
            'reason','exception_class','expected','actual','unexpected_count','mode','capped']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    -- A refusal always says why and names exactly one of its page and its
    -- command; an exception class and an unexpected-field count belong
    -- only to their own reasons.
    IF schema_name='refusal' AND (NOT payload ? 'reason'
        OR (payload ? 'route')=(payload ? 'command')
        OR (payload ? 'exception_class' AND payload->>'reason' IS DISTINCT FROM 'unclassified')
        OR (payload ? 'unexpected_count' AND payload->>'reason' IS DISTINCT FROM 'unexpected_fields')) THEN
        RETURN false;
    END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        -- A refused Admin input (#1403), checked by its own rules: these keys
        -- mean something else in the other schemas. Identifiers share one
        -- shape (Python holds their closed sets); expected and actual are
        -- setting values such as zone names, never an address.
        IF schema_name='refusal' THEN
            IF key IN ('route','method','form','reason','exception_class') THEN
                IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_:-]{0,63}$' THEN RETURN false; END IF;
            ELSIF key='command' THEN
                IF jsonb_typeof(value)<>'string' OR length(text_value)>64
                   OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
            ELSIF key IN ('fields','codes') THEN
                IF jsonb_typeof(value)<>'array' OR jsonb_array_length(value) NOT BETWEEN 1 AND 10 THEN RETURN false; END IF;
                FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
                    IF jsonb_typeof(item)<>'string' OR item#>>'{}'!~'^[a-z][a-z0-9_:-]{0,63}$' THEN RETURN false; END IF;
                END LOOP;
            ELSIF key IN ('expected','actual') THEN
                IF jsonb_typeof(value)<>'string' OR text_value!~'^[A-Za-z0-9_+./-]{1,64}$' THEN RETURN false; END IF;
            ELSIF key='mode' THEN
                IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('testing','production') THEN RETURN false; END IF;
            ELSIF key='capped' THEN
                IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
            ELSIF key='status' THEN
                IF jsonb_typeof(value)<>'number' OR text_value NOT IN ('400','403','404','409','410','422') THEN RETURN false; END IF;
            ELSE
                IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,4}$' THEN RETURN false; END IF;
            END IF;
        ELSIF key='outcome' THEN
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

CREATE OR REPLACE FUNCTION public.stewardship_operational_log_writer_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Each runtime login that writes directly may record only the entries
    -- its own code writes (#293 for mail dispatch and backup, #389 L2 for
    -- web, worker and scheduler): a closed (schema, event, level) list, no
    -- actor to impersonate, and the database's own time. The one exception
    -- is web's refused Admin input (#1403), which must name the signed-in
    -- user it refused, so no anonymous refusal is kept; every other entry
    -- names none. That actor is a bare id web supplies and is taken on
    -- trust, as on every audit record web writes: a compromised web login
    -- could name another user here. Web never writes CRITICAL, which pages
    -- and opens incidents. The schema owner, and so every SECURITY DEFINER
    -- writer, is not limited here; no other login holds INSERT.
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF (NEW.actor_id IS NOT NULL)<>(NEW.event='admin_input_refused') OR NOT (
        -- Work stopped by a time limit, from any runtime login.
        (current_user IN ('pk_stewardship_web','pk_stewardship_worker',
                'pk_stewardship_scheduler','pk_stewardship_mail_dispatch',
                'pk_stewardship_backup_worker')
         AND NEW.schema='timeout'
         AND NEW.event IN ('task_timed_out','helper_timed_out',
                'work_budget_reached','task_lease_lost')
         AND NEW.level IN ('INFO','WARNING','ERROR'))
        -- Web: an unusable source Member on a Family form, a Family
        -- engagement record that could not be written, an Admin
        -- command-line command that failed unexpectedly (#617) and a
        -- refused Admin input (#1403).
        OR (current_user='pk_stewardship_web' AND (
            (NEW.schema,NEW.event,NEW.level) IN (
                ('member_source','source_member_unusable','WARNING'),
                ('failure','family_engagement_failed','ERROR'),
                ('failure','admin_command_failed','ERROR'),
                ('refusal','admin_input_refused','INFO'),
                ('refusal','admin_input_refused','WARNING'))))
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
                ('failure','web_unhealthy','CRITICAL'))))
        -- Mail dispatch: an address follow-up's permanent refusal that
        -- could not be set aside (#1201), CRITICAL so an Administrator sees
        -- it.
        OR (current_user='pk_stewardship_mail_dispatch' AND
            (NEW.schema,NEW.event,NEW.level) IN (
                ('failure','retry_refusal_unrecorded','CRITICAL')))) THEN
        RAISE EXCEPTION 'This login may not record this operational entry'
            USING ERRCODE = '42501';
    END IF;
    NEW.created_at := statement_timestamp();
    RETURN NEW;
END;
$$;

-- The writer's per-actor cap counts one actor's refusal entries in the
-- current window (audit.refusals). A fresh install's baseline already has
-- this index, so it is made only when missing.
CREATE INDEX IF NOT EXISTS operational_refusal_actor_time ON public.stewardship_operational_log USING btree (actor_id, created_at) WHERE ((event)::text = 'admin_input_refused'::text);

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
        'admin_command_failed','debug_logging_switched_off',
        'retry_refusal_unrecorded','admin_input_refused'
        ]) AS name
     WHERE position(quote_literal(name) IN definition)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'operational_event_safe does not admit: %', missing;
    END IF;
    -- The context admits a complete refusal and the command form, keeps
    -- 0089's failure word and 0102's and 0104's action keys, and still
    -- refuses a refusal without its reason, with both or neither of page
    -- and command, with an address or free text, with too many fields or
    -- with an unknown key.
    IF NOT public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:campaign_create", "method": "post", "status": 400,
                        "form": "campaign", "fields": ["timezone", "slots-0-time"],
                        "codes": ["invalid_choice"], "reason": "unclassified",
                        "exception_class": "value_error", "expected": "Africa/Abidjan",
                        "actual": "America/New_York", "mode": "testing",
                        "capped": true}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'refusal', '{"command": "export create", "reason": "unexpected_fields",
                        "unexpected_count": 2}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "retry_refusal"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"void_reason": "reload", "task_state": "failed",
                       "sign_in_age_seconds": 3600}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:campaign_create"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:home", "command": "whoami", "reason": "denied"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"reason": "denied"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:home", "reason": "denied", "actual": "a@example.org"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:home", "reason": "Denied by a person"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:home", "reason": "denied",
                        "fields": ["a","b","c","d","e","f","g","h","i","j","k"]}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'refusal', '{"route": "admin:home", "reason": "denied", "value": "x"}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit the refusal schema as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND position('WHEN ''refusal'' THEN' IN p.prosrc)>0
          AND position('''retry_refusal''' IN p.prosrc)>0
          AND position('''sign_in_age_seconds''' IN p.prosrc)>0
          AND position('''void_reason''' IN p.prosrc)>0)<>1 THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 is not installed as declared';
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
          AND position('(NEW.actor_id IS NOT NULL)<>(NEW.event=''admin_input_refused'')' IN p.prosrc)>0
          AND position('(''refusal'',''admin_input_refused'',''INFO'')' IN p.prosrc)>0
          AND position('(''refusal'',''admin_input_refused'',''WARNING'')' IN p.prosrc)>0
          AND position('(''failure'',''retry_refusal_unrecorded'',''CRITICAL'')' IN p.prosrc)>0
          AND position('This login may not record this operational entry' IN p.prosrc)>0) THEN
        RAISE EXCEPTION 'Migration 0106 (admin input refusal log) is not installed as declared';
    END IF;
    -- The cap's index is valid and exactly the declared partial index.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
        WHERE x.indrelid='public.stewardship_operational_log'::regclass
          AND i.relname='operational_refusal_actor_time'
          AND x.indisvalid AND x.indisready
          AND pg_get_indexdef(i.oid)='CREATE INDEX operational_refusal_actor_time ON public.stewardship_operational_log USING btree (actor_id, created_at) WHERE ((event)::text = ''admin_input_refused''::text)') THEN
        RAISE EXCEPTION 'Migration 0106 (admin input refusal log) index operational_refusal_actor_time is not installed as declared';
    END IF;
END
$check$;
