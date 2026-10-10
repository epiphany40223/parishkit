-- Frozen forward migration file 0047 (the repository-wide file sequence):
-- the users form of the sign-in policy and its offline conversion (#952,
-- slice 1). It is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that each replaced function's copy here still equals the
-- fresh-install baseline's. A fresh install runs the baseline and the
-- earlier files and then this; the baseline already carries the replaced
-- bodies and the final table and constraint definitions, so the install
-- ends in the same catalog as an upgraded database.
--
-- The Administrator decided (2026-10-10) that sign-in is by email address
-- only: one list of users, each with a Name and one role, Ministry leader <
-- Staff < Administrator. A user is materialized as one row of the existing
-- address-rule projection whose roles hold the role's cumulative set, so
-- every SQL check of roles keeps its meaning. This file:
--
--   alters stewardship_address_rule         adds name and role; the origin
--                                            columns become nullable;
--                                            address_rule_shape keeps an
--                                            older rule and a user apart and
--                                            ties roles to role
--   alters the configuration CHECKs         admits the users-v10 document
--                                            schema, the
--                                            operator-upgrade-users-v10
--                                            request format and its
--                                            operator_upgrade authority, with
--                                            one request per operation ID
--   replaces stewardship_policy_projection_v1
--                                            a user row matches its YAML
--                                            entry (email, Name, role)
--   replaces stewardship_policy_complete_v1 users-v10 holds only users and
--                                            only it holds them; a user has
--                                            no grant rows
--   replaces the campaign, content and Ministry projection and completeness
--            functions (six)                 users-v10 joins their schema
--                                            lists, as it keeps every v8
--                                            section
--   adds stewardship_users_form_v1          activation guard: only the
--                                            offline upgrade converts, once,
--                                            keeping every Administrator;
--                                            the users form never goes back
--   adds stewardship_portal_identities_v1   each recorded Google identity
--                                            and whether it ever signed in
--                                            (definer; admin-recovery)
--   adds stewardship_member_names_v1        the ParishSoft Member names of
--                                            each address the upgrade names
--                                            (its rules and recorded
--                                            identities; no address
--                                            argument), in the Name
--                                            default's order (definer;
--                                            admin-recovery)
--
-- Nothing converts here: existing configurations, rows and sessions are
-- unchanged, and every existing row already has the older shape. The
-- conversion is the separate offline `upgrade-users` command. None of the
-- replaced functions is SECURITY DEFINER; CREATE OR REPLACE keeps their
-- owners and grants. Each replaced body is its latest definition with only
-- the change described. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Users (#952): a Name and one role on the address-rule projection. An
-- older rule keeps its creation origin and operation and has neither; a
-- user has both and no origin, and its roles are its role's cumulative set.
ALTER TABLE "stewardship_address_rule" ADD COLUMN "name" varchar(200) NULL;
ALTER TABLE "stewardship_address_rule" ADD COLUMN "role" varchar(32) NULL;
ALTER TABLE "stewardship_address_rule" ALTER COLUMN "creation_operation" DROP NOT NULL;
ALTER TABLE "stewardship_address_rule" ALTER COLUMN "creation_origin" DROP NOT NULL;
ALTER TABLE "stewardship_address_rule" ADD CONSTRAINT "address_rule_shape" CHECK ((((creation_operation IS NOT NULL) AND (creation_origin IS NOT NULL) AND (name IS NULL) AND (role IS NULL)) OR ((creation_operation IS NULL) AND (creation_origin IS NULL) AND (name IS NOT NULL) AND ((role)::text = 'ministry_leader'::text) AND (roles = '["ministry_leader"]'::jsonb)) OR ((creation_operation IS NULL) AND (creation_origin IS NULL) AND (name IS NOT NULL) AND ((role)::text = 'staff'::text) AND (roles = '["ministry_leader", "staff"]'::jsonb)) OR ((creation_operation IS NULL) AND (creation_origin IS NULL) AND (name IS NOT NULL) AND ((role)::text = 'administrator'::text) AND (roles = '["administrator", "ministry_leader", "staff"]'::jsonb))));

-- The users-v10 document schema, and the one request format that converts
-- to it: an offline operator upgrade, attributed to a named operator and
-- the confirmed deployment, with no portal actor and no recovery target.
ALTER TABLE "stewardship_configuration_version" DROP CONSTRAINT "configuration_validation_schema";
ALTER TABLE "stewardship_configuration_version" ADD CONSTRAINT "configuration_validation_schema" CHECK (((validation_schema)::text = ANY ((ARRAY['parish-integrations-v1'::character varying, 'foundation-policy-v2'::character varying, 'campaign-foundation-v3'::character varying, 'bootstrap-policy-v1'::character varying, 'ministry-activity-v4'::character varying, 'campaign-content-v5'::character varying, 'source-cadence-v8'::character varying, 'users-v10'::character varying])::text[])));
ALTER TABLE "stewardship_config_request" DROP CONSTRAINT "config_request_schema";
ALTER TABLE "stewardship_config_request" ADD CONSTRAINT "config_request_schema" CHECK (((request_schema)::text = ANY ((ARRAY['parish-integrations-patch-v1'::character varying, 'foundation-policy-patch-v2'::character varying, 'operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'campaign-foundation-patch-v3'::character varying, 'ministry-activity-patch-v4'::character varying, 'operator-recovery-ministry-v4'::character varying, 'campaign-content-patch-v5'::character varying, 'operator-recovery-content-v5'::character varying, 'integration-credential-patch-v6'::character varying, 'initial-setup-patch-v7'::character varying, 'source-cadence-patch-v8'::character varying, 'operator-recovery-cadence-v8'::character varying, 'integration-credential-cadence-v8'::character varying, 'chair-seed-patch-v9'::character varying, 'operator-upgrade-users-v10'::character varying])::text[])));
ALTER TABLE "stewardship_config_request" DROP CONSTRAINT "config_request_actor";
ALTER TABLE "stewardship_config_request" ADD CONSTRAINT "config_request_actor" CHECK ((((actor_id IS NOT NULL) AND ((authority)::text = 'admin'::text) AND (confirmed_deployment_id IS NULL) AND (operator_name IS NULL) AND (operator_reason IS NULL) AND (recovery_target IS NULL) AND (NOT ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying, 'operator-upgrade-users-v10'::character varying])::text[])))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_recovery'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NOT NULL) AND ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying])::text[]))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_upgrade'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NULL) AND ((request_schema)::text = 'operator-upgrade-users-v10'::text))));
CREATE UNIQUE INDEX "operator_upgrade_operation" ON "stewardship_config_request" ("request_key") WHERE "authority" = 'operator_upgrade';

-- A user's row (#952) matches its YAML entry; the older forms are unchanged.
CREATE OR REPLACE FUNCTION public.stewardship_policy_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected jsonb;
    actual jsonb;
    config uuid;
    record uuid;
    parent public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'stewardship_address_grant' THEN
        SELECT * INTO parent FROM public.stewardship_address_rule
        WHERE id = NEW.rule_id;
        config := parent.configuration_id;
        record := parent.record_id;
    ELSE
        config := NEW.configuration_id;
        record := NEW.record_id;
    END IF;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version version,
        jsonb_array_elements(version.canonical_document->'sections'->'login_rules') item
    WHERE version.id = config AND version.validation_schema IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10')
        AND item->>'id' = record::text;
    IF expected IS NULL THEN
        RAISE EXCEPTION 'Policy projection requires matching YAML record'
            USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'stewardship_domain_rule' THEN
        actual := jsonb_build_object('kind', 'domain', 'domain', NEW.domain,
            'roles', NEW.roles);
        IF NEW.domain <> lower(NEW.domain) OR NEW.domain = 'gmail.com'
           OR NEW.roles = '[]'::jsonb
           OR NOT NEW.roles <@ '["staff", "ministry_leader"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid hosted domain grant' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_rule'
          AND to_jsonb(NEW)->>'role' IS NOT NULL THEN
        -- A user (#952): its YAML entry is the email, Name and role. The
        -- cumulative roles follow from the role (address_rule_shape). The
        -- test reads role through to_jsonb: PL/pgSQL does not short-circuit
        -- AND, and this trigger also fires on tables without that column.
        actual := jsonb_build_object('kind', 'user', 'email', NEW.email,
            'name', NEW.name, 'role', NEW.role);
        IF NEW.email <> lower(btrim(NEW.email)) THEN
            RAISE EXCEPTION 'Invalid user' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_rule' THEN
        actual := jsonb_build_object('kind', 'address', 'email', NEW.email,
            'roles', NEW.roles, 'creation_origin', NEW.creation_origin,
            'creation_operation', NEW.creation_operation);
        expected := expected - 'grants';
        IF NEW.email <> lower(NEW.email)
           OR NEW.creation_origin NOT IN ('manual', 'chair-seed')
           OR NOT NEW.roles <@
               '["administrator", "staff", "ministry_leader"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid address grant' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_grant' THEN
        actual := NEW.origins;
        expected := expected->'grants'->NEW.role;
        IF expected IS NULL OR NEW.origins = '{}'::jsonb
           OR NEW.role NOT IN ('administrator', 'staff', 'ministry_leader')
           OR (NEW.origins ? 'chair-seed' AND NEW.role <> 'ministry_leader') THEN
            RAISE EXCEPTION 'Invalid grant provenance' USING ERRCODE = '23514';
        END IF;
    ELSE
        actual := jsonb_build_object('kind', 'assignment', 'email', NEW.email,
            'ministry_duid', NEW.ministry_duid, 'source', NEW.source,
            'operation_id', NEW.operation_id);
        IF NEW.email <> lower(NEW.email) OR NEW.source NOT IN ('manual', 'chair-seed')
           OR NEW.ministry_duid < 1 THEN
            RAISE EXCEPTION 'Invalid ministry assignment' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Policy projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

-- users-v10 holds only users and only it holds them; a user has no grants.
CREATE OR REPLACE FUNCTION public.stewardship_policy_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected bigint;
    actual bigint;
    rule public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF NEW.validation_schema NOT IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10') THEN RETURN NULL; END IF;
    expected := jsonb_array_length(NEW.canonical_document->'sections'->'login_rules');
    SELECT (SELECT count(*) FROM public.stewardship_domain_rule
            WHERE configuration_id = NEW.id)
         + (SELECT count(*) FROM public.stewardship_address_rule
            WHERE configuration_id = NEW.id)
         + (SELECT count(*) FROM public.stewardship_ministry_assignment
            WHERE configuration_id = NEW.id)
    INTO actual;
    IF expected IS NULL OR actual <> expected OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id AND roles ? 'administrator'
    ) THEN
        RAISE EXCEPTION 'Policy must be complete and retain an Administrator'
            USING ERRCODE = '23514';
    END IF;
    -- The users form (#952) holds only users, and only it holds them.
    IF EXISTS (SELECT 1 FROM public.stewardship_address_rule
               WHERE configuration_id = NEW.id
                 AND (role IS NOT NULL) <> (NEW.validation_schema = 'users-v10'))
       OR (NEW.validation_schema = 'users-v10' AND (
           EXISTS (SELECT 1 FROM public.stewardship_domain_rule
                   WHERE configuration_id = NEW.id)
           OR EXISTS (SELECT 1 FROM public.stewardship_ministry_assignment
                      WHERE configuration_id = NEW.id))) THEN
        RAISE EXCEPTION 'Policy form does not match the configuration schema'
            USING ERRCODE = '23514';
    END IF;
    FOR rule IN SELECT * FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id AND role IS NULL LOOP
        IF (SELECT count(*) FROM public.stewardship_address_grant
            WHERE rule_id = rule.id)
           <> jsonb_array_length(rule.roles) THEN
            RAISE EXCEPTION 'Policy grant provenance is incomplete'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

-- users-v10 keeps every section of source-cadence-v8, so it joins the
-- campaign, content and Ministry projection and completeness checks.
CREATE OR REPLACE FUNCTION public.stewardship_campaign_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.validation_schema NOT IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10') THEN RETURN NULL; END IF;
    IF (SELECT count(*) FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'campaigns'), 0)
       OR (SELECT count(*) FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'schedules'), 0) THEN
        RAISE EXCEPTION 'Campaign projections are incomplete' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT campaign_id FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind IN ('initial', 'reminder')
        GROUP BY campaign_id
        HAVING count(*) FILTER (WHERE kind = 'initial') <> 1
           OR count(DISTINCT due_at) <> count(*)
           OR min(due_at) FILTER (WHERE kind = 'reminder') <=
              min(due_at) FILTER (WHERE kind = 'initial')
    ) OR EXISTS (
        SELECT campaign_id, kind FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind IN ('daily_digest', 'weekly_digest')
        GROUP BY campaign_id, kind HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Invalid schedule relationships' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_campaign_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; section text; owner_zone text; expected_due timestamptz;
    owner_start timestamptz; owner_end timestamptz;
BEGIN
    section := CASE WHEN TG_TABLE_NAME = 'stewardship_campaign_configuration'
                    THEN 'campaigns' ELSE 'schedules' END;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version v,
        jsonb_array_elements(v.canonical_document->'sections'->section) item
    WHERE v.id = NEW.configuration_id AND item->>'id' = NEW.record_id::text
      AND v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10');
    IF expected IS NULL OR expected IS DISTINCT FROM NEW.values THEN
        RAISE EXCEPTION 'Campaign projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    IF section = 'campaigns' THEN
        IF NEW.name IS DISTINCT FROM expected->>'name'
           OR NEW.timezone IS DISTINCT FROM expected->>'timezone'
           OR NEW.start_date IS DISTINCT FROM (expected->>'start_date')::date
           OR NEW.end_date IS DISTINCT FROM (expected->>'end_date')::date
           OR jsonb_typeof(expected->'modules') IS DISTINCT FROM 'array'
           OR coalesce(jsonb_array_length(expected->'modules'), 0) < 1
           OR expected->'modules' IS DISTINCT FROM (
               SELECT jsonb_agg(value ORDER BY value COLLATE "C")
               FROM (SELECT DISTINCT value
                     FROM jsonb_array_elements_text(expected->'modules')) canonical
           )
           OR NOT expected->'modules' <@ '["census", "ministry", "financial"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid indexed campaign projection' USING ERRCODE = '23514';
        END IF;
        IF NEW.starts_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                NEW.start_date::timestamp, NEW.timezone)
           OR NEW.ends_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                (NEW.end_date + 1)::timestamp, NEW.timezone) THEN
            RAISE EXCEPTION 'Invalid resolved campaign boundary' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb AND (
            (expected->'financial'->>'end')::date IS DISTINCT FROM
                ((expected->'financial'->>'start')::date + interval '1 year -1 day')::date
            OR (expected->'financial'->>'comparison_end')::date IS DISTINCT FROM
                ((expected->'financial'->>'comparison_start')::date + interval '1 year -1 day')::date
        ) THEN
            RAISE EXCEPTION 'Invalid financial period' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb
           AND (expected->'financial'->>'start')::date <= NEW.end_date
           AND (expected->'financial'->>'end')::date >= NEW.start_date
           AND expected->'financial'->'overlap_confirmed' IS DISTINCT FROM 'true'::jsonb THEN
            RAISE EXCEPTION 'Financial overlap requires confirmation' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.campaign_id::text IS DISTINCT FROM expected->>'campaign_id'
           OR NEW.kind IS DISTINCT FROM expected->>'kind'
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                          WHERE configuration_id = NEW.configuration_id
                            AND record_id = NEW.campaign_id) THEN
            RAISE EXCEPTION 'Invalid schedule ownership' USING ERRCODE = '23514';
        END IF;
        SELECT timezone, starts_at, ends_at INTO owner_zone, owner_start, owner_end
        FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.configuration_id AND record_id = NEW.campaign_id;
        IF NEW.kind IN ('initial', 'reminder') THEN
            expected_due := public.stewardship_resolve_local_v1(
                (expected->>'date')::date + (expected->>'time')::time, owner_zone);
        END IF;
        IF NEW.due_at IS DISTINCT FROM expected_due THEN
            RAISE EXCEPTION 'Invalid resolved schedule boundary' USING ERRCODE = '23514';
        END IF;
        IF NEW.due_at < owner_start OR NEW.due_at >= owner_end THEN
            RAISE EXCEPTION 'Schedule is outside campaign interval' USING ERRCODE = '23514';
        END IF;
        -- Match preparation's global lock even for direct competing inserts.
        -- PL/pgSQL's following READ COMMITTED query sees the previous winner.
        PERFORM pg_advisory_xact_lock(736210, 1);
        IF EXISTS (SELECT 1 FROM public.stewardship_schedule_revision
                   WHERE record_id = NEW.record_id
                     AND (campaign_id <> NEW.campaign_id OR kind <> NEW.kind)) THEN
            RAISE EXCEPTION 'Logical schedule identity is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_content_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE records jsonb; actual jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'content', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('campaign-content-v5', 'source-cadence-v8', 'users-v10') THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Content requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid content shape' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_content_version
        WHERE configuration_id=NEW.id AND NOT (
            (kind='page' AND slot IN ('welcome', 'login_help', 'pre_start', 'post_end',
                'census', 'member_census', 'ministry', 'financial', 'closing',
                'additional', 'review', 'thank_you', 'access_denied',
                'submission_confirmation'))
            OR (kind='email' AND slot IN ('initial', 'reminder', 'confirmation',
                'daily_digest', 'weekly_digest', 'critical_alert'))
        )) THEN
        RAISE EXCEPTION 'Invalid content slot' USING ERRCODE='23514';
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id, 'values',
        jsonb_build_object('campaign_id', campaign_id, 'kind', kind, 'slot', slot,
            'subject', subject, 'html', html, 'text', text)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_content_version WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Content projections are incomplete' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_content_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'content') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('campaign-content-v5', 'source-cadence-v8', 'users-v10')
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object('id', NEW.record_id,
        'values', jsonb_build_object('campaign_id', NEW.campaign_id,
            'kind', NEW.kind, 'slot', NEW.slot, 'subject', NEW.subject,
            'html', NEW.html, 'text', NEW.text))
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                      WHERE configuration_id=NEW.configuration_id
                        AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Content differs from YAML' USING ERRCODE='23514';
    END IF;
    SELECT predecessor_id INTO predecessor
    FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF EXISTS (
        WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM public.stewardship_configuration_version
            WHERE id=predecessor
            UNION
            SELECT p.id, p.predecessor_id
            FROM public.stewardship_configuration_version p
            JOIN chain c ON p.id=c.predecessor_id
        ) SELECT 1 FROM public.stewardship_content_version v
          JOIN chain c ON c.id=v.configuration_id
          WHERE v.record_id=NEW.record_id AND
              (v.campaign_id, v.kind, v.slot, v.subject, v.html, v.text)
              IS DISTINCT FROM
              (NEW.campaign_id, NEW.kind, NEW.slot, NEW.subject, NEW.html, NEW.text)
    ) THEN
        RAISE EXCEPTION 'Content revision identities must remain immutable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE records jsonb; actual jsonb; vals jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'ministries', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10') THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Ministry activity requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid Ministry activity shape' USING ERRCODE='23514';
    END IF;
    FOR vals IN SELECT item->'values' FROM jsonb_array_elements(records) item LOOP
        IF jsonb_typeof(vals->'organization_id') IS DISTINCT FROM 'number'
           OR jsonb_typeof(vals->'ministry_duid') IS DISTINCT FROM 'number'
           OR vals->>'organization_id' !~ '^[1-9][0-9]*$'
           OR vals->>'ministry_duid' !~ '^[1-9][0-9]*$'
           OR jsonb_typeof(vals->'active') IS DISTINCT FROM 'boolean' THEN
            RAISE EXCEPTION 'Invalid Ministry activity types' USING ERRCODE='23514';
        END IF;
    END LOOP;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id,
        'values', jsonb_build_object('organization_id', organization_id,
             'ministry_duid', ministry_duid, 'active', active)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_ministry_activity WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Ministry activity projections are incomplete'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $_$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'ministries') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10')
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object(
        'id', NEW.record_id, 'values', jsonb_build_object(
            'organization_id', NEW.organization_id,
            'ministry_duid', NEW.ministry_duid, 'active', NEW.active)) THEN
        RAISE EXCEPTION 'Ministry activity differs from YAML' USING ERRCODE='23514';
    END IF;
    SELECT predecessor_id INTO predecessor
    FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF EXISTS (
        WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM public.stewardship_configuration_version
            WHERE id=predecessor
            UNION
            SELECT p.id, p.predecessor_id
            FROM public.stewardship_configuration_version p
            JOIN chain c ON p.id=c.predecessor_id
        ) SELECT 1 FROM public.stewardship_ministry_activity m
          JOIN chain c ON m.configuration_id=c.id
          WHERE (m.record_id=NEW.record_id AND
                 (m.organization_id, m.ministry_duid) IS DISTINCT FROM
                 (NEW.organization_id, NEW.ministry_duid))
             OR (m.record_id<>NEW.record_id AND
                 m.organization_id=NEW.organization_id AND
                 m.ministry_duid=NEW.ministry_duid)
    ) THEN
        RAISE EXCEPTION 'Ministry activity identities must remain stable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- The activation guard of the users form (#952). Only the offline upgrade
