"""Target-bound staging, installer state transitions and consumer acknowledgements."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)

FROZEN_FIELDS = (
    "target",
    "staging_reference",
    "requested_by_id",
    "reauthenticated_at",
    "expires_at",
    "expected_fingerprint",
)
OLD_GUARD = mutable_guard_v1("stewardship_secret_request", frozen_fields=FROZEN_FIELDS)
NEW_GUARD = mutable_guard_v1(
    "stewardship_secret_request",
    frozen_fields=(*FROZEN_FIELDS, "required_consumers"),
    write_once_fields=("resulting_fingerprint", "installed_at", "acknowledged_at"),
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0030_credentialconsumeracknowledgement")]
    operations = [
        migrations.RunSQL(
            sql=OLD_GUARD.reverse_sql + NEW_GUARD.sql,
            reverse_sql=NEW_GUARD.reverse_sql + OLD_GUARD.sql,
        ),
        immutable_guard_v1("stewardship_credential_consumer_ack"),
        migrations.RunSQL(
            sql=r"""
CREATE FUNCTION stewardship_credential_consumers_v1(target text) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
SELECT CASE target
    WHEN 'django_signing' THEN '["web"]'::jsonb
    WHEN 'general_encryption' THEN '["web","worker"]'::jsonb
    WHEN 'family_code_mac' THEN '["web","worker"]'::jsonb
    WHEN 'token_public' THEN '["web","worker","scheduler","mail-dispatch","token-key-rotation"]'::jsonb
    WHEN 'token_private' THEN '["mail-dispatch","token-key-rotation"]'::jsonb
    WHEN 'google_oauth' THEN '["web"]'::jsonb
    WHEN 'google_workspace' THEN '["mail-dispatch"]'::jsonb
    WHEN 'parishsoft' THEN '["worker"]'::jsonb
    WHEN 'slack' THEN '["worker"]'::jsonb
    WHEN 'backup_target' THEN '["backup-worker"]'::jsonb
    WHEN 'backup_data' THEN '["backup-worker"]'::jsonb
    WHEN 'metrics' THEN '["web"]'::jsonb
    ELSE '[]'::jsonb END;
$$;

