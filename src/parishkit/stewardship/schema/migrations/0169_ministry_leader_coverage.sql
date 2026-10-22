-- Frozen forward migration file 0169 (the repository-wide file sequence):
-- the Ministry requests page's Leaders column (#1594). It is installed by
-- the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. It replaces nothing, so the fresh-install baseline is unchanged:
-- a fresh install runs the baseline, the earlier files and then this.
--
-- Staff and Administrators see every Ministry's request counts in By
-- Ministry on Ministry requests, but not whether anyone but Staff will
-- work them (reports specification, "Ministry change summary"). The web
-- login has no grant on Member, contact or roster records; migration
-- 0042's stewardship_ministry_leaders_v1 (the one definition of who leads
-- what, a thin wrapper since 0070) is EXECUTE-able by no login, and 0054's
-- stewardship_leaders_without_user_v1 answers only an Administrator and
-- lists addresses. This file:
--
--   adds stewardship_ministry_leader_coverage_v1(text, uuid)
--                                     for each Ministry with a leader, the
--                                     number of leader addresses, how many
--                                     are users and how many of those users
--                                     have signed in; counts only, for a
--                                     live Staff or Administrator session,
--                                     audited (definer; web)
--
-- The caller passes its Admin session's key and its correlation ID, as
-- 0054's leaders read does, and the definer refuses (SQLSTATE 42501) under
-- the same session rule: the key's digest (0057) names a session that is
-- not revoked, before its deadline, within the 60-minute idle limit and
-- begun after the latest offline Admin-access recovery. Only the role rule
-- differs: its user must be Staff or an Administrator now
-- (stewardship_export_authorized_v1 without admin_only), since Staff work
-- the Ministries nobody else will. It takes no address or Ministry, so it
-- is not an oracle: every call answers the same counts for the current
-- data, and each records one ministry_leader_coverage_viewed audit event
-- (the reader and the number of Ministries answered, never an address).
-- Against a compromised web login that can pass a live Staff session key
-- (#995), what it discloses is exactly what the page shows Staff. Like
-- 0054's read it has no rate cap: one call already returns everything.
--
-- Who leads what is stewardship_ministry_leaders_v1's answer, read whole,
-- so this file restates no leader rule. Per Ministry:
--   leaders    the distinct leader addresses, exactly as the leaders
--              definition gives them (lowercased) and as the leader scope
--              matches them to a user (an address several leader Members
--              list counts once);
--   users      those addresses that are a user in the active configuration
--              whose roles give a Ministry scope (Ministry leader, Staff or
--              Administrator: stewardship_ministry_leader_scope_v1's rule);
--   signed_in  those users with a Google identity at that address that is
--              not disabled and has an admin_login audit event, the durable
--              event written only when a session was issued (the Users
--              page's Last successful sign-in, accounts/user_reads.py).
-- A Ministry with no leader address is left out; the page reads that as
-- "No leader". The answer is {"ministries": [{"ministry", "leaders",
-- "users", "signed_in"}, ...]} in ascending Ministry DUID order.
-- Web gets EXECUTE through `database-grants`
-- (runtime_grants.LEADER_COVERAGE_FUNCTION). Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_ministry_leader_coverage_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    answer jsonb;
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session, exactly as 0054's leaders read (since
    -- 0057, by the key's digest) decides it, whose user is Staff or an
    -- Administrator now.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, false) THEN
        RAISE EXCEPTION 'Leader coverage requires a live Staff or Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- The leaders read once (it parses the current roster), then the few
    -- leader addresses matched against users and sign-ins.
    WITH leaders AS MATERIALIZED (
        SELECT DISTINCT leader.ministry_duid, leader.email
        FROM public.stewardship_ministry_leaders_v1() leader
    ), users AS MATERIALIZED (
        SELECT DISTINCT leaders.email
        FROM leaders
        CROSS JOIN public.stewardship_system_configuration runtime
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=runtime.active_configuration_id
           AND rule.email=leaders.email
        WHERE coalesce(rule.roles,'[]'::jsonb) ?| ARRAY['administrator','staff','ministry_leader']
    ), signed AS MATERIALIZED (
        SELECT users.email FROM users
        WHERE EXISTS (SELECT 1 FROM public.stewardship_portal_user identity
            WHERE lower(identity.email)=users.email AND NOT identity.disabled
              AND EXISTS (SELECT 1 FROM public.stewardship_audit_event login_event
                  WHERE login_event.event_type='admin_login'
                    AND login_event.actor_id=identity.id))
    ), counted AS (
        SELECT leaders.ministry_duid, count(*) AS leaders,
            count(*) FILTER (WHERE leaders.email IN (SELECT users.email FROM users)) AS users,
            count(*) FILTER (WHERE leaders.email IN (SELECT signed.email FROM signed)) AS signed_in
        FROM leaders GROUP BY leaders.ministry_duid
    )
    SELECT jsonb_build_object('ministries', coalesce(jsonb_agg(jsonb_build_object(
            'ministry', counted.ministry_duid, 'leaders', counted.leaders,
            'users', counted.users, 'signed_in', counted.signed_in)
        ORDER BY counted.ministry_duid), '[]'::jsonb))
      INTO answer
    FROM counted;
    -- The trace: who read and how many Ministries came back, never an address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'ministry_leader_coverage_viewed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded',
            'count',jsonb_array_length(answer->'ministries')));
    RETURN answer;
END
$$;
ALTER FUNCTION public.stewardship_ministry_leader_coverage_v1(text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leader_coverage_v1(text, uuid) FROM PUBLIC;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The one definer: SECURITY DEFINER, the fixed search_path, no EXECUTE
    -- for PUBLIC, its two arguments and jsonb answer, the digest session,
    -- recovery and Staff-or-Administrator checks, the one leaders
    -- definition, the sign-in event, the audit event, and no other overload.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_ministry_leader_coverage_v1'
              AND p.prosecdef AND NOT p.proretset AND p.provolatile='v'
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
              AND pg_get_function_identity_arguments(p.oid)
                  ='admin_session text, correlation uuid'
              AND pg_get_function_result(p.oid)='jsonb'
              AND p.prosrc LIKE '%WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL%'
              AND p.prosrc LIKE '%WHERE r.created_at>login.created_at);%'
              AND p.prosrc LIKE '%OR NOT public.stewardship_export_authorized_v1(actor, false) THEN%'
              AND p.prosrc LIKE '%FROM public.stewardship_ministry_leaders_v1() leader%'
              AND p.prosrc LIKE '%WHERE login_event.event_type=''admin_login''%'
              AND p.prosrc LIKE '%VALUES(event,clock_timestamp(),actor,correlation,''ministry_leader_coverage_viewed'',NULL,''deployment'');%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_ministry_leader_coverage_v1')<>1
       OR to_regprocedure('public.stewardship_ministry_leaders_v1()') IS NULL
       OR to_regprocedure('public.stewardship_admin_session_key_v1(text)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0169: the leader coverage definer is not installed as declared';
    END IF;
END
$check$;
