-- Frozen forward migration file 0046 (the repository-wide file sequence):
-- re-running restore-begin while a review of the same backup is open no
-- longer resets it (#799). It is installed by the Django migration that
-- names it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copy of the replaced function still equals the
-- fresh-install baseline's (schema/functions.sql).
--
-- Since migration 0017 the restore_begin branch of the runtime transition
-- guard required only a new restore id. A second run of the operator's
-- restore-begin during an open review (say, after a recovery restart) was
-- therefore admitted: it replaced the review's restore id and moved
-- restore_activated_at, the cutoff stewardship_restore_hold_candidates_v1
-- uses, so the held-email list no longer described the restore. This file
-- replaces stewardship_runtime_transition_v1 to refuse restore_begin while
-- a review is open for the same backup time. A different backup (another
-- restore) still starts a new review, as before. The command checks first
-- and reports the open review without writing; this refusal is the
-- backstop. The function stays invoker's rights with its search_path; no
-- table, column or grant changes.

SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- stewardship_runtime_transition_v1, from functions.sql: keep an open
-- review of the same backup.
CREATE OR REPLACE FUNCTION public.stewardship_runtime_transition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    IF r.version<>NEW.expected_version OR r.mode<>NEW.before_mode OR r.current_campaign_id IS DISTINCT FROM NEW.before_campaign_id THEN
        RAISE EXCEPTION 'Runtime transition is stale' USING ERRCODE='23514'; END IF;
    -- The recovery login only starts a restore review (migration 0017, #537).
    IF current_user='pk_stewardship_admin_recovery' AND NEW.action<>'restore_begin' THEN
        RAISE EXCEPTION 'The recovery login only starts a restore review' USING ERRCODE='23514'; END IF;
    IF NEW.action='return_testing' THEN
        IF NEW.actor_id IS NULL OR NEW.after_mode<>'testing' OR NEW.after_campaign_id IS NOT NULL
           OR r.restore_review_required OR NOT stewardship_campaign_quiet_v1(r.current_campaign_id)
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=r.current_campaign_id AND state='archived')
           OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'Return to Testing requires current archived quiescence' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='campaign' THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.campaign_transition_id
            AND t.expected_runtime_version=r.version AND t.before_mode=NEW.before_mode AND t.after_mode=NEW.after_mode
            AND t.campaign_id=NEW.before_campaign_id AND NEW.after_campaign_id=NEW.before_campaign_id
            AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Mode transition requires campaign evidence' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='restore_begin' THEN
        -- The operator's restore command (admin-recovery login), run after a
        -- restore and before web starts (migration 0017, #537). It changes
        -- no mode, campaign or Family credential: it only closes the site
        -- for review. A second restore starts a new review.
        IF NOT (pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')
                OR current_user='pk_stewardship_admin_recovery')
           OR r.active_configuration_id IS NULL
           OR NEW.after_mode<>NEW.before_mode OR NEW.after_campaign_id IS DISTINCT FROM NEW.before_campaign_id
           OR NEW.restore_id IS NULL OR NEW.restore_id IS NOT DISTINCT FROM r.restore_id
           OR NEW.backup_at IS NULL OR NEW.backup_at>stewardship_campaign_now_v1() OR btrim(NEW.reason)=''
           OR NEW.campaign_transition_id IS NOT NULL OR NEW.session_id IS NOT NULL OR NEW.authenticated_at IS NOT NULL THEN
            RAISE EXCEPTION 'A restore review starts only from the operator restore command' USING ERRCODE='23514'; END IF;
        -- A review already open for the same backup is kept (#799): a re-run
        -- would replace its restore id and move restore_activated_at, the
        -- held-email cutoff. A different backup (another restore) still
        -- starts a new review.
        IF r.restore_review_required AND NEW.backup_at IS NOT DISTINCT FROM r.restore_backup_at THEN
            RAISE EXCEPTION 'A restore review is already open for this backup' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='restore_release' THEN
        -- A freshly signed-in Administrator releases the site after review.
        -- Refused while any email still needs a hold: the list must be found
        -- first, during the review, so every hold can still be decided (none
        -- is decided after release). Mode and campaign stay as restored.
        IF NOT r.restore_review_required OR NEW.restore_id IS DISTINCT FROM r.restore_id
           OR NEW.backup_at IS DISTINCT FROM r.restore_backup_at
           OR NEW.after_mode<>NEW.before_mode OR NEW.after_campaign_id IS DISTINCT FROM NEW.before_campaign_id
           OR NEW.actor_id IS NULL OR NEW.campaign_transition_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=r.current_campaign_id
               AND state IN ('purging','purge_cleanup_failed'))
           OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'This restore review cannot be released' USING ERRCODE='23514'; END IF;
        IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
            RAISE EXCEPTION 'A restore decision requires fresh Administrator authentication' USING ERRCODE='42501'; END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_restore_hold_candidates_v1()) THEN
            RAISE EXCEPTION 'The held-email list is stale: find the held emails again before release'
                USING ERRCODE='23514'; END IF;
    ELSE RAISE EXCEPTION 'Unsupported runtime transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_runtime_transition_v1()'::regprocedure
        AND NOT prosecdef AND proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
        AND prosrc LIKE '%The recovery login only starts a restore review%'
        AND prosrc LIKE '%stewardship_restore_hold_candidates_v1()%'
        AND prosrc LIKE '%IF r.restore_review_required AND NEW.backup_at IS NOT DISTINCT FROM r.restore_backup_at THEN%'
        AND prosrc LIKE '%A restore review is already open for this backup%') THEN
        RAISE EXCEPTION 'Migration 0046: the runtime transition guard does not keep an open restore review';
    END IF;
END
$check$;
