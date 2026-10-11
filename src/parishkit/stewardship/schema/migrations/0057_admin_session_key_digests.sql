-- Frozen forward migration file 0057 (the repository-wide file sequence):
-- Admin session keys are stored only as digests (#995). It is installed by
-- the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. The two functions it replaces were created by frozen files 0053
-- and 0054, not the baseline, so the fresh-install baseline files are
-- unchanged: a fresh install runs the baseline, the earlier files and then
-- this.
--
-- The pk_admin cookie carries a Django session key. Until now Django's
-- database store kept that same value in django_session.session_key, and
-- stewardship_portal_session.session_id named it, so the web database login
-- (which must read both) or a backup could replay a live Administrator's
-- key. From this release the Admin session store
-- (accounts/admin_session_store.py) keeps each Admin session under the first
-- 40 hexadecimal digits of its key's SHA-256, and digests every presented
-- key before looking it up, so a stored value is useless as a cookie or as
-- a definer's session argument. Family sessions keep Django's store and are
-- not touched here in any way. This file:
--
--   adds stewardship_admin_session_key_v1(text)
--                                            the stored form of one Admin
--                                            session key, the same digest
--                                            the Python store computes, for
--                                            any definer that is handed a
--                                            session key to compare it with
--                                            session_id (invoker; no grant)
--   adds stewardship_portal_session_key_digest_v1()
--                                            insert guard: a new Admin
--                                            session row's session_id must
--                                            be such a digest (40 lowercase
--                                            hexadecimal digits); Django's
--                                            undigested 32-character keys
--                                            are refused
--   replaces stewardship_member_names_for_v1(text, text, uuid) (0053)
--        and stewardship_leaders_without_user_v1(text, uuid) (0054)
--                                            the Users page's definers take
--                                            the caller's Admin session key
--                                            and now compare its digest
--                                            with session_id, so a stored
--                                            session_id passed to them is
--                                            refused like any other
--                                            unknown key; each body is its
--                                            0053/0054 body with only that
--                                            comparison changed, SECURITY
--                                            DEFINER is stated again and
--                                            CREATE OR REPLACE keeps their
--                                            owner and grants (web's
--                                            EXECUTE)
--   ends every live Admin session stored before this release
--                                            each live row (unrevoked,
--                                            before its deadline, within the
--                                            60-minute idle limit) whose
--                                            session_id is not a digest is
--                                            revoked, as Python's _revoke
--                                            revokes, with one admin_revoked
--                                            audit event
--
-- Ending those sessions is the accepted cost of the change (#995): no
-- cookie can reach an undigested row any more, because the store digests
-- what it is given, so they could only linger as live rows until cleanup.
-- Their Administrators sign in again; ordinary cleanup then removes the
-- rows and their Django sessions. A row that had already lapsed (expired
-- or idle) is left to that cleanup, which records it as admin_timeout, as
-- it would have anyway. Automation sessions (whose secrets were already
-- stored as digests) stay approved; only a command session that was open
-- during the upgrade ends. Reversing needs its own forward migration.
-- Rolling the application back past this release also needs a database
-- restore: an older release would insert undigested keys, which the guard
-- refuses, so no Admin could sign in.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The stored form of an Admin session key: SHA-256 of its UTF-8 bytes, the
-- first 40 hexadecimal digits (accounts/admin_session_store.digest). A
-- Django key is 32 random characters, so the unkeyed digest cannot be
-- reversed by search, and SQL needs no secret to compute it.
CREATE FUNCTION public.stewardship_admin_session_key_v1(session_key text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO pg_catalog, public, pg_temp
    AS $$
    SELECT left(encode(sha256(convert_to(session_key, 'UTF8')), 'hex'), 40)
$$;
REVOKE ALL ON FUNCTION public.stewardship_admin_session_key_v1(text) FROM PUBLIC;

-- The two definers' grants before they are replaced (count and ACLs),
-- compared in the DO block. A transaction-local setting holds them, not a
-- temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0057_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public'
      AND p.proname IN ('stewardship_member_names_for_v1','stewardship_leaders_without_user_v1')), true);

-- The Users page's definers compare the digest of the key they are handed,
-- as the Python store does, so the stored session_id the web login can read
-- names no session here either. Python callers still pass the cookie's key.
CREATE OR REPLACE FUNCTION public.stewardship_member_names_for_v1(admin_session text, address text, correlation uuid)
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
    -- Who asks: a live Admin session whose user is an Administrator now. A
    -- session begun before the latest recovery is refused as sessions.py's
    -- recovery epoch refuses it: the command line has no later recheck
    -- before the lookup's audit event and names are committed.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
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

CREATE OR REPLACE FUNCTION public.stewardship_leaders_without_user_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    current_configuration uuid; current_values jsonb; current_snapshot uuid;
    leader_keys text[];
    answer jsonb := jsonb_build_object('leaders', '[]'::jsonb, 'unreachable', 0);
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session whose user is an Administrator now,
    -- exactly as stewardship_member_names_for_v1 (0053) decides it.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, true) THEN
        RAISE EXCEPTION 'Leader list requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- The current campaign, its configuration and the promoted snapshot, as
    -- 0042 reads them; no campaign asking about Ministries, or no promoted
    -- source, means no leaders.
    SELECT runtime.active_configuration_id, campaign.values, pointer.snapshot_id
      INTO current_configuration, current_values, current_snapshot
    FROM public.stewardship_system_configuration runtime
    JOIN public.stewardship_campaign_configuration campaign
        ON campaign.record_id=runtime.current_campaign_id
       AND campaign.configuration_id=runtime.active_configuration_id
    CROSS JOIN public.stewardship_source_current pointer
    WHERE pointer.singleton AND campaign.values->'modules' ? 'ministry';
    IF current_snapshot IS NOT NULL THEN
        SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
          INTO leader_keys
        FROM jsonb_array_elements_text(
            public.stewardship_ministry_leader_roles_v1(current_values)) role(name);
        WITH roster AS MATERIALIZED (
            SELECT payload.member_key, payload.ministry_key, payload.canonical::jsonb AS doc
            FROM public.stewardship_snapshot_roster rm
            JOIN public.stewardship_source_roster payload ON payload.id=rm.payload_id
            WHERE rm.snapshot_id=current_snapshot
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
        ), led AS MATERIALIZED (
            -- 0042's joins and conditions, with the contact record optional.
            SELECT member.source_key AS member_key, duid.ministry AS ministry_duid,
                parsed.member_doc, parsed.family_doc,
                CASE WHEN contact.canonical::jsonb->'schema_version'='1'::jsonb
                    THEN contact.canonical::jsonb->'emails' END AS emails
            FROM held
            JOIN public.stewardship_snapshot_member mm
                ON mm.snapshot_id=current_snapshot AND mm.source_key=held.member_key
            JOIN public.stewardship_source_member member ON member.id=mm.payload_id
            JOIN public.stewardship_snapshot_family fm
                ON fm.snapshot_id=current_snapshot AND fm.source_key=member.family_key
            JOIN public.stewardship_source_family family ON family.id=fm.payload_id
            JOIN public.stewardship_snapshot_ministry tm
                ON tm.snapshot_id=current_snapshot AND tm.source_key=held.ministry_key
            JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
            LEFT JOIN public.stewardship_snapshot_contact cm
                ON cm.snapshot_id=current_snapshot AND cm.source_key='member:' || member.source_key
            LEFT JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
            CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
                family.canonical::jsonb AS family_doc,
                ministry.canonical::jsonb AS ministry_doc) parsed
            CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
                THEN ministry.source_key::bigint END AS ministry) duid
            WHERE parsed.member_doc->'schema_version'='1'::jsonb
              AND parsed.family_doc->'schema_version'='1'::jsonb
              AND parsed.ministry_doc->'schema_version'='1'::jsonb
              AND parsed.member_doc->'active'='true'::jsonb
              AND parsed.family_doc->'active'='true'::jsonb
              AND parsed.ministry_doc->'catalog_present'='true'::jsonb
              AND duid.ministry BETWEEN 1 AND 2147483647
              AND current_values->'ministry_duids' @> jsonb_build_array(duid.ministry)
              AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_activity activity
                  WHERE activity.configuration_id=current_configuration
                    AND activity.organization_id=ministry.organization_id
                    AND activity.ministry_duid=duid.ministry
                    AND NOT activity.active)
        ), people AS MATERIALIZED (
            -- One row per leader Member: its "Last, first" name, whether it
            -- heads its household, its DUID and its listed addresses.
            SELECT DISTINCT ON (led.member_key) led.member_key,
                concat_ws(', ',
                    nullif(btrim(led.member_doc->>'lastName'), ''),
                    nullif(btrim(led.member_doc->>'firstName'), '')) AS name,
                EXISTS (SELECT 1 FROM jsonb_array_elements_text(
                            CASE WHEN jsonb_typeof(led.family_doc->'active_head_duids')='array'
                                THEN led.family_doc->'active_head_duids' END) head(duid)
                        WHERE head.duid=led.member_key) AS head,
                CASE WHEN led.member_key ~ '^[0-9]{1,18}$'
                    THEN led.member_key::bigint END AS duid,
                CASE WHEN jsonb_typeof(led.emails)='array' THEN led.emails
                    ELSE '[]'::jsonb END AS emails
            FROM led ORDER BY led.member_key
        ), addresses AS MATERIALIZED (
            -- Each valid address a leader Member lists, as sign-in matches it.
            SELECT DISTINCT people.member_key, lower(btrim(listed.value->>'value')) AS email
            FROM people CROSS JOIN LATERAL jsonb_array_elements(people.emails) listed(value)
            WHERE listed.value->'valid'='true'::jsonb
              AND jsonb_typeof(listed.value->'value')='string'
              AND length(btrim(listed.value->>'value')) BETWEEN 3 AND 254
        ), unused AS MATERIALIZED (
            -- Only addresses that are not a user in the active configuration.
            SELECT addresses.member_key, addresses.email FROM addresses
            WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_address_rule rule
                WHERE rule.configuration_id=current_configuration
                  AND rule.email=addresses.email)
        ), ranked AS (
            SELECT unused.email, people.name, people.duid, row_number() OVER (
                PARTITION BY unused.email
                ORDER BY people.name='', people.head DESC, people.name,
                    people.duid NULLS LAST) AS position
            FROM unused JOIN people ON people.member_key=unused.member_key
        ), answered AS (
            SELECT ranked.email, ranked.name, ranked.duid,
                (SELECT jsonb_agg(DISTINCT led.ministry_duid ORDER BY led.ministry_duid)
                 FROM unused JOIN led ON led.member_key=unused.member_key
                 WHERE unused.email=ranked.email) AS ministries
            FROM ranked WHERE ranked.position=1
        )
        SELECT jsonb_build_object(
            'leaders', coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'email', answered.email, 'name', answered.name,
                    'member_duid', answered.duid, 'ministries', answered.ministries)
                ORDER BY answered.name, answered.duid NULLS LAST, answered.email)
                FROM answered), '[]'::jsonb),
            'unreachable', (SELECT count(*) FROM people
                WHERE NOT EXISTS (SELECT 1 FROM addresses
                                  WHERE addresses.member_key=people.member_key)))
          INTO answer;
    END IF;
    -- The trace: who listed and how many rows came back, never an address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'user_leaders_listed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded',
            'count',jsonb_array_length(answer->'leaders')));
    RETURN answer;
