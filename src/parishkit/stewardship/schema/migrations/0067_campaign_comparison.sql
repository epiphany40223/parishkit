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
-- latest such submission by campaign sequence. The day's end is derived
-- here, from the report date and the time zone of the fact set's own
-- configuration, never taken from the caller, and the function refuses
-- unless its Families responded equal the fact row's cumulative responses:
-- with the cohort and the responses both growing with time, equal counts
-- mean the same Families, so no caller can move the cutoff to learn
-- whether one Family is among last year's. (PostgreSQL resolves a local
-- midnight inside a daylight-saving change as its own rules say; in the
-- rare zone whose clocks change at midnight that could differ from the
-- graph's cutoff, and the check then refuses rather than misreport.) The
-- caller passes the band edges (archive.workbook.BAND_EDGES, the one
-- definition of the band set), which must be positive and increasing.
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
    fact_set uuid, report_date date, band_edges numeric[], prior_archive uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    selected record;
    observed record;
    day_end timestamp with time zone;
    result jsonb;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the web login reads campaign comparison figures' USING ERRCODE='42501';
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
REVOKE ALL ON FUNCTION public.stewardship_campaign_comparison_v1(uuid, date, numeric[], uuid) FROM PUBLIC;

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
                    'fact_set uuid, report_date date, band_edges numeric[], prior_archive uuid'
                AND p.prosrc LIKE '%ORDER BY r.family_id, r.campaign_sequence DESC%'
                AND p.prosrc LIKE '%AT TIME ZONE selected.timezone%'
                AND p.prosrc LIKE '%DISTINCT FROM observed.cumulative_responses%'
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
