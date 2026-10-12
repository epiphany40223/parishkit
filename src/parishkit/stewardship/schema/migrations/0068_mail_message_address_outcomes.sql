-- Frozen forward migration file 0068 (the repository-wide file sequence):
-- the Mail message page says what happened at each of an email's addresses
-- (#806). This file is installed by its Django migration in the jobs app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- One email goes to every address in its envelope at once. The provider can
-- accept it for some addresses and refuse others, for now or for good, and
-- each attempt's immutable evidence (stewardship_outbox_event.evidence_note)
-- records only which envelope positions it refused. The web login has no
-- grant on that evidence or on the rendering that holds the envelope, so
-- this file adds one narrow SECURITY DEFINER read beside 0058's recipient
-- read, with the same caller checks:
--
-- stewardship_delivery_recipient_outcomes_v1(message, actor) returns, for
-- the newest rendering of one email (the one 0058 shows), one row per routed
-- address in stored order: the address and what the latest attempt of that
-- rendering recorded for it. The outcome is 'refused_permanently' or
-- 'refused_for_now' for a position the attempt's evidence lists as refused
-- for good or for now; otherwise 'delivered' when the attempt was accepted,
-- 'unknown' when the provider could not say whether it arrived, and
-- 'not_delivered' for any other result. The evidence is read only through
-- stewardship_family_smtp_result_v1, which validates it against its own
-- render; an email with no rendering, no finished attempt on its newest
-- rendering, or evidence that does not validate returns no rows. No
-- evidence, status word, provider text or other rendering column leaves the
-- function, and every address it returns is one 0058 already returns to the
-- same caller.
--
-- Callers: as in 0058, the session login must be the web login (or the
-- schema owner, for maintenance and tests) and actor must be an enabled
-- Administrator (stewardship_export_authorized_v1(actor, true)); anything
-- else is refused with SQLSTATE 42501. The search_path is fixed, PUBLIC has
-- no EXECUTE, and database-grants grants EXECUTE only to the web login
-- (runtime_grants.runtime_functions). The page's delivery_viewed audit
-- record covers the read. No table, column or other function changes.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_delivery_recipient_outcomes_v1(message_uuid uuid, actor_uuid uuid)
RETURNS TABLE(address text, outcome text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE newest public.stewardship_outbox_render%ROWTYPE; latest uuid; result jsonb;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email address outcomes are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email address outcomes require an Administrator' USING ERRCODE='42501';
    END IF;
    -- The same newest rendering 0058 reads.
    SELECT * INTO newest FROM public.stewardship_outbox_render r
     WHERE r.message_id=message_uuid ORDER BY r.created_at DESC, r.id DESC LIMIT 1;
    IF newest.id IS NULL THEN RETURN; END IF;
    -- Its latest finished attempt: the newest event that left submitting.
    -- Positions in its evidence refer to exactly this rendering's envelope.
    SELECT e.id INTO latest FROM public.stewardship_outbox_event e
     WHERE e.render_id=newest.id AND e.message_id=message_uuid
       AND e.previous_state='submitting'
     ORDER BY e.version DESC LIMIT 1;
    IF latest IS NULL THEN RETURN; END IF;
    result:=public.stewardship_family_smtp_result_v1(latest);
    IF result IS NULL THEN RETURN; END IF;
    RETURN QUERY
        SELECT item.address,
               CASE WHEN result->'permanent' @> to_jsonb(item.position-1) THEN 'refused_permanently'
                    WHEN result->'transient' @> to_jsonb(item.position-1) THEN 'refused_for_now'
                    WHEN result->>'status'='accepted' THEN 'delivered'
                    WHEN result->>'status'='delivery_unknown' THEN 'unknown'
                    ELSE 'not_delivered' END
          FROM jsonb_array_elements_text(newest.routed_recipients)
               WITH ORDINALITY AS item(address,position)
         ORDER BY item.position;
END $$;
ALTER FUNCTION public.stewardship_delivery_recipient_outcomes_v1(uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_recipient_outcomes_v1(uuid, uuid) FROM PUBLIC;

-- Refuse to commit unless the function is installed exactly as declared: a
-- stable definer returning (address, outcome) with the fixed search_path,
-- reading the evidence through the validator, which PUBLIC cannot execute.
-- It reads only the catalog and writes nothing. The missing function is
-- checked first, on its own, because PostgreSQL does not short-circuit OR
-- and the ::regprocedure casts below would otherwise fail with their own
-- error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_delivery_recipient_outcomes_v1(uuid,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0068 (email address outcomes read) is not installed as declared';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_delivery_recipient_outcomes_v1(uuid,uuid)'::regprocedure
          AND p.prosecdef AND p.provolatile='s' AND p.proretset
          AND pg_get_function_result(p.oid)='TABLE(address text, outcome text)'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%public.stewardship_family_smtp_result_v1(latest)%')
       OR has_function_privilege('public',
        'public.stewardship_delivery_recipient_outcomes_v1(uuid,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0068 (email address outcomes read) is not installed as declared';
    END IF;
END
$check$;
