-- Frozen forward migration file 0011 (the repository-wide file sequence;
-- Django's stewardship_source.0005): the integrated refresh schedule's tick
-- guard and the schedule-change catch-up cause (#632, delivery step 2a of
-- docs/plans/stewardship/refresh-schedule.md). This file is installed by
-- source/migrations/0005_refresh_schedule.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copy of the replaced guard still equals the
-- fresh-install baseline's (schema/functions.sql). It follows 0010 (#641). A
-- fresh install runs the baseline, 0002 to 0010 and then this; the baseline
-- already carries the widened cause check and the guard body, and this file
-- creates the one new function, so the install ends in the same catalog as
-- an upgraded database.
--
-- stewardship_refresh_schedule_v1 normalizes a ParishSoft integration's
-- stored schedule settings with the defaults cadence.refresh_settings
-- applies (including whether the schedule was saved with its rules), so two
-- configurations with equal results have the same schedule.
--
-- The command cause check admits 'catch_up': one full refresh the scheduler
-- requests when a new schedule takes effect while a full slot of the
-- previous one is already overdue.
--
-- The refresh-tick guard
-- - admits a delta tick under delta_refresh 'times' only at one of the
--   listed local quick_refresh_times, resolved like the full times; every
--   other delta cadence is checked exactly as before;
-- - admits a 'catch_up' tick only at the instant the current schedule took
--   effect (the activation of the earliest configuration in the latest
--   unbroken run of activations with an equal normalized schedule), only
--   when an earlier schedule exists, at most one at that instant, and with
--   the nightly time recorded as a delta tick records it.
-- Neither the guard nor the new function is SECURITY DEFINER (the baseline
-- has no ALTER ... SECURITY DEFINER for the guard), and both name their
-- search_path in their own header, which CREATE OR REPLACE keeps. No
-- existing row changes. Reversing needs its own forward migration, after any
-- catch-up commands have been accounted for.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The normalized refresh schedule of a stored settings document.
CREATE FUNCTION public.stewardship_refresh_schedule_v1(settings jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
SELECT jsonb_build_object(
    'nightly_time', coalesce(settings->>'nightly_time','02:00'),
    'full_refresh', coalesce(settings->>'full_refresh','daily'),
    'full_refresh_times',
        -- Nested so jsonb_array_length never sees a scalar.
        CASE WHEN jsonb_typeof(settings->'full_refresh_times')='array' THEN
                 CASE WHEN jsonb_array_length(settings->'full_refresh_times')>0
                      THEN settings->'full_refresh_times'
                      ELSE jsonb_build_array(coalesce(settings->>'nightly_time','02:00'))
                 END
             ELSE jsonb_build_array(coalesce(settings->>'nightly_time','02:00'))
        END,
    'delta_refresh', coalesce(settings->>'delta_refresh','quarter_hour'),
    'quick_refresh_times',
        CASE WHEN jsonb_typeof(settings->'quick_refresh_times')='array'
             THEN settings->'quick_refresh_times'
             ELSE '[]'::jsonb
        END,
    'rules', settings ? 'refresh_rules')
$$;

-- The command cause check, exactly as the fresh-install tables.sql defines it.
ALTER TABLE public.stewardship_source_refresh_command DROP CONSTRAINT source_refresh_command_cause;
ALTER TABLE public.stewardship_source_refresh_command ADD CONSTRAINT source_refresh_command_cause CHECK (((cause)::text = ANY ((ARRAY['manual'::character varying, 'nightly'::character varying, 'initial'::character varying, 'delta'::character varying, 'fallback'::character varying, 'catch_up'::character varying])::text[])));

-- The guard, exactly as the fresh-install functions.sql defines it.
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
        quick jsonb;
        schedule jsonb;
        boundary bigint;
        effective timestamptz;
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
       OR command.actor_id IS NOT NULL
       OR command.cause NOT IN ('nightly','delta','catch_up')
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
    -- an older document names no list), the delta cadence (#465) and the
    -- listed local quick times (#632).
    SELECT coalesce(settings->>'nightly_time','02:00'),
           coalesce(settings->>'full_refresh','daily'),
           coalesce(settings->'full_refresh_times',
                    jsonb_build_array(coalesce(settings->>'nightly_time','02:00'))),
           coalesce(settings->>'delta_refresh','quarter_hour'),
           settings->'quick_refresh_times',
           stewardship_refresh_schedule_v1(settings)
        INTO nightly, frequency, times, deltas, quick, schedule
        FROM stewardship_applied_integration
        WHERE configuration_id=NEW.configuration_id AND kind='parishsoft';
    -- A full tick names the configured time it fell due at, which must be
    -- the nightly time or one of the listed times; a delta tick carries the
    -- nightly time, as it always has, and so does a catch-up tick.
    IF NEW.timezone IS DISTINCT FROM zone
       OR (command.cause IN ('delta','catch_up')
           AND NEW.nightly_time IS DISTINCT FROM nightly)
       OR (command.cause='nightly' AND NEW.nightly_time IS DISTINCT FROM nightly
           AND NOT (jsonb_typeof(times)='array' AND times ? NEW.nightly_time::text))
       OR NEW.due_at > clock_timestamp()
       OR NEW.due_at <> date_trunc('second',NEW.due_at) THEN
        RAISE EXCEPTION 'Refresh tick is not due under its applied cadence'
            USING ERRCODE='23514';
    END IF;
    IF command.cause='delta' AND deltas='times' THEN
        -- Listed quick times (#632) are parish-local wall times resolved
        -- like the full times: the tick must fall at one of them on its
        -- local day or the day before.
        local_day := (NEW.due_at AT TIME ZONE
                      public.stewardship_timezone_name_v1(zone))::date;
        IF jsonb_typeof(quick) IS DISTINCT FROM 'array' OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(quick) AS listed(value)
               WHERE NEW.due_at IN (
                   stewardship_resolve_local_v1(local_day+listed.value::time,zone),
                   stewardship_resolve_local_v1((local_day-1)+listed.value::time,zone)))
        THEN
            RAISE EXCEPTION 'Delta tick must match its applied delta cadence'
                USING ERRCODE='23514';
        END IF;
    ELSIF command.cause='delta' THEN
        -- Deltas fall on UTC quarter hours or hours, or not at all.
        IF deltas='off' OR extract(second FROM NEW.due_at) <> 0
           OR mod(extract(minute FROM NEW.due_at AT TIME ZONE 'UTC')::int,
                  CASE deltas WHEN 'hourly' THEN 60 ELSE 15 END) <> 0 THEN
            RAISE EXCEPTION 'Delta tick must match its applied delta cadence'
                USING ERRCODE='23514';
        END IF;
    ELSIF command.cause='catch_up' THEN
        -- The schedule-change catch-up (#632) is due exactly when the
        -- current schedule took effect: the activation of the earliest
        -- configuration in the latest unbroken run of activations whose
        -- schedule equals the current one (bootstrap configurations
        -- ignored), as data_age.schedule_runs finds it. A schedule that
        -- never changed has no catch-up, and there is at most one.
        SELECT max(a.sequence) INTO boundary
            FROM stewardship_config_activation a
            JOIN stewardship_configuration_version v ON v.id=a.configuration_id
            LEFT JOIN stewardship_applied_integration i
                ON i.configuration_id=a.configuration_id AND i.kind='parishsoft'
            WHERE v.validation_schema<>'bootstrap-policy-v1'
              AND stewardship_refresh_schedule_v1(i.settings)
                  IS DISTINCT FROM schedule;
        SELECT date_trunc('second',min(a.created_at)) INTO effective
            FROM stewardship_config_activation a
            JOIN stewardship_configuration_version v ON v.id=a.configuration_id
            WHERE v.validation_schema<>'bootstrap-policy-v1' AND a.sequence>boundary;
        IF boundary IS NULL OR effective IS DISTINCT FROM NEW.due_at
           OR EXISTS (SELECT 1 FROM stewardship_source_refresh_tick t
                      JOIN stewardship_source_refresh_command c ON c.id=t.command_id
                      WHERE c.cause='catch_up' AND t.due_at=NEW.due_at) THEN
            RAISE EXCEPTION 'Catch-up tick must be due when its schedule took effect'
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

-- Refuse to commit unless every part above is installed.
DO $check$
BEGIN
    IF position('''catch_up''' IN pg_get_constraintdef(
           (SELECT oid FROM pg_constraint WHERE conname='source_refresh_command_cause'
              AND conrelid='public.stewardship_source_refresh_command'::regclass)))=0 THEN
        RAISE EXCEPTION 'source_refresh_command_cause does not admit catch_up';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_refresh_schedule_v1'
                     AND NOT p.prosecdef AND p.provolatile='i' AND p.proisstrict
                     AND p.prosrc LIKE '%quick_refresh_times%') THEN
        RAISE EXCEPTION 'stewardship_refresh_schedule_v1 is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public'
                     AND p.proname='stewardship_refresh_tick_guard_v1'
                     AND NOT p.prosecdef
                     AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND p.prosrc LIKE '%''nightly'',''delta'',''catch_up''%'
                     AND p.prosrc LIKE '%Catch-up tick must be due when its schedule took effect%'
                     AND p.prosrc LIKE '%jsonb_array_elements_text(quick)%') THEN
        RAISE EXCEPTION 'stewardship_refresh_tick_guard_v1 was not replaced';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid='public.stewardship_source_refresh_tick'::regclass
                     AND tgname='stewardship_refresh_tick_insert' AND NOT tgisinternal) THEN
        RAISE EXCEPTION 'The refresh tick trigger is missing';
    END IF;
END
$check$;
