"""Final token selection verifies the frozen source/configuration/key manifest."""

# ruff: noqa: E501
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "stewardship_campaigns",
            "0025_familyaccesstokengeneration_configuration_request",
        )
    ]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_token_request_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF NEW.configuration_request_id IS DISTINCT FROM OLD.configuration_request_id THEN
        RAISE EXCEPTION 'Token proposed configuration is immutable' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_token_request_binding_v1 BEFORE UPDATE ON stewardship_family_token_generation
FOR EACH ROW EXECUTE FUNCTION stewardship_token_request_binding_v1();

CREATE FUNCTION stewardship_token_activation_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE generation stewardship_family_token_generation%ROWTYPE;
    population stewardship_campaign_credentials%ROWTYPE;
    deployment stewardship_credential_deployment%ROWTYPE;
    current_key_digest text;
BEGIN
    IF NEW.active_token_generation_id IS NULL OR NEW.active_token_generation_id IS NOT DISTINCT FROM OLD.active_token_generation_id THEN RETURN NEW; END IF;
    IF NOT pg_try_advisory_xact_lock_shared(736226,1) THEN
        RAISE EXCEPTION 'Token key rotation is busy; retry' USING ERRCODE='23514'; END IF;
    SELECT * INTO deployment FROM stewardship_credential_deployment FOR SHARE;
    SELECT * INTO generation FROM stewardship_family_token_generation WHERE id=NEW.active_token_generation_id FOR UPDATE;
    SELECT * INTO population FROM stewardship_campaign_credentials WHERE campaign_id=NEW.id FOR UPDATE;
    SELECT inventory_digest INTO current_key_digest FROM stewardship_credential_key_state WHERE kind='token_public';
    IF generation.id IS NULL OR population.id IS NULL OR deployment.id IS NULL
       OR generation.campaign_id<>NEW.id OR generation.state<>'ready'
       OR generation.credential_epoch IS DISTINCT FROM deployment.family_link_epoch
       OR generation.key_inventory_digest IS DISTINCT FROM current_key_digest
       OR population.population_dirty OR population.rehearsal_epoch_id IS NOT NULL
       OR generation.coverage_digest IS DISTINCT FROM population.eligibility_digest
       OR generation.coverage_count IS DISTINCT FROM population.eligible_count
       OR generation.source_snapshot_id IS DISTINCT FROM population.source_snapshot_id
       OR generation.source_generation IS DISTINCT FROM population.source_generation
       OR EXISTS(SELECT 1 FROM stewardship_rehearsal_credential c
                 JOIN stewardship_rehearsal_epoch e ON e.id=c.epoch_id WHERE e.campaign_id=NEW.id) THEN
        RAISE EXCEPTION 'Campaign requires a complete current token generation and rehearsal cleanup' USING ERRCODE='23514'; END IF;
    IF generation.configuration_request_id IS NULL THEN
        IF generation.configuration_id IS DISTINCT FROM NEW.active_configuration_id THEN
            RAISE EXCEPTION 'Prepared token configuration changed' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(
        SELECT 1 FROM stewardship_campaign_config_intent i
        JOIN stewardship_config_request r ON r.id=i.request_id
        JOIN stewardship_campaign_configuration p ON p.configuration_id=r.candidate_version_id AND p.record_id=NEW.id
        WHERE i.request_id=generation.configuration_request_id AND i.campaign_id=NEW.id
          AND i.action='reopen' AND i.prior_projection_id=generation.configuration_id
          AND i.token_generation_id=generation.id AND p.id=NEW.active_configuration_id
    ) THEN
        RAISE EXCEPTION 'Prepared tokens do not match the reviewed reopen candidate' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_token_activation_v1 BEFORE UPDATE ON stewardship_campaign
FOR EACH ROW EXECUTE FUNCTION stewardship_token_activation_v1();

CREATE FUNCTION stewardship_token_gate_release_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF NEW.active_token_generation_id IS NOT NULL AND NEW.active_token_generation_id IS DISTINCT FROM OLD.active_token_generation_id THEN
        UPDATE stewardship_campaign_credentials SET go_live_gate=false,version=version+1
        WHERE campaign_id=NEW.id AND go_live_gate;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_token_gate_release_v1 AFTER UPDATE ON stewardship_campaign
FOR EACH ROW EXECUTE FUNCTION stewardship_token_gate_release_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_token_gate_release_v1 ON stewardship_campaign;
DROP FUNCTION stewardship_token_gate_release_v1();
DROP TRIGGER stewardship_token_activation_v1 ON stewardship_campaign;
DROP FUNCTION stewardship_token_activation_v1();
DROP TRIGGER stewardship_token_request_binding_v1 ON stewardship_family_token_generation;
DROP FUNCTION stewardship_token_request_binding_v1();
""",
        )
    ]
