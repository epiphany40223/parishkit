-- Frozen forward migration file 0072 (the repository-wide file sequence):
-- the daily digest's Compared with past campaigns section (#1018, slice 7;
-- campaign-history spec, "Digest section"). This file is installed by the
-- Django migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that its copy of stewardship_safe_context_v1 equals the
-- fresh-install baseline's (schema/functions.sql). A fresh install runs the
-- baseline, the earlier files and then this; the baseline already carries
-- the replaced body, so the install ends in the same catalog as an upgraded
-- database.
--
-- This file:
--
--   - adds three nullable columns to stewardship_daily_digest_ready, which
--     a digest compiled with the section fills: comparison (the section's
--     protected figures, chart specifications and each imported year's
--     checksum, so the saved digest reproduces what was sent) and the two
--     charts' PNGs, comparison_families_chart and comparison_pledges_chart.
--     A nullable column with no default rewrites no row; every existing
--     digest has no section. A check keeps them together: no section and
--     no charts, or a section with at least its Families chart;
--   - replaces stewardship_safe_context_v1 with 0065's body (the latest:
--     0059's archive words plus 0065's staff_entered sort tokens) plus the
--     timeout word digest_chart and the helper chart_render_worker,
--     so a kill of the chart engine's server renderer while drawing the
--     section leaves a durable helper_timed_out entry. It is not SECURITY
--     DEFINER (the baseline has no ALTER for it), stays IMMUTABLE with its
--     fixed search_path, and keeps its grants;
--   - replaces stewardship_campaign_comparison_v1 and
--     stewardship_campaign_archive_renewal_v1 (0067's bodies, which created
--     them, so no baseline copy exists) changing only the caller check, so
--     the general worker's login (pk_stewardship_worker), which compiles
--     the digest, may read the same aggregates as the web login. Both stay
--     SECURITY DEFINER with their pinned search_path; EXECUTE stays revoked
--     from PUBLIC, and database-grants gives it to the worker login
--     (runtime_grants.runtime_functions).
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants before the replacement, compared in the
-- DO block. A transaction-local setting holds them, not a temporary table:
-- the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0072_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_safe_context_v1',
        'stewardship_campaign_comparison_v1','stewardship_campaign_archive_renewal_v1')), true);

ALTER TABLE public.stewardship_daily_digest_ready ADD COLUMN comparison jsonb NULL;
ALTER TABLE public.stewardship_daily_digest_ready ADD COLUMN comparison_families_chart bytea NULL;
ALTER TABLE public.stewardship_daily_digest_ready ADD COLUMN comparison_pledges_chart bytea NULL;
ALTER TABLE public.stewardship_daily_digest_ready ADD CONSTRAINT daily_digest_comparison_charts CHECK ((((comparison IS NULL) AND (comparison_families_chart IS NULL) AND (comparison_pledges_chart IS NULL)) OR ((comparison IS NOT NULL) AND (comparison_families_chart IS NOT NULL))));

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

