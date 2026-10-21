-- Frozen forward migration file 0162: a report send skips the recipients whose message for its slots the provider
-- already accepted (#1835, report-emails spec "Catch-up, recovery and
-- holds"). This file is installed by its Django migration in the reports
-- app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- It adds covered_messages to stewardship_report_email_recipient with a
-- third disposition, covered (no message of its own, and the earlier
-- accepted messages that covered every slot of the send), and the
-- comparison stewardship_report_email_prior_v1 that tells covered, uncertain
-- and none apart for one cohort address. It replaces frozen file 0156's
-- recipient guard, which now admits a covered row only with exactly the
-- comparison's messages and a queued row only when the comparison finds no
-- coverage and nothing unsettled, and frozen file 0161's completion view
-- (0156's view as the conversion replaced it), in which a covered
-- recipient is settled and counts as delivered. Every function is
-- invoker's rights, as 0156's recipient guard is. Nothing writes a report
-- recipient until the conversion admits report schedules. The file ends
-- with a DO block that refuses to commit unless all of it is installed as
-- declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The column and its check (reports/report_email_models.py). No row can
-- exist yet; the default only fills any that would, and is then dropped
-- so the column matches the model.
ALTER TABLE public.stewardship_report_email_recipient
    ADD COLUMN "covered_messages" jsonb DEFAULT '[]'::jsonb NOT NULL;
ALTER TABLE public.stewardship_report_email_recipient
    ALTER COLUMN "covered_messages" DROP DEFAULT;
ALTER TABLE public.stewardship_report_email_recipient
    DROP CONSTRAINT "report_email_recipient_disposition";
ALTER TABLE public.stewardship_report_email_recipient
    ADD CONSTRAINT "report_email_recipient_coverage" CHECK (
        (("covered_messages" = '[]'::jsonb) AND (("disposition")::text = 'queued'::text) AND ("outbox_id" IS NOT NULL))
        OR (("covered_messages" = '[]'::jsonb) AND (("disposition")::text = 'revoked'::text) AND ("outbox_id" IS NULL))
        OR ((("disposition")::text = 'covered'::text) AND ("outbox_id" IS NULL) AND (NOT ("covered_messages" = '[]'::jsonb))));

-- Whether one cohort address of a send already has, or may already have,
-- every slot the send covers. The earlier sends compared are the other
-- preparations of the same report (campaign, definition, mode and Testing
-- epoch) whose covered slots overlap this one's, and their messages to the
-- same address. It answers:
--   covered: delivered messages together cover every slot (their ids,
--     sorted, in "messages");
--   uncertain: otherwise, and one of those messages may still be or have
--     been accepted:
--     - submitting or delivery_unknown: the provider may have accepted it,
--       as after a crash between acceptance and its record;
--     - any message, other than one delivered or finally refused
--       (permanent_failure), that was ever delivery_unknown: a resend or
--       idempotent retry after an unknown outcome leaves an earlier
--       attempt that the provider may have accepted;
--     - pending, retry_wait or permanent_failure while its own occurrence
--       may still run: mail dispatch submits a report message only while
--       its occurrence is pending or running, and an occurrence that is
--       delivery_unknown or failed can return to pending, so only
--       succeeded, skipped and coalesced (final) close it; a missing
--       occurrence counts as open;
--   none: otherwise, including a send that covers no slot (Send now).
-- A cancelled message with no unknown outcome in its history is final and
-- was never accepted. Every doubt answers uncertain, never none, so the
-- comparison fails closed: a send waits rather than risk a second copy.
CREATE FUNCTION public.stewardship_report_email_prior_v1(snapshot uuid, recipient text)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH desired AS (
        SELECT p.id,p.campaign_id,p.definition_id,p.mode,p.rehearsal_epoch_id,p.covered_slots
        FROM public.stewardship_report_email_snapshot s
        JOIN public.stewardship_report_email_preparation p ON p.id=s.preparation_id
        WHERE s.id=$1 AND jsonb_typeof(p.covered_slots)='array'
          AND jsonb_array_length(p.covered_slots)>0
    ), earlier AS (
        SELECT m.id,m.state,prior.covered_slots,
            o.state IS NULL OR o.state NOT IN ('succeeded','skipped','coalesced') AS reopenable,
            EXISTS(SELECT 1 FROM public.stewardship_outbox_event ev
                WHERE ev.message_id=m.id AND ev.state='delivery_unknown') AS was_unknown
        FROM desired d
        JOIN public.stewardship_report_email_preparation prior
            ON prior.definition_id=d.definition_id AND prior.mode=d.mode
           AND prior.campaign_id=d.campaign_id AND prior.id<>d.id
           AND prior.rehearsal_epoch_id IS NOT DISTINCT FROM d.rehearsal_epoch_id
        JOIN public.stewardship_report_email_snapshot earlier_snapshot
            ON earlier_snapshot.preparation_id=prior.id
        JOIN public.stewardship_report_email_recipient r
            ON r.snapshot_id=earlier_snapshot.id AND r.address=$2
        JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
        LEFT JOIN public.stewardship_schedule_occurrence o ON o.id=prior.occurrence_id
        WHERE prior.covered_slots ?| ARRAY(SELECT jsonb_array_elements_text(d.covered_slots))
    )
    SELECT CASE
        WHEN NOT EXISTS(SELECT 1 FROM desired) THEN jsonb_build_object('state','none')
        WHEN NOT EXISTS(SELECT 1 FROM desired d
            CROSS JOIN LATERAL jsonb_array_elements_text(d.covered_slots) wanted(slot)
            WHERE NOT EXISTS(SELECT 1 FROM earlier e
                WHERE e.state='delivered' AND e.covered_slots ? wanted.slot))
            THEN jsonb_build_object('state','covered','messages',
                (SELECT jsonb_agg(e.id::text ORDER BY e.id::text) FROM earlier e
                 WHERE e.state='delivered'))
        WHEN EXISTS(SELECT 1 FROM earlier e
            WHERE e.state IN ('submitting','delivery_unknown')
               OR (e.was_unknown AND e.state NOT IN ('delivered','permanent_failure'))
               OR (e.state IN ('pending','retry_wait','permanent_failure') AND e.reopenable))
            THEN jsonb_build_object('state','uncertain')
        ELSE jsonb_build_object('state','none') END
$$;

-- 0156's recipient guard, with the coverage rule first (#1835): a covered
-- row names exactly the accepted messages the comparison finds, a queued
-- row is refused while the address is covered, and neither is admitted
-- while an earlier message may still be accepted (the worker retries until
-- it settles). The advisory lock it already takes serializes every
-- recipient decision, so two fanouts cannot both decide for one address.
-- The ownership checks after it are 0156's, with a covered row held to the
-- same recheck as a queued one.
CREATE OR REPLACE FUNCTION public.stewardship_report_email_recipient_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE s public.stewardship_report_email_snapshot%ROWTYPE;
        p public.stewardship_report_email_preparation%ROWTYPE; t public.stewardship_task_run%ROWTYPE;
        prior jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.disposition IN ('queued','covered') THEN
        prior:=public.stewardship_report_email_prior_v1(NEW.snapshot_id,NEW.address);
        IF prior->>'state'='uncertain' THEN
            RAISE EXCEPTION 'Report recipient waits for an earlier message to settle' USING ERRCODE='23514';
        END IF;
        IF NEW.disposition='queued' AND prior->>'state'='covered' THEN
            RAISE EXCEPTION 'Report recipient must reuse its accepted coverage' USING ERRCODE='23514';
        END IF;
        IF NEW.disposition='covered' AND (prior->>'state'<>'covered'
            OR NEW.covered_messages IS DISTINCT FROM prior->'messages') THEN
            RAISE EXCEPTION 'Report recipient lacks exact accepted coverage' USING ERRCODE='23514';
        END IF;
    END IF;
    SELECT * INTO s FROM public.stewardship_report_email_snapshot WHERE id=NEW.snapshot_id;
    SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=s.preparation_id;
    SELECT run.* INTO t FROM public.stewardship_task_run run
        JOIN public.stewardship_task_event e ON e.run_id=run.id AND e.action='claim'
            AND e.fence=run.fence AND e.worker_id=run.worker_id AND e.state='running'
        WHERE run.root_id=p.task_id AND e.id=NEW.correlation_id AND run.worker_id=NEW.actor_id;
    IF s.id IS NULL OR p.phase IS DISTINCT FROM 'fanout' OR t.id IS NULL
      OR NOT public.stewardship_report_email_live_v1(p.id,t.id,t.fence,t.worker_id)
      OR NOT s.recipients @> jsonb_build_array(jsonb_build_object(
          'address',NEW.address,'kind',NEW.kind,'ref',NEW.ref))
      OR (NEW.disposition='revoked' AND public.stewardship_report_email_current_v1(
          p.definition_id,NEW.kind,NEW.ref,NEW.address))
      OR (NEW.disposition='covered' AND NOT public.stewardship_report_email_current_v1(
          p.definition_id,NEW.kind,NEW.ref,NEW.address))
      OR (NEW.disposition='queued' AND NOT (public.stewardship_report_email_current_v1(
          p.definition_id,NEW.kind,NEW.ref,NEW.address) AND EXISTS(
          SELECT 1 FROM public.stewardship_outbox_message m
          JOIN public.stewardship_outbox_render content ON content.id=m.render_id
          WHERE m.id=NEW.outbox_id AND m.correlation_id=NEW.correlation_id
            AND m.actor_id=NEW.actor_id AND m.semantic_key=NEW.id
            AND m.scope_id=p.campaign_id AND m.campaign_id=p.campaign_id AND m.family_id IS NULL
            AND m.purpose='admin_report' AND m.mode=p.mode AND m.credential_namespace='none'
            AND m.state='pending'
            AND public.stewardship_report_email_render_v1(to_jsonb(content),s.id,NEW.address,NEW.kind,NEW.ref))))
    THEN RAISE EXCEPTION 'Report recipient lacks its exact cohort message' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- 0161's completion view, with a covered recipient settled and counted as
-- delivered: the provider accepted its earlier messages, and delivered is
-- final. Everything else is 0161's, including its own answer for a skipped
-- send, which the scheduler's finalizer can read without
-- stewardship_report_email_skipped_v1. Its columns and grants are
-- unchanged.
CREATE OR REPLACE VIEW public.stewardship_report_email_completion_ready AS
    SELECT p.id AS preparation_id,
        CASE WHEN skipped.value THEN 'nothing_to_report'
            WHEN EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
                LEFT JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE r.snapshot_id=s.id AND (m.state='delivered' OR r.disposition='covered'))
                THEN 'delivered'
            ELSE 'empty' END AS disposition
    FROM public.stewardship_report_email_preparation p
    JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
    CROSS JOIN LATERAL (SELECT NOT s.has_content
        AND coalesce((s.values->>'content_required')::boolean,false)
        AND EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
            WHERE o.id=p.occurrence_id AND o.slot !~ '^manual:') AS value) skipped
    WHERE p.phase='complete' AND (
        skipped.value
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.recipients) member
            WHERE NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
                LEFT JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE r.snapshot_id=s.id AND r.address=member->>'address'
                  AND (r.disposition IN ('revoked','covered') OR m.state='delivered'
                      OR (m.state='cancelled' AND m.reason='recipient_revoked')
                      OR (m.state='permanent_failure' AND NOT public.stewardship_report_email_current_v1(
                          p.definition_id,r.kind,r.ref,r.address))))));
