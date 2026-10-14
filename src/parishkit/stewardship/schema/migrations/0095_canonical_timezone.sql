-- Frozen forward migration file 0095 (the repository-wide file sequence):
-- the campaign comparison reads a legacy time zone alias such as US/Eastern
-- (#1286). This file is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- The accepted-name catalog admits every IANA "backward" link name
-- (US/Eastern, Canada/Pacific, Japan, ...), and setup stores the name as
-- chosen. Debian's PostgreSQL image ships those links only in its separate
-- tzdata-legacy package, so pg_timezone_names lacks them, and every SQL
-- conversion of a stored zone must first map it to the zone it links to
-- with stewardship_timezone_name_v1 (the baseline's frozen table of all of
-- IANA 2026c's links). 0067 created stewardship_campaign_comparison_v1, and
-- 0072 replaced it, with the stored configuration zone applied as stored,
-- so a campaign configured US/Eastern raised 'time zone "US/Eastern" not
-- recognized' and the Campaign comparison page, its command and the daily
-- digest's comparison section were unavailable.
--
-- This file replaces stewardship_campaign_comparison_v1 with 0072's body
-- (the latest; it has no baseline copy) changing only the day's end, which
-- now applies the normalized name. It stays SECURITY DEFINER (declared in
-- the statement and repeated in an ALTER, as 0072 does) with its pinned
-- search_path, and keeps its grants. No stored configuration changes: the
-- stored name is only read differently.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's grants before the replacement, compared in the DO block.
-- A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0095_acl', (
    SELECT coalesce(p.proacl::text,'default')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_campaign_comparison_v1'), true);

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
    -- The day's end: the next local midnight in the fact set's time zone,
    -- named as PostgreSQL knows it (a legacy alias maps to its zone, #1286).
    day_end=(report_date + 1)::timestamp AT TIME ZONE public.stewardship_timezone_name_v1(selected.timezone);
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

DO $check$
BEGIN
    -- The function is still a stable definer with its pinned search_path,
    -- admitting the web and worker logins, with no EXECUTE for PUBLIC; its
    -- day's end applies the normalized name, never the stored one.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_campaign_comparison_v1'
          AND pg_get_function_identity_arguments(p.oid)=
              'fact_set uuid, report_date date, band_edges numeric[], prior_archive uuid'
          AND p.prosecdef AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND p.prosrc LIKE '%session_user NOT IN (''pk_stewardship_web'',''pk_stewardship_worker'')%'
          AND p.prosrc LIKE '%DISTINCT FROM observed.cumulative_responses%'
          AND p.prosrc LIKE '%width_bucket(amount, band_edges) + 1%'
          AND p.prosrc LIKE '%AT TIME ZONE public.stewardship_timezone_name_v1(selected.timezone);%'
          AND p.prosrc NOT LIKE '%AT TIME ZONE selected.timezone%')<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_comparison_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0095: the campaign comparison function is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0095_acl', true) IS DISTINCT FROM (
           SELECT coalesce(p.proacl::text,'default')
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_comparison_v1') THEN
        RAISE EXCEPTION 'Migration 0095: the campaign comparison function lost or changed its grants';
    END IF;
    -- The alias that broke the report now names a zone this server knows.
    IF NOT EXISTS (SELECT 1 FROM pg_timezone_names
                   WHERE name=public.stewardship_timezone_name_v1('US/Eastern')) THEN
        RAISE EXCEPTION 'Migration 0095: US/Eastern does not map to a zone this server knows';
    END IF;
END
$check$;
