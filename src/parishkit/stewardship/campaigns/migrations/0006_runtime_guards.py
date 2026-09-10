"""Versioned lifecycle commands and configuration selection share atomic guards.

External readiness/token/quiescence evidence belongs to the owning services;
these internal storage commands are not granted to operational callers yet.
"""

# ruff: noqa: E501
from importlib import import_module

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


def extend_activation_sequence(apps, editor):
    """Preserve existing activation ordinals while runtime versions advance separately."""
    editor.execute("""
        LOCK TABLE stewardship_system_configuration IN ACCESS EXCLUSIVE MODE;
        ALTER TABLE stewardship_system_configuration DISABLE TRIGGER USER;
        UPDATE stewardship_system_configuration SET configuration_sequence = version;
        ALTER TABLE stewardship_system_configuration ENABLE TRIGGER USER;
    """)
    for name, old, new in [
        (
            "stewardship_activation_guard_v1",
            "NEW.sequence <> runtime.version",
            "NEW.sequence <> runtime.configuration_sequence",
        ),
        (
            "stewardship_activation_effects_v1",
            "version = version + 1,",
            "version = version + 1, configuration_sequence = configuration_sequence + 1,",
        ),
        (
            "stewardship_campaign_activate_v1",
            "IF NEW.current_campaign_id IS NULL THEN RETURN NEW; END IF;",
            "IF NEW.current_campaign_id IS NULL OR NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;",
        ),
    ]:
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [name + "()"])
            definition = cursor.fetchone()[0]
        if definition.count(old) != 1:
            raise RuntimeError("Unexpected activation predecessor.")
        editor.execute(definition.replace(old, new), params=None)


def restore_predecessors(apps, editor):
    """Refuse lossy populated reversal; restore frozen predecessor functions exactly."""
    editor.execute("""
        LOCK TABLE stewardship_campaign_transition, stewardship_runtime_transition,
            stewardship_system_configuration IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM stewardship_campaign_transition)
               OR EXISTS (SELECT 1 FROM stewardship_runtime_transition)
               OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state <> 'draft') THEN
                RAISE EXCEPTION 'Runtime history prevents schema downgrade' USING ERRCODE='23514';
            END IF;
        END $$;
    """)
    for module, names in [
        (
            "parishkit.stewardship.accounts.migrations.0013_activation_guards",
            [
                "stewardship_runtime_guard_v1",
                "stewardship_activation_guard_v1",
                "stewardship_activation_effects_v1",
            ],
        ),
        (
            "parishkit.stewardship.campaigns.migrations.0002_campaign_guards",
            [
                "stewardship_campaign_pointer_v1",
                "stewardship_campaign_runtime_v1",
                "stewardship_campaign_activate_v1",
            ],
        ),
    ]:
        script = "\n".join(
            op.sql
            for op in import_module(module).Migration.operations
            if isinstance(op, migrations.RunSQL)
        )
        for name in names:
            start = script.index("CREATE FUNCTION " + name + "()")
            end = script.index("$$;", start) + 3
            editor.execute(
                script[start:end].replace(
                    "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
                ),
                params=None,
            )


