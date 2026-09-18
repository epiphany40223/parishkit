-- One bounded checkpoint, not an append-only row for every idle scheduler tick.
CREATE TABLE public.stewardship_due_work_health (
    singleton boolean PRIMARY KEY,
    signal varchar(7) NOT NULL,
    scan_started_at timestamptz NOT NULL,
    observed_at timestamptz DEFAULT statement_timestamp() NOT NULL,
    late_since timestamptz NULL,
    clear_since timestamptz NULL,
    last_failure_at timestamptz NULL,
    escalation_seconds integer NOT NULL CHECK (escalation_seconds>=0),
    CONSTRAINT due_health_escalation CHECK (escalation_seconds BETWEEN 60 AND 86400),
    CONSTRAINT due_health_singleton CHECK (singleton),
    CONSTRAINT due_health_signal CHECK (signal::text = ANY(ARRAY[
        ('late'::varchar)::text,('clear'::varchar)::text,('unknown'::varchar)::text])),
    CONSTRAINT due_health_times CHECK (scan_started_at<=observed_at
        AND (late_since IS NULL OR late_since<=observed_at)
        AND (clear_since IS NULL OR clear_since<=scan_started_at))
);

-- The guard supplies its authoritative clock. This pure calculation also lets
-- tests cover elapsed windows without sleeping or weakening installed guards.
CREATE FUNCTION public.stewardship_due_work_since_v1(
    previous_signal text, previous_observed timestamptz, previous_start timestamptz,
    previous_since timestamptz, signal text, scan_start timestamptz, instant timestamptz
) RETURNS timestamptz LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN previous_signal=signal AND previous_since IS NOT NULL
        AND instant>=previous_observed
        -- Allow slow negative scans, but never bridge a long observation outage.
        -- Positive proof remains stricter (Python installer_health.MAX_AGE_SECONDS).
        AND instant-previous_observed<=CASE WHEN signal='late'
            THEN interval '5 minutes' ELSE interval '90 seconds' END
        AND scan_start>=previous_start THEN previous_since
        ELSE CASE WHEN signal='clear' THEN scan_start ELSE instant END END;
$$;

CREATE FUNCTION public.stewardship_due_work_critical_v1(
    signal text, since timestamptz, last_failure timestamptz, seconds integer, instant timestamptz
) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(signal='late' AND instant-since>=make_interval(secs=>seconds)
        AND (last_failure IS NULL OR instant-last_failure>=interval '60 seconds'),false);
$$;

CREATE FUNCTION public.stewardship_due_work_health_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE instant timestamptz;
BEGIN
    IF TG_OP='DELETE' OR NOT EXISTS(SELECT 1 FROM pg_locks
        WHERE locktype='advisory' AND pid=pg_backend_pid()
          AND classid=736229 AND objid=1 AND objsubid=2 AND granted) THEN
        RAISE EXCEPTION 'Due-work observations require the owned scheduler session' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736246,1);
    instant:=clock_timestamp();
    IF NOT isfinite(NEW.scan_started_at) OR NEW.scan_started_at>instant THEN
        RAISE EXCEPTION 'Due-work observation time is invalid' USING ERRCODE='23514';
    END IF;
    IF NEW.signal='clear' AND instant-NEW.scan_started_at>interval '90 seconds' THEN
        NEW.signal:='unknown';
    END IF;
    IF TG_OP='UPDATE' AND NEW.signal='unknown' AND OLD.signal='late' THEN
        -- An inconclusive prefix/interruption invalidates positive proof, not
        -- previous negative evidence. Do not renew its timestamp or emit a log:
        -- only another real late observation may continue/escalate that window.
        RETURN NULL;
    END IF;
    NEW.observed_at:=instant;
    NEW.late_since:=NULL;
    NEW.clear_since:=NULL;
    NEW.last_failure_at:=OLD.last_failure_at;
    IF NEW.signal='late' THEN
        NEW.late_since:=public.stewardship_due_work_since_v1(OLD.signal,OLD.observed_at,
            OLD.scan_started_at,OLD.late_since,NEW.signal,NEW.scan_started_at,instant);
    ELSIF NEW.signal='clear' THEN
        NEW.clear_since:=public.stewardship_due_work_since_v1(OLD.signal,OLD.observed_at,
            OLD.scan_started_at,OLD.clear_since,NEW.signal,NEW.scan_started_at,instant);
    END IF;
    IF public.stewardship_due_work_critical_v1(NEW.signal,NEW.late_since,
        NEW.last_failure_at,NEW.escalation_seconds,instant) THEN
        -- Intake can be unavailable together with the general worker. Retain
        -- immutable negative evidence here before any later scan can recover.
        INSERT INTO public.stewardship_operational_log
            (id,correlation_id,level,event,schema,context)
        VALUES(gen_random_uuid(),gen_random_uuid(),'CRITICAL','due_work_lag','exception','{}');
        NEW.last_failure_at:=instant;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_due_work_health_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_due_work_health FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_due_work_health_v1();
REVOKE ALL ON FUNCTION public.stewardship_due_work_health_v1() FROM PUBLIC;