CREATE OR REPLACE FUNCTION public.stewardship_campaign_comparison_v1(
    fact_set uuid, report_date date, band_edges numeric[], prior_archive uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    selected record;
    observed record;
    day_end timestamp with time zone;
    result jsonb;
BEGIN
    IF session_user NOT IN ('pk_stewardship_web','pk_stewardship_worker') AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web and worker logins read campaign comparison figures' USING ERRCODE='42501';
    END IF;
    IF report_date IS NULL OR band_edges IS NULL OR cardinality(band_edges) NOT BETWEEN 1 AND 20
       OR array_position(band_edges, NULL) IS NOT NULL OR band_edges[1]<=0
       OR EXISTS (SELECT 1 FROM generate_subscripts(band_edges, 1) i
                  WHERE i>1 AND band_edges[i]<=band_edges[i - 1]) THEN
        RAISE EXCEPTION 'Campaign comparison needs a report date and increasing band edges'
            USING ERRCODE='22023';
    END IF;
    -- Only a ready historical generation, and only a day it observed.
    SELECT s.campaign_id, s.submission_watermark, c.timezone INTO selected
    FROM public.stewardship_daily_fact_set s
    JOIN public.stewardship_campaign_configuration c ON c.id=s.timezone_configuration_id
    WHERE s.id=fact_set AND s.state='ready' AND s.population_scope='historical';
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT d.source_generation, d.cumulative_responses INTO observed
    FROM public.stewardship_daily_fact d
    WHERE d.fact_set_id=fact_set AND d.local_date=report_date AND d.population_available;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    -- The day's end: the next local midnight in the fact set's time zone.
    day_end=(report_date + 1)::timestamp AT TIME ZONE selected.timezone;
    WITH cohort AS (
        SELECT f.id, f.family_duid FROM public.stewardship_family_campaign f
        WHERE f.campaign_id=selected.campaign_id AND f.first_eligible_at IS NOT NULL
          AND f.first_eligible_source_generation<=observed.source_generation
          AND f.first_eligible_at<day_end
    ), latest AS (
        -- Each responding Family's latest live version by the day's end.
        SELECT DISTINCT ON (r.family_id) c.family_duid, r.annual_pledge
        FROM public.stewardship_submission r JOIN cohort c ON c.id=r.family_id
        WHERE r.campaign_id=selected.campaign_id AND r.mode='live'
          AND r.campaign_sequence<=selected.submission_watermark
          AND r.submitted_at<day_end
        ORDER BY r.family_id, r.campaign_sequence DESC
    ), pledges AS (
        SELECT annual_pledge AS amount FROM latest WHERE annual_pledge>0
    ), sorted AS (
        SELECT array_agg(amount ORDER BY amount) AS a FROM pledges
    )
    SELECT jsonb_build_object(
        'responded', (SELECT count(*) FROM latest),
        -- A missing pledge makes the day's pledge figures unavailable, as on
        -- the graph; a known zero is not missing.
        'pledge_complete', (SELECT coalesce(bool_and(annual_pledge IS NOT NULL), true) FROM latest),
        'pledge_total', (SELECT coalesce(sum(annual_pledge), 0)::text FROM latest),
        'pledging', (SELECT count(*) FROM pledges),
        -- h = (n - 1)p + 1; x[floor h] + (h - floor h)(x[floor h + 1] - x[floor h]).
        'quartiles', (
            SELECT jsonb_agg(round(s.a[k.k] + (h.h - k.k) * (s.a[least(k.k + 1, n.n)] - s.a[k.k]), 2)::text
                             ORDER BY p.p)
            FROM sorted s
            CROSS JOIN LATERAL (SELECT cardinality(s.a) AS n) n
            CROSS JOIN unnest(ARRAY[0.25, 0.5, 0.75]::numeric[]) p(p)
            CROSS JOIN LATERAL (SELECT (n.n - 1) * p.p + 1 AS h) h
            CROSS JOIN LATERAL (SELECT floor(h.h)::integer AS k) k
            WHERE n.n>0),
        -- Band b holds a pledge at or above edge b - 1 and below edge b.
        'bands', (
            SELECT jsonb_agg(jsonb_build_object('band', b.b, 'families', coalesce(g.families, 0),
                                                'total', coalesce(g.total, 0)::text) ORDER BY b.b)
            FROM generate_series(1, cardinality(band_edges) + 1) b(b)
            LEFT JOIN (SELECT width_bucket(amount, band_edges) + 1 AS band,
                              count(*) AS families, sum(amount) AS total
                       FROM pledges GROUP BY 1) g ON g.band=b.b),
        'renewed', CASE WHEN prior_archive IS NULL THEN NULL ELSE (
            SELECT count(*) FROM public.stewardship_campaign_archive_family a
            JOIN public.stewardship_campaign_archive x ON x.id=a.archive_id AND x.state='active'
            WHERE a.archive_id=prior_archive AND a.family_id_system='parishsoft_duid'
              AND a.family_id IN (SELECT family_duid FROM latest)) END,
        'prior_families', CASE WHEN prior_archive IS NULL THEN NULL ELSE (
            SELECT count(*) FROM public.stewardship_campaign_archive_family a
            JOIN public.stewardship_campaign_archive x ON x.id=a.archive_id AND x.state='active'
            WHERE a.archive_id=prior_archive AND a.family_id_system='parishsoft_duid') END
    ) INTO result;
    IF (result->>'responded')::bigint IS DISTINCT FROM observed.cumulative_responses THEN
        RAISE EXCEPTION 'Campaign comparison figures disagree with their fact row'
            USING ERRCODE='23514';
    END IF;
    RETURN result;
END $$;
ALTER FUNCTION public.stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid) SECURITY DEFINER;

