"""Atomic schedule selections and immutable occurrence/semantic history."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)

SQL = """
CREATE FUNCTION stewardship_schedule_definition_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r stewardship_system_configuration%ROWTYPE; chosen stewardship_schedule_revision%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Schedules are removed through configuration' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration;
    SELECT * INTO chosen FROM stewardship_schedule_revision
        WHERE configuration_id=r.active_configuration_id AND record_id=NEW.id;
    IF NEW.campaign_id IS DISTINCT FROM r.current_campaign_id
       OR NEW.actor_id IS DISTINCT FROM r.actor_id OR NEW.correlation_id<>r.correlation_id
       OR (NEW.current_revision_id IS NOT NULL AND (chosen.id IS NULL OR chosen.id<>NEW.current_revision_id OR chosen.kind<>NEW.kind OR chosen.campaign_id<>NEW.campaign_id))
       OR (NEW.current_revision_id IS NULL AND chosen.id IS NOT NULL) THEN
        RAISE EXCEPTION 'Schedule selection requires exact applied configuration' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND NEW.current_revision_id IS NOT DISTINCT FROM OLD.current_revision_id THEN
        RAISE EXCEPTION 'Schedule selection must change' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence o LEFT JOIN stewardship_task_run t ON t.id=o.task_id
        WHERE o.definition_id=NEW.id AND o.revision_id=OLD.current_revision_id
          AND (o.state IN ('running','delivery_unknown') OR (o.state='pending' AND (o.outbox_id IS NOT NULL OR t.state IN ('queued','running','retry_wait','abandoned'))))
    ) THEN RAISE EXCEPTION 'In-flight schedule work blocks replacement' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_schedule_definition_v1 BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_schedule_definition FOR EACH ROW EXECUTE FUNCTION stewardship_schedule_definition_v1();

CREATE FUNCTION stewardship_schedule_selection_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE config uuid; prior uuid;
BEGIN
    SELECT active_configuration_id INTO config FROM stewardship_system_configuration;
    IF TG_OP='UPDATE' THEN prior:=OLD.current_revision_id; END IF;
    INSERT INTO stewardship_schedule_selection(id,definition_id,configuration_id,previous_revision_id,selected_revision_id,version,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.id,config,prior,NEW.current_revision_id,NEW.version,NEW.actor_id,NEW.correlation_id);
    UPDATE stewardship_schedule_occurrence SET state='skipped',version=version+1,
        reason=CASE WHEN NEW.current_revision_id IS NULL THEN 'schedule_removed' ELSE 'schedule_replaced' END,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE definition_id=NEW.id AND revision_id=prior AND state IN ('pending','failed');
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'schedule_selected',NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_schedule_selection_effect_v1 AFTER INSERT OR UPDATE
ON stewardship_schedule_definition FOR EACH ROW EXECUTE FUNCTION stewardship_schedule_selection_effect_v1();

CREATE FUNCTION stewardship_schedules_activate_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE revision stewardship_schedule_revision%ROWTYPE; previous stewardship_schedule_definition%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id OR NEW.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    FOR revision IN SELECT * FROM stewardship_schedule_revision
        WHERE configuration_id=NEW.active_configuration_id AND campaign_id=NEW.current_campaign_id ORDER BY record_id LOOP
        SELECT * INTO previous FROM stewardship_schedule_definition WHERE id=revision.record_id FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO stewardship_schedule_definition(id,campaign_id,kind,current_revision_id,version,actor_id,correlation_id)
            VALUES(revision.record_id,revision.campaign_id,revision.kind,revision.id,1,NEW.actor_id,NEW.correlation_id);
        ELSIF previous.current_revision_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_schedule_revision WHERE id=previous.current_revision_id AND values=revision.values
        ) THEN
            UPDATE stewardship_schedule_definition SET current_revision_id=revision.id,removed_at=NULL,
                version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=previous.id;
        END IF;
    END LOOP;
    UPDATE stewardship_schedule_definition d SET current_revision_id=NULL,removed_at=statement_timestamp(),version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE d.campaign_id=NEW.current_campaign_id AND d.current_revision_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_revision WHERE configuration_id=NEW.active_configuration_id AND record_id=d.id);
    RETURN NEW;
