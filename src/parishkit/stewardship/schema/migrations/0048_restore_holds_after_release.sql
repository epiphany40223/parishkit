-- Frozen forward migration file 0048 (the repository-wide file sequence):
-- settle held emails after a restore review is released (#757). This file is
-- installed by its Django migration in the campaigns app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced function still
-- equals the fresh-install baseline's (functions.sql). A fresh install runs
-- the baseline, the earlier forward files and then this, and ends in the
-- same catalog as an upgraded database.
--
-- Before this file the held-email decision guard refused every decision once
-- a restore review was released, so a hold nobody decided during the review
-- kept its email back for good, and an undecided invitation kept back every
-- reminder of its Family for the rest of the campaign. This file replaces
-- stewardship_restore_resolution_v1, copied whole from its latest definition
-- (frozen file 0017) with only this change:
--
--   * the "not after release" refusal is dropped, and a hold whose campaign
--     is no longer the current one is refused instead, because the planner
--     never acts on another campaign's holds.
--
-- Every other rule stays: only an undecided hold is decided (each decision
-- is final), only "assumed sent" or "send again", a resend is refused while
-- another restore's hold keeps the same email back, the Administrator must
-- have signed in with Google within five minutes, and each decision stays
-- append-only, versioned and audited (stewardship_restore_resolution_effect_v1,
-- unchanged). Nothing here creates, replaces or cancels a Family code or
-- link: "send again" only lets the ordinary planner send the restored link.
--
-- No table, column or grant change. The function is SECURITY INVOKER (a
-- trigger function), before and after; CREATE OR REPLACE keeps its owner,
-- grants and attributes, and the DO block at the end refuses to commit unless
-- the new body is installed with them. No temporary objects: the migration
-- login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced function's grants before the change, for the DO block. A
-- custom setting holds them, not a temporary table: the migration login has
-- no TEMP privilege.
SELECT set_config('stewardship.migration_0048_acl', (
    SELECT coalesce(proacl::text,'default')||':'||pg_get_userbyid(proowner)
    FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure), true);

-- stewardship_restore_resolution_v1, from functions.sql: undecided holds of the current campaign may be decided after release.
CREATE OR REPLACE FUNCTION public.stewardship_restore_resolution_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE h stewardship_restore_delivery_hold%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO h FROM stewardship_restore_delivery_hold WHERE id=NEW.hold_id FOR UPDATE;
    -- Two decisions (migration 0017, #537): assume it was sent, or send it
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
       -- An undecided hold is still decided after release (#757), but only
       -- for the current campaign: the planner never acts on another
       -- campaign's holds, so a decision there would mean nothing.
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
    -- minutes of a Google sign-in (migration 0017, #537).
    IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
        RAISE EXCEPTION 'A restore decision requires fresh Administrator authentication' USING ERRCODE='42501'; END IF;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure
        AND NOT prosecdef AND provolatile='v' AND prorettype='trigger'::regtype
        AND proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
        AND prosrc LIKE '%stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at)%'
        AND prosrc LIKE '%NEW.recovery_occurrence_id IS NOT NULL AND NOT EXISTS%'
        AND prosrc LIKE '%other.id<>h.id%' AND prosrc LIKE '%h.state<>''unreviewed''%'
        AND prosrc LIKE '%current_runtime.current_campaign_id=held.campaign_id%'
        AND prosrc NOT LIKE '%restore_released_at%'
        AND prosrc NOT LIKE '%''not_applicable''%') THEN
        RAISE EXCEPTION 'Migration 0048: the held-email decision guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='stewardship_restore_resolution_v1'
        AND tgrelid='public.stewardship_restore_hold_resolution'::regclass
        AND tgfoid='public.stewardship_restore_resolution_v1()'::regprocedure AND tgenabled='O') THEN
        RAISE EXCEPTION 'Migration 0048: the held-email decision guard is not attached';
    END IF;
    -- CREATE OR REPLACE keeps the owner and grants; prove it.
    IF current_setting('stewardship.migration_0048_acl', true) IS DISTINCT FROM (
        SELECT coalesce(proacl::text,'default')||':'||pg_get_userbyid(proowner)
        FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0048 changed the held-email decision guard''s owner or grants';
    END IF;
END
$check$;
