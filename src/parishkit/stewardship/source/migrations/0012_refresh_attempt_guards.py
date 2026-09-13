"""Bind concrete source attempts and completion to current scope and credentials."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_source_current_window_v1(campaign_id uuid)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE campaign stewardship_campaign%ROWTYPE;
        values jsonb;
        periods jsonb := '[]'::jsonb;
BEGIN
    IF campaign_id IS NOT NULL THEN
        SELECT * INTO campaign FROM stewardship_campaign WHERE id=campaign_id;
        IF NOT FOUND OR campaign.state NOT IN
            ('draft','scheduled','active','closed','archived') THEN
            RAISE EXCEPTION 'Source campaign is unavailable' USING ERRCODE='23514';
        END IF;
        SELECT c.values INTO values FROM stewardship_campaign_configuration c
            WHERE c.id=campaign.active_configuration_id;
        IF campaign.state <> 'archived' AND values->'modules' ? 'financial'
           AND values->'financial' <> 'null'::jsonb THEN
            periods := jsonb_build_array(
                jsonb_build_object('start',values#>'{financial,start}',
                    'end',values#>'{financial,end}',
                    'funds',values#>'{financial,fund_duids}'),
                jsonb_build_object('start',values#>'{financial,comparison_start}',
                    'end',values#>'{financial,comparison_end}',
                    'funds',values#>'{financial,comparison_fund_duids}'));
        END IF;
    END IF;
    RETURN stewardship_source_canonical(
        jsonb_build_object('campaign_id',campaign_id,'periods',periods));
END;
$$;

CREATE FUNCTION stewardship_refresh_attempt_guard_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_source_refresh_request r
        JOIN stewardship_source_snapshot s ON s.id=NEW.snapshot_id
        JOIN stewardship_task_run t ON t.id=NEW.task_id
        JOIN stewardship_source_lease l ON l.owner_id=t.id
        JOIN stewardship_system_configuration c ON c.active_configuration_id=
            NEW.configuration_id
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        WHERE r.id=NEW.request_id AND i.kind='parishsoft'
          AND i.credential_fingerprint=NEW.credential_fingerprint
          AND i.settings->>'organization_id'=r.organization_id::text
          AND c.current_campaign_id IS NOT DISTINCT FROM r.campaign_id
          AND NOT c.restore_review_required
          AND r.window_canonical=stewardship_source_current_window_v1(r.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.root_id=r.task_root_id AND t.domain_request_id=r.id
          AND t.task_type='source_refresh' AND t.state='running'
          AND t.fence=NEW.task_fence AND t.lease_expires_at > clock_timestamp()
          AND s.task_id=t.id AND s.state='staging' AND s.kind=r.kind
          AND s.organization_id=r.organization_id AND s.source_fence=l.fence
          AND l.phase=r.kind AND l.task_fence=t.fence AND l.worker_id=t.worker_id
          AND l.expires_at > clock_timestamp() AND NEW.actor_id=t.worker_id
       ) THEN
        RAISE EXCEPTION 'Source attempt requires current scope/credential/fences'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_refresh_attempt_insert
BEFORE INSERT ON stewardship_source_refresh_attempt
FOR EACH ROW EXECUTE FUNCTION stewardship_refresh_attempt_guard_v1();

CREATE FUNCTION stewardship_refresh_snapshot_completion_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE attempt stewardship_source_refresh_attempt%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
        base_cursor jsonb;
BEGIN
    IF NEW.state NOT IN ('ready','promoted') OR OLD.state='promoted'
       OR NOT EXISTS (SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
           AND task_type='source_refresh') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO attempt FROM stewardship_source_refresh_attempt
        WHERE snapshot_id=NEW.id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Source completion requires its bound attempt'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO request FROM stewardship_source_refresh_request
        WHERE id=attempt.request_id;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration c
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        JOIN stewardship_task_run t ON t.id=attempt.task_id
        WHERE i.kind='parishsoft'
          AND i.credential_fingerprint=attempt.credential_fingerprint
          AND i.settings->>'organization_id'=request.organization_id::text
          AND c.current_campaign_id IS NOT DISTINCT FROM request.campaign_id
          AND NOT c.restore_review_required
          AND request.window_canonical=
              stewardship_source_current_window_v1(request.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.state='running' AND t.fence=attempt.task_fence
          AND t.lease_expires_at > clock_timestamp()
       ) OR NEW.cursor->>'schema' IS DISTINCT FROM 'source-refresh-v1'
       OR NEW.cursor->>'window_digest' IS DISTINCT FROM request.window_digest
       OR NEW.cursor->>'watermark' IS NULL
       OR (NEW.cursor->>'watermark')::timestamptz IS DISTINCT FROM NEW.started_at
    THEN
        RAISE EXCEPTION 'Source completion scope/credential/cursor is stale'
            USING ERRCODE='23514';
    END IF;
    IF NEW.kind='full' THEN
        IF NEW.cursor->>'full_snapshot_id' IS DISTINCT FROM NEW.id::text
           OR NEW.cursor->>'full_started_at' IS NULL
           OR (NEW.cursor->>'full_started_at')::timestamptz
              IS DISTINCT FROM NEW.started_at THEN
            RAISE EXCEPTION 'Full source cursor is not its own observation'
                USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT cursor INTO base_cursor FROM stewardship_source_snapshot
            WHERE id=NEW.base_id AND state='promoted';
        IF NOT FOUND OR base_cursor->>'schema' IS DISTINCT FROM 'source-refresh-v1'
           OR base_cursor->>'window_digest' IS DISTINCT FROM request.window_digest
           OR NEW.cursor->>'full_snapshot_id'
              IS DISTINCT FROM base_cursor->>'full_snapshot_id'
           OR NEW.cursor->>'full_started_at'
              IS DISTINCT FROM base_cursor->>'full_started_at' THEN
            RAISE EXCEPTION 'Delta source cursor lacks coherent full coverage'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_refresh_snapshot_complete
BEFORE UPDATE ON stewardship_source_snapshot
FOR EACH ROW EXECUTE FUNCTION stewardship_refresh_snapshot_completion_v1();
"""

REVERSE = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_source_refresh_attempt) THEN
        RAISE EXCEPTION 'Source attempt history prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_refresh_snapshot_complete ON stewardship_source_snapshot;
DROP FUNCTION stewardship_refresh_snapshot_completion_v1();
DROP TRIGGER stewardship_refresh_attempt_insert ON stewardship_source_refresh_attempt;
DROP FUNCTION stewardship_refresh_attempt_guard_v1();
DROP FUNCTION stewardship_source_current_window_v1(uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0011_sourcerefreshattempt")]
    operations = [
        immutable_guard_v1("stewardship_source_refresh_attempt"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
