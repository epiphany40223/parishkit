-- Frozen forward migration file 0083 (the repository-wide file sequence):
-- the daily digest's Response funnel charts (#477, slice 2; reports spec,
-- "Chart engine"). This file is installed by the Django migration that
-- names it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copy of stewardship_safe_context_v1 equals the
-- fresh-install baseline's (schema/functions.sql). A fresh install runs the
-- baseline, the earlier files and then this; the baseline already carries
-- the replaced body, so the install ends in the same catalog as an upgraded
-- database.
--
-- This file:
--
--   - adds two nullable bytea columns to stewardship_daily_digest_ready,
--     which a Production digest compiled with its funnel charts fills:
--     funnel_chart and activity_chart, the PNGs of the Response funnel and
--     Response activity charts its email shows, retained so a retry or a
--     later fan-out sends the same bytes. A nullable column with no default
--     rewrites no row. Every existing digest, every Testing digest (which
--     has no funnel) and a digest sent with its charts withheld has neither.
--     A check keeps them together: both or neither. Size and format are the
--     compiled-body validator's, as for the participation and comparison
--     charts' columns;
--   - replaces 0072's daily_digest_comparison_charts check: a retained
--     comparison section may now have no chart images, because the last
--     compile attempt withholds every server-drawn chart the renderer
--     cannot draw and sends the digest without them (the section is then
--     kept with no charts). A pledges chart still needs the Families chart,
--     and no section still means no images;
--   - replaces stewardship_safe_context_v1 with 0072's body (the latest)
--     plus three failure words naming which digest chart was withheld
--     (digest_funnel_chart, digest_activity_chart, digest_comparison_chart),
--     for the ERROR task_failed entry that records it. It is not SECURITY
--     DEFINER (the baseline has no ALTER for it), stays IMMUTABLE with its
--     fixed search_path, and keeps its grants.
--
-- The worker and mail dispatch logins use table-level grants on the ready
-- row; the web login is granted the funnel_chart column
-- (runtime_grants.CLOSURE_COLUMNS) so the saved page shows the charts only
-- for a digest whose email had them.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced function's grants before the replacement, compared in the
-- DO block. A transaction-local setting holds them, not a temporary table:
-- the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0083_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'), true);

ALTER TABLE public.stewardship_daily_digest_ready ADD COLUMN funnel_chart bytea NULL;
ALTER TABLE public.stewardship_daily_digest_ready ADD COLUMN activity_chart bytea NULL;
ALTER TABLE public.stewardship_daily_digest_ready ADD CONSTRAINT daily_digest_funnel_charts CHECK ((((activity_chart IS NULL) AND (funnel_chart IS NULL)) OR ((activity_chart IS NOT NULL) AND (funnel_chart IS NOT NULL))));
ALTER TABLE public.stewardship_daily_digest_ready DROP CONSTRAINT daily_digest_comparison_charts;
ALTER TABLE public.stewardship_daily_digest_ready ADD CONSTRAINT daily_digest_comparison_charts CHECK ((((comparison IS NOT NULL) OR ((comparison_families_chart IS NULL) AND (comparison_pledges_chart IS NULL))) AND ((comparison_pledges_chart IS NULL) OR (comparison_families_chart IS NOT NULL))));

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
            'archive_year','archive_days','archive_family_rows']
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
                'digest_activity_chart','digest_comparison_chart') THEN RETURN false; END IF;
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

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_attribute
        WHERE attrelid='public.stewardship_daily_digest_ready'::regclass AND NOT attisdropped
          AND NOT attnotnull AND atthasdef IS FALSE
          AND attname IN ('funnel_chart','activity_chart')
          AND atttypid='bytea'::regtype)<>2
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_daily_digest_ready'::regclass
             AND conname='daily_digest_funnel_charts' AND contype='c' AND convalidated
             AND pg_get_constraintdef(oid) LIKE '%(activity_chart IS NULL) AND (funnel_chart IS NULL)%'
             AND pg_get_constraintdef(oid) LIKE '%(activity_chart IS NOT NULL) AND (funnel_chart IS NOT NULL)%') THEN
        RAISE EXCEPTION 'Migration 0083: the digest funnel chart columns are not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_daily_digest_ready'::regclass
             AND conname='daily_digest_comparison_charts' AND contype='c' AND convalidated
             AND pg_get_constraintdef(oid) LIKE '%(comparison IS NOT NULL) OR ((comparison_families_chart IS NULL) AND (comparison_pledges_chart IS NULL))%'
             AND pg_get_constraintdef(oid) LIKE '%(comparison_pledges_chart IS NULL) OR (comparison_families_chart IS NOT NULL)%') THEN
        RAISE EXCEPTION 'Migration 0083: the digest comparison chart check is not installed as declared';
    END IF;
    -- The context allowlist admits the withheld-chart words, still refuses
    -- unknown ones, and still admits the earlier contexts (the body is
    -- whole, not just this change): 0072's chart timeout, 0065's Entered by
    -- Staff sorts and 0059's archive words.
    IF NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "digest_funnel_chart", "attempt": 5, "outcome": "failed"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "digest_activity_chart"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "digest_comparison_chart"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "admin_command_outcome_unknown"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'timeout', '{"what": "digest_chart", "helper": "chart_render_worker",
                        "limit_seconds": 60, "elapsed_seconds": 61}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"report_sort": "-staff_entered"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"outcome": "succeeded", "archive_year": 2026, "count": 12}'::jsonb)
       OR public.stewardship_safe_context_v1('failure', '{"failure": "digest_chart"}'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0083: stewardship_safe_context_v1 does not admit the withheld chart words and the earlier contexts as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>1 THEN
        RAISE EXCEPTION 'Migration 0083: stewardship_safe_context_v1 is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0083_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1') THEN
        RAISE EXCEPTION 'Migration 0083: stewardship_safe_context_v1 lost or changed its grants';
    END IF;
END
$check$;
