-- Frozen forward migration file 0051 (the repository-wide file sequence):
-- editing a users configuration (#952, slice 3). It is installed by the
-- Django migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that the replaced function's copy here still equals the
-- fresh-install baseline's. A fresh install runs the baseline and the
-- earlier files and then this; the baseline already carries the replaced
-- body and the final constraint definitions, so the install ends in the same
-- catalog as an upgraded database.
--
-- Migration 0047 admitted the users-v10 document schema and the one request
-- format that converts to it. A configuration in that form also needs its
-- ordinary request formats, each the source-cadence-v8 counterpart with the
-- users list in place of the sign-in rules. This file:
--
--   alters the request CHECKs               admits users-patch-v10 (Admin
--                                            edits),
--                                            integration-credential-users-v10
--                                            (a credential update) and
--                                            operator-recovery-users-v10
--                                            (offline Admin-access recovery,
--                                            under the operator_recovery
--                                            authority only)
--   replaces stewardship_recovery_request_v1
--                                            a users recovery adds an
--                                            Administrator user, or raises
--                                            one user to Administrator and
--                                            changes nothing else; a user has
--                                            no creation operation or grants
--   replaces stewardship_request_checkpoint_v2
--                                            a users upgrade that SQL refused
--                                            at activation may be recorded
--                                            failed from yaml_activated, so
--                                            the installer restores the base
--                                            manifest and offline recovery
--                                            still runs
--
-- No table, column or grant changes: CREATE OR REPLACE keeps each trigger
-- function's owner and grants, and both stay invokers (not SECURITY
-- DEFINER). Each body is its latest definition (the baseline's; no earlier
-- file replaced either) with only the change described. Existing requests and
-- configurations are unchanged. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The ordinary and recovery request formats of a users configuration.
ALTER TABLE "stewardship_config_request" DROP CONSTRAINT "config_request_schema";
ALTER TABLE "stewardship_config_request" ADD CONSTRAINT "config_request_schema" CHECK (((request_schema)::text = ANY ((ARRAY['parish-integrations-patch-v1'::character varying, 'foundation-policy-patch-v2'::character varying, 'operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'campaign-foundation-patch-v3'::character varying, 'ministry-activity-patch-v4'::character varying, 'operator-recovery-ministry-v4'::character varying, 'campaign-content-patch-v5'::character varying, 'operator-recovery-content-v5'::character varying, 'integration-credential-patch-v6'::character varying, 'initial-setup-patch-v7'::character varying, 'source-cadence-patch-v8'::character varying, 'operator-recovery-cadence-v8'::character varying, 'integration-credential-cadence-v8'::character varying, 'chair-seed-patch-v9'::character varying, 'operator-upgrade-users-v10'::character varying, 'users-patch-v10'::character varying, 'integration-credential-users-v10'::character varying, 'operator-recovery-users-v10'::character varying])::text[])));
ALTER TABLE "stewardship_config_request" DROP CONSTRAINT "config_request_actor";
ALTER TABLE "stewardship_config_request" ADD CONSTRAINT "config_request_actor" CHECK ((((actor_id IS NOT NULL) AND ((authority)::text = 'admin'::text) AND (confirmed_deployment_id IS NULL) AND (operator_name IS NULL) AND (operator_reason IS NULL) AND (recovery_target IS NULL) AND (NOT ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying, 'operator-recovery-users-v10'::character varying, 'operator-upgrade-users-v10'::character varying])::text[])))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_recovery'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NOT NULL) AND ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying, 'operator-recovery-users-v10'::character varying])::text[]))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_upgrade'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NULL) AND ((request_schema)::text = 'operator-upgrade-users-v10'::text))));

