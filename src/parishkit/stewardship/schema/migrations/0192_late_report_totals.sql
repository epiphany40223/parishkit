-- Frozen forward migration file 0192 (the repository-wide file sequence,
-- assigned by the coordinator): report totals count the responses Staff
-- enter after the campaign closes (#2298; reports spec, "Responses entered
-- after the campaign closes"). This file is installed by its Django
-- migration in the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- Staff and Administrators can enter or replace a Family's response through
-- Open form after the campaign closes (#2294, 0188), but the database capped
-- every daily fact generation at the campaign's end date, so those late
-- responses were left out of the Response dashboard, the report emails, the
-- comparison and the archive. Each report now runs to the campaign's report
-- end: the later of the end date and the campaign-local date of its newest
-- live response under the submission watermark in question. This file
-- creates nothing; it replaces three functions in place:
--   - stewardship_fact_set_guard: a generation's dates run from the start
--     date through its through_date, with no cap at the end date (its
--     callers cap through_date at the report end). Every generation built
--     before this has a through_date at or before the end date, so its
--     bounds read the same under the new rule.
--   - stewardship_exact_request_guard_v1: an exact export's through_date is
--     today capped at the report end under the request's watermark, rather
--     than at the end date.
--   - stewardship_report_email_preparation_guard_v1: a send's recorded
--     inputs are held at the report end under the send's own watermark, so
--     a send already made keeps its report day however many responses come
--     later.
-- reports/inputs.py (report_end, expected_dates) and
-- reports/report_email_inputs.py (send_inputs) compute the same in Python.
--
-- It ends with a DO block that refuses to commit unless every replacement
-- is installed as declared, with its owner, grants and settings unchanged.

SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0192_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text||'/'||p.prosecdef::text,';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_fact_set_guard','stewardship_exact_request_guard_v1',
        'stewardship_report_email_preparation_guard_v1')), true);

-- The fact-set guard: no cap at the end date.
CREATE OR REPLACE FUNCTION public.stewardship_fact_set_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE projection stewardship_campaign_configuration%ROWTYPE;
        input_source stewardship_source_snapshot%ROWTYPE;
        first_day date;
        last_day date;
        expected integer;
        actual bigint;
