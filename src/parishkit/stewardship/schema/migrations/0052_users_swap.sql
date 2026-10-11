-- Frozen forward migration file 0052 (the repository-wide file sequence):
-- sign-in reads only users (#952, slice 4). It is installed by the Django
-- migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that each replaced function's copy here still equals the
-- fresh-install baseline's. A fresh install runs the baseline and the
-- earlier files and then this; the baseline already carries the replaced
-- bodies, so the install ends in the same catalog as an upgraded database.
--
-- The Administrator decided (2026-10-10) that sign-in is by email address
-- only. Migration 0047 added the users form and the offline `upgrade-users`
-- conversion, which runs before this file in the same offline upgrade
-- (operations specification, "Users upgrade from sign-in rules"). Once the
-- configuration holds users there are no hosted-domain rules, so every
-- function that decided access by joining stewardship_domain_rule reads only
-- the address-rule projection, where each user is one row whose roles are
-- its role's cumulative set. This file:
--
--   replaces stewardship_portal_session_admission_v1
--                                            an Admin session needs a user
--   replaces stewardship_export_authorized_v1
--                                            export authority comes from the
--                                            user's roles
--   replaces stewardship_ministry_scope_v1  a Ministry report scope needs a
--                                            user
--   replaces stewardship_ministry_leader_scope_v1
--                                            the role-derived scope needs a
--                                            user (definer, re-asserted)
--   replaces stewardship_policy_activation_v1
--                                            alerts and the denial-namespace
--                                            reset read users only; no
--                                            hosted-domain alert remains
--
-- Each body is its latest definition with only the hosted-domain join (and
-- the comment naming it) removed: the baseline's, and 0042's for the leader
-- scope. CREATE OR REPLACE keeps owners and grants; only the leader scope is
-- SECURITY DEFINER, stated again below. The policy projection and
-- completeness triggers are unchanged: they validate rows, never grant
-- access, and initial setup still writes an exact-address rule until the
-- setup wizard writes users (#952 slice 5); the cleanup migration removes
-- their hosted-domain branches with the tables.
--
-- The closing DO block refuses to commit unless the active configuration is
-- in the users form, so these functions never run on unconverted sign-in
-- rules. Two states have no rules to convert: a database with no active
-- configuration (a fresh install, before bootstrap) and a deployment still
-- in initial setup, judged exactly as startup judges it
-- (users_schema.require_users_form). Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- An Admin session needs a user that grants an Admin role (#952).
CREATE OR REPLACE FUNCTION public.stewardship_portal_session_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- #306 M2: the SQL Admin checks trust these rows, so an Admin session
    -- cannot be minted by a web logic bug that SQL can see. The absolute
    -- limit is ADMIN_ABSOLUTE (12 hours, accounts/session_policy.py); an
    -- authority rotation copies the older row's earlier deadline. The
    -- principal must be a live PortalUser whose current user (#952; an
    -- address rule) grants at least one Admin role (the same projection as
    -- stewardship_export_authorized_v1). Python may grant fewer roles than
    -- the rule names, never more.
    IF NEW.expires_at > statement_timestamp() + interval '12 hours'
       OR NEW.authenticated_at > statement_timestamp()
       OR NEW.last_activity_at > statement_timestamp()
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_portal_user u
        CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        WHERE u.id=NEW.principal_id AND NOT u.disabled
            AND coalesce(a.roles,'[]'::jsonb) ?| ARRAY['administrator','staff','ministry_leader'])
    THEN
        RAISE EXCEPTION 'Admin session requires a current authorized principal and bounded lifetime'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- Export authority comes only from the user's roles (#952).
CREATE OR REPLACE FUNCTION public.stewardship_export_authorized_v1(user_uuid uuid, admin_only boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce((SELECT CASE WHEN admin_only THEN roles ? 'administrator'
        ELSE roles ?| ARRAY['administrator','staff'] END FROM (
        SELECT coalesce(a.roles,'[]'::jsonb) AS roles
        FROM stewardship_portal_user u CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        WHERE u.id=user_uuid AND NOT u.disabled
    ) candidate),false)
$$;

-- A Ministry report scope needs a user (#952); Ministries still come only
-- from the role-derived scope (#922).
CREATE OR REPLACE FUNCTION public.stewardship_ministry_scope_v1(user_uuid uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH policy AS (
        SELECT coalesce(a.roles,'[]'::jsonb) AS roles,
            stewardship_ministry_leader_scope_v1(u.id) AS ministries
        FROM stewardship_portal_user u CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        WHERE u.id=user_uuid AND NOT u.disabled
    )
    SELECT jsonb_build_object('capability','ministry_report',
        'operational',p.roles ?| ARRAY['administrator','staff'],
        'ministries',p.ministries)
    FROM policy p WHERE p.roles ?| ARRAY['administrator','staff','ministry_leader']
$$;

-- The role-derived Ministry scope (#922) of a user (#952): the ascending
-- DUIDs of the Ministries stewardship_ministry_leaders_v1 lists for the
-- user's address, or [] for an unknown or disabled portal user or one with
-- no user granting Ministry leader, Staff or Administrator. Definer, as
-- before, and stated again on its own, as every definer is.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_leader_scope_v1(user_uuid uuid)
    RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT coalesce(jsonb_agg(DISTINCT leader.ministry_duid ORDER BY leader.ministry_duid),
        '[]'::jsonb)
    FROM public.stewardship_portal_user u
    CROSS JOIN public.stewardship_system_configuration r
    LEFT JOIN public.stewardship_address_rule a
        ON a.configuration_id=r.active_configuration_id AND a.email=lower(u.email)
    JOIN public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(u.email)
    WHERE u.id=user_uuid AND NOT u.disabled
      AND coalesce(a.roles,'[]'::jsonb) ?| ARRAY['administrator','staff','ministry_leader']
$$;
ALTER FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) FROM PUBLIC;

-- Security alerts and the denial-namespace reset read users only (#952).
CREATE OR REPLACE FUNCTION public.stewardship_policy_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    entry record;
    prior jsonb;
    recipients jsonb;
    alert_kind text;
    expanded boolean := false;
    event_id uuid;
    key_record uuid;
    new_key text;
    old_key text;
    old_id text;
    key_recipients jsonb;
    -- Who hears of a replaced backup key: everyone who was an Administrator
    -- in any configuration in effect this recently (activated in the window,
    -- or replaced by one that was), so one who first removed the others
    -- within the window cannot hide it.
    key_alert_window CONSTANT interval := interval '30 days';
BEGIN
    SELECT COALESCE(jsonb_agg(email ORDER BY email), '[]'::jsonb) INTO recipients
    FROM public.stewardship_address_rule
    WHERE configuration_id = NEW.predecessor_id AND roles ? 'administrator';
    -- Users (#952): sign-in is by address only, so hosted-domain rules
    -- grant nothing and are not read.
    FOR entry IN
        SELECT record_id, email AS target, roles
        FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.configuration_id
    LOOP
        prior := NULL;
        alert_kind := NULL;
        SELECT roles INTO prior FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.predecessor_id AND email = entry.target;
        IF entry.roles ? 'administrator'
           AND NOT COALESCE(prior ? 'administrator', false) THEN
            alert_kind := 'administrator_granted';
        END IF;
        -- Any additional role broadens access.
        expanded := expanded OR NOT entry.roles <@ COALESCE(prior, '[]'::jsonb);
        IF alert_kind IS NOT NULL THEN
            event_id := gen_random_uuid();
            INSERT INTO public.stewardship_policy_security_event
                (id, created_at, actor_id, correlation_id, activation_id,
                 rule_record_id, target, kind, before_roles, after_roles, recipients)
            VALUES (event_id, NEW.created_at, NEW.actor_id, NEW.correlation_id, NEW.id,
                entry.record_id, entry.target, alert_kind, COALESCE(prior, '[]'::jsonb),
                entry.roles, recipients);
            INSERT INTO public.stewardship_audit_event
                (id, actor_id, correlation_id, event_type, subject_id)
            VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
                'policy_security_event', event_id);
        END IF;
    END LOOP;
    -- A replaced backup encryption key (#198) decides who can open every
    -- later backup, so it is announced to the Administrators like a widened
    -- sign-in rule. The key before is the previous configured key or, before
    -- one was set, the key the newest backup used, read only by a login that
    -- may. Key IDs are the first 16 hex digits of the key's SHA-256.
    SELECT record_id, settings->>'public_key' INTO key_record, new_key
    FROM public.stewardship_applied_integration
    WHERE configuration_id = NEW.configuration_id AND kind = 'backup_key';
    IF new_key IS NOT NULL THEN
        SELECT settings->>'public_key' INTO old_key
        FROM public.stewardship_applied_integration
        WHERE configuration_id = NEW.predecessor_id AND kind = 'backup_key';
        IF old_key IS DISTINCT FROM new_key THEN
            IF old_key IS NOT NULL THEN
                old_id := left(encode(sha256(decode(old_key, 'base64')), 'hex'), 16);
            ELSIF has_column_privilege('public.stewardship_backup_run', 'id', 'SELECT')
               AND has_column_privilege('public.stewardship_backup_run', 'completed_at', 'SELECT')
               AND has_column_privilege('public.stewardship_backup_run', 'recipient_fingerprint', 'SELECT') THEN
                SELECT recipient_fingerprint INTO old_id
                FROM public.stewardship_backup_run
                ORDER BY completed_at DESC, id DESC LIMIT 1;
            END IF;
            SELECT COALESCE(jsonb_agg(DISTINCT admins.email ORDER BY admins.email),
                    '[]'::jsonb) INTO key_recipients
            FROM (
                -- Each activation in the window, and the configuration it
                -- replaced: that one was in effect when the window opened.
                SELECT rule.email FROM public.stewardship_address_rule rule
                JOIN public.stewardship_config_activation activation
                  ON rule.configuration_id IN (activation.configuration_id,
                                               activation.predecessor_id)
                WHERE rule.roles ? 'administrator' AND activation.id <> NEW.id
                  AND activation.created_at >= NEW.created_at - key_alert_window
                UNION
                SELECT email FROM public.stewardship_address_rule
                WHERE configuration_id = NEW.predecessor_id
                  AND roles ? 'administrator'
            ) admins;
            event_id := gen_random_uuid();
            INSERT INTO public.stewardship_policy_security_event
                (id, created_at, actor_id, correlation_id, activation_id,
                 rule_record_id, target, kind, before_roles, after_roles, recipients)
            VALUES (event_id, NEW.created_at, NEW.actor_id, NEW.correlation_id, NEW.id,
                key_record, 'Backup encryption key', 'backup_key_replaced',
                CASE WHEN old_id IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(old_id) END,
                jsonb_build_array(left(encode(sha256(decode(new_key, 'base64')), 'hex'), 16)),
                key_recipients);
            INSERT INTO public.stewardship_audit_event
                (id, actor_id, correlation_id, event_type, subject_id)
            VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
                'policy_security_event', event_id);
        END IF;
    END IF;
    -- New manual scope or an independent manual Minister origin may unsuppress
    -- a seeded login without changing any configured role-name array.
    expanded := expanded OR EXISTS (
        SELECT 1 FROM public.stewardship_ministry_assignment assignment
        WHERE assignment.configuration_id = NEW.configuration_id
          AND assignment.source = 'manual'
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_ministry_assignment previous_assignment
              WHERE previous_assignment.configuration_id = NEW.predecessor_id
                AND previous_assignment.email = assignment.email
                AND previous_assignment.ministry_duid = assignment.ministry_duid
                AND previous_assignment.source = 'manual')
    ) OR EXISTS (
        SELECT 1 FROM public.stewardship_address_rule rule
        JOIN public.stewardship_address_grant grant_row ON grant_row.rule_id = rule.id
        WHERE rule.configuration_id = NEW.configuration_id
          AND grant_row.role = 'ministry_leader' AND grant_row.origins ? 'manual'
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_address_rule previous_rule
              JOIN public.stewardship_address_grant previous_grant
                ON previous_grant.rule_id = previous_rule.id
              WHERE previous_rule.configuration_id = NEW.predecessor_id
                AND previous_rule.email = rule.email
                AND previous_grant.role = 'ministry_leader'
                AND previous_grant.origins ? 'manual')
    );
    IF expanded THEN
        INSERT INTO public.stewardship_policy_epoch
            (id, created_at, actor_id, correlation_id, activation_id, sequence)
        SELECT gen_random_uuid(), NEW.created_at, NEW.actor_id, NEW.correlation_id,
            NEW.id, COALESCE(MAX(sequence), 0) + 1 FROM public.stewardship_policy_epoch;
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
            'policy_denial_namespace_reset', NEW.id);
    END IF;
    RETURN NEW;
END;
$$;

-- Refuse to commit unless everything above is installed as declared and the
-- configuration it serves is already in the users form.
DO $check$
DECLARE
    runtime record;
BEGIN
    -- The five replaced functions: the fixed search_path, no hosted-domain
    -- join left, each one's new text, and SECURITY DEFINER (with no EXECUTE
    -- for PUBLIC) on the leader scope only. No other overload of any exists.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc, 'domain_rule')=0 AND strpos(p.prosrc, 'd.roles')=0
          AND ((p.proname='stewardship_portal_session_admission_v1' AND NOT p.prosecdef
                AND pg_get_function_identity_arguments(p.oid)=''
                AND p.prosrc LIKE '%AND coalesce(a.roles,''[]''::jsonb) ?| ARRAY[''administrator'',''staff'',''ministry_leader''])%')
            OR (p.proname='stewardship_export_authorized_v1' AND NOT p.prosecdef
                AND pg_get_function_identity_arguments(p.oid)='user_uuid uuid, admin_only boolean'
                AND p.prosrc LIKE '%SELECT coalesce(a.roles,''[]''::jsonb) AS roles%')
            OR (p.proname='stewardship_ministry_scope_v1' AND NOT p.prosecdef
                AND pg_get_function_identity_arguments(p.oid)='user_uuid uuid'
                AND p.prosrc LIKE '%SELECT coalesce(a.roles,''[]''::jsonb) AS roles,%')
            OR (p.proname='stewardship_ministry_leader_scope_v1' AND p.prosecdef
                AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
                AND pg_get_function_identity_arguments(p.oid)='user_uuid uuid'
                AND p.prosrc LIKE '%AND coalesce(a.roles,''[]''::jsonb) ?| ARRAY[''administrator'',''staff'',''ministry_leader'']%')
            OR (p.proname='stewardship_policy_activation_v1' AND NOT p.prosecdef
                AND pg_get_function_identity_arguments(p.oid)=''
                AND p.prosrc LIKE '%SELECT record_id, email AS target, roles%'))
       )<>5
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN
               ('stewardship_portal_session_admission_v1',
                'stewardship_export_authorized_v1', 'stewardship_ministry_scope_v1',
                'stewardship_ministry_leader_scope_v1',
                'stewardship_policy_activation_v1'))<>5 THEN
        RAISE EXCEPTION 'Migration 0052: a replaced function is not installed as declared';
    END IF;
    -- The active configuration must hold users: the offline upgrade-users
    -- conversion runs first. No runtime row or no active configuration is a
    -- fresh install; initial setup is exempt exactly as startup exempts it.
    SELECT r.mode, r.current_campaign_id, r.restore_review_required,
        v.validation_schema
    INTO runtime
    FROM public.stewardship_system_configuration r
    JOIN public.stewardship_configuration_version v ON v.id=r.active_configuration_id;
    IF FOUND AND runtime.validation_schema IS DISTINCT FROM 'users-v10'
       AND NOT (runtime.validation_schema='bootstrap-policy-v1'
                AND runtime.mode='testing'
                AND runtime.current_campaign_id IS NULL
                AND NOT runtime.restore_review_required) THEN
        RAISE EXCEPTION 'Migration 0052: the configuration still holds sign-in rules; run upgrade-users first'
            USING HINT = 'Migrate with --users-upgrade-base, apply database-grants, run upgrade-users, then migrate again.';
    END IF;
END
$check$;
