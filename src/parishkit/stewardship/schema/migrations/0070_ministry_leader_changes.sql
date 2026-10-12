-- Frozen forward migration file 0070 (the repository-wide file sequence):
-- count-only Ministry leader changes (#1034). It is installed by the Django
-- migration that names it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. The
-- one function it replaces was created by migration 0042, not the baseline,
-- so the fresh-install baseline is unchanged: a fresh install runs the
-- baseline, the earlier files and then this.
--
-- A Ministry leader's Ministries come from ParishSoft roster roles (#922),
-- recomputed on every request by migration 0042's
-- stewardship_ministry_leaders_v1, so a ParishSoft promotion or a settings
-- activation (leader roles, Ministries, Ministry activity) can widen or
-- narrow what a leader sees with nothing recorded. Each of those two owners
-- now records one count-only audit event when its change moves who leads
-- which Ministry. Neither login may learn a leader's address or Member DUID
-- (the Administrator's decision on #1067), and neither may read even the
-- counts back, since a login that could would have an aggregate oracle
-- (craft a configuration in a transaction it rolls back, read the counts).
-- This file:
--
--   adds stewardship_ministry_leaders_at_v1(uuid, uuid, uuid)
--                                     the leader definition evaluated at a
--                                     given configuration, campaign and
--                                     snapshot: 0042's query with the
--                                     pointers as arguments (invoker;
--                                     executable by no login)
--   replaces stewardship_ministry_leaders_v1
--                                     now a thin wrapper over the above at
--                                     the current pointers, so there is one
--                                     definition of who leads what; still
--                                     SECURITY DEFINER, executable by no
--                                     login, same rows
--   adds stewardship_ministry_leader_changes_v1(uuid x 6)
--                                     compares the current (address,
--                                     Ministry) pairs with those at the given
--                                     earlier snapshot, configuration and
--                                     campaign and, when any differ, writes
--                                     one ministry_leaders_changed audit event
--                                     with the two counts itself; returns
--                                     nothing (definer; worker and
--                                     configuration installer)
--
-- The owner reads the three pointers just before its change and calls the
-- recorder just after it, in the same transaction, so the event commits or
-- rolls back with the change. The recorder refuses (SQLSTATE 42501) any
-- session but the general worker's (source promotion and setup completion),
-- the configuration installer's (activation) and the schema owner's;
-- database-grants gives EXECUTE only to the first two
-- (runtime_grants.LEADER_CHANGES_FUNCTION). The installer may only INSERT
-- into the audit tables, so it never reads the event back; the worker
-- already reads every source record the leaders derive from. One bit
-- remains, accepted in review: a caller can tell whether an event was
-- written (from pg_stat_xact_user_tables, or from an insert-only error), but
-- never a count, an address or who leads what.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Every Ministry leader at the given configuration, campaign and snapshot
-- (#922, #1034), the one definition of who leads what: 0042's query and
-- rules (described there), with the pointers as arguments rather than read
-- from the runtime and source pointers. Nothing is read when the campaign
-- is not in that configuration, does not ask about Ministries, or no
-- snapshot is given. Invoker rights and no EXECUTE for any login: the two
-- definers below call it as their owner.
CREATE FUNCTION public.stewardship_ministry_leaders_at_v1(at_configuration uuid, at_campaign uuid, at_snapshot uuid)
    RETURNS TABLE(email text, ministry_duid integer, member_duid bigint)
    LANGUAGE plpgsql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    configuration_key uuid; campaign_values jsonb; snapshot_key uuid;
    leader_keys text[];
BEGIN
    -- As in 0042, the inputs are read on their own first, so the query below
    -- is planned with the snapshot as a known value.
    SELECT campaign.configuration_id, campaign.values, at_snapshot
      INTO configuration_key, campaign_values, snapshot_key
    FROM public.stewardship_campaign_configuration campaign
    WHERE campaign.record_id=at_campaign
      AND campaign.configuration_id=at_configuration
      AND campaign.values->'modules' ? 'ministry';
    IF snapshot_key IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
      INTO leader_keys
    FROM jsonb_array_elements_text(
        public.stewardship_ministry_leader_roles_v1(campaign_values)) role(name);
    -- Each MATERIALIZED step runs once: the current roster parsed once;
    -- its few distinct labels, each compared once (the planner would
    -- otherwise push the comparison down to every roster row); and the
    -- leaders' rows, so the keyed lookups after them run for those only.
    RETURN QUERY
    WITH roster AS MATERIALIZED (
        SELECT payload.member_key, payload.ministry_key, payload.canonical::jsonb AS doc
        FROM public.stewardship_snapshot_roster rm
        JOIN public.stewardship_source_roster payload ON payload.id=rm.payload_id
        WHERE rm.snapshot_id=snapshot_key
    ), labels AS MATERIALIZED (
        SELECT DISTINCT roster.doc->>'ministryRoleName' AS label FROM roster
    ), leader_labels AS MATERIALIZED (
        SELECT labels.label FROM labels
        WHERE public.stewardship_ministry_leader_role_key_v1(labels.label)=ANY(leader_keys)
    ), held AS MATERIALIZED (
        SELECT roster.member_key, roster.ministry_key
        FROM roster JOIN leader_labels ON leader_labels.label=roster.doc->>'ministryRoleName'
        WHERE roster.doc->'schema_version'='1'::jsonb
          AND roster.doc->'current'='true'::jsonb
    )
    SELECT DISTINCT lower(address.value->>'value'), duid.ministry::integer,
        CASE WHEN member.source_key ~ '^[0-9]{1,18}$' THEN member.source_key::bigint END
    FROM held
    JOIN public.stewardship_snapshot_member mm
        ON mm.snapshot_id=snapshot_key AND mm.source_key=held.member_key
    JOIN public.stewardship_source_member member ON member.id=mm.payload_id
    JOIN public.stewardship_snapshot_family fm
        ON fm.snapshot_id=snapshot_key AND fm.source_key=member.family_key
    JOIN public.stewardship_source_family family ON family.id=fm.payload_id
    JOIN public.stewardship_snapshot_ministry tm
        ON tm.snapshot_id=snapshot_key AND tm.source_key=held.ministry_key
    JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
    JOIN public.stewardship_snapshot_contact cm
        ON cm.snapshot_id=snapshot_key AND cm.source_key='member:' || member.source_key
    JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
    CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
        family.canonical::jsonb AS family_doc,
        ministry.canonical::jsonb AS ministry_doc,
        contact.canonical::jsonb AS contact_doc) parsed
    -- Each DUID is a positive signed 32-bit key; CASE orders the cast after
    -- the shape test.
    CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
        THEN ministry.source_key::bigint END AS ministry) duid
    CROSS JOIN LATERAL jsonb_array_elements(parsed.contact_doc->'emails') address(value)
    WHERE parsed.member_doc->'schema_version'='1'::jsonb
      AND parsed.family_doc->'schema_version'='1'::jsonb
      AND parsed.contact_doc->'schema_version'='1'::jsonb
      AND parsed.ministry_doc->'schema_version'='1'::jsonb
      AND parsed.member_doc->'active'='true'::jsonb
      AND parsed.family_doc->'active'='true'::jsonb
      AND parsed.ministry_doc->'catalog_present'='true'::jsonb
      AND duid.ministry BETWEEN 1 AND 2147483647
      AND campaign_values->'ministry_duids' @> jsonb_build_array(duid.ministry)
      AND address.value->'valid'='true'::jsonb
      AND jsonb_typeof(address.value->'value')='string'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_activity activity
          WHERE activity.configuration_id=configuration_key
            AND activity.organization_id=ministry.organization_id
            AND activity.ministry_duid=duid.ministry
            AND NOT activity.active);
