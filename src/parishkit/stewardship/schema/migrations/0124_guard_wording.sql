-- Frozen forward migration file 0124 (the repository-wide file sequence):
-- the guards' refusals stop asking for "fresh ... authentication" (#1762).
-- This file is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copies of baseline functions equal the fresh-install
-- baseline's (delivery_control.sql, production_confirmation.sql,
-- production_withdrawal.sql, functions.sql).
--
-- Since #1547 (0114) no guard checks how old a sign-in is, but ten
-- refusals still said a write "requires fresh ... authentication". They now
-- say what is checked: a live session (the actor's own, unrevoked,
-- unexpired, active in the last 60 minutes and, where the row records one,
-- with exactly its sign-in instant; a command session only with its live
-- full-scope automation session). Python matches the new words
-- (system_health.SIGNED_OUT_REFUSAL). Four comments that still described a
-- sign-in age or a step-up sign-in moving authenticated_at (which #1550,
-- 0123, made immutable) are reworded too. Each body below is its latest
-- earlier copy with only that text changed; no rule changes, and no row
-- changes, so no Admin session or Family credential ends.
--
-- CREATE OR REPLACE keeps each function's owner and grants; every header
-- repeats its language, rights (SECURITY DEFINER where it was) and
-- search_path. The DO block at the end refuses to commit unless every body
-- is installed exactly as declared (its md5 and new words) with its
-- rights, configuration and grants unchanged and its trigger enabled, and
-- no installed function still raises the old words. It can run again (the
-- users swap's test reinstalls some bodies). Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Each function's owner, grants, rights and configuration before the
-- replacement, compared in the DO block (a transaction-local setting: the
-- migration login has no TEMP privilege).
SELECT set_config('stewardship.migration_0124_attributes', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text||'|'||p.proowner::regrole::text, ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
            'stewardship_backup_request_guard_v1',
            'stewardship_debug_off_switch_guard_v1',
            'stewardship_delivery_control_guard_v1',
            'stewardship_production_confirmation_guard_v1',
            'stewardship_production_withdrawal_guard_v1',
            'stewardship_publication_confirmation_guard_v1',
            'stewardship_restore_resolution_v1',
            'stewardship_runtime_transition_v1',
            'stewardship_sealed_intake_admission_v1',
            'stewardship_setup_install_binding_v1',
            'stewardship_setup_secret_audit_v1',
            'stewardship_source_drop_acceptance_guard_v1'
    )), true);

-- How many enabled triggers each function serves before the replacement
-- (the sealed intake check guards two tables), compared in the DO block.
SELECT set_config('stewardship.migration_0124_triggers', (
    SELECT string_agg(p.proname||'='||(SELECT count(*) FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')::text, ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
        'stewardship_backup_request_guard_v1',
        'stewardship_debug_off_switch_guard_v1',
        'stewardship_delivery_control_guard_v1',
        'stewardship_production_confirmation_guard_v1',
        'stewardship_production_withdrawal_guard_v1',
        'stewardship_publication_confirmation_guard_v1',
        'stewardship_restore_resolution_v1',
        'stewardship_runtime_transition_v1',
        'stewardship_sealed_intake_admission_v1',
        'stewardship_setup_install_binding_v1',
        'stewardship_setup_secret_audit_v1',
        'stewardship_source_drop_acceptance_guard_v1')), true);

-- stewardship_backup_request_guard_v1, from 0114_no_sign_in_age.sql: only
-- its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_backup_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'A backup request is never deleted' USING ERRCODE='42501';
    END IF;
    -- Each check below reads other rows at the statement's own snapshot,
    -- which only READ COMMITTED gives a statement that waited on the lock.
    IF NOT owner AND current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A backup request change requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF owner THEN
            RETURN NEW;
        END IF;
        IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web portal records a backup request' USING ERRCODE='42501';
        END IF;
        -- One request at a time: serialize inserts before looking for others.
        PERFORM pg_advisory_xact_lock(736241, 2);
        IF NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=NEW.authenticated_at
                  -- Any sign-in age (#1547); a command session still needs
                  -- its live full-scope automation session (ADM-11).
                  AND login.authenticated_at<=clock_timestamp()
                  AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                           WHERE command.portal_session_id=login.id)
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
            RAISE EXCEPTION 'A backup request requires a live Administrator session' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_system_configuration
                  WHERE restore_review_required) THEN
            RAISE EXCEPTION 'No backup request during a restore review' USING ERRCODE='23514';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_backup_request r
                  WHERE (r.state='running' AND r.claimed_at>instant-interval '2 hours')
                     OR (r.state='waiting'
                         AND greatest(r.created_at,coalesce(r.held_at,r.created_at))
                             >instant-interval '30 minutes')) THEN
            RAISE EXCEPTION 'A backup request is already waiting or running' USING ERRCODE='23505';
        END IF;
        NEW.created_at:=instant;
        NEW.state:='waiting';
        NEW.held_at:=NULL;
        NEW.claimed_at:=NULL;
        NEW.finished_at:=NULL;
        NEW.backup_run_id:=NULL;
        NEW.failure_kind:=NULL;
        RETURN NEW;
    END IF;
    IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_backup_worker' THEN
        RAISE EXCEPTION 'Only the backup worker settles a backup request' USING ERRCODE='42501';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
        RAISE EXCEPTION 'A backup request keeps its identity' USING ERRCODE='23514';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    IF OLD.state='waiting' AND NEW.state='waiting' THEN
        -- A poll that saw a bulk send: only the held stamp moves. A request
        -- whose window has already passed is never held again (that would
        -- revive it): it lapses, as a claim refuses it.
        IF NEW.held_at IS NULL THEN
            RAISE EXCEPTION 'A held stamp needs a time' USING ERRCODE='23514';
        END IF;
        IF greatest(OLD.created_at,coalesce(OLD.held_at,OLD.created_at))
               <=instant-interval '30 minutes' THEN
            RAISE EXCEPTION 'This backup request can no longer be held' USING ERRCODE='23514';
        END IF;
        NEW.held_at:=instant;
    ELSIF OLD.state='waiting' AND NEW.state='running' THEN
        -- Never run a lapsed request late, nor any during a restore review.
        IF greatest(OLD.created_at,coalesce(OLD.held_at,OLD.created_at))
               <=instant-interval '30 minutes'
           OR EXISTS(SELECT 1 FROM public.stewardship_system_configuration
                     WHERE restore_review_required) THEN
            RAISE EXCEPTION 'This backup request can no longer be run' USING ERRCODE='23514';
        END IF;
        NEW.claimed_at:=instant;
    ELSIF OLD.state='waiting' AND NEW.state='expired' THEN
        IF greatest(OLD.created_at,coalesce(OLD.held_at,OLD.created_at))
           >instant-interval '30 minutes' THEN
            RAISE EXCEPTION 'A backup request expires only after 30 minutes' USING ERRCODE='23514';
        END IF;
        NEW.finished_at:=instant;
    ELSIF OLD.state='running' AND NEW.state='finished' THEN
        NEW.finished_at:=instant;
    ELSIF OLD.state='running' AND NEW.state='failed' THEN
        NEW.finished_at:=instant;
    ELSE
        RAISE EXCEPTION 'A backup request moves only forward' USING ERRCODE='23514';
    END IF;
    IF NEW.state<>'waiting' AND NEW.held_at IS DISTINCT FROM OLD.held_at THEN
        RAISE EXCEPTION 'Only a waiting request is stamped held' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('finished','failed') AND NEW.claimed_at IS DISTINCT FROM OLD.claimed_at THEN
        RAISE EXCEPTION 'A backup request keeps its claim' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- stewardship_debug_off_switch_guard_v1, from 0114_no_sign_in_age.sql:
