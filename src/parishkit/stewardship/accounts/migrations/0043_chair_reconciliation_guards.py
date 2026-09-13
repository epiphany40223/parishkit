"""Validate exact source/configuration ownership and apply all chair effects.

The receipt owns overlay and review changes in one transaction. It contains
only opaque assignment references and closed decision vocabulary, not contacts.
Manual assignments and authoritative configured roles are never rewritten.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)

FORWARD = """
CREATE FUNCTION stewardship_chair_decisions_v1(configuration uuid)
RETURNS jsonb LANGUAGE sql STABLE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'assignment_record_id',record_id::text,
        'active',reason='current_chair','reason',reason) ORDER BY record_id),
        '[]'::jsonb)
    FROM (
        SELECT assignment.record_id, CASE
            WHEN evidence.id IS NULL THEN 'missing_binding'
            WHEN evidence.organization_id::text IS DISTINCT FROM
                integration.settings->>'organization_id' THEN 'organization_changed'
            WHEN EXISTS (SELECT 1 FROM stewardship_ministry_activity activity
                WHERE activity.configuration_id=configuration
                  AND activity.organization_id=evidence.organization_id
                  AND activity.ministry_duid=assignment.ministry_duid
                  AND NOT activity.active) THEN 'ministry_inactive'
            WHEN EXISTS (SELECT 1 FROM stewardship_current_chair chair
                WHERE chair.organization_id=evidence.organization_id
                  AND chair.member_duid=evidence.member_duid
                  AND chair.ministry_duid=assignment.ministry_duid
                  AND chair.email=assignment.email) THEN 'current_chair'
            ELSE 'relationship_missing' END AS reason
        FROM stewardship_ministry_assignment assignment
        LEFT JOIN stewardship_chair_seed_evidence evidence
            ON evidence.assignment_record_id=assignment.record_id
        LEFT JOIN stewardship_applied_integration integration
            ON integration.configuration_id=configuration
            AND integration.kind='parishsoft'
        WHERE assignment.configuration_id=configuration
            AND assignment.source='chair-seed'
    ) decisions;
$$;

CREATE FUNCTION stewardship_chair_reconciliation_guard_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.owner_transaction IS DISTINCT FROM txid_current()
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM stewardship_system_configuration
          WHERE active_configuration_id=NEW.configuration_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_source_current
          WHERE snapshot_id=NEW.snapshot_id) THEN
        RAISE EXCEPTION 'Chair reconciliation requires current ordered inputs'
            USING ERRCODE='23514';
    END IF;
    IF NEW.activation_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_config_activation
            WHERE id=NEW.activation_id AND configuration_id=NEW.configuration_id
              AND actor_id IS NOT DISTINCT FROM NEW.actor_id
              AND correlation_id=NEW.correlation_id
              AND xmin=(pg_current_xact_id()::text)::xid) THEN
            RAISE EXCEPTION 'Chair reconciliation requires its owning activation'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM stewardship_source_snapshot snapshot
            JOIN stewardship_source_lease lease ON lease.owner_id=snapshot.task_id
                AND lease.fence=snapshot.source_fence
            JOIN stewardship_task_run task ON task.id=lease.owner_id
            WHERE snapshot.id=NEW.snapshot_id AND snapshot.task_id=NEW.source_owner_id
              AND snapshot.source_fence=NEW.source_fence
              AND lease.worker_id=NEW.actor_id AND lease.phase IN ('full','delta')
              AND lease.expires_at>clock_timestamp() AND task.state='running'
              AND task.fence=lease.task_fence AND task.worker_id=lease.worker_id
              AND task.correlation_id=NEW.correlation_id
              AND task.lease_expires_at>clock_timestamp()) THEN
            RAISE EXCEPTION 'Chair reconciliation requires its live source owner'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.decisions IS DISTINCT FROM
        stewardship_chair_decisions_v1(NEW.configuration_id) THEN
        RAISE EXCEPTION 'Chair reconciliation decisions differ from exact evidence'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_chair_reconciliation_insert
BEFORE INSERT ON stewardship_chair_reconciliation
FOR EACH ROW EXECUTE FUNCTION stewardship_chair_reconciliation_guard_v1();

CREATE FUNCTION stewardship_chair_reconciliation_effects_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE decision jsonb;
        assignment uuid;
