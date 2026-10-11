-- Frozen forward migration file 0056 (the repository-wide file sequence):
-- the retired Talents mechanism leaves the database (#970, slice 3). This
-- file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced guard equals the
-- fresh-install baseline's (schema/guards.sql). A fresh install runs the
-- baseline, which already has the new guard and neither dropped function,
-- the earlier files and then this, and ends in the same catalog as an
-- upgraded database.
--
-- The Family form stopped asking for talents and the report became the
-- Limitations report in the application (#970 slices 1 and 2); this removes
-- what the database still kept for talents:
--   stewardship_service_answers_guard_v1  replaced: the baseline body, but
--                                     each new Member entry's talents map
--                                     must be empty, so the talent list
--                                     lookup and the talent text checks go
--   stewardship_talent_defaults_v1    dropped: only that lookup called it
--   stewardship_limitations_report_v1 new: the Limitations report, the
--                                     former talent report without talents
--                                     and with each Member row's Member DUID
--                                     (#932); its filter is `show` ('any',
--                                     'cannot_serve' or 'cannot_attend')
--   stewardship_talent_report_v1      dropped: the application calls the
--                                     Limitations report instead
-- Stored data is untouched. Submissions are immutable and the guard runs
-- only when a new one is inserted, so older submissions keep the talents
-- they recorded and campaign configurations keep their talent lists (the
-- form definition digest still hashes them). The application stores an
-- empty map, dropping any talents a form opened before #970 still sends.
-- The download audit's talent_option_id context key stays, so recorded
-- rows still validate and read.
--
-- The guard keeps its owner, grants and attributes (it is an invoker
-- function, not SECURITY DEFINER, with a fixed search_path); the new report
-- is an invoker function with default grants, as the dropped one was, and
-- reads only what its caller's grants allow. DROP ... IF EXISTS because a
-- fresh install's baseline already lacks both functions; no CASCADE, so an
-- unexpected dependent object fails the migration instead of being dropped
-- with it. The scripted upgrade stops every application service before
-- migrating, so no running process still calls a dropped function. The DO
-- block at the end refuses to commit unless everything is installed as
-- declared. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced guard's grants before the replacement, compared in the DO
-- block. A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0056_acl', (
    SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default'),';'),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_service_answers_guard_v1'), true);

CREATE OR REPLACE FUNCTION public.stewardship_service_answers_guard_v1(
    response public.stewardship_submission, campaign public.stewardship_campaign_configuration)
    RETURNS void LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    answers jsonb := response.answers->'service';
    group_name text; member_key text; entry jsonb; choices jsonb; current_count integer;