END $$;
CREATE TRIGGER zzz_stewardship_schedules_activate_v1 AFTER UPDATE ON stewardship_system_configuration
FOR EACH ROW EXECUTE FUNCTION stewardship_schedules_activate_v1();

CREATE FUNCTION stewardship_occurrence_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE d stewardship_schedule_definition%ROWTYPE; c stewardship_campaign%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; t stewardship_task_run%ROWTYPE;
    instant timestamptz := stewardship_campaign_now_v1();
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Occurrence history requires exceptional retention' USING ERRCODE='23514'; END IF;
    SELECT * INTO d FROM stewardship_schedule_definition WHERE id=NEW.definition_id FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF NOT EXISTS(SELECT 1 FROM stewardship_schedule_revision WHERE id=NEW.revision_id AND record_id=d.id AND campaign_id=d.campaign_id)
       OR NEW.target='' OR NEW.slot='' OR NEW.occurrence_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid occurrence identity' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.fence<>0 OR NEW.attempts<>0
           OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR NEW.mode<>r.mode OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
               AND ((instant>=p.starts_at AND instant<p.ends_at
                   AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                   OR (NEW.mode='production' AND c.state='closed' AND d.kind IN ('daily_digest','weekly_digest'))))
           OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
           OR EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot) THEN
            RAISE EXCEPTION 'Occurrence creation is not admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF (OLD.state='pending' AND NEW.state NOT IN ('pending','running','skipped','coalesced'))
           OR (OLD.state='running' AND NEW.state NOT IN ('running','pending','delivery_unknown','succeeded','failed','skipped','coalesced'))
           OR (OLD.state='delivery_unknown' AND NEW.state NOT IN ('succeeded','pending','failed'))
           OR (OLD.state='failed' AND NEW.state NOT IN ('pending','skipped'))
           OR OLD.state IN ('succeeded','skipped','coalesced') THEN
            RAISE EXCEPTION 'Invalid occurrence transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='running' THEN
            SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
            IF NOT FOUND OR t.state<>'running' OR t.worker_id<>NEW.worker_id OR t.fence<>NEW.fence OR t.lease_expires_at<=clock_timestamp()
               OR NEW.lease_expires_at>t.lease_expires_at OR NEW.heartbeat_at>clock_timestamp()
               OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
               OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode OR r.restore_review_required
               OR t.domain_request_id IS DISTINCT FROM NEW.id
               OR t.task_type<>'schedule_occurrence'
               OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
                   AND ((instant>=p.starts_at AND instant<p.ends_at
                       AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                       OR (NEW.mode='production' AND c.state='closed' AND d.kind IN ('daily_digest','weekly_digest'))))
               OR NEW.due_at>instant
               OR (NEW.mode='production' AND c.delivery_paused)
               OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
               OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
               OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h WHERE h.definition_id=d.id AND h.mode=NEW.mode
                   AND h.target=NEW.target AND h.slot=NEW.slot AND h.state IN ('unreviewed','assumed_delivered'))
               OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
                RAISE EXCEPTION 'Occurrence claim requires current fenced work' USING ERRCODE='23514'; END IF;
        END IF;
        IF OLD.state='running' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NEW.fence<>OLD.fence) THEN
            RAISE EXCEPTION 'Occurrence worker identity changed' USING ERRCODE='23514'; END IF;
        IF OLD.state='running' AND NOT EXISTS(SELECT 1 FROM stewardship_task_run owner_task WHERE owner_task.id=OLD.task_id
            AND ((owner_task.state='running' AND owner_task.worker_id=NEW.actor_id AND owner_task.fence=NEW.fence AND owner_task.lease_expires_at>clock_timestamp())
                OR (owner_task.state IN ('abandoned','cancelled','succeeded','failed') AND owner_task.fence>=OLD.fence AND NEW.actor_id IS NOT NULL
                    AND NEW.reason IN ('recovery_retry','recovery_unknown','recovery_complete','recovery_fail','recovery_skip','recovery_coalesce')))) THEN
            RAISE EXCEPTION 'Occurrence write requires live worker fencing' USING ERRCODE='23514'; END IF;
        IF NEW.state<>'running' AND OLD.state<>'running' AND NEW.fence<>OLD.fence THEN
            RAISE EXCEPTION 'Only a new claim advances occurrence fencing' USING ERRCODE='23514'; END IF;
        IF OLD.state='delivery_unknown' AND (
            NEW.actor_id IS NULL
            OR NEW.reason IS DISTINCT FROM CASE NEW.state WHEN 'pending' THEN 'recovery_retry'
                WHEN 'succeeded' THEN 'recovery_complete' WHEN 'failed' THEN 'recovery_fail' END
            OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
            OR NOT EXISTS(SELECT 1 FROM stewardship_task_run reconciled WHERE reconciled.id=OLD.task_id
                AND reconciled.task_type='schedule_occurrence' AND reconciled.domain_request_id=OLD.id
                AND reconciled.fence>=OLD.fence AND reconciled.state IN ('abandoned','cancelled','succeeded','failed'))
        ) THEN RAISE EXCEPTION 'Unknown delivery requires attributed reconciled ownership' USING ERRCODE='23514'; END IF;
        IF NEW.state='pending' AND OLD.state='failed' AND (NEW.retry_command_id IS NULL OR NEW.revision_id IS DISTINCT FROM d.current_revision_id) THEN
            RAISE EXCEPTION 'Occurrence retry requires explicit current identity' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND OLD.state='failed' AND NEW.reason NOT IN ('schedule_removed','schedule_replaced') THEN
            RAISE EXCEPTION 'Failed occurrence cannot be silently discarded' USING ERRCODE='23514'; END IF;
        IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state<>'running' AND NEW.state='running' THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Occurrence attempt count is inconsistent' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='coalesced' AND (NEW.replacement_id=NEW.id OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence replacement JOIN stewardship_schedule_definition rd ON rd.id=replacement.definition_id
        WHERE replacement.id=NEW.replacement_id AND rd.campaign_id=d.campaign_id AND replacement.mode=NEW.mode
          AND replacement.state NOT IN ('skipped','coalesced','failed')
    )) THEN RAISE EXCEPTION 'Invalid occurrence coalescing target' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_occurrence_guard_v1 BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION stewardship_occurrence_guard_v1();

