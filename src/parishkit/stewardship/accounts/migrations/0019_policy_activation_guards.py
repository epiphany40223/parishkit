"""Commit detective controls with policy activation, never with provider delivery."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0018_policy_activation_evidence"),
        ("stewardship_audit", "0006_parish_ownership"),
    ]
    operations = [
        immutable_guard_v1("stewardship_policy_security_event"),
        immutable_guard_v1("stewardship_policy_epoch"),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_policy_activation_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    entry record;
    prior jsonb;
    recipients jsonb;
    alert_kind text;
    expanded boolean := false;
    event_id uuid;
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
CREATE TRIGGER stewardship_policy_activation_v1
AFTER INSERT ON public.stewardship_config_activation
FOR EACH ROW EXECUTE FUNCTION stewardship_policy_activation_v1();
""",
            reverse_sql="""
LOCK TABLE public.stewardship_config_activation IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_policy_security_event)
       OR EXISTS (SELECT 1 FROM public.stewardship_policy_epoch) THEN
        RAISE EXCEPTION 'Policy security history prevents downgrade'
            USING ERRCODE = '23514';
    END IF;
END $$;
DROP TRIGGER stewardship_policy_activation_v1 ON public.stewardship_config_activation;
DROP FUNCTION stewardship_policy_activation_v1();
""",
        ),
    ]
