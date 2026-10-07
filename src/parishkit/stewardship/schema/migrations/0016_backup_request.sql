-- Frozen forward migration file 0016 (the repository-wide file sequence;
-- Django's stewardship_jobs.0011): Take a backup now's request record
-- (ADM-13 PR 3, #530). This file is installed by
-- jobs/migrations/0011_backup_request.py and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. A fresh install runs the baseline, the
-- earlier forward files and then this, and ends in the same catalog as an
-- upgraded database.
--
-- stewardship_backup_request: one Administrator's request for a backup now.
-- The web process cannot start a backup (no Docker, no credentials tree, no
-- backup login), so it records a request and the backup-worker profile's
-- request mode, run from the host's cron every five minutes, claims it, runs
-- the ordinary backup and settles it. Who, when, the session and its fresh
-- sign-in instant, the state, the run it produced or the failure category:
-- no path, host or credential.
--
-- The guard is the boundary:
--   INSERT: only the web login (the schema owner is not checked), for an enabled
--     Administrator whose live session signed in within the last five
--     minutes (the check stewardship_delivery_control_guard_v1 makes), never
--     during a restore review, and only while no other request is running or
--     waiting unexpired. Inserts are serialized by a transaction advisory
--     lock so two cannot both see none. The row starts "waiting" with every
--     time from the database clock.
--   UPDATE: only the backup login (or the schema owner). Identity, actor,
--     session, sign-in and creation never change, and a request moves only
--     forward: waiting may be stamped held (held_at), claimed (running, only
--     before it lapses and never during a restore review) or expired;
--     running may finish with its backup run or fail with a category. Times
--     are the database's.
--   Both run only under READ COMMITTED, so every check reads the rows as
--   committed when the statement ran.
--   DELETE: never.
-- Invoker's rights with a fixed search_path: each login reads only what its
-- grants already allow (database-grants, runtime_grants).
--
-- Expiry: a waiting request lapses 30 minutes after the later of its creation
-- and its last held stamp; a running one counts as failed ("did not finish")
-- two hours after its claim. Request mode records both on the rows; the
-- insert check treats a lapsed request as not live even before that.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_backup_request" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NOT NULL, "session_id" uuid NOT NULL, "authenticated_at" timestamp with time zone NOT NULL, "state" varchar(16) DEFAULT 'waiting' NOT NULL, "held_at" timestamp with time zone NULL, "claimed_at" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL, "backup_run_id" uuid NULL, "failure_kind" varchar(48) NULL);
ALTER TABLE "stewardship_backup_request" ADD CONSTRAINT "stewardship_backup_r_backup_run_id_9980d4ac_fk_stewardsh" FOREIGN KEY ("backup_run_id") REFERENCES "stewardship_backup_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_backup_request" ADD CONSTRAINT "backup_request_state" CHECK (((state)::text = ANY (ARRAY[('waiting'::character varying)::text, ('running'::character varying)::text, ('finished'::character varying)::text, ('failed'::character varying)::text, ('expired'::character varying)::text])));
ALTER TABLE "stewardship_backup_request" ADD CONSTRAINT "backup_request_failure_kind" CHECK (("failure_kind" IS NULL OR "failure_kind"::text ~ '^[a-z][a-z0-9_]{0,47}$'));
ALTER TABLE "stewardship_backup_request" ADD CONSTRAINT "backup_request_shape" CHECK ((("backup_run_id" IS NULL AND "claimed_at" IS NULL AND "failure_kind" IS NULL AND "finished_at" IS NULL AND "state" = 'waiting') OR ("backup_run_id" IS NULL AND "claimed_at" IS NOT NULL AND "failure_kind" IS NULL AND "finished_at" IS NULL AND "state" = 'running') OR ("backup_run_id" IS NOT NULL AND "claimed_at" IS NOT NULL AND "failure_kind" IS NULL AND "finished_at" IS NOT NULL AND "state" = 'finished') OR ("backup_run_id" IS NULL AND "claimed_at" IS NOT NULL AND "failure_kind" IS NOT NULL AND "finished_at" IS NOT NULL AND "state" = 'failed') OR ("backup_run_id" IS NULL AND "claimed_at" IS NULL AND "failure_kind" IS NULL AND "finished_at" IS NOT NULL AND "state" = 'expired')));
CREATE INDEX "stewardship_backup_request_backup_run_id_9980d4ac" ON "stewardship_backup_request" ("backup_run_id");
CREATE INDEX "backup_request_newest" ON "stewardship_backup_request" ("created_at" DESC);

CREATE FUNCTION public.stewardship_backup_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'A backup request is never deleted' USING ERRCODE='42501';
    END IF;
    -- Each check below reads other rows at the statement's own snapshot,
    -- which only READ COMMITTED gives a statement that waited on the lock.
    IF NOT owner AND current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A backup request change requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF owner THEN
            RETURN NEW;
        END IF;
        IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web portal records a backup request' USING ERRCODE='42501';
        END IF;
        -- One request at a time: serialize inserts before looking for others.
        PERFORM pg_advisory_xact_lock(736241, 2);
        IF NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=NEW.authenticated_at
                  AND login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                      AND clock_timestamp()) THEN
            RAISE EXCEPTION 'A backup request requires fresh Administrator authentication' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_system_configuration
                  WHERE restore_review_required) THEN
            RAISE EXCEPTION 'No backup request during a restore review' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_backup_request r
                  WHERE (r.state='running' AND r.claimed_at>instant-interval '2 hours')
                     OR (r.state='waiting'
                         AND greatest(r.created_at,coalesce(r.held_at,r.created_at))
                             >instant-interval '30 minutes')) THEN
            RAISE EXCEPTION 'A backup request is already waiting or running' USING ERRCODE='23505';
        END IF;
        NEW.created_at:=instant;
        NEW.state:='waiting';
        NEW.held_at:=NULL;
        NEW.claimed_at:=NULL;
        NEW.finished_at:=NULL;
        NEW.backup_run_id:=NULL;
        NEW.failure_kind:=NULL;
        RETURN NEW;
    END IF;
    IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_backup_worker' THEN
        RAISE EXCEPTION 'Only the backup worker settles a backup request' USING ERRCODE='42501';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
        RAISE EXCEPTION 'A backup request keeps its identity' USING ERRCODE='23514';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    IF OLD.state='waiting' AND NEW.state='waiting' THEN
        -- A poll that saw a bulk send: only the held stamp moves.
        IF NEW.held_at IS NULL THEN
            RAISE EXCEPTION 'A held stamp needs a time' USING ERRCODE='23514';
        END IF;
        NEW.held_at:=instant;
    ELSIF OLD.state='waiting' AND NEW.state='running' THEN
        -- Never run a lapsed request late, nor any during a restore review.
        IF greatest(OLD.created_at,coalesce(OLD.held_at,OLD.created_at))
               <=instant-interval '30 minutes'
           OR EXISTS(SELECT 1 FROM public.stewardship_system_configuration
                     WHERE restore_review_required) THEN
            RAISE EXCEPTION 'This backup request can no longer be run' USING ERRCODE='23514';
        END IF;
        NEW.claimed_at:=instant;
    ELSIF OLD.state='waiting' AND NEW.state='expired' THEN
        IF greatest(OLD.created_at,coalesce(OLD.held_at,OLD.created_at))
           >instant-interval '30 minutes' THEN
            RAISE EXCEPTION 'A backup request expires only after 30 minutes' USING ERRCODE='23514';
        END IF;
        NEW.finished_at:=instant;
    ELSIF OLD.state='running' AND NEW.state='finished' THEN
        NEW.finished_at:=instant;
    ELSIF OLD.state='running' AND NEW.state='failed' THEN
        NEW.finished_at:=instant;
    ELSE
        RAISE EXCEPTION 'A backup request moves only forward' USING ERRCODE='23514';
    END IF;
    IF NEW.state<>'waiting' AND NEW.held_at IS DISTINCT FROM OLD.held_at THEN
        RAISE EXCEPTION 'Only a waiting request is stamped held' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('finished','failed') AND NEW.claimed_at IS DISTINCT FROM OLD.claimed_at THEN
        RAISE EXCEPTION 'A backup request keeps its claim' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_backup_request_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_backup_request_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_backup_request
