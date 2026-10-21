-- Frozen forward migration file 0115 (the repository-wide file sequence):
-- clear a halted mail sender, server side (ADM-13 PR 4a, #530). This file is
-- installed by its Django migration in the jobs app and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. A later change gets its own numbered file. A fresh install runs
-- the baseline, the earlier forward files and then this, and ends in the
-- same catalog as an upgraded database. Reversing needs its own forward
-- migration.
--
-- A SYSTEMIC fault (a configuration or credential fault that waiting cannot
-- fix) halts Family mail in both mail consumers of mail-dispatch through a
-- marker file in the container, until the container restarts. These three
-- records let an Administrator clear the halt instead. No row holds a
-- message, recipient, address, host or credential, and every time is the
-- database's.
--
-- stewardship_mail_halt: one halt, with the identity the consumer that
-- halted made for it (also written into the marker, so both consumers share
-- it), its kind (refused before DATA was sent, or a fault once it was) and
-- when it began. A halted consumer records it from its idle loop, so the
-- web can name the halt it clears.
--   INSERT: only the mail-dispatch login (the schema owner is not checked),
--     with a start no more than an hour back and never in the future; the
--     consumer passes the database clock less the seconds since it halted.
--   UPDATE and DELETE: never.
--
-- stewardship_mail_halt_clear: an Administrator's clear signal for one halt
-- (unique per halt). Each mail consumer reads the signal for its own
-- current halt and acts on it only while it still holds that halt.
--   INSERT: only the web login, for an enabled Administrator with a live
--     session. No recent sign-in is required: the Administrator removed that
--     requirement from the Admin portal (#1547) before this action was
--     built, so no sign-in instant is recorded. Inserts are serialized by a
--     transaction advisory lock, and each repeats the page's checks at its
--     own statement's snapshot: the halt has no clear signal and is the
--     newest such halt that began while a mail consumer that reported
--     within three minutes, and reports it is halted, was running (so a
--     split halt stays clearable one halt at a time), the newest mailbox
--     check was claimed after the halt began and passed within the last
--     five minutes, and no message in Outgoing mail
--     is delivery_unknown (every one, not only the halt's: the bulk send
--     commits several submissions before it sends, so no time window
--     separates the halt's attempts from earlier ones).
--   UPDATE and DELETE: never.
--
-- stewardship_mailbox_check: an Administrator's request to check that the
-- mailbox still signs in, which the mail-dispatch main process runs (the web
-- holds no Workspace credential) and settles with its outcome.
--   INSERT: only the web login, for an enabled Administrator with a live
--     session, while no other check is live (waiting or running, and less
--     than two minutes old). Serialized by its own advisory lock. The row
--     starts waiting.
--   UPDATE: only the mail-dispatch login (or the schema owner). Identity,
--     actor, session and creation never change; a live waiting check may be
--     claimed (running), and a running one settles once (passed, refused or
--     unavailable). A check older than two minutes is never claimed: the
--     page has said by then that the mail sender did not answer.
--     Mail dispatch's main process claims one only while a consumer in its
--     container is halted, as it sees locally or as a mail-dispatch status
--     record that reported Halted within three minutes says (so a halt the
--     second consumer holds with no marker can be cleared); for that its
--     login reads stewardship_service_status's service, sender_state and
--     reported_at (runtime_grants, after migrating).
--   DELETE: never.
-- Non-owner changes run only under READ COMMITTED, so every check reads the
-- rows as committed when the statement ran. Invoker's rights with a fixed
-- search_path: each login reads only what its grants already allow
-- (database-grants, runtime_grants). No SECURITY DEFINER routine is created.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_mail_halt" ("id" uuid NOT NULL PRIMARY KEY, "began_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "kind" varchar(16) NOT NULL);
ALTER TABLE "stewardship_mail_halt" ADD CONSTRAINT "mail_halt_kind" CHECK (((kind)::text = ANY (ARRAY[('before_data'::character varying)::text, ('after_data'::character varying)::text])));
CREATE INDEX "mail_halt_newest" ON "stewardship_mail_halt" ("began_at" DESC);

CREATE TABLE "stewardship_mail_halt_clear" ("id" uuid NOT NULL PRIMARY KEY, "halt_id" uuid NOT NULL UNIQUE, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NOT NULL, "session_id" uuid NOT NULL);
ALTER TABLE "stewardship_mail_halt_clear" ADD CONSTRAINT "stewardship_mail_hal_halt_id_7e49b3d3_fk_stewardsh" FOREIGN KEY ("halt_id") REFERENCES "stewardship_mail_halt" ("id") DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE "stewardship_mailbox_check" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NOT NULL, "session_id" uuid NOT NULL, "state" varchar(16) DEFAULT 'waiting' NOT NULL, "claimed_at" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL);
ALTER TABLE "stewardship_mailbox_check" ADD CONSTRAINT "mailbox_check_state" CHECK (((state)::text = ANY (ARRAY[('waiting'::character varying)::text, ('running'::character varying)::text, ('passed'::character varying)::text, ('refused'::character varying)::text, ('unavailable'::character varying)::text])));
ALTER TABLE "stewardship_mailbox_check" ADD CONSTRAINT "mailbox_check_shape" CHECK ((("claimed_at" IS NULL AND "finished_at" IS NULL AND "state" = 'waiting') OR ("claimed_at" IS NOT NULL AND "finished_at" IS NULL AND "state" = 'running') OR ("claimed_at" IS NOT NULL AND "finished_at" IS NOT NULL AND ("state")::text = ANY (ARRAY[('passed'::character varying)::text, ('refused'::character varying)::text, ('unavailable'::character varying)::text]))));
CREATE INDEX "mailbox_check_newest" ON "stewardship_mailbox_check" ("created_at" DESC);

CREATE FUNCTION public.stewardship_mail_halt_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'A mail halt record is never changed or deleted' USING ERRCODE='42501';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    IF current_user IS DISTINCT FROM 'pk_stewardship_mail_dispatch' THEN
        RAISE EXCEPTION 'Only mail dispatch records a mail halt' USING ERRCODE='42501';
    END IF;
    -- The consumer passes the database clock less the seconds since it
    -- halted, so the start is the halt's own moment, never a guess.
    IF NEW.began_at IS NULL OR NEW.began_at>instant
       OR NEW.began_at<instant-interval '1 hour' THEN
        RAISE EXCEPTION 'A mail halt starts within the last hour' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_mail_halt_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_mail_halt_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_mail_halt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_mail_halt_guard_v1();

CREATE FUNCTION public.stewardship_mail_halt_clear_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
    newest_id uuid;
    newest_at timestamptz;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'A clear signal is never changed or deleted' USING ERRCODE='42501';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web portal clears a mail halt' USING ERRCODE='42501';
    END IF;
    -- Each check reads other rows at the statement's own snapshot, which
    -- only READ COMMITTED gives a statement that waited on the lock.
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A clear signal requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    -- One clear at a time: serialize before looking.
    PERFORM pg_advisory_xact_lock(736241, 6);
    IF NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes') THEN
        RAISE EXCEPTION 'A clear signal requires an Administrator' USING ERRCODE='42501';
    END IF;
    IF EXISTS(SELECT 1 FROM public.stewardship_mail_halt_clear c
              WHERE c.halt_id=NEW.halt_id) THEN
        RAISE EXCEPTION 'The mail halt is already cleared' USING ERRCODE='23505';
    END IF;
    -- The current halt: the newest one with no clear signal that began
    -- while a mail consumer now reporting Halted was running (a minute's
    -- slack for the dating back). Uncleared, so that if the two consumers
    -- ever hold different halts, clearing one leaves the other clearable.
    SELECT h.id, h.began_at INTO newest_id, newest_at FROM public.stewardship_mail_halt h
     WHERE NOT EXISTS(SELECT 1 FROM public.stewardship_mail_halt_clear c
                      WHERE c.halt_id=h.id)
       AND EXISTS(SELECT 1 FROM public.stewardship_service_status s
            WHERE s.service='mail-dispatch' AND s.sender_state='halted'
              AND s.reported_at>instant-interval '3 minutes'
              AND s.started_at<=h.began_at+interval '1 minute')
     ORDER BY h.began_at DESC, h.id DESC LIMIT 1;
    IF newest_id IS DISTINCT FROM NEW.halt_id THEN
        RAISE EXCEPTION 'The mail halt is no longer current' USING ERRCODE='23514';
    END IF;
    -- Only the newest mailbox check counts: a later refused or unavailable
    -- check cancels an earlier pass.
    IF NOT EXISTS(SELECT 1 FROM (SELECT m.state, m.claimed_at, m.finished_at
            FROM public.stewardship_mailbox_check m
            ORDER BY m.created_at DESC, m.id DESC LIMIT 1) newest
        WHERE newest.state='passed' AND newest.claimed_at>=newest_at
          AND newest.finished_at>instant-interval '5 minutes') THEN
        RAISE EXCEPTION 'No mailbox check passed since the halt began' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.stewardship_outbox_message o
              WHERE o.state='delivery_unknown') THEN
        RAISE EXCEPTION 'An email that may have arrived is not settled yet' USING ERRCODE='23514';
    END IF;
    NEW.created_at:=instant;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_mail_halt_clear_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_mail_halt_clear_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_mail_halt_clear
