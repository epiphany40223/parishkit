-- Frozen forward migration file 0115 (the repository-wide file sequence):
-- Sent mail's Recipient filter and Recipient column (#1562). This file is
-- installed by its Django migration in the jobs app and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- Sent mail (once Outgoing mail) lists every email of every kind. Its
-- Recipient filter takes an email address, and its Recipient column names
-- the Administrator a report or notice was for. Both need the email's
-- intended recipients, which live only in its rendering
-- (stewardship_outbox_render), beside its subject and body; the web login
-- has no grant on that table. As 0058 did for the Mail message page, this
-- file adds narrow SECURITY DEFINER reads rather than widen the web login's
-- grants, plus the index the address read needs:
--
-- outbox_render_intended_lower is a GIN index over the intended recipients
-- lower-cased, so an address typed in any case finds the emails meant for
-- it without reading every rendering. Its operator class is jsonb_path_ops:
-- the read uses only containment (@>), which that class answers with a
-- smaller index than the default jsonb_ops. Lower-casing the
-- JSON text keeps it valid JSON: lower() changes only letters, and JSON's
-- escapes (É and É alike) read the same in either case. Made only
-- when missing, so the users swap's test can install this file again.
--
-- stewardship_delivery_ids_for_address_v1(address, actor) returns the ids of
-- the emails any of whose renderings has address among its intended
-- recipients, compared in lower case, and nothing else. A Testing email's
-- routed address (the Testing recipient) is not searched.
--
-- stewardship_delivery_page_recipients_v1(messages, actor) returns
-- (message_id, address) for the intended addresses, in stored order, of the
-- current rendering of each given email that has no Family: a daily or
-- weekly Administrator report, an operational notice or a security notice,
-- each sent to one Administrator address. A Family email's addresses never
-- leave it (the list page shows the Family's name instead). At most 500 ids
-- per call: a list page shows at most 100 rows.
--
-- Both enforce what 0058's recipient read enforces, in SQL: the caller must
-- be the web login (or the schema owner, for maintenance and tests), and
-- actor must be an enabled portal user whose role is Administrator
-- (stewardship_export_authorized_v1(actor, true)). Anything else is refused
-- with SQLSTATE 42501, and malformed input with 22023. The search_path is
-- fixed, PUBLIC has no EXECUTE, and database-grants grants EXECUTE only to
-- the web login (runtime_grants.runtime_functions). The page's
-- delivery_viewed audit record names the actor; the functions add no audit
-- of their own, as the other definer reads do not.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE INDEX IF NOT EXISTS outbox_render_intended_lower
    ON public.stewardship_outbox_render
    USING gin (((lower((intended_recipients)::text))::jsonb) jsonb_path_ops);

CREATE FUNCTION public.stewardship_delivery_ids_for_address_v1(address_text text, actor_uuid uuid)
RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email recipients are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email recipients require an Administrator' USING ERRCODE='42501';
    END IF;
    IF address_text IS NULL OR length(address_text) NOT BETWEEN 3 AND 254
       OR strpos(address_text,'@')=0 THEN
        RAISE EXCEPTION 'Invalid email address' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
        SELECT DISTINCT r.message_id
          FROM public.stewardship_outbox_render r
         WHERE (lower((r.intended_recipients)::text))::jsonb
               @> jsonb_build_array(lower(address_text));
END $$;
ALTER FUNCTION public.stewardship_delivery_ids_for_address_v1(text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_ids_for_address_v1(text, uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_page_recipients_v1(message_uuids uuid[], actor_uuid uuid)
RETURNS TABLE(message_id uuid, address text)
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
        SELECT m.id, item.value
          FROM public.stewardship_outbox_message m
          JOIN public.stewardship_outbox_render r ON r.id=m.render_id
         CROSS JOIN LATERAL jsonb_array_elements_text(r.intended_recipients)
               WITH ORDINALITY AS item(value,position)
         WHERE m.id=ANY(message_uuids) AND m.family_id IS NULL
         ORDER BY m.id, item.position;
END $$;
ALTER FUNCTION public.stewardship_delivery_page_recipients_v1(uuid[], uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_page_recipients_v1(uuid[], uuid) FROM PUBLIC;

-- Refuse to commit unless the index and both functions are installed
-- exactly as declared: stable definers with the fixed search_path and the
-- declared results, which PUBLIC cannot execute. It reads only the catalog
-- and writes nothing. Missing functions are checked first, on their own:
-- PostgreSQL does not short-circuit OR, so the ::regprocedure casts below
-- would otherwise fail with their own error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_delivery_ids_for_address_v1(text,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_delivery_page_recipients_v1(uuid[],uuid)') IS NULL
       OR to_regclass('public.outbox_render_intended_lower') IS NULL THEN
        RAISE EXCEPTION 'Migration 0115 (Sent mail recipient reads) is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p
         WHERE p.oid IN ('public.stewardship_delivery_ids_for_address_v1(text,uuid)'::regprocedure,
                         'public.stewardship_delivery_page_recipients_v1(uuid[],uuid)'::regprocedure)
           AND p.prosecdef AND p.provolatile='s' AND p.proretset
           AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])<>2
       OR pg_get_function_result('public.stewardship_delivery_ids_for_address_v1(text,uuid)'::regprocedure)
          <>'SETOF uuid'
       OR pg_get_function_result('public.stewardship_delivery_page_recipients_v1(uuid[],uuid)'::regprocedure)
          <>'TABLE(message_id uuid, address text)'
       OR has_function_privilege('public',
          'public.stewardship_delivery_ids_for_address_v1(text,uuid)', 'EXECUTE')
       OR has_function_privilege('public',
          'public.stewardship_delivery_page_recipients_v1(uuid[],uuid)', 'EXECUTE')
       OR pg_get_indexdef('public.outbox_render_intended_lower'::regclass)
          <>'CREATE INDEX outbox_render_intended_lower ON public.stewardship_outbox_render USING gin (((lower((intended_recipients)::text))::jsonb) jsonb_path_ops)' THEN
        RAISE EXCEPTION 'Migration 0115 (Sent mail recipient reads) is not installed as declared';
    END IF;
END
$check$;
