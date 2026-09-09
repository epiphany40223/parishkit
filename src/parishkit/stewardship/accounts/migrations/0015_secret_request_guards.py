"""Freeze the staging/cleanup subset; installation requires a later migration."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


class Migration(migrations.Migration):
    """Keep target reservation, immutable history and audit inseparable."""

    dependencies = [("stewardship_accounts", "0014_secret_request_records")]
    operations = [
        mutable_guard_v1(
            "stewardship_secret_request",
            frozen_fields=(
                "target",
                "staging_reference",
                "requested_by_id",
                "reauthenticated_at",
                "expires_at",
                "expected_fingerprint",
            ),
        ),
        immutable_guard_v1("stewardship_secret_checkpoint"),
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION stewardship_secret_state_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'Secret request history cannot be deleted'
                            USING ERRCODE = '23514';
                    ELSIF TG_OP = 'INSERT' THEN
                        -- Caller timestamps cannot move the lifetime ceiling.
                        NEW.created_at := statement_timestamp();
                        NEW.updated_at := NEW.created_at;
                        IF NEW.state <> 'staged' OR NEW.version <> 1
                           OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
                           OR NEW.expires_at <= statement_timestamp() THEN
                            RAISE EXCEPTION 'Invalid secret request intake'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF OLD.state = 'staged' AND NEW.state = 'cleanup_pending' THEN
                        IF NEW.cleanup_reason = 'expired'
                           AND NEW.actor_id IS NOT NULL THEN
                            RAISE EXCEPTION 'Expiry requires system attribution'
                                USING ERRCODE = '23514';
                        END IF;
                        IF NEW.cleanup_reason = 'expired'
                           AND OLD.expires_at > statement_timestamp() THEN
                            RAISE EXCEPTION 'Secret request is not expired'
                                USING ERRCODE = '23514';
                        END IF;
                        IF NEW.cleanup_reason = 'cancelled'
                           AND NEW.actor_id IS DISTINCT FROM OLD.requested_by_id THEN
                            RAISE EXCEPTION 'Invalid cancellation attribution'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF OLD.state = 'cleanup_pending'
                          AND NEW.state = OLD.cleanup_reason
                          AND NEW.cleanup_reason = OLD.cleanup_reason THEN
                        IF NEW.actor_id IS NOT NULL THEN
                            RAISE EXCEPTION 'Cleanup requires system attribution'
                                USING ERRCODE = '23514';
                        END IF;
                        NEW.scrubbed_at := statement_timestamp();
                    ELSE
                        RAISE EXCEPTION 'Invalid secret request transition'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_secret_state_v1
                BEFORE INSERT OR UPDATE OR DELETE ON stewardship_secret_request
                FOR EACH ROW EXECUTE FUNCTION stewardship_secret_state_v1();

                CREATE FUNCTION stewardship_secret_checkpoint_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE owner stewardship_secret_request%ROWTYPE;
                BEGIN
                    SELECT * INTO owner FROM stewardship_secret_request
                    WHERE id = NEW.request_id FOR UPDATE;
                    IF NOT FOUND OR NEW.sequence <> owner.version
                       OR NEW.state <> owner.state
                       OR NEW.actor_id IS DISTINCT FROM owner.actor_id
                       OR NEW.correlation_id <> owner.correlation_id
                       OR NEW.created_at <> owner.updated_at THEN
                        RAISE EXCEPTION 'Invalid secret checkpoint binding'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_secret_checkpoint_v1
                BEFORE INSERT ON stewardship_secret_checkpoint
                FOR EACH ROW EXECUTE FUNCTION stewardship_secret_checkpoint_v1();

                CREATE FUNCTION stewardship_secret_history_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO stewardship_secret_checkpoint
                        (id, created_at, actor_id, correlation_id,
                         request_id, sequence, state)
                    VALUES (gen_random_uuid(), NEW.updated_at,
                            NEW.actor_id, NEW.correlation_id,
                            NEW.id, NEW.version, NEW.state);
                    INSERT INTO stewardship_audit_event
                        (id, created_at, actor_id, correlation_id,
                         event_type, subject_id)
                    VALUES (gen_random_uuid(), NEW.updated_at,
                            NEW.actor_id, NEW.correlation_id,
                            'secret_request_' || NEW.state, NEW.id);
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_secret_history_v1
                AFTER INSERT OR UPDATE ON stewardship_secret_request
                FOR EACH ROW EXECUTE FUNCTION stewardship_secret_history_v1();
            """,
            reverse_sql="""
                DROP TRIGGER stewardship_secret_history_v1
                    ON stewardship_secret_request;
                DROP FUNCTION stewardship_secret_history_v1();
                DROP TRIGGER stewardship_secret_checkpoint_v1
                    ON stewardship_secret_checkpoint;
                DROP FUNCTION stewardship_secret_checkpoint_v1();
                DROP TRIGGER stewardship_secret_state_v1 ON stewardship_secret_request;
                DROP FUNCTION stewardship_secret_state_v1();
            """,
        ),
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql="""
                LOCK TABLE stewardship_secret_request IN ACCESS EXCLUSIVE MODE;
                DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM stewardship_secret_request) THEN
                        RAISE EXCEPTION 'Secret request history prevents downgrade'
                            USING ERRCODE = '23514';
                    END IF;
                END $$;
            """,
        ),
    ]
