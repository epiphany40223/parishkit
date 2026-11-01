-- Frozen forward migration file 0203: the audit context of sign-in through
-- another verified address (#2343 slice 2; admin-portal spec
-- "Verified-address outcomes"). This file is installed by its Django
-- migration in the accounts app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration. (Prefix 0203 in the
-- repository-wide sequence; 0201 and 0202 belong to other open pull
-- requests, and the train renumbers this file if the sequence moved.)
--
-- A check of a Google account's other verified addresses records
-- admin_address_checked with its outcome, and every sign-in through a match
-- records its admin_login with the match's ID, IDs only, never an address.
-- The action audit context had no key for either, so this file replaces
--   stewardship_safe_context_v1   two action keys: address_check, one of
--     rechecked, declined, none, several, own_account_only and taken, and
--     match_id, a UUID as every *_id key
-- with the baseline body (0174's) otherwise unchanged, except that its
-- jsonb operators are written OPERATOR(pg_catalog.x), as every new frozen
-- file's are (#2166). That final body is also the baseline's
-- (schema/functions.sql). It stays SECURITY INVOKER and IMMUTABLE with its
-- pinned search_path, and CREATE OR REPLACE keeps its grants. The file ends with a DO block that refuses to commit unless it is
-- installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced function's grants before the replacement, compared in the DO
-- block. A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0203_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'), true);

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
            'sign_in_age_seconds',
            -- An export's report kind and file format (#1398), so the audit
            -- log answers who exported what without its request row.
            'report','format',
            -- A report view the open page made by itself to bring newer
            -- figures in (#1620; audit.schemas): a boolean.
            'automatic',
            -- The setup wizard's ParishSoft organization choice (#1554;
            -- audit.schemas): the chosen organization ID and how many
            -- organizations the key reached, held no Families or were
            -- unknown, integers as every unlisted key.
            'organization','organizations_reached','organizations_without_families',
            'organizations_unknown',
            -- A check of a Google account's other verified addresses
            -- (#2343; audit.schemas): its closed outcome word, and the
            -- verified-address match a re-check or a matched sign-in used.
            'address_check','match_id']
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
    IF schema_name IN ('member_source','boundary') AND NOT payload OPERATOR(pg_catalog.?&) allowed THEN RETURN false; END IF;
    -- A refusal always says why and names exactly one of its page and its
    -- command; an exception class and an unexpected-field count belong
    -- only to their own reasons.
    IF schema_name='refusal' AND (NOT payload OPERATOR(pg_catalog.?) 'reason'
        OR (payload OPERATOR(pg_catalog.?) 'route')=(payload OPERATOR(pg_catalog.?) 'command')
        OR (payload OPERATOR(pg_catalog.?) 'exception_class' AND payload->>'reason' IS DISTINCT FROM 'unclassified')
        OR (payload OPERATOR(pg_catalog.?) 'unexpected_count' AND payload->>'reason' IS DISTINCT FROM 'unexpected_fields')) THEN
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
                'due_work_health_check','backup_health_check','export_cleanup','export_discard','fact_verification',
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
        -- A verified-address check's outcome (#2343;
        -- audit.schemas.ADDRESS_CHECKS).
        ELSIF key='address_check' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('rechecked','declined','none',
                'several','own_account_only','taken') THEN RETURN false; END IF;
        ELSIF key='task_state' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('succeeded','failed') THEN RETURN false; END IF;
        -- An export's report kind and file format (#1398): the closed sets
        -- export_report_known admits (audit.schemas.EXPORT_REPORTS, EXPORT_FORMATS).
        ELSIF key='report' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('participation','additional_information',
                'family_directory','postal_outreach','ministry','financial','family_test_names',
                'family_timeline','campaign_comparison','campaign_archive') THEN RETURN false; END IF;
        ELSIF key='format' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('csv','xlsx','pdf','png') THEN RETURN false; END IF;
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
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational','directory_response_columns','automatic') THEN
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

DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_safe_context_v1(text,jsonb)')
          AND strpos(p.prosrc, '''address_check'',''match_id'']')>0
          AND strpos(p.prosrc, 'ELSIF key=''address_check'' THEN')>0
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'Migration 0203: stewardship_safe_context_v1 lacks the verified-address keys';
    END IF;
    IF NOT public.stewardship_safe_context_v1('action',
           '{"address_check": "declined"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('action',
           '{"address_check": "rechecked", "match_id": "0b4a4f6e-3c55-4c5e-9d0e-7f1b2a3c4d5e"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('action',
           '{"match_id": "0b4a4f6e-3c55-4c5e-9d0e-7f1b2a3c4d5e"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"address_check": "matched"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"address_check": 1}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"match_id": "person@example.org"}'::jsonb)
       OR public.stewardship_safe_context_v1('request', '{"address_check": "none"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('action',
           '{"organization": 1234, "outcome": "succeeded", "automatic": true}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('refusal',
           '{"route": "admin:home", "reason": "denied"}'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0203: stewardship_safe_context_v1 does not admit the verified-address keys as declared';
    END IF;
    IF current_setting('stewardship.migration_0203_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'), ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1') THEN
        RAISE EXCEPTION 'Migration 0203: stewardship_safe_context_v1 lost or changed its grants';
    END IF;
END
$check$;