-- only its messages reworded.
CREATE OR REPLACE FUNCTION public.stewardship_debug_off_switch_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'The debug-off switch is never deleted' USING ERRCODE='42501';
    END IF;
    -- The insert's check reads other rows at the statement's own snapshot,
    -- which only READ COMMITTED gives a statement that waited on the lock.
    IF NOT owner AND current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A debug-off switch change requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF owner THEN
            RETURN NEW;
        END IF;
        IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web portal sets the debug-off switch' USING ERRCODE='42501';
        END IF;
        -- One set switch at a time: serialize inserts before looking.
        PERFORM pg_advisory_xact_lock(736241, 3);
        IF NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=NEW.authenticated_at
                  -- Any sign-in age (#1547); a command session still needs
                  -- its live full-scope automation session (ADM-11).
                  AND login.authenticated_at<=clock_timestamp()
                  AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                           WHERE command.portal_session_id=login.id)
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
            RAISE EXCEPTION 'The debug-off switch requires a live Administrator session' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_debug_off_switch s
                  WHERE s.cleared_at IS NULL) THEN
            RAISE EXCEPTION 'The debug-off switch is already set' USING ERRCODE='23505';
        END IF;
        NEW.set_at:=instant;
        NEW.cleared_at:=NULL;
        NEW.cleared_by:=NULL;
        NEW.cleared_actor_id:=NULL;
        RETURN NEW;
    END IF;
    -- A non-owner clear is the web login's Allow debug logging again, and
    -- only in Testing mode (System health decision 3).
    IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the server operator clears the debug-off switch' USING ERRCODE='42501';
    END IF;
    IF NOT owner AND NOT EXISTS(SELECT 1 FROM public.stewardship_system_configuration r
                                WHERE r.mode='testing') THEN
        RAISE EXCEPTION 'Only the server operator clears the debug-off switch in Production' USING ERRCODE='42501';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.set_at IS DISTINCT FROM OLD.set_at
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
        RAISE EXCEPTION 'A debug-off switch keeps who set it and when' USING ERRCODE='23514';
    END IF;
    IF OLD.cleared_at IS NOT NULL OR NEW.cleared_at IS NULL THEN
        RAISE EXCEPTION 'A debug-off switch is cleared once and stays cleared' USING ERRCODE='23514';
    END IF;
    IF NOT owner THEN
        IF NEW.cleared_by IS DISTINCT FROM 'administrator' OR NEW.cleared_actor_id IS NULL THEN
            RAISE EXCEPTION 'The web clears the debug-off switch only as an Administrator' USING ERRCODE='23514';
        END IF;
        IF public.stewardship_export_authorized_v1(NEW.cleared_actor_id,true) IS NOT TRUE
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.principal_id=NEW.cleared_actor_id
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  -- Any sign-in age (#1547); a command session still needs
                  -- its live full-scope automation session (ADM-11).
                  AND login.authenticated_at<=clock_timestamp()
                  AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                           WHERE command.portal_session_id=login.id)
                       OR public.stewardship_automation_fresh_v1(login.id,NEW.cleared_actor_id))) THEN
            RAISE EXCEPTION 'Allowing debug logging again requires a live Administrator session' USING ERRCODE='42501';
        END IF;
    END IF;
    NEW.cleared_at:=instant;
    RETURN NEW;
END $$;

