-- Frozen forward migration file 0220 (provisional: the repository-wide file
-- sequence assigns its final number when it merges): Sent mail names every
-- email to a person by a ParishSoft Member (#1562). This file is installed
-- by its Django migration in the jobs app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- Sent mail's Recipient names an email to a person (an Administrator's
-- report or notice, or a Ministry leader's sign-in email) by the Users-page
-- Name of its address, else, by Administrator decision (2026-10-20), by the
-- name of a ParishSoft Member whose contact record lists that address, else
-- by the address. The Users page's own Member-name reads do not serve it:
-- stewardship_member_names_v1 (0047) names only the current users' and
-- signed-in people's addresses, so a removed Ministry leader who never
-- signed in stayed an address, and stewardship_member_names_for_v1 (0053)
-- takes any typed address, so it is limited to 60 an hour and audited.
--
-- stewardship_delivery_page_member_names_v1(messages, actor) returns
-- (message_id, name): for each given email that has no Family, the one name
-- of a Member whose contact record in the current ParishSoft snapshot lists
-- the first intended address of its current rendering, matched trimmed and
-- case-insensitively as sign-in matches. When several Members list it, the
-- name is the one the Users page offers as a new user's default Name, in
-- 0047's order: a Member of an active Family first, then a head of
-- household, then by name, then by Member DUID. An email no Member's
-- address matches, and every Family email, gets no row. It takes email ids,
-- not addresses, so it names only people the parish has emailed, never an
-- address typed in: it is no lookup of arbitrary addresses, and it returns
-- no address, contact field, DUID or Family. At most 500 ids per call, as
-- stewardship_delivery_page_recipients_v1 (0116) takes. Each contact's
-- addresses are unnested once and joined to the page's distinct addresses
-- by equality, so many emails to one address cost one match, not one each.
--
-- It enforces what 0116's reads enforce, in SQL: the caller must be the
-- web login (or the schema owner, for maintenance and tests), and actor
-- must be an enabled portal user whose role is Administrator
-- (stewardship_export_authorized_v1(actor, true)). Anything else is refused
-- with SQLSTATE 42501, and a malformed list with 22023. The search_path is
-- fixed, PUBLIC has no EXECUTE, and database-grants grants EXECUTE only to
-- the web login (runtime_grants.runtime_functions). The page's
-- delivery_viewed audit record names the actor; the function adds no audit
-- of its own, as the other definer reads do not.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_delivery_page_member_names_v1(message_uuids uuid[], actor_uuid uuid)
RETURNS TABLE(message_id uuid, name text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email recipients are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email recipients require an Administrator' USING ERRCODE='42501';
    END IF;
    IF message_uuids IS NULL OR cardinality(message_uuids)>500 THEN
        RAISE EXCEPTION 'Invalid email list' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
        WITH wanted AS (
            SELECT DISTINCT ON (m.id) m.id AS email_id, lower(btrim(item.value)) AS address
              FROM public.stewardship_outbox_message m
              JOIN public.stewardship_outbox_render r ON r.id=m.render_id
             CROSS JOIN LATERAL jsonb_array_elements_text(r.intended_recipients)
                   WITH ORDINALITY AS item(value,position)
             WHERE m.id=ANY(message_uuids) AND m.family_id IS NULL
             ORDER BY m.id, item.position
        ), addresses AS (
            SELECT DISTINCT wanted.address FROM wanted
        ), listed AS (
            SELECT listed_address.address,
                concat_ws(', ',
                    nullif(btrim(parsed.member_doc->>'lastName'), ''),
                    nullif(btrim(parsed.member_doc->>'firstName'), '')) AS shown,
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
            CROSS JOIN LATERAL (SELECT DISTINCT lower(btrim(listed_email.value->>'value')) AS address
                FROM jsonb_array_elements(parsed.contact_doc->'emails') listed_email(value)
                WHERE jsonb_typeof(listed_email.value->'value')='string') listed_address
            JOIN addresses ON addresses.address=listed_address.address
            WHERE pointer.singleton
              AND parsed.member_doc->'schema_version'='1'::jsonb
              AND parsed.contact_doc->'schema_version'='1'::jsonb
        ), best AS (
            SELECT DISTINCT ON (listed.address) listed.address, listed.shown
              FROM listed
             WHERE listed.shown<>''
             ORDER BY listed.address, listed.active_family DESC, listed.head DESC,
                   listed.shown, listed.duid NULLS LAST
        )
        SELECT wanted.email_id, best.shown
          FROM wanted JOIN best ON best.address=wanted.address;
END $$;
ALTER FUNCTION public.stewardship_delivery_page_member_names_v1(uuid[], uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_page_member_names_v1(uuid[], uuid) FROM PUBLIC;

-- Refuse to commit unless the function is installed exactly as declared: a
-- stable definer with the fixed search_path and the declared result, its
-- caller and Administrator checks, its Users-page ranking, which PUBLIC
-- cannot execute. It reads only the catalog and writes nothing. A missing
-- function is checked first, on its own: PostgreSQL does not short-circuit
-- OR, so the ::regprocedure casts below would otherwise fail with their own
-- error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_delivery_page_member_names_v1(uuid[],uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0220 (Sent mail Member names) is not installed as declared';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
         WHERE p.oid='public.stewardship_delivery_page_member_names_v1(uuid[],uuid)'::regprocedure
           AND p.prosecdef AND p.provolatile='s' AND p.proretset
           AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
           AND p.prosrc LIKE '%IF session_user<>''pk_stewardship_web'' AND NOT pg_has_role(session_user,%'
           AND p.prosrc LIKE '%public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN%'
           AND p.prosrc LIKE '%cardinality(message_uuids)>500 THEN%'
           AND p.prosrc LIKE '%WHERE m.id=ANY(message_uuids) AND m.family_id IS NULL%'
           AND p.prosrc LIKE '%JOIN addresses ON addresses.address=listed_address.address%'
           AND p.prosrc LIKE '%ORDER BY listed.address, listed.active_family DESC, listed.head DESC,%')
       OR pg_get_function_result('public.stewardship_delivery_page_member_names_v1(uuid[],uuid)'::regprocedure)
          <>'TABLE(message_id uuid, name text)'
       OR has_function_privilege('public',
          'public.stewardship_delivery_page_member_names_v1(uuid[],uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0220 (Sent mail Member names) is not installed as declared';
    END IF;
END
$check$;
