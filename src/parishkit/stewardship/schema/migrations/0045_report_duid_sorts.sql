-- Frozen forward migration file 0045 (the repository-wide file sequence):
-- the SQL-ordered report tables sort by their DUID columns, and their Family
-- name sort and search use the name the pages show (#960). This file is
-- installed by its Django migration in the reports app and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that its copy of each replaced function equals the
-- fresh-install baseline's (schema/financial_reports.sql,
-- information_reports.sql, ministry_reports.sql and ministry_exports.sql). A
-- fresh install runs the baseline, the earlier files and then this; the
-- baseline already carries these bodies, so the install ends in the same
-- catalog as an upgraded database.
--
-- The Administrator's rule (#932) is that a table with a Family name column
-- and a DUID column sorts by both. Three report tables are ordered by these
-- selections, which accept a closed set of sort tokens, so their DUID
-- columns could not sort. Five functions change, each copied whole from its
-- latest definition (the baseline; none was replaced by an earlier frozen
-- file) with only these changes:
--   stewardship_financial_report_v1   sort tokens 'duid' and 'duid_desc'
--                                     (Family DUID); name search and sort use
--                                     the shown name
--   stewardship_information_report_v1 the same; the shown name and heads are
--                                     stripped from the returned rows
--   stewardship_talent_report_v1      Member rows return member_duid (NULL
--                                     for a Member added on the form); search
--                                     and the SQL order use the shown name
--   stewardship_ministry_report_v1    'duid' and 'duid_desc' sort the
--                                     summary by Ministry DUID and one
--                                     Ministry's requests by Member DUID, a
--                                     Member added on the form last either
--                                     way; v2 calls it unchanged
--   stewardship_ministry_export_capture_v1  accepts those two tokens, so an
--                                     export keeps the page's order
-- The shown name is family_names.family_heads_name's: the surname, then the
-- active heads' first names ("Squyres, Tracy and Jeff"), built as the
-- directory selection builds it. A name sort orders by surname and then that
-- name; every order keeps its unique tiebreak. Each function keeps its
-- signature, attributes and grants: none is SECURITY DEFINER, before or
-- after, so there is no ALTER FUNCTION to repeat, and the DO block below
-- checks prosecdef, the settings and the ACL recorded before the
-- replacement. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants before the replacement (with how many
-- definitions there are), compared in the DO block. A transaction-local
-- setting holds them, not a temporary table: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0045_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_financial_report_v1',
        'stewardship_information_report_v1','stewardship_talent_report_v1',
        'stewardship_ministry_report_v1','stewardship_ministry_export_capture_v1')), true);

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
                'sort']<>'{}'::jsonb
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
    IF bad THEN
        RAISE EXCEPTION 'Invalid financial report parameters' USING ERRCODE='23514';
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
            'Unavailable Family') AS family_name,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
            THEN (p.canonical::jsonb->'active')::boolean END AS active,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
            THEN p.canonical::jsonb->'active_head_duids' END AS head_duids
    FROM source x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id AND s.answers ? 'financial'
    JOIN stewardship_submission first ON first.id=i.first_live_submission_id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), head_names AS MATERIALIZED (
    -- The Family as the page names it (family_names.family_heads_name): the
    -- surname, then its active heads' first names, a head of another surname
    -- in full, so search and sort match what the page shows (#960). One
    -- set-based join over every head, as in the directory selection.
    SELECT r.family_duid,array_agg(n.part ORDER BY h.head::bigint)
        FILTER (WHERE n.part<>'') AS parts
    FROM responses r CROSS JOIN source x
    CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
    JOIN stewardship_snapshot_member mm
        ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
    JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    -- Each head's record is parsed once (OFFSET 0 keeps the subquery
    -- from being flattened into one parse per expression).
    CROSS JOIN LATERAL (SELECT btrim(coalesce(v->>'firstName','')),
        btrim(coalesce(v->>'lastName',''))
        FROM (SELECT sm.canonical::jsonb AS v OFFSET 0) j
        WHERE v->'active'='true'::jsonb) t(first,last)
    CROSS JOIN LATERAL (SELECT CASE WHEN t.last=r.family_name THEN t.first
        ELSE concat_ws(' ',nullif(t.first,''),nullif(t.last,'')) END) n(part)
    GROUP BY r.family_duid
), named AS MATERIALIZED (
    -- "A", "A and B", "A, B and C" after the surname (family_names.name_series).
    SELECT r.*,r.family_name||coalesce(', '||(SELECT CASE WHEN cardinality(n.parts)<3
            THEN array_to_string(n.parts,' and ')
            ELSE array_to_string(n.parts[1:cardinality(n.parts)-1],', ')
                ||' and '||n.parts[cardinality(n.parts)] END),'') AS display_name
    FROM responses r LEFT JOIN head_names n ON n.family_duid=r.family_duid
), filtered AS MATERIALIZED (
    SELECT r.*,nullif(r.financial->>'frequency','') AS frequency
    FROM named r CROSS JOIN source x
    WHERE (f->>'search'='' OR position(lower(f->>'search') IN lower(r.display_name))>0
            OR position(f->>'search' IN r.family_duid::text)>0)
        AND (f->>'active'='any' OR (f->>'active'='active' AND r.active IS TRUE)
            OR (f->>'active'='inactive' AND r.active IS FALSE)
            OR (f->>'active'='unavailable' AND r.active IS NULL))
        AND (f->>'first_start'='' OR (r.first_submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif(f->>'first_start','')::date)
        AND (f->>'first_end'='' OR (r.first_submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif(f->>'first_end','')::date)
        AND (f->>'latest_start'='' OR (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif(f->>'latest_start','')::date)
        AND (f->>'latest_end'='' OR (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif(f->>'latest_end','')::date)
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
        CASE WHEN f->>'sort'='name' THEN lower(family_name) END,
        CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
        CASE WHEN f->>'sort'='name_desc' THEN lower(family_name) END DESC,
        CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
        CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
        CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
        CASE WHEN f->>'sort'='pledge' THEN annual_pledge END,
        CASE WHEN f->>'sort'='pledge_desc' THEN annual_pledge END DESC,
        CASE WHEN f->>'sort'='duid' THEN family_duid END,
        CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,
        lower(family_name),lower(display_name),family_duid) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
    OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
), detail AS (
    SELECT r.ordinal,r.id,r.family_name,r.family_duid,r.active,r.submitted_at,
        r.first_submitted_at,r.family_version,
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

CREATE OR REPLACE FUNCTION public.stewardship_information_report_v1(
    campaign uuid, parameters jsonb, page_number integer DEFAULT NULL,
    item_uuid uuid DEFAULT NULL, page_size integer DEFAULT 50
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters ?& ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','disposition','needed','completed','start','end','sort']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'disposition' NOT IN ('current_actionable','superseded','withdrawn','all')
       OR f->>'needed' NOT IN ('any','yes','no')
       OR f->>'completed' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('newest','oldest','name','name_desc','duid','duid_desc')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'Invalid information report parameters' USING ERRCODE='23514'; END IF;
    -- Python validates the same canonical dates before reaching SQL. The SQL
    -- owner also rejects alternate spellings on direct restricted-role writes.
    IF (f->>'start'<>'' AND (f->>'start')::date::text<>f->>'start')
       OR (f->>'end'<>'' AND (f->>'end')::date::text<>f->>'end')
       OR (f->>'start'<>'' AND f->>'end'<>'' AND f->>'start'>f->>'end')
    THEN RAISE EXCEPTION 'Invalid information report interval' USING ERRCODE='23514'; END IF;

    WITH selected AS MATERIALIZED (
        SELECT c.id,cc.name,cc.timezone,cc.id AS timezone_configuration_id,
            sc.snapshot_id AS source_id,ss.generation AS source_generation,
            ss.promoted_at AS source_as_of,statement_timestamp() AS observed_at
        FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        JOIN stewardship_source_current sc ON sc.singleton
        JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
            AND ss.state='promoted' AND ss.compacted_at IS NULL
        WHERE c.id=campaign
    ), history AS MATERIALIZED (
        SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value FROM selected
    ), rows AS MATERIALIZED (
        SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
            i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
            coalesce(u.email,'Former portal user') AS completed_by,
            coalesce(r.notes,'') AS notes,s.submitted_at,fam.family_duid,
            coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
                nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
                nullif(btrim(concat_ws(' ',
                    nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                    nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                'Family') AS family_name,
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            h.value->'reported' ? i.id::text AS previously_reported,
            h.value->'corrected' @>
                jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
                AS correction_resolved
        FROM selected x CROSS JOIN history h
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_additional_information i ON i.submission_id=s.id
        JOIN stewardship_family_campaign fam ON fam.id=s.family_id
        LEFT JOIN stewardship_snapshot_family m
            ON m.snapshot_id=x.source_id AND m.source_key=fam.family_duid::text
        LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
        LEFT JOIN LATERAL (
            SELECT notes,followed_up_by_id FROM stewardship_information_revision
            WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
        LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
        WHERE (item_uuid IS NULL OR i.id=item_uuid)
          AND (f->>'start'='' OR (s.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date>=(f->>'start')::date)
          AND (f->>'end'='' OR (s.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date<=(f->>'end')::date)
    ), head_names AS MATERIALIZED (
        -- The Family as the page names it (family_names.family_heads_name):
        -- the surname, then its active heads' first names, a head of another
        -- surname in full, so search and sort match what the page shows
        -- (#960). One set-based join over each Family's heads.
        SELECT r.family_duid,array_agg(n.part ORDER BY h.head::bigint)
            FILTER (WHERE n.part<>'') AS parts
        FROM (SELECT DISTINCT family_duid,family_name,head_duids FROM rows) r
        CROSS JOIN selected x
        CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
        JOIN stewardship_snapshot_member mm
            ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
        -- Each head's record is parsed once (OFFSET 0 keeps the subquery
        -- from being flattened into one parse per expression).
        CROSS JOIN LATERAL (SELECT btrim(coalesce(v->>'firstName','')),
            btrim(coalesce(v->>'lastName',''))
            FROM (SELECT sm.canonical::jsonb AS v OFFSET 0) j
            WHERE v->'active'='true'::jsonb) t(first,last)
        CROSS JOIN LATERAL (SELECT CASE WHEN t.last=r.family_name THEN t.first
            ELSE concat_ws(' ',nullif(t.first,''),nullif(t.last,'')) END) n(part)
        GROUP BY r.family_duid
    ), named AS MATERIALIZED (
        -- "A", "A and B", "A, B and C" after the surname (name_series).
        SELECT r.*,r.family_name||coalesce(', '||(SELECT CASE WHEN cardinality(n.parts)<3
                THEN array_to_string(n.parts,' and ')
                ELSE array_to_string(n.parts[1:cardinality(n.parts)-1],', ')
                    ||' and '||n.parts[cardinality(n.parts)] END),'') AS display_name
        FROM rows r LEFT JOIN head_names n ON n.family_duid=r.family_duid
    ), filtered AS MATERIALIZED (
        SELECT * FROM named WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(display_name))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        -- A name sort orders by surname, then the whole shown name, as the
        -- directory does; the item id is the unique tiebreak.
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(family_name) END,
            CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(family_name) END DESC,
            CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
            CASE WHEN f->>'sort'='duid' THEN family_duid END,
            CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,id) AS ordinal
        FROM filtered
    ), page AS (
        SELECT * FROM ordered ORDER BY ordinal
        LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
        OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
    ), detached AS (
        -- The shown name and heads only order and search; the page and an
        -- export capture keep the row they always had.
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','display_name']
            || jsonb_build_object('history',
            CASE WHEN (parameters->>'history')::boolean THEN coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version',r.expected_version+1,'created_at',r.created_at,
                    'actor_id',r.actor_id,'actor_label',coalesce(a.email,'Former portal user'),
                    'follow_up_needed',r.follow_up_needed,'followed_up',r.followed_up,
                    'followed_up_at',r.followed_up_at,'followed_up_by_id',r.followed_up_by_id,
                    'completed_by',coalesce(u.email,'Former portal user'),'notes',r.notes)
                    ORDER BY r.expected_version)
                FROM stewardship_information_revision r
                LEFT JOIN stewardship_portal_user a ON a.id=r.actor_id
                LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
                WHERE r.item_id=page.id AND r.expected_version<page.version
            ),'[]'::jsonb) ELSE '[]'::jsonb END) AS value FROM page
    )
    SELECT jsonb_build_object('metadata',to_jsonb(x),
        'total',(SELECT count(*) FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_talent_report_v1(campaign_uuid uuid, parameters jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT off, as in the information report above.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE answer jsonb; bad boolean;
    uuid_text constant text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    -- Statements run in order, so each check relies only on earlier ones.
    bad:=jsonb_typeof(parameters) IS DISTINCT FROM 'object';
    IF NOT bad THEN
        bad:=coalesce(NOT parameters ?& ARRAY['search','talent']
            OR parameters-ARRAY['search','talent']<>'{}'::jsonb
            OR jsonb_typeof(parameters->'search') IS DISTINCT FROM 'string'
            OR jsonb_typeof(parameters->'talent') IS DISTINCT FROM 'string',true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(length(parameters->>'search')>200
            OR (parameters->>'talent' NOT IN ('any','cannot_serve','cannot_attend')
                AND NOT parameters->>'talent' ~ uuid_text),true);
    END IF;
    IF bad THEN
        RAISE EXCEPTION 'Invalid talent report parameters' USING ERRCODE='23514';
    END IF;
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.values,sc.snapshot_id AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), responses AS MATERIALIZED (
    SELECT s.id,s.submitted_at,s.answers,i.family_duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
            nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            'Unavailable Family') AS family_name,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
            THEN p.canonical::jsonb->'active_head_duids' END AS head_duids
    FROM selected x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), head_names AS MATERIALIZED (
    -- The Family as the page names it (family_names.family_heads_name): the
    -- surname, then its active heads' first names, a head of another surname
    -- in full, so search and the SQL order match what the page shows (#960).
    SELECT r.family_duid,array_agg(n.part ORDER BY h.head::bigint)
        FILTER (WHERE n.part<>'') AS parts
    FROM responses r CROSS JOIN selected x
    CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
    JOIN stewardship_snapshot_member mm
        ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
    JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    -- Each head's record is parsed once (OFFSET 0 keeps the subquery
    -- from being flattened into one parse per expression).
    CROSS JOIN LATERAL (SELECT btrim(coalesce(v->>'firstName','')),
        btrim(coalesce(v->>'lastName',''))
        FROM (SELECT sm.canonical::jsonb AS v OFFSET 0) j
        WHERE v->'active'='true'::jsonb) t(first,last)
    CROSS JOIN LATERAL (SELECT CASE WHEN t.last=r.family_name THEN t.first
        ELSE concat_ws(' ',nullif(t.first,''),nullif(t.last,'')) END) n(part)
    GROUP BY r.family_duid
), named AS MATERIALIZED (
    -- "A", "A and B", "A, B and C" after the surname (name_series).
    SELECT r.*,r.family_name||coalesce(', '||(SELECT CASE WHEN cardinality(n.parts)<3
            THEN array_to_string(n.parts,' and ')
            ELSE array_to_string(n.parts[1:cardinality(n.parts)-1],', ')
                ||' and '||n.parts[cardinality(n.parts)] END),'') AS display_name
    FROM responses r LEFT JOIN head_names n ON n.family_duid=r.family_duid
), people AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.display_name,r.submitted_at,g.grp,
        e.key AS member_key,
        -- A listed Member's ParishSoft DUID (#960); a Member the Family added
        -- on the form has none yet.
        CASE WHEN g.grp='members' AND e.key ~ '^[1-9][0-9]{0,9}$'
            THEN e.key::bigint END AS member_duid,
        coalesce(e.value->'cannot_serve'='true'::jsonb,false) AS cannot_serve,
        coalesce(e.value->'talents','{}'::jsonb) AS talents,
        -- The Family's own corrected name first, then the parish record.
        coalesce(nullif(btrim(concat_ws(' ',
                nullif(btrim(r.answers->g.grp->e.key->>'first_name'),''),
                nullif(btrim(r.answers->g.grp->e.key->>'last_name'),''))),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(sm.canonical::jsonb->>'firstName'),''),
                nullif(btrim(sm.canonical::jsonb->>'lastName'),''))),''),
            'Unavailable name') AS member_name
    FROM named r
    CROSS JOIN LATERAL (VALUES ('members'),('proposed_members')) g(grp)
    CROSS JOIN LATERAL jsonb_each(coalesce(r.answers->'service'->g.grp,'{}'::jsonb)) e
    CROSS JOIN selected x
    LEFT JOIN stewardship_snapshot_member mm
        ON g.grp='members' AND mm.snapshot_id=x.source_id AND mm.source_key=e.key
    LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    WHERE e.value->'cannot_serve'='true'::jsonb OR e.value->'talents'<>'{}'::jsonb
), members AS MATERIALIZED (
    SELECT * FROM people q
    WHERE (parameters->>'search'=''
            OR position(lower(parameters->>'search') IN lower(q.member_name))>0
            OR position(lower(parameters->>'search') IN lower(q.display_name))>0
            OR position(parameters->>'search' IN q.family_duid::text)>0)
      AND (parameters->>'talent'='any'
            OR (parameters->>'talent'='cannot_serve' AND q.cannot_serve)
            OR q.talents ? (parameters->>'talent'))
), families AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.display_name,r.submitted_at FROM named r
    WHERE r.answers->'cannot_attend'='true'::jsonb
      AND parameters->>'talent' IN ('any','cannot_attend')
      AND (parameters->>'search'=''
            OR position(lower(parameters->>'search') IN lower(r.display_name))>0
            OR position(parameters->>'search' IN r.family_duid::text)>0)
)
SELECT CASE WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'source_id',x.source_id),
    'summary',jsonb_build_object(
        'members',(SELECT count(*) FROM members),
        'cannot_serve',(SELECT count(*) FROM members WHERE cannot_serve),
        'cannot_attend',(SELECT count(*) FROM families),
        'talents',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT t.key AS k,count(*) AS n FROM members q
            CROSS JOIN LATERAL jsonb_object_keys(q.talents) t(key) GROUP BY 1) d),
            '{}'::jsonb)),
    'members',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',family_name,'family_duid',family_duid,
            'member_name',member_name,'member_duid',member_duid,
            'proposed',grp='proposed_members',
            'cannot_serve',cannot_serve,'talents',talents,'submitted_at',submitted_at)
        ORDER BY lower(family_name),lower(display_name),family_duid,lower(member_name),member_key)
        FROM members),'[]'::jsonb),
    'families',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',family_name,'family_duid',family_duid,
            'submitted_at',submitted_at)
        ORDER BY lower(family_name),lower(display_name),family_duid) FROM families),'[]'::jsonb)) END
