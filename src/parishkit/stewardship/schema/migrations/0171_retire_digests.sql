-- Frozen forward migration file 0171 (the repository-wide file sequence):
-- the Daily and Weekly digest pipelines retire once report emails have
-- replaced them (#1834; report-emails spec, "Database functions"). This
-- file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. A fresh install runs the baseline, which already lacks
-- everything dropped here and whose definition check already admits no
-- digest kind, then the earlier files (0161 widens that check with the
-- digest kinds again) and then this, and ends in the same catalog as an
-- upgraded database.
--
-- The conversion (0161 plus the offline `pk-stewardship upgrade-reports`)
-- retyped every digest definition in place as a report and stopped every
-- gate from admitting a digest write, but left the digest kinds in the
-- definition check and the digest pipelines' write functions installed,
-- because the rows are converted after its migration runs. This file:
--   refuses to run, naming the count, while any schedule definition still
--     has a digest kind: the conversion command has not run on this
--     database, so this file ships in a release after the conversion's
--     (operations specification, "Report email conversion");
--   drops daily_digest and weekly_digest from the definition kind check.
--     The revision checks keep them: old revisions are immutable history;
--   drops the digest pipelines' write machinery, which no gate calls any
--     more (0161's closing block proves no gate names the write
--     admissions, and no digest task type or producer remains):
--       the write admissions and their mail writes
--         stewardship_daily_digest_write_admitted_v1,
--         stewardship_daily_digest_mail_write_v1,
--         stewardship_weekly_digest_write_admitted_v1,
--         stewardship_weekly_digest_mail_write_v1;
--       the dispatch writes and the render checks only they used
--         stewardship_daily_dispatch_write_v1, _render_v1 and the Weekly's;
--       the deferred commit checks on digest snapshot, ready and outbox
--         inserts (daily_digest_snapshot_complete, daily_digest_ready_complete,
--         daily_digest_mail_complete, weekly_snapshot_complete,
--         weekly_mail_complete) and their functions;
--       the settlement triggers on the digest preparations, task runs and
--         outbox messages (daily_digest_cohort_complete,
--         daily_digest_late_completion, daily_digest_delivery_complete and
--         the three weekly_* ones), their settle functions and the
--         completion and resolved checks only those used;
--       the Weekly manual request's allocation trigger and function.
--     The two mail commit checks ran, deferred, on every outbox message
--     insert (Family, report and alert mail alike), only to return at once.
-- Kept as history: every digest table and its rows; their insert guards,
-- which admit nothing once no digest definition remains; their
-- immutability, cleanup and pin retention triggers; the completion views;
-- and the read functions the saved digest pages, Sent mail's retry check
-- and delivery resolution still call (scope, live, page, predecessors,
-- replacement, dates, prior messages and items, seed and dispatch
-- liveness, Weekly history, observation and selection). The service
-- logins' write grants on the digest tables are revoked: the worker's and
-- the scheduler's on the preparations, snapshots, ready rows and
-- recipients, and web's on the Weekly Send now requests. Their read grants
-- stay for the saved copies, and the insert guards still refuse every
-- digest write.
--
-- Nothing is replaced, so no SECURITY DEFINER attribute needs restoring.
-- No view or SQL-standard body depends on a dropped function (plpgsql
-- bodies are not tracked, so the closing block searches every installed
-- body instead), and no CASCADE is used: an unexpected dependent object
-- fails the migration rather than being dropped with it. DROP ... IF
-- EXISTS because a fresh install's baseline already lacks them. The
-- scripted upgrade stops every application service before migrating. The
-- DO block at the end refuses to commit unless everything is as declared.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Never retire the kinds while a definition still has one: that database
-- has not run the conversion command, and the check below would refuse it
-- with a less helpful message.
DO $preflight$
DECLARE remaining bigint;
BEGIN
    SELECT count(*) INTO remaining FROM public.stewardship_schedule_definition
        WHERE kind IN ('daily_digest','weekly_digest');
    IF remaining>0 THEN
        RAISE EXCEPTION 'Migration 0171: % schedule definitions are still Daily or Weekly digests; upgrade to the release that converts them and run its report email conversion first (operations specification, Report email conversion)',
            remaining;
    END IF;
END
$preflight$;

ALTER TABLE "stewardship_schedule_definition" DROP CONSTRAINT "schedule_definition_kind";
ALTER TABLE "stewardship_schedule_definition" ADD CONSTRAINT "schedule_definition_kind" CHECK (((kind)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'report'::character varying])::text[])));

