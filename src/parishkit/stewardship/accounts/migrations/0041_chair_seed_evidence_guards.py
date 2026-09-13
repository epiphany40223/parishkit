"""Bind retained seed identity to an applied assignment and exact current source.

No online identity receives INSERT here yet. ADM-07 must introduce the confirmed
request owner before granting that authority; a refresh can only read evidence.
These guards also protect against bypasses of that future owning Python code.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_chair_seed_evidence_guard_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE assignment stewardship_ministry_assignment%ROWTYPE;
        evidence jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Chair seed evidence requires work ownership'
            USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM stewardship_source_current
        WHERE snapshot_id=NEW.snapshot_id AND organization_id=NEW.organization_id
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Chair seed evidence requires current source ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT a.* INTO assignment FROM stewardship_ministry_assignment a
        JOIN stewardship_system_configuration runtime
            ON runtime.active_configuration_id=a.configuration_id
        JOIN stewardship_applied_integration integration
            ON integration.configuration_id=a.configuration_id
            AND integration.kind='parishsoft'
            AND integration.settings->>'organization_id'=NEW.organization_id::text
        WHERE a.id=NEW.assignment_id;
    IF assignment.id IS NULL OR assignment.source<>'chair-seed'
       OR assignment.record_id IS DISTINCT FROM NEW.assignment_record_id THEN
        RAISE EXCEPTION 'Chair seed evidence requires its applied seeded assignment'
            USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(roster_key ORDER BY roster_key) INTO evidence
        FROM stewardship_current_chair
        WHERE snapshot_id=NEW.snapshot_id AND organization_id=NEW.organization_id
          AND member_duid=NEW.member_duid AND ministry_duid=assignment.ministry_duid
          AND email=assignment.email;
    IF evidence IS NULL OR evidence IS DISTINCT FROM NEW.roster_keys
       OR EXISTS (SELECT 1 FROM stewardship_ministry_activity
          WHERE configuration_id=assignment.configuration_id
            AND organization_id=NEW.organization_id
            AND ministry_duid=assignment.ministry_duid AND NOT active) THEN
        RAISE EXCEPTION 'Chair seed evidence requires the exact current relationship'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_chair_seed_evidence_insert
BEFORE INSERT ON stewardship_chair_seed_evidence
FOR EACH ROW EXECUTE FUNCTION stewardship_chair_seed_evidence_guard_v1();
"""

REVERSE = """
LOCK TABLE stewardship_chair_seed_evidence IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_chair_seed_evidence) THEN
        RAISE EXCEPTION 'Retained Chair seed evidence prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_chair_seed_evidence_insert ON stewardship_chair_seed_evidence;
DROP FUNCTION stewardship_chair_seed_evidence_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0040_chair_seed_evidence"),
        ("stewardship_source", "0017_current_chair_projection"),
    ]
    operations = [
        immutable_guard_v1("stewardship_chair_seed_evidence"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
