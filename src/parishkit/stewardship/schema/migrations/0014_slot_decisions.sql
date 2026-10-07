-- Frozen forward migration file 0014 (the repository-wide file sequence;
-- Django's stewardship_source.0006): the slot decision record of the
-- integrated refresh schedule (#632, delivery step 2b of
-- docs/plans/stewardship/refresh-schedule.md). This file is installed by
-- source/migrations/0006_slot_decisions.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copy of the replaced refresh-tick guard still equals
-- the fresh-install baseline's (schema/functions.sql). It follows 0013. A
-- fresh install runs the baseline, 0002 to 0013 and then this; the
-- baseline already carries the replaced guard, and this file creates the
-- table and its three functions, so the install ends in the same catalog as
-- an upgraded database.
--
-- stewardship_source_slot_decision: the scheduler's one decision for a due
-- slot that got no refresh: "skipped" around a Family email (never due,
-- never run, and ignored by the data-age alarm) or "held" by a bulk Family
-- send (still due; it may run later as the catch-up). Its slot_key is
-- unique, so a slot has at most one decision, and each row carries the
-- inputs of the tick the slot would have.
--
-- stewardship_refresh_slot_due_v1 holds the checks a tick and a decision
-- share: the zone, the applied cadence for the slot's cause (a listed full
-- or quick time resolved through the shared daylight-saving rules, a
-- legacy UTC quarter hour or hour, or the instant the applied schedule took
-- effect for a catch-up), a due instant not in the future, and the slot
-- identity. The refresh-tick guard now calls it, keeping its refusals'
-- wording, and also refuses a tick for a slot recorded as skipped.
--
-- None of the functions is SECURITY DEFINER; each names its search_path in
-- its own header, which CREATE OR REPLACE keeps. No existing row changes.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The table, as Django renders the model, with the closed name lists in the
-- form PostgreSQL deparses them, so the installed constraints equal the
-- model declarations.
CREATE TABLE "stewardship_source_slot_decision" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "cause" varchar(16) NOT NULL, "due_at" timestamp with time zone NOT NULL, "timezone" varchar(128) NOT NULL, "nightly_time" varchar(5) NOT NULL, "slot_key" varchar(64) NOT NULL UNIQUE, "decision" varchar(8) NOT NULL, "window_cause" varchar(24) NULL, "configuration_id" uuid NOT NULL, CONSTRAINT "source_slot_decision_key" CHECK ("slot_key"::text ~ '^[0-9a-f]{64}$'), CONSTRAINT "source_slot_decision_time" CHECK ("nightly_time"::text ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'));
ALTER TABLE "stewardship_source_slot_decision" ADD CONSTRAINT "source_slot_decision_cause" CHECK (((cause)::text = ANY ((ARRAY['nightly'::character varying, 'delta'::character varying, 'catch_up'::character varying])::text[])));
ALTER TABLE "stewardship_source_slot_decision" ADD CONSTRAINT "source_slot_decision_kind" CHECK (((decision)::text = ANY ((ARRAY['skipped'::character varying, 'held'::character varying])::text[])));
ALTER TABLE "stewardship_source_slot_decision" ADD CONSTRAINT "source_slot_decision_window" CHECK (((((decision)::text = 'skipped'::text) AND ((window_cause)::text = ANY ((ARRAY['reminder_preparing'::character varying, 'reminder_sending'::character varying, 'initial_sending'::character varying])::text[])) AND (NOT ((cause)::text = 'catch_up'::text))) OR (((decision)::text = 'held'::text) AND (window_cause IS NULL))));
ALTER TABLE "stewardship_source_slot_decision" ADD CONSTRAINT "stewardship_source_s_configuration_id_b9b8d620_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_source_slot_decision_correlation_id_c33caf07" ON "stewardship_source_slot_decision" ("correlation_id");
CREATE INDEX "stewardship_source_slot_decision_slot_key_192855bf_like" ON "stewardship_source_slot_decision" ("slot_key" varchar_pattern_ops);
CREATE INDEX "stewardship_source_slot_decision_configuration_id_b9b8d620" ON "stewardship_source_slot_decision" ("configuration_id");
CREATE INDEX "source_slot_decision_due" ON "stewardship_source_slot_decision" ("due_at");

-- The checks a tick and a slot decision share.
CREATE FUNCTION public.stewardship_refresh_slot_due_v1(subject text, config uuid,
        campaign uuid, cause text, due timestamp with time zone, recorded_zone text,
        recorded_time text, organization bigint, window_digest text, slot_key text)
    RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE zone text;
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
        label text := CASE subject WHEN 'tick' THEN 'Refresh tick'
                                   ELSE 'Slot decision' END;
        noun text := CASE subject WHEN 'tick' THEN 'tick' ELSE 'slot decision' END;
BEGIN
    -- The checks a scheduled slot's tick and its slot decision share (#632):
    -- the zone, the applied cadence for the slot's cause, and the identity
    -- derived from the scope, zone, cause, due instant and, for a full slot,
    -- its configured time. ``subject`` ('tick' or 'decision') only words the
    -- refusals; a tick's keep their earlier text.
    IF campaign IS NULL THEN
        SELECT timezone INTO zone FROM stewardship_parish
            WHERE configuration_id=config;
    ELSE
        SELECT cfg.timezone INTO zone FROM stewardship_campaign c
            JOIN stewardship_campaign_configuration cfg
                ON cfg.id=c.active_configuration_id
            WHERE c.id=campaign;
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
        WHERE configuration_id=config AND kind='parishsoft';
    -- A full slot names the configured time it fell due at, which must be
    -- the nightly time or one of the listed times; a delta slot records the
    -- nightly time, as it always has, and so does a catch-up.
    IF recorded_zone IS DISTINCT FROM zone
       OR (cause IN ('delta','catch_up') AND recorded_time IS DISTINCT FROM nightly)
       OR (cause='nightly' AND recorded_time IS DISTINCT FROM nightly
           AND NOT (jsonb_typeof(times)='array' AND times ? recorded_time))
       OR due > clock_timestamp()
       OR due <> date_trunc('second',due) THEN
        RAISE EXCEPTION '% is not due under its applied cadence', label
            USING ERRCODE='23514';
    END IF;
    IF cause='delta' AND deltas='times' THEN
        -- Listed quick times (#632) are parish-local wall times resolved
        -- like the full times: the slot must fall at one of them on its
        -- local day or the day before.
        local_day := (due AT TIME ZONE
                      public.stewardship_timezone_name_v1(zone))::date;
        IF jsonb_typeof(quick) IS DISTINCT FROM 'array' OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(quick) AS listed(value)
               WHERE due IN (
                   stewardship_resolve_local_v1(local_day+listed.value::time,zone),
                   stewardship_resolve_local_v1((local_day-1)+listed.value::time,zone)))
        THEN
            RAISE EXCEPTION 'Delta % must match its applied delta cadence',
                noun USING ERRCODE='23514';
        END IF;
    ELSIF cause='delta' THEN
        -- Deltas fall on UTC quarter hours or hours, or not at all.
        IF deltas='off' OR extract(second FROM due) <> 0
           OR mod(extract(minute FROM due AT TIME ZONE 'UTC')::int,
                  CASE deltas WHEN 'hourly' THEN 60 ELSE 15 END) <> 0 THEN
            RAISE EXCEPTION 'Delta % must match its applied delta cadence',
                noun USING ERRCODE='23514';
        END IF;
    ELSIF cause='catch_up' THEN
        -- The schedule-change catch-up (#632) is due exactly when the
        -- current schedule took effect: the activation of the earliest
        -- configuration in the latest unbroken run of activations whose
        -- schedule equals the current one (bootstrap configurations
        -- ignored), as data_age.schedule_runs finds it. A schedule that
        -- never changed has no catch-up.
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
        IF boundary IS NULL OR effective IS DISTINCT FROM due THEN
            RAISE EXCEPTION 'Catch-up % must be due when its schedule took effect',
                noun USING ERRCODE='23514';
        END IF;
    ELSIF frequency IN ('hourly','quarter_hour') THEN
        -- A frequent full refresh uses UTC hour or quarter-hour boundaries.
        IF extract(second FROM due) <> 0
           OR mod(extract(minute FROM due AT TIME ZONE 'UTC')::int,
                  CASE frequency WHEN 'hourly' THEN 60 ELSE 15 END) <> 0 THEN
            RAISE EXCEPTION 'Full refresh % must match its applied frequency',
                noun USING ERRCODE='23514';
        END IF;
    ELSE
        local_day := (due AT TIME ZONE
                      public.stewardship_timezone_name_v1(zone))::date;
        IF due <> stewardship_resolve_local_v1(local_day+recorded_time::time,zone)
           AND due <> stewardship_resolve_local_v1(
               (local_day-1)+recorded_time::time,zone) THEN
            RAISE EXCEPTION 'Nightly % must use canonical local-time resolution',
                noun USING ERRCODE='23514';
        END IF;
    END IF;
    scope_digest := encode(sha256(convert_to(stewardship_source_canonical(
        jsonb_build_object('organization_id',organization,
                           'window_digest',window_digest)),'UTF8')),'hex');
    expected_key := encode(sha256(convert_to(stewardship_source_canonical(
        jsonb_build_object('schema','source-refresh-slot-v1',
            'scope_fingerprint',scope_digest,'timezone',zone,
            'nightly_time',CASE WHEN cause='nightly' THEN recorded_time ELSE NULL END,
            'cause',cause,'due_at',
            to_char(due AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||'+00:00')
        ),'UTF8')),'hex');
    IF slot_key IS DISTINCT FROM expected_key THEN
        RAISE EXCEPTION '% identity does not match its exact inputs', label
            USING ERRCODE='23514';
    END IF;
END;
$$;

-- Only the scheduler records a slot decision, under the same ownership and
-- current admitted configuration as a refresh tick, for a slot whose time,
-- cadence and identity are exactly what that tick would carry; the scope
-- is the current one (the applied organization and the current source
-- window). A skip never names the nightly refresh or a catch-up, needs the
-- applied schedule to skip around Family emails, and never follows a tick.
CREATE FUNCTION public.stewardship_source_slot_decision_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        settings jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736229 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Slot decision requires scheduler and work ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
    IF NOT FOUND OR runtime.active_configuration_id
       IS DISTINCT FROM NEW.configuration_id
       OR runtime.restore_review_required
       OR NEW.actor_id IS NOT NULL
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
                  WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Slot decision requires current admitted configuration'
            USING ERRCODE='23514';
    END IF;
    SELECT i.settings INTO settings FROM stewardship_applied_integration i
        WHERE i.configuration_id=NEW.configuration_id AND i.kind='parishsoft';
    IF settings IS NULL OR settings->>'organization_id' !~ '^[1-9][0-9]{0,18}$' THEN
        RAISE EXCEPTION 'Slot decision does not match its current source scope'
            USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_refresh_slot_due_v1('decision', NEW.configuration_id,
        runtime.current_campaign_id, NEW.cause, NEW.due_at, NEW.timezone,
        NEW.nightly_time, (settings->>'organization_id')::bigint,
        encode(sha256(convert_to(stewardship_source_current_window_v1(
            runtime.current_campaign_id),'UTF8')),'hex'),
        NEW.slot_key);
    IF NEW.decision='skipped' AND (
           NEW.cause<>'delta' AND NEW.nightly_time
               = coalesce(settings->>'nightly_time','02:00')
           OR settings->'refresh_rules'->'skip_around_family_emails'
               IS DISTINCT FROM 'true'::jsonb
           OR EXISTS (SELECT 1 FROM stewardship_source_refresh_tick
                      WHERE slot_key=NEW.slot_key)) THEN
        RAISE EXCEPTION 'Slot decision may not skip this slot'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- Slot decisions are append-only, except that the temporary retention
-- housekeeping removes a decision eight days after its due time.
CREATE FUNCTION public.stewardship_source_slot_decision_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' AND OLD.due_at < statement_timestamp() - interval '8 days' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END;
$$;

-- The refresh-tick guard, exactly as the fresh-install functions.sql defines it.
CREATE OR REPLACE FUNCTION public.stewardship_refresh_tick_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        command stewardship_source_refresh_command%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
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
    -- The slot's time, cadence and identity, checked exactly as for a slot
    -- decision (#632): stewardship_refresh_slot_due_v1.
    PERFORM stewardship_refresh_slot_due_v1('tick', NEW.configuration_id,
        runtime.current_campaign_id, command.cause, NEW.due_at, NEW.timezone,
        NEW.nightly_time, request.organization_id, request.window_digest,
        NEW.slot_key);
    -- At most one schedule-change catch-up per effective instant.
    IF command.cause='catch_up' AND EXISTS (
           SELECT 1 FROM stewardship_source_refresh_tick t
           JOIN stewardship_source_refresh_command c ON c.id=t.command_id
           WHERE c.cause='catch_up' AND t.due_at=NEW.due_at) THEN
        RAISE EXCEPTION 'Catch-up tick must be due when its schedule took effect'
            USING ERRCODE='23514';
    END IF;
    -- A slot recorded as skipped around a Family email never runs; a held
    -- slot's tick is admitted (#632).
    IF EXISTS (SELECT 1 FROM stewardship_source_slot_decision
               WHERE slot_key=NEW.slot_key AND decision='skipped') THEN
        RAISE EXCEPTION 'Refresh tick slot was recorded as skipped'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stewardship_source_slot_decision_insert_guard BEFORE INSERT ON public.stewardship_source_slot_decision
FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_slot_decision_insert_v1();
CREATE TRIGGER stewardship_source_slot_decision_immutable_guard_v1 BEFORE UPDATE OR DELETE ON public.stewardship_source_slot_decision
FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_slot_decision_immutable_v1();
REVOKE ALL ON FUNCTION public.stewardship_source_slot_decision_insert_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_source_slot_decision_immutable_v1() FROM PUBLIC;

-- Refuse to commit unless every part above is installed.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_source_slot_decision') IS NULL
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_source_slot_decision'::regclass
             AND conname IN ('source_slot_decision_key','source_slot_decision_time',
                             'source_slot_decision_cause','source_slot_decision_kind',
                             'source_slot_decision_window'))<>5 THEN
        RAISE EXCEPTION 'stewardship_source_slot_decision is incomplete';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.proname IN ('stewardship_refresh_slot_due_v1',
                            'stewardship_source_slot_decision_insert_v1',
                            'stewardship_source_slot_decision_immutable_v1'))<>3 THEN
        RAISE EXCEPTION 'The slot decision functions are missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public'
                     AND p.proname='stewardship_refresh_tick_guard_v1'
                     AND NOT p.prosecdef
                     AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND p.prosrc LIKE '%stewardship_refresh_slot_due_v1(''tick''%'
                     AND p.prosrc LIKE '%Refresh tick slot was recorded as skipped%') THEN
        RAISE EXCEPTION 'stewardship_refresh_tick_guard_v1 was not replaced';
    END IF;
    IF (SELECT count(*) FROM pg_trigger
        WHERE NOT tgisinternal AND (
              (tgrelid='public.stewardship_source_slot_decision'::regclass
               AND tgname IN ('stewardship_source_slot_decision_insert_guard',
                              'stewardship_source_slot_decision_immutable_guard_v1'))
           OR (tgrelid='public.stewardship_source_refresh_tick'::regclass
               AND tgname='stewardship_refresh_tick_insert')))<>3 THEN
        RAISE EXCEPTION 'The slot decision or refresh tick triggers are missing';
    END IF;
END
$check$;
