-- Frozen forward migration file 0040 (the repository-wide file sequence):
-- the Ministry report and Ministry follow-up pages read their scope in SQL
-- from the signed-in actor (#389, L3). It is installed by the Django
-- migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest.
--
-- stewardship_ministry_report_v1 and stewardship_ministry_followup_v1 take
-- the caller's scope as arguments (operational, ministry_scope). The pages
-- passed Python's view of the signed-in user's roles and Ministries, so a
-- Python bug could widen a Ministry leader's report. Export captures already
-- derive that scope in SQL from stewardship_ministry_scope_v1(actor_id).
--
-- Two new functions take the actor instead and derive the scope the same
-- way, then call the unchanged v1 selection:
--   stewardship_ministry_report_v2     the Ministry report page
--   stewardship_ministry_followup_v2   the Ministry follow-up page; the
--                                      viewer is the actor
-- An actor with no current scope (unknown, disabled, or holding no report
-- role) reads with operational false and no Ministries, so v1 returns
-- authorized false and no rows. Administrators and Staff read as before.
-- Both are invoker functions (not SECURITY DEFINER) with a fixed
-- search_path, executable as every other invoker function is, and read only
-- what their caller's grants already allow. No existing function changes,
-- so no earlier body is copied here. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_ministry_report_v2(
    campaign_uuid uuid, filters jsonb, actor_uuid uuid,
    ministry_id integer DEFAULT NULL, request_action text DEFAULT 'join',
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_ministry_report_v1(campaign_uuid,filters,
        coalesce(p.scope->'operational'='true'::jsonb,false),
        ARRAY(SELECT m.value::bigint FROM jsonb_array_elements_text(
            coalesce(p.scope->'ministries','[]'::jsonb)) m(value)),
        ministry_id,request_action,page_limit,page_offset)
    FROM (SELECT public.stewardship_ministry_scope_v1(actor_uuid) AS scope) p
$$;

CREATE FUNCTION public.stewardship_ministry_followup_v2(
    campaign_uuid uuid, filters jsonb, actor_uuid uuid,
    request_uuid uuid DEFAULT NULL,
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_ministry_followup_v1(campaign_uuid,filters,
        coalesce(p.scope->'operational'='true'::jsonb,false),
        ARRAY(SELECT m.value::bigint FROM jsonb_array_elements_text(
            coalesce(p.scope->'ministries','[]'::jsonb)) m(value)),
        actor_uuid,request_uuid,page_limit,page_offset)
    FROM (SELECT public.stewardship_ministry_scope_v1(actor_uuid) AS scope) p
$$;

DO $check$
BEGIN
    -- Each is installed with its declared signature and attributes, and
    -- derives its scope from the actor rather than from an argument.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND ((p.proname='stewardship_ministry_report_v2'
                AND pg_get_function_identity_arguments(p.oid)=
                    'campaign_uuid uuid, filters jsonb, actor_uuid uuid, '
                    'ministry_id integer, request_action text, '
                    'page_limit integer, page_offset integer')
            OR (p.proname='stewardship_ministry_followup_v2'
                AND pg_get_function_identity_arguments(p.oid)=
                    'campaign_uuid uuid, filters jsonb, actor_uuid uuid, '
                    'request_uuid uuid, page_limit integer, page_offset integer'))
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%public.stewardship_ministry_scope_v1(actor_uuid)%')<>2 THEN
        RAISE EXCEPTION 'Migration 0040 (Ministry report actor scope) is not installed as declared';
    END IF;
END
$check$;
