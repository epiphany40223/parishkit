-- Frozen forward migration file 0104 (the repository-wide file sequence):
-- the setup wizard's credential steps accept a sign-in up to an hour old,
-- and the sealing records how old it was (#1402). This file is installed
-- by the Django migration that names it in FROZEN_SQL and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- stewardship_setup_secret_guard_v1 admits a staged setup credential only
-- for the setup attempt's original owner and original live session, while
-- the attempt is collecting, in Testing mode with no campaign, over a
-- bootstrap configuration. It also required that session's Google sign-in
-- to be under five minutes old. Since #1353 the ParishSoft connection is
-- step 9, so an Administrator was asked to confirm with Google on almost
-- every credential page. The web now accepts a sign-in up to an hour old
-- on those steps (sessions.SETUP_FRESH_SECONDS, inclusive), and the guard
-- enforces the same hour, inclusive too.
--
-- With the window twelve times wider, the staged credential's audit
-- context also records the sign-in's age in whole seconds when it is
-- sealed (sign_in_age_seconds), since a later step-up in the same session
-- overwrites the session's authenticated_at.
--
-- This file replaces:
--   stewardship_setup_secret_guard_v1   0055's body (the latest), the
--                                       sign-in interval 60 minutes,
--                                       inclusive
--   stewardship_setup_secret_audit_v1   the baseline's body (never replaced
--                                       before), adding the age to a
--                                       staged credential's context
--   stewardship_safe_context_v1         0102's body plus the action key
--                                       sign_in_age_seconds (an integer,
--                                       as every unlisted key)
-- Each stays SECURITY INVOKER with its pinned search_path and keeps its
-- grants. Every other fresh-sign-in rule, including secret replacement
-- after setup (five minutes), is unchanged.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The functions' grants before the replacement, compared in the DO block.
-- A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0104_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
        'stewardship_safe_context_v1','stewardship_setup_secret_guard_v1',
        'stewardship_setup_secret_audit_v1')), true);

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