FOR EACH ROW EXECUTE FUNCTION public.stewardship_backup_request_guard_v1();

-- Refuse to commit unless the table, its checks, indexes and key, the guard
-- function (invoker's rights, fixed search_path, no PUBLIC EXECUTE) and its
-- trigger are all installed as declared.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_backup_request') IS NULL THEN
        RAISE EXCEPTION 'The backup request table was not created';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.stewardship_backup_request'::regclass
          AND conname IN ('backup_request_state','backup_request_failure_kind',
                          'backup_request_shape')
          AND contype='c' AND convalidated)<>3 THEN
        RAISE EXCEPTION 'The backup request checks were not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_backup_request'::regclass
          AND conname='stewardship_backup_r_backup_run_id_9980d4ac_fk_stewardsh'
          AND contype='f' AND condeferrable AND condeferred
          AND confrelid='public.stewardship_backup_run'::regclass) THEN
        RAISE EXCEPTION 'The backup request run key was not created';
    END IF;
    IF (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE c.relname IN ('stewardship_backup_request_backup_run_id_9980d4ac',
                            'backup_request_newest')
          AND i.indrelid='public.stewardship_backup_request'::regclass
          AND i.indisvalid)<>2 THEN
        RAISE EXCEPTION 'The backup request indexes were not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_backup_request_guard_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'The backup request guard has the wrong security or search_path';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_backup_request_guard_v1()'::regprocedure
          AND p.prosrc LIKE '%transaction_isolation%'
          AND p.prosrc LIKE '%pg_advisory_xact_lock(736241, 2)%'
          AND p.prosrc LIKE '%can no longer be run%') THEN
        RAISE EXCEPTION 'The backup request guard is not the one this file installs';
    END IF;
    IF has_function_privilege('public',
        'public.stewardship_backup_request_guard_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'PUBLIC may execute the backup request guard';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_backup_request'::regclass
          AND t.tgname='stewardship_backup_request_guard' AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_backup_request_guard_v1()'::regprocedure
          AND t.tgtype=31) THEN
        RAISE EXCEPTION 'The backup request guard trigger was not created';
    END IF;
END
$check$;
