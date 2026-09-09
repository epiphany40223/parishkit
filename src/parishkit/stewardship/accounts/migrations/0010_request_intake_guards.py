"""Append-only intake and serialized checkpoints with inseparable safe audit."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    """Freeze this two-state intake protocol; installers need a later migration."""

    dependencies = [
        ("stewardship_accounts", "0009_configurationchangerequest_and_more"),
        ("stewardship_audit", "0005_alter_auditevent_subject_id"),
    ]
    operations = [
        immutable_guard_v1("stewardship_config_request"),
        immutable_guard_v1("stewardship_config_checkpoint"),
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION stewardship_request_checkpoint_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE
                    owner_id uuid;
                    requested_at timestamptz;
                    previous stewardship_config_checkpoint%ROWTYPE;
                BEGIN
                    SELECT actor_id, created_at INTO owner_id, requested_at
                    FROM stewardship_config_request WHERE id = NEW.request_id
                    FOR UPDATE;
                    IF NOT FOUND OR NEW.actor_id IS DISTINCT FROM owner_id
                       OR NEW.created_at < requested_at THEN
                        RAISE EXCEPTION 'Invalid configuration checkpoint attribution'
                            USING ERRCODE = '23514';
                    END IF;
                    SELECT * INTO previous FROM stewardship_config_checkpoint
                    WHERE request_id = NEW.request_id
                    ORDER BY sequence DESC LIMIT 1;
                    IF NOT FOUND THEN
                        IF NEW.sequence <> 1 OR NEW.state <> 'staged' THEN
                            RAISE EXCEPTION 'Configuration intake must start staged'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF previous.sequence <> 1 OR previous.state <> 'staged'
                          OR NEW.sequence <> 2 OR NEW.state <> 'cancelled'
                          OR NEW.created_at < previous.created_at THEN
                        RAISE EXCEPTION 'Invalid configuration intake transition'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_request_checkpoint_v1
                BEFORE INSERT ON stewardship_config_checkpoint
                FOR EACH ROW EXECUTE FUNCTION stewardship_request_checkpoint_v1();

                CREATE FUNCTION stewardship_request_stage_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO stewardship_config_checkpoint
                        (id, created_at, actor_id, correlation_id, request_id,
                         sequence, state)
                    VALUES (gen_random_uuid(), NEW.created_at, NEW.actor_id,
                            NEW.correlation_id, NEW.id, 1, 'staged');
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_request_stage_v1
                AFTER INSERT ON stewardship_config_request
                FOR EACH ROW EXECUTE FUNCTION stewardship_request_stage_v1();

                CREATE FUNCTION stewardship_request_audit_v1()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO stewardship_audit_event
                        (id, created_at, actor_id, correlation_id,
                         event_type, subject_id)
                    VALUES (gen_random_uuid(), NEW.created_at, NEW.actor_id,
                            NEW.correlation_id, 'config_request_' || NEW.state,
                            NEW.request_id);
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER stewardship_request_audit_v1
                AFTER INSERT ON stewardship_config_checkpoint
                FOR EACH ROW EXECUTE FUNCTION stewardship_request_audit_v1();
            """,
            reverse_sql="""
                DROP TRIGGER stewardship_request_audit_v1
                    ON stewardship_config_checkpoint;
                DROP FUNCTION stewardship_request_audit_v1();
                DROP TRIGGER stewardship_request_stage_v1 ON stewardship_config_request;
                DROP FUNCTION stewardship_request_stage_v1();
                DROP TRIGGER stewardship_request_checkpoint_v1
                    ON stewardship_config_checkpoint;
                DROP FUNCTION stewardship_request_checkpoint_v1();
            """,
        ),
    ]