DROP TRIGGER IF EXISTS daily_digest_mail_complete ON public.stewardship_outbox_message;
DROP TRIGGER IF EXISTS daily_digest_delivery_complete ON public.stewardship_outbox_message;
DROP TRIGGER IF EXISTS daily_digest_late_completion ON public.stewardship_task_run;
DROP TRIGGER IF EXISTS daily_digest_cohort_complete ON public.stewardship_daily_digest_preparation;
DROP TRIGGER IF EXISTS daily_digest_snapshot_complete ON public.stewardship_daily_digest_snapshot;
DROP TRIGGER IF EXISTS daily_digest_ready_complete ON public.stewardship_daily_digest_ready;
DROP TRIGGER IF EXISTS weekly_mail_complete ON public.stewardship_outbox_message;
DROP TRIGGER IF EXISTS weekly_delivery_complete ON public.stewardship_outbox_message;
DROP TRIGGER IF EXISTS weekly_late_completion ON public.stewardship_task_run;
DROP TRIGGER IF EXISTS weekly_cohort_complete ON public.stewardship_weekly_digest_preparation;
DROP TRIGGER IF EXISTS weekly_snapshot_complete ON public.stewardship_weekly_digest_snapshot;
DROP TRIGGER IF EXISTS weekly_manual_allocate ON public.stewardship_weekly_manual_request;