BEGIN
    IF NOT (campaign.values->'modules' ? 'ministry') THEN
        IF answers IS DISTINCT FROM '{}'::jsonb THEN
            RAISE EXCEPTION 'Disabled Ministry service answers must be empty' USING ERRCODE='23514';
        END IF;
        RETURN;
    END IF;
    IF jsonb_typeof(answers) IS DISTINCT FROM 'object'
       OR NOT (answers ?& ARRAY['members','proposed_members'])
       OR answers-ARRAY['members','proposed_members'] <> '{}'::jsonb THEN
        RAISE EXCEPTION 'Service answers must be complete' USING ERRCODE='23514';
    END IF;
    FOREACH group_name IN ARRAY ARRAY['members','proposed_members'] LOOP
        IF jsonb_typeof(answers->group_name) IS DISTINCT FROM 'object'
           OR (SELECT coalesce(array_agg(key ORDER BY key),'{}'::text[])
               FROM jsonb_object_keys(answers->group_name) key)
              IS DISTINCT FROM (SELECT coalesce(array_agg(key ORDER BY key),'{}'::text[])
               FROM jsonb_object_keys(response.answers->'ministries'->group_name) key) THEN
            RAISE EXCEPTION 'Service identities must match the Ministry answer' USING ERRCODE='23514';
        END IF;
        FOR member_key,entry IN SELECT * FROM jsonb_each(answers->group_name) LOOP
            IF jsonb_typeof(entry) IS DISTINCT FROM 'object'
               OR NOT (entry ?& ARRAY['cannot_serve','talents'])
               OR entry-ARRAY['cannot_serve','talents'] <> '{}'::jsonb
               OR jsonb_typeof(entry->'cannot_serve') IS DISTINCT FROM 'boolean'
               OR entry->'talents' IS DISTINCT FROM '{}'::jsonb THEN
                RAISE EXCEPTION 'Service entry requires exact fields and no talents' USING ERRCODE='23514';
            END IF;
            IF entry->'cannot_serve'='true'::jsonb THEN
                choices := response.answers->'ministries'->group_name->member_key;
                -- The Ministry guard already proved each leave is a current,
                -- offered membership; equal counts therefore mean all of them.
                current_count := CASE WHEN group_name='proposed_members' THEN 0 ELSE (
                    SELECT count(*) FROM jsonb_array_elements_text(campaign.values->'ministry_duids') duid
                    WHERE public.stewardship_response_ministry_visible_v1(
                              response.validation_source_id,response.configuration_id,
                              response.campaign_id,duid::integer)
                      AND public.stewardship_response_ministry_current_v1(
                              response.validation_source_id,response.family_id,member_key,
                              duid::integer) IS TRUE) END;
                IF choices->'join' <> '[]'::jsonb
                   OR coalesce(jsonb_array_length(choices->'leave'),0) <> current_count THEN
                    RAISE EXCEPTION 'A Member who cannot participate must stop every Ministry' USING ERRCODE='23514';
                END IF;
            END IF;
        END LOOP;
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS public.stewardship_talent_defaults_v1();

-- Limitations (#247, #970): from each Family's currently effective live
-- response, the Members who cannot participate in ministries and the
-- Families who cannot attend Mass or prayer services. Filters are a closed
-- vocabulary: `search` (name or Family DUID text, at most 200 characters)
-- and `show` ('any', 'cannot_serve' or 'cannot_attend'). The selection,
-- names and order are the former talent report's; stored talents are
-- neither listed nor counted. Each Member row also carries its Member DUID
-- (the entry's key; NULL for a proposed Member, whose key is the form's
-- local identity), so the Members table can show it (#932).
CREATE FUNCTION public.stewardship_limitations_report_v1(campaign_uuid uuid, parameters jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT off, as in every report function (#277).
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE answer jsonb; bad boolean;
BEGIN
    -- Statements run in order, so each check relies only on earlier ones.
    bad:=jsonb_typeof(parameters) IS DISTINCT FROM 'object';
    IF NOT bad THEN
        bad:=coalesce(NOT parameters ?& ARRAY['search','show']
            OR parameters-ARRAY['search','show']<>'{}'::jsonb
            OR jsonb_typeof(parameters->'search') IS DISTINCT FROM 'string'
            OR jsonb_typeof(parameters->'show') IS DISTINCT FROM 'string',true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(length(parameters->>'search')>200
            OR parameters->>'show' NOT IN ('any','cannot_serve','cannot_attend'),true);
    END IF;
    IF bad THEN
        RAISE EXCEPTION 'Invalid limitations report parameters' USING ERRCODE='23514';
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
            'Unavailable Family') AS family_name
    FROM selected x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), members AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.submitted_at,g.grp,e.key AS member_key,
        -- The Family's own corrected name first, then the parish record.
        coalesce(nullif(btrim(concat_ws(' ',
                nullif(btrim(r.answers->g.grp->e.key->>'first_name'),''),
                nullif(btrim(r.answers->g.grp->e.key->>'last_name'),''))),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(sm.canonical::jsonb->>'firstName'),''),
                nullif(btrim(sm.canonical::jsonb->>'lastName'),''))),''),
            'Unavailable name') AS member_name
    FROM responses r
    CROSS JOIN LATERAL (VALUES ('members'),('proposed_members')) g(grp)
    CROSS JOIN LATERAL jsonb_each(coalesce(r.answers->'service'->g.grp,'{}'::jsonb)) e
    CROSS JOIN selected x
    LEFT JOIN stewardship_snapshot_member mm
        ON g.grp='members' AND mm.snapshot_id=x.source_id AND mm.source_key=e.key
    LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    WHERE e.value->'cannot_serve'='true'::jsonb
      AND parameters->>'show' IN ('any','cannot_serve')
), matching_members AS MATERIALIZED (
    SELECT * FROM members q
    WHERE parameters->>'search'=''
       OR position(lower(parameters->>'search') IN lower(q.member_name))>0
       OR position(lower(parameters->>'search') IN lower(q.family_name))>0
       OR position(parameters->>'search' IN q.family_duid::text)>0
), families AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.submitted_at FROM responses r
    WHERE r.answers->'cannot_attend'='true'::jsonb
      AND parameters->>'show' IN ('any','cannot_attend')
      AND (parameters->>'search'=''
            OR position(lower(parameters->>'search') IN lower(r.family_name))>0
            OR position(parameters->>'search' IN r.family_duid::text)>0)
)
SELECT CASE WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'source_id',x.source_id),
    'summary',jsonb_build_object(
        'cannot_serve',(SELECT count(*) FROM matching_members),
        'cannot_attend',(SELECT count(*) FROM families)),
    'members',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',family_name,'family_duid',family_duid,
            'member_name',member_name,'proposed',grp='proposed_members',
            'member_duid',CASE WHEN grp='members' AND member_key ~ '^[0-9]{1,18}$'
                THEN member_key::bigint END,
            'cannot_serve',true,'submitted_at',submitted_at)
        ORDER BY lower(family_name),family_duid,lower(member_name),member_key)
        FROM matching_members),'[]'::jsonb),
    'families',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',family_name,'family_duid',family_duid,
            'submitted_at',submitted_at)
        ORDER BY lower(family_name),family_duid) FROM families),'[]'::jsonb)) END
