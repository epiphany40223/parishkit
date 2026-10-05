-- Frozen forward migration file 0005 (the repository-wide file sequence;
-- Django's stewardship_campaigns.0003): Family mail preparation may claim a
-- Production occurrence up to two hours before its due time (BG-12, #447).
-- This file is installed by campaigns/migrations/0003_occurrence_prepare_ahead.py
-- and must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that this copy of the guard still equals the
-- fresh-install baseline's (schema/functions.sql). A later change to the guard
-- gets its own numbered file. A fresh install runs 0001 through 0004 and then
-- this; functions.sql already carries this body, so the install ends in the
-- same catalog as an upgraded database.
--
-- Before BG-12 the guard's fenced running-claim branch refused every claim on
-- an occurrence that was not yet due (NEW.due_at>instant). The bulk Family
-- send now prepares a Production reminder in a lead window of two hours
-- before its due time, so that branch refuses a family_mail_prepare claim on
-- a Production occurrence only when it is due more than two hours from now,
-- and refuses every other task type, and every Testing claim, exactly as
-- before. Nothing else in the guard changes. Sending is unaffected: a
-- delivery claim still waits for the due time, and the dispatch guard
-- (stewardship_family_dispatch_live_v1) is unchanged. No credential, code,
-- link token or token lookup changes. Reversing needs its own forward
-- migration that restores the old condition.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_occurrence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE d stewardship_schedule_definition%ROWTYPE; c stewardship_campaign%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; t stewardship_task_run%ROWTYPE;
    instant timestamptz := stewardship_campaign_now_v1();
    digest_completed boolean := false;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('occurrences',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Occurrence history requires exceptional retention' USING ERRCODE='23514';
    END IF;
    SELECT * INTO d FROM stewardship_schedule_definition WHERE id=NEW.definition_id FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF NOT EXISTS(SELECT 1 FROM stewardship_schedule_revision WHERE id=NEW.revision_id AND record_id=d.id AND campaign_id=d.campaign_id)
       OR NEW.target='' OR NEW.slot='' OR NEW.occurrence_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid occurrence identity' USING ERRCODE='23514'; END IF;
    IF (TG_OP='INSERT' OR NEW.state IN ('pending','running'))
       AND NEW.production_cycle IS DISTINCT FROM (CASE WHEN NEW.mode='production' THEN c.production_cycle ELSE 0 END) THEN
        RAISE EXCEPTION 'Occurrence belongs to a retired Production cycle' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('succeeded','failed','skipped','coalesced') AND EXISTS (
        SELECT 1 FROM stewardship_outbox_message m WHERE m.id=NEW.outbox_id
          AND public.stewardship_occurrence_delivery_conflict_v1(NEW.state,m.state)
    ) THEN RAISE EXCEPTION 'Occurrence outcome contradicts terminal delivery'
        USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NOT public.stewardship_initial_recovery_valid_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Initial recovery requires a new deliverability transition'
                USING ERRCODE='23514';
        END IF;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.fence<>0 OR NEW.attempts<>0
           OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR NEW.mode<>r.mode OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
               AND ((instant>=p.starts_at AND instant<p.ends_at
                   AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                   OR (NEW.mode='production' AND c.state='closed'
                       AND (d.kind IN ('daily_digest','weekly_digest')
                           OR (NEW.due_at>=p.starts_at AND NEW.due_at<p.ends_at)))))
           OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
               AND NOT public.stewardship_catchup_materializing_v1(c.id,NEW.actor_id,NEW.correlation_id))
           OR EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
           OR EXISTS (SELECT 1 FROM stewardship_postclose_resolution resolved
               JOIN public.stewardship_postclose_current covered ON covered.id=resolved.id
               WHERE resolved.campaign_id=c.id AND resolved.mode=NEW.mode
                   AND resolved.obligation_key='schedule:'||d.id::text||':'||NEW.slot) THEN
            RAISE EXCEPTION 'Occurrence creation is not admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF session_user='pk_stewardship_scheduler' AND (
            OLD.state<>'pending' OR NEW.state NOT IN ('skipped','coalesced')
            OR OLD.task_id IS NOT NULL OR OLD.outbox_id IS NOT NULL
            OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
            OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode
            OR r.restore_review_required
            OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate
                WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
            OR (NEW.state='coalesced' AND NOT EXISTS(
                SELECT 1 FROM stewardship_schedule_occurrence selected
                WHERE selected.id=NEW.replacement_id AND selected.target=NEW.target
                  AND selected.mode=NEW.mode AND selected.state='pending'
                  AND selected.task_id IS NULL AND selected.outbox_id IS NULL
            ))
        ) THEN RAISE EXCEPTION 'Scheduler may only reconcile unallocated pending work'
            USING ERRCODE='23514'; END IF;
        IF (OLD.state='pending' AND NEW.state NOT IN ('pending','running','skipped','coalesced'))
           OR (OLD.state='running' AND NEW.state NOT IN ('running','pending','delivery_unknown','succeeded','failed','skipped','coalesced'))
           OR (OLD.state='delivery_unknown' AND NEW.state NOT IN ('succeeded','pending','failed'))
           OR (OLD.state='failed' AND NEW.state NOT IN ('pending','skipped'))
           OR OLD.state IN ('succeeded','skipped','coalesced') THEN
            RAISE EXCEPTION 'Invalid occurrence transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='running' THEN
            -- Branch before planning private report reads: SQL boolean order is
            -- not a privilege boundary for unrelated Family/daily mail owners.
            IF d.kind='weekly_digest' THEN
                digest_completed:=public.stewardship_weekly_digest_completion_v1(to_jsonb(NEW));
            ELSE
                digest_completed:=public.stewardship_daily_digest_completion_v1(to_jsonb(NEW));
            END IF;
        END IF;
        IF NEW.state='running' AND NOT digest_completed THEN
            SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
            IF NOT FOUND OR t.state<>'running' OR t.worker_id<>NEW.worker_id OR t.fence<>NEW.fence OR t.lease_expires_at<=clock_timestamp()
               OR NEW.lease_expires_at>t.lease_expires_at OR NEW.heartbeat_at>clock_timestamp()
               OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
               OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode OR r.restore_review_required
               OR NOT ((t.task_type='schedule_occurrence' AND t.domain_request_id IS NOT DISTINCT FROM NEW.id)
                   OR (t.task_type='family_mail_prepare' AND EXISTS (
                       SELECT 1 FROM public.stewardship_family_mail_preparation q
                       WHERE q.id=t.domain_request_id AND q.task_id=t.root_id
                         AND q.occurrence_id=NEW.id AND q.mode=NEW.mode))
                   OR (t.task_type='outbox_delivery' AND EXISTS (
                       SELECT 1 FROM public.stewardship_outbox_message mail
                       WHERE mail.id=t.domain_request_id AND mail.task_id=t.root_id
                         AND mail.id=NEW.outbox_id AND mail.semantic_key=NEW.id
                         AND mail.mode=NEW.mode AND mail.family_id::text=substring(NEW.target FROM 8))))
               OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
                   AND ((instant>=p.starts_at AND instant<p.ends_at
                       AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                       OR (NEW.mode='production' AND c.state='closed' AND d.kind IN ('daily_digest','weekly_digest'))))
               -- Not yet due. Only Family mail preparation of a Production
               -- occurrence may claim it early, within the two-hour lead
               -- window (BG-12, #447). Every other claim, including a
               -- delivery claim, and the dispatch guard still wait for it.
               OR (NEW.due_at>instant AND NOT (t.task_type='family_mail_prepare'
                   AND NEW.mode='production' AND NEW.due_at<=instant+interval '2 hours'))
               OR (NEW.mode='production' AND c.delivery_paused)
               OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
               OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
               OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h WHERE h.definition_id=d.id AND h.mode=NEW.mode
                   AND h.target=NEW.target AND h.slot=NEW.slot AND h.state IN ('unreviewed','assumed_delivered'))
               OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
                RAISE EXCEPTION 'Occurrence claim requires current fenced work' USING ERRCODE='23514'; END IF;
        END IF;
        IF OLD.state='running' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NEW.fence<>OLD.fence) THEN
            RAISE EXCEPTION 'Occurrence worker identity changed' USING ERRCODE='23514'; END IF;
        IF OLD.state='running' AND NOT (
            (NEW.state='skipped' OR (NEW.state='coalesced' AND NEW.reason IN (
                'missed_family_recovery','missed_daily_recovery','missed_weekly_recovery')))
            AND public.stewardship_schedule_effect_v1(
                OLD.id,OLD.version,NEW.actor_id,NEW.correlation_id,NEW.reason)
        ) AND NOT EXISTS(SELECT 1 FROM stewardship_task_run owner_task WHERE owner_task.id=OLD.task_id
            AND ((owner_task.state='running' AND owner_task.worker_id=NEW.actor_id AND owner_task.fence=NEW.fence AND owner_task.lease_expires_at>clock_timestamp())
                OR (owner_task.state IN ('abandoned','cancelled','succeeded','failed') AND owner_task.fence>=OLD.fence AND NEW.actor_id IS NOT NULL
                    AND NEW.reason IN ('recovery_retry','recovery_unknown','recovery_complete','recovery_fail','recovery_skip','recovery_coalesce')))) THEN
            RAISE EXCEPTION 'Occurrence write requires live worker fencing' USING ERRCODE='23514'; END IF;
        IF NEW.state<>'running' AND OLD.state<>'running' AND NEW.fence<>OLD.fence THEN
            RAISE EXCEPTION 'Only a new claim advances occurrence fencing' USING ERRCODE='23514'; END IF;
        IF OLD.state='delivery_unknown' AND (
            NEW.actor_id IS NULL
            OR NEW.reason IS DISTINCT FROM CASE NEW.state WHEN 'pending' THEN 'recovery_retry'
                WHEN 'succeeded' THEN 'recovery_complete' WHEN 'failed' THEN 'recovery_fail' END
            OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
            OR (NOT public.stewardship_delivery_resolution_edge_v1(to_jsonb(OLD),to_jsonb(NEW))
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run reconciled WHERE reconciled.id=OLD.task_id
                AND reconciled.task_type='schedule_occurrence' AND reconciled.domain_request_id=OLD.id
                AND reconciled.fence>=OLD.fence AND reconciled.state IN ('abandoned','cancelled','succeeded','failed')))
        ) THEN RAISE EXCEPTION 'Unknown delivery requires attributed reconciled ownership' USING ERRCODE='23514'; END IF;
        IF NEW.state='pending' AND OLD.state='failed' AND (NEW.retry_command_id IS NULL OR NEW.revision_id IS DISTINCT FROM d.current_revision_id) THEN
            RAISE EXCEPTION 'Occurrence retry requires explicit current identity' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND OLD.state='failed' AND NEW.reason NOT IN ('schedule_removed','schedule_replaced') THEN
            RAISE EXCEPTION 'Failed occurrence cannot be silently discarded' USING ERRCODE='23514'; END IF;
        IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state<>'running' AND NEW.state='running' THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Occurrence attempt count is inconsistent' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='coalesced' AND (NEW.replacement_id=NEW.id OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence replacement JOIN stewardship_schedule_definition rd ON rd.id=replacement.definition_id
        WHERE replacement.id=NEW.replacement_id AND rd.campaign_id=d.campaign_id AND replacement.mode=NEW.mode
          AND replacement.state NOT IN ('skipped','coalesced','failed')
    )) THEN RAISE EXCEPTION 'Invalid occurrence coalescing target' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $_$;

-- CREATE OR REPLACE keeps the owner and grants but resets every attribute the
-- command does not name, so restore what the baseline gives this guard
-- (schedule_reconciliation.sql): it runs as its owner, reading the private
-- schedule journal its callers cannot. Without it the guard would run with
-- its caller's rights, and the worker, which may not execute the journal's
-- functions, would have its occurrence updates refused.
ALTER FUNCTION public.stewardship_occurrence_guard_v1() SECURITY DEFINER;

-- Refuse to commit unless the new body is installed with its owner's rights
-- and no PUBLIC execute grant, so an upgrade cannot report success while the
-- old guard, or a weakened one, is in place.
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public'
                     AND p.proname='stewardship_occurrence_guard_v1'
                     AND p.prosrc LIKE '%NOT (t.task_type=''family_mail_prepare''%'
                     AND p.prosrc LIKE '%NEW.due_at<=instant+interval ''2 hours''))%'
                     AND p.prosecdef
                     AND NOT has_function_privilege('public',p.oid,'EXECUTE')) THEN
        RAISE EXCEPTION 'stewardship_occurrence_guard_v1 was not replaced';
    END IF;
END
$check$;