INTO answer FROM (SELECT 1) one LEFT JOIN selected x ON true;
    RETURN answer;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_report_v1(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    ministry_id integer DEFAULT NULL, request_action text DEFAULT 'join',
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.timezone,cc.values,
        CASE WHEN c.state='archived' THEN cc.configuration_id
             ELSE sys.active_configuration_id END AS configuration_id,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    CROSS JOIN stewardship_system_configuration sys
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.*,s.organization_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,statement_timestamp() AS observed_at,
        (statement_timestamp() AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date AS report_date
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Configuration permits signed-64-bit IDs; source/requests accept only
    -- positive signed-32-bit DUIDs. Widen casts before filtering so planner
    -- predicate reordering cannot turn an unsupported draft ID into an outage.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        CASE WHEN p.canonical::jsonb->'catalog_present'='true'::jsonb
            THEN coalesce(a.active,true) ELSE NULL END AS active,
        n.in_campaign
    FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT r.ministry_duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        WHERE s.campaign_id=x.id AND s.mode='live'
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(r.ministry_duid)
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    LEFT JOIN stewardship_ministry_activity a
        ON a.configuration_id=x.configuration_id
        AND a.organization_id=x.organization_id AND a.ministry_duid=n.duid::bigint
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
      AND (ministry_id::integer IS NULL OR n.duid::bigint=ministry_id)
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,r.assignee_id,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), summary AS MATERIALIZED (
    SELECT m.*,
        count(r.id) FILTER (WHERE r.action='join') AS joining,
        count(r.id) FILTER (WHERE r.action='leave') AS leaving,
        count(r.id) FILTER (
            WHERE r.state IN ('new','assigned','in_progress')) AS unresolved,
        count(r.id) FILTER (
            WHERE r.state IN ('resolved','closed_no_response')) AS completed,
        count(r.id) AS requests
    FROM ministries m LEFT JOIN requests r ON r.ministry_duid=m.duid
        AND r.revision=1 AND r.state NOT IN ('cancelled','superseded')
    GROUP BY m.duid,m.name,m.active,m.in_campaign
), summary_filtered AS MATERIALIZED (
    SELECT * FROM summary WHERE
        ((filters->>'activity')='any' OR ((filters->>'activity')='active' AND active IS TRUE)
            OR ((filters->>'activity')='inactive' AND active IS FALSE)
            OR ((filters->>'activity')='unavailable' AND active IS NULL))
        AND (ministry_id::integer IS NOT NULL OR (filters->>'search')=''
            OR position(lower((filters->>'search')) IN lower(name))>0
            OR position((filters->>'search') IN duid::text)>0)
), named AS MATERIALIZED (
    SELECT r.*,m.name AS ministry_name,p.canonical::jsonb AS person,
        CASE WHEN r.entity_kind='proposed_member'
            THEN s.answers->'proposed_members'->r.entity_key ELSE NULL END AS proposed,
        coalesce(nullif(btrim(concat_ws(' ',
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'middleName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'middle_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'last_name'
                END)),''),
            'Unavailable Member') AS member_name
    FROM requests r JOIN summary_filtered m ON m.duid=r.ministry_duid
    CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    WHERE ministry_id::integer IS NOT NULL AND r.action=request_action
        AND ((filters->>'history'='all') OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved' AND r.state IN ('new','assigned','in_progress')))
        AND ((filters->>'start')='' OR (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif((filters->>'start'),'')::date)
        AND ((filters->>'end')='' OR (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif((filters->>'end'),'')::date)
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR (entity_kind='member' AND position((filters->>'search') IN entity_key)>0)
), page AS MATERIALIZED (
    -- A Member DUID sort lists Members the Family added on the form (no
    -- DUID yet) last in either direction (#960).
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(member_name) END DESC,
        CASE WHEN (filters->>'sort')='newest' THEN submitted_at END DESC,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        CASE WHEN (filters->>'sort')='duid' AND entity_kind='member'
            THEN entity_key::bigint END NULLS LAST,
        CASE WHEN (filters->>'sort')='duid_desc' AND entity_kind='member'
            THEN entity_key::bigint END DESC NULLS LAST,id) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.ministry_duid,r.member_name,r.entity_kind,
        CASE WHEN r.entity_kind='member' THEN r.entity_key::bigint END AS member_duid,
        CASE WHEN r.entity_kind='proposed_member' THEN r.entity_key END AS proposed_id,
        r.action,r.state,r.outcome,r.submitted_at,
        r.revision=1 AS latest,
        -- Resolved in this statement so an export captures who held the work
        -- as of its data, not whoever holds that identity when it renders.
        (SELECT u.email FROM stewardship_portal_user u
            WHERE u.id=r.assignee_id) AS assignee,
        CASE WHEN r.action='leave' THEN (
            SELECT string_agg(DISTINCT p.canonical::jsonb->>'ministryRoleName',', '
                ORDER BY p.canonical::jsonb->>'ministryRoleName')
            FROM stewardship_snapshot_roster m
            JOIN stewardship_source_roster p ON p.id=m.payload_id
            WHERE m.snapshot_id=x.source_id AND r.person IS NOT NULL
                AND p.canonical::jsonb->>'member_key'=r.entity_key
                AND p.canonical::jsonb->>'ministry_key'=r.ministry_duid::text
                AND p.canonical::jsonb->'current'='true'::jsonb
        ) END AS current_role,
        CASE WHEN r.action='join' THEN coalesce(
            r.person->>'sex',r.proposed->>'gender') END AS gender,
        CASE WHEN r.action='join' AND
            pg_input_is_valid(coalesce(r.person->>'birthdate',r.proposed->>'birth_date'),'date')
            THEN CASE WHEN coalesce(r.person->>'birthdate',
                r.proposed->>'birth_date')::date<=x.report_date
                THEN extract(year FROM age(x.report_date,
                    coalesce(r.person->>'birthdate',r.proposed->>'birth_date')::date))::integer
                END END AS age,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_email','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS email_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_email'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS phone_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_phone'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'phones'
                ELSE jsonb_build_object('home',r.proposed->>'home_phone',
                    'mobile',r.proposed->>'mobile_phone',
                    'work',r.proposed->>'work_phone') END END AS phones,
        a.fields AS address
    FROM page r CROSS JOIN source x
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb AS value FROM stewardship_snapshot_contact m
        JOIN stewardship_source_contact p ON p.id=m.payload_id
        WHERE r.action='join' AND r.person IS NOT NULL AND m.snapshot_id=x.source_id
            AND m.source_key='member:'||r.entity_key
    ) c ON true
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb->'fields' AS fields
        FROM stewardship_snapshot_address m
        JOIN stewardship_source_address p ON p.id=m.payload_id
        WHERE r.action='join' AND (r.person IS NOT NULL OR r.proposed IS NOT NULL)
            AND m.snapshot_id=x.source_id
            AND m.source_key='family:'||r.family_duid::text||':primary'
    ) a ON true
), summary_page AS (
    SELECT * FROM summary_filtered ORDER BY
        CASE WHEN (filters->>'sort')='duid' THEN duid END,
        CASE WHEN (filters->>'sort')='duid_desc' THEN duid END DESC,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid
    LIMIT page_limit OFFSET CASE WHEN ministry_id::integer IS NULL
        THEN page_offset ELSE 0 END
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'authorization_scope',jsonb_build_object('capability','ministry_report',
        'operational',operational,'ministries',coalesce((SELECT jsonb_agg(duid ORDER BY duid)
            FROM ministries),'[]'::jsonb)),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'report_date',x.report_date),
    'total',CASE WHEN ministry_id::integer IS NULL
        THEN (SELECT count(*) FROM summary_filtered)
        ELSE (SELECT count(*) FROM filtered) END,
    'summaries',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY
        CASE WHEN (filters->>'sort')='duid' THEN duid END,
        CASE WHEN (filters->>'sort')='duid_desc' THEN duid END DESC,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid)
        FROM summary_page p),'[]'::jsonb),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE current_scope jsonb; filters jsonb; ministry_id integer; request_action text;
        selection bigint[]; packet boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    current_scope:=stewardship_ministry_scope_v1(NEW.actor_id);
    IF current_scope IS NULL
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Ministry export capture is unavailable' USING ERRCODE='23514'; END IF;
    -- Closed parameters have identical meanings in HTML and immutable capture.
    filters:=NEW.parameters->'filters';
    request_action:=NEW.parameters->>'action';
    packet:=coalesce(request_action='packet',false);
    -- Only a packet carries a Ministry selection, and it always carries one.
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR NOT NEW.parameters ?& ARRAY['filters','ministry','action']
       OR NEW.parameters-ARRAY['filters','ministry','action','ministries']<>'{}'::jsonb
       OR (NEW.parameters ? 'ministries') IS DISTINCT FROM packet
       OR jsonb_typeof(filters) IS DISTINCT FROM 'object'
       OR NOT filters ?& ARRAY['search','activity','history','state','start','end','sort']
       OR filters-ARRAY['search','activity','history','state','start','end','sort']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(filters) f WHERE jsonb_typeof(f.value)<>'string')
       OR octet_length(filters->>'search')>131072
       OR filters->>'activity' NOT IN ('any','active','inactive','unavailable')
       OR filters->>'history' NOT IN ('current','all')
       OR filters->>'state' NOT IN ('any','unresolved','new','assigned','in_progress',
           'resolved','closed_no_response','cancelled','superseded')
       OR filters->>'sort' NOT IN ('name','name_desc','newest','oldest','duid','duid_desc')
       OR request_action IS NULL
       OR request_action NOT IN ('summary','join','leave','packet')
    THEN RAISE EXCEPTION 'Invalid Ministry export parameters' USING ERRCODE='23514'; END IF;
    IF NEW.parameters->'ministry'<>'null'::jsonb THEN
        IF jsonb_typeof(NEW.parameters->'ministry')<>'number'
           OR NOT (NEW.parameters->>'ministry') ~ '^[1-9][0-9]{0,9}$'
           OR (NEW.parameters->>'ministry')::bigint>2147483647
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        ministry_id:=(NEW.parameters->>'ministry')::integer;
    END IF;
    IF packet THEN
        -- A packet's only choices are its Ministries and the history option;
        -- every other filter must hold its neutral value so one capture has
        -- exactly one meaning. JSON null selects every authorized Ministry.
        IF filters-'history'<>jsonb_build_object('search','','activity','any',
               'state','any','start','','end','','sort','name')
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        IF NEW.parameters->'ministries'<>'null'::jsonb THEN
            IF jsonb_typeof(NEW.parameters->'ministries')<>'array'
               OR jsonb_array_length(NEW.parameters->'ministries') NOT BETWEEN 1 AND 200
               OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.parameters->'ministries') e
                   WHERE jsonb_typeof(e.value)<>'number'
                      OR NOT (e.value#>>'{}') ~ '^[1-9][0-9]{0,9}$'
                      OR (e.value#>>'{}')::bigint>2147483647)
            THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
            selection:=ARRAY(SELECT (e.value#>>'{}')::bigint
                FROM jsonb_array_elements(NEW.parameters->'ministries')
                    WITH ORDINALITY e(value,position) ORDER BY e.position);
            -- Canonical means strictly ascending, which also forbids repeats.
            IF selection<>ARRAY(SELECT DISTINCT v FROM unnest(selection) v ORDER BY v)
            THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    IF (ministry_id IS NULL) IS DISTINCT FROM (request_action IN ('summary','packet'))
       OR (request_action='summary' AND (filters->>'history'<>'current'
           OR filters->>'state'<>'any' OR filters->>'start'<>'' OR filters->>'end'<>''
           OR filters->>'sort' NOT IN ('name','name_desc','duid','duid_desc')))
       OR EXISTS(SELECT 1 FROM jsonb_each_text(filters) f
           WHERE f.key IN ('start','end') AND f.value<>'' AND (
               NOT f.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
               OR NOT pg_input_is_valid(f.value,'date')))
       OR (filters->>'start'<>'' AND filters->>'end'<>''
           AND filters->>'start'>filters->>'end')
    THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    IF packet THEN
        NEW.document:=stewardship_ministry_packet_v1(NEW.campaign_id,selection,
            filters->>'history'='all',(current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')));
        -- Scope is intersected, never trusted. An explicit selection must
        -- resolve to exactly the Ministries asked for: dropping one the caller
        -- cannot see would capture a packet that misstates its own request.
        IF selection IS NOT NULL AND NEW.document ? 'sections'
           AND jsonb_array_length(NEW.document->'sections')<>cardinality(selection)
        THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    ELSE
        NEW.document:=stewardship_ministry_report_v1(NEW.campaign_id,filters,
            (current_scope->>'operational')::boolean,
            ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')),
            ministry_id,request_action);
    END IF;
    IF NEW.document IS NULL OR NEW.document ?| ARRAY['disabled','unavailable']
       OR (NEW.document->'authorized'<>'true'::jsonb AND
           (ministry_id IS NOT NULL OR current_scope->'operational'<>'true'::jsonb))
    THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    -- Lifecycle authority intentionally covers the whole selected policy scope.
    -- Audit attribution instead names only Ministries present after filtering,
    -- including a named empty detail section but not an empty summary result.
    NEW.authorization_scope:=NEW.document->'authorization_scope' || jsonb_build_object(
        'result_ministries',coalesce((SELECT jsonb_agg((value->>'duid')::bigint
            ORDER BY (value->>'duid')::bigint)
            FROM jsonb_array_elements(NEW.document->
                CASE WHEN packet THEN 'sections' ELSE 'summaries' END)), '[]'::jsonb));
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    -- Each function is installed once, with its identity, attributes and
    -- settings, not SECURITY DEFINER, and with the #960 body.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND ((p.proname='stewardship_financial_report_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'campaign_uuid uuid, parameters jsonb, page_number integer, page_size integer'
                AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
                AND p.proconfig @> ARRAY['jit=off']
                AND p.prosrc LIKE '%''duid'',''duid_desc''%'
                AND p.prosrc LIKE '%lower(r.display_name)%')
            OR (p.proname='stewardship_information_report_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'campaign uuid, parameters jsonb, page_number integer, item_uuid uuid, page_size integer'
                AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
                AND p.proconfig @> ARRAY['jit=off']
                AND p.prosrc LIKE '%''duid'',''duid_desc''%'
                AND p.prosrc LIKE '%''ordinal'',''head_duids'',''display_name''%')
            OR (p.proname='stewardship_talent_report_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'campaign_uuid uuid, parameters jsonb'
                AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
                AND p.proconfig @> ARRAY['jit=off']
                AND p.prosrc LIKE '%''member_duid'',member_duid%'
                AND p.prosrc LIKE '%lower(q.display_name)%')
            OR (p.proname='stewardship_ministry_report_v1'
                AND pg_get_function_identity_arguments(p.oid)=
                    'campaign_uuid uuid, filters jsonb, operational boolean, '
                    'ministry_scope bigint[], ministry_id integer, request_action text, '
                    'page_limit integer, page_offset integer'
                AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
                AND p.proconfig @> ARRAY['jit=off']
                AND p.prosrc LIKE '%(filters->>''sort'')=''duid_desc'' AND entity_kind=''member''%'
                AND p.prosrc LIKE '%(filters->>''sort'')=''duid_desc'' THEN duid END DESC%')
            OR (p.proname='stewardship_ministry_export_capture_v1'
                AND pg_get_function_identity_arguments(p.oid)=''
                AND p.provolatile='v' AND p.prorettype='trigger'::regtype
                AND p.prosrc LIKE '%''newest'',''oldest'',''duid'',''duid_desc''%'
                AND p.prosrc LIKE '%(''name'',''name_desc'',''duid'',''duid_desc'')))%')))<>5
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_financial_report_v1',
               'stewardship_information_report_v1','stewardship_talent_report_v1',
               'stewardship_ministry_report_v1','stewardship_ministry_export_capture_v1'))<>5 THEN
        RAISE EXCEPTION 'Migration 0045 (report DUID sorts) is not installed as declared';
    END IF;
    -- CREATE OR REPLACE keeps the owner and grants; prove it.
    IF current_setting('stewardship.migration_0045_acl', true) IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_financial_report_v1',
            'stewardship_information_report_v1','stewardship_talent_report_v1',
            'stewardship_ministry_report_v1','stewardship_ministry_export_capture_v1')) THEN
        RAISE EXCEPTION 'Migration 0045 changed the replaced functions'' grants';
    END IF;
END
$check$;
