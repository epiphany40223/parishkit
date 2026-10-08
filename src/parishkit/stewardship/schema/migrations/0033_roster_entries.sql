-- Frozen forward migration file 0033 (the repository-wide file sequence):
-- Staff and Administrators record that a resolved Ministry join or leave was
-- entered in ParishSoft by hand (#528, step 4). The ParishSoft API cannot
-- change Ministry rosters, so a person does it. This file is installed by
-- the stewardship_workflows migration that names it in FROZEN_SQL and must
-- never change once released; a later change needs a new numbered file.
--
-- stewardship_ministry_roster_entry (new, migration-owned) is the history of
-- the Entered in ParishSoft tick: each row sets or clears it, with who and
-- when, and the latest row is the current state (no row: not entered). The
-- request row is a closed outcome and is never touched, so its guard and the
-- Ministry report, packet and export functions are unchanged; nothing
-- existing is replaced. The guard admits INSERT only, from a current Admin
-- or Staff member (never a Ministry leader), for a live response's request
-- that a person resolved as joined or leave confirmed (resolved, with no
-- resolution source: one the ParishSoft roster already showed needs no
-- tick), in a campaign that admits changes, at the next sequence, and only
-- as a real change of the tick. It takes the request's row lock, so two
-- ticks cannot race. A deferred constraint trigger requires the
-- ministry_roster_entered audit event, which names the Ministry and the
-- sequences only. The file ends with a DO block that refuses to commit
-- unless all of this is installed.
SET LOCAL check_function_bodies = false;

CREATE TABLE "stewardship_ministry_roster_entry" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "sequence" bigint NOT NULL CHECK ("sequence" >= 0), "entered" boolean NOT NULL, "request_key" uuid NOT NULL, "request_id" uuid NOT NULL, CONSTRAINT "ministry_roster_sequence" UNIQUE ("request_id", "sequence"), CONSTRAINT "ministry_roster_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "ministry_roster_identity" CHECK (("actor_id" IS NOT NULL AND "sequence" >= 1)));
ALTER TABLE "stewardship_ministry_roster_entry" ADD CONSTRAINT "stewardship_ministry_request_id_2da44913_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_ministry_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ministry_roster_entry_correlation_id_a99a623c" ON "stewardship_ministry_roster_entry" ("correlation_id");
CREATE INDEX "stewardship_ministry_roster_entry_request_id_2da44913" ON "stewardship_ministry_roster_entry" ("request_id");

CREATE FUNCTION public.stewardship_ministry_roster_entry_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target public.stewardship_ministry_request%ROWTYPE; campaign uuid;
        latest public.stewardship_ministry_roster_entry%ROWTYPE;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Roster entry history is immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO target FROM public.stewardship_ministry_request
        WHERE id=NEW.request_id FOR NO KEY UPDATE;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=target.submission_id AND mode='live';
    SELECT * INTO latest FROM public.stewardship_ministry_roster_entry
        WHERE request_id=NEW.request_id ORDER BY sequence DESC LIMIT 1;
    IF target.id IS NULL OR campaign IS NULL
       OR target.state<>'resolved'
       OR target.outcome NOT IN ('joined','leave_confirmed')
       OR target.outcome IS NULL
       OR target.resolution_source_id IS NOT NULL
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.sequence IS DISTINCT FROM coalesce(latest.sequence,0)+1
       OR NEW.sequence>=9223372036854775807
       OR NEW.entered IS NOT DISTINCT FROM coalesce(latest.entered,false)
    THEN RAISE EXCEPTION 'Roster entry lacks current authority, request or sequence'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_ministry_roster_entry_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target public.stewardship_ministry_request%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO target FROM public.stewardship_ministry_request WHERE id=NEW.request_id;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=target.submission_id;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='ministry_roster_entered' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.sequence-1,'after_version',NEW.sequence,
                 'ministry_duid',target.ministry_duid))
    THEN RAISE EXCEPTION 'Roster entry requires its audit' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_ministry_roster_entry_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_ministry_roster_entry
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_roster_entry_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_ministry_roster_entry_effect
    AFTER INSERT ON public.stewardship_ministry_roster_entry DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_roster_entry_effect_v1();

DO $check$
BEGIN
    -- Both triggers, enabled, on this table: the guard a BEFORE row trigger
    -- that also covers UPDATE and DELETE (history immutability), the effect
    -- deferrable and initially deferred.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_ministry_roster_entry'::regclass
              AND NOT t.tgisinternal AND t.tgenabled<>'D'
              AND p.proname IN ('stewardship_ministry_roster_entry_guard_v1',
                                'stewardship_ministry_roster_entry_effect_v1'))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_ministry_roster_entry_guard'
              AND tgrelid='public.stewardship_ministry_roster_entry'::regclass
              AND (tgtype & 1)<>0 AND (tgtype & 2)<>0 AND (tgtype & 24)=24)
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_ministry_roster_entry_effect'
              AND tgrelid='public.stewardship_ministry_roster_entry'::regclass
              AND tgdeferrable AND tginitdeferred)
    THEN
        RAISE EXCEPTION 'stewardship_ministry_roster_entry is not guarded as declared';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_proc
            WHERE proname IN ('stewardship_ministry_roster_entry_guard_v1',
                              'stewardship_ministry_roster_entry_effect_v1')
              AND (prosecdef OR prosrc NOT LIKE '%RAISE EXCEPTION%'))
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE proname='stewardship_ministry_roster_entry_guard_v1'
              AND prosrc LIKE '%target.resolution_source_id IS NOT NULL%'
              AND prosrc LIKE '%FOR NO KEY UPDATE%')
    THEN
        RAISE EXCEPTION 'stewardship_ministry_roster_entry guards lack their checks';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conname='ministry_roster_sequence'
              AND conrelid='public.stewardship_ministry_roster_entry'::regclass)
    THEN
        RAISE EXCEPTION 'stewardship_ministry_roster_entry lacks its sequence key';
    END IF;
END $check$;
