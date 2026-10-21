-- Frozen forward migration file 0170 (the repository-wide file sequence;
-- number assigned centrally): the Ministries one user's address leads, for
-- that user's Timeline (#1925; admin portal spec, User timeline). This file
-- is installed by its Django migration in the accounts app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- A user's Timeline shows "Ministry leader of" from the shared leader scope,
-- stewardship_ministry_leader_scope_v1(user), which is keyed by a Google
-- account (stewardship_portal_user) row. That row exists only after a first
-- sign-in, so a Ministry leader who never signed in showed no Ministries,
-- although ParishSoft already says what they lead. The address-keyed leader
-- list, stewardship_ministry_leaders_v1(), is a definer no login may
-- execute, and the only address-keyed read the web has
-- (stewardship_leader_emails_v1) writes an audit event on every call and
-- covers only the Ministry leader role.
--
-- stewardship_address_leader_scope_v1(address, actor) returns what the
-- scope function would return for a Google account with that address: the
-- ascending DUIDs of the Ministries stewardship_ministry_leaders_v1 (the
-- one definition, with the campaign's configured leader roles) lists for
-- the address, compared in lower case, or [] unless the address's current
-- user (stewardship_address_rule in the active configuration) is a Ministry
-- leader, Staff or Administrator, the same role gate as the scope function.
-- It reads and writes nothing else and returns nothing else: no Member
-- DUID, name or other contact field.
--
-- It enforces what Sent mail's address read (0116) enforces, in SQL: the
-- caller must be the web login (or the schema owner, for maintenance and
-- tests), and actor must be an enabled portal user whose role is
-- Administrator (stewardship_export_authorized_v1(actor, true)). Anything
-- else is refused with SQLSTATE 42501, and a malformed address with 22023.
-- It is STABLE and writes nothing, so it runs in the page's read-only
-- snapshot; the page's user_timeline_viewed audit record names the actor,
-- and the function adds no audit of its own, as the other definer reads do
-- not. The search_path is fixed, PUBLIC has no EXECUTE, and database-grants
-- grants EXECUTE only to the web login (runtime_grants.runtime_functions).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_address_leader_scope_v1(address_text text, actor_uuid uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'A user''s Ministries are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'A user''s Ministries require an Administrator' USING ERRCODE='42501';
    END IF;
    IF address_text IS NULL OR length(address_text) NOT BETWEEN 3 AND 254
       OR strpos(address_text,'@')=0 THEN
        RAISE EXCEPTION 'Invalid email address' USING ERRCODE='22023';
    END IF;
    RETURN (
        SELECT coalesce(jsonb_agg(DISTINCT leader.ministry_duid ORDER BY leader.ministry_duid),
            '[]'::jsonb)
        FROM public.stewardship_system_configuration r
        JOIN public.stewardship_address_rule a
            ON a.configuration_id=r.active_configuration_id AND a.email=lower(address_text)
        JOIN public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(address_text)
        WHERE coalesce(a.roles,'[]'::jsonb) ?| ARRAY['administrator','staff','ministry_leader']);
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_address_leader_scope_v1(text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_address_leader_scope_v1(text, uuid) FROM PUBLIC;

-- Refuse to commit unless the function is installed exactly as declared: a
-- stable definer returning jsonb with the fixed search_path, its caller,
-- Administrator and leader-definition checks in its body, and EXECUTE held
-- by no role but its owner (database-grants grants the web login later).
-- It reads only the catalog and writes nothing. A missing function is
-- checked first, on its own: PostgreSQL does not short-circuit OR, so the
-- ::regprocedure casts below would otherwise fail with their own error
-- instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_address_leader_scope_v1(text,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0170 (address leader scope) is not installed as declared';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
         WHERE p.oid='public.stewardship_address_leader_scope_v1(text,uuid)'::regprocedure
           AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
           AND p.prorettype='jsonb'::regtype
           AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
           AND p.prosrc LIKE '%session_user<>''pk_stewardship_web''%'
           AND p.prosrc LIKE '%public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE%'
           AND p.prosrc LIKE '%public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(address_text)%'
           AND p.prosrc LIKE '%?| ARRAY[''administrator'',''staff'',''ministry_leader'']%'
           AND NOT EXISTS (SELECT 1 FROM aclexplode(p.proacl) grant_row
                            WHERE grant_row.grantee<>p.proowner))
       OR has_function_privilege('public',
          'public.stewardship_address_leader_scope_v1(text,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0170 (address leader scope) is not installed as declared';
    END IF;
END
$check$;
