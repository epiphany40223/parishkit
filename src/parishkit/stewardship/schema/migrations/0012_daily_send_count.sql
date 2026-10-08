-- Frozen forward migration file 0012 (the repository-wide file sequence;
-- Django's stewardship_jobs.0010): the System health page's 24-hour Family
-- mail count (ADM-13 PR 2c, #530) and the daily-count index (#382 item L9).
-- This file is installed by jobs/migrations/0010_daily_send_count.py and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. A later change gets its own numbered file. A fresh install
-- runs the baseline, 0002 to 0011 and then this, and ends in the same catalog
-- as an upgraded database.
--
-- stewardship_family_daily_sends_v1() returns the number of recipients this
-- deployment handed to the mail provider in the last 24 hours: the count the
-- mail consumers compare with the daily limit
-- (jobs.family_mail_dispatch.sends_in_last_day, which a test keeps equal to
-- it). Accepted and uncertain submissions count; definitive refusals do not.
-- The count needs the length of each render's routed recipient list, which
-- holds recipient addresses the web login must never read, so the function
-- is SECURITY DEFINER and returns one number. It takes no argument, so a
-- caller cannot steer it at any message, Family or time. The window is the
-- 24 hours before the statement's timestamp, measured on timestamptz with a
-- fixed-length interval, so neither the session's time zone nor a daylight
-- saving change moves it. EXECUTE goes only to the web login
-- (database-grants, runtime_grants.runtime_functions); PUBLIC has none.
--
-- outbox_event_daily on (previous_state, created_at) serves that count and
-- the provider-acceptance check (accepted_since), which the mail consumers
-- re-read every few seconds near the limit and which otherwise scan the
-- whole event table (#382 L9). Plain CREATE INDEX, not CONCURRENTLY: this
-- file runs in the migration's transaction, where CONCURRENTLY is not
-- allowed. The table holds tens of thousands of rows, so the build takes
-- about a second under a lock that blocks only writes to this table, during
-- the deploy.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE INDEX "outbox_event_daily" ON "stewardship_outbox_event" ("previous_state", "created_at");

CREATE FUNCTION public.stewardship_family_daily_sends_v1() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce(sum(jsonb_array_length(r.routed_recipients)),0)::bigint
      FROM public.stewardship_outbox_event e
      JOIN public.stewardship_outbox_render r ON r.id=e.render_id
     WHERE e.previous_state='submitting'
       AND e.submitted_at IS NOT NULL
       AND e.reason IN ('smtp_accepted','smtp_delivery_unknown','recovery_unknown')
       AND e.created_at>=statement_timestamp()-interval '24 hours'
$$;
REVOKE ALL ON FUNCTION public.stewardship_family_daily_sends_v1() FROM PUBLIC;

-- Refuse to commit unless the index and the function are installed as
-- declared: a valid plain B-tree over exactly these two columns in this
-- order, and a definer function with the fixed search_path that PUBLIC
-- cannot execute.
DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_am am ON am.oid=c.relam
        WHERE c.relname='outbox_event_daily'
          AND c.relnamespace='public'::regnamespace
          AND i.indrelid='public.stewardship_outbox_event'::regclass
          AND am.amname='btree' AND i.indisvalid AND NOT i.indisunique
          AND i.indpred IS NULL AND i.indexprs IS NULL
          AND i.indnatts=2
          AND i.indkey[0]=(SELECT attnum FROM pg_attribute
              WHERE attrelid='public.stewardship_outbox_event'::regclass
                AND attname='previous_state')
          AND i.indkey[1]=(SELECT attnum FROM pg_attribute
              WHERE attrelid='public.stewardship_outbox_event'::regclass
                AND attname='created_at')) THEN
        RAISE EXCEPTION 'The outbox_event_daily index was not created';
    END IF;
    IF to_regprocedure('public.stewardship_family_daily_sends_v1()') IS NULL THEN
        RAISE EXCEPTION 'The daily send count function was not created';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_family_daily_sends_v1()'::regprocedure
          AND p.prosecdef AND p.provolatile='s'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'The daily send count function has the wrong security or search_path';
    END IF;
    IF has_function_privilege('public',
        'public.stewardship_family_daily_sends_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'PUBLIC may execute the daily send count function';
    END IF;
END
$check$;
