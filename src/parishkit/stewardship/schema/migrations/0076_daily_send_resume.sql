-- Frozen forward migration file 0076 (the repository-wide file sequence):
-- System health says when Family email sending resumes after the daily limit
-- ("Sending resumes at about 3:10 PM", ADM-13, #530). This file is installed
-- by its Django migration in the jobs app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- stewardship_family_daily_resume_v1() returns the earliest instant at which
-- the rolling 24-hour Family mail count (0012's
-- stewardship_family_daily_sends_v1(), which the mail consumers compare with
-- the daily limit) falls below the 1,600 recipients Family invitations,
-- reminders and Family tests may use, or NULL when the count is already
-- below it. Each counted submission leaves the window 24 hours after it was
-- recorded, so walking the counted submissions oldest first, the answer is
-- the first one whose leaving brings the rest below the limit. "Below", not
-- "at": the consumers hold a message while sent + recipients > limit, so at
-- exactly the limit a one-recipient message still waits. The page says
-- "about" for two reasons: a message with more recipients may wait a little
-- longer, and a message held at the limit is deferred, not woken: it is
-- tried again every 15 minutes plus up to 5 minutes of jitter
-- (jobs.family_mail_dispatch.CAPPED_RETRY_SECONDS; the first send can come up
-- to about 20 minutes after this instant).
-- Submissions recorded later can only move the answer later; the page reads
-- it again on every poll.
--
-- The answer needs each counted submission's recipient count, the length of
-- its render's routed recipient list, which holds addresses the web login
-- must never read; so, like 0012, the function is SECURITY DEFINER and
-- returns one instant. It takes no argument and the limit is fixed in its
-- body (jobs.family_mail_dispatch.DAILY_SEND_LIMIT - RESERVED_SENDS, which a
-- test keeps equal), so a caller cannot steer it at any message, Family,
-- time or threshold. The WHERE clause is 0012's (a test keeps both counts in
-- step) and is served by 0012's outbox_event_daily index. The window is
-- measured on timestamptz with a fixed-length interval, so neither the
-- session's time zone nor a daylight saving change moves it. EXECUTE goes
-- only to the web login (runtime_grants.runtime_functions); PUBLIC has none.
-- No table, column or other function changes.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_family_daily_resume_v1() RETURNS timestamp with time zone
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH counted AS (
        SELECT e.created_at, jsonb_array_length(r.routed_recipients) AS recipients
          FROM public.stewardship_outbox_event e
          JOIN public.stewardship_outbox_render r ON r.id=e.render_id
         WHERE e.previous_state='submitting'
           AND e.submitted_at IS NOT NULL
           AND e.reason IN ('smtp_accepted','smtp_delivery_unknown','recovery_unknown')
           AND e.created_at>=statement_timestamp()-interval '24 hours'
    ), walked AS (
        -- remaining: what stays counted once every submission recorded at or
        -- before this one has left the window (peers leave together).
        SELECT created_at,
               sum(recipients) OVER () - sum(recipients) OVER (ORDER BY created_at) AS remaining
          FROM counted
    )
    SELECT min(created_at)+interval '24 hours'
      FROM walked
     WHERE remaining<1600
       AND (SELECT coalesce(sum(recipients),0) FROM counted)>=1600
$$;
ALTER FUNCTION public.stewardship_family_daily_resume_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_family_daily_resume_v1() FROM PUBLIC;

-- Refuse to commit unless the function is installed exactly as declared: a
-- stable definer returning one timestamptz with the fixed search_path, with
-- the 1,600 limit in its body, which PUBLIC cannot execute. It reads only
-- the catalog and writes nothing. The missing function is checked first, on
-- its own, because PostgreSQL does not short-circuit OR and the
-- ::regprocedure casts below would otherwise fail with their own error
-- instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_family_daily_resume_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0076 (daily send resume time) is not installed as declared';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_family_daily_resume_v1()'::regprocedure
          AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
          AND p.prorettype='timestamp with time zone'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%remaining<1600%')
       OR has_function_privilege('public',
        'public.stewardship_family_daily_resume_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0076 (daily send resume time) is not installed as declared';
    END IF;
END
$check$;