CREATE OR REPLACE FUNCTION public.stewardship_campaign_archive_renewal_v1(
    archive uuid, prior_archive uuid, through_day integer)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user NOT IN ('pk_stewardship_web','pk_stewardship_worker') AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web and worker logins read campaign comparison figures' USING ERRCODE='42501';
    END IF;
    -- Both imports must be active; pending previews and tombstones never count.
    IF (SELECT count(*) FROM public.stewardship_campaign_archive
        WHERE id IN (archive, prior_archive) AND state='active')<>2 OR archive=prior_archive THEN
        RETURN NULL;
    END IF;
    RETURN jsonb_build_object(
        'renewed', (SELECT count(*) FROM public.stewardship_campaign_archive_family a
                    WHERE a.archive_id=archive AND a.first_campaign_day<=through_day
                      AND EXISTS (SELECT 1 FROM public.stewardship_campaign_archive_family b
                                  WHERE b.archive_id=prior_archive
                                    AND b.family_id_system=a.family_id_system
                                    AND b.family_id=a.family_id)),
        'prior_families', (SELECT count(*) FROM public.stewardship_campaign_archive_family b
                           WHERE b.archive_id=prior_archive));
END $$;
ALTER FUNCTION public.stewardship_campaign_archive_renewal_v1(uuid, uuid, integer) SECURITY DEFINER;

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_attribute
        WHERE attrelid='public.stewardship_daily_digest_ready'::regclass AND NOT attisdropped
          AND NOT attnotnull AND atthasdef IS FALSE
          AND ((attname='comparison' AND atttypid='jsonb'::regtype)
            OR (attname IN ('comparison_families_chart','comparison_pledges_chart')
                AND atttypid='bytea'::regtype)))<>3
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_daily_digest_ready'::regclass
             AND conname='daily_digest_comparison_charts' AND contype='c' AND convalidated
             AND pg_get_constraintdef(oid) LIKE '%(comparison IS NOT NULL) AND (comparison_families_chart IS NOT NULL)%') THEN
        RAISE EXCEPTION 'Migration 0072: the digest comparison columns are not installed as declared';
    END IF;
    -- The context allowlist admits the renderer's timeout values, still
    -- refuses unknown ones, and still admits the earlier contexts (the
    -- body is whole, not just this change): 0059's archive words and
    -- 0065's Entered by Staff sorts.
    IF NOT public.stewardship_safe_context_v1(
           'timeout', '{"what": "digest_chart", "helper": "chart_render_worker",
                        "limit_seconds": 60, "elapsed_seconds": 61}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'timeout', '{"what": "archive_check", "helper": "archive_check_worker"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"outcome": "succeeded", "archive_year": 2026, "count": 12}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"report_sort": "staff_entered"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"report_sort": "-staff_entered"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"report_sort": "staff"}'::jsonb)
       OR public.stewardship_safe_context_v1('timeout', '{"what": "chart_render_worker"}'::jsonb)
       OR public.stewardship_safe_context_v1('timeout', '{"helper": "digest_chart"}'::jsonb) THEN
        RAISE EXCEPTION 'Migration 0072: stewardship_safe_context_v1 does not admit the chart timeout and the earlier contexts as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>1 THEN
        RAISE EXCEPTION 'Migration 0072: stewardship_safe_context_v1 is not installed as declared';
    END IF;
    -- Each comparison function is still a stable definer with its pinned
    -- search_path and no EXECUTE for PUBLIC, now admitting the worker login.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%session_user NOT IN (''pk_stewardship_web'',''pk_stewardship_worker'')%'
          AND p.prosrc NOT LIKE '%session_user<>''pk_stewardship_web''%'
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND ((p.proname='stewardship_campaign_comparison_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'fact_set uuid, report_date date, band_edges numeric[], prior_archive uuid'
                AND p.prosrc LIKE '%DISTINCT FROM observed.cumulative_responses%'
                AND p.prosrc LIKE '%width_bucket(amount, band_edges) + 1%')
            OR (p.proname='stewardship_campaign_archive_renewal_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'archive uuid, prior_archive uuid, through_day integer'
                AND p.prosrc LIKE '%a.first_campaign_day<=through_day%')))<>2 THEN
        RAISE EXCEPTION 'Migration 0072: the campaign comparison functions are not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0072_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_safe_context_v1',
               'stewardship_campaign_comparison_v1','stewardship_campaign_archive_renewal_v1')) THEN
        RAISE EXCEPTION 'Migration 0072: the replaced functions lost or changed their grants';
    END IF;
END
$check$;
