"""Serialize activation and make runtime/request/audit effects inseparable.

SQL cannot inspect the authority mount: only the installer may use these writes
after checking the manifest. Runtime-role grants remain an OPS-02 prerequisite.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


class Migration(migrations.Migration):
    dependencies = [
        (
            "stewardship_accounts",
            "0012_remove_configurationrequestcheckpoint_config_checkpoint_intake_states_and_more",
        ),
    ]
    operations = [
        immutable_guard_v1("stewardship_config_activation"),
        mutable_guard_v1(
            "stewardship_system_configuration",
            frozen_fields=(
                "mode",
                "testing_recipient",
                "restore_review_required",
            ),
        ),
        migrations.RunSQL(
            sql="""
    CREATE FUNCTION stewardship_request_checkpoint_v2()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        intent stewardship_config_request%ROWTYPE;
        previous stewardship_config_checkpoint%ROWTYPE;
    BEGIN
        SELECT * INTO intent FROM stewardship_config_request
        WHERE id = NEW.request_id FOR UPDATE;
        IF NOT FOUND OR NEW.actor_id IS DISTINCT FROM intent.actor_id
           OR NEW.created_at < intent.created_at THEN
            RAISE EXCEPTION 'Invalid configuration checkpoint attribution'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO previous FROM stewardship_config_checkpoint
        WHERE request_id = NEW.request_id ORDER BY sequence DESC LIMIT 1;
        IF NOT FOUND THEN
            IF NEW.sequence <> 1 OR NEW.state <> 'staged' THEN
                RAISE EXCEPTION 'Configuration intake must start staged'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.sequence <> previous.sequence + 1
               OR NEW.created_at < previous.created_at
               OR NOT (
                   (previous.state = 'staged'
                    AND NEW.state IN ('cancelled', 'validating'))
                   OR (previous.state = 'validating'
                       AND NEW.state IN ('prepared', 'failed'))
                   OR (previous.state = 'prepared'
                       AND NEW.state IN ('yaml_activated', 'failed'))
                   OR (previous.state = 'yaml_activated'
                       AND NEW.state = 'applied')
               ) THEN
                RAISE EXCEPTION 'Invalid configuration installer transition'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF NEW.state IN ('prepared', 'yaml_activated', 'applied')
           AND NOT EXISTS (
               SELECT 1 FROM stewardship_configuration_version
               WHERE id = intent.candidate_version_id
                 AND digest = intent.candidate_digest
                 AND predecessor_id = intent.base_id
           ) THEN
            RAISE EXCEPTION 'Checkpoint requires matching prepared candidate'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.state = 'applied' AND NOT EXISTS (
            SELECT 1 FROM stewardship_config_activation activation
            JOIN stewardship_system_configuration runtime
              ON runtime.active_configuration_id = activation.configuration_id
            WHERE activation.request_id = NEW.request_id
        ) THEN
            RAISE EXCEPTION 'Applied checkpoint requires matching activation'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    DROP TRIGGER stewardship_request_checkpoint_v1 ON stewardship_config_checkpoint;
    CREATE TRIGGER stewardship_request_checkpoint_v2
    BEFORE INSERT ON stewardship_config_checkpoint
    FOR EACH ROW EXECUTE FUNCTION stewardship_request_checkpoint_v2();

    CREATE FUNCTION stewardship_runtime_guard_v1()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Runtime configuration cannot be deleted'
                USING ERRCODE = '23514';
        ELSIF TG_OP = 'INSERT' THEN
            IF NEW.version <> 1 OR NEW.active_configuration_id IS NOT NULL THEN
                RAISE EXCEPTION 'Runtime configuration must start unconfigured'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NOT EXISTS (
            SELECT 1 FROM stewardship_config_activation
            WHERE configuration_id = NEW.active_configuration_id
              AND predecessor_id IS NOT DISTINCT FROM OLD.active_configuration_id
              AND sequence = OLD.version
              AND actor_id IS NOT DISTINCT FROM NEW.actor_id
              AND correlation_id = NEW.correlation_id
        ) THEN
            RAISE EXCEPTION 'Runtime pointer requires activation evidence'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_runtime_guard_v1
    BEFORE INSERT OR UPDATE OR DELETE ON stewardship_system_configuration
    FOR EACH ROW EXECUTE FUNCTION stewardship_runtime_guard_v1();

    CREATE FUNCTION stewardship_activation_guard_v1()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        runtime stewardship_system_configuration%ROWTYPE;
        candidate stewardship_configuration_version%ROWTYPE;
        intent stewardship_config_request%ROWTYPE;
        checkpoint stewardship_config_checkpoint%ROWTYPE;
    BEGIN
        SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
        IF NOT FOUND OR NEW.sequence <> runtime.version
           OR NEW.predecessor_id IS DISTINCT FROM runtime.active_configuration_id
           OR NEW.created_at < runtime.created_at THEN
            RAISE EXCEPTION 'Activation requires current runtime predecessor'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO candidate FROM stewardship_configuration_version
        WHERE id = NEW.configuration_id;
        IF NOT FOUND OR candidate.predecessor_id IS DISTINCT FROM NEW.predecessor_id
           OR NEW.created_at < candidate.created_at THEN
            RAISE EXCEPTION 'Activation requires matching prepared predecessor'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.request_id IS NOT NULL THEN
            SELECT * INTO intent FROM stewardship_config_request
            WHERE id = NEW.request_id FOR UPDATE;
            IF NOT FOUND OR intent.candidate_version_id <> NEW.configuration_id
               OR intent.candidate_digest <> candidate.digest
               OR intent.base_id <> NEW.predecessor_id
               OR intent.actor_id IS DISTINCT FROM NEW.actor_id THEN
                RAISE EXCEPTION 'Activation request does not match candidate'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO checkpoint FROM stewardship_config_checkpoint
            WHERE request_id = NEW.request_id ORDER BY sequence DESC LIMIT 1;
            IF NOT FOUND OR checkpoint.state <> 'yaml_activated'
               OR NEW.created_at < checkpoint.created_at THEN
                RAISE EXCEPTION 'Activation requires YAML checkpoint'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_activation_guard_v1
    BEFORE INSERT ON stewardship_config_activation
    FOR EACH ROW EXECUTE FUNCTION stewardship_activation_guard_v1();

    CREATE FUNCTION stewardship_activation_effects_v1()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE stewardship_system_configuration
        SET active_configuration_id = NEW.configuration_id,
            version = version + 1, actor_id = NEW.actor_id,
            correlation_id = NEW.correlation_id;
        IF NEW.request_id IS NOT NULL THEN
            INSERT INTO stewardship_config_checkpoint
                (id, created_at, actor_id, correlation_id, request_id,
                 sequence, state)
            SELECT gen_random_uuid(), NEW.created_at, NEW.actor_id,
                   NEW.correlation_id, NEW.request_id, MAX(sequence) + 1, 'applied'
            FROM stewardship_config_checkpoint WHERE request_id = NEW.request_id;
        END IF;
        INSERT INTO stewardship_audit_event
            (id, created_at, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.created_at, NEW.actor_id,
                NEW.correlation_id, 'configuration_activated', NEW.id);
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER stewardship_activation_effects_v1
    AFTER INSERT ON stewardship_config_activation
    FOR EACH ROW EXECUTE FUNCTION stewardship_activation_effects_v1();
            """,
            reverse_sql="""
    DROP TRIGGER stewardship_activation_effects_v1 ON stewardship_config_activation;
    DROP FUNCTION stewardship_activation_effects_v1();
    DROP TRIGGER stewardship_activation_guard_v1 ON stewardship_config_activation;
    DROP FUNCTION stewardship_activation_guard_v1();
    DROP TRIGGER stewardship_runtime_guard_v1 ON stewardship_system_configuration;
    DROP FUNCTION stewardship_runtime_guard_v1();
    DROP TRIGGER stewardship_request_checkpoint_v2 ON stewardship_config_checkpoint;
    DROP FUNCTION stewardship_request_checkpoint_v2();
    CREATE TRIGGER stewardship_request_checkpoint_v1
    BEFORE INSERT ON stewardship_config_checkpoint
    FOR EACH ROW EXECUTE FUNCTION stewardship_request_checkpoint_v1();
            """,
        ),
    ]