-- (an operator_upgrade request) turns sign-in rules into users, and it does
-- so once, from a non-root configuration in the older form. It must keep
-- every address that held Administrator in the configuration it replaces
-- as an Administrator user: the Administrator's acceptance criterion, held
-- here as well as in Python. A users-form configuration is never replaced
-- by sign-in rules again. A root (no predecessor) may start in either form.
-- The upgrade's activation also records one audit event.
CREATE FUNCTION public.stewardship_users_form_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    candidate_schema text;
    predecessor_schema text;
    upgrade boolean;
BEGIN
    SELECT validation_schema INTO candidate_schema
    FROM public.stewardship_configuration_version WHERE id = NEW.configuration_id;
    SELECT validation_schema INTO predecessor_schema
    FROM public.stewardship_configuration_version WHERE id = NEW.predecessor_id;
    upgrade := EXISTS (SELECT 1 FROM public.stewardship_config_request
                       WHERE id = NEW.request_id AND authority = 'operator_upgrade');
    IF predecessor_schema = 'users-v10'
       AND candidate_schema IS DISTINCT FROM 'users-v10' THEN
        RAISE EXCEPTION 'Users cannot return to sign-in rules'
            USING ERRCODE = '23514';
    END IF;
    IF upgrade AND (candidate_schema IS DISTINCT FROM 'users-v10'
                    OR predecessor_schema IS NULL
                    OR predecessor_schema = 'users-v10') THEN
        RAISE EXCEPTION 'The users upgrade converts sign-in rules once'
            USING ERRCODE = '23514';
    END IF;
    IF NOT upgrade AND candidate_schema = 'users-v10'
       AND predecessor_schema IS NOT NULL
       AND predecessor_schema <> 'users-v10' THEN
        RAISE EXCEPTION 'Only the offline users upgrade converts sign-in rules'
            USING ERRCODE = '23514';
    END IF;
    IF upgrade AND EXISTS (
        SELECT 1 FROM public.stewardship_address_rule prior_rule
        WHERE prior_rule.configuration_id = NEW.predecessor_id
          AND prior_rule.roles ? 'administrator'
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_address_rule next_rule
              WHERE next_rule.configuration_id = NEW.configuration_id
                AND next_rule.email = prior_rule.email
                AND next_rule.role = 'administrator')
    ) THEN
        RAISE EXCEPTION 'The users upgrade must keep every Administrator'
            USING ERRCODE = '23514';
    END IF;
    -- One audit event for the conversion, in its activation transaction. It
    -- names the request, whose immutable patch is the exact conversion.
    IF upgrade THEN
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NULL, NEW.correlation_id,
            'operator_users_upgraded', NEW.request_id);
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_users_form_v1 BEFORE INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_users_form_v1();