CREATE FUNCTION stewardship_occurrence_history_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE before_state text;
BEGIN
    IF TG_OP='UPDATE' THEN before_state:=OLD.state; END IF;
    INSERT INTO stewardship_occurrence_transition(id,occurrence_id,version,before_state,after_state,fence,attempts,reason,retry_command_id,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.id,NEW.version,before_state,NEW.state,NEW.fence,NEW.attempts,NEW.reason,
        CASE WHEN TG_OP='UPDATE' AND OLD.state='failed' AND NEW.state='pending' THEN NEW.retry_command_id ELSE NULL END,
        NEW.actor_id,NEW.correlation_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_occurrence_history_v1 AFTER INSERT OR UPDATE
ON stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION stewardship_occurrence_history_v1();

CREATE FUNCTION stewardship_fulfillment_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot='' OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence o JOIN stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN stewardship_schedule_definition wanted ON wanted.id=NEW.definition_id
        WHERE o.id=NEW.occurrence_id AND o.mode=NEW.mode AND d.campaign_id=wanted.campaign_id
          AND ((NEW.disposition='delivered' AND o.state='succeeded' AND o.definition_id=NEW.definition_id AND o.target=NEW.target AND o.slot=NEW.slot)
            OR (NEW.disposition='coalesced' AND EXISTS (SELECT 1 FROM stewardship_schedule_occurrence original
                WHERE original.definition_id=NEW.definition_id AND original.mode=NEW.mode AND original.target=NEW.target AND original.slot=NEW.slot
                  AND original.state='coalesced' AND original.replacement_id=o.id)))
    ) THEN RAISE EXCEPTION 'Fulfillment requires exact semantic outcome evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_fulfillment_guard_v1 BEFORE INSERT ON stewardship_schedule_fulfillment