END $$;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leaders_at_v1(uuid, uuid, uuid) FROM PUBLIC;

-- The current leaders under 0042's body, as one digest, for the check at
-- the end: the wrapper below must answer the same rows on this database. It
-- is kept in a transaction-local setting, as the migration login holds no
-- TEMP privilege.
DO $before$
BEGIN
    PERFORM set_config('stewardship.leaders_before_0070', coalesce((
        SELECT md5(string_agg(leader::text, ',' ORDER BY leader::text))
        FROM public.stewardship_ministry_leaders_v1() leader), ''), true);
END
$before$;

-- Every current Ministry leader (#922): the definition above at the current
-- configuration, campaign and source snapshot. Same signature, rows, owner
-- and grants as 0042's body, which this replaces, so its callers
-- (stewardship_ministry_leader_scope_v1 and 0054's leaders table, through
-- the scope) follow at once. Still a definer no login may execute.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_leaders_v1()
    RETURNS TABLE(email text, ministry_duid integer, member_duid bigint)
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    SELECT leader.email, leader.ministry_duid, leader.member_duid
    FROM public.stewardship_system_configuration runtime
    CROSS JOIN public.stewardship_source_current pointer
    CROSS JOIN LATERAL public.stewardship_ministry_leaders_at_v1(
        runtime.active_configuration_id, runtime.current_campaign_id,
        pointer.snapshot_id) leader
    WHERE pointer.singleton;
END $$;
-- CREATE OR REPLACE resets what it does not name; stated again on its own,
-- as every definer is.
ALTER FUNCTION public.stewardship_ministry_leaders_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leaders_v1() FROM PUBLIC;

-- Record how many (address, Ministry) leader pairs the current leaders
-- gained and lost against those at the given earlier snapshot,
-- configuration and campaign (#1034): one ministry_leaders_changed audit
-- event, written here, when either count is non-zero; nothing otherwise.
-- An address that changed counts once each way. The caller names the
-- event's actor (the worker), subject (the refresh task or configuration
-- request), correlation and, for a promotion, the promoted snapshot; the
-- ownership trigger attributes the event as it does any other. Nothing is
-- returned, so the counts never reach the caller.
CREATE FUNCTION public.stewardship_ministry_leader_changes_v1(previous_snapshot uuid, previous_configuration uuid, previous_campaign uuid, actor uuid, subject uuid, correlation uuid, promoted_snapshot uuid)
    RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    gained integer; lost integer; event uuid := gen_random_uuid();
BEGIN
    IF session_user NOT IN ('pk_stewardship_worker','pk_stewardship_config_installer')
       AND NOT pg_has_role(session_user,
           (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only source promotion and configuration activation record Ministry leader changes'
            USING ERRCODE='42501';
    END IF;
    IF correlation IS NULL THEN
        RAISE EXCEPTION 'A Ministry leader change needs its correlation' USING ERRCODE='23514';
    END IF;
    WITH previous_pairs AS MATERIALIZED (
        SELECT DISTINCT leader.email, leader.ministry_duid
        FROM public.stewardship_ministry_leaders_at_v1(
            previous_configuration, previous_campaign, previous_snapshot) leader
    ), current_pairs AS MATERIALIZED (
        SELECT DISTINCT leader.email, leader.ministry_duid
        FROM public.stewardship_ministry_leaders_v1() leader
    )
    SELECT (SELECT count(*) FROM (SELECT * FROM current_pairs
                EXCEPT SELECT * FROM previous_pairs) added)::integer,
           (SELECT count(*) FROM (SELECT * FROM previous_pairs
                EXCEPT SELECT * FROM current_pairs) removed)::integer
      INTO gained, lost;
    IF gained=0 AND lost=0 THEN
        RETURN;
    END IF;
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id)
    VALUES (event, actor, correlation, 'ministry_leaders_changed', subject);
    INSERT INTO public.stewardship_audit_context
        (id, event_id, actor_id, correlation_id, actor_kind, schema, context)
    VALUES (gen_random_uuid(), event, actor, correlation, 'system', 'action',
        jsonb_build_object('count', gained, 'matching_count', lost)
        || CASE WHEN promoted_snapshot IS NULL THEN '{}'::jsonb
                ELSE jsonb_build_object('snapshot_id', promoted_snapshot::text) END);
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_ministry_leader_changes_v1(uuid, uuid, uuid, uuid, uuid, uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leader_changes_v1(uuid, uuid, uuid, uuid, uuid, uuid, uuid) FROM PUBLIC;

-- Refuse to commit unless the three functions are installed as declared:
-- the definition with invoker rights, the wrapper and the recorder as
-- definers, each with the fixed search_path and no EXECUTE for PUBLIC, and
-- unless the wrapper answers this database's leaders as 0042's body did.
DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND ((p.proname='stewardship_ministry_leaders_at_v1' AND NOT p.prosecdef
                AND p.provolatile='s'
                AND pg_get_function_identity_arguments(p.oid)='at_configuration uuid, at_campaign uuid, at_snapshot uuid'
                AND p.prosrc LIKE '%WHERE rm.snapshot_id=snapshot_key%'
                AND p.prosrc LIKE '%parsed.family_doc->''active''=''true''::jsonb%'
                AND p.prosrc LIKE '%NOT activity.active%')
            OR (p.proname='stewardship_ministry_leaders_v1' AND p.prosecdef
                AND p.provolatile='s'
                AND pg_get_function_identity_arguments(p.oid)=''
                AND p.prosrc LIKE '%public.stewardship_ministry_leaders_at_v1(%'
                AND p.prosrc NOT LIKE '%stewardship_snapshot_roster%')
            OR (p.proname='stewardship_ministry_leader_changes_v1' AND p.prosecdef
                AND p.prorettype='void'::regtype
                AND pg_get_function_identity_arguments(p.oid)='previous_snapshot uuid, previous_configuration uuid, previous_campaign uuid, actor uuid, subject uuid, correlation uuid, promoted_snapshot uuid'
                AND p.prosrc LIKE '%session_user NOT IN (''pk_stewardship_worker'',''pk_stewardship_config_installer'')%'
                AND p.prosrc LIKE '%public.stewardship_ministry_leaders_v1() leader%'
                AND p.prosrc LIKE '%''ministry_leaders_changed''%'))
       )<>3 THEN
        RAISE EXCEPTION 'Migration 0070: the Ministry leader functions are not installed as declared';
    END IF;
    -- The wrapper answers exactly what 0042's body answered before it.
    IF current_setting('stewardship.leaders_before_0070', true) IS DISTINCT FROM coalesce((
        SELECT md5(string_agg(leader::text, ',' ORDER BY leader::text))
        FROM public.stewardship_ministry_leaders_v1() leader), '') THEN
        RAISE EXCEPTION 'Migration 0070: the leaders wrapper disagrees with the definition it replaced';
    END IF;
END
$check$;
