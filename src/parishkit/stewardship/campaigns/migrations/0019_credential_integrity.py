"""Frozen credential/cohort identities, linked scopes and safe eligibility history."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


class Migration(migrations.Migration):
    dependencies = [
        (
            "stewardship_campaigns",
            "0018_credentialkeystate_deploymentcredentialstate_and_more",
        )
    ]
    operations = [
        mutable_guard_v1("stewardship_credential_deployment"),
        mutable_guard_v1("stewardship_credential_key_state", frozen_fields=("kind",)),
        mutable_guard_v1(
            "stewardship_family_campaign",
            frozen_fields=("campaign_id", "family_duid"),
            write_once_fields=(
                "first_eligible_at",
                "first_eligible_source_generation",
                "first_live_submission_id",
            ),
        ),
        mutable_guard_v1(
            "stewardship_family_token_generation",
            frozen_fields=(
                "campaign_id",
                "operation_id",
                "credential_epoch",
                "restore_id",
                "preparation_revision",
                "source_snapshot_id",
                "source_generation",
                "configuration_id",
                "key_id",
                "key_inventory_digest",
            ),
        ),
        mutable_guard_v1(
            "stewardship_family_token",
            frozen_fields=("family_id", "campaign_id", "generation_id"),
            write_once_fields=("destroyed_at",),
        ),
        mutable_guard_v1(
            "stewardship_rehearsal_epoch",
            frozen_fields=("campaign_id",),
            write_once_fields=("invalidated_at",),
        ),
        mutable_guard_v1(
            "stewardship_campaign_credentials", frozen_fields=("campaign_id",)
        ),
        mutable_guard_v1(
            "stewardship_rehearsal_credential", frozen_fields=("epoch_id", "family_id")
        ),
        mutable_guard_v1(
            "stewardship_family_session",
            frozen_fields=(
                "session_id",
                "family_id",
                "mode",
                "rehearsal_epoch_id",
                "authenticated_at",
                "expires_at",
            ),
            write_once_fields=("revoked_at",),
        ),
        immutable_guard_v1("stewardship_family_eligibility"),
        immutable_guard_v1("stewardship_family_code_mac"),
        immutable_guard_v1("stewardship_rehearsal_reservation"),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_family_cohort_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF TG_OP='UPDATE' AND (NEW.source_generation<OLD.source_generation
       OR NEW.eligibility_changed_at<OLD.eligibility_changed_at
       OR (OLD.code_ciphertext IS NOT NULL AND NEW.code_ciphertext IS NULL)) THEN
        RAISE EXCEPTION 'Family identity cannot rewind source/cohort/code state' USING ERRCODE='23514';
    END IF;
    IF NEW.first_eligible_at IS NOT NULL AND (NEW.first_eligible_at>NEW.eligibility_changed_at
       OR NEW.first_eligible_source_generation>NEW.source_generation) THEN
        RAISE EXCEPTION 'Invalid Family cohort provenance' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_family_cohort_v1 BEFORE INSERT OR UPDATE
ON stewardship_family_campaign FOR EACH ROW EXECUTE FUNCTION stewardship_family_cohort_v1();

CREATE FUNCTION stewardship_family_history_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF NEW.status_reason='population_pending' THEN RETURN NEW; END IF;
    IF TG_OP='INSERT' OR (NEW.active,NEW.portal_eligible,NEW.email_eligible,NEW.email_deliverable,NEW.status_reason,NEW.deliverability_reason)
       IS DISTINCT FROM (OLD.active,OLD.portal_eligible,OLD.email_eligible,OLD.email_deliverable,OLD.status_reason,OLD.deliverability_reason) THEN
        INSERT INTO stewardship_family_eligibility(id,created_at,actor_id,correlation_id,family_id,family_version,source_generation,
            active,portal_eligible,email_eligible,email_deliverable,status_reason,deliverability_reason)
        VALUES(gen_random_uuid(),statement_timestamp(),NEW.actor_id,NEW.correlation_id,NEW.id,NEW.version,NEW.source_generation,
            NEW.active,NEW.portal_eligible,NEW.email_eligible,NEW.email_deliverable,NEW.status_reason,NEW.deliverability_reason);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_family_history_v1 AFTER INSERT OR UPDATE
ON stewardship_family_campaign FOR EACH ROW EXECUTE FUNCTION stewardship_family_history_v1();

CREATE FUNCTION stewardship_credential_scope_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE family_campaign uuid; epoch_campaign uuid; generation_campaign uuid;
BEGIN
    IF TG_TABLE_NAME='stewardship_family_code_mac' THEN
        IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'MAC fingerprints are immutable' USING ERRCODE='23514'; END IF;
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        IF NEW.campaign_id IS DISTINCT FROM family_campaign THEN
            RAISE EXCEPTION 'Fingerprint campaign must match Family' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_family_token' THEN
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        SELECT campaign_id INTO generation_campaign FROM stewardship_family_token_generation WHERE id=NEW.generation_id;
        IF NEW.campaign_id IS DISTINCT FROM family_campaign OR NEW.campaign_id IS DISTINCT FROM generation_campaign THEN
            RAISE EXCEPTION 'Token campaign scopes must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_rehearsal_credential' THEN
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        SELECT campaign_id INTO epoch_campaign FROM stewardship_rehearsal_epoch WHERE id=NEW.epoch_id;
        IF family_campaign IS DISTINCT FROM epoch_campaign THEN
            RAISE EXCEPTION 'Rehearsal Family/campaign scopes must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_rehearsal_code_mac' THEN
        IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'MAC fingerprints are immutable' USING ERRCODE='23514'; END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_rehearsal_credential WHERE id=NEW.credential_id AND epoch_id=NEW.epoch_id) THEN
            RAISE EXCEPTION 'Rehearsal fingerprint scopes must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_family_session' THEN
        IF NEW.mode<>'testing' THEN RETURN NEW; END IF;
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        SELECT campaign_id INTO epoch_campaign FROM stewardship_rehearsal_epoch WHERE id=NEW.rehearsal_epoch_id;
        IF family_campaign IS DISTINCT FROM epoch_campaign THEN
            RAISE EXCEPTION 'Family session epoch scope must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_campaign_credentials' THEN
        IF NEW.rehearsal_epoch_id IS NULL THEN RETURN NEW; END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch WHERE id=NEW.rehearsal_epoch_id AND campaign_id=NEW.campaign_id AND state='active') THEN
            RAISE EXCEPTION 'Current rehearsal epoch must be active in this campaign' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON stewardship_family_code_mac
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_scope_v1();
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON stewardship_family_token
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_scope_v1();
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON stewardship_rehearsal_credential
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_scope_v1();
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON stewardship_rehearsal_code_mac
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_scope_v1();
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON stewardship_family_session
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_scope_v1();
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON stewardship_campaign_credentials
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_scope_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_credential_scope_v1 ON stewardship_campaign_credentials;
DROP TRIGGER stewardship_credential_scope_v1 ON stewardship_family_session;
DROP TRIGGER stewardship_credential_scope_v1 ON stewardship_rehearsal_code_mac;
DROP TRIGGER stewardship_credential_scope_v1 ON stewardship_rehearsal_credential;
DROP TRIGGER stewardship_credential_scope_v1 ON stewardship_family_token;
DROP TRIGGER stewardship_credential_scope_v1 ON stewardship_family_code_mac;
DROP FUNCTION stewardship_credential_scope_v1();
DROP TRIGGER stewardship_family_history_v1 ON stewardship_family_campaign;
DROP FUNCTION stewardship_family_history_v1();
DROP TRIGGER stewardship_family_cohort_v1 ON stewardship_family_campaign;
DROP FUNCTION stewardship_family_cohort_v1();
""",
        ),
    ]