-- Every recorded Google identity (#952): its normalized address, its
-- recorded hosted-domain claim, whether it is disabled, and whether it ever
-- signed in to the Admin portal (an admin_login audit event as its actor).
-- The users upgrade converts a hosted-domain rule's people from this, and
-- its preview lists them. Definer, so the offline admin-recovery login
-- learns one fact per identity without reading the audit log; database-
-- grants grants EXECUTE to that login only.
CREATE FUNCTION public.stewardship_portal_identities_v1()
    RETURNS TABLE(email text, hosted_domain text, disabled boolean, signed_in boolean)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT lower(u.email)::text, lower(u.hosted_domain)::text, u.disabled,
        u.id IN (SELECT e.actor_id FROM public.stewardship_audit_event e
                 WHERE e.event_type='admin_login' AND e.actor_id IS NOT NULL)
    FROM public.stewardship_portal_user u
$$;
ALTER FUNCTION public.stewardship_portal_identities_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_portal_identities_v1() FROM PUBLIC;

-- The distinct names of the ParishSoft Members in the current promoted data
-- whose contact record lists an address (#952, "Name from ParishSoft"), one
-- row per address that has any, best first: a Member of an active Family,
-- then a head of household, then by name, then by Member DUID, so the
-- choice is always the same. Only the addresses the users upgrade names are
-- looked up: the active configuration's address rules (deny rules included)
-- and every recorded Google identity. It takes no address argument, so its
-- callers cannot ask about any other address. Addresses match trimmed and
-- case-insensitively, with no provider folding. A name is "Last, First"
-- (the Member-name rule), or whichever part the Member has. No promoted
-- source means no rows. Definer: it reads Member contact data that its
-- callers otherwise cannot; database-grants grants EXECUTE to the logins
-- that run or preview the users upgrade.
CREATE FUNCTION public.stewardship_member_names_v1()
    RETURNS TABLE(address text, names jsonb)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    WITH wanted AS (
        SELECT lower(btrim(rule.email)) AS address
        FROM public.stewardship_address_rule rule
        JOIN public.stewardship_system_configuration runtime
            ON rule.configuration_id=runtime.active_configuration_id
        UNION
        SELECT lower(btrim(portal.email)) FROM public.stewardship_portal_user portal
    ), listed AS (
        SELECT wanted.address,
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
              AND lower(btrim(listed_email.value->>'value'))=wanted.address)
        WHERE pointer.singleton
          AND parsed.member_doc->'schema_version'='1'::jsonb
          AND parsed.contact_doc->'schema_version'='1'::jsonb
    ), ranked AS (
        SELECT listed.address, listed.name, row_number() OVER (
            PARTITION BY listed.address
            ORDER BY listed.active_family DESC, listed.head DESC, listed.name,
                listed.duid NULLS LAST) AS position
        FROM listed WHERE listed.name <> ''
    ), best AS (
        SELECT ranked.address, ranked.name, min(ranked.position) AS position
        FROM ranked GROUP BY ranked.address, ranked.name
    )
    SELECT best.address, jsonb_agg(best.name ORDER BY best.position)
    FROM best GROUP BY best.address
$$;
ALTER FUNCTION public.stewardship_member_names_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_member_names_v1() FROM PUBLIC;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The users columns and the row shape.
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='stewardship_address_rule'
          AND is_nullable='YES'
          AND ((column_name='name' AND data_type='character varying'
                AND character_maximum_length=200)
            OR (column_name='role' AND data_type='character varying'
                AND character_maximum_length=32)
            OR column_name IN ('creation_origin', 'creation_operation')))<>4
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_address_rule'::regclass
              AND conname='address_rule_shape' AND contype='c'
              AND pg_get_constraintdef(oid) LIKE '%["administrator", "ministry_leader", "staff"]%') THEN
        RAISE EXCEPTION 'Migration 0047: the users columns are not installed as declared';
    END IF;
    -- The document schema, request format, authority and operation index.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_configuration_version'::regclass
              AND conname='configuration_validation_schema'
              AND pg_get_constraintdef(oid) LIKE '%users-v10%')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_config_request'::regclass
              AND conname='config_request_schema'
              AND pg_get_constraintdef(oid) LIKE '%operator-upgrade-users-v10%')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_config_request'::regclass
              AND conname='config_request_actor'
              AND pg_get_constraintdef(oid) LIKE '%operator_upgrade%')
       OR NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            WHERE c.relname='operator_upgrade_operation' AND i.indisunique
              AND i.indrelid='public.stewardship_config_request'::regclass) THEN
        RAISE EXCEPTION 'Migration 0047: the users configuration constraints are not installed as declared';
    END IF;
    -- The eight replaced invoker functions: not SECURITY DEFINER, the fixed
    -- search_path, users-v10 in their schema lists, and the policy changes.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND pg_get_function_identity_arguments(p.oid)=''
          AND p.prosrc LIKE '%''source-cadence-v8'', ''users-v10'')%'
          AND (p.proname IN ('stewardship_campaign_complete_v1',
                             'stewardship_campaign_projection_v1',
                             'stewardship_content_complete_v1',
                             'stewardship_content_projection_v1',
                             'stewardship_ministry_complete_v1',
                             'stewardship_ministry_projection_v1')
            OR (p.proname='stewardship_policy_projection_v1'
                AND p.prosrc LIKE '%jsonb_build_object(''kind'', ''user'', ''email'', NEW.email,%'
                AND p.prosrc LIKE '%IF NEW.email <> lower(btrim(NEW.email)) THEN%')
            OR (p.proname='stewardship_policy_complete_v1'
                AND p.prosrc LIKE '%(role IS NOT NULL) <> (NEW.validation_schema = ''users-v10'')%'
                AND p.prosrc LIKE '%WHERE configuration_id = NEW.id AND role IS NULL LOOP%'))
       )<>8 THEN
        RAISE EXCEPTION 'Migration 0047: a replaced function is not installed as declared';
    END IF;
    -- The activation guard: an invoker with the fixed search_path, and its
    -- BEFORE INSERT trigger on activations.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_users_form_v1'
              AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.prosrc LIKE '%next_rule.role = ''administrator''%'
              AND p.prosrc LIKE '%''operator_users_upgraded'', NEW.request_id%')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_config_activation'::regclass
              AND t.tgname='stewardship_users_form_v1' AND NOT t.tgisinternal
              AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_users_form_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0047: the users activation guard is not installed as declared';
    END IF;
    -- The two definers: SECURITY DEFINER, the fixed search_path, no
    -- EXECUTE for PUBLIC and no arguments; the names lookup is set-returning
    -- and limited to the active rules' and recorded identities' addresses,
    -- and no other overload of either exists.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND pg_get_function_identity_arguments(p.oid)=''
          AND ((p.proname='stewardship_portal_identities_v1'
                AND p.prosrc LIKE '%e.event_type=''admin_login''%')
            OR (p.proname='stewardship_member_names_v1'
                AND p.proretset
                AND pg_get_function_result(p.oid)='TABLE(address text, names jsonb)'
                AND p.prosrc LIKE '%ON rule.configuration_id=runtime.active_configuration_id%'
                AND p.prosrc LIKE '%FROM public.stewardship_portal_user portal%'
                AND p.prosrc LIKE '%lower(btrim(listed_email.value->>''value''))=wanted.address)%'))
       )<>2
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN
               ('stewardship_portal_identities_v1', 'stewardship_member_names_v1'))<>2 THEN
        RAISE EXCEPTION 'Migration 0047: the users definers are not installed as declared';
    END IF;
END
$check$;