-- stewardship_delivery_control_guard_v1, from 0114_no_sign_in_age.sql:
-- only its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_delivery_control_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign public.stewardship_campaign%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
    instant timestamptz; current_inventory jsonb; current_health jsonb; family_impact jsonb; digest_impact jsonb;
    selected_types jsonb; selected_count bigint; clears_pause boolean;
    current_coverage jsonb; preparing bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Delivery control intent is immutable' USING ERRCODE='23514';
    END IF;
    IF current_setting('transaction_isolation')<>'read committed'
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736212 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR (session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
            (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')) THEN
        RAISE EXCEPTION 'Delivery control requires its ordered web owner' USING ERRCODE='42501';
    END IF;
    SELECT * INTO runtime FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO campaign FROM public.stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    instant:=public.stewardship_campaign_now_v1();
    IF NEW.actor_id IS NULL OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Delivery control requires a live Admin session' USING ERRCODE='42501';
    END IF;
    IF campaign.id IS NULL OR runtime.current_campaign_id IS DISTINCT FROM campaign.id
       OR runtime.mode<>'production' OR runtime.restore_review_required
       OR runtime.version IS DISTINCT FROM NEW.expected_runtime_version
       OR campaign.version IS DISTINCT FROM NEW.expected_campaign_version
       OR campaign.state NOT IN ('scheduled','active','closed')
       OR NEW.preview_at>instant OR NEW.preview_at>=NEW.expires_at OR NEW.expires_at<=instant
       OR NEW.expires_at>NEW.preview_at+interval '5 minutes' OR btrim(NEW.reason)=''
       OR EXISTS(SELECT 1 FROM public.stewardship_campaign_work_gate
            WHERE campaign_id=campaign.id AND state IN ('preparing','running','tombstone'))
       OR EXISTS(SELECT 1 FROM public.stewardship_campaign_credentials
            WHERE campaign_id=campaign.id AND go_live_gate) THEN
        RAISE EXCEPTION 'Delivery control has stale or blocked inputs' USING ERRCODE='23514';
    END IF;
    SELECT inventory INTO current_inventory FROM public.stewardship_delivery_control_inventory
        WHERE campaign_id=campaign.id;
    IF NEW.inventory IS DISTINCT FROM current_inventory THEN
        RAISE EXCEPTION 'Delivery work changed; review a new preview' USING ERRCODE='23514';
    END IF;
    IF NEW.action='resolve' THEN
        IF NOT campaign.delivery_paused OR campaign.state<>'closed'
           OR jsonb_typeof(NEW.selection->'types') IS DISTINCT FROM 'array'
           OR coalesce(NEW.selection->>'decision','') NOT IN ('release','cancel','clear') THEN
            RAISE EXCEPTION 'Held-message resolution requires a closed paused campaign' USING ERRCODE='23514';
        END IF;
        SELECT coalesce(jsonb_agg(kind ORDER BY kind),'[]') INTO selected_types
            FROM (SELECT DISTINCT value AS kind FROM jsonb_array_elements_text(NEW.selection->'types')
                WHERE value IN ('receipt','daily_digest','weekly_digest')) types;
        SELECT coalesce(sum((current_inventory->'types'->kind->>'held')::bigint),0) INTO selected_count
            FROM jsonb_array_elements_text(selected_types) kind;
        SELECT coverage,s.preparing INTO current_coverage,preparing
            FROM public.stewardship_delivery_closed_coverage_summary s WHERE campaign_id=campaign.id;
        SELECT coalesce(jsonb_object_agg(key,value),'{}') INTO current_coverage
            FROM jsonb_each(current_coverage) WHERE selected_types ? key;
        clears_pause:=(current_inventory->>'held')::bigint
                =selected_count+(current_inventory->>'stranded')::bigint
            AND (current_inventory->>'submitting')::bigint=0 AND (current_inventory->>'unknown')::bigint=0;
        current_health:='null'::jsonb;
        IF NEW.selection->>'decision'='release' THEN
            SELECT health INTO current_health FROM public.stewardship_delivery_control_health
                WHERE campaign_id=campaign.id;
            IF (current_health->>'ready')::boolean IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Held-message release requires current sender health' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.selection IS DISTINCT FROM jsonb_build_object('plan','closed','decision',NEW.selection->>'decision',
                'types',selected_types,'health',current_health,'coverage',current_coverage)
           OR preparing IS DISTINCT FROM 0
           OR (NEW.selection->>'decision'='cancel' AND EXISTS(
                SELECT 1 FROM jsonb_array_elements_text(selected_types) kind
                WHERE (current_inventory->'types'->kind->>'submitting')::bigint>0
                    OR (current_inventory->'types'->kind->>'unknown')::bigint>0))
           OR (NEW.control_id IS NOT NULL) IS DISTINCT FROM clears_pause
           OR (NEW.selection->>'decision'='clear' AND (NOT clears_pause OR selected_types<>'[]'::jsonb))
           OR (NEW.selection->>'decision'<>'clear' AND selected_count=0) THEN
            RAISE EXCEPTION 'Held-message selection changed; review exact current counts' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.control_id IS NULL OR NEW.action NOT IN ('pause','resume') THEN
        RAISE EXCEPTION 'Delivery control action is not admitted' USING ERRCODE='23514';
    END IF;
    IF NEW.action='pause' THEN
        IF campaign.delivery_paused OR NEW.selection<>'{}'::jsonb THEN
            RAISE EXCEPTION 'Delivery pause is not admitted' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT health INTO current_health FROM public.stewardship_delivery_control_health
            WHERE campaign_id=campaign.id;
        IF NOT campaign.delivery_paused
           OR (current_health->>'ready')::boolean IS DISTINCT FROM true
           OR (current_inventory->>'submitting')::bigint<>0
           OR (current_inventory->>'unknown')::bigint<>0 THEN
            RAISE EXCEPTION 'Resume requires current health and resolved uncertainty' USING ERRCODE='23514';
        END IF;
        IF NEW.selection->>'plan'='before_start' THEN
            IF campaign.state<>'scheduled'
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_configuration p
                    WHERE p.id=campaign.active_configuration_id AND instant<p.starts_at)
               OR NEW.selection IS DISTINCT FROM jsonb_build_object('plan','before_start','health',current_health)
               OR EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
                WHERE d.campaign_id=campaign.id AND o.mode='production'
                    AND o.state IN ('pending','running','delivery_unknown') AND o.due_at<=instant) THEN
                RAISE EXCEPTION 'Pre-start resume cannot consume due work' USING ERRCODE='23514';
            END IF;
        ELSIF NEW.selection->>'plan'='family' THEN
            SELECT impact INTO family_impact FROM public.stewardship_delivery_family_recovery_summary
                WHERE campaign_id=campaign.id;
            SELECT impact INTO digest_impact FROM public.stewardship_delivery_digest_recovery_summary
                WHERE campaign_id=campaign.id;
            IF campaign.state NOT IN ('scheduled','active')
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_configuration p
                    WHERE p.id=campaign.active_configuration_id AND instant>=p.starts_at AND instant<p.ends_at)
               OR (family_impact->>'blocked')::bigint IS DISTINCT FROM 0
               OR (digest_impact->>'blocked')::bigint IS DISTINCT FROM 0
               OR NEW.selection IS DISTINCT FROM jsonb_build_object('plan','family',
                    'health',current_health,'family',family_impact,'digests',digest_impact)
               OR EXISTS(SELECT 1 FROM public.stewardship_activation_catchup
                    WHERE campaign_id=campaign.id AND completed_at IS NULL) THEN
                RAISE EXCEPTION 'Resume requires the complete current recovery plan' USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Unknown delivery recovery plan' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- stewardship_production_confirmation_guard_v1, from
