-- Frozen forward migration file 0075 (the repository-wide file sequence):
-- remembered reminder series (#469, slice 2b). It is installed by the Django
-- migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that each replaced function's copy here still equals
-- the fresh-install baseline's. A fresh install runs the baseline and the
-- earlier files and then this; the baseline already carries the replaced
-- bodies and the final constraint definition, so the install ends in the
-- same catalog as an upgraded database.
--
-- A repeat rule turns into ordinary reminders. The configuration document
-- schema campaign-series-v11 is every rule of users-v10 plus an optional
-- schedule_series section that remembers the rule each series' reminders
-- came from, so an edit can later offer "this and following". Delivery
-- never reads a series, and the section has no projection: SQL only has to
-- admit the schema name and treat it as the users form. The users request
-- formats (users-patch-v10, integration-credential-users-v10 and
-- operator-recovery-users-v10) build its candidates, so no request format
-- is added. This file:
--
--   alters the configuration CHECK          admits campaign-series-v11
--   replaces the campaign, content, Ministry and policy projection and
--            completeness functions (eight)  campaign-series-v11 joins
--                                            users-v10 in each schema list
--   replaces stewardship_users_form_v1      campaign-series-v11 is the
--                                            users form: it follows only the
--                                            users form, never returns to
--                                            sign-in rules or to users-v10
--                                            (the schema sticks), and is
--                                            never upgraded
--
-- Each body is its latest definition (the baseline's; 0055's for the
-- migration-owned users-form guard) with only the change described. No
-- table, column, grant or trigger changes, and no function here is SECURITY
-- DEFINER: CREATE OR REPLACE keeps each function's owner and grants.
-- Existing configurations and requests are unchanged. Reversing needs its
-- own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The new document schema.
ALTER TABLE "stewardship_configuration_version" DROP CONSTRAINT "configuration_validation_schema";
ALTER TABLE "stewardship_configuration_version" ADD CONSTRAINT "configuration_validation_schema" CHECK (((validation_schema)::text = ANY ((ARRAY['parish-integrations-v1'::character varying, 'foundation-policy-v2'::character varying, 'campaign-foundation-v3'::character varying, 'bootstrap-policy-v1'::character varying, 'ministry-activity-v4'::character varying, 'campaign-content-v5'::character varying, 'source-cadence-v8'::character varying, 'users-v10'::character varying, 'bootstrap-users-v10'::character varying, 'campaign-series-v11'::character varying])::text[])));

-- campaign-series-v11 holds campaigns and schedules exactly as users-v10 does.
CREATE OR REPLACE FUNCTION public.stewardship_campaign_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.validation_schema NOT IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11') THEN RETURN NULL; END IF;
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

-- campaign-series-v11 holds campaigns and schedules exactly as users-v10 does.
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
      AND v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11');
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

-- campaign-series-v11 holds content exactly as users-v10 does.
CREATE OR REPLACE FUNCTION public.stewardship_content_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE records jsonb; actual jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'content', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11') THEN
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

-- campaign-series-v11 holds content exactly as users-v10 does.
CREATE OR REPLACE FUNCTION public.stewardship_content_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'content') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11')
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

-- campaign-series-v11 holds Ministry activity exactly as users-v10 does.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE records jsonb; actual jsonb; vals jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'ministries', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11') THEN
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

-- campaign-series-v11 holds Ministry activity exactly as users-v10 does.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'ministries') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11')
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

