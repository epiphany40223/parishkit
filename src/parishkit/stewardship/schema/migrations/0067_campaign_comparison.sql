-- Frozen forward migration file 0067 (the repository-wide file sequence):
-- the campaign comparison report's figures that need each Family's own rows
-- (#1018 slice 3; campaign-history spec, "Metrics"). It is installed by the
-- Django migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest.
--
-- Two new functions, each returning aggregates only, never a Family row,
-- identifier or single pledge:
--   stewardship_campaign_comparison_v1   the current campaign at the end of
--                                        one day of a ready "Historical as
--                                        of day" fact set: the Families
--                                        responded and their pledge total
--                                        (the fact row's own figures, which
--                                        the caller compares as a check),
--                                        the Families pledging more than
--                                        zero, their quartiles (Excel's
--                                        QUARTILE.INC, exact numeric, cents
--                                        half up), their pledge bands, and
--                                        how many of one imported year's
--                                        Families (by ParishSoft DUID) are
--                                        among the Families responded
--   stewardship_campaign_archive_renewal_v1  one imported year's Families
--                                        that responded by a campaign day
--                                        and were among another imported
--                                        year's Families, and how many that
--                                        other year kept
-- The current figures follow the participation graph's rule
-- (reports/calculations.py): the day's cohort is every Family first
-- eligible by the day's end from a ParishSoft load no newer than the day's
-- own; a Family responded when it has a live submission within the fact
-- set's watermark before the day's end, and its pledge is that of its
-- latest such submission by campaign sequence. The caller passes the
-- day's end instant (campaigns.intervals.local_day, which resolves a
-- daylight-saving gap as the graph does) and the band edges
-- (archive.workbook.BAND_EDGES, the one definition of the band set).
--
-- Both are SECURITY DEFINER with a fixed search_path: the web login reads
-- only the id and archive of a kept archive Family row, so the renewal
-- count is made here, inside the database, as the specification requires.
-- EXECUTE is revoked from PUBLIC; database-grants gives it to the web login
-- alone (runtime_grants.runtime_functions), and each function refuses any
-- other session but the schema owner's. No existing function changes, so
-- no earlier body is copied here. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_campaign_comparison_v1(
    fact_set uuid, report_date date, day_end timestamp with time zone,
    band_edges numeric[], prior_archive uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    selected record;
    generation bigint;
    result jsonb;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web login reads campaign comparison figures' USING ERRCODE='42501';
    END IF;
    IF day_end IS NULL OR band_edges IS NULL OR cardinality(band_edges) NOT BETWEEN 1 AND 20
       OR array_position(band_edges, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'Campaign comparison needs a day end and band edges' USING ERRCODE='22023';
    END IF;
    -- Only a ready historical generation, and only a day it observed.
    SELECT s.campaign_id, s.submission_watermark INTO selected
    FROM stewardship_daily_fact_set s
    WHERE s.id=fact_set AND s.state='ready' AND s.population_scope='historical';
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT d.source_generation INTO generation FROM stewardship_daily_fact d
    WHERE d.fact_set_id=fact_set AND d.local_date=report_date AND d.population_available;
    IF generation IS NULL THEN
        RETURN NULL;
    END IF;
    WITH cohort AS (
        SELECT f.id, f.family_duid FROM stewardship_family_campaign f
        WHERE f.campaign_id=selected.campaign_id AND f.first_eligible_at IS NOT NULL
          AND f.first_eligible_source_generation<=generation
          AND f.first_eligible_at<day_end
    ), latest AS (
        -- Each responding Family's latest live version by the day's end.
        SELECT DISTINCT ON (r.family_id) c.family_duid, r.annual_pledge
        FROM stewardship_submission r JOIN cohort c ON c.id=r.family_id
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
            SELECT count(*) FROM stewardship_campaign_archive_family a
            JOIN stewardship_campaign_archive x ON x.id=a.archive_id AND x.state='active'
            WHERE a.archive_id=prior_archive AND a.family_id_system='parishsoft_duid'
              AND a.family_id IN (SELECT family_duid FROM latest)) END,
        'prior_families', CASE WHEN prior_archive IS NULL THEN NULL ELSE (
            SELECT count(*) FROM stewardship_campaign_archive_family a
            JOIN stewardship_campaign_archive x ON x.id=a.archive_id AND x.state='active'
            WHERE a.archive_id=prior_archive AND a.family_id_system='parishsoft_duid') END
    ) INTO result;
    RETURN result;
END $$;
ALTER FUNCTION public.stewardship_campaign_comparison_v1(uuid, date, timestamp with time zone, numeric[], uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_campaign_comparison_v1(uuid, date, timestamp with time zone, numeric[], uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_campaign_archive_renewal_v1(
    archive uuid, prior_archive uuid, through_day integer)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web login reads campaign comparison figures' USING ERRCODE='42501';
    END IF;
    -- Both imports must be active; pending previews and tombstones never count.
    IF (SELECT count(*) FROM stewardship_campaign_archive
        WHERE id IN (archive, prior_archive) AND state='active')<>2 OR archive=prior_archive THEN
        RETURN NULL;
    END IF;
    RETURN jsonb_build_object(
        'renewed', (SELECT count(*) FROM stewardship_campaign_archive_family a
                    WHERE a.archive_id=archive AND a.first_campaign_day<=through_day
                      AND EXISTS (SELECT 1 FROM stewardship_campaign_archive_family b
                                  WHERE b.archive_id=prior_archive
                                    AND b.family_id_system=a.family_id_system
                                    AND b.family_id=a.family_id)),
        'prior_families', (SELECT count(*) FROM stewardship_campaign_archive_family b
                           WHERE b.archive_id=prior_archive));
END $$;
ALTER FUNCTION public.stewardship_campaign_archive_renewal_v1(uuid, uuid, integer) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_campaign_archive_renewal_v1(uuid, uuid, integer) FROM PUBLIC;

DO $check$
BEGIN
    -- Each function is installed once, as declared: definer, stable, its
    -- search_path pinned, its caller check and aggregates in place, and no
    -- EXECUTE for PUBLIC.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%session_user<>''pk_stewardship_web''%'
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND ((p.proname='stewardship_campaign_comparison_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'fact_set uuid, report_date date, day_end timestamp with time zone, '
                    'band_edges numeric[], prior_archive uuid'
                AND p.prosrc LIKE '%ORDER BY r.family_id, r.campaign_sequence DESC%'
                AND p.prosrc LIKE '%width_bucket(amount, band_edges) + 1%')
            OR (p.proname='stewardship_campaign_archive_renewal_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'archive uuid, prior_archive uuid, through_day integer'
                AND p.prosrc LIKE '%a.first_campaign_day<=through_day%')))<>2
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_campaign_comparison_v1',
               'stewardship_campaign_archive_renewal_v1'))<>2 THEN
        RAISE EXCEPTION 'Migration 0067 (campaign comparison) is not installed as declared';
    END IF;
END
$check$;