CREATE OR REPLACE FUNCTION public.stewardship_setup_secret_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
        stamp timestamptz:=clock_timestamp(); keys text[]; item jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup credential tombstones are retained'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup credentials require ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup credentials cannot be repopulated'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF attempt.state NOT IN ('expired','completed')
           OR NEW.ciphertext IS NOT NULL OR NEW.actor_id IS NOT NULL
           OR NEW.settings<>'{}'::jsonb
           OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
            RAISE EXCEPTION 'Setup credential scrub requires its terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '60 minutes'
            -- Setup's credential steps accept a sign-in up to an hour old
            -- (#1402), the hour inclusive, as the web's check is.
            AND login.authenticated_at>=stamp-interval '60 minutes'
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup credential requires its original freshly signed-in Admin'
            USING ERRCODE='23514';
    END IF;
    IF NEW.ciphertext IS NULL OR length(NEW.ciphertext)=0
       OR octet_length(NEW.ciphertext)>2097152
       OR jsonb_typeof(NEW.settings) IS DISTINCT FROM 'object'
       OR octet_length(NEW.settings::text)>2048 THEN
        RAISE EXCEPTION 'Invalid sealed setup credential shape' USING ERRCODE='23514';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO keys
        FROM jsonb_object_keys(NEW.settings) key;
    IF NEW.target='parishsoft' THEN
        IF keys IS DISTINCT FROM ARRAY['organization_id']
           OR jsonb_typeof(NEW.settings->'organization_id') <> 'number'
           OR (NEW.settings->>'organization_id') !~ '^[1-9][0-9]{0,9}$'
           OR (NEW.settings->>'organization_id')::numeric >= 2147483648 THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='slack' THEN
        IF keys IS DISTINCT FROM ARRAY['channel_id']
           OR jsonb_typeof(NEW.settings->'channel_id') <> 'string'
           OR (NEW.settings->>'channel_id') !~ '^[CG][A-Z0-9]{1,63}$' THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='google_workspace' THEN
        IF keys IS DISTINCT FROM
            ARRAY['delegated_email','recipient','reply_to','sender'] THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
        FOR item IN SELECT value FROM jsonb_each(NEW.settings) LOOP
            IF jsonb_typeof(item) <> 'string' OR length(item#>>'{}')>254
               OR (item#>>'{}') <> lower(item#>>'{}')
               OR (item#>>'{}') ~ '[[:space:][:cntrl:]]'
               OR (item#>>'{}') !~ '^[^@]+@[^@]+$' THEN
                RAISE EXCEPTION 'Invalid provider validation context'
                    USING ERRCODE='23514';
            END IF;
        END LOOP;
    ELSE
        RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END $_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_secret_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid(); context jsonb; age bigint;
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        CASE WHEN NEW.scrubbed_at IS NULL THEN 'setup_credential_staged'
            ELSE 'setup_credential_scrubbed' END,NEW.id,'deployment');
    context:=jsonb_build_object('version',NEW.version,'outcome',
        CASE WHEN NEW.scrubbed_at IS NULL THEN 'changed' ELSE 'cancelled' END);
    IF NEW.scrubbed_at IS NULL THEN
        -- How old the setup session's Google sign-in was when the credential
        -- was sealed (#1402): a later step-up in the same session overwrites
        -- authenticated_at, so the age is recorded now. A separate statement,
        -- run only for a staged credential: the scrubbing scheduler cannot
        -- read the session.
        SELECT greatest(0,floor(extract(epoch FROM clock_timestamp()-login.authenticated_at)))::bigint
            INTO age
            FROM public.stewardship_setup_attempt attempt
            JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            WHERE attempt.id=NEW.attempt_id;
        IF age IS NOT NULL THEN
            context:=context||jsonb_build_object('sign_in_age_seconds',age);
        END IF;
    END IF;
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        context);
    RETURN NULL;
END $$;

DO $check$
BEGIN
    -- Each function is the one overload, runs as its caller with its pinned
    -- search_path, and has its new text.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN (
            'stewardship_safe_context_v1','stewardship_setup_secret_guard_v1',
            'stewardship_setup_secret_audit_v1'))<>3
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_safe_context_v1','stewardship_setup_secret_guard_v1',
               'stewardship_setup_secret_audit_v1')
             AND NOT p.prosecdef
             AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>3 THEN
        RAISE EXCEPTION 'Migration 0104: a replaced function is not installed as declared';
    END IF;
    -- The guard accepts a sign-in up to 60 minutes old, inclusive, keeps the
    -- 60-minute idle rule and the original-owner, Testing and bootstrap
    -- conditions, and no longer has the five-minute sign-in rule.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1'
          AND pg_get_function_identity_arguments(p.oid)=''
          AND p.prorettype='trigger'::regtype
          AND p.prosrc LIKE '%AND login.authenticated_at>=stamp-interval ''60 minutes''%'
          AND p.prosrc LIKE '%AND login.last_activity_at>stamp-interval ''60 minutes''%'
          AND p.prosrc LIKE '%NEW.actor_id IS DISTINCT FROM attempt.owner_id%'
          AND p.prosrc LIKE '%WHERE runtime.mode=''testing'' AND NOT runtime.restore_review_required%'
          AND p.prosrc LIKE '%base.validation_schema IN (''bootstrap-policy-v1'', ''bootstrap-users-v10'')%'
          AND p.prosrc NOT LIKE '%authenticated_at>stamp-interval ''5 minutes''%')<>1 THEN
        RAISE EXCEPTION 'Migration 0104: the setup credential guard is not installed as declared';
    END IF;
    -- The audit records a staged credential's sign-in age.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_audit_v1'
          AND p.prorettype='trigger'::regtype
          AND p.prosrc LIKE '%jsonb_build_object(''sign_in_age_seconds'',age)%'
          AND p.prosrc LIKE '%''setup_credential_staged''%')<>1 THEN
        RAISE EXCEPTION 'Migration 0104: the setup credential audit is not installed as declared';
    END IF;
    -- The safe context admits the age as an action key, still refuses an
    -- unknown key, and keeps 0102's discarded-load keys.
    IF NOT public.stewardship_safe_context_v1(
           'action', '{"version": 2, "outcome": "changed", "sign_in_age_seconds": 3600}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"sign_in_age_seconds": "3600"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"sign_in_instant": 1}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"void_reason": "reload", "task_state": "failed"}'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0104: the safe context does not admit the sign-in age as declared';
    END IF;
    IF current_setting('stewardship.migration_0104_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_safe_context_v1','stewardship_setup_secret_guard_v1',
               'stewardship_setup_secret_audit_v1')) THEN
        RAISE EXCEPTION 'Migration 0104: a replaced function lost or changed its grants';
    END IF;
    -- Both still guard and audit the sealed setup credential table.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_setup_sealed_credential'::regclass
          AND t.tgenabled='O'
          AND ((t.tgname='stewardship_setup_secret_admission'
                AND p.proname='stewardship_setup_secret_guard_v1')
            OR (t.tgname='stewardship_setup_secret_audit'
                AND p.proname='stewardship_setup_secret_audit_v1')))<>2 THEN
        RAISE EXCEPTION 'Migration 0104: a setup credential trigger is missing';
    END IF;
END
$check$;
