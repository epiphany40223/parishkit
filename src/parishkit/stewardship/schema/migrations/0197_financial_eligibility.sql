-- Frozen forward migration file 0197 (the repository-wide file sequence,
-- assigned by the coordinator): Financial stewardship's and Additional
-- information's Family status read the same eligibility as the Response
-- dashboard (#2335; reports spec, "Financial stewardship detail" and
-- "Additional information"; ui-conventions, "Members and Families no longer
-- in ParishSoft"). This file is installed by its Django migration in the
-- reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copies of the baseline functions equal the fresh-install
-- baseline's.
--
-- #2324 made Financial stewardship's "In this campaign today" the
-- dashboard's current population, carried by the selection's Family status
-- filter (active). Under a Families rule both read the row's
-- portal_eligible ("matches the rule") and agree. Under the legacy rule
-- (no campaign_families on the row) the selection read ParishSoft's
-- active flag alone, while the dashboard's portal_eligible is the legacy
-- rule itself: active AND registered at the parish. A Family that moved
-- its registration to another parish but stays active was counted by
-- Financial stewardship and not by the dashboard. Production's campaign is
-- on the legacy rule. The Additional information selections' status badge
-- read the same legacy branch, so the same Family would be in the campaign
-- there and out of it on Financial stewardship.
--
-- Replaced functions, each copied whole from its newest definition (0194,
-- #2320) and changed only in its active column, which is now the row's
-- portal_eligible for every row, under any rule (NULL for a Family the data
-- no longer has, as before):
--   stewardship_financial_report_v1   (the baseline; 0194 last replaced it)
--                                     the column drives the row's
--                                     ParishSoft status badge, the "No
--                                     longer in this campaign" refinement and
--                                     "In this campaign today"; Pledges and
--                                     the Family hub's Financial tab read
--                                     this selection too
--   stewardship_information_report_v1 (the baseline; 0194 last replaced it)
--   stewardship_information_report_v2 (migration-owned; 0194) the column
--                                     drives the row's status badge only
-- Every payload has a boolean portal_eligible (source/families.py checks
-- it), so no fallback to active remains. The one ? operator each old column
-- used is gone; every other ?, ?& and @> stays written
-- OPERATOR(pg_catalog.x) (#2166). The export captures call these
-- functions, so a file captured from now on names such a Family so; a
-- retained capture keeps what it held. None of these functions is
-- SECURITY DEFINER, before or after, and none has a REVOKE (no baseline
-- ALTER names them), so there is no ALTER FUNCTION or REVOKE to repeat;
-- CREATE OR REPLACE keeps their owner, grants and settings (search_path
-- and jit off), and the DO block below checks the bodies (0194's, 0182's,
-- 0175's and 0110's rules kept), that each selection still plans and runs,
-- and that the grants, owner and settings equal those recorded before the
-- replacement. No row, grant or credential changes, and no Family code,
-- link or session is touched. No temporary objects. Reversing needs its
-- own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants, owner and settings before the replacement
-- (with how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0197_reads', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text,';' ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_financial_report_v1',
        'stewardship_information_report_v1','stewardship_information_report_v2')), true);

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
        bad:=coalesce(NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','proof']
            OR parameters-ARRAY['filters','proof']<>'{}'::jsonb
            OR jsonb_typeof(f) IS DISTINCT FROM 'object'
            OR jsonb_typeof(proof) NOT IN ('null','object')
            OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
            OR (page_number IS NOT NULL
                AND (page_size IS NULL OR page_size NOT BETWEEN 1 AND 200)),true);
    END IF;
    IF NOT bad AND jsonb_typeof(proof)='object' THEN
        bad:=coalesce(NOT proof OPERATOR(pg_catalog.?&) ARRAY['snapshot','configuration']
            OR proof-ARRAY['snapshot','configuration']<>'{}'::jsonb
            OR jsonb_typeof(proof->'snapshot') IS DISTINCT FROM 'string'
            OR jsonb_typeof(proof->'configuration') IS DISTINCT FROM 'string'
            OR NOT proof->>'snapshot' ~ uuid_text
            OR NOT proof->>'configuration' ~ uuid_text,true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(NOT f OPERATOR(pg_catalog.?&) ARRAY['search','active','first_start','first_end',
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
    IF NOT bad AND f OPERATOR(pg_catalog.?) 'zone' THEN
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
        AND x.values->'modules' OPERATOR(pg_catalog.?) 'financial'
), edits AS MATERIALIZED (
    -- Each Family's pledge Edit in force (#2196): its latest pledge review
    -- row, when the selection that places Pledges' stages (0181's
    -- stewardship_pledge_review_states_v1) holds it in force and its
    -- decision is Accepted (edited). Entered by hand and the undo of a
    -- terminal step carry that decision with its values, so they count
    -- too; Back to review of a decision, Accept, Ignore and a newer,
    -- different pledge answer from the Family leave none.
    SELECT k.family_id,v.reviewed_annual_pledge,v.reviewed_frequency,v.reviewed_shares
    FROM source x
    CROSS JOIN LATERAL stewardship_pledge_review_states_v1(x.id,NULL) k
    JOIN stewardship_pledge_review v ON v.id=k.review_id
    WHERE k.in_force AND v.decision='accepted_edited'
), responses AS MATERIALIZED (
    -- The pledge in force (#2196): an Edit's amount, frequency and share
    -- methods while it is in force, else the Family's own answer. An
    -- edited pledge reads as the reviewed Family form shows it (0181's
    -- selection, rule 1): above zero the reviewer's frequency, and the
    -- reviewer's share methods where the Edit changed them, else the
    -- Family's own; a zero pledge no frequency and no share methods, with
    -- the Family's own Cannot contribute answer. The Family's own parts are
    -- read as 0181's rule reads them (stewardship_pledge_answer_v1), so an
    -- answer without share methods carries none. Every filter, sort and
    -- summary below reads these.
    SELECT s.id,s.submitted_at,s.family_version,
        coalesce(e.reviewed_annual_pledge,s.annual_pledge) AS annual_pledge,
        s.configuration_id,
        i.family_duid,first.submitted_at AS first_submitted_at,
        CASE WHEN e.family_id IS NULL THEN s.answers->'financial'
            ELSE s.answers->'financial'||jsonb_build_object(
                'frequency',CASE WHEN e.reviewed_annual_pledge>0
                    THEN coalesce(e.reviewed_frequency,'') ELSE '' END,
                'cannot_give',CASE WHEN e.reviewed_annual_pledge>0 THEN 'false'::jsonb
                    ELSE stewardship_pledge_answer_v1(s.answers,s.annual_pledge)->'cannot_give'
                    END,
                'shares',CASE WHEN e.reviewed_annual_pledge>0
                    THEN coalesce(e.reviewed_shares,
                        stewardship_pledge_answer_v1(s.answers,s.annual_pledge)->'shares')
                    ELSE '{}'::jsonb END) END AS financial,
        -- The Family's own answer beside an Edit in force (NULL without
        -- one), from its current response: the page's Edited marker words
        -- it, and Pledges and the Family hub show it in the Family's
        -- answer's place, as they did before 0182. After a reviewed form
        -- sent the edited pledge back as shown, that answer is the edited
        -- pledge.
        CASE WHEN e.family_id IS NOT NULL THEN jsonb_build_object(
            'annual_pledge',s.annual_pledge::numeric(24,2)::text,
            'frequency',nullif(s.answers->'financial'->>'frequency',''),
            'cannot_give',coalesce(s.answers->'financial'->'cannot_give'='true'::jsonb,false),
            'shares',s.answers->'financial'->'shares') END AS family_answer,
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
        -- Whether surname is the Family's own lastName, not a stand-in for
        -- a blank one (family_names.family_last_name): without one, the
        -- heads' own surnames lead the shown name (#2320).
        stewardship_family_last_name_v1(p.canonical::jsonb)<>'' AS surname_recorded,
        -- Whether the Family is still one of the campaign's Families (NULL
        -- when it is not in the data), for its status badge and the Active
        -- filter (#1393): its portal_eligible, the eligibility the Response
        -- dashboard counts (#2335). Under a Families rule that is "matches
        -- the rule"; under the legacy rule, ParishSoft's active and
        -- registered at the parish.
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
            THEN (p.canonical::jsonb->'portal_eligible')::boolean END AS active,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
            THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
        -- Whether Staff entered this effective response for the Family
        -- through Open form (#529, #794): a yes or no, never who.
        s.entered_by_id IS NOT NULL AS staff_entered
    FROM source x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id AND s.answers OPERATOR(pg_catalog.?) 'financial'
    JOIN stewardship_submission first ON first.id=i.first_live_submission_id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
    LEFT JOIN edits e ON e.family_id=i.id
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
    SELECT r.*,stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)
        AS display_name
    FROM responses r LEFT JOIN head_names n ON n.family_duid=r.family_duid
), filtered AS MATERIALIZED (
    SELECT r.*,nullif(r.financial->>'frequency','') AS frequency
    FROM named r CROSS JOIN source x
    -- Search matches the shown name, which names every head's surname,
    -- and the Family surname (or its stand-in), which it may not (#2320).
    WHERE (f->>'search'='' OR position(lower(f->>'search') IN lower(r.display_name))>0
            OR position(lower(f->>'search') IN lower(r.surname))>0
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
            OR r.financial->'shares' OPERATOR(pg_catalog.?) (f->>'share'))
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
        -- Whether an Edit is in force (#2196), and the Family's own answer
        -- beside it.
        r.family_answer IS NOT NULL AS edited,r.family_answer,
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
                        OPERATOR(pg_catalog.@>) to_jsonb(p.fund_key::bigint)
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
                        OPERATOR(pg_catalog.@>) to_jsonb(p.fund_key::bigint)
                    AND (p.canonical::jsonb->>'effective_date')::date BETWEEN
                        (x.values->'financial'->>'comparison_start')::date
                        AND least((x.values->'financial'->>'comparison_end')::date,
                            x.giving_through)),0)
            END AS contribution_total
    ) g ON true
)
SELECT CASE WHEN NOT z.values->'modules' OPERATOR(pg_catalog.?) 'financial'
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
       OR NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f OPERATOR(pg_catalog.?&) ARRAY['search','disposition','needed','completed','start','end','sort']
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
                -- No name (NULL) for a Family the data no longer has: the
                -- page shows its ParishSoft status badge alone, and it sorts
                -- after the names (#1216). A present Family without a usable
                -- name reads so.
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END)
                AS family_name,
            -- The surname that leads the shown name, as Python names it
            -- (migration 0045's shared functions); family_name above keeps
            -- its own trim for the returned rows.
            stewardship_family_surname_v1(p.canonical::jsonb,
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
            -- Whether surname is the Family's own lastName, not a stand-in for
            -- a blank one (family_names.family_last_name): without one, the
            -- heads' own surnames lead the shown name (#2320).
            stewardship_family_last_name_v1(p.canonical::jsonb)<>'' AS surname_recorded,
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether the Family is still one of the campaign's Families
            -- (NULL when it is not in the data), for its status badge (#1216,
            -- #1393): its portal_eligible, the eligibility the Response
            -- dashboard and Financial stewardship read (#2335). Under a
            -- Families rule that is "matches the rule"; under the legacy
            -- rule, ParishSoft's active and registered at the parish.
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
                THEN (p.canonical::jsonb->'portal_eligible')::boolean END AS active,
            h.value->'reported' OPERATOR(pg_catalog.?) i.id::text AS previously_reported,
            h.value->'corrected' OPERATOR(pg_catalog.@>)
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
        -- Each Family's active heads as the shown name lists them, in DUID
        -- order (stewardship_family_head_part_v1), so search and sort match
        -- what the page shows (#960). One set-based join over each Family's
        -- heads, skipped when neither a search nor a name sort reads the name.
        SELECT r.family_duid,array_agg(
                stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
                ORDER BY h.head::bigint) AS parts
        FROM (SELECT DISTINCT family_duid,surname,head_duids FROM rows) r
        CROSS JOIN selected x
        CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
        JOIN stewardship_snapshot_member mm
            ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
        WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
        GROUP BY r.family_duid
    ), named AS MATERIALIZED (
        -- "Surname, A and B" (family_names.family_heads_name).
        SELECT r.*,stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)
            AS display_name
        FROM rows r LEFT JOIN head_names n ON n.family_duid=r.family_duid
    ), filtered AS MATERIALIZED (
        SELECT * FROM named WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          -- Search matches the shown name, which names every head's
          -- surname, and the Family surname (or its stand-in), which it
          -- may not (#2320).
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(display_name))>0
            OR position(lower(f->>'search') IN lower(surname))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        -- A name sort orders by surname, then the whole shown name, as the
        -- directory does; the item id is the unique tiebreak.
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(surname) END,
            CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
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
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname',
            'surname_recorded','display_name']
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

CREATE OR REPLACE FUNCTION public.stewardship_information_report_v2(
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
       OR NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f OPERATOR(pg_catalog.?&) ARRAY['search','disposition','needed','completed','start','end','sort','zone']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort','zone']<>'{}'::jsonb
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
       -- Days are the viewer's browser zone (#558): required with a date,
       -- and always one the server's time zone catalog knows.
       OR ((f->>'start'<>'' OR f->>'end'<>'') AND f->>'zone'='')
       OR (f->>'zone'<>'' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=stewardship_timezone_name_v1(f->>'zone')))
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
                -- No name (NULL) for a Family the data no longer has: the
                -- page shows its ParishSoft status badge alone, and it sorts
                -- after the names (#1216). A present Family without a usable
                -- name reads so.
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END)
                AS family_name,
            -- The surname that leads the shown name, as Python names it
            -- (migration 0045's shared functions); family_name above keeps
            -- its own trim for the returned rows.
            stewardship_family_surname_v1(p.canonical::jsonb,
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
            -- Whether surname is the Family's own lastName, not a stand-in for
            -- a blank one (family_names.family_last_name): without one, the
            -- heads' own surnames lead the shown name (#2320).
            stewardship_family_last_name_v1(p.canonical::jsonb)<>'' AS surname_recorded,
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether the Family is still one of the campaign's Families
            -- (NULL when it is not in the data), for its status badge (#1216,
            -- #1393): its portal_eligible, the eligibility the Response
            -- dashboard and Financial stewardship read (#2335). Under a
            -- Families rule that is "matches the rule"; under the legacy
            -- rule, ParishSoft's active and registered at the parish.
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
                THEN (p.canonical::jsonb->'portal_eligible')::boolean END AS active,
            h.value->'reported' OPERATOR(pg_catalog.?) i.id::text AS previously_reported,
            h.value->'corrected' OPERATOR(pg_catalog.@>)
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
          -- From local midnight of the first day to local midnight after the
          -- last, in the viewer's zone; a DST day is 23 or 25 hours long.
          -- Each bound is a scalar subquery, so it is computed once per call.
          AND (f->>'start'='' OR s.submitted_at>=(SELECT
              stewardship_browser_day_start_v1((f->>'start')::date,f->>'zone')))
          AND (f->>'end'='' OR s.submitted_at<(SELECT
              stewardship_browser_day_start_v1((f->>'end')::date+1,f->>'zone')))
    ), head_names AS MATERIALIZED (
        -- Each Family's active heads as the shown name lists them, in DUID
        -- order (stewardship_family_head_part_v1), so search and sort match
        -- what the page shows (#960). One set-based join over each Family's
        -- heads, skipped when neither a search nor a name sort reads the name.
        SELECT r.family_duid,array_agg(
                stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
                ORDER BY h.head::bigint) AS parts
        FROM (SELECT DISTINCT family_duid,surname,head_duids FROM rows) r
        CROSS JOIN selected x
        CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
        JOIN stewardship_snapshot_member mm
            ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
        WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
        GROUP BY r.family_duid
    ), named AS MATERIALIZED (
        -- "Surname, A and B" (family_names.family_heads_name).
        SELECT r.*,stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)
            AS display_name
        FROM rows r LEFT JOIN head_names n ON n.family_duid=r.family_duid
    ), filtered AS MATERIALIZED (
        SELECT * FROM named WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          -- Search matches the shown name, which names every head's
          -- surname, and the Family surname (or its stand-in), which it
          -- may not (#2320).
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(display_name))>0
            OR position(lower(f->>'search') IN lower(surname))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        -- A name sort orders by surname, then the whole shown name, as the
        -- directory does; the item id is the unique tiebreak.
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(surname) END,
            CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
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
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname',
            'surname_recorded','display_name']
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
        -- Every matching request by its follow-up state, for the page's
        -- Follow-up status chart (#720): completed, or not yet completed
        -- with follow-up marked needed or not. The three add up to total.
        'counts',(SELECT jsonb_build_object(
            'completed',count(*) FILTER (WHERE followed_up_at IS NOT NULL),
            'needed_open',count(*) FILTER (WHERE followed_up_at IS NULL AND follow_up_needed),
            'not_needed_open',count(*) FILTER (WHERE followed_up_at IS NULL AND NOT follow_up_needed))
            FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

DO $check$
DECLARE f jsonb:='{"search":"","active":"any","first_start":"","first_end":"",
    "latest_start":"","latest_end":"","pledge_min":"","pledge_max":"",
    "amount":"any","frequency":"any","share":"any","sort":"name"}';
    i jsonb:='{"search":"","disposition":"all","needed":"any","completed":"any",
    "start":"","end":"","sort":"newest"}';
BEGIN
    -- The Financial selection: the active column is the row's
    -- portal_eligible for every row, and the legacy branch (ParishSoft's
    -- active alone) is gone, with 0194's, 0182's, 0175's and 0110's other
    -- rules kept and its operators qualified: still one invoker, stable
    -- function returning jsonb.
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_financial_report_v1(uuid,jsonb,integer,integer)'::regprocedure
              AND prosrc LIKE '%THEN (p.canonical::jsonb->''portal_eligible'')::boolean END AS active,%'
              AND prosrc NOT LIKE '%''campaign_families''%'
              AND prosrc NOT LIKE '%(p.canonical::jsonb->''active'')::boolean%'
              -- 0194's rules (#2320), kept.
              AND prosrc LIKE '%stewardship_family_last_name_v1(p.canonical::jsonb)<>'''' AS surname_recorded,%'
              AND prosrc LIKE '%stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)%'
              AND prosrc LIKE '%OR position(lower(f->>''search'') IN lower(%surname))>0%'
              AND prosrc NOT LIKE '%stewardship_family_heads_name_v1(%'
              -- 0182's rules, kept.
              AND prosrc LIKE '%CROSS JOIN LATERAL stewardship_pledge_review_states_v1(x.id,NULL) k%'
              AND prosrc LIKE '%WHERE k.in_force AND v.decision=''accepted_edited''%'
              AND prosrc LIKE '%coalesce(e.reviewed_annual_pledge,s.annual_pledge) AS annual_pledge%'
              AND prosrc LIKE '%THEN coalesce(e.reviewed_shares,%'
              AND prosrc LIKE '%stewardship_pledge_answer_v1(s.answers,s.annual_pledge)->''shares'')%'
              AND prosrc LIKE '%LEFT JOIN edits e ON e.family_id=i.id%'
              AND prosrc LIKE '%r.family_answer IS NOT NULL AS edited,r.family_answer,%'
              -- 0175's and 0110's rules, kept.
              AND prosrc LIKE '%CASE WHEN zoned THEN r.first_submitted_at>=first_from%'
              AND prosrc LIKE '%s.entered_by_id IS NOT NULL AS staff_entered%'
              -- Its operators, qualified (#2166): ten (0194's eleven less
              -- the old column's ?), none bare.
              AND (length(prosrc)-length(replace(prosrc,'OPERATOR(pg_catalog.','')))
                  /length('OPERATOR(pg_catalog.')=10
              AND prosrc !~ '\s(\?[|&]?|@>|<@)\s'
              AND NOT prosecdef AND provolatile='s' AND prorettype='jsonb'::regtype)
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_financial_report_v1')<>1
    THEN
        RAISE EXCEPTION 'Migration 0197 (financial eligibility) did not replace the Financial selection';
    END IF;
    -- Both Additional information selections: the same active column and no
    -- legacy branch, with 0194's rules and their history columns kept, each
    -- with four qualified operators (0194's five less the old column's ?)
    -- and none bare: still invokers, stable, returning jsonb.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_information_report_v1','stewardship_information_report_v2')
          AND pg_get_function_identity_arguments(p.oid)=
              'campaign uuid, parameters jsonb, page_number integer, item_uuid uuid, page_size integer'
          AND p.prosrc LIKE '%THEN (p.canonical::jsonb->''portal_eligible'')::boolean END AS active,%'
          AND p.prosrc NOT LIKE '%''campaign_families''%'
          AND p.prosrc NOT LIKE '%(p.canonical::jsonb->''active'')::boolean%'
          AND p.prosrc LIKE '%stewardship_family_last_name_v1(p.canonical::jsonb)<>'''' AS surname_recorded,%'
          AND p.prosrc LIKE '%stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)%'
          AND p.prosrc LIKE '%OR position(lower(f->>''search'') IN lower(%surname))>0%'
          AND p.prosrc NOT LIKE '%stewardship_family_heads_name_v1(%'
          AND p.prosrc LIKE '%h.value->''reported'' OPERATOR(pg_catalog.?) i.id::text AS previously_reported,%'
          -- v2's follow-up status counts (#720), kept.
          AND (p.proname='stewardship_information_report_v1'
              OR p.prosrc LIKE '%''counts'',(SELECT jsonb_build_object(%')
          AND (length(p.prosrc)-length(replace(p.prosrc,'OPERATOR(pg_catalog.','')))
              /length('OPERATOR(pg_catalog.')=4
          AND p.prosrc !~ '\s(\?[|&]?|@>|<@)\s'
          AND NOT p.prosecdef AND p.provolatile='s' AND p.prorettype='jsonb'::regtype)<>2
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_information_report_v1',
               'stewardship_information_report_v2'))<>2
    THEN
        RAISE EXCEPTION 'Migration 0197 (financial eligibility) did not replace the Additional information selections';
    END IF;
    -- Each whole selection still plans and runs (a column or function it
    -- names that is missing fails here): a campaign that does not exist
    -- reads as nothing.
    IF public.stewardship_financial_report_v1('00000000-0000-0000-0000-000000000000',
            jsonb_build_object('filters',f,'proof',NULL),NULL,NULL) IS NOT NULL
       OR public.stewardship_information_report_v1('00000000-0000-0000-0000-000000000000',
            jsonb_build_object('filters',i,'history',true)) IS NOT NULL
       OR public.stewardship_information_report_v2('00000000-0000-0000-0000-000000000000',
            jsonb_build_object('filters',i||'{"zone":""}','history',true)) IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0197 (financial eligibility) cannot read the selections';
    END IF;
    -- CREATE OR REPLACE keeps the grants, owner and settings; check that
    -- nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
                ||'/'||p.proowner::regrole::text,';' ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_financial_report_v1',
            'stewardship_information_report_v1','stewardship_information_report_v2'))
        IS DISTINCT FROM current_setting('stewardship.migration_0197_reads') THEN
        RAISE EXCEPTION 'Migration 0197 changed the selections'' grants, owner or settings';
    END IF;
END
$check$;
