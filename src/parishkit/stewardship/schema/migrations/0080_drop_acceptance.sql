-- Frozen forward migration file 0080 (the repository-wide file sequence):
-- accept a large ParishSoft change once, server side (ADM-13 PR 5a, #530).
-- This file is installed by its Django migration in the source app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. A later change gets its own numbered file. A fresh install
-- runs the baseline, the earlier forward files and then this. Reversing needs
-- its own forward migration.
--
-- A full ParishSoft refresh is refused when a record or eligibility count
-- falls too far, and every count it was checked on is recorded with the
-- refused attempt (stewardship_source_drop_count, 0007). Until now only the
-- server operator could let a known large change through, by restarting the
-- worker with PARISHKIT_SOURCE_MAX_DROP_PERCENT. An Administrator's one-time
-- acceptance instead names one refusal and copies every count recorded with
-- it, as reviewed. The worker (parishkit.stewardship.source.drop_acceptance)
-- lets one later full refresh through only while the acceptance is live and
-- only when no count fell below its reviewed value and every count failing
-- now also failed in the refusal; the drop limit itself is never raised.
--
-- stewardship_source_drop_acceptance: the refused attempt (one acceptance per
-- refusal, ever), the counts as reviewed, who, the session and its fresh
-- sign-in instant, and when it was given and when it expires (24 hours later).
-- No parish data: counts only.
--
-- The guard is the boundary:
--   INSERT: only the web login (the schema owner is not checked), for an
--     enabled Administrator whose live session signed in within the last
--     five minutes or is a live full-scope automation session's command login
--     (the check stewardship_debug_off_switch_guard_v1 makes). The attempt
--     must be the newest refusal, with no full refresh promoted since; at
--     least one of its counts must have failed; it must not be a load with
--     no Families or no Members (refused at any setting); the copied counts
--     must equal the recorded ones exactly; and no other acceptance may be
--     live. Inserts are serialized by a transaction advisory lock so two
--     cannot both see none live. Both times come from the database clock.
--     (The web's grants come with System health's button, ADM-13 PR 5b.)
--   UPDATE and DELETE: never, by anyone. An acceptance's lifetime is derived,
--     never stored, so nothing has to remember to end it.
--   Non-owner inserts run only under READ COMMITTED, so every check reads
--   the rows as committed when the statement ran.
-- Invoker's rights with a fixed search_path.
--
-- stewardship_source_drop_acceptance_active_v1() answers which acceptance is
-- live, or NULL: one that has not expired, that names a recorded refusal
-- which is still the newest (no other attempt's counts were recorded since),
-- and after whose refusal no full refresh has been promoted. So the first
-- full refresh promoted afterwards ends it whether or not it used it, and so
-- does any new refusal. The guard and the worker both call it, so they
-- cannot disagree. Invoker's rights, STABLE, fixed search_path: it reads
-- only what its caller may read, so it keeps PostgreSQL's default PUBLIC
-- EXECUTE, as stewardship_automation_fresh_v1 does, and needs no grant of
-- its own. A login without SELECT on the tables it reads gets an error,
-- never an answer.
--
-- A full refresh loaded under an acceptance is promoted only while that
-- acceptance is still the live one, checked under the work-order lock in
-- the promotion's own transaction (source.drop_acceptance.require_live).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_source_drop_acceptance" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NOT NULL, "session_id" uuid NOT NULL, "authenticated_at" timestamp with time zone NOT NULL, "counts" jsonb NOT NULL, "expires_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "attempt_id" uuid NOT NULL UNIQUE);
ALTER TABLE "stewardship_source_drop_acceptance" ADD CONSTRAINT "stewardship_source_d_attempt_id_6c026bc5_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_source_refresh_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "source_drop_acceptance_newest" ON "stewardship_source_drop_acceptance" ("created_at" DESC);

CREATE FUNCTION public.stewardship_source_drop_acceptance_active_v1() RETURNS uuid
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT a.id
      FROM public.stewardship_source_drop_acceptance a
      CROSS JOIN LATERAL (SELECT max(c.created_at) AS refused_at
                            FROM public.stewardship_source_drop_count c
                           WHERE c.attempt_id=a.attempt_id) r
     WHERE a.expires_at>statement_timestamp()
       AND r.refused_at IS NOT NULL
       AND NOT EXISTS(SELECT 1 FROM public.stewardship_source_drop_count later
                       WHERE later.attempt_id<>a.attempt_id
                         AND later.created_at>=r.refused_at)
       AND NOT EXISTS(SELECT 1 FROM public.stewardship_source_snapshot s
                       WHERE s.kind='full' AND s.state='promoted'
                         AND s.promoted_at>=r.refused_at)
     ORDER BY a.created_at DESC
     LIMIT 1
$$;

CREATE FUNCTION public.stewardship_source_drop_acceptance_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
    refused_at timestamptz;
    any_failed boolean;
    empty boolean;
    recorded jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'A large-change acceptance is never changed or deleted' USING ERRCODE='42501';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    -- The checks read other rows at the statement's own snapshot, which only
    -- READ COMMITTED gives a statement that waited on the lock.
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A large-change acceptance requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web portal accepts a large ParishSoft change' USING ERRCODE='42501';
    END IF;
    -- One live acceptance at a time: serialize inserts before looking.
    PERFORM pg_advisory_xact_lock(736241, 4);
    IF NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                       AND clock_timestamp()
                   -- A live full-scope automation session stands in for a
                   -- fresh sign-in, as in the other fresh-gated guards.
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Accepting a large ParishSoft change requires fresh Administrator authentication' USING ERRCODE='42501';
    END IF;
    SELECT max(c.created_at), bool_or(c.failed),
           bool_or(c.measure IN ('family','member') AND c.after=0),
           jsonb_object_agg(c.measure, jsonb_build_object(
               'before', c.before, 'after', c.after, 'failed', c.failed))
      INTO refused_at, any_failed, empty, recorded
      FROM public.stewardship_source_drop_count c
     WHERE c.attempt_id=NEW.attempt_id;
    IF refused_at IS NULL THEN
        RAISE EXCEPTION 'An acceptance names a refused ParishSoft refresh' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.stewardship_source_drop_count later
               WHERE later.attempt_id<>NEW.attempt_id AND later.created_at>=refused_at)
       OR EXISTS(SELECT 1 FROM public.stewardship_source_snapshot s
                  WHERE s.kind='full' AND s.state='promoted' AND s.promoted_at>=refused_at) THEN
        RAISE EXCEPTION 'The refusal is no longer the newest full refresh''s outcome' USING ERRCODE='23514';
    END IF;
    IF NOT any_failed THEN
        RAISE EXCEPTION 'The refusal has no count that fell too far' USING ERRCODE='23514';
    END IF;
    IF empty THEN
        RAISE EXCEPTION 'A load with no Families or no Members cannot be accepted' USING ERRCODE='23514';
    END IF;
    IF NEW.counts IS DISTINCT FROM recorded THEN
        RAISE EXCEPTION 'The reviewed counts differ from the recorded refusal' USING ERRCODE='23514';
    END IF;
    IF public.stewardship_source_drop_acceptance_active_v1() IS NOT NULL THEN
        RAISE EXCEPTION 'Another large-change acceptance is still in effect' USING ERRCODE='23505';
    END IF;
    NEW.created_at:=instant;
    NEW.expires_at:=instant+interval '24 hours';
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_source_drop_acceptance_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_source_drop_acceptance_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_source_drop_acceptance
FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_drop_acceptance_guard_v1();

-- Refuse to commit unless the table (with no PUBLIC privilege), its unique
-- attempt, key and index, the guard (invoker's rights, fixed search_path, no
-- PUBLIC EXECUTE) and its trigger, and the lifetime function (invoker's
-- rights, STABLE, fixed search_path) are all installed as declared. Missing
-- objects are checked first, on their own, because PostgreSQL does not
-- short-circuit OR and the ::regprocedure casts below would otherwise fail
-- with their own error.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_source_drop_acceptance') IS NULL
       OR to_regprocedure('public.stewardship_source_drop_acceptance_guard_v1()') IS NULL
       OR to_regprocedure('public.stewardship_source_drop_acceptance_active_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0080 (large-change acceptance) is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.stewardship_source_drop_acceptance'::regclass
          AND convalidated
          AND ((contype='u' AND pg_get_constraintdef(oid)='UNIQUE (attempt_id)')
               OR (contype='f' AND confrelid='public.stewardship_source_refresh_attempt'::regclass)))<>2 THEN
        RAISE EXCEPTION 'The large-change acceptance keys were not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE c.relname='source_drop_acceptance_newest'
          AND i.indrelid='public.stewardship_source_drop_acceptance'::regclass
          AND i.indisvalid) THEN
        RAISE EXCEPTION 'The large-change acceptance index was not created';
    END IF;
    -- PUBLIC holds no privilege on the table (grantee 0 in its ACL).
    IF EXISTS(SELECT 1 FROM pg_class c, aclexplode(coalesce(c.relacl,
                  acldefault('r',c.relowner))) a
              WHERE c.oid='public.stewardship_source_drop_acceptance'::regclass
                AND a.grantee=0) THEN
        RAISE EXCEPTION 'PUBLIC holds a privilege on the large-change acceptance';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_source_drop_acceptance_guard_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%transaction_isolation%'
          AND p.prosrc LIKE '%current_user IS DISTINCT FROM ''pk_stewardship_web''%'
          AND p.prosrc LIKE '%pg_advisory_xact_lock(736241, 4)%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)%'
          AND p.prosrc LIKE '%NEW.counts IS DISTINCT FROM recorded%'
          AND p.prosrc LIKE '%c.measure IN (''family'',''member'') AND c.after=0%'
          AND p.prosrc LIKE '%NEW.expires_at:=instant+interval ''24 hours''%'
          AND p.prosrc LIKE '%public.stewardship_source_drop_acceptance_active_v1() IS NOT NULL%'
          AND p.prosrc LIKE '%never changed or deleted%')
       OR has_function_privilege('public',
            'public.stewardship_source_drop_acceptance_guard_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'The large-change acceptance guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_source_drop_acceptance'::regclass
          AND t.tgname='stewardship_source_drop_acceptance_guard' AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_source_drop_acceptance_guard_v1()'::regprocedure
          AND t.tgtype=31) THEN
        RAISE EXCEPTION 'The large-change acceptance guard trigger was not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_source_drop_acceptance_active_v1()'::regprocedure
          AND NOT p.prosecdef AND p.provolatile='s' AND NOT p.proretset
          AND p.prorettype='uuid'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%a.expires_at>statement_timestamp()%'
          AND p.prosrc LIKE '%r.refused_at IS NOT NULL%'
          AND p.prosrc LIKE '%later.attempt_id<>a.attempt_id%'
          AND p.prosrc LIKE '%s.promoted_at>=r.refused_at%') THEN
        RAISE EXCEPTION 'The large-change acceptance lifetime is not installed as declared';
    END IF;
END
$check$;
