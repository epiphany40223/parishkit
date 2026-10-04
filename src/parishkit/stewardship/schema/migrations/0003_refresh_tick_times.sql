-- Frozen forward migration file 0003 (the repository-wide file sequence;
-- Django's stewardship_source.0002): the refresh-tick guard accepts the
-- configured full-refresh times and delta cadence (#465). This file is
-- installed by source/migrations/0002_refresh_tick_times.py and must never
-- change once released; tests/stewardship/test_schema_migration_files.py pins
-- its digest and checks that this copy of the guard still equals the
-- fresh-install baseline's (schema/functions.sql). A later change to the guard
-- gets its own numbered file. A fresh install runs 0001, 0002 (campaigns'
-- Family engagement) and then this; functions.sql already carries this body,
-- so the install ends in the same catalog as an upgraded database.
--
-- Before #465 the guard required a scheduled full tick's time to equal the
-- applied nightly_time. Full refreshes may now also run at the other local
-- times listed in full_refresh_times, and deltas may run hourly or not at all
-- (delta_refresh), so the guard checks a full tick's time against the list and
-- uses that time in the slot identity and the DST check. Reversing would also
-- have to remove the ticks the new guard admitted, so it is forward-only.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_refresh_tick_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        command stewardship_source_refresh_command%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
        zone text;
        nightly text;
        frequency text;
        times jsonb;
        deltas text;
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
    -- The applied schedule: the nightly time, the full-refresh frequency,
    -- the configured local full-refresh times (the nightly time alone when
    -- an older document names no list) and the delta cadence (#465).
    SELECT coalesce(settings->>'nightly_time','02:00'),
           coalesce(settings->>'full_refresh','daily'),
           coalesce(settings->'full_refresh_times',
                    jsonb_build_array(coalesce(settings->>'nightly_time','02:00'))),
           coalesce(settings->>'delta_refresh','quarter_hour')
        INTO nightly, frequency, times, deltas
        FROM stewardship_applied_integration
        WHERE configuration_id=NEW.configuration_id AND kind='parishsoft';
    -- A full tick names the configured time it fell due at, which must be
    -- the nightly time or one of the listed times; a delta tick carries the
    -- nightly time, as it always has.
    IF NEW.timezone IS DISTINCT FROM zone
       OR (command.cause='delta' AND NEW.nightly_time IS DISTINCT FROM nightly)
       OR (command.cause='nightly' AND NEW.nightly_time IS DISTINCT FROM nightly
           AND NOT (jsonb_typeof(times)='array' AND times ? NEW.nightly_time::text))
       OR NEW.due_at > clock_timestamp()
       OR NEW.due_at <> date_trunc('second',NEW.due_at) THEN
        RAISE EXCEPTION 'Refresh tick is not due under its applied cadence'
            USING ERRCODE='23514';
    END IF;
    IF command.cause='delta' THEN
        -- Deltas fall on UTC quarter hours or hours, or not at all.
        IF deltas='off' OR extract(second FROM NEW.due_at) <> 0
           OR mod(extract(minute FROM NEW.due_at AT TIME ZONE 'UTC')::int,
                  CASE deltas WHEN 'hourly' THEN 60 ELSE 15 END) <> 0 THEN
            RAISE EXCEPTION 'Delta tick must match its applied delta cadence'
                USING ERRCODE='23514';
        END IF;
    ELSIF frequency IN ('hourly','quarter_hour') THEN
        -- A frequent full refresh uses UTC hour or quarter-hour boundaries.
        IF extract(second FROM NEW.due_at) <> 0
           OR mod(extract(minute FROM NEW.due_at AT TIME ZONE 'UTC')::int,
                  CASE frequency WHEN 'hourly' THEN 60 ELSE 15 END) <> 0 THEN
            RAISE EXCEPTION 'Full refresh tick must match its applied frequency'
                USING ERRCODE='23514';
        END IF;
    ELSE
        local_day := (NEW.due_at AT TIME ZONE
                      public.stewardship_timezone_name_v1(zone))::date;
        IF NEW.due_at <> stewardship_resolve_local_v1(
               local_day+NEW.nightly_time::time,zone)
           AND NEW.due_at <> stewardship_resolve_local_v1(
               (local_day-1)+NEW.nightly_time::time,zone) THEN
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
            'nightly_time',CASE WHEN command.cause='nightly'
                                THEN NEW.nightly_time::text ELSE NULL END,
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

-- Refuse to commit unless the new body is installed, so an upgrade cannot
-- report success while the old guard is still in place.
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public'
                     AND p.proname='stewardship_refresh_tick_guard_v1'
                     AND p.prosrc LIKE '%full_refresh_times%'
                     AND p.prosrc LIKE '%applied delta cadence%') THEN
        RAISE EXCEPTION 'stewardship_refresh_tick_guard_v1 was not replaced';
    END IF;
END
$check$;