FOR EACH ROW EXECUTE FUNCTION public.stewardship_mail_halt_clear_guard_v1();

CREATE FUNCTION public.stewardship_mailbox_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'A mailbox check is never deleted' USING ERRCODE='42501';
    END IF;
    IF NOT owner AND current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A mailbox check change requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF owner THEN
            RETURN NEW;
        END IF;
        IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web portal requests a mailbox check' USING ERRCODE='42501';
        END IF;
        -- One live check at a time: serialize inserts before looking.
        PERFORM pg_advisory_xact_lock(736241, 5);
        IF NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes') THEN
            RAISE EXCEPTION 'A mailbox check requires an Administrator' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_mailbox_check m
                  WHERE m.state IN ('waiting','running')
                    AND m.created_at>instant-interval '2 minutes') THEN
            RAISE EXCEPTION 'A mailbox check is already waiting or running' USING ERRCODE='23505';
        END IF;
        NEW.created_at:=instant;
        NEW.state:='waiting';
        NEW.claimed_at:=NULL;
        NEW.finished_at:=NULL;
        RETURN NEW;
    END IF;
    IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_mail_dispatch' THEN
        RAISE EXCEPTION 'Only mail dispatch runs a mailbox check' USING ERRCODE='42501';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id THEN
        RAISE EXCEPTION 'A mailbox check keeps its identity' USING ERRCODE='23514';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    IF OLD.state='waiting' AND NEW.state='running' THEN
        -- Never run a check the page has already called unanswered.
        IF OLD.created_at<=instant-interval '2 minutes' THEN
            RAISE EXCEPTION 'This mailbox check can no longer be run' USING ERRCODE='23514';
        END IF;
        NEW.claimed_at:=instant;
        NEW.finished_at:=NULL;
    ELSIF OLD.state='running' AND NEW.state IN ('passed','refused','unavailable') THEN
        IF NEW.claimed_at IS DISTINCT FROM OLD.claimed_at THEN
            RAISE EXCEPTION 'A mailbox check keeps its claim' USING ERRCODE='23514';
        END IF;
        NEW.finished_at:=instant;
    ELSE
        RAISE EXCEPTION 'A mailbox check moves only forward' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_mailbox_check_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_mailbox_check_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_mailbox_check
