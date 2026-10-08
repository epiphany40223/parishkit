-- Frozen forward migration file 0038 (the repository-wide file sequence): the
-- interactive Ministry report and follow-up pages take their role scope from
-- SQL (#389 L3). This file is installed by its Django migration and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. A later change gets its own numbered file. A fresh install
-- runs the baseline, the earlier files and then this, and ends in the same
-- catalog as an upgraded database.
--
-- stewardship_ministry_report_v1 and stewardship_ministry_followup_v1 take
-- the caller's scope as arguments (operational, ministry_scope). The export
-- captures already derive it in SQL from stewardship_ministry_scope_v1, but
-- the pages passed what Python computed, so a Python bug in that computation
-- could widen what a Ministry leader sees. These two wrappers take only the
-- actor and read its current scope from the same function the exports use;
-- an actor with no current Admin rule gets the empty scope (operational
-- false, no Ministries), which the reports answer with authorized false.
-- The two report functions are unchanged: the export captures still call
-- them with SQL-derived scope.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_ministry_report_for_v1(
    actor uuid, campaign_uuid uuid, filters jsonb,
    ministry_id integer DEFAULT NULL, request_action text DEFAULT 'join',
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
    SELECT public.stewardship_ministry_report_v1(campaign_uuid, filters,
        coalesce((s.scope->>'operational')::boolean, false),
        ARRAY(SELECT value::bigint
            FROM jsonb_array_elements_text(coalesce(s.scope->'ministries','[]'::jsonb))),
        ministry_id, request_action, page_limit, page_offset)
    FROM (SELECT public.stewardship_ministry_scope_v1(actor) AS scope) s
$$;

CREATE FUNCTION public.stewardship_ministry_followup_for_v1(
    actor uuid, campaign_uuid uuid, filters jsonb, request_uuid uuid DEFAULT NULL,
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_ministry_followup_v1(campaign_uuid, filters,
        coalesce((s.scope->>'operational')::boolean, false),
        ARRAY(SELECT value::bigint
            FROM jsonb_array_elements_text(coalesce(s.scope->'ministries','[]'::jsonb))),
        actor, request_uuid, page_limit, page_offset)
    FROM (SELECT public.stewardship_ministry_scope_v1(actor) AS scope) s
$$;

-- Refuse to commit unless both wrappers are installed as declared: invokers
-- (the caller's own grants still apply) with a fixed search_path.
DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p
        WHERE p.oid IN (
            'public.stewardship_ministry_report_for_v1(uuid,uuid,jsonb,integer,text,integer,integer)'::regprocedure,
            'public.stewardship_ministry_followup_for_v1(uuid,uuid,jsonb,uuid,integer,integer)'::regprocedure)
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.proconfig[1]='search_path=pg_catalog, public, pg_temp')<>2 THEN
        RAISE EXCEPTION 'Migration 0038 (Ministry page scope) is not installed as declared';
    END IF;
END
$check$;
