"""Scheduled refresh provenance must match a due slot and current applied scope."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1

FORWARD = """
CREATE FUNCTION stewardship_refresh_tick_guard_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        command stewardship_source_refresh_command%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
        zone text;
        nightly text;
        scope_digest text;
        expected_key text;
        local_day date;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736229 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Refresh tick requires scheduler and work ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
    IF NOT FOUND OR runtime.active_configuration_id
       IS DISTINCT FROM NEW.configuration_id
       OR runtime.restore_review_required
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
                  WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Refresh tick requires current admitted configuration'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO command FROM stewardship_source_refresh_command
        WHERE id=NEW.command_id;
    SELECT * INTO request FROM stewardship_source_refresh_request
        WHERE id=command.request_id;
    IF command.id IS NULL OR request.id IS NULL OR NEW.actor_id IS NOT NULL
       OR command.actor_id IS NOT NULL OR command.cause NOT IN ('nightly','delta')
       OR request.campaign_id IS DISTINCT FROM runtime.current_campaign_id
       OR request.window_canonical IS DISTINCT FROM
          stewardship_source_current_window_v1(request.campaign_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_applied_integration
          WHERE configuration_id=NEW.configuration_id AND kind='parishsoft'
            AND settings->>'organization_id'=request.organization_id::text) THEN
        RAISE EXCEPTION 'Refresh tick command does not match its current source scope'
            USING ERRCODE='23514';
    END IF;
    IF runtime.current_campaign_id IS NULL THEN
        SELECT timezone INTO zone FROM stewardship_parish
            WHERE configuration_id=NEW.configuration_id;
    ELSE
        SELECT cfg.timezone INTO zone FROM stewardship_campaign c
            JOIN stewardship_campaign_configuration cfg
                ON cfg.id=c.active_configuration_id
            WHERE c.id=runtime.current_campaign_id;
    END IF;
    SELECT coalesce(settings->>'nightly_time','02:00') INTO nightly
        FROM stewardship_applied_integration
        WHERE configuration_id=NEW.configuration_id AND kind='parishsoft';
    IF NEW.timezone IS DISTINCT FROM zone OR NEW.nightly_time IS DISTINCT FROM nightly
       OR NEW.due_at > clock_timestamp()
       OR NEW.due_at <> date_trunc('second',NEW.due_at) THEN
        RAISE EXCEPTION 'Refresh tick is not due under its applied cadence'
            USING ERRCODE='23514';
    END IF;
    IF command.cause='delta' THEN
        IF extract(second FROM NEW.due_at) <> 0
           OR mod(extract(minute FROM NEW.due_at AT TIME ZONE 'UTC')::int,15) <> 0 THEN
            RAISE EXCEPTION 'Delta tick must be a quarter-hour UTC slot'
                USING ERRCODE='23514';
        END IF;
    ELSE
        local_day := (NEW.due_at AT TIME ZONE zone)::date;
        IF NEW.due_at <> stewardship_resolve_local_v1(local_day+nightly::time,zone)
           AND NEW.due_at <> stewardship_resolve_local_v1(
               (local_day-1)+nightly::time,zone) THEN
            RAISE EXCEPTION 'Nightly tick must use canonical local-time resolution'
                USING ERRCODE='23514';
        END IF;
    END IF;
    scope_digest := encode(sha256(convert_to(stewardship_source_canonical(
        jsonb_build_object('organization_id',request.organization_id,
                           'window_digest',request.window_digest)),'UTF8')),'hex');
    expected_key := encode(sha256(convert_to(stewardship_source_canonical(
        jsonb_build_object('schema','source-refresh-slot-v1',
            'scope_fingerprint',scope_digest,'timezone',zone,
            'nightly_time',CASE WHEN command.cause='nightly' THEN nightly ELSE NULL END,
            'cause',command.cause,'due_at',
            to_char(NEW.due_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||'+00:00')
        ),'UTF8')),'hex');
    IF NEW.slot_key IS DISTINCT FROM expected_key THEN
        RAISE EXCEPTION 'Refresh tick identity does not match its exact inputs'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_refresh_tick_insert
BEFORE INSERT ON stewardship_source_refresh_tick
FOR EACH ROW EXECUTE FUNCTION stewardship_refresh_tick_guard_v1();
"""

REVERSE = """
LOCK TABLE stewardship_source_refresh_tick IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stewardship_source_refresh_tick) THEN
        RAISE EXCEPTION 'Scheduled refresh history prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_refresh_tick_insert ON stewardship_source_refresh_tick;
DROP FUNCTION stewardship_refresh_tick_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0015_sourcerefreshtick")]
    operations = [
        immutable_guard_v1("stewardship_source_refresh_tick"),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
