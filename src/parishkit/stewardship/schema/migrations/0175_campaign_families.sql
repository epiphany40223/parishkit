-- Frozen forward migration file 0175 (the repository-wide file sequence):
-- a campaign's Families rule is fixed when the campaign is created (#1393).
-- This file is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- A campaign's configuration may now carry campaign_families, the rule that
-- chooses which ParishSoft Families the campaign is sent to (a preset or an
-- Advanced rule; the data specification's "Campaign Families"). A campaign
-- without the value uses the legacy rule, today's: every campaign created
-- before this file, the Production campaign among them, keeps exactly the
-- Families and email behaviour it has.
--
-- This file replaces stewardship_campaign_pointer_v1 with 0166's body (the
-- latest; no later file replaces it) plus two checks: a configuration for a
-- campaign that already exists must carry exactly the campaign_families
-- value that campaign's current configuration carries, absent staying
-- absent, in every campaign state; and a configuration that creates a
-- campaign may carry one only in a canonical shape. campaign_families is not
-- one of the live structural exemptions. Python's installer preflight
-- (campaigns/admission.py) and configuration validation apply the same
-- rules first. The function is not SECURITY DEFINER, keeps its pinned
-- search_path, and keeps its grants; no stored row changes: no
-- FamilyCampaign, code, link token, lookup digest, schedule occurrence or
-- outbox message.
--
-- It also replaces four functions in place, copied whole and changed only
-- as their own comments below say, none of them SECURITY DEFINER before or
-- after, keeping their grants, owners and settings:
-- stewardship_source_current_window_v1 (the baseline), so a refresh request
-- for a campaign with a rule matches the window Python writes; and
-- stewardship_financial_report_v1 (0110),
-- stewardship_information_report_v1 (0094) and
-- stewardship_information_report_v2 (0100), so a Family's "No longer in this
-- campaign" badge follows the campaign's rule. A campaign without a rule
-- reads exactly as before in all four.
--
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's grants before the replacement, compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0175_acl', (
    SELECT coalesce(p.proacl::text,'default')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1'), true);

-- The same for the four functions this file also replaces in place (their
-- grants, owner and settings: search_path, and jit off for the reports).
SELECT set_config('stewardship.migration_0175_reads', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text,';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_source_current_window_v1',
        'stewardship_financial_report_v1','stewardship_information_report_v1',
        'stewardship_information_report_v2')), true);