SQL = """
CREATE FUNCTION stewardship_activation_global_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
    PERFORM pg_advisory_xact_lock(736220,1); RETURN NEW;
END $$;
CREATE TRIGGER aaa_stewardship_activation_global_v1 BEFORE INSERT
ON stewardship_config_activation FOR EACH ROW
EXECUTE FUNCTION stewardship_activation_global_v1();

CREATE FUNCTION stewardship_campaign_now_v1() RETURNS timestamptz
LANGUAGE sql STABLE SET search_path=pg_catalog,public,pg_temp AS $$ SELECT statement_timestamp() $$;

CREATE FUNCTION stewardship_campaign_quiet_v1(target uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT NOT EXISTS (SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=$1 AND completed_at IS NULL)
       AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=$1 AND delivery_paused)
       AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_occurrence o
           JOIN stewardship_schedule_definition d ON d.id=o.definition_id
           WHERE d.campaign_id=$1 AND o.state IN ('pending','running','delivery_unknown'))
$$;

CREATE OR REPLACE FUNCTION stewardship_runtime_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Runtime configuration cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.configuration_sequence<>1 OR NEW.active_configuration_id IS NOT NULL OR NEW.mode<>'testing' OR NEW.restore_review_required
           OR NEW.restore_id IS NOT NULL OR NEW.restore_backup_at IS NOT NULL OR NEW.restore_activated_at IS NOT NULL OR NEW.restore_released_at IS NOT NULL THEN
            RAISE EXCEPTION 'Runtime must start unconfigured in Testing' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS DISTINCT FROM OLD.active_configuration_id THEN
        IF NEW.configuration_sequence<>OLD.configuration_sequence+1
           OR NEW.mode<>OLD.mode OR NEW.restore_review_required<>OLD.restore_review_required
           OR NOT EXISTS (SELECT 1 FROM stewardship_config_activation
               WHERE configuration_id=NEW.active_configuration_id AND predecessor_id IS NOT DISTINCT FROM OLD.active_configuration_id
                 AND sequence=OLD.configuration_sequence AND actor_id IS NOT DISTINCT FROM NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Runtime pointer requires exact activation evidence' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.configuration_sequence<>OLD.configuration_sequence OR NOT EXISTS (
            SELECT 1 FROM stewardship_runtime_transition t
            WHERE t.expected_version=OLD.version AND t.before_mode=OLD.mode AND t.after_mode=NEW.mode
              AND t.before_campaign_id IS NOT DISTINCT FROM OLD.current_campaign_id
              AND t.after_campaign_id IS NOT DISTINCT FROM NEW.current_campaign_id
              AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id
        ) THEN RAISE EXCEPTION 'Runtime mutation requires transition evidence' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION stewardship_campaign_pointer_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE target uuid; candidate stewardship_campaign_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Configuration cannot replace the current pointer' USING ERRCODE='23514';
    END IF;
    target := OLD.current_campaign_id;
    IF target IS NOT NULL AND EXISTS (
        SELECT 1 FROM stewardship_campaign_configuration c WHERE c.configuration_id=NEW.active_configuration_id
        AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)
    ) THEN RAISE EXCEPTION 'Current campaign prevents successor creation' USING ERRCODE='23514'; END IF;
    IF target IS NULL THEN
        IF (SELECT count(*) FROM stewardship_campaign_configuration c
            WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)) > 1 THEN
            RAISE EXCEPTION 'Only one successor can be created' USING ERRCODE='23514';
        END IF;
        SELECT record_id INTO target FROM stewardship_campaign_configuration c
        WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id);
        IF target IS NOT NULL AND (NEW.mode<>'testing' OR NEW.restore_review_required
            OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state NOT IN ('archived','purged'))
            OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))) THEN
            RAISE EXCEPTION 'Successor creation is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM stewardship_campaign old_campaign
        JOIN stewardship_campaign_configuration old_c ON old_c.id=old_campaign.active_configuration_id
        LEFT JOIN stewardship_campaign_configuration new_c ON new_c.record_id=old_campaign.id AND new_c.configuration_id=NEW.active_configuration_id
        WHERE new_c.id IS NULL OR (old_campaign.id IS DISTINCT FROM target AND old_c.values IS DISTINCT FROM new_c.values)
    ) THEN RAISE EXCEPTION 'Historical campaigns cannot be removed or edited' USING ERRCODE='23514'; END IF;
    IF target IS NOT NULL THEN
        SELECT * INTO candidate FROM stewardship_campaign_configuration WHERE record_id=target AND configuration_id=NEW.active_configuration_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Current campaign cannot be removed' USING ERRCODE='23514'; END IF;
        IF NEW.restore_review_required AND EXISTS (
            SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values IS DISTINCT FROM candidate.values
        ) THEN RAISE EXCEPTION 'Restore review holds campaign configuration changes' USING ERRCODE='23514'; END IF;
        IF OLD.current_campaign_id IS NULL AND candidate.timezone<>(SELECT timezone FROM stewardship_parish WHERE configuration_id=NEW.active_configuration_id) THEN
            RAISE EXCEPTION 'New draft must copy the parish timezone' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION stewardship_campaign_runtime_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Campaign deletion requires exceptional purge' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND NEW.active_configuration_id=OLD.active_configuration_id THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_campaign_transition t
            WHERE t.campaign_id=NEW.id AND t.expected_version=OLD.version
              AND t.before_state=OLD.state AND t.after_state=NEW.state
              AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Campaign writes require lifecycle evidence' USING ERRCODE='23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration r
        JOIN stewardship_campaign_configuration c ON c.configuration_id=r.active_configuration_id
        JOIN stewardship_config_activation a ON a.configuration_id=r.active_configuration_id
        WHERE r.current_campaign_id=NEW.id AND c.id=NEW.active_configuration_id AND c.record_id=NEW.id
          AND a.actor_id IS NOT DISTINCT FROM NEW.actor_id AND a.correlation_id=NEW.correlation_id
    ) THEN RAISE EXCEPTION 'Campaign projection requires activation evidence' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND NEW.active_configuration_id<>OLD.active_configuration_id
       AND (to_jsonb(NEW)-ARRAY['active_configuration_id','version','updated_at','actor_id','correlation_id'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['active_configuration_id','version','updated_at','actor_id','correlation_id']) THEN
        RAISE EXCEPTION 'Configuration cannot mutate campaign runtime' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_campaign_transition_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
    p stewardship_campaign_configuration%ROWTYPE; expected_state text; expected_mode text;
    instant timestamptz := stewardship_campaign_now_v1();
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    IF NOT FOUND OR c.version<>NEW.expected_version OR r.version<>NEW.expected_runtime_version
       OR c.state<>NEW.before_state OR r.mode<>NEW.before_mode
       OR NEW.configuration_id<>r.active_configuration_id
       OR r.current_campaign_id IS DISTINCT FROM c.id OR r.restore_review_required
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Campaign transition has stale or blocked inputs' USING ERRCODE='23514';
    END IF;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    expected_mode := r.mode;
    CASE NEW.action
        WHEN 'activate' THEN
            IF c.state<>'draft' OR r.mode<>'testing' OR instant>=p.ends_at OR NEW.token_generation_id IS NULL THEN
                RAISE EXCEPTION 'Activation is not admitted' USING ERRCODE='23514'; END IF;
            expected_state := CASE WHEN instant<p.starts_at THEN 'scheduled' ELSE 'active' END;
            expected_mode := 'production';
        WHEN 'start' THEN
            IF c.state<>'scheduled' OR r.mode<>'production' OR instant<p.starts_at THEN
                RAISE EXCEPTION 'Start is not due' USING ERRCODE='23514'; END IF;
            expected_state := 'active';
        WHEN 'close' THEN
            IF c.state<>'active' OR r.mode<>'production' OR instant<p.ends_at THEN
                RAISE EXCEPTION 'Close is not due' USING ERRCODE='23514'; END IF;
            expected_state := 'closed';
        WHEN 'withdraw' THEN
            IF c.state<>'scheduled' OR c.ever_active OR r.mode<>'production' OR instant>=p.starts_at OR NEW.reason='' OR NOT stewardship_campaign_quiet_v1(c.id) THEN
                RAISE EXCEPTION 'Withdrawal is not admitted' USING ERRCODE='23514'; END IF;
            expected_state := 'draft'; expected_mode := 'testing';
        WHEN 'archive' THEN
            IF c.state<>'closed' OR NOT stewardship_campaign_quiet_v1(c.id) THEN
                RAISE EXCEPTION 'Archive requires closed and quiet campaign' USING ERRCODE='23514'; END IF;
            expected_state := 'archived';
        WHEN 'unarchive' THEN
            IF c.state<>'archived' OR NOT stewardship_campaign_quiet_v1(c.id) THEN
                RAISE EXCEPTION 'Unarchive is not admitted' USING ERRCODE='23514'; END IF;
            expected_state := 'closed';
        ELSE RAISE EXCEPTION 'Unsupported campaign transition' USING ERRCODE='23514';
    END CASE;
    IF NEW.after_state<>expected_state OR NEW.after_mode<>expected_mode
       OR (NEW.action NOT IN ('start','close') AND NEW.actor_id IS NULL) THEN
        RAISE EXCEPTION 'Campaign transition result or actor is inconsistent' USING ERRCODE='23514';
    END IF;
    IF NEW.action IN ('start','close') AND NOT EXISTS (
        SELECT 1 FROM stewardship_campaign_boundary b JOIN stewardship_task_run t ON t.id=b.task_id
        WHERE b.id=NEW.boundary_id AND b.campaign_id=c.id AND b.kind=NEW.action AND b.state='pending'
          AND b.due_at=CASE WHEN NEW.action='start' THEN p.starts_at ELSE p.ends_at END
          AND t.state='running' AND t.lease_expires_at>clock_timestamp()
          AND t.fence=NEW.task_fence AND t.worker_id=NEW.actor_id
    ) THEN RAISE EXCEPTION 'Boundary requires a current fenced task' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_transition_v1 BEFORE INSERT ON stewardship_campaign_transition
FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_transition_v1();

CREATE FUNCTION stewardship_runtime_transition_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    IF r.version<>NEW.expected_version OR r.mode<>NEW.before_mode OR r.current_campaign_id IS DISTINCT FROM NEW.before_campaign_id THEN
        RAISE EXCEPTION 'Runtime transition is stale' USING ERRCODE='23514'; END IF;
    IF NEW.action='return_testing' THEN
        IF NEW.actor_id IS NULL OR NEW.after_mode<>'testing' OR NEW.after_campaign_id IS NOT NULL
           OR r.restore_review_required OR NOT stewardship_campaign_quiet_v1(r.current_campaign_id)
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=r.current_campaign_id AND state='archived')
           OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'Return to Testing requires current archived quiescence' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='campaign' THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.campaign_transition_id
            AND t.expected_runtime_version=r.version AND t.before_mode=NEW.before_mode AND t.after_mode=NEW.after_mode
            AND t.campaign_id=NEW.before_campaign_id AND NEW.after_campaign_id=NEW.before_campaign_id
            AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Mode transition requires campaign evidence' USING ERRCODE='23514'; END IF;
    ELSE RAISE EXCEPTION 'Unsupported runtime transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_runtime_transition_v1 BEFORE INSERT ON stewardship_runtime_transition
FOR EACH ROW EXECUTE FUNCTION stewardship_runtime_transition_v1();

CREATE FUNCTION stewardship_runtime_transition_effect_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE stewardship_system_configuration SET mode=NEW.after_mode, current_campaign_id=NEW.after_campaign_id,
        version=version+1, actor_id=NEW.actor_id, correlation_id=NEW.correlation_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'runtime_transition',NEW.id,NEW.before_campaign_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_runtime_transition_effect_v1 AFTER INSERT ON stewardship_runtime_transition
FOR EACH ROW EXECUTE FUNCTION stewardship_runtime_transition_effect_v1();

CREATE FUNCTION stewardship_campaign_transition_effect_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE stewardship_campaign SET state=NEW.after_state, version=version+1,
        structural_locked=CASE WHEN NEW.action='withdraw' THEN false WHEN NEW.action='activate' THEN true ELSE structural_locked END,
        ever_active=ever_active OR NEW.after_state='active',
        active_token_generation_id=CASE WHEN NEW.action='activate' THEN NEW.token_generation_id
            WHEN NEW.action IN ('close','withdraw') THEN NULL ELSE active_token_generation_id END,
        readiness_revision=readiness_revision+CASE WHEN NEW.action IN ('activate','withdraw') THEN 1 ELSE 0 END,
        actor_id=NEW.actor_id, correlation_id=NEW.correlation_id WHERE id=NEW.campaign_id;
    INSERT INTO stewardship_runtime_transition(id,request_id,expected_version,action,before_mode,after_mode,
        before_campaign_id,after_campaign_id,campaign_transition_id,reason,actor_id,correlation_id)
    VALUES (gen_random_uuid(),NEW.request_id,NEW.expected_runtime_version,'campaign',NEW.before_mode,NEW.after_mode,
        NEW.campaign_id,NEW.campaign_id,NEW.id,NEW.reason,NEW.actor_id,NEW.correlation_id);
    IF NEW.action='activate' AND NEW.after_state='active' THEN
        INSERT INTO stewardship_activation_catchup(id,campaign_id,activation_id,cutoff,configuration_id,
            phase,cursor,groups_completed,items_completed,failure_code,version,actor_id,correlation_id)
        VALUES (gen_random_uuid(),NEW.campaign_id,NEW.id,stewardship_campaign_now_v1(),NEW.configuration_id,
            'pending','',0,0,'',1,NEW.actor_id,NEW.correlation_id);
    END IF;
    IF NEW.boundary_id IS NOT NULL THEN
        UPDATE stewardship_campaign_boundary SET state='succeeded',transition_id=NEW.id,
            completed_at=stewardship_campaign_now_v1(),version=version+1,actor_id=NEW.actor_id,
            correlation_id=NEW.correlation_id WHERE id=NEW.boundary_id;
    END IF;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'campaign_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_transition_effect_v1 AFTER INSERT ON stewardship_campaign_transition
FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_transition_effect_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0005_admission_gate")]
    operations = [
        migrations.RunPython(extend_activation_sequence, restore_predecessors),
        migrations.RunSQL(
            "DROP TRIGGER stewardship_campaign_mutable_guard_v1 ON stewardship_campaign; "
            "DROP FUNCTION stewardship_campaign_mutable_v1(); "
            "DROP TRIGGER stewardship_system_configuration_mutable_guard_v1 ON stewardship_system_configuration; "
            "DROP FUNCTION stewardship_system_configuration_mutable_v1();",
            reverse_sql=(
                mutable_guard_v1("stewardship_campaign", frozen_fields=("state",)).sql
                + mutable_guard_v1(
                    "stewardship_system_configuration",
                    frozen_fields=(
                        "mode",
                        "testing_recipient",
                        "restore_review_required",
                    ),
                ).sql
            ),
        ),
        mutable_guard_v1("stewardship_campaign"),
        mutable_guard_v1(
            "stewardship_system_configuration",
            frozen_fields=(
                "testing_recipient",
                "restore_review_required",
                "restore_id",
                "restore_backup_at",
                "restore_activated_at",
                "restore_released_at",
            ),
        ),
        immutable_guard_v1("stewardship_campaign_transition"),
        immutable_guard_v1("stewardship_runtime_transition"),
        immutable_guard_v1("stewardship_catchup_checkpoint"),
        mutable_guard_v1(
            "stewardship_campaign_boundary",
            frozen_fields=("campaign_id", "kind", "due_at"),
        ),
        mutable_guard_v1(
            "stewardship_activation_catchup",
            frozen_fields=(
                "campaign_id",
                "activation_id",
                "cutoff",
                "configuration_id",
            ),
            write_once_fields=("source_snapshot_id", "task_root_id"),
        ),
        mutable_guard_v1(
            "stewardship_campaign_work_gate",
            frozen_fields=("campaign_id", "request_id", "initiated_by_id"),
        ),
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER aaa_stewardship_activation_global_v1 ON stewardship_config_activation;
DROP FUNCTION stewardship_activation_global_v1();
DROP TRIGGER stewardship_campaign_transition_effect_v1 ON stewardship_campaign_transition;
DROP FUNCTION stewardship_campaign_transition_effect_v1();
DROP TRIGGER stewardship_runtime_transition_effect_v1 ON stewardship_runtime_transition;
DROP FUNCTION stewardship_runtime_transition_effect_v1();
DROP TRIGGER stewardship_runtime_transition_v1 ON stewardship_runtime_transition;
DROP FUNCTION stewardship_runtime_transition_v1();
DROP TRIGGER stewardship_campaign_transition_v1 ON stewardship_campaign_transition;
DROP FUNCTION stewardship_campaign_transition_v1();
DROP FUNCTION stewardship_campaign_quiet_v1(uuid);
DROP FUNCTION stewardship_campaign_now_v1();
""",
        ),
    ]
