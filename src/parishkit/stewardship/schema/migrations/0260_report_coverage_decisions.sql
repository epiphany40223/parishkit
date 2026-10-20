-- Frozen forward migration file 0260 (provisional: the repository-wide file
-- sequence assigns the final number when the pull request merges): a report
-- send that waits on an earlier message says which message, and an
-- Administrator can settle an earlier message that nothing else ever will
-- (#1872, report-emails spec "Catch-up, recovery and holds"). This file is
-- installed by its Django migration in the reports app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- It creates stewardship_report_email_coverage_decision, an immutable
-- Administrator decision about one earlier report message whose outcome was
-- once unknown and which then ended cancelled: delivered (it covers its
-- slots) or unsent (it never did). Its insert guard admits only such a
-- message, at its current version, together with the evidence note of the
-- same id, and records the audit event. It replaces frozen file 0250's
-- comparison stewardship_report_email_prior_v1 so that a decided message
-- counts as decided and an uncertain answer names the messages it waits on
-- ("waiting"); the recipient guard, which compares only the answer's state
-- and covered messages, is unchanged. The Administrator-only read of the
-- sends that wait is frozen file 0261's. Every object here depends on
-- frozen files 0156 and 0250 being installed first.
-- Nothing writes a report message until the conversion admits report
-- schedules. The file ends with a DO block that refuses to commit unless
-- all of it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The decision table (reports/report_email_models.py). Its id is the
-- evidence note's (stewardship_delivery_resolution), so a replayed command
-- finds both; message_id names an outbox message without a foreign key, as
-- the resolution journal it extends does, so a Testing cleanup is never
-- blocked by it.
CREATE TABLE "stewardship_report_email_coverage_decision" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "message_id" uuid NOT NULL UNIQUE, "decision" varchar(16) NOT NULL, CONSTRAINT "report_email_coverage_decision_known" CHECK ("decision"::text = ANY(ARRAY['delivered'::varchar::text,'unsent'::varchar::text])));
CREATE INDEX "stewardship_report_email_c_correlation_id_fb846c25" ON "stewardship_report_email_coverage_decision" ("correlation_id");
CREATE TRIGGER report_email_coverage_decision_immutable BEFORE UPDATE OR DELETE
    ON public.stewardship_report_email_coverage_decision
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_recipient_immutable_v1();

-- The decision's insert guard, with owner rights: web may insert a
-- decision but reads neither the report cohort nor every outbox event
-- column it checks. It takes the advisory lock every report recipient decision
-- takes (the recipient guard's), so a fanout and a decision about the same
-- message are serialized. It admits a decision only from the web login,
-- for an Administrator, about an admin_report message of a report's
-- cohort that is cancelled and whose history includes delivery_unknown,
-- together with the evidence note of the same id (a note, by the same
-- Administrator, at the message's current version, recorded in this same
-- transaction, so an older note cannot be reused), while the campaign
-- accepts Admin changes and is not archived. One decision per message: the
-- unique message_id refuses a second.
CREATE FUNCTION public.stewardship_report_email_coverage_decision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE m public.stewardship_outbox_message%ROWTYPE;
        note public.stewardship_delivery_resolution%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=NEW.message_id FOR UPDATE;
    SELECT * INTO note FROM public.stewardship_delivery_resolution WHERE id=NEW.id;
    IF session_user<>'pk_stewardship_web'
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,true)
       OR NEW.correlation_id IS DISTINCT FROM NEW.id
       OR m.id IS NULL OR m.purpose<>'admin_report' OR m.state<>'cancelled'
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
           WHERE r.outbox_id=m.id AND r.id=m.semantic_key)
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_outbox_event e
           WHERE e.message_id=m.id AND e.state='delivery_unknown')
       OR note.id IS NULL OR note.action<>'note' OR note.message_id<>m.id
       OR note.actor_id IS DISTINCT FROM NEW.actor_id OR note.expected_version<>m.version
       OR note.created_at<transaction_timestamp()
       OR NOT public.stewardship_export_admitted_v1(m.campaign_id,true)
       OR EXISTS(SELECT 1 FROM public.stewardship_campaign WHERE id=m.campaign_id AND state='archived')
    THEN RAISE EXCEPTION 'Report coverage decision lacks current exact Admin authority'
        USING ERRCODE='23514'; END IF;
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.id,'report_email_coverage_'||NEW.decision,NEW.id,m.campaign_id);
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_report_email_coverage_decision_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_coverage_decision_guard_v1() FROM PUBLIC;
CREATE TRIGGER report_email_coverage_decision_insert BEFORE INSERT
    ON public.stewardship_report_email_coverage_decision
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_coverage_decision_guard_v1();