FOR EACH ROW EXECUTE FUNCTION stewardship_fulfillment_guard_v1();
"""


def backfill_definitions(apps, editor):
    """Adopt current selections without inventing occurrences or delivery evidence."""
    editor.execute("""
        INSERT INTO stewardship_schedule_definition(id,campaign_id,kind,current_revision_id,removed_at,version,actor_id,correlation_id)
        SELECT DISTINCT ON (old.record_id) old.record_id, old.campaign_id, old.kind, current.id,
            CASE WHEN current.id IS NULL THEN statement_timestamp() END,1,c.actor_id,c.correlation_id
        FROM stewardship_schedule_revision old JOIN stewardship_campaign c ON c.id=old.campaign_id
        JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        LEFT JOIN stewardship_schedule_revision current ON current.record_id=old.record_id AND current.configuration_id=p.configuration_id
        ORDER BY old.record_id,old.created_at DESC;
        INSERT INTO stewardship_schedule_selection(id,definition_id,configuration_id,previous_revision_id,selected_revision_id,version,actor_id,correlation_id)
        SELECT gen_random_uuid(),d.id,p.configuration_id,NULL,d.current_revision_id,1,d.actor_id,d.correlation_id
        FROM stewardship_schedule_definition d JOIN stewardship_campaign c ON c.id=d.campaign_id
        JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id;
    """)


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0006_runtime_guards")]
    operations = [
        migrations.RunPython(backfill_definitions, migrations.RunPython.noop),
        mutable_guard_v1(
            "stewardship_schedule_definition", frozen_fields=("campaign_id", "kind")
        ),
        mutable_guard_v1(
            "stewardship_schedule_occurrence",
            frozen_fields=(
                "definition_id",
                "revision_id",
                "mode",
                "routing",
                "target",
                "slot",
                "due_at",
                "occurrence_key",
            ),
        ),
        immutable_guard_v1("stewardship_schedule_selection"),
        immutable_guard_v1("stewardship_occurrence_transition"),
        immutable_guard_v1("stewardship_schedule_fulfillment"),
        immutable_guard_v1("stewardship_postclose_resolution"),
        immutable_guard_v1("stewardship_restore_hold_resolution"),
        mutable_guard_v1(
            "stewardship_restore_delivery_hold",
            frozen_fields=(
                "restore_id",
                "definition_id",
                "mode",
                "target",
                "slot",
                "backup_at",
                "window_start",
                "window_end",
                "discovery",
            ),
        ),
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER stewardship_fulfillment_guard_v1 ON stewardship_schedule_fulfillment;
DROP FUNCTION stewardship_fulfillment_guard_v1();
DROP TRIGGER stewardship_occurrence_history_v1 ON stewardship_schedule_occurrence;
DROP FUNCTION stewardship_occurrence_history_v1();
DROP TRIGGER stewardship_occurrence_guard_v1 ON stewardship_schedule_occurrence;
DROP FUNCTION stewardship_occurrence_guard_v1();
DROP TRIGGER zzz_stewardship_schedules_activate_v1 ON stewardship_system_configuration;
DROP FUNCTION stewardship_schedules_activate_v1();
DROP TRIGGER stewardship_schedule_selection_effect_v1 ON stewardship_schedule_definition;
DROP FUNCTION stewardship_schedule_selection_effect_v1();
DROP TRIGGER stewardship_schedule_definition_v1 ON stewardship_schedule_definition;
DROP FUNCTION stewardship_schedule_definition_v1();
""",
        ),
        migrations.RunSQL(
            migrations.RunSQL.noop,
            reverse_sql="""
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_schedule_definition)
       OR EXISTS(SELECT 1 FROM stewardship_postclose_resolution)
       OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold) THEN
        RAISE EXCEPTION 'Campaign history prevents schedule reversal' USING ERRCODE='23514';
    END IF;
END $$;
""",
        ),
    ]
