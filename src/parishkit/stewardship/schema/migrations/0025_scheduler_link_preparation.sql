-- Frozen forward migration file 0025 (the repository-wide file sequence):
-- let the scheduler record and discard inactive Family link preparation for
-- go-live sequencing (#462). This file is installed by its Django migration
-- in the campaigns app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later change gets its own numbered file. A fresh install
-- runs the baseline (whose activation_tokens.sql already carries this body),
-- 0002 to 0024 and then this, and ends in the same catalog as an upgraded
-- database.
--
-- Until now only the web login, acting for a current Administrator, could
-- record a link preparation (stewardship_production_tokens) or its discard
-- (stewardship_production_token_cancel). Go-live sequencing moves both into
-- the scheduler's go-live producer, so the shared intake trigger function now
-- also admits pk_stewardship_scheduler, but only when the actor is the
-- Administrator who started the go-live (the transition request's
-- initiated_by_id; for a discard, the transition of the preparation it
-- discards), only while that Administrator is still current
-- (stewardship_export_authorized_v1) and only while the request is
-- cleanup_complete, the one state in which links are prepared. The web
-- login's check, and every other check (READ COMMITTED, the work-order lock,
-- an attributed actor, current scope, an available slot, the exact task root,
-- and the refusal to cancel selected live links), are unchanged. The retry
-- check (stewardship_production_tokens_task_pin_v1) is not touched: retries
-- still come only from the web.
--
-- Grants are not here: the scheduler already reads both tables, and its new
-- INSERT on them comes from the grants registry through the upgrade's
-- database-grants step. The function is SECURITY DEFINER inline, which
-- CREATE OR REPLACE keeps because the definition names it; the DO block
-- checks it anyway. No temporary objects: the migration login has no TEMP
-- privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_production_tokens_intake_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE kind text; preparation public.stewardship_production_tokens%ROWTYPE; transition uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Production link preparation history is immutable' USING ERRCODE='23514';
    END IF;
    -- Waiting for work order must expose the preceding owner's committed state.
    -- A fixed transaction snapshot cannot satisfy that admission contract.
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'Production link intent requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NOT pg_has_role(session_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        IF session_user='pk_stewardship_scheduler' THEN
            -- Go-live sequencing (#462, migration 0025): the scheduler records
            -- or discards a preparation only for the Administrator who started
            -- this go-live, still a current Administrator, while its request
            -- is cleanup_complete (it still owns the go-live gate). Each
            -- branch reads only its own table's columns.
            IF TG_TABLE_NAME='stewardship_production_tokens' THEN
                transition:=NEW.transition_id;
            ELSE
                SELECT p.transition_id INTO transition FROM stewardship_production_tokens p
                    WHERE p.id=NEW.preparation_id;
            END IF;
            IF public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
               OR NOT EXISTS(SELECT 1 FROM stewardship_production_request request
                   WHERE request.id=transition AND request.state='cleanup_complete'
                       AND request.initiated_by_id=NEW.actor_id) THEN
                RAISE EXCEPTION 'Scheduled Production link intent requires the go-live requester' USING ERRCODE='42501';
            END IF;
        ELSIF session_user<>'pk_stewardship_web'
              OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE THEN
            RAISE EXCEPTION 'Production link intent requires a current Admin' USING ERRCODE='42501';
        END IF;
    END IF;
    IF NEW.actor_id IS NULL THEN
        RAISE EXCEPTION 'Production link intent requires an attributed actor' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_production_tokens' THEN
        preparation:=NEW;
        kind:='production_tokens';
        IF NOT public.stewardship_production_tokens_current_v1(preparation) THEN
            RAISE EXCEPTION 'Production link preparation has stale scope' USING ERRCODE='23514';
        END IF;
        IF NOT public.stewardship_production_tokens_available_v1(NEW.transition_id,NEW.id) THEN
            RAISE EXCEPTION 'Prior link preparation requires completion or disposal' USING ERRCODE='23514';
        END IF;
    ELSE
        kind:='production_token_cleanup';
        SELECT * INTO preparation FROM stewardship_production_tokens WHERE id=NEW.preparation_id;
        IF preparation.id IS NULL OR EXISTS(
            SELECT 1 FROM stewardship_family_token_generation generation
            JOIN stewardship_campaign campaign ON campaign.id=generation.campaign_id
            WHERE generation.operation_id=preparation.id
                AND (generation.state='active' OR campaign.active_token_generation_id=generation.id)
        ) THEN
            RAISE EXCEPTION 'Selected live links cannot be cancelled' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stewardship_task_run task
        WHERE task.id=NEW.task_id AND task.root_id=task.id AND task.parent_id IS NULL
            AND task.task_type=kind AND task.domain_request_id=NEW.id
            AND task.idempotency_key=NEW.id::text AND task.state='queued'
            AND task.initiated_by_id=NEW.actor_id AND task.correlation_id=NEW.correlation_id) THEN
        RAISE EXCEPTION 'Production link intent requires its exact task root' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_tokens_intake_v1() FROM PUBLIC;

-- Refuse to commit unless the function is installed with the scheduler
-- branch, is still SECURITY DEFINER with the pinned search_path, and keeps
-- the live-link refusal; the previous definition fails this.
DO $check$
DECLARE source text;
BEGIN
    SELECT p.prosrc INTO source FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_production_tokens_intake_v1'
          AND p.pronargs=0 AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'];
    IF source IS NULL OR position('pk_stewardship_scheduler' IN source)=0
       OR position('request.initiated_by_id' IN source)=0
       OR position('cleanup_complete' IN source)=0
       OR position('Selected live links cannot be cancelled' IN source)=0 THEN
        RAISE EXCEPTION 'stewardship_production_tokens_intake_v1 is not installed with the go-live scheduler branch';
    END IF;
END
$check$;