DROP FUNCTION IF EXISTS public.stewardship_daily_digest_write_admitted_v1(text,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_mail_write_v1(uuid,text,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_mail_commit_v1();
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_source_commit_v1();
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_fact_commit_v1();
DROP FUNCTION IF EXISTS public.stewardship_daily_dispatch_write_v1(text,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.stewardship_daily_dispatch_render_v1(jsonb,uuid);
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_settle_v1();
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_completion_v1(jsonb);
DROP FUNCTION IF EXISTS public.stewardship_daily_digest_resolved_v1(uuid);
DROP FUNCTION IF EXISTS public.stewardship_weekly_digest_write_admitted_v1(text,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.stewardship_weekly_digest_mail_write_v1(uuid,text,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.stewardship_weekly_mail_commit_v1();
DROP FUNCTION IF EXISTS public.stewardship_weekly_snapshot_commit_v1();
DROP FUNCTION IF EXISTS public.stewardship_weekly_dispatch_write_v1(text,jsonb,jsonb);
DROP FUNCTION IF EXISTS public.stewardship_weekly_dispatch_render_v1(jsonb,uuid);
DROP FUNCTION IF EXISTS public.stewardship_weekly_digest_settle_v1();
DROP FUNCTION IF EXISTS public.stewardship_weekly_digest_completion_v1(jsonb);
DROP FUNCTION IF EXISTS public.stewardship_weekly_digest_resolved_v1(uuid);
DROP FUNCTION IF EXISTS public.stewardship_weekly_manual_allocate_v1();

-- The write grants on the digest tables end here, before database-grants
-- runs in the same upgrade (database-grants never revokes: it refuses a
-- login holding more than it declares, and it no longer declares them).
-- A login that does not exist (a fresh install before provisioning) has
-- nothing to revoke.
DO $revoke$
DECLARE login text;
BEGIN
    FOREACH login IN ARRAY ARRAY['pk_stewardship_worker','pk_stewardship_scheduler',
            'pk_stewardship_web'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=login) THEN
            EXECUTE format('REVOKE INSERT, UPDATE ON public.stewardship_daily_digest_preparation, '
                'public.stewardship_daily_digest_snapshot, public.stewardship_daily_digest_ready, '
                'public.stewardship_daily_digest_recipient, public.stewardship_weekly_digest_preparation, '
                'public.stewardship_weekly_digest_snapshot, public.stewardship_weekly_digest_recipient, '
                'public.stewardship_weekly_manual_request FROM %I', login);
        END IF;
    END LOOP;
END
$revoke$;

-- Refuse to commit unless the file is installed as declared: the check
-- admits no digest kind (the revision checks still do), nothing dropped
-- remains by name or is still named in any installed function or view
-- body, the read functions and guards kept for history are present, and
-- no service login keeps a write grant on a digest table.
DO $check$
DECLARE
    dropped text[]:=ARRAY[
        'stewardship_daily_digest_write_admitted_v1','stewardship_daily_digest_mail_write_v1',
        'stewardship_daily_digest_mail_commit_v1','stewardship_daily_digest_source_commit_v1',
        'stewardship_daily_digest_fact_commit_v1','stewardship_daily_dispatch_write_v1',
        'stewardship_daily_dispatch_render_v1','stewardship_daily_digest_settle_v1',
        'stewardship_daily_digest_completion_v1','stewardship_daily_digest_resolved_v1',
        'stewardship_weekly_digest_write_admitted_v1','stewardship_weekly_digest_mail_write_v1',
        'stewardship_weekly_mail_commit_v1','stewardship_weekly_snapshot_commit_v1',
        'stewardship_weekly_dispatch_write_v1','stewardship_weekly_dispatch_render_v1',
        'stewardship_weekly_digest_settle_v1','stewardship_weekly_digest_completion_v1',
        'stewardship_weekly_digest_resolved_v1','stewardship_weekly_manual_allocate_v1'];
    triggers text[]:=ARRAY[
        'daily_digest_mail_complete','daily_digest_delivery_complete',
        'daily_digest_late_completion','daily_digest_cohort_complete',
        'daily_digest_snapshot_complete','daily_digest_ready_complete',
        'weekly_mail_complete','weekly_delivery_complete','weekly_late_completion',
        'weekly_cohort_complete','weekly_snapshot_complete','weekly_manual_allocate'];
    named text:='\m('||array_to_string(dropped,'|')||')\M';
BEGIN
    IF (SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid='public.stewardship_schedule_definition'::regclass
          AND conname='schedule_definition_kind')
       IS DISTINCT FROM 'CHECK (((kind)::text = ANY (ARRAY[(''initial''::character varying)::text, (''reminder''::character varying)::text, (''report''::character varying)::text])))'
       OR (SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid='public.stewardship_schedule_revision'::regclass
          AND conname='schedule_revision_due_kind') NOT LIKE '%''weekly_digest''%' THEN
        RAISE EXCEPTION 'Migration 0171: the schedule kind checks are not as declared';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname=ANY(dropped))
       OR EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname=ANY(triggers)) THEN
        RAISE EXCEPTION 'Migration 0171: a digest write function or trigger remains';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prosrc ~ named)
       OR EXISTS(SELECT 1 FROM pg_views WHERE schemaname='public'
            AND definition ~ named) THEN
        RAISE EXCEPTION 'Migration 0171: an installed body still names a dropped digest function';
    END IF;
    IF to_regprocedure('public.stewardship_daily_dispatch_live_v1(uuid,boolean)') IS NULL
       OR to_regprocedure('public.stewardship_weekly_dispatch_live_v1(uuid,boolean)') IS NULL
       OR to_regprocedure('public.stewardship_daily_digest_seed_v1(uuid,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_weekly_digest_seed_v1(uuid,uuid)') IS NULL
       OR to_regclass('public.stewardship_daily_digest_completion_ready') IS NULL
       OR to_regclass('public.stewardship_weekly_digest_completion_ready') IS NULL
       OR (SELECT count(*) FROM pg_trigger t
            WHERE NOT t.tgisinternal AND t.tgname IN ('daily_digest_preparation_write',
                'daily_digest_snapshot_insert','daily_digest_ready_insert',
                'daily_digest_recipient_insert','daily_digest_source_retained',
                'daily_digest_facts_retained','weekly_digest_preparation_write',
                'weekly_snapshot_insert','weekly_recipient_insert','weekly_manual_guard',
                'weekly_snapshot_immutable','weekly_recipient_immutable'))<>12 THEN
        RAISE EXCEPTION 'Migration 0171: a digest read function, view or guard kept for history is missing';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles r
            CROSS JOIN unnest(ARRAY['stewardship_daily_digest_preparation',
                'stewardship_daily_digest_snapshot','stewardship_daily_digest_ready',
                'stewardship_daily_digest_recipient','stewardship_weekly_digest_preparation',
                'stewardship_weekly_digest_snapshot','stewardship_weekly_digest_recipient',
                'stewardship_weekly_manual_request']) d(name)
            CROSS JOIN unnest(ARRAY['INSERT','UPDATE']) w(privilege)
            WHERE r.rolname IN ('pk_stewardship_worker','pk_stewardship_scheduler',
                'pk_stewardship_web')
              AND has_table_privilege(r.oid,('public.'||d.name)::regclass,w.privilege)) THEN
        RAISE EXCEPTION 'Migration 0171: a service login can still write a digest table';
    END IF;
END
$check$;
