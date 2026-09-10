"""Population manifests, prepared generation state, close cleanup and session fence."""

# ruff: noqa: E501
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0022_campaigncredentialstate_population_dirty")
    ]
    operations = [
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_population_insert_dirty_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    UPDATE stewardship_campaign_credentials SET population_dirty=true,version=version+1
    WHERE campaign_id IN(SELECT DISTINCT campaign_id FROM new_families);
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_population_insert_dirty_v1 AFTER INSERT ON stewardship_family_campaign
REFERENCING NEW TABLE AS new_families FOR EACH STATEMENT EXECUTE FUNCTION stewardship_population_insert_dirty_v1();
CREATE FUNCTION stewardship_population_update_dirty_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    UPDATE stewardship_campaign_credentials SET population_dirty=true,version=version+1
    WHERE campaign_id IN(
        SELECT n.campaign_id FROM new_families n JOIN old_families o ON o.id=n.id
        WHERE (n.portal_eligible,n.source_generation) IS DISTINCT FROM (o.portal_eligible,o.source_generation));
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_population_update_dirty_v1 AFTER UPDATE ON stewardship_family_campaign
REFERENCING NEW TABLE AS new_families OLD TABLE AS old_families FOR EACH STATEMENT EXECUTE FUNCTION stewardship_population_update_dirty_v1();

CREATE FUNCTION stewardship_population_manifest_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE expected_digest text; expected_count bigint;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NOT NEW.population_dirty THEN RAISE EXCEPTION 'Population starts unverified' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NOT NEW.population_dirty AND (OLD.population_dirty OR
       (NEW.source_snapshot_id,NEW.source_generation,NEW.eligibility_digest,NEW.eligible_count)
       IS DISTINCT FROM (OLD.source_snapshot_id,OLD.source_generation,OLD.eligibility_digest,OLD.eligible_count)) THEN
        SELECT count(*),encode(sha256(convert_to('family-token-coverage-v1','UTF8')||decode('00','hex')||
            coalesce(string_agg(uuid_send(id),''::bytea ORDER BY family_duid),''::bytea)),'hex')
        INTO expected_count,expected_digest FROM stewardship_family_campaign WHERE campaign_id=NEW.campaign_id AND portal_eligible;
        IF NEW.source_snapshot_id IS NULL OR NEW.source_generation IS NULL OR NEW.source_generation<1
           OR NEW.eligible_count<>expected_count OR NEW.eligibility_digest<>expected_digest
           OR EXISTS(SELECT 1 FROM stewardship_family_campaign WHERE campaign_id=NEW.campaign_id AND source_generation<>NEW.source_generation) THEN
            RAISE EXCEPTION 'Population manifest must match the complete committed generation' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_population_manifest_v1 BEFORE INSERT OR UPDATE ON stewardship_campaign_credentials
FOR EACH ROW EXECUTE FUNCTION stewardship_population_manifest_v1();

CREATE FUNCTION stewardship_token_generation_state_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE population stewardship_campaign_credentials%ROWTYPE; current_epoch uuid; actual_count bigint;
BEGIN
    SELECT family_link_epoch INTO current_epoch FROM stewardship_credential_deployment;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'building' OR NEW.credential_epoch IS DISTINCT FROM current_epoch THEN
            RAISE EXCEPTION 'A token generation starts inactive in the current epoch' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF (OLD.state IN ('cancelled','superseded') AND NEW.state<>OLD.state)
       OR (OLD.state='active' AND NEW.state NOT IN ('active','superseded'))
       OR (OLD.state='ready' AND NEW.state NOT IN ('ready','active','cancelled','superseded'))
       OR (OLD.completed_at IS NOT NULL AND (NEW.completed_at,NEW.coverage_digest,NEW.coverage_count)
           IS DISTINCT FROM (OLD.completed_at,OLD.coverage_digest,OLD.coverage_count)) THEN
        RAISE EXCEPTION 'Token generation cannot revive or rewrite a ready manifest' USING ERRCODE='23514'; END IF;
    IF NEW.state='ready' AND OLD.state<>'ready' THEN
        SELECT * INTO population FROM stewardship_campaign_credentials WHERE campaign_id=NEW.campaign_id FOR UPDATE;
        SELECT count(*) INTO actual_count FROM stewardship_family_token WHERE generation_id=NEW.id AND destroyed_at IS NULL;
        IF NEW.credential_epoch IS DISTINCT FROM current_epoch OR population.population_dirty
           OR NEW.source_snapshot_id IS DISTINCT FROM population.source_snapshot_id OR NEW.source_generation IS DISTINCT FROM population.source_generation
           OR NEW.coverage_digest IS DISTINCT FROM population.eligibility_digest OR NEW.coverage_count IS DISTINCT FROM population.eligible_count
           OR actual_count<>population.eligible_count OR EXISTS(
               SELECT 1 FROM stewardship_family_token t JOIN stewardship_family_campaign f ON f.id=t.family_id
               WHERE t.generation_id=NEW.id AND (NOT f.portal_eligible OR t.destroyed_at IS NOT NULL)) THEN
            RAISE EXCEPTION 'Token readiness requires complete current coverage' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='active' AND NOT EXISTS(SELECT 1 FROM stewardship_campaign WHERE id=NEW.campaign_id AND active_token_generation_id=NEW.id) THEN
        RAISE EXCEPTION 'Only the campaign pointer activates a token generation' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_token_generation_state_v1 BEFORE INSERT OR UPDATE ON stewardship_family_token_generation
FOR EACH ROW EXECUTE FUNCTION stewardship_token_generation_state_v1();

CREATE FUNCTION stewardship_token_campaign_effects_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF NEW.active_token_generation_id IS DISTINCT FROM OLD.active_token_generation_id AND NEW.active_token_generation_id IS NOT NULL THEN
        UPDATE stewardship_family_token_generation SET state='active',version=version+1
        WHERE id=NEW.active_token_generation_id AND state='ready';
    END IF;
    IF (NEW.state='closed' AND OLD.state<>'closed') OR (NEW.state='draft' AND OLD.state='scheduled') THEN
        UPDATE stewardship_family_token SET ciphertext=NULL,digest=NULL,destroyed_at=stewardship_campaign_now_v1(),version=version+1
        WHERE campaign_id=NEW.id AND destroyed_at IS NULL;
        UPDATE stewardship_family_token_generation SET state='superseded',version=version+1
        WHERE campaign_id=NEW.id AND state NOT IN ('superseded','cancelled');
        UPDATE stewardship_family_session SET revoked_at=greatest(stewardship_campaign_now_v1(),last_activity_at),version=version+1
        WHERE family_id IN(SELECT id FROM stewardship_family_campaign WHERE campaign_id=NEW.id) AND revoked_at IS NULL;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_token_campaign_effects_v1 AFTER UPDATE ON stewardship_campaign
FOR EACH ROW EXECUTE FUNCTION stewardship_token_campaign_effects_v1();

CREATE FUNCTION stewardship_family_session_epoch_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.credential_epoch IS DISTINCT FROM OLD.credential_epoch THEN
            RAISE EXCEPTION 'Family session epoch is immutable' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(SELECT 1 FROM stewardship_credential_deployment WHERE family_link_epoch=NEW.credential_epoch) THEN
        RAISE EXCEPTION 'Family session requires the current deployment epoch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_family_session_epoch_v1 BEFORE INSERT OR UPDATE ON stewardship_family_session
FOR EACH ROW EXECUTE FUNCTION stewardship_family_session_epoch_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_family_session_epoch_v1 ON stewardship_family_session;
DROP FUNCTION stewardship_family_session_epoch_v1();
DROP TRIGGER stewardship_token_campaign_effects_v1 ON stewardship_campaign;
DROP FUNCTION stewardship_token_campaign_effects_v1();
DROP TRIGGER stewardship_token_generation_state_v1 ON stewardship_family_token_generation;
DROP FUNCTION stewardship_token_generation_state_v1();
DROP TRIGGER stewardship_population_manifest_v1 ON stewardship_campaign_credentials;
DROP FUNCTION stewardship_population_manifest_v1();
DROP TRIGGER stewardship_population_update_dirty_v1 ON stewardship_family_campaign;
DROP FUNCTION stewardship_population_update_dirty_v1();
DROP TRIGGER stewardship_population_insert_dirty_v1 ON stewardship_family_campaign;
DROP FUNCTION stewardship_population_insert_dirty_v1();
""",
        )
    ]
