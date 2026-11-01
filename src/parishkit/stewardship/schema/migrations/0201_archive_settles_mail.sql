-- Frozen forward migration file 0201 (the repository-wide file sequence):
-- archiving a campaign waits until every email of the campaign is settled
-- (#2305, admin-portal spec "Reopen and archive"). This file is installed by
-- the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- The delivery resolution guard refuses every action on an archived
-- campaign's email, and every unsettled delivery_unknown email holds a
-- recorded mail halt from being cleared (frozen file 0115). The campaign
-- transition guard admitted archive with only a closed, quiet campaign and
-- every Production receipt delivered, so an invitation, reminder or report
-- email still delivery_unknown, or still pending, retry_wait or submitting
-- (each of which may yet end delivery_unknown), could outlive the archive
-- and never be settled. This file replaces the transition guard in place,
-- its fresh-install baseline body (functions.sql; no earlier frozen file
-- replaced it) with one change: archive is also refused, with the stable
-- words "Archive requires settled campaign email", while any outbox message
-- of the campaign, of any purpose and mode, is in one of those four states.
-- Nothing else changes, and no row changes.
--
-- The guard is no SECURITY DEFINER and has no grants of its own; CREATE OR
-- REPLACE keeps its owner and privileges, and the header repeats its
-- language and search_path. The file ends with a DO block that refuses to
-- commit unless the new check is installed with that configuration and the
-- trigger still calls the guard.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_campaign_transition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
    p stewardship_campaign_configuration%ROWTYPE; expected_state text; expected_mode text;
    instant timestamptz := stewardship_campaign_now_v1();
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    -- Lifecycle state is an export admission input (#147).
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,true);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    IF NOT FOUND OR c.version<>NEW.expected_version OR r.version<>NEW.expected_runtime_version
       OR c.state<>NEW.before_state OR r.mode<>NEW.before_mode
       OR NEW.configuration_id<>r.active_configuration_id
       OR r.current_campaign_id IS DISTINCT FROM c.id OR r.restore_review_required
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Campaign transition has stale or blocked inputs' USING ERRCODE='23514';
    END IF;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    expected_mode := r.mode;
    CASE NEW.action
        WHEN 'activate' THEN
            IF c.state<>'draft' OR r.mode<>'testing' OR instant>=p.ends_at OR NEW.token_generation_id IS NULL THEN
                RAISE EXCEPTION 'Activation is not admitted' USING ERRCODE='23514'; END IF;
            expected_state := CASE WHEN instant<p.starts_at THEN 'scheduled' ELSE 'active' END;
            expected_mode := 'production';
        WHEN 'start' THEN
            IF c.state<>'scheduled' OR r.mode<>'production' OR instant<p.starts_at THEN
                RAISE EXCEPTION 'Start is not due' USING ERRCODE='23514'; END IF;
            expected_state := 'active';
        WHEN 'close' THEN
            IF c.state<>'active' OR r.mode<>'production' OR instant<p.ends_at THEN
                RAISE EXCEPTION 'Close is not due' USING ERRCODE='23514'; END IF;
            expected_state := 'closed';
        WHEN 'withdraw' THEN
            IF c.state<>'scheduled' OR c.ever_active OR r.mode<>'production' OR instant>=p.starts_at OR NEW.reason='' OR NOT stewardship_campaign_quiet_v1(c.id) THEN
                RAISE EXCEPTION 'Withdrawal is not admitted' USING ERRCODE='23514'; END IF;
            expected_state := 'draft'; expected_mode := 'testing';
        WHEN 'reopen' THEN
            IF c.state<>'closed' OR NEW.token_generation_id IS NULL OR instant>=p.ends_at OR instant<p.starts_at
               OR NOT stewardship_campaign_quiet_v1(c.id) OR NOT EXISTS (
                   SELECT 1 FROM stewardship_campaign_config_intent i JOIN stewardship_config_activation a ON a.request_id=i.request_id
                   JOIN stewardship_campaign_configuration prior ON prior.id=i.prior_projection_id
                   WHERE a.configuration_id=NEW.configuration_id AND i.action='reopen' AND i.campaign_id=c.id
                   AND i.prior_projection_id=NEW.prior_projection_id AND p.starts_at=prior.starts_at AND p.ends_at>prior.ends_at
                   AND i.token_generation_id=NEW.token_generation_id AND i.expected_version+1=c.version
                   AND i.expected_runtime_version+1=r.version AND i.actor_id=NEW.actor_id
               ) THEN RAISE EXCEPTION 'Reopen requires exact extended configuration intent' USING ERRCODE='23514'; END IF;
            expected_state:='active'; expected_mode:='production';
        WHEN 'archive' THEN
            IF c.state<>'closed' OR NOT stewardship_campaign_quiet_v1(c.id) THEN
                RAISE EXCEPTION 'Archive requires closed and quiet campaign' USING ERRCODE='23514'; END IF;
            IF EXISTS (SELECT 1 FROM stewardship_outbox_message receipt
                   WHERE receipt.campaign_id=c.id AND receipt.purpose='receipt'
                     AND receipt.mode='production' AND receipt.state<>'delivered') THEN
                RAISE EXCEPTION 'Archive requires resolved submission confirmations' USING ERRCODE='23514'; END IF;
            -- Every email of the campaign settled first (#2305): the delivery
            -- resolution guard refuses an archived campaign's email, so one
            -- left delivery_unknown (or still in flight, which may end so)
            -- could never be settled and would hold a mail halt for good.
            IF EXISTS (SELECT 1 FROM stewardship_outbox_message unsettled
                   WHERE unsettled.campaign_id=c.id
                     AND unsettled.state IN ('pending','retry_wait','submitting','delivery_unknown')) THEN
                RAISE EXCEPTION 'Archive requires settled campaign email' USING ERRCODE='23514'; END IF;
            expected_state := 'archived';
        WHEN 'unarchive' THEN
            IF c.state<>'archived' OR NOT stewardship_campaign_quiet_v1(c.id) THEN
                RAISE EXCEPTION 'Unarchive is not admitted' USING ERRCODE='23514'; END IF;
            expected_state := 'closed';
        ELSE RAISE EXCEPTION 'Unsupported campaign transition' USING ERRCODE='23514';
    END CASE;
    IF NEW.after_state<>expected_state OR NEW.after_mode<>expected_mode
       OR (NEW.action NOT IN ('start','close') AND NEW.actor_id IS NULL) THEN
        RAISE EXCEPTION 'Campaign transition result or actor is inconsistent' USING ERRCODE='23514';
    END IF;
    IF NEW.action IN ('start','close') AND NOT EXISTS (
        SELECT 1 FROM stewardship_campaign_boundary b JOIN stewardship_task_run t ON t.id=b.task_id
        WHERE b.id=NEW.boundary_id AND b.campaign_id=c.id AND b.kind=NEW.action AND b.state='pending'
          AND b.due_at=CASE WHEN NEW.action='start' THEN p.starts_at ELSE p.ends_at END
          AND t.state='running' AND t.lease_expires_at>clock_timestamp()
          AND t.fence=NEW.task_fence AND t.worker_id=NEW.actor_id
    ) THEN RAISE EXCEPTION 'Boundary requires a current fenced task' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_campaign_transition_v1()')
          AND NOT p.prosecdef
          AND p.proconfig OPERATOR(pg_catalog.=) ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc, 'unsettled.state IN (''pending'',''retry_wait'',''submitting'',''delivery_unknown'')')>0
          AND strpos(p.prosrc, 'WHERE unsettled.campaign_id=c.id')>0
          AND strpos(p.prosrc, 'Archive requires settled campaign email')>0
          AND strpos(p.prosrc, 'Archive requires resolved submission confirmations')>0
          AND strpos(p.prosrc, 'Archive requires closed and quiet campaign')>0) THEN
        RAISE EXCEPTION 'Migration 0201: the campaign transition guard does not wait for settled campaign email';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_campaign_transition'::regclass
          AND t.tgname='stewardship_campaign_transition_v1' AND NOT t.tgisinternal AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_campaign_transition_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0201: the campaign transition trigger is missing';
    END IF;
END
$check$;
