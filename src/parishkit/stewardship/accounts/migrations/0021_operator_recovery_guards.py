"""Atomically revoke administration sessions and attribute offline recovery."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0020_operator_recovery_records")]
    operations = [
        immutable_guard_v1("stewardship_admin_revocation"),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_recovery_request_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    operation jsonb;
    target text;
BEGIN
    IF NEW.authority <> 'operator_recovery' THEN RETURN NEW; END IF;
    operation := NEW.patch -> 0;
    IF operation ->> 'operation' = 'add' THEN
        target := operation -> 'values' ->> 'email';
    ELSE
        SELECT record -> 'values' ->> 'email' INTO target
        FROM public.stewardship_configuration_version version,
             jsonb_array_elements(version.canonical_document -> 'sections'
                 -> 'login_rules') record
        WHERE version.id = NEW.base_id
          AND record ->> 'id' = operation ->> 'id';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration
                   WHERE id = NEW.confirmed_deployment_id)
       OR NEW.operator_name = '' OR NEW.operator_reason = ''
       OR jsonb_array_length(NEW.patch) <> 1
       OR operation ->> 'section' IS DISTINCT FROM 'login_rules'
       OR operation ->> 'operation' NOT IN ('add', 'update')
       OR target IS DISTINCT FROM NEW.recovery_target
       OR (operation ->> 'operation' = 'add' AND operation -> 'values'
           ->> 'creation_operation' IS DISTINCT FROM NEW.request_key::text)
       OR operation -> 'values' -> 'grants' -> 'administrator'
          IS DISTINCT FROM jsonb_build_object('manual', NEW.request_key::text) THEN
        RAISE EXCEPTION 'Invalid offline recovery attribution'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_recovery_request_v1
BEFORE INSERT ON public.stewardship_config_request
FOR EACH ROW EXECUTE FUNCTION stewardship_recovery_request_v1();

CREATE FUNCTION stewardship_recovery_activation_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    intent public.stewardship_config_request%ROWTYPE;
BEGIN
    SELECT * INTO intent FROM public.stewardship_config_request
    WHERE id = NEW.request_id AND authority = 'operator_recovery';
    IF NOT FOUND THEN RETURN NEW; END IF;
    INSERT INTO public.stewardship_admin_revocation
        (id, created_at, actor_id, correlation_id, activation_id)
    VALUES (gen_random_uuid(), NEW.created_at, NULL, NEW.correlation_id, NEW.id);
    UPDATE public.stewardship_portal_session
    SET revoked_at = GREATEST(statement_timestamp(), last_activity_at),
        version = version + 1,
        actor_id = NULL, correlation_id = NEW.correlation_id
    WHERE revoked_at IS NULL;
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id)
    VALUES (gen_random_uuid(), NULL, NEW.correlation_id,
        'operator_admin_recovered', intent.id);
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_recovery_activation_v1
AFTER INSERT ON public.stewardship_config_activation
FOR EACH ROW EXECUTE FUNCTION stewardship_recovery_activation_v1();

CREATE FUNCTION stewardship_recovery_recipient_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    target text;
BEGIN
    SELECT intent.recovery_target INTO target
    FROM public.stewardship_config_activation activation
    JOIN public.stewardship_config_request intent ON intent.id = activation.request_id
    WHERE activation.id = NEW.activation_id AND intent.authority = 'operator_recovery';
    IF FOUND AND NOT NEW.recipients ? target THEN
        SELECT jsonb_agg(value ORDER BY value) INTO NEW.recipients
        FROM jsonb_array_elements_text(NEW.recipients || to_jsonb(target));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_recovery_recipient_v1
BEFORE INSERT ON public.stewardship_policy_security_event
FOR EACH ROW EXECUTE FUNCTION stewardship_recovery_recipient_v1();
""",
            reverse_sql="""
LOCK TABLE public.stewardship_config_request, public.stewardship_config_activation
    IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_config_request
               WHERE authority = 'operator_recovery')
       OR EXISTS (SELECT 1 FROM public.stewardship_admin_revocation) THEN
        RAISE EXCEPTION 'Recovery history prevents downgrade' USING ERRCODE = '23514';
    END IF;
END $$;
DROP TRIGGER stewardship_recovery_recipient_v1
    ON public.stewardship_policy_security_event;
DROP FUNCTION stewardship_recovery_recipient_v1();
DROP TRIGGER stewardship_recovery_activation_v1
    ON public.stewardship_config_activation;
DROP FUNCTION stewardship_recovery_activation_v1();
DROP TRIGGER stewardship_recovery_request_v1 ON public.stewardship_config_request;
DROP FUNCTION stewardship_recovery_request_v1();
""",
        ),
    ]
