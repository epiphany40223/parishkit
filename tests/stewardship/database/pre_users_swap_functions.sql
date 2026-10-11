-- Test fixture, frozen: the three functions migration 0052 (the users swap,
-- #952 slice 4) replaces whose earlier bodies exist in no frozen migration
-- file, only in the fresh-install baseline that 0052 updated. Copied
-- verbatim from the baseline at e930e9fc0 (schema/functions.sql and
-- schema/exports.sql, the release before the swap), with CREATE turned into
-- CREATE OR REPLACE so a test can reinstall them over the swapped bodies.
-- None of the three is SECURITY DEFINER in the baseline, so CREATE OR
-- REPLACE restores them exactly. The Ministry scope and leader scope come
-- from frozen migration 0042 instead. test_users_swap_postgresql.py uses
-- this file to put a database into the real state `migrate
-- --users-upgrade-base` leaves: every migration but the swap applied, and
-- these bodies still reading hosted-domain rules. Never edit it.

CREATE OR REPLACE FUNCTION public.stewardship_portal_session_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- #306 M2: the SQL Admin checks trust these rows, so an Admin session
    -- cannot be minted by a web logic bug that SQL can see. The absolute
    -- limit is ADMIN_ABSOLUTE (12 hours, accounts/session_policy.py); an
    -- authority rotation copies the older row's earlier deadline. The
    -- principal must be a live PortalUser whose current address or domain
    -- rule grants at least one Admin role (the same projection as
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
        LEFT JOIN stewardship_domain_rule d ON d.configuration_id=r.active_configuration_id
            AND d.domain=lower(u.hosted_domain) AND d.domain=split_part(lower(u.email),'@',2)
        WHERE u.id=NEW.principal_id AND NOT u.disabled
            AND coalesce(a.roles,d.roles,'[]'::jsonb) ?| ARRAY['administrator','staff','ministry_leader'])
    THEN
        RAISE EXCEPTION 'Admin session requires a current authorized principal and bounded lifetime'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_export_authorized_v1(user_uuid uuid, admin_only boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce((SELECT CASE WHEN admin_only THEN roles ? 'administrator'
        ELSE roles ?| ARRAY['administrator','staff'] END FROM (
        SELECT coalesce(a.roles,d.roles,'[]'::jsonb) AS roles
        FROM stewardship_portal_user u CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        LEFT JOIN stewardship_domain_rule d ON d.configuration_id=r.active_configuration_id
            AND d.domain=lower(u.hosted_domain) AND d.domain=split_part(lower(u.email),'@',2)
        WHERE u.id=user_uuid AND NOT u.disabled
    ) candidate),false)
$$;

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
    FOR entry IN
        SELECT record_id, 'address' AS kind, email AS target, roles
        FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.configuration_id
        UNION ALL
        SELECT record_id, 'domain', domain, roles
        FROM public.stewardship_domain_rule
        WHERE configuration_id = NEW.configuration_id
    LOOP
        prior := NULL;
        alert_kind := NULL;
        IF entry.kind = 'address' THEN
            SELECT roles INTO prior FROM public.stewardship_address_rule
            WHERE configuration_id = NEW.predecessor_id AND email = entry.target;
            IF entry.roles ? 'administrator'
               AND NOT COALESCE(prior ? 'administrator', false) THEN
                alert_kind := 'administrator_granted';
            END IF;
        ELSE
            SELECT roles INTO prior FROM public.stewardship_domain_rule
            WHERE configuration_id = NEW.predecessor_id AND domain = entry.target;
            IF prior IS NULL THEN
                alert_kind := 'domain_created';
            ELSIF entry.roles ? 'staff' AND NOT prior ? 'staff' THEN
                alert_kind := 'domain_staff_granted';
            END IF;
        END IF;
        -- A new domain or any additional exact/domain role broadens access.
        -- Removing an exact denial may expose a domain grant, checked below.
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
    expanded := expanded OR EXISTS (
        SELECT 1 FROM public.stewardship_address_rule previous_rule
        JOIN public.stewardship_domain_rule domain
          ON domain.configuration_id = NEW.configuration_id
         AND domain.domain = split_part(previous_rule.email, '@', 2)
        WHERE previous_rule.configuration_id = NEW.predecessor_id
          AND NOT domain.roles <@ previous_rule.roles
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_address_rule current
                          WHERE current.configuration_id = NEW.configuration_id
                            AND current.email = previous_rule.email)
    );
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
