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
-- not. Answering any typed address is a names oracle, so the definer itself
-- enforces who may ask and how often, whatever the web code does. This
-- file:
--
--   adds stewardship_member_names_for_v1(text, text, uuid)
--                                            the ordered, distinct names of
--                                            the current Members listing one
--                                            address, for a live
--                                            Administrator session, audited
--                                            and limited (definer; web)
--
-- The caller passes its Admin session's key (the Django session key the
-- stewardship_portal_session row names), the address and its correlation
-- ID. The definer refuses (SQLSTATE 42501) unless that session is live (not
-- revoked, before its deadline and within the 60-minute idle limit, as
-- accounts/session_policy.py says) and its user is an Administrator now
-- (stewardship_export_authorized_v1 with admin_only). It then refuses
-- (SQLSTATE 53400) when that Administrator already has 60
-- user_names_looked_up audit events in the last hour; a per-Administrator
-- transaction lock makes concurrent calls count one another. Otherwise it
-- records one user_names_looked_up audit event (the Administrator, and how
-- many names it returns, never the address) and returns the names.
--
-- The names are 0047's stewardship_member_names_v1 with its set of wanted
-- addresses replaced by the one argument, matched trimmed and
-- case-insensitively as sign-in matches, with no Gmail dot or plus folding.
-- It returns only names, never another contact field, a DUID or a Family;
-- an address longer than an email address can be answers no names. Web gets
-- EXECUTE through `database-grants` (runtime_grants.MEMBER_NAMES_FOR_FUNCTION).
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_member_names_for_v1(admin_session text, address text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    recent bigint;
    names jsonb;
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session whose user is an Administrator now.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=admin_session AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes';
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, true) THEN
        RAISE EXCEPTION 'Name lookup requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- How often: 60 lookups an hour for each Administrator, counted from
    -- this definer's own audit events. The lock serializes one
    -- Administrator's concurrent lookups, so two cannot both see 59.
    PERFORM pg_advisory_xact_lock(736247, hashtext(actor::text));
    SELECT count(*) INTO recent FROM public.stewardship_audit_event e
    WHERE e.event_type='user_names_looked_up' AND e.actor_id=actor
      AND e.created_at>clock_timestamp()-interval '1 hour';
    IF recent>=60 THEN
        RAISE EXCEPTION 'Name lookup limit reached: 60 an hour for each Administrator'
            USING ERRCODE='53400';
    END IF;
    WITH wanted AS (
        SELECT lower(btrim(address)) AS wanted_address
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
              AND lower(btrim(listed_email.value->>'value'))=wanted.wanted_address)
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
    INTO names FROM best;
    -- The trace: who looked and how many names came back, never the address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'user_names_looked_up',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded','count',jsonb_array_length(names)));
    RETURN names;
END
$$;
ALTER FUNCTION public.stewardship_member_names_for_v1(text, text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_member_names_for_v1(text, text, uuid) FROM PUBLIC;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The one definer: SECURITY DEFINER, the fixed search_path, no EXECUTE
    -- for PUBLIC, its three arguments and jsonb answer, the session and
    -- Administrator check, the hourly limit, the audit event, matching the
    -- address trimmed and case-insensitively, and no other overload.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_member_names_for_v1'
              AND p.prosecdef AND NOT p.proretset AND p.provolatile='v'
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
              AND pg_get_function_identity_arguments(p.oid)
                  ='admin_session text, address text, correlation uuid'
              AND pg_get_function_result(p.oid)='jsonb'
              AND p.prosrc LIKE '%WHERE login.session_id=admin_session AND login.revoked_at IS NULL%'
              AND p.prosrc LIKE '%OR NOT public.stewardship_export_authorized_v1(actor, true) THEN%'
              AND p.prosrc LIKE '%IF recent>=60 THEN%'
              AND p.prosrc LIKE '%VALUES(event,clock_timestamp(),actor,correlation,''user_names_looked_up'',NULL,''deployment'');%'
              AND p.prosrc LIKE '%SELECT lower(btrim(address)) AS wanted_address%'
              AND p.prosrc LIKE '%lower(btrim(listed_email.value->>''value''))=wanted.wanted_address)%'
              AND p.prosrc LIKE '%ORDER BY listed.active_family DESC, listed.head DESC, listed.name,%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_member_names_for_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0053: the Name lookup definer is not installed as declared';
    END IF;
END
$check$;