-- 0250's comparison of one cohort address with the report's earlier sends,
-- with two changes (#1872):
--   - an earlier message an Administrator decided about counts as decided:
--     delivered covers its slots (and is named in "messages"), unsent never
--     does and is final. Only a cancelled, once-unknown message can have a
--     decision, so every other rule reads as 0250's;
--   - an uncertain answer names, in "waiting" (sorted ids), the earlier
--     messages that make it uncertain, for the waiting read (frozen file
--     0261). The recipient guard reads only "state" and "messages".
-- Every doubt still answers uncertain, never none: a send waits rather
-- than risk a second copy.
CREATE OR REPLACE FUNCTION public.stewardship_report_email_prior_v1(snapshot uuid, recipient text)
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
                WHERE ev.message_id=m.id AND ev.state='delivery_unknown') AS was_unknown,
            -- Read only for a cancelled message, the one kind the guard
            -- admits a decision for, so no other doubt can be voided.
            CASE WHEN m.state='cancelled' THEN
                (SELECT d.decision FROM public.stewardship_report_email_coverage_decision d
                 WHERE d.message_id=m.id) END AS decision
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
    ), judged AS (
        SELECT e.id,e.covered_slots,
            e.state='delivered' OR e.decision IS NOT DISTINCT FROM 'delivered' AS accepted,
            e.state IN ('submitting','delivery_unknown')
              OR (e.was_unknown AND e.decision IS NULL
                  AND e.state NOT IN ('delivered','permanent_failure'))
              OR (e.state IN ('pending','retry_wait','permanent_failure') AND e.reopenable)
              AS doubtful
        FROM earlier e
    )
    SELECT CASE
        WHEN NOT EXISTS(SELECT 1 FROM desired) THEN jsonb_build_object('state','none')
        WHEN NOT EXISTS(SELECT 1 FROM desired d
            CROSS JOIN LATERAL jsonb_array_elements_text(d.covered_slots) wanted(slot)
            WHERE NOT EXISTS(SELECT 1 FROM judged j
                WHERE j.accepted AND j.covered_slots ? wanted.slot))
            THEN jsonb_build_object('state','covered','messages',
                (SELECT jsonb_agg(j.id::text ORDER BY j.id::text) FROM judged j
                 WHERE j.accepted))
        WHEN EXISTS(SELECT 1 FROM judged j WHERE j.doubtful)
            THEN jsonb_build_object('state','uncertain','waiting',
                (SELECT jsonb_agg(j.id::text ORDER BY j.id::text) FROM judged j
                 WHERE j.doubtful))
        ELSE jsonb_build_object('state','none') END
$$;

DO $check$
BEGIN
    IF to_regclass('public.stewardship_report_email_coverage_decision') IS NULL
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_report_email_coverage_decision'::regclass
          AND conname='report_email_coverage_decision_known' AND contype='c')
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_report_email_coverage_decision'::regclass
          AND contype='u' AND conkey=ARRAY[(SELECT attnum FROM pg_attribute
              WHERE attrelid='public.stewardship_report_email_coverage_decision'::regclass
                AND attname='message_id')]) THEN
        RAISE EXCEPTION 'Migration 0260: the coverage decision table is not installed';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_report_email_coverage_decision_guard_v1()')
          AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc,'Report coverage decision lacks current exact Admin authority')>0
          AND strpos(p.prosrc,'''report_email_coverage_''||NEW.decision')>0
          AND strpos(p.prosrc,'pg_advisory_xact_lock(736220,1)')>0
          AND strpos(p.prosrc,'note.expected_version<>m.version')>0
          AND strpos(p.prosrc,'note.created_at<transaction_timestamp()')>0
          AND strpos(p.prosrc,'m.state<>''cancelled''')>0)
       OR has_function_privilege('public',
          'public.stewardship_report_email_coverage_decision_guard_v1()','EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0260: the coverage decision guard is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_report_email_coverage_decision'::regclass
          AND NOT t.tgisinternal AND t.tgenabled='O'
          AND ((t.tgname='report_email_coverage_decision_insert'
                AND t.tgfoid=to_regprocedure('public.stewardship_report_email_coverage_decision_guard_v1()'))
            OR (t.tgname='report_email_coverage_decision_immutable'
                AND t.tgfoid=to_regprocedure('public.stewardship_recipient_immutable_v1()'))))<>2 THEN
        RAISE EXCEPTION 'Migration 0260: the coverage decision triggers are missing';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_report_email_prior_v1(uuid,text)')
          AND NOT p.prosecdef AND p.provolatile='s'
          AND strpos(p.prosrc,'stewardship_report_email_coverage_decision')>0
          AND strpos(p.prosrc,'CASE WHEN m.state=''cancelled'' THEN')>0
          AND strpos(p.prosrc,'''waiting''')>0
          AND strpos(p.prosrc,'was_unknown')>0
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'Migration 0260: the coverage comparison is not installed as declared';
    END IF;
    -- 0250's recipient guard still reads the comparison it replaces here.
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_report_email_recipient_guard_v1()')
          AND strpos(p.prosrc,'stewardship_report_email_prior_v1(NEW.snapshot_id,NEW.address)')>0) THEN
        RAISE EXCEPTION 'Migration 0260: the recipient guard no longer reads the comparison';
    END IF;
    -- A send that covers no slot (here: no such snapshot) is never covered.
    IF public.stewardship_report_email_prior_v1(gen_random_uuid(),'nobody@example.org')
       IS DISTINCT FROM '{"state":"none"}'::jsonb THEN
        RAISE EXCEPTION 'Migration 0260: the coverage comparison does not answer as declared';
    END IF;
END
$check$;