-- 0114_no_sign_in_age.sql: only its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_production_confirmation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request public.stewardship_production_request%ROWTYPE;
    preparation public.stewardship_production_tokens%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
    generation public.stewardship_family_token_generation%ROWTYPE;
    impact bigint; instant timestamptz;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Production confirmation is immutable' USING ERRCODE='23514';
    END IF;
    IF current_setting('transaction_isolation')<>'read committed'
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736212 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR (session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
            (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')) THEN
        RAISE EXCEPTION 'Production confirmation requires its ordered web owner' USING ERRCODE='42501';
    END IF;
    SELECT * INTO runtime FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id;
    SELECT * INTO campaign FROM public.stewardship_campaign WHERE id=request.campaign_id FOR UPDATE;
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id FOR UPDATE;
    SELECT * INTO preparation FROM public.stewardship_production_tokens
        WHERE id=NEW.preparation_id AND transition_id=request.id;
    SELECT * INTO generation FROM public.stewardship_family_token_generation WHERE id=NEW.generation_id FOR UPDATE;
    SELECT version INTO impact FROM public.stewardship_activation_impact WHERE singleton FOR UPDATE;
    instant:=public.stewardship_campaign_now_v1();
    PERFORM public.stewardship_cleanup_counts_v1(NEW.preview_counts);
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(NEW.preview_counts) key)
        IS DISTINCT FROM ARRAY['active_families','coalesced_slots','daily_messages','eligible_families',
            'family_messages','no_email_families','weekly_messages']::text[]
       OR NEW.preview_at>instant OR NEW.preview_at>=NEW.expires_at
       OR NEW.expires_at>NEW.preview_at+interval '5 minutes' THEN
        RAISE EXCEPTION 'Production confirmation requires bounded preview evidence' USING ERRCODE='23514';
    END IF;
    IF NEW.actor_id IS NULL OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)))
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_production_event event
            WHERE event.request_id=request.id AND event.action='complete') THEN
        RAISE EXCEPTION 'Production confirmation requires completed cleanup and a live Admin session' USING ERRCODE='42501';
    END IF;
    IF request.id IS NULL OR preparation.id IS NULL OR generation.id IS NULL OR campaign.id IS NULL
       OR runtime.current_campaign_id IS DISTINCT FROM campaign.id
       OR runtime.version IS DISTINCT FROM NEW.expected_runtime_version
       OR campaign.version IS DISTINCT FROM NEW.expected_campaign_version
       OR request.version IS DISTINCT FROM NEW.expected_request_version
       OR impact IS DISTINCT FROM NEW.impact_revision
       OR NEW.expires_at<=instant OR NEW.expires_at>instant+interval '5 minutes'
       OR public.stewardship_production_tokens_current_v1(preparation) IS NOT TRUE
       OR generation.state<>'ready' OR generation.operation_id<>preparation.id
       OR generation.task_id<>preparation.task_id OR generation.campaign_id<>campaign.id
       OR generation.completed_at IS NULL
       OR generation.configuration_id IS DISTINCT FROM preparation.configuration_id
       OR generation.source_snapshot_id IS DISTINCT FROM preparation.source_snapshot_id
       OR generation.source_generation IS DISTINCT FROM preparation.source_generation
       OR generation.credential_epoch IS DISTINCT FROM preparation.credential_epoch
       OR generation.key_inventory_digest IS DISTINCT FROM preparation.key_inventory_digest
       OR generation.coverage_digest IS DISTINCT FROM preparation.eligibility_digest
       OR generation.coverage_count IS DISTINCT FROM preparation.eligible_count
       OR (SELECT state FROM public.stewardship_task_run WHERE root_id=preparation.task_id
            ORDER BY retry_sequence DESC LIMIT 1) IS DISTINCT FROM 'succeeded'
       OR NEW.target_state IS DISTINCT FROM (SELECT CASE WHEN instant<starts_at THEN 'scheduled' ELSE 'active' END
            FROM public.stewardship_campaign_configuration WHERE id=campaign.active_configuration_id) THEN
        RAISE EXCEPTION 'Production confirmation inputs changed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- stewardship_production_withdrawal_guard_v1, from
