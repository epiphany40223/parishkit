-- Frozen forward migration file 0029 (the repository-wide file sequence):
-- settle restore holds after release (#757), and refuse a restore-begin
-- re-run on an open review (#799). This file is installed by its Django
-- migration in the campaigns app and must never change once released; tests/stewardship/test_schema_migration_files.py pins
-- its digest and checks that its copies of the replaced functions still equal
-- the fresh-install baseline's (functions.sql). A fresh install runs the
-- baseline, the earlier forward files and then this, and ends in the same
-- catalog as an upgraded database.
--
-- Migration 0019 refused every held-email decision once a restore review was
-- released, so a hold nobody decided during the review stayed held forever,
-- and an undecided invitation kept back every reminder of its Family. This
-- file:
--
--   * stewardship_restore_resolution_v1: drops the "not after release"
--     refusal. Every other rule stays: only an undecided hold is decided
--     (decisions are final), only "assumed sent" or "send again", a resend is
--     refused while another restore's hold keeps the same email back, and the
--     Administrator must have signed in within five minutes. New: a hold of a
--     campaign that is no longer current is refused, because the planner
--     would never act on it.
--   * stewardship_runtime_transition_v1: restore_begin is refused while a
--     review is already open for the same backup. A re-run on the same
--     restored data would move restore_activated_at, the held-email cutoff,
--     and hold reminders that came due while the site was closed. A
--     different backup is a new restore and starts a new review, as before.
--
-- No table, column or grant changes. Neither function is SECURITY DEFINER,
-- before or after. The DO block at the end refuses to commit unless both
-- bodies are installed. No temporary objects: the migration login has no
-- TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- stewardship_restore_resolution_v1, from functions.sql: undecided holds may be decided after release.
CREATE OR REPLACE FUNCTION public.stewardship_restore_resolution_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE h stewardship_restore_delivery_hold%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO h FROM stewardship_restore_delivery_hold WHERE id=NEW.hold_id FOR UPDATE;
    -- Two decisions (migration 0019, #537): assume it was sent, or send it
    -- again. Each is final: an assumption may already have cancelled the
    -- unsent copy, so turning it into a resend later would send nothing.
    -- (not_applicable is no longer decided: it would have released the
    -- email like "send again" without saying so.)
    IF h.id IS NULL OR NEW.version<>h.version+1 OR NEW.actor_id IS NULL OR btrim(NEW.evidence)=''
       OR NEW.state NOT IN ('assumed_delivered','resend_authorized')
       OR h.state<>'unreviewed'
       OR (NEW.state='resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL AND NOT EXISTS(
           SELECT 1 FROM stewardship_schedule_occurrence o WHERE o.id=NEW.recovery_occurrence_id
           AND o.definition_id=h.definition_id AND o.mode=h.mode AND o.target=h.target AND o.slot=h.slot AND o.state='pending'
       )) OR (NEW.state<>'resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL)
       -- After release an undecided hold is still decided, on the Held emails
       -- page (migration 0029, #757), but only for the current campaign: the
       -- planner never acts on another campaign's holds.
       OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_definition held
           JOIN stewardship_system_configuration current_runtime ON current_runtime.current_campaign_id=held.campaign_id
           WHERE held.id=h.definition_id)
       -- Another (earlier restore's) hold still keeps this email back, so a
       -- resend would silently do nothing: settle that one first.
       OR (NEW.state='resend_authorized' AND EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold other
           WHERE other.id<>h.id AND other.definition_id=h.definition_id AND other.mode=h.mode
             AND other.target=h.target AND other.slot=h.slot AND other.state IN ('unreviewed','assumed_delivered'))) THEN
        RAISE EXCEPTION 'Invalid restore hold review or resend binding' USING ERRCODE='23514'; END IF;
    -- Settling a held email is an Administrator's decision made within five
    -- minutes of a Google sign-in (migration 0019, #537).
    IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
        RAISE EXCEPTION 'A restore decision requires fresh Administrator authentication' USING ERRCODE='42501'; END IF;
    RETURN NEW;
END $$;

-- stewardship_runtime_transition_v1, from functions.sql: refuse a restore-begin re-run for the same backup.
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
    -- The recovery login only starts a restore review (migration 0019, #537).
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
        -- restore and before web starts (migration 0019, #537). It changes
        -- no mode, campaign or Family credential: it only closes the site
        -- for review. A second restore starts a new review.
        IF NOT (pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')
                OR current_user='pk_stewardship_admin_recovery')
           OR r.active_configuration_id IS NULL
           OR NEW.after_mode<>NEW.before_mode OR NEW.after_campaign_id IS DISTINCT FROM NEW.before_campaign_id
           OR NEW.restore_id IS NULL OR NEW.restore_id IS NOT DISTINCT FROM r.restore_id
           OR NEW.backup_at IS NULL OR NEW.backup_at>stewardship_campaign_now_v1() OR btrim(NEW.reason)=''
           -- A re-run on the same restored data while its review is open
           -- would move the restore cutoff (migration 0029, #799); a
           -- different backup is a new restore and starts a new review.
           OR (r.restore_review_required AND NEW.backup_at=r.restore_backup_at)
           OR NEW.campaign_transition_id IS NOT NULL OR NEW.session_id IS NOT NULL OR NEW.authenticated_at IS NOT NULL THEN
            RAISE EXCEPTION 'A restore review starts only from the operator restore command' USING ERRCODE='23514'; END IF;
    ELSIF NEW.action='restore_release' THEN
        -- A freshly signed-in Administrator releases the site after review.
        -- Refused while any email still needs a hold: the list must be found
        -- first, during the review, so every hold exists before release; one
        -- left undecided is decided later on the Held emails page (migration
        -- 0029, #757). Mode and campaign stay as restored.
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

-- Refuse to commit unless both replaced bodies are installed as declared.
DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure
        AND NOT prosecdef AND prosrc LIKE '%current_runtime.current_campaign_id=held.campaign_id%'
        AND prosrc NOT LIKE '%released.restore_released_at IS NOT NULL%'
        AND prosrc LIKE '%h.state<>''unreviewed''%') THEN
        RAISE EXCEPTION 'The held-email decision guard does not admit decisions after release';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_runtime_transition_v1()'::regprocedure
        AND NOT prosecdef AND prosrc LIKE '%r.restore_review_required AND NEW.backup_at=r.restore_backup_at%'
        AND prosrc LIKE '%stewardship_restore_hold_candidates_v1()%') THEN
        RAISE EXCEPTION 'The runtime transition guard does not refuse a restore-begin re-run';
    END IF;
END
$check$;