-- campaign-series-v11 holds users exactly as users-v10 does.
CREATE OR REPLACE FUNCTION public.stewardship_policy_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected bigint;
    actual bigint;
    rule public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF NEW.validation_schema NOT IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'bootstrap-users-v10', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11') THEN RETURN NULL; END IF;
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
    -- The users form (#952) and its bootstrap (#993) hold only users, and
    -- only they hold them.
    IF EXISTS (SELECT 1 FROM public.stewardship_address_rule
               WHERE configuration_id = NEW.id
                 AND (role IS NOT NULL)
                     <> (NEW.validation_schema IN ('users-v10', 'campaign-series-v11', 'bootstrap-users-v10')))
       OR (NEW.validation_schema IN ('users-v10', 'campaign-series-v11', 'bootstrap-users-v10') AND (
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

-- campaign-series-v11 holds users exactly as users-v10 does.
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
    WHERE version.id = config AND version.validation_schema IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'bootstrap-users-v10', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11')
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

-- A configuration with remembered reminder series is in the users form:
-- it follows only a users-form configuration, never returns to sign-in
-- rules or to users-v10, and the offline users upgrade never starts
-- from it.
CREATE OR REPLACE FUNCTION public.stewardship_users_form_v1() RETURNS trigger
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
    -- A users bootstrap (#993) is in the users form too: its successor is a
    -- users bootstrap (recovery) or, through setup, users-v10. So is
    -- campaign-series-v11 (#469): users-v10 with remembered reminder series.
    IF predecessor_schema IN ('users-v10', 'campaign-series-v11', 'bootstrap-users-v10')
       AND (candidate_schema IS NULL OR candidate_schema NOT IN ('users-v10', 'campaign-series-v11', 'bootstrap-users-v10')) THEN
        RAISE EXCEPTION 'Users cannot return to sign-in rules'
            USING ERRCODE = '23514';
    END IF;
    -- Remembered reminder series keep their schema once present, even with
    -- no series left, as the configuration schema selection does.
    IF predecessor_schema = 'campaign-series-v11'
       AND candidate_schema IS DISTINCT FROM 'campaign-series-v11' THEN
        RAISE EXCEPTION 'Remembered reminder series keep their schema'
            USING ERRCODE = '23514';
    END IF;
    IF upgrade AND (candidate_schema IS DISTINCT FROM 'users-v10'
                    OR predecessor_schema IS NULL
                    OR predecessor_schema IN ('users-v10', 'campaign-series-v11', 'bootstrap-users-v10')) THEN
        RAISE EXCEPTION 'The users upgrade converts sign-in rules once'
            USING ERRCODE = '23514';
    END IF;
    IF NOT upgrade AND candidate_schema IN ('users-v10', 'campaign-series-v11')
       AND predecessor_schema IS NOT NULL
       AND predecessor_schema NOT IN ('users-v10', 'campaign-series-v11', 'bootstrap-users-v10') THEN
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

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The CHECK admits the new name.
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.stewardship_configuration_version'::regclass
          AND conname='configuration_validation_schema'
          AND strpos(pg_get_constraintdef(oid), '''campaign-series-v11''')>0)<>1 THEN
        RAISE EXCEPTION 'Migration 0075: the configuration CHECK is not installed as declared';
    END IF;
    -- Each replaced trigger function holds its new text, and no other
    -- overload of any exists.
    IF (SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
        JOIN (VALUES
            ('stewardship_campaign_complete_v1',
             '''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'') THEN RETURN NULL;'),
            ('stewardship_campaign_projection_v1',
             '''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'');'),
            ('stewardship_content_complete_v1',
             'IF NEW.validation_schema NOT IN (''campaign-content-v5'', ''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'') THEN'),
            ('stewardship_content_projection_v1',
             'v.validation_schema IN (''campaign-content-v5'', ''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'')'),
            ('stewardship_ministry_complete_v1',
             'IF NEW.validation_schema NOT IN (''ministry-activity-v4'', ''campaign-content-v5'', ''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'') THEN'),
            ('stewardship_ministry_projection_v1',
             'v.validation_schema IN (''ministry-activity-v4'', ''campaign-content-v5'', ''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'')'),
            ('stewardship_policy_complete_v1',
             '<> (NEW.validation_schema IN (''users-v10'', ''campaign-series-v11'', ''bootstrap-users-v10'')))'),
            ('stewardship_policy_projection_v1',
             '''source-cadence-v8'', ''users-v10'', ''campaign-series-v11'')'),
            ('stewardship_users_form_v1',
             'RAISE EXCEPTION ''Remembered reminder series keep their schema''')
        ) expected(name, marker)
          ON p.proname=expected.name
         AND pg_get_function_identity_arguments(p.oid)=''
         AND strpos(p.prosrc, expected.marker)>0)<>9
       OR (SELECT count(*) FROM pg_proc p
           JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
           WHERE p.proname IN (
                'stewardship_campaign_complete_v1',
                'stewardship_campaign_projection_v1',
                'stewardship_content_complete_v1',
                'stewardship_content_projection_v1',
                'stewardship_ministry_complete_v1',
                'stewardship_ministry_projection_v1',
                'stewardship_policy_complete_v1',
                'stewardship_policy_projection_v1',
                'stewardship_users_form_v1'))<>9 THEN
        RAISE EXCEPTION 'Migration 0075: a replaced function is not installed as declared';
    END IF;
    -- None of them is SECURITY DEFINER: each runs as its caller.
    IF EXISTS (SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace AND n.nspname='public'
        WHERE p.prosecdef AND p.proname IN (
                'stewardship_campaign_complete_v1',
                'stewardship_campaign_projection_v1',
                'stewardship_content_complete_v1',
                'stewardship_content_projection_v1',
                'stewardship_ministry_complete_v1',
                'stewardship_ministry_projection_v1',
                'stewardship_policy_complete_v1',
                'stewardship_policy_projection_v1',
                'stewardship_users_form_v1')) THEN
        RAISE EXCEPTION 'Migration 0075: a replaced function gained SECURITY DEFINER';
    END IF;
END
$check$;