CREATE FUNCTION stewardship_secret_state_v2() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Secret request history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF jsonb_typeof(NEW.required_consumers)<>'array'
       OR jsonb_array_length(NEW.required_consumers)>6
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.required_consumers) x WHERE jsonb_typeof(x)<>'string')
       OR NOT stewardship_credential_consumers_v1(NEW.target) @> NEW.required_consumers
       OR (SELECT count(*)<>count(DISTINCT x) FROM jsonb_array_elements(NEW.required_consumers) x) THEN
        RAISE EXCEPTION 'Invalid credential consumer inventory' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
        IF NEW.state<>'staged' OR NEW.version<>1 OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.expires_at<=statement_timestamp() OR NEW.resulting_fingerprint IS NOT NULL
           OR NEW.installed_at IS NOT NULL OR NEW.acknowledged_at IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid secret request intake' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.required_consumers IS DISTINCT FROM OLD.required_consumers
       OR (OLD.resulting_fingerprint IS NOT NULL AND NEW.resulting_fingerprint IS DISTINCT FROM OLD.resulting_fingerprint)
       OR (OLD.installed_at IS NOT NULL AND NEW.installed_at IS DISTINCT FROM OLD.installed_at)
       OR (OLD.acknowledged_at IS NOT NULL AND NEW.acknowledged_at IS DISTINCT FROM OLD.acknowledged_at) THEN
        RAISE EXCEPTION 'Credential installation evidence is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.required_consumers<>'[]'::jsonb
       AND NOT(OLD.state='staged' AND NEW.state='cleanup_pending' AND NEW.cleanup_reason='cancelled')
       AND current_user<>'pk_stewardship_credential_'||OLD.target THEN
        RAISE EXCEPTION 'Only the target installer may advance this request' USING ERRCODE='23514';
    END IF;
    IF OLD.resulting_fingerprint IS NULL AND NEW.resulting_fingerprint IS NOT NULL
       AND NOT(OLD.state='testing' AND NEW.state='installing') THEN
        RAISE EXCEPTION 'Credential fingerprint requires successful testing' USING ERRCODE='23514';
    END IF;
    IF OLD.installed_at IS NULL AND NEW.installed_at IS NOT NULL
       AND NOT(OLD.state='installing' AND NEW.state='awaiting_ack') THEN
        RAISE EXCEPTION 'Credential install instant requires installation' USING ERRCODE='23514';
    END IF;
    IF OLD.acknowledged_at IS NULL AND NEW.acknowledged_at IS NOT NULL
       AND NOT(OLD.state='awaiting_ack' AND NEW.state='cleanup_pending' AND NEW.cleanup_reason='applied') THEN
        RAISE EXCEPTION 'Credential acknowledgement requires consumer evidence' USING ERRCODE='23514';
    END IF;
    IF OLD.state='staged' AND NEW.state='testing' THEN
        IF NEW.actor_id IS NOT NULL OR NEW.expires_at<=statement_timestamp()
           OR NEW.reauthenticated_at<NEW.created_at-interval '5 minutes'
           OR NEW.required_consumers='[]'::jsonb
           OR NOT EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging WHERE request_id=NEW.id AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Credential testing requires a live sealed request' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='testing' AND NEW.state='installing' THEN
        IF NEW.resulting_fingerprint IS NULL OR NEW.expires_at<=statement_timestamp() OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Credential installation requires tested fingerprint' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging
            WHERE request_id=NEW.id AND fingerprint=NEW.resulting_fingerprint AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Tested credential must match its sealed intake' USING ERRCODE='23514'; END IF;
    ELSIF OLD.state='installing' AND NEW.state='awaiting_ack' THEN
        IF NEW.actor_id IS NOT NULL THEN RAISE EXCEPTION 'Installation requires system attribution' USING ERRCODE='23514'; END IF;
        NEW.installed_at:=statement_timestamp();
    ELSIF OLD.state IN('staged','testing','installing','awaiting_ack') AND NEW.state='cleanup_pending' THEN
        IF NEW.cleanup_reason='cancelled' THEN
            IF NEW.actor_id IS DISTINCT FROM OLD.requested_by_id THEN
                RAISE EXCEPTION 'Invalid cancellation attribution' USING ERRCODE='23514'; END IF;
        ELSIF NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Cleanup requires system attribution' USING ERRCODE='23514';
        END IF;
        IF NEW.cleanup_reason='expired' AND OLD.expires_at>statement_timestamp() THEN
            RAISE EXCEPTION 'Secret request is not expired' USING ERRCODE='23514';
        END IF;
        IF NEW.cleanup_reason='applied' THEN
            IF OLD.state<>'awaiting_ack' OR OLD.expires_at<=statement_timestamp()
               OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.required_consumers) c
                    WHERE NOT EXISTS(SELECT 1 FROM stewardship_credential_consumer_ack a
                        WHERE a.request_id=NEW.id AND a.consumer=c AND a.fingerprint=NEW.resulting_fingerprint)) THEN
                RAISE EXCEPTION 'Credential consumers have not acknowledged' USING ERRCODE='23514';
            END IF;
            NEW.acknowledged_at:=statement_timestamp();
        END IF;
    ELSIF OLD.state='cleanup_pending' AND NEW.state=OLD.cleanup_reason AND NEW.cleanup_reason=OLD.cleanup_reason THEN
        IF NEW.actor_id IS NOT NULL THEN RAISE EXCEPTION 'Cleanup requires system attribution' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging WHERE request_id=NEW.id AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Credential ciphertext must be scrubbed before completion' USING ERRCODE='23514'; END IF;
        NEW.scrubbed_at:=statement_timestamp();
    ELSE
        RAISE EXCEPTION 'Invalid secret request transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER stewardship_secret_state_v1 ON stewardship_secret_request;
CREATE TRIGGER stewardship_secret_state_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_secret_request
FOR EACH ROW EXECUTE FUNCTION stewardship_secret_state_v2();