INTO answer FROM (SELECT 1) one LEFT JOIN selected x ON true;
    RETURN answer;
END $$;

DROP FUNCTION IF EXISTS public.stewardship_talent_report_v1(uuid, jsonb);

-- Refuse to commit unless the guard has its new body, attributes and
-- grants, both talent functions are gone, and the Limitations report is
-- installed once as declared. No temporary objects: the migration login has
-- no TEMP grant.
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid=to_regprocedure('public.stewardship_service_answers_guard_v1('
                 'public.stewardship_submission,public.stewardship_campaign_configuration)')
             AND NOT prosecdef AND provolatile='v' AND prorettype='void'::regtype
             AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%OR entry->''talents'' IS DISTINCT FROM ''{}''::jsonb THEN%'
             AND prosrc NOT LIKE '%talent_defaults%'
             AND prosrc NOT LIKE '%talent_options%'
             AND prosrc LIKE '%A Member who cannot participate must stop every Ministry%') THEN
        RAISE EXCEPTION 'stewardship_service_answers_guard_v1 is not installed as declared';
    END IF;
    -- CREATE OR REPLACE keeps the owner and grants; prove it.
    IF current_setting('stewardship.migration_0056_acl', true) IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default'),';'),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_service_answers_guard_v1') THEN
        RAISE EXCEPTION 'Migration 0056 changed the guard''s grants';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_talent_defaults_v1','stewardship_talent_report_v1')) THEN
        RAISE EXCEPTION 'A retired talent function is still installed';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_limitations_report_v1')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid=to_regprocedure('public.stewardship_limitations_report_v1(uuid,jsonb)')
             AND NOT prosecdef AND proacl IS NULL AND provolatile='s'
             AND prorettype='jsonb'::regtype
             AND proconfig @> ARRAY['jit=off','search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%parameters->>''show'' NOT IN (''any'',''cannot_serve'',''cannot_attend'')%'
             AND prosrc LIKE '%''member_duid'',CASE WHEN grp=''members''%'
             AND prosrc NOT LIKE '%talent%') THEN
        RAISE EXCEPTION 'stewardship_limitations_report_v1 is not installed as declared';
    END IF;
END
$check$;
