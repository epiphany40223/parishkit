-- Frozen forward migration file 0031 (the repository-wide file sequence): a
-- BEFORE INSERT guard on stewardship_critical_event_ack (#389 L8). This file
-- is installed by its Django migration and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. A fresh install runs the baseline, the
-- earlier files and then this, and ends in the same catalog as an upgraded
-- database.
--
-- An acknowledgement hides one CRITICAL operational log entry from every
-- Administrator's banner. Its log_id had no foreign key and no level check,
-- so a web bug could record an acknowledgement for an entry that does not
-- exist yet (hiding it in advance) or for a non-CRITICAL one. Only the web
-- login records acknowledgements (audit/critical_events.acknowledge), and
-- only for a CRITICAL entry it has just read. The guard admits only that:
-- the web login, an existing CRITICAL entry, and a named actor; it sets
-- created_at itself. A foreign key would add nothing: log entries are
-- append-only (their immutable guard refuses every DELETE), so an entry that
-- exists at the insert exists for good, and a key would change the model's
-- declared schema. The schema owner (migrations, restore, tests) is exempt,
-- as in the other guards.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_critical_event_ack_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web login acknowledges critical entries' USING ERRCODE='42501';
    END IF;
    IF NEW.actor_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_operational_log
        WHERE id=NEW.log_id AND level='CRITICAL') THEN
        RAISE EXCEPTION 'An acknowledgement names an existing CRITICAL entry and its Administrator'
            USING ERRCODE='23514';
    END IF;
    NEW.created_at := statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_critical_event_ack_insert_guard_v1 BEFORE INSERT ON public.stewardship_critical_event_ack
FOR EACH ROW EXECUTE FUNCTION public.stewardship_critical_event_ack_insert_v1();
REVOKE ALL ON FUNCTION public.stewardship_critical_event_ack_insert_v1() FROM PUBLIC;

-- Refuse to commit unless every existing acknowledgement already names an
-- existing CRITICAL entry (the rule was always true of the web's path), and
-- the guard is installed as declared: an enabled row-level BEFORE INSERT
-- trigger on the table calling this invoker with a fixed search_path.
DO $check$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.stewardship_critical_event_ack ack
        WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_operational_log log
            WHERE log.id=ack.log_id AND log.level='CRITICAL')) THEN
        RAISE EXCEPTION 'Migration 0031: an acknowledgement names no CRITICAL entry';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgname='stewardship_critical_event_ack_insert_guard_v1'
          AND t.tgrelid='public.stewardship_critical_event_ack'::regclass
          AND t.tgenabled='O' AND NOT t.tgisinternal AND t.tgtype=7
          AND p.oid='public.stewardship_critical_event_ack_insert_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'Migration 0031 (critical acknowledgement guard) is not installed as declared';
    END IF;
END
$check$;