END
$$;
ALTER FUNCTION public.stewardship_leaders_without_user_v1(text, uuid) SECURITY DEFINER;


-- A new Admin session row names its Django session by digest only. The
-- foreign key to django_session means the parent row is stored under the
-- same digest.
CREATE FUNCTION public.stewardship_portal_session_key_digest_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO pg_catalog, public, pg_temp
    AS $$
BEGIN
    IF NEW.session_id IS NULL OR NEW.session_id !~ '^[0-9a-f]{40}$' THEN
        RAISE EXCEPTION 'An Admin session row stores only its key''s digest'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_portal_session_key_digest_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_portal_session_key_digest_v1 BEFORE INSERT ON public.stewardship_portal_session
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_portal_session_key_digest_v1();

-- End every live Admin session stored under an undigested key, with the
-- revocation instant and audit event Python's sessions._revoke records.
-- Live is ADMIN_ABSOLUTE's deadline and ADMIN_IDLE's 60 minutes
-- (accounts/session_policy.py), as stewardship_admin_session_purge_v1
-- reads them.
WITH migration AS (
    SELECT gen_random_uuid() AS correlation
), ended AS (
    UPDATE public.stewardship_portal_session
    SET revoked_at = GREATEST(statement_timestamp(), last_activity_at, authenticated_at),
        version = version + 1
    WHERE revoked_at IS NULL AND session_id !~ '^[0-9a-f]{40}$'
      AND expires_at > statement_timestamp()
      AND last_activity_at > statement_timestamp() - interval '60 minutes'
    RETURNING id, principal_id
)
INSERT INTO public.stewardship_audit_event
    (id, actor_id, correlation_id, event_type, subject_id)