BEGIN
    IF TG_OP='DELETE' THEN
        IF NOT pg_try_advisory_xact_lock(736231, hashtext(OLD.id::text))
            OR NOT stewardship_fact_disposable(OLD.id) THEN
            RAISE EXCEPTION 'Fact generation is protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NOT stewardship_fact_live(NEW.task_id,NEW.task_fence,NEW.worker_id) THEN
        RAISE EXCEPTION 'Fact generation requires its live fenced builder'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.state<>'building' THEN
        RAISE EXCEPTION 'Fact generation must begin building' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.state='ready' OR NOT (
            (OLD.state='building' AND NEW.state IN ('building','ready','failed'))
            OR (OLD.state='failed' AND NEW.state='building')) THEN
            RAISE EXCEPTION 'Fact generation transition is invalid'
                USING ERRCODE='23514';
        END IF;
        IF ROW(NEW.task_id,NEW.task_fence,NEW.worker_id)
            IS DISTINCT FROM ROW(OLD.task_id,OLD.task_fence,OLD.worker_id)
            AND stewardship_fact_live(OLD.task_id,OLD.task_fence,OLD.worker_id) THEN
            RAISE EXCEPTION 'Live fact ownership cannot be replaced'
                USING ERRCODE='23514';
        END IF;
    END IF;
    SELECT * INTO projection FROM stewardship_campaign_configuration
        WHERE id=NEW.timezone_configuration_id;
    SELECT * INTO input_source FROM stewardship_source_snapshot WHERE id=NEW.source_id;
    IF projection.record_id IS DISTINCT FROM NEW.campaign_id
        OR input_source.state IS DISTINCT FROM 'promoted'
        OR input_source.generation IS DISTINCT FROM NEW.source_generation THEN
        RAISE EXCEPTION 'Fact inputs are inconsistent' USING ERRCODE='23514';
    END IF;
    -- The dates run through through_date, which may pass the end date:
    -- its callers cap it at the campaign's report end, so a response Staff
    -- entered after the close counts on its own day (0192, #2298).
    last_day := NEW.through_date;
    first_day := projection.start_date;
    expected := GREATEST(0,last_day-first_day+1);
    IF expected=0 THEN first_day:=NULL; last_day:=NULL; END IF;
    IF NEW.expected_count<>expected OR NEW.first_date IS DISTINCT FROM first_day
        OR NEW.last_date IS DISTINCT FROM last_day THEN
        RAISE EXCEPTION 'Fact date bounds differ from frozen campaign inputs'
            USING ERRCODE='23514';
    END IF;
    IF NEW.population_scope='current' AND (
        input_source.compacted_at IS NOT NULL OR NOT EXISTS(
            SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=NEW.source_id
                AND parent_kind='facts' AND parent_id=NEW.id)) THEN
        RAISE EXCEPTION 'Current facts require a retained exact source input'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state='ready' THEN
        SELECT count(*) INTO actual FROM stewardship_daily_fact
            WHERE fact_set_id=NEW.id;
        IF actual<>NEW.expected_count OR NEW.ready_at > clock_timestamp()
            OR NEW.failure_code<>'' OR EXISTS (
                SELECT 1 FROM (
                    SELECT cumulative_responses, sum(first_responses) OVER (
                        ORDER BY local_date) AS expected_cumulative
                    FROM stewardship_daily_fact WHERE fact_set_id=NEW.id
                ) counts WHERE cumulative_responses<>expected_cumulative
            ) THEN
            RAISE EXCEPTION 'Fact generation is incomplete or inconsistent'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- The exact export request guard: today capped at the report end.
CREATE OR REPLACE FUNCTION public.stewardship_exact_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.actor_id IS DISTINCT FROM NEW.requester_id
      OR NOT stewardship_export_authorized_v1(NEW.requester_id)
      OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
      OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
        WHERE active_configuration_id=NEW.configuration_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN stewardship_source_current s ON s.snapshot_id=NEW.source_id
        WHERE c.id=NEW.campaign_id AND p.id=NEW.timezone_configuration_id
          -- Today, capped at the report end: the end date, or the day of the
          -- newest live response under the watermark (0192, #2298).
          AND NEW.through_date=least(greatest(p.end_date,(SELECT max((r.submitted_at
              AT TIME ZONE stewardship_timezone_name_v1(p.timezone))::date)
            FROM stewardship_submission r WHERE r.campaign_id=NEW.campaign_id AND r.mode='live'
              AND r.campaign_sequence<=NEW.submission_watermark)),
            (stewardship_campaign_now_v1() AT TIME ZONE stewardship_timezone_name_v1(p.timezone))::date))
      OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
        FROM stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
      -- The campaign's current pledge review watermark too (0185, #2202).
      OR NEW.review_watermark<>public.stewardship_pledge_review_watermark_v1(NEW.campaign_id)
      OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
        WHERE name=stewardship_timezone_name_v1(NEW.browser_timezone))
      OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
        AND root_id=id AND task_type='report_exact_export' AND domain_request_id=NEW.id
        AND initiated_by_id=NEW.requester_id AND idempotency_key=NEW.id::text AND state='queued')
    THEN RAISE EXCEPTION 'Exact export lacks frozen authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- The report email preparation guard: held at the report end.