REVOKE ALL ON public.stewardship_report_email_completion_ready FROM PUBLIC;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_attribute
        WHERE attrelid='public.stewardship_report_email_recipient'::regclass
          AND attname='covered_messages' AND NOT attisdropped AND attnotnull
          AND NOT atthasdef AND atttypid='jsonb'::regtype) THEN
        RAISE EXCEPTION 'Migration 0162: the recipient coverage column is missing';
    END IF;
    IF to_regclass('public.stewardship_report_email_recipient') IS NULL
       OR EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_report_email_recipient'::regclass
          AND conname='report_email_recipient_disposition')
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_report_email_recipient'::regclass
          AND conname='report_email_recipient_coverage' AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%''covered''::text%') THEN
        RAISE EXCEPTION 'Migration 0162: the recipient coverage check is not installed';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_report_email_prior_v1(uuid,text)')
          AND NOT p.prosecdef AND p.provolatile='s'
          AND strpos(p.prosrc,'was_unknown')>0
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'Migration 0162: the coverage comparison is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_report_email_recipient_guard_v1()')
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc,'stewardship_report_email_prior_v1(NEW.snapshot_id,NEW.address)')>0
          AND strpos(p.prosrc,'Report recipient waits for an earlier message to settle')>0
          AND strpos(p.prosrc,'Report recipient lacks its exact cohort message')>0) THEN
        RAISE EXCEPTION 'Migration 0162: the recipient guard does not check coverage';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_report_email_recipient'::regclass
          AND t.tgname='report_email_recipient_insert' AND NOT t.tgisinternal AND t.tgenabled='O'
          AND t.tgfoid=to_regprocedure('public.stewardship_report_email_recipient_guard_v1()')) THEN
        RAISE EXCEPTION 'Migration 0162: the recipient guard trigger is missing';
    END IF;
    IF strpos(pg_get_viewdef('public.stewardship_report_email_completion_ready'::regclass),
              '''covered''')=0 THEN
        RAISE EXCEPTION 'Migration 0162: the completion view does not settle covered recipients';
    END IF;
    -- 0161's change holds: the view answers a skipped send itself.
    IF strpos(pg_get_viewdef('public.stewardship_report_email_completion_ready'::regclass),
              'stewardship_report_email_skipped_v1')>0 THEN
        RAISE EXCEPTION 'Migration 0162: the completion view calls the skipped check';
    END IF;
    IF has_table_privilege('public','public.stewardship_report_email_completion_ready','SELECT') THEN
        RAISE EXCEPTION 'Migration 0162: the completion proof is readable by PUBLIC';
    END IF;
    -- A send that covers no slot (here: no such snapshot) is never covered.
    IF public.stewardship_report_email_prior_v1(gen_random_uuid(),'nobody@example.org')
       IS DISTINCT FROM '{"state":"none"}'::jsonb THEN
        RAISE EXCEPTION 'Migration 0162: the coverage comparison does not answer as declared';
    END IF;
END
$check$;
