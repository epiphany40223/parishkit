-- Frozen forward migration file 0026 (the repository-wide file sequence):
-- stewardship_chair_decisions_v1 reads the current snapshot's chair
-- relationships once, as a materialized CTE, instead of a correlated read of
-- stewardship_current_chair (#147). The Django migration that names this file
-- in FROZEN_SQL installs it. This file must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copy of the function still equals the fresh-install baseline's
-- (schema/functions.sql).
--
-- The function's result is unchanged. Its body used a subquery whose CASE
-- result, reason, appears twice in jsonb_build_object; when the planner
-- flattened the subquery it copied the CASE, and with it the correlated
-- EXISTS over the ten-relation current-chair view, into both places. Every
-- call (twice per source promotion, and once per configuration activation,
-- all under the work-order lock) spent 100-200 ms planning and under 1 ms
-- executing. Read once as a materialized CTE, the view plans in about 15 ms.
-- EXISTS over its DISTINCT projection, with the same four equality
-- predicates, matches exactly what EXISTS over the view matched.
--
-- The function is LANGUAGE sql STABLE with its own search_path in the
-- baseline and is not SECURITY DEFINER; CREATE OR REPLACE names the same
-- attributes and keeps its owner and grants, so nothing is re-altered. No
-- row changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function, exactly as the fresh-install functions.sql defines it.
CREATE OR REPLACE FUNCTION public.stewardship_chair_decisions_v1(configuration uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    -- The current snapshot's chair relationships, read once. The planner
    -- copies the CASE below into both uses of reason, so a correlated read
    -- of the view there planned its ten-relation join twice per call, about
    -- 100-200 ms under the work-order lock (#147).
    WITH chair AS MATERIALIZED (
        SELECT DISTINCT organization_id, member_duid, ministry_duid, email
        FROM stewardship_current_chair)
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'assignment_record_id',record_id::text,
        'active',reason='current_chair','reason',reason) ORDER BY record_id),
        '[]'::jsonb)
    FROM (
        SELECT assignment.record_id, CASE
            WHEN evidence.id IS NULL THEN 'missing_binding'
            WHEN evidence.organization_id::text IS DISTINCT FROM
                integration.settings->>'organization_id' THEN 'organization_changed'
            WHEN EXISTS (SELECT 1 FROM stewardship_ministry_activity activity
                WHERE activity.configuration_id=configuration
                  AND activity.organization_id=evidence.organization_id
                  AND activity.ministry_duid=assignment.ministry_duid
                  AND NOT activity.active) THEN 'ministry_inactive'
            WHEN EXISTS (SELECT 1 FROM chair
                WHERE chair.organization_id=evidence.organization_id
                  AND chair.member_duid=evidence.member_duid
                  AND chair.ministry_duid=assignment.ministry_duid
                  AND chair.email=assignment.email) THEN 'current_chair'
            ELSE 'relationship_missing' END AS reason
        FROM stewardship_ministry_assignment assignment
        LEFT JOIN stewardship_chair_seed_evidence evidence
            ON evidence.assignment_record_id=assignment.record_id
        LEFT JOIN stewardship_applied_integration integration
            ON integration.configuration_id=configuration
            AND integration.kind='parishsoft'
        WHERE assignment.configuration_id=configuration
            AND assignment.source='chair-seed'
    ) decisions;
$$;

-- Refuse to commit unless the new body is installed with unchanged attributes.
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_chair_decisions_v1'
                     AND pg_get_function_identity_arguments(p.oid)='configuration uuid'
                     AND p.prorettype='jsonb'::regtype
                     AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')
                     AND p.provolatile='s'
                     AND NOT p.prosecdef
                     AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND p.prosrc LIKE '%WITH chair AS MATERIALIZED (%'
                     AND p.prosrc LIKE '%WHEN EXISTS (SELECT 1 FROM chair%'
                     AND p.prosrc NOT LIKE '%FROM stewardship_current_chair chair%') THEN
        RAISE EXCEPTION 'stewardship_chair_decisions_v1 does not read current chairs once';
    END IF;
END
$check$;
