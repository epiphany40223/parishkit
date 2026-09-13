"""Refresh scope is immutable and must match its task and applied source window."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_refresh_request_guard_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        campaign stewardship_campaign%ROWTYPE;
        values jsonb;
        periods jsonb := '[]'::jsonb;
        expected jsonb;
        organization text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Refresh requires its owning work order' USING ERRCODE='23514';
    END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
    IF NOT FOUND OR runtime.active_configuration_id
       IS DISTINCT FROM NEW.configuration_id
       OR runtime.current_campaign_id IS DISTINCT FROM NEW.campaign_id
       OR runtime.restore_review_required
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
                  WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Refresh configuration is not current' USING ERRCODE='23514';
    END IF;
    SELECT settings->>'organization_id' INTO organization
      FROM stewardship_applied_integration
      WHERE configuration_id=NEW.configuration_id AND kind='parishsoft';
    IF organization IS DISTINCT FROM NEW.organization_id::text
       OR EXISTS (SELECT 1 FROM stewardship_source_current
           WHERE organization_id IS NOT NULL AND organization_id <> NEW.organization_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_root_id
           AND root_id=id AND parent_id IS NULL AND task_type='source_refresh'
           AND domain_request_id=NEW.id AND state='queued' AND attempt=0
           AND initiated_by_id IS NOT DISTINCT FROM NEW.actor_id) THEN
        RAISE EXCEPTION 'Refresh tenant or task binding is invalid'
            USING ERRCODE='23514';
    END IF;
    IF NEW.campaign_id IS NOT NULL THEN
        SELECT * INTO campaign FROM stewardship_campaign WHERE id=NEW.campaign_id;
        IF NOT FOUND OR campaign.state NOT IN
           ('draft','scheduled','active','closed','archived') THEN
            RAISE EXCEPTION 'Refresh campaign is unavailable' USING ERRCODE='23514';
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
    expected := jsonb_build_object('campaign_id',NEW.campaign_id,'periods',periods);
    IF octet_length(NEW.window_canonical) > 1048576
       OR NEW.window_canonical IS DISTINCT FROM stewardship_source_canonical(expected)
       OR NEW.window_digest IS DISTINCT FROM
           encode(sha256(convert_to(NEW.window_canonical,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'Refresh window is not the current exact scope'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_refresh_request_insert
BEFORE INSERT ON stewardship_source_refresh_request
FOR EACH ROW EXECUTE FUNCTION stewardship_refresh_request_guard_v1();

CREATE FUNCTION stewardship_refresh_command_guard_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM stewardship_source_refresh_request r
           JOIN stewardship_task_run t ON t.root_id=r.task_root_id
           WHERE r.id=NEW.request_id AND (r.kind='full' OR r.kind=NEW.kind)
             AND t.state IN ('queued','retry_wait','abandoned')) THEN
        RAISE EXCEPTION 'Refresh command requires waiting compatible work'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_refresh_command_insert
BEFORE INSERT ON stewardship_source_refresh_command
FOR EACH ROW EXECUTE FUNCTION stewardship_refresh_command_guard_v1();
"""

REVERSE = """
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_source_refresh_request)
       OR EXISTS (SELECT 1 FROM stewardship_source_refresh_command) THEN
        RAISE EXCEPTION 'Refresh history prevents scope schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_refresh_command_insert ON stewardship_source_refresh_command;
DROP FUNCTION stewardship_refresh_command_guard_v1();
DROP TRIGGER stewardship_refresh_request_insert ON stewardship_source_refresh_request;
DROP FUNCTION stewardship_refresh_request_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [
        (
            "stewardship_source",
            "0008_sourcerefreshrequest_sourcerefreshcommand_and_more",
        )
    ]
    operations = [
        immutable_guard_v1("stewardship_source_refresh_request"),
        immutable_guard_v1("stewardship_source_refresh_command"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
