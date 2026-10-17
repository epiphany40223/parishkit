-- Frozen forward migration file 0110 (the repository-wide file sequence):
-- the Financial stewardship response-date filters in the viewer's browser
-- time zone (#558). This file is installed by its Django migration in the
-- reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copy of the baseline function equals the fresh-install baseline's.
-- A fresh install runs the baseline, the earlier files and then this; the
-- baseline already carries the replaced body, so the install ends in the
-- same catalog as an upgraded database.
--
-- Financial stewardship filters pledges by the day of the Family's first and
-- latest response (first_start, first_end, latest_start, latest_end). Until
-- now those were days in the campaign's time zone. Every date an Admin page
-- takes is now in the viewer's browser zone (the Administrator, 2026-10-04),
-- so the filters may carry that zone as a new 'zone' key, and a day then runs
-- from local midnight to the next local midnight there, through 0064's
-- stewardship_browser_day_start_v1, as the information and Ministry reports
-- do.
--
-- Replaced in place, copied whole from its newest definition (0094):
--   stewardship_financial_report_v1   (the baseline)
-- Only #558's change: 'zone' joins the closed filter keys as an optional
-- key, is required with a date and must be a catalog zone when present, and
-- the four date predicates use the zone's day bounds, computed once per call.
-- Filters without 'zone' keep the campaign-zone predicates unchanged, so a
-- release from before this file (a rollback) and every retained export
-- capture, which the export capture trigger re-reads with its own retained
-- filters, mean exactly what they meant before.
--
-- Why in place rather than a v2, as 0064 did: a v2 needs a new export
-- capture function and its trigger re-pointed, and the financial capture
-- function is replaced in place elsewhere (export discard, #1398); a copied
-- capture would drift from it. In place, the capture function and its
-- trigger are untouched and keep calling this selection.
--
-- The function is not SECURITY DEFINER, before or after, so there is no
-- ALTER FUNCTION ... SECURITY DEFINER to repeat; CREATE OR REPLACE keeps its
-- grants, owner and settings (search_path, jit off). The DO block at the end
-- refuses to commit unless the new body is installed with those attributes
-- unchanged, accepts zoned and zone-less filters, and refuses a date without
-- a zone. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The selection's grants, settings and owner before the replacement (with
-- how many definitions there are), compared in the DO block. Settings are
-- compared in sorted order: an installed database may hold them in the
-- order of a later ALTER FUNCTION ... SET. A transaction-local setting holds
-- them, not a temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0110_acl', (
    SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text,';'),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_financial_report_v1'), true);