BEGIN
    FOR decision IN SELECT * FROM jsonb_array_elements(NEW.decisions) LOOP
        assignment := (decision->>'assignment_record_id')::uuid;
        INSERT INTO stewardship_assignment_overlay
            (id,assignment_record_id,active,source_snapshot_id,reason,version,
             actor_id,correlation_id)
        VALUES (gen_random_uuid(),assignment,(decision->>'active')::boolean,
            NEW.snapshot_id,decision->>'reason',1,NEW.actor_id,NEW.correlation_id)
        ON CONFLICT (assignment_record_id) DO UPDATE SET
            active=EXCLUDED.active,source_snapshot_id=EXCLUDED.source_snapshot_id,
            reason=EXCLUDED.reason,version=stewardship_assignment_overlay.version+1,
            actor_id=EXCLUDED.actor_id,correlation_id=EXCLUDED.correlation_id;
        IF (decision->>'active')::boolean THEN
            UPDATE stewardship_chair_review SET closed_by_id=NEW.id,
                latest_by_id=NEW.id,close_reason='relationship_returned',
                version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
                WHERE assignment_record_id=assignment AND closed_by_id IS NULL;
        ELSE
            UPDATE stewardship_chair_review SET latest_by_id=NEW.id,version=version+1,
                actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
                WHERE assignment_record_id=assignment AND closed_by_id IS NULL;
            IF NOT FOUND THEN
                INSERT INTO stewardship_chair_review
                    (id,assignment_record_id,opened_by_id,latest_by_id,close_reason,
                     version,actor_id,correlation_id)
                VALUES (gen_random_uuid(),assignment,NEW.id,NEW.id,'',1,
                        NEW.actor_id,NEW.correlation_id);
            END IF;
        END IF;
    END LOOP;
    UPDATE stewardship_chair_review review SET closed_by_id=NEW.id,latest_by_id=NEW.id,
        close_reason='assignment_removed',version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
        WHERE review.closed_by_id IS NULL AND NOT EXISTS (
            SELECT 1 FROM stewardship_ministry_assignment assignment
            WHERE assignment.configuration_id=NEW.configuration_id
              AND assignment.source='chair-seed'
              AND assignment.record_id=review.assignment_record_id);
    INSERT INTO stewardship_audit_event
        (id,event_type,subject_id,actor_id,correlation_id,ownership_scope,parish_id)
    SELECT NEW.id,'chair_reconciled',NEW.id,NEW.actor_id,NEW.correlation_id,
        'parish',id FROM stewardship_parish WHERE configuration_id=NEW.configuration_id;
    RETURN NULL;
END;
$$;
CREATE TRIGGER stewardship_chair_reconciliation_effects
AFTER INSERT ON stewardship_chair_reconciliation
FOR EACH ROW EXECUTE FUNCTION stewardship_chair_reconciliation_effects_v1();

CREATE FUNCTION stewardship_chair_review_owner_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE receipt stewardship_chair_reconciliation%ROWTYPE;
        decision jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Chair review history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.closed_by_id IS NOT NULL THEN
        RAISE EXCEPTION 'Closed Chair review history cannot change'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO receipt FROM stewardship_chair_reconciliation
        WHERE id=NEW.latest_by_id AND owner_transaction=txid_current();
    IF receipt.id IS NULL THEN
        RAISE EXCEPTION 'Chair review requires its current reconciliation owner'
            USING ERRCODE='23514';
    END IF;
    SELECT value INTO decision FROM jsonb_array_elements(receipt.decisions)
        WHERE (value->>'assignment_record_id')::uuid=NEW.assignment_record_id;
    IF (TG_OP='INSERT' AND (
        NEW.opened_by_id<>receipt.id OR NEW.closed_by_id IS NOT NULL))
       OR (NEW.closed_by_id IS NULL
           AND decision->'active' IS DISTINCT FROM 'false'::jsonb)
       OR (NEW.closed_by_id IS NOT NULL AND (
           NEW.closed_by_id<>receipt.id
           OR (NEW.close_reason='relationship_returned'
               AND decision->'active' IS DISTINCT FROM 'true'::jsonb)
           OR (NEW.close_reason='assignment_removed' AND decision IS NOT NULL))) THEN
        RAISE EXCEPTION 'Chair review differs from its exact reconciliation decision'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_chair_review_owner
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_chair_review
FOR EACH ROW EXECUTE FUNCTION stewardship_chair_review_owner_v1();
"""

REVERSE = """
LOCK TABLE stewardship_chair_reconciliation,stewardship_chair_review
    IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_chair_reconciliation)
       OR EXISTS (SELECT 1 FROM stewardship_chair_review) THEN
        RAISE EXCEPTION 'Retained Chair reconciliation prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_chair_review_owner ON stewardship_chair_review;
DROP FUNCTION stewardship_chair_review_owner_v1();
DROP TRIGGER stewardship_chair_reconciliation_effects
    ON stewardship_chair_reconciliation;
DROP FUNCTION stewardship_chair_reconciliation_effects_v1();
DROP TRIGGER stewardship_chair_reconciliation_insert
    ON stewardship_chair_reconciliation;
DROP FUNCTION stewardship_chair_reconciliation_guard_v1();
DROP FUNCTION stewardship_chair_decisions_v1(uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0042_chair_reconciliation")]
    operations = [
        immutable_guard_v1("stewardship_chair_reconciliation"),
        mutable_guard_v1(
            "stewardship_chair_review",
            frozen_fields=("assignment_record_id", "opened_by_id"),
            write_once_fields=("closed_by_id",),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