FOR EACH ROW EXECUTE FUNCTION public.stewardship_mailbox_check_guard_v1();

-- Refuse to commit unless the three tables (with no PUBLIC privilege), their
-- checks, indexes and key, the three guards (invoker's rights, fixed
-- search_path, no PUBLIC EXECUTE) and their triggers are all installed as
-- declared. Missing objects are checked first, on their own, because
-- PostgreSQL does not short-circuit OR and the ::regclass and
-- ::regprocedure casts below would otherwise fail with their own error.
DO $check$
DECLARE
    guarded text[][] := ARRAY[
        ['public.stewardship_mail_halt','stewardship_mail_halt_guard',
         'public.stewardship_mail_halt_guard_v1()'],
        ['public.stewardship_mail_halt_clear','stewardship_mail_halt_clear_guard',
         'public.stewardship_mail_halt_clear_guard_v1()'],
        ['public.stewardship_mailbox_check','stewardship_mailbox_check_guard',
         'public.stewardship_mailbox_check_guard_v1()']];
    i integer;
BEGIN
    FOR i IN 1..3 LOOP
        IF to_regclass(guarded[i][1]) IS NULL OR to_regprocedure(guarded[i][3]) IS NULL THEN
            RAISE EXCEPTION 'Migration 0115: the clear a halted mail sender objects are not installed';
        END IF;
    END LOOP;
    FOR i IN 1..3 LOOP
        -- PUBLIC holds no privilege on the table (grantee 0 in its ACL).
        IF EXISTS(SELECT 1 FROM pg_class c, aclexplode(coalesce(c.relacl,
                      acldefault('r',c.relowner))) a
                  WHERE c.oid=guarded[i][1]::regclass AND a.grantee=0) THEN
            RAISE EXCEPTION 'Migration 0115: PUBLIC holds a privilege on %', guarded[i][1];
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_proc p
            WHERE p.oid=guarded[i][3]::regprocedure AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
           OR has_function_privilege('public', guarded[i][3], 'EXECUTE') THEN
            RAISE EXCEPTION 'Migration 0115: the guard % has the wrong security or search_path', guarded[i][3];
        END IF;
        IF NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid=guarded[i][1]::regclass AND t.tgname=guarded[i][2]
              AND t.tgenabled='O' AND t.tgfoid=guarded[i][3]::regprocedure
              AND t.tgtype=31) THEN
            RAISE EXCEPTION 'Migration 0115: the guard trigger % was not created', guarded[i][2];
        END IF;
    END LOOP;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_mail_halt_guard_v1()'::regprocedure
          AND p.prosrc LIKE '%pk_stewardship_mail_dispatch%'
          AND p.prosrc LIKE '%interval ''1 hour''%') THEN
        RAISE EXCEPTION 'Migration 0115: the mail halt guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_mail_halt_clear_guard_v1()'::regprocedure
          AND p.prosrc LIKE '%current_user IS DISTINCT FROM ''pk_stewardship_web''%'
          AND p.prosrc LIKE '%transaction_isolation%'
          AND p.prosrc LIKE '%pg_advisory_xact_lock(736241, 6)%'
          AND p.prosrc LIKE '%public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE%'
          AND p.prosrc LIKE '%login.principal_id=NEW.actor_id%'
          AND p.prosrc LIKE '%s.started_at<=h.began_at+interval ''1 minute''%'
          AND p.prosrc LIKE '%ORDER BY m.created_at DESC, m.id DESC LIMIT 1%'
          AND p.prosrc LIKE '%delivery_unknown%'
          AND p.prosrc LIKE '%interval ''5 minutes''%'
          AND p.prosrc NOT LIKE '%authenticated_at%') THEN
        RAISE EXCEPTION 'Migration 0115: the clear signal guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_mailbox_check_guard_v1()'::regprocedure
          AND p.prosrc LIKE '%current_user IS DISTINCT FROM ''pk_stewardship_web''%'
          AND p.prosrc LIKE '%current_user IS DISTINCT FROM ''pk_stewardship_mail_dispatch''%'
          AND p.prosrc LIKE '%transaction_isolation%'
          AND p.prosrc LIKE '%pg_advisory_xact_lock(736241, 5)%'
          AND p.prosrc LIKE '%public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE%'
          AND p.prosrc LIKE '%login.principal_id=NEW.actor_id%'
          AND p.prosrc LIKE '%can no longer be run%'
          AND p.prosrc NOT LIKE '%authenticated_at%') THEN
        RAISE EXCEPTION 'Migration 0115: the mailbox check guard is not the one this file installs';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid IN ('public.stewardship_mail_halt'::regclass,
                           'public.stewardship_mailbox_check'::regclass)
          AND conname IN ('mail_halt_kind','mailbox_check_state','mailbox_check_shape')
          AND contype='c' AND convalidated)<>3 THEN
        RAISE EXCEPTION 'Migration 0115: the mail halt and mailbox check checks were not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_mail_halt_clear'::regclass
          AND conname='stewardship_mail_hal_halt_id_7e49b3d3_fk_stewardsh'
          AND contype='f' AND condeferrable AND condeferred
          AND confrelid='public.stewardship_mail_halt'::regclass)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_mail_halt_clear'::regclass
          AND contype='u') THEN
        RAISE EXCEPTION 'Migration 0115: the clear signal''s halt key was not created';
    END IF;
    IF (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE c.relname IN ('mail_halt_newest','mailbox_check_newest')
          AND i.indisvalid)<>2 THEN
        RAISE EXCEPTION 'Migration 0115: the mail halt and mailbox check indexes were not created';
    END IF;
END
$check$;