-- stewardship_financial_report_v1: copied from its newest definition (0094);
-- only the 'zone' key and the date predicates changed.
CREATE OR REPLACE FUNCTION public.stewardship_financial_report_v1(
    -- A NULL page number returns the complete, unpaged result. The caller owns
    -- the page size and must state it, so its paging arithmetic cannot drift
    -- from the rows returned here; it is ignored for a complete result.
    campaign_uuid uuid, parameters jsonb, page_number integer, page_size integer
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; proof jsonb:=parameters->'proof';
    answer jsonb; bad boolean;
    -- Whether the dates are days in the viewer's browser zone (#558), and
    -- each date's bound there: From is the instant its day begins, Through
    -- the instant the next day begins (exclusive).
    zoned boolean:=false; first_from timestamptz; first_before timestamptz;
    latest_from timestamptz; latest_before timestamptz;
    money_text constant text:='^(0|[1-9][0-9]{0,8})(\.[0-9]{2})?$';
    -- Canonical lowercase text, so identities compare without a fallible cast.
    uuid_text constant text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    -- Each step relies only on what an earlier statement established. PL/pgSQL
    -- runs statements in order, which SQL does not promise for the terms of one
    -- condition: a container operator or a cast placed beside its own guard could
    -- run first and fail with its own error, echoing a filter value, instead of
    -- this closed refusal. An undecidable step refuses.
    bad:=jsonb_typeof(parameters) IS DISTINCT FROM 'object';
    IF NOT bad THEN
        bad:=coalesce(NOT parameters ?& ARRAY['filters','proof']
            OR parameters-ARRAY['filters','proof']<>'{}'::jsonb
            OR jsonb_typeof(f) IS DISTINCT FROM 'object'
            OR jsonb_typeof(proof) NOT IN ('null','object')
            OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
            OR (page_number IS NOT NULL
                AND (page_size IS NULL OR page_size NOT BETWEEN 1 AND 200)),true);
    END IF;
    IF NOT bad AND jsonb_typeof(proof)='object' THEN
        bad:=coalesce(NOT proof ?& ARRAY['snapshot','configuration']
            OR proof-ARRAY['snapshot','configuration']<>'{}'::jsonb
            OR jsonb_typeof(proof->'snapshot') IS DISTINCT FROM 'string'
            OR jsonb_typeof(proof->'configuration') IS DISTINCT FROM 'string'
            OR NOT proof->>'snapshot' ~ uuid_text
            OR NOT proof->>'configuration' ~ uuid_text,true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(NOT f ?& ARRAY['search','active','first_start','first_end',
                'latest_start','latest_end','pledge_min','pledge_max','amount',
                'frequency','share','sort']
            OR f-ARRAY['search','active','first_start','first_end','latest_start',
                'latest_end','pledge_min','pledge_max','amount','frequency','share',
                'sort','zone']<>'{}'::jsonb
            OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
            OR length(f->>'search')>200
            OR (f->>'share' NOT IN ('any','none') AND NOT f->>'share' ~ uuid_text)
            OR f->>'active' NOT IN ('any','active','inactive','unavailable')
            OR f->>'amount' NOT IN ('any','zero','nonzero','cannot_give')
            OR f->>'frequency'
                NOT IN ('any','none','weekly','monthly','quarterly','annual')
            OR f->>'sort'
                NOT IN ('name','name_desc','newest','oldest','pledge','pledge_desc',
                    'duid','duid_desc')
            OR EXISTS(SELECT 1 FROM jsonb_each_text(f) d
                WHERE d.key IN ('first_start','first_end','latest_start','latest_end')
                  AND d.value<>'' AND (NOT d.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                      OR NOT pg_input_is_valid(d.value,'date')))
            OR EXISTS(SELECT 1 FROM jsonb_each_text(f) d
                WHERE d.key IN ('pledge_min','pledge_max') AND d.value<>''
                  AND NOT d.value ~ money_text),true);
    END IF;
    IF NOT bad THEN
        -- Ranges, once every bound is known to be well formed. An absent bound
        -- becomes NULL, so no term here depends on another to be safe.
        bad:=coalesce(nullif(f->>'first_start','')>nullif(f->>'first_end',''),false)
            OR coalesce(nullif(f->>'latest_start','')>nullif(f->>'latest_end',''),false)
            OR coalesce(nullif(f->>'pledge_min','')::numeric
                >nullif(f->>'pledge_max','')::numeric,false);
    END IF;
    IF NOT bad AND f ? 'zone' THEN
        -- Days are the viewer's browser zone (#558): one is required with a
        -- date and is always one the server's time zone catalog knows.
        -- Filters without the key, from a release before migration 0110,
        -- keep the campaign-zone days.
        bad:=coalesce((f->>'first_start'<>'' OR f->>'first_end'<>''
                OR f->>'latest_start'<>'' OR f->>'latest_end'<>'')
                AND f->>'zone'='',true)
            OR coalesce(f->>'zone'<>'' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names
                WHERE name=stewardship_timezone_name_v1(f->>'zone')),true);
        zoned:=true;
    END IF;
    IF bad THEN
        RAISE EXCEPTION 'Invalid financial report parameters' USING ERRCODE='23514';
    END IF;
    IF zoned THEN
        -- Each bound once per call, never per row; a daylight-saving day is
        -- 23 or 25 hours long.
        first_from:=CASE WHEN f->>'first_start'<>'' THEN
            stewardship_browser_day_start_v1((f->>'first_start')::date,f->>'zone') END;
        first_before:=CASE WHEN f->>'first_end'<>'' THEN
            stewardship_browser_day_start_v1((f->>'first_end')::date+1,f->>'zone') END;
        latest_from:=CASE WHEN f->>'latest_start'<>'' THEN
            stewardship_browser_day_start_v1((f->>'latest_start')::date,f->>'zone') END;
        latest_before:=CASE WHEN f->>'latest_end'<>'' THEN
            stewardship_browser_day_start_v1((f->>'latest_end')::date+1,f->>'zone') END;
    END IF;

WITH selected AS MATERIALIZED (
    -- The campaign configuration row the proof names. It is not a submission's
    -- applied configuration version, which the response rows carry below.
    SELECT c.id,cc.id AS active_configuration_id,cc.name,cc.timezone,cc.values,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.*,s.generation AS source_generation,s.promoted_at AS source_as_of,
        statement_timestamp() AS observed_at,
        -- Source money is shown only with a completeness proof of exactly the
        -- snapshot and configuration selected above; any other proof withholds.
        CASE WHEN v.proven
            AND pg_input_is_valid(s.cursor->'load'->>'giving_as_of_date','date')
            THEN (s.cursor->'load'->>'giving_as_of_date')::date END AS giving_through,
        CASE WHEN v.proven THEN s.cursor->>'full_started_at' END AS giving_observed_at
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    CROSS JOIN LATERAL (SELECT coalesce(
        proof->>'snapshot'=x.source_id::text
        AND proof->>'configuration'=x.active_configuration_id::text,false)
        AS proven) v
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'financial'
), responses AS MATERIALIZED (
    SELECT s.id,s.submitted_at,s.family_version,s.annual_pledge,s.configuration_id,
        i.family_duid,first.submitted_at AS first_submitted_at,
        s.answers->'financial' AS financial,
        coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
            nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
            -- No name (NULL) for a Family the data no longer has: the page
            -- shows its ParishSoft status badge alone, and it sorts after
            -- the names (#1216). A present Family without a usable name
            -- reads so.
            CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS family_name,
        -- The surname that leads the shown name, as Python names it
        -- (migration 0045's shared functions); family_name above keeps its
        -- own trim for the returned rows.
        stewardship_family_surname_v1(p.canonical::jsonb,
            CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
            THEN (p.canonical::jsonb->'active')::boolean END AS active,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
            THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
        -- Whether Staff entered this effective response for the Family
        -- through Open form (#529, #794): a yes or no, never who.
        s.entered_by_id IS NOT NULL AS staff_entered
    FROM source x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id AND s.answers ? 'financial'
    JOIN stewardship_submission first ON first.id=i.first_live_submission_id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), head_names AS MATERIALIZED (
    -- Each Family's active heads as the shown name lists them, in DUID order
    -- (stewardship_family_head_part_v1), so search and sort match what the
    -- page shows (#960). One set-based join over every head, as in the
    -- directory selection, skipped when neither a search nor a name sort
    -- reads the name.
    SELECT r.family_duid,array_agg(
            stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
            ORDER BY h.head::bigint) AS parts
    FROM responses r CROSS JOIN source x
    CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
    JOIN stewardship_snapshot_member mm
        ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
    JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
    GROUP BY r.family_duid
), named AS MATERIALIZED (
    -- "Surname, A and B" (family_names.family_heads_name).
    SELECT r.*,stewardship_family_heads_name_v1(r.surname,n.parts) AS display_name
    FROM responses r LEFT JOIN head_names n ON n.family_duid=r.family_duid
), filtered AS MATERIALIZED (
    SELECT r.*,nullif(r.financial->>'frequency','') AS frequency
    FROM named r CROSS JOIN source x
    WHERE (f->>'search'='' OR position(lower(f->>'search') IN lower(r.display_name))>0
            OR position(f->>'search' IN r.family_duid::text)>0)
        AND (f->>'active'='any' OR (f->>'active'='active' AND r.active IS TRUE)
            OR (f->>'active'='inactive' AND r.active IS FALSE)
            OR (f->>'active'='unavailable' AND r.active IS NULL))
        -- Zoned (#558): from local midnight of the From day to local midnight
        -- after the Through day, in the viewer's zone. Otherwise the
        -- campaign-zone days, unchanged.
        AND (f->>'first_start'='' OR CASE WHEN zoned THEN r.first_submitted_at>=first_from
            ELSE (r.first_submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif(f->>'first_start','')::date END)
        AND (f->>'first_end'='' OR CASE WHEN zoned THEN r.first_submitted_at<first_before
            ELSE (r.first_submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif(f->>'first_end','')::date END)
        AND (f->>'latest_start'='' OR CASE WHEN zoned THEN r.submitted_at>=latest_from
            ELSE (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif(f->>'latest_start','')::date END)
        AND (f->>'latest_end'='' OR CASE WHEN zoned THEN r.submitted_at<latest_before
            ELSE (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif(f->>'latest_end','')::date END)
        AND (f->>'pledge_min'='' OR r.annual_pledge>=nullif(f->>'pledge_min','')::numeric)
        AND (f->>'pledge_max'='' OR r.annual_pledge<=nullif(f->>'pledge_max','')::numeric)
        -- "Zero" and "No frequency" mean a Family that pledged nothing, not one
        -- that cannot contribute; that answer has its own filter and count.
        AND (f->>'amount'='any' OR (f->>'amount'='zero' AND r.annual_pledge=0
                AND r.financial->'cannot_give' IS DISTINCT FROM 'true'::jsonb)
            OR (f->>'amount'='nonzero' AND r.annual_pledge>0)
            -- "Cannot contribute financially" (a zero pledge with that answer).
            OR (f->>'amount'='cannot_give' AND r.financial->'cannot_give'='true'::jsonb))
        AND (f->>'frequency'='any'
            OR (f->>'frequency'='none' AND nullif(r.financial->>'frequency','') IS NULL
                AND r.financial->'cannot_give' IS DISTINCT FROM 'true'::jsonb)
            OR r.financial->>'frequency'=f->>'frequency')
        AND (f->>'share'='any'
            OR (f->>'share'='none' AND r.financial->'shares'='{}'::jsonb)
            OR r.financial->'shares' ? (f->>'share'))
), page AS MATERIALIZED (
    -- A name sort orders by surname, then the whole shown name, as the
    -- directory does; the Family DUID is unique here, so it ends every order.
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN f->>'sort'='name' THEN lower(surname) END,
        CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
        CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
        CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
        CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
        CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
        CASE WHEN f->>'sort'='pledge' THEN annual_pledge END,
        CASE WHEN f->>'sort'='pledge_desc' THEN annual_pledge END DESC,
        CASE WHEN f->>'sort'='duid' THEN family_duid END,
        CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,
        lower(surname),lower(display_name),family_duid) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
    OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
), detail AS (
    SELECT r.ordinal,r.id,r.family_name,r.family_duid,r.active,r.submitted_at,
        r.first_submitted_at,r.family_version,r.staff_entered,
        -- Money crosses JSON only as canonical text: a JSON number would be
        -- parsed as a binary float and lose exactness.
        r.annual_pledge::numeric(24,2)::text AS annual_pledge,r.frequency,
        -- Responses recorded before this answer existed read as false.
        coalesce(r.financial->'cannot_give'='true'::jsonb,false) AS cannot_give,
        r.financial->'shares' AS shares,
        -- Option wording is versioned with the configuration the Family saw; the
        -- application words it with the Family form's own rule.
        r.configuration_id,
        g.pledge_total::numeric(24,2)::text AS pledge_total,
        g.contribution_total::numeric(24,2)::text AS contribution_total
    FROM page r CROSS JOIN source x
    LEFT JOIN LATERAL (
        -- Unavailable, never zero, without a proven complete giving read. With
        -- one, a Family with no matching source row really does total zero.
        SELECT CASE WHEN x.giving_through IS NOT NULL THEN coalesce((
                SELECT sum((p.canonical::jsonb->>'amount')::numeric)
                FROM stewardship_snapshot_pledge m
                JOIN stewardship_source_pledge p ON p.id=m.payload_id
                WHERE m.snapshot_id=x.source_id AND p.family_key=r.family_duid::text
                    AND x.values->'financial'->'comparison_fund_duids'
                        @> to_jsonb(p.fund_key::bigint)
                    AND (p.canonical::jsonb->>'effective_date')::date BETWEEN
                        (x.values->'financial'->>'comparison_start')::date
                        AND (x.values->'financial'->>'comparison_end')::date),0)
            END AS pledge_total,
            CASE WHEN x.giving_through IS NOT NULL THEN coalesce((
                SELECT sum((p.canonical::jsonb->>'amount')::numeric)
                FROM stewardship_snapshot_contribution m
                JOIN stewardship_source_contribution p ON p.id=m.payload_id
                WHERE m.snapshot_id=x.source_id AND p.family_key=r.family_duid::text
                    AND x.values->'financial'->'comparison_fund_duids'
                        @> to_jsonb(p.fund_key::bigint)
                    AND (p.canonical::jsonb->>'effective_date')::date BETWEEN
                        (x.values->'financial'->>'comparison_start')::date
                        AND least((x.values->'financial'->>'comparison_end')::date,
                            x.giving_through)),0)
            END AS contribution_total
    ) g ON true
)
SELECT CASE WHEN NOT z.values->'modules' ? 'financial'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'comparison_start',x.values->'financial'->>'comparison_start',
        'comparison_end',x.values->'financial'->>'comparison_end',
        'giving_through',x.giving_through,'giving_observed_at',x.giving_observed_at),
    'summary',(SELECT jsonb_build_object('families',count(*),
        'annual_total',coalesce(sum(annual_pledge),0)::numeric(24,2)::text,
        'frequencies',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT coalesce(frequency,'none') AS k,count(*) AS n FROM filtered
            WHERE financial->'cannot_give' IS DISTINCT FROM 'true'::jsonb
            GROUP BY 1) d),'{}'::jsonb),
        'shares',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT s.key AS k,count(*) AS n FROM filtered q
            CROSS JOIN LATERAL jsonb_object_keys(q.financial->'shares') s(key)
            GROUP BY 1) d),'{}'::jsonb),
        'no_share',count(*) FILTER (WHERE financial->'shares'='{}'::jsonb),
        'cannot_give',count(*) FILTER (WHERE financial->'cannot_give'='true'::jsonb))
        FROM filtered),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
INTO answer FROM selected z LEFT JOIN source x ON true;
    RETURN answer;
END $$;

DO $check$
DECLARE
    nil constant uuid:='00000000-0000-0000-0000-000000000000';
    neutral constant jsonb:=jsonb_build_object('search','','active','any',
        'first_start','','first_end','','latest_start','','latest_end','',
        'pledge_min','','pledge_max','','amount','any','frequency','any',
        'share','any','sort','name');
    refused boolean;
BEGIN
    -- The one selection, invoker, stable, plpgsql, with its arguments and
    -- #558's zone bounds and the campaign-zone predicates it keeps.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_financial_report_v1'
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
          AND p.prorettype='jsonb'::regtype
          AND pg_get_function_arguments(p.oid)
              ='campaign_uuid uuid, parameters jsonb, page_number integer, page_size integer'
          AND p.prosrc LIKE '%''sort'',''zone'']<>''{}''::jsonb%'
          AND p.prosrc LIKE '%stewardship_browser_day_start_v1((f->>''latest_end'')::date+1,f->>''zone'')%'
          AND p.prosrc LIKE '%CASE WHEN zoned THEN r.submitted_at<latest_before%'
          AND p.prosrc LIKE '%CASE WHEN zoned THEN r.first_submitted_at>=first_from%'
          AND p.prosrc LIKE '%<=nullif(f->>''latest_end'','''')::date END)%')<>1 THEN
        RAISE EXCEPTION 'Migration 0110 (Financial stewardship dates in the browser zone) did not replace the selection';
    END IF;
    -- CREATE OR REPLACE keeps the grants, owner and settings; check that
    -- nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
                ||'/'||p.proowner::regrole::text,';'),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_financial_report_v1')
        IS DISTINCT FROM current_setting('stewardship.migration_0110_acl') THEN
        RAISE EXCEPTION 'Migration 0110 changed the selection''s grants, owner or settings';
    END IF;
    -- The installed selection reads both forms of filters: zone-less ones
    -- (an older release, a retained capture) and zoned dates. No campaign
    -- has the nil id, so each answers NULL after validating its filters.
    PERFORM public.stewardship_financial_report_v1(nil,
        jsonb_build_object('filters',neutral,'proof',NULL),1,50);
    PERFORM public.stewardship_financial_report_v1(nil,
        jsonb_build_object('filters',neutral||jsonb_build_object('first_start','2026-03-08',
            'latest_end','2026-11-01','zone','America/Los_Angeles'),'proof',NULL),1,50);
    -- A date without a zone, when the key is sent, is refused.
    refused:=false;
    BEGIN
        PERFORM public.stewardship_financial_report_v1(nil,
            jsonb_build_object('filters',neutral||jsonb_build_object('first_start','2026-03-08',
                'zone',''),'proof',NULL),1,50);
    EXCEPTION WHEN check_violation THEN refused:=true;
    END;
    IF NOT refused THEN
        RAISE EXCEPTION 'Migration 0110 selection accepts a date without its time zone';
    END IF;
END
$check$;
