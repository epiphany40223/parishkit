"""Config-installer proof for initial finalization without private target reads."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

# ruff: noqa: E501 -- frozen SQL keeps related predicates together.
FORWARD = """
CREATE FUNCTION public.stewardship_setup_prepared_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_setup_config_intent%ROWTYPE;
    request public.stewardship_config_request%ROWTYPE;
    candidate public.stewardship_configuration_version%ROWTYPE;
    readiness public.stewardship_setup_readiness_binding%ROWTYPE;
    integration jsonb; bound public.stewardship_setup_credential_install%ROWTYPE;
    installed public.stewardship_secret_request%ROWTYPE;
    target_count integer=0;
BEGIN
    IF current_user<>'pk_stewardship_config_installer'
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736212 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT public.stewardship_setup_install_ready_live_v1(NEW.readiness_id) THEN
        RAISE EXCEPTION 'Prepared setup requires its live configuration installer'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO readiness FROM public.stewardship_setup_readiness_binding WHERE id=NEW.readiness_id;
    SELECT * INTO intent FROM public.stewardship_setup_config_intent WHERE id=readiness.intent_id;
    SELECT * INTO request FROM public.stewardship_config_request WHERE id=intent.request_id;
    SELECT * INTO candidate FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF request.request_schema IS DISTINCT FROM 'initial-setup-patch-v7'
        OR NEW.actor_id IS DISTINCT FROM request.actor_id
        OR candidate.id IS DISTINCT FROM request.candidate_version_id
        OR candidate.digest IS DISTINCT FROM request.candidate_digest
        OR candidate.predecessor_id IS DISTINCT FROM request.base_id
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=request.id ORDER BY sequence DESC LIMIT 1) IS DISTINCT FROM 'yaml_activated'
        THEN
        RAISE EXCEPTION 'Prepared setup requires its exact selected candidate' USING ERRCODE='23514';
    END IF;
    FOR integration IN SELECT value->'values' FROM jsonb_array_elements(
        candidate.canonical_document->'sections'->'integrations')
        WHERE value->'values'->>'kind' IN ('parishsoft','google_workspace','slack')
    LOOP
        target_count=target_count+1;
        SELECT * INTO bound FROM public.stewardship_setup_credential_install
            WHERE readiness_id=NEW.readiness_id AND target=integration->>'kind';
        SELECT * INTO installed FROM public.stewardship_secret_request WHERE id=bound.request_id;
        IF bound.id IS NULL OR installed.id IS NULL
            OR bound.fingerprint IS DISTINCT FROM integration->>'credential_fingerprint'
            OR installed.resulting_fingerprint IS DISTINCT FROM bound.fingerprint
            OR installed.state IS DISTINCT FROM 'awaiting_ack'
            OR installed.expires_at<=clock_timestamp()
            OR installed.required_consumers IS DISTINCT FROM
                public.stewardship_credential_consumers_v1(bound.target)
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(installed.required_consumers) required(consumer_name)
                WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_credential_consumer_ack ack
                    WHERE ack.request_id=installed.id AND ack.consumer=required.consumer_name
                        AND ack.fingerprint=bound.fingerprint)) THEN
            RAISE EXCEPTION 'Prepared setup requires all exact initial consumer acknowledgements'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF target_count<>(CASE WHEN readiness.slack_delivery_id IS NULL THEN 2 ELSE 3 END)
        OR target_count<>(SELECT count(*) FROM public.stewardship_setup_credential_install
            WHERE readiness_id=NEW.readiness_id) THEN
        RAISE EXCEPTION 'Prepared setup has an incomplete credential set' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_prepared_admission
BEFORE INSERT ON public.stewardship_setup_prepared
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_prepared_guard_v1();

CREATE FUNCTION public.stewardship_setup_initial_hold_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.target NOT IN ('parishsoft','google_workspace','slack')
        OR NEW.state<>'cleanup_pending' OR NEW.cleanup_reason='applied' THEN RETURN NEW; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install WHERE request_id=NEW.id)
        THEN RETURN NEW; END IF;
    IF (OLD.state='awaiting_ack' OR NEW.cleanup_reason='cancelled')
        AND public.stewardship_setup_install_live_v1(NEW.id) THEN
        RAISE EXCEPTION 'Initial rollback requires original setup cancellation or expiry'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER aac_stewardship_setup_initial_hold
BEFORE UPDATE ON public.stewardship_secret_request
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_initial_hold_v1();
"""

REVERSE = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_prepared) THEN
        RAISE EXCEPTION 'Prepared setup history prevents downgrade' USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER aac_stewardship_setup_initial_hold ON public.stewardship_secret_request;
DROP FUNCTION public.stewardship_setup_initial_hold_v1();
DROP TRIGGER stewardship_setup_prepared_admission ON public.stewardship_setup_prepared;
DROP FUNCTION public.stewardship_setup_prepared_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0086_setup_prepared_receipt")]
    operations = [
        immutable_guard_v1("stewardship_setup_prepared"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