-- 0114_no_sign_in_age.sql: only its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_production_withdrawal_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE confirmation public.stewardship_production_confirmation%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
    instant timestamptz; current_inventory jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Production withdrawal is immutable' USING ERRCODE='23514';
    END IF;
    IF current_setting('transaction_isolation')<>'read committed'
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736212 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR (session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
            (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')) THEN
        RAISE EXCEPTION 'Withdrawal requires its ordered web owner' USING ERRCODE='42501';
    END IF;
    SELECT * INTO runtime FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO confirmation FROM public.stewardship_production_confirmation WHERE id=NEW.confirmation_id;
    SELECT c.* INTO campaign FROM public.stewardship_campaign c
        JOIN public.stewardship_production_request request ON request.campaign_id=c.id
        WHERE request.id=confirmation.request_id FOR UPDATE OF c;
    instant:=public.stewardship_campaign_now_v1();
    IF NEW.actor_id IS NULL OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Withdrawal requires a live Admin session' USING ERRCODE='42501';
    END IF;
    IF campaign.id IS NULL OR confirmation.target_state<>'scheduled'
       OR runtime.current_campaign_id IS DISTINCT FROM campaign.id
       OR runtime.mode<>'production' OR runtime.restore_review_required
       OR runtime.version IS DISTINCT FROM NEW.expected_runtime_version
       OR campaign.version IS DISTINCT FROM NEW.expected_campaign_version
       OR campaign.state<>'scheduled' OR campaign.ever_active OR campaign.delivery_paused
       OR campaign.active_token_generation_id IS DISTINCT FROM confirmation.generation_id
       OR instant >= (SELECT starts_at FROM public.stewardship_campaign_configuration WHERE id=campaign.active_configuration_id)
       OR NEW.preview_at>instant OR NEW.preview_at>=NEW.expires_at OR NEW.expires_at<=instant
       OR NEW.expires_at>NEW.preview_at+interval '5 minutes'
       OR btrim(NEW.reason)='' OR NOT NEW.cleanup_acknowledged THEN
        RAISE EXCEPTION 'Withdrawal is no longer admitted' USING ERRCODE='23514';
    END IF;
    SELECT inventory INTO current_inventory FROM public.stewardship_withdrawal_inventory WHERE campaign_id=campaign.id;
    IF NEW.inventory IS DISTINCT FROM current_inventory OR (current_inventory->>'blocking')::bigint<>0 THEN
        RAISE EXCEPTION 'Withdrawal work changed or remains uncertain' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- stewardship_publication_confirmation_guard_v1, from
-- 0114_no_sign_in_age.sql: only its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_publication_confirmation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE plan public.stewardship_publication_plan%ROWTYPE;
        writes bigint; fields bigint; blocked bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication confirmations are immutable' USING ERRCODE='23514';
    END IF;
    -- One confirmation at a time, so two cannot both see no overlap.
    PERFORM pg_advisory_xact_lock(736241, 91);
    IF NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Publishing to ParishSoft requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO plan FROM public.stewardship_publication_plan WHERE id=NEW.plan_id;
    -- In Testing mode publication stops after preflight.
    IF plan.id IS NULL OR plan.testing
       OR plan.created_at<=statement_timestamp()-interval '60 minutes'
       OR public.stewardship_export_admitted_v1(plan.campaign_id,true) IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication plan is missing, from Testing mode or too old to confirm'
        USING ERRCODE='23514'; END IF;
    SELECT count(*) FILTER (WHERE e.planned_sha256 IS NOT NULL),
           count(*) FILTER (WHERE e.blocked)
      INTO writes, blocked
      FROM public.stewardship_publication_entity e WHERE e.plan_id=plan.id;
    SELECT count(*) INTO fields
      FROM public.stewardship_publication_field f
      JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
     WHERE e.plan_id=plan.id AND e.planned_sha256 IS NOT NULL AND f.outcome='write';
    IF NEW.write_entities IS DISTINCT FROM writes OR NEW.write_fields IS DISTINCT FROM fields
       OR NEW.blocked_entities IS DISTINCT FROM blocked
    THEN RAISE EXCEPTION 'Publication confirmation counts differ from the plan'
        USING ERRCODE='23514'; END IF;
    -- Share-locked in key order, so no decision or edit lands before commit.
    PERFORM 1 FROM public.stewardship_proposed_change p
      JOIN public.stewardship_publication_field f ON f.proposal_id=p.id
      JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
     WHERE e.plan_id=plan.id ORDER BY p.id FOR SHARE OF p;
    IF EXISTS(SELECT 1 FROM public.stewardship_publication_field f
          JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
          JOIN public.stewardship_proposed_change p ON p.id=f.proposal_id
         WHERE e.plan_id=plan.id
           AND CASE WHEN f.outcome='already_in_parishsoft'
                -- Marked at the check (#528, ADM-09): resolved upstream, in
                -- the plan's own transaction, at the next version.
                THEN p.version<>f.proposal_version+1 OR p.execution<>'resolved_upstream'
                ELSE p.version<>f.proposal_version
                OR p.execution NOT IN ('pending','failed','conflict')
                -- A planned write must still be approved and out of
                -- conflict: a writer need not bump the version (#1243).
                OR (f.outcome='write'
                    AND (p.decision<>'approved' OR p.execution='conflict')) END)
    THEN RAISE EXCEPTION 'A planned census change has changed since preflight'
        USING ERRCODE='23514'; END IF;
    -- No change this plan writes is still being written by another
    -- confirmed plan: that entity has no published or invalidated outcome.
    IF EXISTS(SELECT 1 FROM public.stewardship_publication_field f
          JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
          JOIN public.stewardship_publication_field other_field
            ON other_field.proposal_id=f.proposal_id AND other_field.outcome='write'
          JOIN public.stewardship_publication_entity other
            ON other.id=other_field.entity_id AND other.plan_id<>plan.id
           AND other.planned_sha256 IS NOT NULL
          JOIN public.stewardship_publication_confirmation c ON c.plan_id=other.plan_id
         WHERE e.plan_id=plan.id AND e.planned_sha256 IS NOT NULL AND f.outcome='write'
           AND NOT EXISTS(SELECT 1 FROM public.stewardship_publication_outcome o
                WHERE o.entity_id=other.id AND o.result IN ('published','invalidated')))
    THEN RAISE EXCEPTION 'Another confirmed publication is still writing these changes'
        USING ERRCODE='23505'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

-- stewardship_restore_resolution_v1, from
-- 0048_restore_holds_after_release.sql: only its message and a stale
-- sign-in comment reworded.
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
    -- "Send again" on a held reminder is refused once a later reminder for
    -- the same Family (removed since or not) was delivered, is on its way or
    -- its delivery is unknown: it would arrive out of order (#757). The
    -- Family dispatch check refuses the same thing at send time.
    IF NEW.state='resend_authorized' AND EXISTS(SELECT 1 FROM stewardship_schedule_definition earlier
        JOIN stewardship_schedule_revision earlier_revision ON earlier_revision.id=earlier.current_revision_id
        JOIN stewardship_schedule_definition later_definition ON later_definition.campaign_id=earlier.campaign_id
        JOIN stewardship_schedule_occurrence later ON later.definition_id=later_definition.id
        WHERE earlier.id=h.definition_id AND earlier.kind='reminder' AND later_definition.kind='reminder'
          AND later_definition.id<>earlier.id AND later.mode=h.mode AND later.target=h.target
          AND later.due_at>earlier_revision.due_at
          AND (EXISTS(SELECT 1 FROM stewardship_outbox_message later_message
                  WHERE later_message.id=later.outbox_id
                    AND later_message.state IN ('submitting','delivery_unknown','delivered'))
              OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment later_sent
                  WHERE later_sent.occurrence_id=later.id AND later_sent.disposition='delivered'))) THEN
        RAISE EXCEPTION 'A later reminder reached this Family or is on its way; assume this one sent'
            USING ERRCODE='23514'; END IF;
    -- Settling a held email is the decision of an Administrator whose
    -- session is live (migration 0017, #537; any sign-in age, #1547).
    IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
        RAISE EXCEPTION 'A restore decision requires a live Administrator session' USING ERRCODE='42501'; END IF;
    RETURN NEW;
END $$;

-- stewardship_runtime_transition_v1, from
-- 0046_restore_begin_keeps_review.sql: only its message and a stale
-- sign-in comment reworded.
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
        -- An Administrator whose session is live releases the site after review.
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
            RAISE EXCEPTION 'A restore decision requires a live Administrator session' USING ERRCODE='42501'; END IF;
        IF EXISTS(SELECT 1 FROM public.stewardship_restore_hold_candidates_v1()) THEN
            RAISE EXCEPTION 'The held-email list is stale: find the held emails again before release'
                USING ERRCODE='23514'; END IF;
    ELSE RAISE EXCEPTION 'Unsupported runtime transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- stewardship_sealed_intake_admission_v1, from 0114_no_sign_in_age.sql:
-- only its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_sealed_intake_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE ended boolean := false;
BEGIN
    IF TG_TABLE_NAME='stewardship_secret_request' THEN
        -- The recorded sign-in instant may be of any age (#1547). One that
        -- is a full-scope automation session's needs one such session
        -- live (ADM-11), so an ended session's request is refused. A
        -- separate statement, reached only by the web (which holds
        -- EXECUTE) or the schema owner: EXECUTE is checked whenever an
        -- expression is initialized. A setup install's installer
        -- records the setup browser session's instant.
        IF NEW.required_consumers<>'[]'::jsonb
           AND (current_user='pk_stewardship_web' OR pg_has_role(current_user,
                (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')) THEN
            ended := coalesce(public.stewardship_automation_instant_ended_v1(
                NEW.requested_by_id,NEW.reauthenticated_at),false);
        END IF;
        IF ended THEN
            RAISE EXCEPTION 'Sealed intake requires a live automation session'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NOT EXISTS(SELECT 1 FROM stewardship_secret_request
            WHERE id=NEW.request_id AND required_consumers<>'[]'::jsonb) THEN
            RAISE EXCEPTION 'Sealed intake requires installer consumers'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- stewardship_setup_install_binding_v1, from the baseline functions.sql:
-- only its comment reworded.
CREATE OR REPLACE FUNCTION public.stewardship_setup_install_binding_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE credential public.stewardship_setup_sealed_credential%ROWTYPE;
    request public.stewardship_secret_request%ROWTYPE;
    attempt public.stewardship_setup_attempt%ROWTYPE;
    login public.stewardship_portal_session%ROWTYPE;
BEGIN
    IF current_user<>'pk_stewardship_credential_'||NEW.target
        OR NOT public.stewardship_setup_install_ready_live_v1(NEW.readiness_id)
        OR NOT public.stewardship_setup_install_selected_v1(
            NEW.readiness_id,NEW.credential_id,NEW.credential_version,NEW.fingerprint)
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736213 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Initial credential requires its isolated frozen setup owner'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO credential FROM public.stewardship_setup_sealed_credential
        WHERE id=NEW.credential_id;
    SELECT * INTO request FROM public.stewardship_secret_request WHERE id=NEW.request_id;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt WHERE id=credential.attempt_id;
    SELECT id,authenticated_at,expires_at INTO login.id,login.authenticated_at,login.expires_at
        FROM public.stewardship_portal_session WHERE id=attempt.session_id;
    IF NEW.request_id<>NEW.credential_id OR request.target IS DISTINCT FROM NEW.target
        OR credential.target IS DISTINCT FROM NEW.target
        OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.requested_by_id IS DISTINCT FROM attempt.owner_id
        OR request.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.state IS DISTINCT FROM 'staged' OR request.version<>1
        -- The installer copies the login's instant, which never changes in
        -- place (#1550); never a later claim.
        OR login.id IS NULL OR request.reauthenticated_at>login.authenticated_at
        OR request.expires_at>login.expires_at OR request.expires_at<=clock_timestamp()
        OR request.required_consumers IS DISTINCT FROM stewardship_credential_consumers_v1(NEW.target)
        THEN
        RAISE EXCEPTION 'Initial credential request differs from its original setup'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- stewardship_setup_secret_audit_v1, from 0104_setup_sign_in_hour.sql:
-- only its comment reworded.
CREATE OR REPLACE FUNCTION public.stewardship_setup_secret_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid(); context jsonb; age bigint;
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        CASE WHEN NEW.scrubbed_at IS NULL THEN 'setup_credential_staged'
            ELSE 'setup_credential_scrubbed' END,NEW.id,'deployment');
    context:=jsonb_build_object('version',NEW.version,'outcome',
        CASE WHEN NEW.scrubbed_at IS NULL THEN 'changed' ELSE 'cancelled' END);
    IF NEW.scrubbed_at IS NULL THEN
        -- How old the setup session's Google sign-in was when the credential
        -- was sealed (#1402), recorded as audit evidence when it is staged
        -- (the instant never changes in place, #1550). A separate statement,
        -- run only for a staged credential: the scrubbing scheduler cannot
        -- read the session.
        SELECT greatest(0,floor(extract(epoch FROM clock_timestamp()-login.authenticated_at)))::bigint
            INTO age
            FROM public.stewardship_setup_attempt attempt
            JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            WHERE attempt.id=NEW.attempt_id;
        IF age IS NOT NULL THEN
            context:=context||jsonb_build_object('sign_in_age_seconds',age);
        END IF;
    END IF;
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        context);
    RETURN NULL;
END $$;

-- stewardship_source_drop_acceptance_guard_v1, from
-- 0114_no_sign_in_age.sql: only its message reworded.
CREATE OR REPLACE FUNCTION public.stewardship_source_drop_acceptance_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz := statement_timestamp();
    refused_at timestamptz;
    any_failed boolean;
    empty boolean;
    recorded jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'A large-change acceptance is never changed or deleted' USING ERRCODE='42501';
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    -- The checks read other rows at the statement's own snapshot, which only
    -- READ COMMITTED gives a statement that waited on the lock.
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'A large-change acceptance requires READ COMMITTED' USING ERRCODE='42501';
    END IF;
    IF current_user IS DISTINCT FROM 'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web portal accepts a large ParishSoft change' USING ERRCODE='42501';
    END IF;
    -- One live acceptance at a time: serialize inserts before looking.
    PERFORM pg_advisory_xact_lock(736241, 4);
    IF NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Accepting a large ParishSoft change requires a live Administrator session' USING ERRCODE='42501';
    END IF;
    SELECT max(c.created_at), bool_or(c.failed),
           bool_or(c.measure IN ('family','member') AND c.after=0),
           jsonb_object_agg(c.measure, jsonb_build_object(
               'before', c.before, 'after', c.after, 'failed', c.failed))
      INTO refused_at, any_failed, empty, recorded
      FROM public.stewardship_source_drop_count c
     WHERE c.attempt_id=NEW.attempt_id;
    IF refused_at IS NULL THEN
        RAISE EXCEPTION 'An acceptance names a refused ParishSoft refresh' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.stewardship_source_drop_count later
               WHERE later.attempt_id<>NEW.attempt_id AND later.created_at>=refused_at)
       OR EXISTS(SELECT 1 FROM public.stewardship_source_snapshot s
                  WHERE s.kind='full' AND s.state='promoted' AND s.promoted_at>=refused_at) THEN
        RAISE EXCEPTION 'The refusal is no longer the newest full refresh''s outcome' USING ERRCODE='23514';
    END IF;
    IF NOT any_failed THEN
        RAISE EXCEPTION 'The refusal has no count that fell too far' USING ERRCODE='23514';
    END IF;
    IF empty THEN
        RAISE EXCEPTION 'A load with no Families or no Members cannot be accepted' USING ERRCODE='23514';
    END IF;
    IF NEW.counts IS DISTINCT FROM recorded THEN
        RAISE EXCEPTION 'The reviewed counts differ from the recorded refusal' USING ERRCODE='23514';
    END IF;
    IF public.stewardship_source_drop_acceptance_active_v1() IS NOT NULL THEN
        RAISE EXCEPTION 'Another large-change acceptance is still in effect' USING ERRCODE='23505';
    END IF;
    NEW.created_at:=instant;
    NEW.expires_at:=instant+interval '24 hours';
    RETURN NEW;
END $$;

DO $check$
DECLARE wanted record;
BEGIN
    -- Each body exactly as declared, with its rights and search_path: a
    -- definer stays a definer, an invoker an invoker.
    FOR wanted IN SELECT * FROM (VALUES
        ('stewardship_backup_request_guard_v1','e14b49f44e6cb445f17517edbda0a33a',false,'A backup request requires a live Administrator session'),
        ('stewardship_debug_off_switch_guard_v1','fe1adc4a74921b52c4b9fd7d92b59d3c',false,'Allowing debug logging again requires a live Administrator session'),
        ('stewardship_debug_off_switch_guard_v1','fe1adc4a74921b52c4b9fd7d92b59d3c',false,'The debug-off switch requires a live Administrator session'),
        ('stewardship_delivery_control_guard_v1','c2c9ed722bb6eb4f4eff80ad29c34bb4',true,'Delivery control requires a live Admin session'),
        ('stewardship_production_confirmation_guard_v1','db6d94c9f931ff7f7724b7d4664c73bf',true,'Production confirmation requires completed cleanup and a live Admin session'),
        ('stewardship_production_withdrawal_guard_v1','88925e3b733dfefca52d87871329d547',true,'Withdrawal requires a live Admin session'),
        ('stewardship_publication_confirmation_guard_v1','c290c49c8f0d49aa1dd15336cdacc4c4',false,'Publishing to ParishSoft requires a live Administrator session'),
        ('stewardship_restore_resolution_v1','d7609bc84fc058d7ed6cd7dd1741a70c',false,'A restore decision requires a live Administrator session'),
        ('stewardship_runtime_transition_v1','dac560aa371a77eab7e92c988edd3e60',false,'A restore decision requires a live Administrator session'),
        ('stewardship_sealed_intake_admission_v1','ec8e4b98eeefb9fe437cee968865d00d',false,'Sealed intake requires a live automation session'),
        ('stewardship_setup_install_binding_v1','7991ea0ae8a73e58aef58440322b1f39',false,'which never changes in'),
        ('stewardship_setup_secret_audit_v1','0b4bfb4b520e433ce281f358cfd9bf82',false,'(the instant never changes in place, #1550)'),
        ('stewardship_source_drop_acceptance_guard_v1','b49b08956f693575b8c734e53f5c43be',false,'Accepting a large ParishSoft change requires a live Administrator session')
    ) AS w(name, digest, definer, phrase) LOOP
        IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE n.nspname='public' AND p.proname=wanted.name
              AND md5(p.prosrc)=wanted.digest
              AND strpos(p.prosrc, wanted.phrase)>0
              AND p.prosecdef=wanted.definer
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND l.lanname='plpgsql'
              AND p.prosrc NOT LIKE '%step-up%')<>1 THEN
            RAISE EXCEPTION 'Migration 0124: % is not installed as declared', wanted.name;
        END IF;
    END LOOP;
    -- No installed function still raises the old words.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosrc ~ 'requires fresh [^'']*authentication') THEN
        RAISE EXCEPTION 'Migration 0124: a function still asks for fresh authentication';
    END IF;
    -- Owner, rights, search_path, language and grants are as before.
    IF current_setting('stewardship.migration_0124_attributes', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text||'|'||p.proowner::regrole::text, ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_backup_request_guard_v1',
               'stewardship_debug_off_switch_guard_v1',
               'stewardship_delivery_control_guard_v1',
               'stewardship_production_confirmation_guard_v1',
               'stewardship_production_withdrawal_guard_v1',
               'stewardship_publication_confirmation_guard_v1',
               'stewardship_restore_resolution_v1',
               'stewardship_runtime_transition_v1',
               'stewardship_sealed_intake_admission_v1',
               'stewardship_setup_install_binding_v1',
               'stewardship_setup_secret_audit_v1',
               'stewardship_source_drop_acceptance_guard_v1')) THEN
        RAISE EXCEPTION 'Migration 0124: a replaced function changed its rights, configuration or grants';
    END IF;
    -- Every one is a trigger function that still guards every table it
    -- guarded, with each of its triggers still enabled.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN (
            'stewardship_backup_request_guard_v1',
            'stewardship_debug_off_switch_guard_v1',
            'stewardship_delivery_control_guard_v1',
            'stewardship_production_confirmation_guard_v1',
            'stewardship_production_withdrawal_guard_v1',
            'stewardship_publication_confirmation_guard_v1',
            'stewardship_restore_resolution_v1',
            'stewardship_runtime_transition_v1',
            'stewardship_sealed_intake_admission_v1',
            'stewardship_setup_install_binding_v1',
            'stewardship_setup_secret_audit_v1',
            'stewardship_source_drop_acceptance_guard_v1')
          AND (p.prorettype<>'trigger'::regtype
               OR NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')))
       OR current_setting('stewardship.migration_0124_triggers', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||(SELECT count(*) FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')::text, ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_backup_request_guard_v1',
               'stewardship_debug_off_switch_guard_v1',
               'stewardship_delivery_control_guard_v1',
               'stewardship_production_confirmation_guard_v1',
               'stewardship_production_withdrawal_guard_v1',
               'stewardship_publication_confirmation_guard_v1',
               'stewardship_restore_resolution_v1',
               'stewardship_runtime_transition_v1',
               'stewardship_sealed_intake_admission_v1',
               'stewardship_setup_install_binding_v1',
               'stewardship_setup_secret_audit_v1',
               'stewardship_source_drop_acceptance_guard_v1')) THEN
        RAISE EXCEPTION 'Migration 0124: a replaced guard no longer has its trigger';
    END IF;
END
$check$;