-- Offline recovery of a users configuration (#952): no creation operation
-- or grants to bind to the operation; the user's role is what it raises.
CREATE OR REPLACE FUNCTION public.stewardship_recovery_request_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    operation jsonb;
    target text;
BEGIN
    IF NEW.authority <> 'operator_recovery' THEN RETURN NEW; END IF;
    operation := NEW.patch -> 0;
    IF operation ->> 'operation' = 'add' THEN
        target := operation -> 'values' ->> 'email';
    ELSE
        SELECT record -> 'values' ->> 'email' INTO target
        FROM public.stewardship_configuration_version version,
             jsonb_array_elements(version.canonical_document -> 'sections'
                 -> 'login_rules') record
        WHERE version.id = NEW.base_id
          AND record ->> 'id' = operation ->> 'id';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration
                   WHERE id = NEW.confirmed_deployment_id)
       OR NEW.operator_name = '' OR NEW.operator_reason = ''
       OR jsonb_array_length(NEW.patch) <> 1
       OR operation ->> 'section' IS DISTINCT FROM 'login_rules'
       OR operation ->> 'operation' NOT IN ('add', 'update')
       OR target IS DISTINCT FROM NEW.recovery_target
       -- A users configuration (#952): add an Administrator user, or raise
       -- one user to Administrator and change nothing else about it.
       OR (NEW.request_schema = 'operator-recovery-users-v10'
           AND (operation -> 'values' ->> 'role' IS DISTINCT FROM 'administrator'
                OR (operation ->> 'operation' = 'add'
                    AND operation -> 'values' ->> 'kind' IS DISTINCT FROM 'user')
                OR (operation ->> 'operation' = 'update'
                    AND operation -> 'values'
                        IS DISTINCT FROM '{"role": "administrator"}'::jsonb)))
       OR (NEW.request_schema <> 'operator-recovery-users-v10'
           AND ((operation ->> 'operation' = 'add' AND operation -> 'values'
                 ->> 'creation_operation' IS DISTINCT FROM NEW.request_key::text)
                OR operation -> 'values' -> 'grants' -> 'administrator'
                   IS DISTINCT FROM jsonb_build_object('manual', NEW.request_key::text))) THEN
        RAISE EXCEPTION 'Invalid offline recovery attribution'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

-- The checkpoint guard lets a users upgrade that SQL refused inside its
-- activation be recorded as failed, as a refused Chairperson confirmation
-- is, so the installer selects the base manifest again (#952).
CREATE OR REPLACE FUNCTION public.stewardship_request_checkpoint_v2() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    DECLARE
        intent stewardship_config_request%ROWTYPE;
        previous stewardship_config_checkpoint%ROWTYPE;
    BEGIN
        SELECT * INTO intent FROM stewardship_config_request
        WHERE id = NEW.request_id FOR UPDATE;
        IF NOT FOUND OR NEW.actor_id IS DISTINCT FROM intent.actor_id
           OR NEW.created_at < intent.created_at THEN
            RAISE EXCEPTION 'Invalid configuration checkpoint attribution'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO previous FROM stewardship_config_checkpoint
        WHERE request_id = NEW.request_id ORDER BY sequence DESC LIMIT 1;
        IF NOT FOUND THEN
            IF NEW.sequence <> 1 OR NEW.state <> 'staged' THEN
                RAISE EXCEPTION 'Configuration intake must start staged'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.sequence <> previous.sequence + 1
               OR NEW.created_at < previous.created_at
               OR NOT (
                   (previous.state = 'staged'
                    AND NEW.state IN ('cancelled', 'validating'))
                   OR (previous.state = 'validating'
                       AND NEW.state IN ('prepared', 'failed'))
                   OR (previous.state = 'prepared'
                       AND NEW.state IN ('yaml_activated', 'failed'))
                   OR (previous.state = 'yaml_activated'
                       AND (NEW.state = 'applied'
                        -- The activation transaction refused the confirming
                        -- Administrator, who is no longer one; the installer
                        -- restores the base selection and records the refusal.
                        -- Never from the web role: its own checkpoint grant
                        -- must not be able to strand a selected candidate.
                        OR (NEW.state='failed' AND NEW.failure_code='actor_unauthorized'
                            AND current_user<>'pk_stewardship_web')
                         -- A Chairperson confirmation whose seed the promoted
                         -- source no longer shows is refused inside the
                         -- activation the same way and restored the same way.
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                            AND intent.request_schema='chair-seed-patch-v9'
                            AND current_user<>'pk_stewardship_web')
                         -- SQL's users-form guard refused the offline users
                         -- upgrade (#952) inside its activation: refused and
                         -- restored the same way, so the manifest never stays
                         -- ahead of the database and recovery still runs.
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                            AND intent.request_schema='operator-upgrade-users-v10'
                            AND current_user<>'pk_stewardship_web')
                         -- A Ministry added to a live campaign that a source
                         -- promotion dropped, or that turned inactive, after
                         -- preflight (#342) is refused and restored the same
                         -- way. Only a request whose one operation changes
                         -- nothing but a structurally locked campaign's
                         -- ministry_duids qualifies.
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                            AND jsonb_typeof(intent.patch)='array'
                            AND jsonb_array_length(intent.patch)=1
                            AND intent.patch->0->>'section'='campaigns'
                            AND intent.patch->0->>'operation'='update'
                            AND intent.patch->0->'values' ? 'ministry_duids'
                            AND (intent.patch->0->'values') - 'ministry_duids'::text = '{}'::jsonb
                            AND EXISTS (SELECT 1 FROM stewardship_campaign c
                                WHERE c.id::text=intent.patch->0->>'id' AND c.structural_locked)
                            AND current_user<>'pk_stewardship_web')
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                        AND (EXISTS(SELECT 1 FROM stewardship_campaign_config_abort b
                            JOIN stewardship_campaign_config_intent i ON i.id=b.intent_id WHERE i.request_id=NEW.request_id) OR EXISTS (
    SELECT 1 FROM public.stewardship_setup_config_abort b
    JOIN public.stewardship_setup_config_intent i ON i.id=b.intent_id
    WHERE i.request_id=NEW.request_id)))))
               ) THEN
                RAISE EXCEPTION 'Invalid configuration installer transition'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF NEW.state IN ('prepared', 'yaml_activated', 'applied')
           AND NOT EXISTS (
               SELECT 1 FROM stewardship_configuration_version
               WHERE id = intent.candidate_version_id
                 AND digest = intent.candidate_digest
                 AND predecessor_id = intent.base_id
           ) THEN
            RAISE EXCEPTION 'Checkpoint requires matching prepared candidate'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.state = 'applied' AND NOT EXISTS (
            SELECT 1 FROM stewardship_config_activation activation
            JOIN stewardship_system_configuration runtime
              ON runtime.active_configuration_id = activation.configuration_id
            WHERE activation.request_id = NEW.request_id
        ) THEN
            RAISE EXCEPTION 'Applied checkpoint requires matching activation'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The three request formats, the users recovery only under its operator
    -- authority (and still refused for an Admin request).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_config_request'::regclass
              AND conname='config_request_schema' AND contype='c'
              AND pg_get_constraintdef(oid) LIKE '%''users-patch-v10''%'
              AND pg_get_constraintdef(oid) LIKE '%''integration-credential-users-v10''%'
              AND pg_get_constraintdef(oid) LIKE '%''operator-recovery-users-v10''%')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_config_request'::regclass
              AND conname='config_request_actor' AND contype='c'
              AND pg_get_constraintdef(oid) LIKE
                  '%operator-recovery-users-v10%operator_recovery%operator-recovery-users-v10%operator_upgrade%') THEN
        RAISE EXCEPTION 'Migration 0051: the users request formats are not installed as declared';
    END IF;
    -- The replaced recovery trigger: an invoker with the fixed search_path,
    -- its users branch, and still the trigger before each request insert.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_recovery_request_v1'
              AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND pg_get_function_identity_arguments(p.oid)=''
              AND p.prosrc LIKE '%IS DISTINCT FROM ''{"role": "administrator"}''::jsonb%'
              AND p.prosrc LIKE '%NEW.request_schema <> ''operator-recovery-users-v10''%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_recovery_request_v1')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_config_request'::regclass
              AND t.tgname='stewardship_recovery_request_v1' AND NOT t.tgisinternal
              AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_recovery_request_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0051: the users recovery trigger is not installed as declared';
    END IF;
    -- The replaced checkpoint guard: an invoker with the fixed search_path,
    -- its users-upgrade refusal, and still the trigger before each insert.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_request_checkpoint_v2'
              AND NOT p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND pg_get_function_identity_arguments(p.oid)=''
              AND p.prosrc LIKE '%intent.request_schema=''operator-upgrade-users-v10''%'
              AND p.prosrc LIKE '%intent.request_schema=''chair-seed-patch-v9''%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_request_checkpoint_v2')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_config_checkpoint'::regclass
              AND t.tgname='stewardship_request_checkpoint_v2' AND NOT t.tgisinternal
              AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_request_checkpoint_v2()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0051: the installer checkpoint guard is not installed as declared';
    END IF;
END
$check$;
