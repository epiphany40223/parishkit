-- Financial stewardship detail: one row per currently effective live Family
-- response that carries a financial answer, for Admin and Staff only.
--
-- Every filter is a closed, bounded vocabulary validated here, so interactive
-- pages and a later immutable capture share one meaning. The summary covers the
-- whole filtered result, never just the returned page.
--
-- `proof` is the application's evidence that one snapshot's last giving read is
-- complete for one campaign configuration's window; that proof depends on a
-- window digest and stays in one place there. It names exactly what it proved,
-- and counts only when those are the snapshot and configuration selected here:
-- the application's reads are separate READ COMMITTED statements, so a promotion
-- or configuration change between them must withhold money, not misattribute it.
-- It only ever withholds: the comparison window, its funds and the through-date
-- are read here from the campaign's own configuration and the snapshot cursor,
-- never from the caller. Without it, totals are unavailable, never zero.
CREATE FUNCTION stewardship_financial_report_v1(
    -- A NULL page number returns the complete, unpaged result. The caller owns
    -- the page size and must state it, so its paging arithmetic cannot drift
    -- from the rows returned here; it is ignored for a complete result.
    campaign_uuid uuid, parameters jsonb, page_number integer, page_size integer
) RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE f jsonb:=parameters->'filters'; proof jsonb:=parameters->'proof';
    answer jsonb;
    money_text constant text:='^(0|[1-9][0-9]{0,8})(\.[0-9]{2})?$';
    -- Canonical lowercase text, so identities compare without a fallible cast.
    uuid_text constant text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters ?& ARRAY['filters','proof']
       OR parameters-ARRAY['filters','proof']<>'{}'::jsonb
       OR jsonb_typeof(proof) NOT IN ('null','object')
       OR (jsonb_typeof(proof)='object' AND (
           NOT proof ?& ARRAY['snapshot','configuration']
           OR proof-ARRAY['snapshot','configuration']<>'{}'::jsonb
           OR jsonb_typeof(proof->'snapshot') IS DISTINCT FROM 'string'
           OR jsonb_typeof(proof->'configuration') IS DISTINCT FROM 'string'
           OR NOT proof->>'snapshot' ~ uuid_text
           OR NOT proof->>'configuration' ~ uuid_text))
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','active','first_start','first_end','latest_start',
           'latest_end','pledge_min','pledge_max','amount','frequency','share','sort']
       OR f-ARRAY['search','active','first_start','first_end','latest_start',
           'latest_end','pledge_min','pledge_max','amount','frequency','share','sort']
          <>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR (f->>'share' NOT IN ('any','none') AND NOT f->>'share' ~ uuid_text)
       OR f->>'active' NOT IN ('any','active','inactive','unavailable')
       OR f->>'amount' NOT IN ('any','zero','nonzero')
       OR f->>'frequency' NOT IN ('any','none','weekly','monthly','quarterly','annual')
       OR f->>'sort' NOT IN ('name','name_desc','newest','oldest','pledge','pledge_desc')
       OR EXISTS(SELECT 1 FROM jsonb_each_text(f) d
           WHERE d.key IN ('first_start','first_end','latest_start','latest_end')
             AND d.value<>'' AND (NOT d.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                 OR NOT pg_input_is_valid(d.value,'date')))
       OR EXISTS(SELECT 1 FROM jsonb_each_text(f) d
           WHERE d.key IN ('pledge_min','pledge_max') AND d.value<>''
             AND NOT d.value ~ money_text)
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR (page_number IS NOT NULL
           AND (page_size IS NULL OR page_size NOT BETWEEN 1 AND 200))
    THEN RAISE EXCEPTION 'Invalid financial report parameters' USING ERRCODE='23514'; END IF;
    -- Compare ranges only after the grammar above has passed. SQL does not
    -- promise to evaluate one condition's terms in order, so a cast placed beside
    -- its own guard could fail first and echo a filter value in its error. An
    -- absent bound becomes NULL, so no term here depends on another to be safe.
    IF coalesce(nullif(f->>'first_start','')>nullif(f->>'first_end',''),false)
       OR coalesce(nullif(f->>'latest_start','')>nullif(f->>'latest_end',''),false)
       OR coalesce(nullif(f->>'pledge_min','')::numeric
           >nullif(f->>'pledge_max','')::numeric,false)
    THEN RAISE EXCEPTION 'Invalid financial report parameters' USING ERRCODE='23514'; END IF;