SELECT gen_random_uuid(), ended.principal_id, migration.correlation,
    'admin_revoked', ended.id
FROM ended CROSS JOIN migration;

DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_admin_session_key_v1(text)') IS NULL
       OR public.stewardship_admin_session_key_v1('abcdefghijklmnopqrstuvwxyz012345')
          <> '653bb1245e828fcda4fa53fcd5a3def5bd7654e6' THEN
        RAISE EXCEPTION 'The Admin session key digest was not installed';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
                   WHERE t.tgrelid='public.stewardship_portal_session'::regclass
                     AND t.tgname='stewardship_portal_session_key_digest_v1'
                     AND NOT t.tgisinternal AND t.tgenabled='O'
                     AND t.tgfoid='public.stewardship_portal_session_key_digest_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'The Admin session key digest guard was not installed';
    END IF;
    -- Neither function is a definer, both keep the fixed search_path, and
    -- PUBLIC may execute neither.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_admin_session_key_v1',
                            'stewardship_portal_session_key_digest_v1')
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE'))<>2 THEN
        RAISE EXCEPTION 'An Admin session key function has the wrong security, search_path or grant';
    END IF;
    -- Each replaced definer: still SECURITY DEFINER with the fixed
    -- search_path and no EXECUTE for PUBLIC, one definition, comparing the
    -- key's digest and never the key itself, and the grants it had.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_member_names_for_v1','stewardship_leaders_without_user_v1')
          AND p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND p.prosrc LIKE '%WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL%'
          AND p.prosrc NOT LIKE '%login.session_id=admin_session%')<>2
       OR to_regprocedure('public.stewardship_member_names_for_v1(text,text,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_leaders_without_user_v1(text,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0057: a Users session definer is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0057_acl', true) IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_member_names_for_v1','stewardship_leaders_without_user_v1')) THEN
        RAISE EXCEPTION 'Migration 0057: a Users session definer lost or changed its grants';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_portal_session
               WHERE revoked_at IS NULL AND session_id !~ '^[0-9a-f]{40}$'
                 AND expires_at > statement_timestamp()
                 AND last_activity_at > statement_timestamp() - interval '60 minutes') THEN
        RAISE EXCEPTION 'An Admin session stored under an undigested key is still live';
    END IF;
END
$check$;