CREATE FUNCTION stewardship_valid_sealed_candidate_v1(value text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE j jsonb;
BEGIN
    IF value IS NULL OR octet_length(value)>262144 THEN RETURN false; END IF;
    BEGIN j:=value::jsonb; EXCEPTION WHEN OTHERS THEN RETURN false; END;
    RETURN jsonb_typeof(j)='object' AND j ?& ARRAY['v','alg','kid','body']
        AND j-ARRAY['v','alg','kid','body']='{}'::jsonb AND j->'v'='1'::jsonb
        AND j->>'alg'='sealedbox-v1' AND j->>'kid' ~ '^[A-Za-z0-9_-]{1,48}$'
        AND j->>'body' ~ '^[A-Za-z0-9_-]+$';
END $$;
CREATE FUNCTION stewardship_sealed_staging_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner stewardship_secret_request%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.ciphertext IS NOT NULL THEN RAISE EXCEPTION 'Sealed staging must be scrubbed first' USING ERRCODE='23514'; END IF;
        RETURN OLD;
    END IF;
    SELECT * INTO owner FROM stewardship_secret_request WHERE id=NEW.request_id FOR UPDATE;
    IF NOT FOUND OR NEW.target<>owner.target OR NEW.reference<>owner.staging_reference THEN
        RAISE EXCEPTION 'Sealed staging requires its exact request target' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF owner.state<>'staged' OR owner.expires_at<=statement_timestamp()
           OR NOT coalesce(stewardship_valid_sealed_candidate_v1(NEW.ciphertext),false) THEN
            RAISE EXCEPTION 'Invalid sealed credential payload' USING ERRCODE='23514'; END IF;
    ELSIF (NEW.reference,NEW.request_id,NEW.target,NEW.fingerprint) IS DISTINCT FROM(OLD.reference,OLD.request_id,OLD.target,OLD.fingerprint)
        OR NEW.ciphertext IS NOT NULL OR OLD.ciphertext IS NULL OR owner.state NOT IN('awaiting_ack','cleanup_pending') THEN
        RAISE EXCEPTION 'Sealed staging permits only post-install or terminal scrubbing' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_sealed_staging_guard_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_sealed_credential_staging
FOR EACH ROW EXECUTE FUNCTION stewardship_sealed_staging_guard_v1();

CREATE FUNCTION stewardship_credential_ack_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner stewardship_secret_request%ROWTYPE;
BEGIN
    SELECT * INTO owner FROM stewardship_secret_request WHERE id=NEW.request_id FOR SHARE;
    IF NOT FOUND OR current_user<>'pk_stewardship_'||replace(NEW.consumer,'-','_')
       OR NOT owner.required_consumers ? NEW.consumer OR owner.state<>'awaiting_ack'
       OR owner.resulting_fingerprint IS DISTINCT FROM NEW.fingerprint
       OR owner.expires_at<=statement_timestamp() OR NEW.actor_id IS NOT NULL THEN
        RAISE EXCEPTION 'Consumer acknowledgement is not authorized' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    INSERT INTO stewardship_audit_event(id,created_at,actor_id,correlation_id,event_type,subject_id)
    VALUES(gen_random_uuid(),NEW.created_at,NULL,NEW.correlation_id,'credential_consumer_acknowledged',NEW.request_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_credential_ack_guard_v1 BEFORE INSERT ON stewardship_credential_consumer_ack
FOR EACH ROW EXECUTE FUNCTION stewardship_credential_ack_guard_v1();

ALTER TABLE stewardship_secret_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE stewardship_secret_request FORCE ROW LEVEL SECURITY;
CREATE POLICY stewardship_secret_target_scope ON stewardship_secret_request
USING (current_user IN('pk_stewardship_web','pk_stewardship_backup_worker')
    OR current_user='pk_stewardship_credential_'||target
    OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(required_consumers) c WHERE current_user='pk_stewardship_'||replace(c,'-','_')))
WITH CHECK (current_user='pk_stewardship_web' OR current_user='pk_stewardship_credential_'||target);
ALTER TABLE stewardship_sealed_credential_staging ENABLE ROW LEVEL SECURITY;
ALTER TABLE stewardship_sealed_credential_staging FORCE ROW LEVEL SECURITY;
CREATE POLICY stewardship_staging_intake ON stewardship_sealed_credential_staging FOR INSERT
WITH CHECK (current_user='pk_stewardship_web');
CREATE POLICY stewardship_staging_read ON stewardship_sealed_credential_staging FOR SELECT
USING (current_user='pk_stewardship_credential_'||target OR current_user IN('pk_stewardship_web','pk_stewardship_backup_worker'));
CREATE POLICY stewardship_staging_scrub ON stewardship_sealed_credential_staging FOR UPDATE
USING (current_user='pk_stewardship_credential_'||target) WITH CHECK (current_user='pk_stewardship_credential_'||target);
CREATE POLICY stewardship_staging_cleanup ON stewardship_sealed_credential_staging FOR DELETE
USING (current_user='pk_stewardship_credential_'||target);
ALTER TABLE stewardship_credential_consumer_ack ENABLE ROW LEVEL SECURITY;
ALTER TABLE stewardship_credential_consumer_ack FORCE ROW LEVEL SECURITY;
CREATE POLICY stewardship_ack_read ON stewardship_credential_consumer_ack FOR SELECT
USING (EXISTS(SELECT 1 FROM stewardship_secret_request r WHERE r.id=request_id));
CREATE POLICY stewardship_ack_write ON stewardship_credential_consumer_ack FOR INSERT
WITH CHECK (current_user='pk_stewardship_'||replace(consumer,'-','_'));
""",
            reverse_sql="""
DROP POLICY stewardship_ack_write ON stewardship_credential_consumer_ack;
DROP POLICY stewardship_ack_read ON stewardship_credential_consumer_ack;
ALTER TABLE stewardship_credential_consumer_ack DISABLE ROW LEVEL SECURITY;
ALTER TABLE stewardship_credential_consumer_ack NO FORCE ROW LEVEL SECURITY;
DROP POLICY stewardship_staging_cleanup ON stewardship_sealed_credential_staging;
DROP POLICY stewardship_staging_scrub ON stewardship_sealed_credential_staging;
DROP POLICY stewardship_staging_read ON stewardship_sealed_credential_staging;
DROP POLICY stewardship_staging_intake ON stewardship_sealed_credential_staging;
ALTER TABLE stewardship_sealed_credential_staging DISABLE ROW LEVEL SECURITY;
ALTER TABLE stewardship_sealed_credential_staging NO FORCE ROW LEVEL SECURITY;
DROP POLICY stewardship_secret_target_scope ON stewardship_secret_request;
ALTER TABLE stewardship_secret_request DISABLE ROW LEVEL SECURITY;
ALTER TABLE stewardship_secret_request NO FORCE ROW LEVEL SECURITY;
DROP TRIGGER stewardship_credential_ack_guard_v1 ON stewardship_credential_consumer_ack;
DROP FUNCTION stewardship_credential_ack_guard_v1();
DROP TRIGGER stewardship_sealed_staging_guard_v1 ON stewardship_sealed_credential_staging;
DROP FUNCTION stewardship_sealed_staging_guard_v1();
DROP FUNCTION stewardship_valid_sealed_candidate_v1(text);
DROP TRIGGER stewardship_secret_state_v1 ON stewardship_secret_request;
CREATE TRIGGER stewardship_secret_state_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_secret_request
FOR EACH ROW EXECUTE FUNCTION stewardship_secret_state_v1();
DROP FUNCTION stewardship_secret_state_v2();
DROP FUNCTION stewardship_credential_consumers_v1(text);
""",
        ),
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql="""
LOCK TABLE stewardship_secret_request,stewardship_sealed_credential_staging,stewardship_credential_consumer_ack IN ACCESS EXCLUSIVE MODE;
-- The schema owner must see retained history even without superuser/BYPASSRLS.
-- A rejected downgrade rolls these changes back with the atomic migration.
ALTER TABLE stewardship_secret_request NO FORCE ROW LEVEL SECURITY;
ALTER TABLE stewardship_sealed_credential_staging NO FORCE ROW LEVEL SECURITY;
ALTER TABLE stewardship_credential_consumer_ack NO FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_secret_request WHERE state NOT IN('staged','cleanup_pending','cancelled','expired')
        OR required_consumers<>'[]'::jsonb OR resulting_fingerprint IS NOT NULL OR installed_at IS NOT NULL OR acknowledged_at IS NOT NULL)
        OR EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging)
        OR EXISTS(SELECT 1 FROM stewardship_credential_consumer_ack) THEN
        RAISE EXCEPTION 'Credential installation history prevents downgrade' USING ERRCODE='23514'; END IF;
END $$;
""",
        ),
    ]