WITH selected AS MATERIALIZED (
    SELECT c.id,cc.id AS configuration_id,cc.name,cc.timezone,cc.values,
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
        AND proof->>'configuration'=x.configuration_id::text,false) AS proven) v
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'financial'
), responses AS MATERIALIZED (
    SELECT s.id,s.submitted_at,s.family_version,s.annual_pledge,s.configuration_id,
        i.family_duid,first.submitted_at AS first_submitted_at,
        s.answers->'financial' AS financial,
        coalesce(nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
            'Unavailable Family') AS family_name,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
            THEN (p.canonical::jsonb->'active')::boolean END AS active
    FROM source x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id AND s.answers ? 'financial'
    JOIN stewardship_submission first ON first.id=i.first_live_submission_id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), filtered AS MATERIALIZED (
    SELECT r.*,nullif(r.financial->>'frequency','') AS frequency
    FROM responses r CROSS JOIN source x
    WHERE (f->>'search'='' OR position(lower(f->>'search') IN lower(r.family_name))>0
            OR position(f->>'search' IN r.family_duid::text)>0)
        AND (f->>'active'='any' OR (f->>'active'='active' AND r.active IS TRUE)
            OR (f->>'active'='inactive' AND r.active IS FALSE)
            OR (f->>'active'='unavailable' AND r.active IS NULL))
        AND (f->>'first_start'='' OR (r.first_submitted_at AT TIME ZONE x.timezone)::date
            >=nullif(f->>'first_start','')::date)
        AND (f->>'first_end'='' OR (r.first_submitted_at AT TIME ZONE x.timezone)::date
            <=nullif(f->>'first_end','')::date)
        AND (f->>'latest_start'='' OR (r.submitted_at AT TIME ZONE x.timezone)::date
            >=nullif(f->>'latest_start','')::date)
        AND (f->>'latest_end'='' OR (r.submitted_at AT TIME ZONE x.timezone)::date
            <=nullif(f->>'latest_end','')::date)
        AND (f->>'pledge_min'='' OR r.annual_pledge>=nullif(f->>'pledge_min','')::numeric)
        AND (f->>'pledge_max'='' OR r.annual_pledge<=nullif(f->>'pledge_max','')::numeric)
        AND (f->>'amount'='any' OR (f->>'amount'='zero' AND r.annual_pledge=0)
            OR (f->>'amount'='nonzero' AND r.annual_pledge>0))
        AND (f->>'frequency'='any'
            OR (f->>'frequency'='none' AND nullif(r.financial->>'frequency','') IS NULL)
            OR r.financial->>'frequency'=f->>'frequency')
        AND (f->>'share'='any'
            OR (f->>'share'='none' AND r.financial->'shares'='{}'::jsonb)
            OR r.financial->'shares' ? (f->>'share'))
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN f->>'sort'='name' THEN lower(family_name) END,
        CASE WHEN f->>'sort'='name_desc' THEN lower(family_name) END DESC,
        CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
        CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
        CASE WHEN f->>'sort'='pledge' THEN annual_pledge END,
        CASE WHEN f->>'sort'='pledge_desc' THEN annual_pledge END DESC,
        lower(family_name),family_duid) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
    OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
), detail AS (
    SELECT r.ordinal,r.id,r.family_name,r.family_duid,r.active,r.submitted_at,
        r.first_submitted_at,r.family_version,
        -- Money crosses JSON only as canonical text: a JSON number would be
        -- parsed as a binary float and lose exactness.
        r.annual_pledge::numeric(24,2)::text AS annual_pledge,r.frequency,
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
            GROUP BY 1) d),'{}'::jsonb),
        'shares',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT s.key AS k,count(*) AS n FROM filtered q
            CROSS JOIN LATERAL jsonb_object_keys(q.financial->'shares') s(key)
            GROUP BY 1) d),'{}'::jsonb),
        'no_share',count(*) FILTER (WHERE financial->'shares'='{}'::jsonb))
        FROM filtered),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
INTO answer FROM selected z LEFT JOIN source x ON true;
    RETURN answer;
END $$;
