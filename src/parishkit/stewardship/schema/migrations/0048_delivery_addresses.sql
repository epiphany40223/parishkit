-- Frozen forward migration file 0048 (the repository-wide file sequence):
-- Outgoing mail's message page says how many of a Family message's addresses
-- each accepted attempt reached (#806 slice 1). This file is installed by its
-- Django migration in the jobs app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. The function exists only here, so a
-- fresh install and an upgraded database end in the same catalog.
--
-- One Family message goes to every head's address, and an attempt's
-- immutable evidence records which positions in its envelope the provider
-- refused for now or for good, even when it accepted the message for the
-- others. The web cannot read that evidence (stewardship_outbox_event.
-- evidence_note is outside its grants on purpose), so this definer function
-- returns only counts: for each accepted attempt of one message, its event
-- version, the envelope's size and how many positions were refused for now
-- and for good. The evidence is read through
-- stewardship_family_smtp_result_v1, which validates it against the event's
-- own render; an attempt whose evidence does not validate is left out. No
-- address, position, provider text or other evidence leaves the function.
-- No table, column or other function changes. Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_delivery_addresses_v1(message uuid)
RETURNS TABLE(version bigint, total integer, transient integer, permanent integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT e.version, (v.result->>'recipient_count')::integer,
        jsonb_array_length(v.result->'transient'),
        jsonb_array_length(v.result->'permanent')
    FROM public.stewardship_outbox_event e
    CROSS JOIN LATERAL (
        SELECT public.stewardship_family_smtp_result_v1(e.id) AS result) v
    WHERE e.message_id=message AND e.state='delivered' AND e.reason='smtp_accepted'
      AND v.result IS NOT NULL AND v.result->>'status'='accepted'
$$;
-- Only the logins runtime_grants names may call it (database-grants grants
-- the web its EXECUTE).
REVOKE ALL ON FUNCTION public.stewardship_delivery_addresses_v1(uuid) FROM PUBLIC;

-- Refuse to commit unless the function is installed as declared: a definer
-- with the fixed search_path, STABLE, not callable by PUBLIC. No temporary
-- objects: the migration login has no TEMP grant.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_delivery_addresses_v1(uuid)') IS NULL THEN
        RAISE EXCEPTION 'stewardship_delivery_addresses_v1 was not installed';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid=to_regprocedure('public.stewardship_delivery_addresses_v1(uuid)')
             AND prosecdef AND provolatile='s'
             AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%stewardship_family_smtp_result_v1(e.id)%') THEN
        RAISE EXCEPTION 'stewardship_delivery_addresses_v1 is not installed as declared';
    END IF;
    IF has_function_privilege('public',
           'public.stewardship_delivery_addresses_v1(uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'stewardship_delivery_addresses_v1 is callable by PUBLIC';
    END IF;
END
$check$;
