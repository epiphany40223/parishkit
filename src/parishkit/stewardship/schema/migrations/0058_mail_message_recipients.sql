-- Frozen forward migration file 0058 (the repository-wide file sequence):
-- the Mail message page names the email's recipient addresses (#1024).
-- This file is installed by its Django migration in the jobs app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- An email's recipient lists live only in its rendering
-- (stewardship_outbox_render), beside its subject and body, and the web
-- login has no grant on that table: an Admin page never reads an email's
-- content. The Administrator asked to see whom an email was for on its own
-- page (#1015), so rather than widen the web login's grants this file adds
-- one narrow SECURITY DEFINER read, as 0012 did for the 24-hour count:
--
-- stewardship_delivery_recipients_v1(message, actor) returns the recipient
-- addresses of one email's newest rendering (the one its latest or next
-- attempt uses; a duplicate-risk resend renders again) and nothing else
-- from it: one row per address, with kind 'intended' (whom the email is
-- for) or 'routed' (where the provider is told to send it, which in Testing
-- mode is the Testing recipient), in each list's own order. Every address
-- is To: the rendering stores no Cc or Bcc. No subject, body, sender or
-- digest leaves the function.
--
-- It enforces the authority the Mail message page requires, in SQL, as the
-- same page's resolution guard does (stewardship_delivery_resolution_edge_v1
-- and the resolution trigger): the caller must be the web login (or the
-- schema owner, for maintenance and tests, as the delivery control guard
-- admits), and actor must be an enabled portal user whose role is
-- Administrator (stewardship_export_authorized_v1(actor, true), the SQL
-- counterpart of Python's Capability.BACKGROUND_WORK). Anything else is
-- refused with SQLSTATE 42501, so a caller cannot read an address by
-- naming a non-Administrator or omitting the actor. An unknown message
-- returns no rows. The search_path is fixed, PUBLIC has no EXECUTE, and
-- database-grants grants EXECUTE only to the web login
-- (runtime_grants.runtime_functions). The page's existing delivery_viewed
-- audit record (actor and message) records each read; the function adds no
-- audit of its own, as the other definer reads do not.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_delivery_recipients_v1(message_uuid uuid, actor_uuid uuid)
RETURNS TABLE(kind text, address text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email recipients are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email recipients require an Administrator' USING ERRCODE='42501';
    END IF;
    RETURN QUERY
        SELECT list.kind, item.address
          FROM (SELECT r.intended_recipients, r.routed_recipients
                  FROM public.stewardship_outbox_render r
                 WHERE r.message_id=message_uuid
                 ORDER BY r.created_at DESC, r.id DESC LIMIT 1) newest
         CROSS JOIN LATERAL (VALUES (1,'intended',newest.intended_recipients),
                                    (2,'routed',newest.routed_recipients)) AS list(rank,kind,recipients)
         CROSS JOIN LATERAL jsonb_array_elements_text(list.recipients)
               WITH ORDINALITY AS item(address,position)
         ORDER BY list.rank, item.position;
END $$;
ALTER FUNCTION public.stewardship_delivery_recipients_v1(uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_recipients_v1(uuid, uuid) FROM PUBLIC;

-- Refuse to commit unless the function is installed exactly as declared: a
-- stable definer returning (kind, address) with the fixed search_path,
-- which PUBLIC cannot execute. It reads only the catalog and writes nothing.
-- The missing function is checked first, on its own: PostgreSQL does not
-- short-circuit OR, so the ::regprocedure casts below would otherwise fail
-- with their own error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_delivery_recipients_v1(uuid,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0058 (email recipients read) is not installed as declared';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_delivery_recipients_v1(uuid,uuid)'::regprocedure
          AND p.prosecdef AND p.provolatile='s' AND p.proretset
          AND pg_get_function_result(p.oid)='TABLE(kind text, address text)'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR has_function_privilege('public',
        'public.stewardship_delivery_recipients_v1(uuid,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0058 (email recipients read) is not installed as declared';
    END IF;
END
$check$;
