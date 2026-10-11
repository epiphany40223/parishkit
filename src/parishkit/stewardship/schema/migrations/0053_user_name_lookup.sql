-- Frozen forward migration file 0053 (the repository-wide file sequence):
-- the Users page's Name lookup (#952, slice 5). It is installed by the
-- Django migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. It replaces nothing, so the fresh-install baseline is unchanged:
-- a fresh install runs the baseline, the earlier files and then this.
--
-- When the Administrator leaves the Email field on New user, the page asks
-- the server for the distinct names of the ParishSoft Members whose contact
-- record lists that address (admin-portal specification, "Name from
-- ParishSoft"). The web login has no grant on Member or contact records,
-- and migration 0047's names definer answers only for the active rules' and
-- recorded identities' addresses, which a new user's address usually is
-- not. This file:
--
--   adds stewardship_member_names_for_v1(text)
--                                            the ordered, distinct names of
--                                            the current Members listing one
--                                            address (definer; web)
--
-- The body is 0047's stewardship_member_names_v1 with its set of wanted
-- addresses replaced by the one argument, matched trimmed and
-- case-insensitively as sign-in matches, with no Gmail dot or plus folding.
-- It returns only names, never another contact field, a DUID or a Family;
-- an address longer than an email address can be answers no names. Web gets
-- EXECUTE through `database-grants` (runtime_grants.MEMBER_NAMES_FOR_FUNCTION).
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_member_names_for_v1(address text)
    RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    WITH wanted AS (
        SELECT lower(btrim(address)) AS address
        WHERE address IS NOT NULL AND length(btrim(address)) BETWEEN 3 AND 254
    ), listed AS (
        SELECT
            concat_ws(', ',
                nullif(btrim(parsed.member_doc->>'lastName'), ''),
                nullif(btrim(parsed.member_doc->>'firstName'), '')) AS name,
            coalesce(parsed.family_doc->'active'='true'::jsonb, false) AS active_family,
            EXISTS (SELECT 1 FROM jsonb_array_elements_text(
                        parsed.family_doc->'active_head_duids') head(duid)
                    WHERE head.duid = member.source_key) AS head,
            CASE WHEN member.source_key ~ '^[0-9]{1,18}$'
                THEN member.source_key::bigint END AS duid
        FROM public.stewardship_source_current pointer
        JOIN public.stewardship_snapshot_member mm ON mm.snapshot_id=pointer.snapshot_id
        JOIN public.stewardship_source_member member ON member.id=mm.payload_id
        JOIN public.stewardship_snapshot_contact cm
            ON cm.snapshot_id=pointer.snapshot_id AND cm.source_key='member:' || member.source_key
        JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
        LEFT JOIN public.stewardship_snapshot_family fm
            ON fm.snapshot_id=pointer.snapshot_id AND fm.source_key=member.family_key
        LEFT JOIN public.stewardship_source_family family ON family.id=fm.payload_id
        CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
            family.canonical::jsonb AS family_doc,
            contact.canonical::jsonb AS contact_doc) parsed
        JOIN wanted ON EXISTS (
            SELECT 1 FROM jsonb_array_elements(parsed.contact_doc->'emails') listed_email(value)
            WHERE jsonb_typeof(listed_email.value->'value')='string'
              AND lower(btrim(listed_email.value->>'value'))=wanted.address)
        WHERE pointer.singleton
          AND parsed.member_doc->'schema_version'='1'::jsonb
          AND parsed.contact_doc->'schema_version'='1'::jsonb
    ), ranked AS (
        SELECT listed.name, row_number() OVER (
            ORDER BY listed.active_family DESC, listed.head DESC, listed.name,
                listed.duid NULLS LAST) AS position
        FROM listed WHERE listed.name <> ''
    ), best AS (
        SELECT ranked.name, min(ranked.position) AS position
        FROM ranked GROUP BY ranked.name
    )
    SELECT coalesce(jsonb_agg(best.name ORDER BY best.position), '[]'::jsonb)
    FROM best
$$;
ALTER FUNCTION public.stewardship_member_names_for_v1(text) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_member_names_for_v1(text) FROM PUBLIC;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The one definer: SECURITY DEFINER, the fixed search_path, no EXECUTE
    -- for PUBLIC, one text argument, one jsonb answer, matching the address
    -- trimmed and case-insensitively, and no other overload.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_member_names_for_v1'
              AND p.prosecdef AND NOT p.proretset AND p.provolatile='s'
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
              AND pg_get_function_identity_arguments(p.oid)='address text'
              AND pg_get_function_result(p.oid)='jsonb'
              AND p.prosrc LIKE '%SELECT lower(btrim(address)) AS address%'
              AND p.prosrc LIKE '%lower(btrim(listed_email.value->>''value''))=wanted.address)%'
              AND p.prosrc LIKE '%ORDER BY listed.active_family DESC, listed.head DESC, listed.name,%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_member_names_for_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0053: the Name lookup definer is not installed as declared';
    END IF;
END
$check$;