CREATE OR REPLACE FUNCTION public.stewardship_report_email_preparation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c public.stewardship_campaign_configuration%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE; expected jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Report preparation history is retained' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.cursor IS NOT NULL OR NEW.run_id IS NOT NULL
          OR NEW.task_fence IS NOT NULL OR NEW.worker_id IS NOT NULL
          OR NEW.covered_slots IS NOT NULL OR NEW.cutoff>public.stewardship_campaign_now_v1()
          OR NOT (
              (NEW.phase='dates' AND NEW.occurrence_id IS NULL)
              OR (NEW.phase='inputs' AND NEW.occurrence_id=NEW.id AND EXISTS(
                  SELECT 1 FROM public.stewardship_report_email_manual_request r
                  JOIN public.stewardship_schedule_occurrence o ON o.id=r.id
                  WHERE r.id=NEW.id AND r.task_id=NEW.task_id AND r.campaign_id=NEW.campaign_id
                    AND r.definition_id=NEW.definition_id AND r.revision_id=NEW.revision_id
                    AND o.definition_id=NEW.definition_id AND o.revision_id=NEW.revision_id
                    AND o.mode=NEW.mode AND o.target='admins' AND o.state='pending'
                    AND o.slot='manual:'||NEW.id::text)))
          OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
              JOIN public.stewardship_task_run t ON t.root_id=p.task_id
              WHERE p.definition_id=NEW.definition_id AND p.mode=NEW.mode
                AND p.phase NOT IN ('complete','cancelled')
                AND t.state IN ('queued','running','retry_wait','abandoned'))
          OR NOT public.stewardship_report_email_scope_v1(NEW.campaign_id,NEW.revision_id,
              NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id)
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition d
              WHERE d.id=NEW.definition_id AND d.campaign_id=NEW.campaign_id
                AND d.current_revision_id=NEW.revision_id AND d.kind='report')
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.task_id
              AND t.root_id=t.id AND t.task_type='report_email_prepare'
              AND t.domain_request_id=NEW.id AND t.idempotency_key=NEW.id::text
              AND t.state='queued' AND t.initiated_by_id IS NOT DISTINCT FROM NEW.actor_id)
        THEN RAISE EXCEPTION 'Report preparation lacks current schedule ownership' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.created_at,NEW.campaign_id,NEW.definition_id,NEW.revision_id,
          NEW.campaign_configuration_id,NEW.task_id,NEW.mode,NEW.rehearsal_epoch_id)
        IS DISTINCT FROM
       ROW(OLD.id,OLD.created_at,OLD.campaign_id,OLD.definition_id,OLD.revision_id,
          OLD.campaign_configuration_id,OLD.task_id,OLD.mode,OLD.rehearsal_epoch_id)
      OR (NEW.cutoff<>OLD.cutoff AND NOT (OLD.version=1 AND OLD.phase='dates'
          AND NEW.phase='dates' AND NEW.cursor IS NULL AND NEW.occurrence_id IS NULL
          AND NEW.cutoff>=OLD.cutoff AND NEW.cutoff<=public.stewardship_campaign_now_v1()))
      OR NEW.version<>OLD.version+1 OR OLD.phase IN ('complete','cancelled')
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.run_id
          AND t.root_id=OLD.task_id AND t.task_type='report_email_prepare'
          AND t.domain_request_id=OLD.id
          AND public.stewardship_fact_live(t.id,NEW.task_fence,NEW.worker_id))
      OR (NEW.phase<>'cancelled' AND NOT public.stewardship_report_email_scope_v1(
          NEW.campaign_id,NEW.revision_id,NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id))
      OR NOT (NEW.phase='cancelled' OR NEW.phase=OLD.phase
          OR (OLD.phase='dates' AND NEW.phase='cover')
          OR (OLD.phase='cover' AND NEW.phase IN ('inputs','complete'))
          OR (OLD.phase='inputs' AND NEW.phase='capture')
          OR (OLD.phase='capture' AND NEW.phase IN ('fanout','complete'))
          OR (OLD.phase='fanout' AND NEW.phase='complete'))
      OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor<OLD.cursor))
      OR (OLD.phase<>'dates' AND NEW.cursor IS DISTINCT FROM OLD.cursor)
      OR NOT public.stewardship_report_email_page_v1(to_jsonb(OLD),to_jsonb(NEW))
      OR (OLD.occurrence_id IS NOT NULL AND NEW.occurrence_id IS DISTINCT FROM OLD.occurrence_id)
      OR (NEW.phase IN ('inputs','capture','fanout') AND NEW.occurrence_id IS NULL)
      OR (NEW.phase<>'cancelled' AND NEW.occurrence_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM public.stewardship_schedule_occurrence o
          WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
            AND o.revision_id=NEW.revision_id AND o.mode=NEW.mode
            AND o.target='admins' AND o.due_at<=NEW.cutoff))
      -- The inputs are recorded exactly once, entering capture.
      OR (NOT (OLD.phase='inputs' AND NEW.phase='capture')
          AND ROW(NEW.covered_slots,NEW.as_of_kind,NEW.as_of,NEW.captured_at,NEW.report_day,
              NEW.source_id,NEW.submission_watermark,NEW.review_watermark,
              NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM
              ROW(OLD.covered_slots,OLD.as_of_kind,OLD.as_of,OLD.captured_at,OLD.report_day,
              OLD.source_id,OLD.submission_watermark,OLD.review_watermark,
              OLD.timezone_configuration_id,OLD.through_date))
    THEN RAISE EXCEPTION 'Report preparation requires a fenced monotonic transition' USING ERRCODE='23514'; END IF;
    IF OLD.phase='cover' AND NEW.phase IN ('inputs','complete') AND (EXISTS(
        SELECT 1 FROM public.stewardship_report_email_predecessors_v1(
            NEW.definition_id,NEW.mode,NEW.rehearsal_epoch_id)) OR EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=NEW.revision_id AND o.mode=NEW.mode AND o.target='admins'
          AND o.state='pending' AND o.task_id IS NULL AND o.outbox_id IS NULL
          AND o.due_at<=NEW.cutoff AND o.id IS DISTINCT FROM NEW.occurrence_id
          AND o.slot !~ '^manual:'
          AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation other
              WHERE other.id<>NEW.id AND other.occurrence_id=o.id))) THEN
        RAISE EXCEPTION 'Report coverage must finish before its inputs' USING ERRCODE='23514';
    END IF;
    IF OLD.phase='inputs' AND NEW.phase='capture' THEN
        SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=NEW.revision_id;
        SELECT p.* INTO c FROM public.stewardship_campaign x
            JOIN public.stewardship_campaign_configuration p ON p.id=x.active_configuration_id
            WHERE x.id=NEW.campaign_id;
        -- The report day is held at the report end, not the end date: the
        -- day of the newest live response under the send's watermark when
        -- Staff entered one after the close (0192, #2298).
        expected:=public.stewardship_report_email_inputs_v1(v.values,NEW.covered_slots,
            NEW.captured_at,c.start_date,greatest(c.end_date,(SELECT max((s.submitted_at
                AT TIME ZONE public.stewardship_timezone_name_v1(c.timezone))::date)
              FROM public.stewardship_submission s WHERE s.campaign_id=NEW.campaign_id
                AND s.mode='live' AND s.campaign_sequence<=NEW.submission_watermark)),c.timezone);
        IF NEW.covered_slots IS DISTINCT FROM public.stewardship_report_email_slots_v1(NEW.occurrence_id,NEW.mode)
          OR NEW.as_of_kind IS DISTINCT FROM v.values->>'as_of'
          -- Captured in this transaction, on the campaign clock (#1786's
          -- conversion: the clock the capture reads, which a test may fix).
          OR NEW.captured_at NOT BETWEEN public.stewardship_campaign_now_v1()
              -(statement_timestamp()-transaction_timestamp()) AND public.stewardship_campaign_now_v1()
          OR NEW.timezone_configuration_id IS DISTINCT FROM c.id
          OR NEW.as_of IS DISTINCT FROM (expected->>'as_of')::timestamptz
          OR NEW.report_day IS DISTINCT FROM (expected->>'report_day')::date
          OR NEW.through_date IS DISTINCT FROM (expected->>'through_date')::date
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_source_current current
              JOIN public.stewardship_source_snapshot s ON s.id=current.snapshot_id
              WHERE s.id=NEW.source_id AND s.state='promoted' AND s.compacted_at IS NULL)
          OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
              FROM public.stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
          -- The campaign's current pledge review watermark too (0185, #2202).
          OR NEW.review_watermark IS DISTINCT FROM
              public.stewardship_pledge_review_watermark_v1(NEW.campaign_id)
        THEN RAISE EXCEPTION 'Report inputs must be the exact current observation' USING ERRCODE='23514'; END IF;
    END IF;
    -- Fanout needs the snapshot, a recipient and something to send.
    IF NEW.phase='fanout' AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_snapshot s
        WHERE s.preparation_id=NEW.id AND s.recipients<>'[]'::jsonb
          AND NOT public.stewardship_report_email_skipped_v1(s.id)) THEN
        RAISE EXCEPTION 'Report fanout requires its committed snapshot with something to send' USING ERRCODE='23514';
    END IF;
    -- A capture completes without fanout only when nothing is to be sent:
    -- the send is skipped as empty, or it has no recipient.
    IF OLD.phase='capture' AND NEW.phase='complete' AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_report_email_snapshot s WHERE s.preparation_id=NEW.id
          AND (s.recipients='[]'::jsonb OR public.stewardship_report_email_skipped_v1(s.id))) THEN
        RAISE EXCEPTION 'Report capture completes only with nothing to send' USING ERRCODE='23514';
    END IF;
    IF OLD.phase='fanout' AND NEW.phase='complete' AND EXISTS(
        SELECT 1 FROM public.stewardship_report_email_snapshot s
        CROSS JOIN LATERAL jsonb_array_elements(s.recipients) member
        WHERE s.preparation_id=NEW.id AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_report_email_recipient r
            WHERE r.snapshot_id=s.id AND r.address=member->>'address')) THEN
        RAISE EXCEPTION 'Report completion requires every cohort recipient' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_fact_set_guard()'::regprocedure
            AND prosrc LIKE '%last_day := NEW.through_date;%'
            AND prosrc NOT LIKE '%LEAST(projection.end_date,NEW.through_date)%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_exact_request_guard_v1()'::regprocedure
            AND prosrc LIKE '%AND NEW.through_date=least(greatest(p.end_date,(SELECT max((r.submitted_at%'
            AND prosrc LIKE '%AND r.campaign_sequence<=NEW.submission_watermark)),%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_report_email_preparation_guard_v1()'::regprocedure
            AND prosrc LIKE '%NEW.captured_at,c.start_date,greatest(c.end_date,(SELECT max((s.submitted_at%'
            AND prosrc LIKE '%AND s.mode=''live'' AND s.campaign_sequence<=NEW.submission_watermark)),c.timezone);%') THEN
        RAISE EXCEPTION 'Migration 0192 (late report totals) did not replace the guards';
    END IF;
    -- CREATE OR REPLACE keeps owners and grants and names the same
    -- settings; check that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,'')
            ||'/'||p.proowner::regrole::text||'/'||p.prosecdef::text,';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_fact_set_guard','stewardship_exact_request_guard_v1',
        'stewardship_report_email_preparation_guard_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0192_acl') THEN
        RAISE EXCEPTION 'Migration 0192 changed the replaced functions'' owners, grants or settings';
    END IF;
END
$check$;
