"""Bind immutable policy projections to YAML and guard mutable identity metadata."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0016_policy_foundation")]

    operations = [
        immutable_guard_v1("stewardship_domain_rule"),
        immutable_guard_v1("stewardship_address_rule"),
        immutable_guard_v1("stewardship_address_grant"),
        immutable_guard_v1("stewardship_ministry_assignment"),
        mutable_guard_v1("stewardship_portal_user", frozen_fields=("google_subject",)),
        mutable_guard_v1(
            "stewardship_assignment_overlay", frozen_fields=("assignment_record_id",)
        ),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_policy_projection_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
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
    WHERE version.id = config AND version.validation_schema = 'foundation-policy-v2'
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
CREATE TRIGGER stewardship_policy_projection_v1
BEFORE INSERT ON public.stewardship_domain_rule
FOR EACH ROW EXECUTE FUNCTION stewardship_policy_projection_v1();
CREATE TRIGGER stewardship_policy_projection_v1
BEFORE INSERT ON public.stewardship_address_rule
FOR EACH ROW EXECUTE FUNCTION stewardship_policy_projection_v1();
CREATE TRIGGER stewardship_policy_projection_v1
BEFORE INSERT ON public.stewardship_address_grant
FOR EACH ROW EXECUTE FUNCTION stewardship_policy_projection_v1();
CREATE TRIGGER stewardship_policy_projection_v1
BEFORE INSERT ON public.stewardship_ministry_assignment
FOR EACH ROW EXECUTE FUNCTION stewardship_policy_projection_v1();

CREATE FUNCTION stewardship_policy_complete_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    expected bigint;
    actual bigint;
    rule public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF NEW.validation_schema <> 'foundation-policy-v2' THEN RETURN NULL; END IF;
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
    FOR rule IN SELECT * FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id LOOP
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
CREATE CONSTRAINT TRIGGER stewardship_policy_complete_v1
AFTER INSERT ON public.stewardship_configuration_version DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_policy_complete_v1();
""",
            reverse_sql="""
LOCK TABLE public.stewardship_configuration_version, public.stewardship_config_request,
    public.stewardship_portal_user, public.stewardship_assignment_overlay
    IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_configuration_version
               WHERE validation_schema = 'foundation-policy-v2')
       OR EXISTS (SELECT 1 FROM public.stewardship_config_request
                  WHERE request_schema = 'foundation-policy-patch-v2')
       OR EXISTS (SELECT 1 FROM public.stewardship_portal_user)
       OR EXISTS (SELECT 1 FROM public.stewardship_assignment_overlay) THEN
        RAISE EXCEPTION 'Policy history prevents this schema downgrade'
            USING ERRCODE = '23514';
    END IF;
END $$;
DROP TRIGGER stewardship_policy_complete_v1 ON public.stewardship_configuration_version;
DROP FUNCTION stewardship_policy_complete_v1();
DROP TRIGGER stewardship_policy_projection_v1 ON public.stewardship_domain_rule;
DROP TRIGGER stewardship_policy_projection_v1 ON public.stewardship_address_rule;
DROP TRIGGER stewardship_policy_projection_v1 ON public.stewardship_address_grant;
DROP TRIGGER stewardship_policy_projection_v1 ON public.stewardship_ministry_assignment;
DROP FUNCTION stewardship_policy_projection_v1();
""",
        ),
    ]
