-- Frozen forward migration file 0157 (the repository-wide file sequence):
-- the person identity reads (#1867, part of #1570; data spec, Person
-- identity reads). This file is installed by its Django migration in the
-- accounts app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. Reversing
-- needs its own forward migration.
--
-- A user's Timeline shows the ParishSoft Family the user's address belongs
-- to, and a Family's Timeline names the Family's users. Both need to match
-- an address against Family and Member contact records, which the web login
-- may not read (contacts are read only through definers, as
-- stewardship_ministry_leaders_v1 reads them). This file adds two narrow
-- SECURITY DEFINER reads that return identifiers only:
--
-- stewardship_address_people_v1(address, actor) returns (family_duid,
-- member_duid) for each Family contact (member_duid NULL) and each Member
-- contact (with that Member's Family) in the current ParishSoft snapshot
-- holding the address as a valid email, compared in lower case. Only
-- Families present in that snapshot are returned.
--
-- stewardship_family_users_v1(family_duid, actor) returns the record ids of
-- the active configuration's users (address rules with a role) whose
-- address, in lower case, is a valid email of the Family's contact or of one
-- of its Members' contacts in the current snapshot.
--
-- Both enforce, in SQL, what 0116's Sent mail reads enforce: the caller must
-- be the web login (or the schema owner, for maintenance and tests), and
-- actor must be an enabled portal user whose role is Administrator
-- (stewardship_export_authorized_v1(actor, true)). Anything else is refused
-- with SQLSTATE 42501, and malformed input with 22023. The search_path is
-- fixed, PUBLIC has no EXECUTE, and database-grants grants EXECUTE only to
-- the web login (runtime_grants.runtime_functions). The pages' own view
-- audit records name the actor and subject; the functions add no audit of
-- their own, as the other definer reads do not. Nothing is returned before
-- the first promoted snapshot.
--
-- Cost: the current snapshot's contacts are read through the snapshot
-- membership's (snapshot_id, source_key) key. The address read parses every
-- contact of that snapshot once (a few thousand at parish scale, as
-- stewardship_ministry_leaders_v1 parses the roster); the Family read only
-- the Family's own contact and its Members'. No index is added.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_address_people_v1(address_text text, actor_uuid uuid)
RETURNS TABLE(family_duid bigint, member_duid bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    current_snapshot uuid;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'People are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'People require an Administrator' USING ERRCODE='42501';
    END IF;
    IF address_text IS NULL OR length(address_text) NOT BETWEEN 3 AND 254
       OR strpos(address_text,'@')=0 THEN
        RAISE EXCEPTION 'Invalid email address' USING ERRCODE='22023';
    END IF;
    -- Read on its own first, so the query below is planned with the
    -- current snapshot as a known value (as 0042's leaders read does).
    SELECT pointer.snapshot_id INTO current_snapshot
      FROM public.stewardship_source_current pointer WHERE pointer.singleton;
    IF current_snapshot IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    WITH matched AS MATERIALIZED (
        SELECT contact.owner_kind, contact.owner_key
          FROM public.stewardship_snapshot_contact cm
          JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
         CROSS JOIN LATERAL (SELECT contact.canonical::jsonb AS doc) parsed
         WHERE cm.snapshot_id=current_snapshot
           AND parsed.doc->'schema_version'='1'::jsonb
           AND jsonb_typeof(parsed.doc->'emails')='array'
           AND EXISTS (
               SELECT 1 FROM jsonb_array_elements(parsed.doc->'emails') item(value)
                WHERE item.value->'valid'='true'::jsonb
                  AND jsonb_typeof(item.value->'value')='string'
                  AND lower(item.value->>'value')=lower(address_text))
    ), people AS (
        SELECT matched.owner_key AS family_key, NULL::text AS member_key
          FROM matched WHERE matched.owner_kind='family'
        UNION
        SELECT member.family_key, matched.owner_key
          FROM matched
          JOIN public.stewardship_snapshot_member mm
            ON mm.snapshot_id=current_snapshot AND mm.source_key=matched.owner_key
          JOIN public.stewardship_source_member member ON member.id=mm.payload_id
         WHERE matched.owner_kind='member'
    )
    -- Each DUID is a positive key of at most 18 digits; CASE orders the
    -- cast after the shape test.
    SELECT DISTINCT
           CASE WHEN people.family_key ~ '^[0-9]{1,18}$' THEN people.family_key::bigint END,
           CASE WHEN people.member_key ~ '^[0-9]{1,18}$' THEN people.member_key::bigint END
      FROM people
      JOIN public.stewardship_snapshot_family fm
        ON fm.snapshot_id=current_snapshot AND fm.source_key=people.family_key
     WHERE people.family_key ~ '^[0-9]{1,18}$'
       AND (people.member_key IS NULL OR people.member_key ~ '^[0-9]{1,18}$')
     ORDER BY 1, 2;
END $$;
ALTER FUNCTION public.stewardship_address_people_v1(text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_address_people_v1(text, uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_family_users_v1(family_number bigint, actor_uuid uuid)
RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    current_snapshot uuid;
    wanted text;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'People are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'People require an Administrator' USING ERRCODE='42501';
    END IF;
    IF family_number IS NULL OR family_number NOT BETWEEN 1 AND 999999999999999999 THEN
        RAISE EXCEPTION 'Invalid Family DUID' USING ERRCODE='22023';
    END IF;
    wanted := family_number::text;
    SELECT pointer.snapshot_id INTO current_snapshot
      FROM public.stewardship_source_current pointer WHERE pointer.singleton;
    IF current_snapshot IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY
    WITH owners AS (
        SELECT 'family:'||wanted AS contact_key
        UNION ALL
        SELECT 'member:'||mm.source_key
          FROM public.stewardship_snapshot_member mm
          JOIN public.stewardship_source_member member ON member.id=mm.payload_id
         WHERE mm.snapshot_id=current_snapshot AND member.family_key=wanted
    ), addresses AS MATERIALIZED (
        SELECT DISTINCT lower(item.value->>'value') AS address
          FROM owners
          JOIN public.stewardship_snapshot_contact cm
            ON cm.snapshot_id=current_snapshot AND cm.source_key=owners.contact_key
          JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
         CROSS JOIN LATERAL (SELECT contact.canonical::jsonb AS doc) parsed
         CROSS JOIN LATERAL jsonb_array_elements(
               CASE WHEN jsonb_typeof(parsed.doc->'emails')='array'
                    THEN parsed.doc->'emails' ELSE '[]'::jsonb END) item(value)
         WHERE parsed.doc->'schema_version'='1'::jsonb
           AND item.value->'valid'='true'::jsonb
           AND jsonb_typeof(item.value->'value')='string'
    )
    SELECT DISTINCT rule.record_id
      FROM public.stewardship_system_configuration runtime
      JOIN public.stewardship_address_rule rule
        ON rule.configuration_id=runtime.active_configuration_id
      JOIN addresses ON addresses.address=lower(rule.email)
     WHERE rule.role IS NOT NULL;
END $$;
ALTER FUNCTION public.stewardship_family_users_v1(bigint, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_family_users_v1(bigint, uuid) FROM PUBLIC;

-- Refuse to commit unless both functions are installed exactly as declared:
-- stable definers with the fixed search path and the declared results,
-- which PUBLIC cannot execute. It reads only the catalog and writes
-- nothing. Missing functions are checked first, on their own: PostgreSQL
-- does not short-circuit OR, so the ::regprocedure casts below would
-- otherwise fail with their own error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_address_people_v1(text,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_family_users_v1(bigint,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0157 (person identity reads) is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p
         WHERE p.oid IN ('public.stewardship_address_people_v1(text,uuid)'::regprocedure,
                         'public.stewardship_family_users_v1(bigint,uuid)'::regprocedure)
           AND p.prosecdef AND p.provolatile='s' AND p.proretset
           AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])<>2
       OR pg_get_function_result('public.stewardship_address_people_v1(text,uuid)'::regprocedure)
          <>'TABLE(family_duid bigint, member_duid bigint)'
       OR pg_get_function_result('public.stewardship_family_users_v1(bigint,uuid)'::regprocedure)
          <>'SETOF uuid'
       OR has_function_privilege('public',
          'public.stewardship_address_people_v1(text,uuid)', 'EXECUTE')
       OR has_function_privilege('public',
          'public.stewardship_family_users_v1(bigint,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0157 (person identity reads) is not installed as declared';
    END IF;
END
$check$;