CREATE OR REPLACE FUNCTION public.stewardship_campaign_pointer_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE target uuid; candidate stewardship_campaign_configuration%ROWTYPE;
    live_state text; live_before jsonb; live_added numeric[];
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Configuration cannot replace the current pointer' USING ERRCODE='23514';
    END IF;
    target := OLD.current_campaign_id;
    IF target IS NOT NULL AND EXISTS (
        SELECT 1 FROM stewardship_campaign_configuration c WHERE c.configuration_id=NEW.active_configuration_id
        AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)
    ) THEN RAISE EXCEPTION 'Current campaign prevents successor creation' USING ERRCODE='23514'; END IF;
    IF target IS NULL THEN
        IF (SELECT count(*) FROM stewardship_campaign_configuration c
            WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)) > 1 THEN
            RAISE EXCEPTION 'Only one successor can be created' USING ERRCODE='23514';
        END IF;
        SELECT record_id INTO target FROM stewardship_campaign_configuration c
        WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id);
        IF target IS NOT NULL AND (NEW.mode<>'testing' OR NEW.restore_review_required
            OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state NOT IN ('archived','purged'))
            OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))) THEN
            RAISE EXCEPTION 'Successor creation is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM stewardship_campaign old_campaign
        JOIN stewardship_campaign_configuration old_c ON old_c.id=old_campaign.active_configuration_id
        LEFT JOIN stewardship_campaign_configuration new_c ON new_c.record_id=old_campaign.id AND new_c.configuration_id=NEW.active_configuration_id
        WHERE new_c.id IS NULL OR (old_campaign.id IS DISTINCT FROM target AND old_c.values IS DISTINCT FROM new_c.values)
    ) THEN RAISE EXCEPTION 'Historical campaigns cannot be removed or edited' USING ERRCODE='23514'; END IF;
    IF target IS NOT NULL THEN
        SELECT * INTO candidate FROM stewardship_campaign_configuration WHERE record_id=target AND configuration_id=NEW.active_configuration_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Current campaign cannot be removed' USING ERRCODE='23514'; END IF;
        -- The Families rule (#1393) is fixed when the campaign is created:
        -- every later configuration of an existing campaign carries exactly
        -- the value its current one carries, in every state, and an absent
        -- value (the legacy rule) stays absent.
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values->'campaign_families' IS DISTINCT FROM candidate.values->'campaign_families') THEN
            RAISE EXCEPTION 'The campaign''s Families cannot be changed' USING ERRCODE='23514';
        END IF;
        -- A configuration that creates the campaign may carry a rule, which
        -- must be one canonical shape (campaigns/campaign_families.py
        -- validate): a preset, or Advanced with a registration flag, Family
        -- groups by ascending id ({id, name}; id 0 only as the no-group
        -- choice) and Member statuses in ParishSoft's order. Nested CASEs fix
        -- the evaluation order, so a malformed value is refused, not miscast.
        IF NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=target)
            AND candidate.values ? 'campaign_families'
            AND NOT coalesce(CASE WHEN jsonb_typeof(candidate.values->'campaign_families')<>'object' THEN false
                WHEN candidate.values->'campaign_families'->>'rule' IN ('active_parishioners','active_parishioners_and_staff') THEN
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(candidate.values->'campaign_families') k)=ARRAY['rule']
                WHEN candidate.values->'campaign_families'->>'rule'<>'advanced'
                    OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(candidate.values->'campaign_families') k)
                        IS DISTINCT FROM ARRAY['family_groups','member_statuses','registered','rule']
                    OR jsonb_typeof(candidate.values->'campaign_families'->'registered')<>'boolean'
                    OR jsonb_typeof(candidate.values->'campaign_families'->'family_groups')<>'array'
                    OR jsonb_typeof(candidate.values->'campaign_families'->'member_statuses')<>'array' THEN false
                WHEN jsonb_array_length(candidate.values->'campaign_families'->'family_groups')=0
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements(candidate.values->'campaign_families'->'family_groups') g(grp)
                        WHERE CASE WHEN jsonb_typeof(g.grp)<>'object' THEN true
                            WHEN (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(g.grp) k) IS DISTINCT FROM ARRAY['id','name']
                                OR jsonb_typeof(g.grp->'id')<>'number' OR g.grp->>'id'!~'^(0|[1-9][0-9]{0,9})$'
                                OR jsonb_typeof(g.grp->'name')<>'string' THEN true
                            ELSE (g.grp->>'id')::bigint>=2147483648
                                OR char_length(g.grp->>'name') NOT BETWEEN 1 AND 200
                                OR (g.grp->>'id'='0' AND g.grp->>'name'<>'(No Family Group)') END) THEN false
                ELSE coalesce((SELECT bool_and(x.id>x.previous) FROM (
                        SELECT (e.grp->>'id')::bigint AS id, lag((e.grp->>'id')::bigint) OVER (ORDER BY e.position) AS previous
                        FROM jsonb_array_elements(candidate.values->'campaign_families'->'family_groups') WITH ORDINALITY e(grp,position)) x
                        WHERE x.previous IS NOT NULL),true)
                    AND candidate.values->'campaign_families'->'member_statuses'=(
                        SELECT jsonb_agg(to_jsonb(u.status) ORDER BY u.position)
                        FROM unnest(ARRAY['Active','ActOther','Unknown']) WITH ORDINALITY u(status,position)
                        WHERE candidate.values->'campaign_families'->'member_statuses' @> jsonb_build_array(u.status))
                END,false) THEN
            RAISE EXCEPTION 'Invalid campaign Families rule' USING ERRCODE='23514';
        END IF;
        IF NEW.restore_review_required AND EXISTS (
            SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values IS DISTINCT FROM candidate.values
        ) THEN RAISE EXCEPTION 'Restore review holds campaign configuration changes' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles','share_options']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles','share_options'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
        -- A share option's short label (#1569) is Admin-only wording, outside
        -- the Family form and its digest, so it stays editable while the
        -- structure is locked: the share options are compared without it, in
        -- their order. Everything else about them stays fixed.
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (CASE WHEN jsonb_typeof(old_c.values->'share_options')='array' THEN
                    (SELECT coalesce(jsonb_agg(CASE WHEN jsonb_typeof(e.option)='object' THEN e.option - 'short_label' ELSE e.option END ORDER BY e.position),'[]'::jsonb)
                     FROM jsonb_array_elements(old_c.values->'share_options') WITH ORDINALITY e(option,position))
                    ELSE old_c.values->'share_options' END)
                IS DISTINCT FROM
                  (CASE WHEN jsonb_typeof(candidate.values->'share_options')='array' THEN
                    (SELECT coalesce(jsonb_agg(CASE WHEN jsonb_typeof(e.option)='object' THEN e.option - 'short_label' ELSE e.option END ORDER BY e.position),'[]'::jsonb)
                     FROM jsonb_array_elements(candidate.values->'share_options') WITH ORDINALITY e(option,position))
                    ELSE candidate.values->'share_options' END)) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
        -- The one reviewed live structural exemption (#342): an Administrator
        -- may change a locked campaign's Ministry selections while it is still
        -- open. The value must stay canonical: a strictly ascending array of
        -- whole numbers, as the YAML schema writes it. Removing is always
        -- allowed. Every added DUID must be visible now: in the promoted
        -- catalog, in a campaign with the Ministry module. Answers are never
        -- touched here.
        -- Nested CASEs fix the evaluation order, so a malformed value is
        -- refused, not miscast.
        SELECT c.state, old_c.values->'ministry_duids' INTO live_state, live_before
        FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
        WHERE c.id=target AND c.structural_locked
          AND old_c.values->'ministry_duids' IS DISTINCT FROM candidate.values->'ministry_duids';
        IF FOUND THEN
            IF live_state NOT IN ('scheduled','active')
                OR NOT coalesce(CASE WHEN jsonb_typeof(candidate.values->'ministry_duids')='array'
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)
                        WHERE jsonb_typeof(e.duid)<>'number' OR e.duid#>>'{}'!~'^[0-9]{1,19}$') THEN
                    candidate.values->'ministry_duids'=(SELECT coalesce(jsonb_agg(to_jsonb(d.duid) ORDER BY d.duid),'[]'::jsonb)
                        FROM (SELECT DISTINCT (e.duid#>>'{}')::numeric AS duid
                              FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)) d)
                    END,false) THEN
                RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
            END IF;
            -- The value is now a canonical array of whole numbers.
            SELECT coalesce(array_agg((added.duid#>>'{}')::numeric),'{}') INTO live_added
            FROM jsonb_array_elements(candidate.values->'ministry_duids') added(duid)
            WHERE NOT coalesce(live_before @> added.duid,false);
            IF cardinality(live_added)>0 THEN
                IF NOT coalesce(candidate.values->'modules' ? 'ministry',false)
                    OR EXISTS (SELECT 1 FROM unnest(live_added) d WHERE d NOT BETWEEN 1 AND 2147483647) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
                -- A separate statement, reached only when a locked open campaign
                -- gains a Ministry: PostgreSQL checks EXECUTE on the definer
                -- stewardship_ministry_catalog_v1 whenever a statement naming it
                -- starts, whatever a CASE or AND would evaluate. Only the
                -- configuration installer, which holds no source grants, needs
                -- it; setup completion and other activations never get here.
                IF EXISTS (SELECT 1 FROM unnest(live_added) d
                    WHERE NOT EXISTS (SELECT 1 FROM stewardship_ministry_catalog_v1() present
                        WHERE present.ministry_duid=d::integer)) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

-- The campaign's exact source scope, copied whole from the baseline (no
-- frozen file replaced it before). Only #1393's change: the window names the
-- campaign's campaign_families value when it has one, as source/windows.py
-- writes the request's window_canonical, so the request guard's equality
-- holds for a campaign with a rule. Without one the text is byte for byte
-- what it was, so the legacy campaign's window digest, its pending requests
-- and setup completion's window check are unchanged.
CREATE OR REPLACE FUNCTION public.stewardship_source_current_window_v1(campaign_id uuid) RETURNS text
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE campaign stewardship_campaign%ROWTYPE;
        values jsonb;
        periods jsonb := '[]'::jsonb;
        document jsonb;
BEGIN
    IF campaign_id IS NOT NULL THEN
        SELECT * INTO campaign FROM stewardship_campaign WHERE id=campaign_id;
        IF NOT FOUND OR campaign.state NOT IN
            ('draft','scheduled','active','closed','archived') THEN
            RAISE EXCEPTION 'Source campaign is unavailable' USING ERRCODE='23514';
        END IF;
        SELECT c.values INTO values FROM stewardship_campaign_configuration c
            WHERE c.id=campaign.active_configuration_id;
        IF campaign.state <> 'archived' AND values->'modules' ? 'financial'
           AND values->'financial' <> 'null'::jsonb THEN
            periods := jsonb_build_array(
                jsonb_build_object('start',values#>'{financial,start}',
                    'end',values#>'{financial,end}',
                    'funds',values#>'{financial,fund_duids}'),
                jsonb_build_object('start',values#>'{financial,comparison_start}',
                    'end',values#>'{financial,comparison_end}',
                    'funds',values#>'{financial,comparison_fund_duids}'));
        END IF;
    END IF;
    document := jsonb_build_object('campaign_id',campaign_id,'periods',periods);
    -- The campaign's Families rule (#1393) decides the load's eligibility, so
    -- it is part of the exact scope, as source/windows.py builds it. It joins
    -- only when the campaign has one: a legacy campaign's window text, and
    -- its digest, stay exactly what they were before rules existed.
    IF values->'campaign_families' IS NOT NULL
       AND values->'campaign_families' <> 'null'::jsonb THEN
        document := document || jsonb_build_object(
            'campaign_families', values->'campaign_families');
    END IF;
    RETURN stewardship_source_canonical(document);
END;
$$;

-- Financial stewardship (with pledge review, which reads the same selection)
-- and Additional information, copied whole from their newest definitions
-- (0110 and 0094). Only #1393's change: a Family row's "active", which draws
-- the "No longer in this campaign" badge and the Financial Active filter, is
-- the row's portal_eligible ("matches the campaign's Families rule") when the
-- row was loaded under a rule (it carries campaign_families). A legacy row
-- carries none and keeps ParishSoft's active exactly as before, so nothing
-- changes for the existing campaign. In place rather than as v2 functions,
-- as 0110 explains: the export capture functions keep calling these.
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
        -- Whether the Family is still one of the campaign's Families (NULL
        -- when it is not in the data), for its status badge and the Active
        -- filter (#1393). A row loaded under a Families rule names the rule
        -- (campaign_families) and its portal_eligible is "matches the rule";
        -- a legacy row names none and keeps ParishSoft's active.
        CASE WHEN p.canonical::jsonb ? 'campaign_families'
                AND jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
            THEN (p.canonical::jsonb->'portal_eligible')::boolean
            WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
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
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether the Family is still one of the campaign's Families
            -- (NULL when it is not in the data), for its status badge (#1216,
            -- #1393). A row loaded under a Families rule names the rule
            -- (campaign_families) and its portal_eligible is "matches the
            -- rule"; a legacy row names none and keeps ParishSoft's active.
            CASE WHEN p.canonical::jsonb ? 'campaign_families'
                    AND jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
                THEN (p.canonical::jsonb->'portal_eligible')::boolean
                WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
                THEN (p.canonical::jsonb->'active')::boolean END AS active,
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
        SELECT r.*,stewardship_family_heads_name_v1(r.surname,n.parts) AS display_name
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
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname','display_name']
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

-- The Additional information page's own selection (browser-local days),
-- created by 0064 and copied whole from its newest definition (0100), with
-- the same change; the v1 selection above remains for captures without a
-- zone (a rollback's). Neither is SECURITY DEFINER.
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
       OR NOT parameters ?& ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','disposition','needed','completed','start','end','sort','zone']
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
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether the Family is still one of the campaign's Families
            -- (NULL when it is not in the data), for its status badge (#1216,
            -- #1393). A row loaded under a Families rule names the rule
            -- (campaign_families) and its portal_eligible is "matches the
            -- rule"; a legacy row names none and keeps ParishSoft's active.
            CASE WHEN p.canonical::jsonb ? 'campaign_families'
                    AND jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
                THEN (p.canonical::jsonb->'portal_eligible')::boolean
                WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
                THEN (p.canonical::jsonb->'active')::boolean END AS active,
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
        SELECT r.*,stewardship_family_heads_name_v1(r.surname,n.parts) AS display_name
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
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname','display_name']
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
BEGIN
    -- Still the one invoker trigger function with its pinned search_path,
    -- holding 0166's rules plus the Families rule's two checks.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1'
          AND p.oid='public.stewardship_campaign_pointer_v1()'::regprocedure
          AND NOT p.prosecdef AND p.prorettype='trigger'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc, 'can only add current Ministries to an open campaign')>0
          AND strpos(p.prosrc, 'Successor creation is not admitted')>0
          AND strpos(p.prosrc, '''ministry_leader_roles'',''share_options'']')>0
          AND strpos(p.prosrc, 'e.option - ''short_label''')>0
          AND strpos(p.prosrc, 'The campaign''''s Families cannot be changed')>0
          AND strpos(p.prosrc, 'Invalid campaign Families rule')>0
          AND strpos(p.prosrc, '''campaign_families''')>0)<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0175: the campaign pointer function is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0175_acl', true) IS DISTINCT FROM (
           SELECT coalesce(p.proacl::text,'default')
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_campaign_pointer_v1') THEN
        RAISE EXCEPTION 'Migration 0175: the campaign pointer function''s grants changed';
    END IF;
    -- The exact source scope: still the one invoker, stable function with
    -- its pinned search_path, naming the rule only when the campaign has
    -- one, and with no campaign still exactly the text it always was.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_source_current_window_v1'
          AND p.oid='public.stewardship_source_current_window_v1(uuid)'::regprocedure
          AND NOT p.prosecdef AND p.provolatile='s' AND p.prorettype='text'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc, 'IF values->''campaign_families'' IS NOT NULL')>0
          AND strpos(p.prosrc, '''campaign_families'', values->''campaign_families''')>0
          AND strpos(p.prosrc, 'RETURN stewardship_source_canonical(document);')>0)<>1
       OR public.stewardship_source_current_window_v1(NULL)
           IS DISTINCT FROM '{"campaign_id":null,"periods":[]}' THEN
        RAISE EXCEPTION 'Migration 0175: the source window function is not installed as declared';
    END IF;
    -- The three report selections: still invoker and stable, each reading
    -- a rule-loaded row's portal_eligible as the Family's "active".
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_financial_report_v1','stewardship_information_report_v1',
              'stewardship_information_report_v2')
          AND NOT p.prosecdef AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
          AND strpos(p.prosrc, 'WHEN p.canonical::jsonb ? ''campaign_families''')>0
          AND strpos(p.prosrc, 'THEN (p.canonical::jsonb->''portal_eligible'')::boolean')>0)<>3
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_source_current_window_v1',
               'stewardship_financial_report_v1','stewardship_information_report_v1',
               'stewardship_information_report_v2'))<>4 THEN
        RAISE EXCEPTION 'Migration 0175: the report selections are not installed as declared';
    END IF;
    -- CREATE OR REPLACE keeps grants, owner and settings; check that nothing
    -- changed them.
    IF current_setting('stewardship.migration_0175_reads', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                   ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
                   ||'/'||p.proowner::regrole::text,';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_source_current_window_v1',
               'stewardship_financial_report_v1','stewardship_information_report_v1',
               'stewardship_information_report_v2')) THEN
        RAISE EXCEPTION 'Migration 0175: a replaced function''s grants, owner or settings changed';
    END IF;
END
$check$;
