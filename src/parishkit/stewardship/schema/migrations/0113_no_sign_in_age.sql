-- Frozen forward migration file 0113 (the repository-wide file sequence):
-- the Admin portal no longer asks for a recent sign-in (#1547). This file is
-- installed by the Django migration that names it in FROZEN_SQL and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copies of baseline functions equal the
-- fresh-install baseline's (delivery_control.sql, family_mail_tests.sql,
-- production_confirmation.sql, production_withdrawal.sql, functions.sql).
--
-- Google ignores max_age and never sends auth_time, so "signed in within the
-- last five minutes" only proved a live Google browser session. The web no
-- longer checks a sign-in's age, and these guards stop checking it too. Each
-- replaced body differs from its latest earlier copy only there:
--   - a session-bound guard's "signed in within five minutes, or a live
--     full-scope automation session stands in" becomes "not in the future,
--     and a command session only with its live full-scope automation
--     session" (stewardship_automation_fresh_v1, as 0013 already required of
--     a command session); the session must still be the actor's, unrevoked,
--     unexpired, active in the last 60 minutes and, where it was, have
--     exactly the row's sign-in instant (delivery controls, chosen-Family
--     tests, the Production confirmation and withdrawal, backup requests,
--     the debug-off switch and its clear, large ParishSoft change
--     acceptance, publication confirmation); restore decisions, which no
--     automation session makes, just drop the age;
--   - the Production confirmation no longer needs a sign-in after cleanup
--     completed, only completed cleanup;
--   - a secret request's recorded sign-in instant may be of any age (the
--     intake and staged-to-testing checks);
--   - a staged setup credential's setup session may have signed in at any
--     time (0104's hour);
--   - an automation session may be approved from a browser session signed in
--     at any time.
-- Error messages keep their wording, which callers match. A secret
-- request's instant cannot tell a browser from an automation session (an
-- automation session records its approving sign-in), so the web's
-- automation check guards those alone. stewardship_automation_fresh_
-- principal_v1 stays installed, unused, as do the recorded sign-in columns.
--
-- CREATE OR REPLACE keeps each function's owner and grants; every header
-- repeats its language, rights and search_path. The DO block at the end
-- refuses to commit unless every body is installed as declared with its
-- rights, configuration and grants unchanged. Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;


-- Each function's grants, rights and configuration before the replacement,
-- compared in the DO block (a transaction-local setting: the migration
-- login has no TEMP privilege).
SELECT set_config('stewardship.migration_0113_attributes', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text, ';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
            'stewardship_automation_session_guard_v1',
            'stewardship_backup_request_guard_v1',
            'stewardship_debug_off_switch_guard_v1',
            'stewardship_delivery_control_guard_v1',
            'stewardship_family_mail_test_guard_v1',
            'stewardship_production_confirmation_guard_v1',
            'stewardship_production_withdrawal_guard_v1',
            'stewardship_publication_confirmation_guard_v1',
            'stewardship_restore_decision_admitted_v1',
            'stewardship_sealed_intake_admission_v1','stewardship_secret_state_v2',
            'stewardship_setup_secret_guard_v1',
            'stewardship_source_drop_acceptance_guard_v1'
    )), true);

-- stewardship_automation_session_guard_v1: 0004_automation_sessions.sql's body without the sign-in age.
CREATE OR REPLACE FUNCTION public.stewardship_automation_session_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE owner boolean := pg_has_role(current_user,
    (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Automation sessions are kept for attribution' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NOT owner AND current_user<>'pk_stewardship_web' THEN
            RAISE EXCEPTION 'Only the web login approves automation sessions' USING ERRCODE='42501';
        END IF;
        IF NEW.version<>1 OR NEW.revoked_at IS NOT NULL OR NEW.end_reason IS NOT NULL
           OR NEW.last_used_at IS NOT NULL OR NEW.created_at>statement_timestamp()
           OR NEW.expires_at<=statement_timestamp()
           OR NEW.expires_at>statement_timestamp()+interval '30 days'
           -- The approving sign-in may be of any age (#1547).
           OR NEW.authenticated_at>statement_timestamp() THEN
            RAISE EXCEPTION 'Automation session approval is outside its bounds' USING ERRCODE='23514';
        END IF;
        IF NOT public.stewardship_export_authorized_v1(NEW.principal_id,true)
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session s
               WHERE s.id=NEW.approving_session_id AND s.principal_id=NEW.principal_id
                 AND s.revoked_at IS NULL AND s.expires_at>statement_timestamp()
                 AND s.last_activity_at>statement_timestamp()-interval '60 minutes'
                 AND s.authenticated_at=NEW.authenticated_at)
           OR EXISTS (SELECT 1 FROM public.stewardship_automation_login l
               WHERE l.portal_session_id=NEW.approving_session_id) THEN
            RAISE EXCEPTION 'Automation approval requires a freshly signed-in Administrator browser session' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION 'An ended automation session cannot change' USING ERRCODE='23514';
    END IF;
    IF NEW.last_used_at IS DISTINCT FROM OLD.last_used_at
       AND ((NOT owner AND current_user<>'pk_stewardship_web')
            OR NEW.last_used_at IS NULL OR NEW.last_used_at>statement_timestamp()
            OR NEW.last_used_at<coalesce(OLD.last_used_at,OLD.created_at)) THEN
        RAISE EXCEPTION 'Automation session use is recorded forward by the web login only' USING ERRCODE='23514';
    END IF;
    IF NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
       OR NEW.end_reason IS DISTINCT FROM OLD.end_reason THEN
        IF NEW.revoked_at IS NULL OR NEW.end_reason IS NULL
           OR NEW.revoked_at>statement_timestamp() OR NEW.revoked_at<OLD.created_at THEN
            RAISE EXCEPTION 'An automation session ends once, with its reason' USING ERRCODE='23514';
        END IF;
        IF NOT owner AND NOT (current_user='pk_stewardship_web'
            OR (current_user='pk_stewardship_worker'
                AND NEW.end_reason IN ('role_lost','user_removed','recovery')
                AND NOT public.stewardship_automation_live_v1(NEW.id))
            OR (current_user='pk_stewardship_admin_recovery'
                AND NEW.end_reason IN ('restore','revoked_by_operator'))) THEN
            RAISE EXCEPTION 'This login cannot end an automation session for this reason' USING ERRCODE='42501';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- stewardship_backup_request_guard_v1: 0062_backup_request.sql's body without the sign-in age.
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
            RAISE EXCEPTION 'A backup request requires fresh Administrator authentication' USING ERRCODE='42501';
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

-- stewardship_debug_off_switch_guard_v1: 0088_debug_allow.sql's body without the sign-in age.
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
            RAISE EXCEPTION 'The debug-off switch requires fresh Administrator authentication' USING ERRCODE='42501';
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
            RAISE EXCEPTION 'Allowing debug logging again requires fresh Administrator authentication' USING ERRCODE='42501';
        END IF;
    END IF;
    NEW.cleared_at:=instant;
    RETURN NEW;
END $$;

-- stewardship_delivery_control_guard_v1: 0013_automation_fresh_guards.sql's body without the sign-in age.
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
        RAISE EXCEPTION 'Delivery control requires fresh Admin authentication' USING ERRCODE='42501';
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

-- stewardship_family_mail_test_guard_v1: 0013_automation_fresh_guards.sql's body without the sign-in age.
CREATE OR REPLACE FUNCTION public.stewardship_family_mail_test_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Family test tickets are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Family test requires ordered ownership' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web'
           OR NOT public.stewardship_family_test_live_v1(NEW.configuration_id,NEW.campaign_id,
                NEW.template_id,NEW.requested_by_id,NEW.rehearsal_epoch_id)
           OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.state<>'queued' OR NEW.version<>1
           OR NEW.family_id IS NULL OR NEW.outbox_id IS NOT NULL
           -- A live session of the Admin has the recorded sign-in instant,
           -- whatever its age (#1547); a command session only with its
           -- live full-scope automation session (ADM-11).
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session
                WHERE principal_id=NEW.requested_by_id AND revoked_at IS NULL
                    AND authenticated_at=NEW.reauthenticated_at AND expires_at>stamp
                    AND authenticated_at<=stamp
                    AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                             WHERE command.portal_session_id=id)
                         OR public.stewardship_automation_fresh_v1(id,NEW.requested_by_id))
                    AND last_activity_at>stamp-interval '60 minutes')
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.task_id AND task.root_id=task.id
                    AND task.task_type='family_mail_test' AND task.state='queued'
                    AND task.domain_request_id=NEW.id
                    AND task.idempotency_key=NEW.id::text
                    AND task.initiated_by_id=NEW.requested_by_id)
           -- Only a template a current invitation/reminder schedule really uses.
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_content_version content
                JOIN public.stewardship_schedule_revision revision
                    ON revision.values->>'template_version'=content.record_id::text
                    AND revision.campaign_id=NEW.campaign_id
                JOIN public.stewardship_schedule_definition definition
                    ON definition.current_revision_id=revision.id
                    AND definition.kind IN ('initial','reminder')
                WHERE content.id=NEW.template_id AND content.configuration_id=NEW.configuration_id)
           -- A real, currently deliverable Family of this campaign.
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign f
                JOIN public.stewardship_campaign_credentials k ON k.campaign_id=f.campaign_id
                JOIN public.stewardship_source_current s ON s.snapshot_id=k.source_snapshot_id
                    AND s.generation=k.source_generation
                WHERE f.id=NEW.family_id AND f.campaign_id=NEW.campaign_id
                    AND f.active AND f.portal_eligible AND f.email_eligible AND f.email_deliverable
                    AND NOT k.population_dirty AND f.source_generation=s.generation) THEN
            RAISE EXCEPTION 'Family test requires explicit current Admin intent'
                USING ERRCODE='23514';
        END IF;
        -- Bound unsettled tests per campaign: queued tickets plus prepared
        -- messages that have not reached a terminal delivery state.
        IF (SELECT count(*) FROM public.stewardship_family_mail_test
                WHERE campaign_id=NEW.campaign_id AND state='queued')
           +(SELECT count(*) FROM public.stewardship_outbox_message
                WHERE campaign_id=NEW.campaign_id AND purpose='family_test'
                    AND state NOT IN ('delivered','permanent_failure','cancelled'))>=10 THEN
            RAISE EXCEPTION 'Too many Family tests are in progress' USING ERRCODE='23514';
        END IF;
        NEW.created_at:=stamp; NEW.updated_at:=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state<>'queued' THEN
        RAISE EXCEPTION 'Terminal Family test cannot be rewritten' USING ERRCODE='23514';
    END IF;
    IF NEW.state='prepared' THEN
        -- Only the general worker, under its live claim, with the outbox
        -- message already allocated for this exact ticket and Family.
        IF current_user<>'pk_stewardship_worker' OR NEW.family_id IS NOT NULL
           OR NEW.outbox_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_task_run t
            JOIN public.stewardship_outbox_message m ON m.id=NEW.outbox_id
            WHERE t.id=NEW.correlation_id AND t.root_id=OLD.task_id
              AND t.task_type='family_mail_test' AND t.state='running'
              AND t.worker_id=NEW.actor_id AND t.lease_expires_at>stamp
              AND t.domain_request_id=OLD.id
              AND m.semantic_key=OLD.id AND m.campaign_id=OLD.campaign_id
              AND m.family_id=OLD.family_id AND m.purpose='family_test' AND m.mode='testing'
              AND m.rehearsal_epoch_id=OLD.rehearsal_epoch_id AND m.state='pending'
              AND m.correlation_id=t.id AND m.actor_id=t.worker_id) THEN
            RAISE EXCEPTION 'Only the live preparation worker may complete a Family test'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    -- The scheduler settles stale tickets: failed when the task failed without
    -- a message, cancelled when the durable scope is gone or the worker safely
    -- cancelled the task (a lastingly ineligible Family). Temporary gates
    -- (restore review, campaign work) leave a queued ticket waiting.
    IF current_user<>'pk_stewardship_scheduler' OR NEW.actor_id IS NOT NULL
       OR NEW.family_id IS NOT NULL OR NEW.outbox_id IS NOT NULL
       OR NOT ((NEW.state='failed' AND EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=OLD.task_id AND task.state='failed'))
           OR (NEW.state='cancelled' AND (EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=OLD.task_id AND task.state='cancelled')
             OR NOT public.stewardship_family_test_scope_v1(
                OLD.configuration_id,OLD.campaign_id,OLD.template_id,OLD.requested_by_id,
                OLD.rehearsal_epoch_id)))) THEN
        RAISE EXCEPTION 'Only stale unsent Family tests can be settled' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- stewardship_production_confirmation_guard_v1: 0013_automation_fresh_guards.sql's body without the sign-in age.
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
        RAISE EXCEPTION 'Production confirmation requires fresh post-cleanup Admin authentication' USING ERRCODE='42501';
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

-- stewardship_production_withdrawal_guard_v1: 0013_automation_fresh_guards.sql's body without the sign-in age.
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
        RAISE EXCEPTION 'Withdrawal requires fresh Admin authentication' USING ERRCODE='42501';
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

-- stewardship_publication_confirmation_guard_v1: 0112_upstream_at_check.sql's body without the sign-in age.
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
        RAISE EXCEPTION 'Publishing to ParishSoft requires fresh Administrator authentication'
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

-- stewardship_restore_decision_admitted_v1: 0017_restore_review.sql's body without the sign-in age.
CREATE OR REPLACE FUNCTION public.stewardship_restore_decision_admitted_v1(actor uuid, session uuid, signed_in timestamptz) RETURNS boolean
LANGUAGE sql VOLATILE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')
        OR (current_user='pk_stewardship_web' AND actor IS NOT NULL
            AND public.stewardship_export_authorized_v1(actor,true) IS TRUE
            AND EXISTS(SELECT 1 FROM public.stewardship_portal_session login
                WHERE login.id=session AND login.principal_id=actor
                  AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                  AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
                  AND login.authenticated_at=signed_in
                  -- Any sign-in age (#1547): the session is live and bound.
                  AND login.authenticated_at<=clock_timestamp()))
$$;

-- stewardship_sealed_intake_admission_v1: 0013_automation_fresh_guards.sql's body without the sign-in age.
CREATE OR REPLACE FUNCTION public.stewardship_sealed_intake_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- A secret request records its session's sign-in instant at any age
    -- (#1547); only a staging row is checked here.
    IF TG_TABLE_NAME<>'stewardship_secret_request' THEN
        IF NOT EXISTS(SELECT 1 FROM stewardship_secret_request
            WHERE id=NEW.request_id AND required_consumers<>'[]'::jsonb) THEN
            RAISE EXCEPTION 'Sealed intake requires installer consumers'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- stewardship_secret_state_v2: 0013_automation_fresh_guards.sql's body without the sign-in age.
CREATE OR REPLACE FUNCTION public.stewardship_secret_state_v2() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Secret request history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF jsonb_typeof(NEW.required_consumers)<>'array'
       OR jsonb_array_length(NEW.required_consumers)>6
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.required_consumers) x WHERE jsonb_typeof(x)<>'string')
       OR NOT stewardship_credential_consumers_v1(NEW.target) @> NEW.required_consumers
       OR (SELECT count(*)<>count(DISTINCT x) FROM jsonb_array_elements(NEW.required_consumers) x) THEN
        RAISE EXCEPTION 'Invalid credential consumer inventory' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.created_at:=statement_timestamp(); NEW.updated_at:=NEW.created_at;
        IF NEW.state<>'staged' OR NEW.version<>1 OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.expires_at<=statement_timestamp() OR NEW.resulting_fingerprint IS NOT NULL
           OR NEW.installed_at IS NOT NULL OR NEW.acknowledged_at IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid secret request intake' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.required_consumers IS DISTINCT FROM OLD.required_consumers
       OR (OLD.resulting_fingerprint IS NOT NULL AND NEW.resulting_fingerprint IS DISTINCT FROM OLD.resulting_fingerprint)
       OR (OLD.installed_at IS NOT NULL AND NEW.installed_at IS DISTINCT FROM OLD.installed_at)
       OR (OLD.acknowledged_at IS NOT NULL AND NEW.acknowledged_at IS DISTINCT FROM OLD.acknowledged_at) THEN
        RAISE EXCEPTION 'Credential installation evidence is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.required_consumers<>'[]'::jsonb
       AND NOT(OLD.state='staged' AND NEW.state='cleanup_pending' AND NEW.cleanup_reason='cancelled')
       AND current_user<>'pk_stewardship_credential_'||OLD.target THEN
        RAISE EXCEPTION 'Only the target installer may advance this request' USING ERRCODE='23514';
    END IF;
    IF OLD.resulting_fingerprint IS NULL AND NEW.resulting_fingerprint IS NOT NULL
       AND NOT(OLD.state='testing' AND NEW.state='installing') THEN
        RAISE EXCEPTION 'Credential fingerprint requires successful testing' USING ERRCODE='23514';
    END IF;
    IF OLD.installed_at IS NULL AND NEW.installed_at IS NOT NULL
       AND NOT(OLD.state='installing' AND NEW.state='awaiting_ack') THEN
        RAISE EXCEPTION 'Credential install instant requires installation' USING ERRCODE='23514';
    END IF;
    IF OLD.acknowledged_at IS NULL AND NEW.acknowledged_at IS NOT NULL
       AND NOT(OLD.state='awaiting_ack' AND NEW.state='cleanup_pending' AND NEW.cleanup_reason='applied') THEN
        RAISE EXCEPTION 'Credential acknowledgement requires consumer evidence' USING ERRCODE='23514';
    END IF;
    IF OLD.state='staged' AND NEW.state='testing' THEN
        -- The request's sign-in instant may be of any age (#1547).
        IF NEW.actor_id IS NOT NULL OR NEW.expires_at<=statement_timestamp()
           OR NEW.required_consumers='[]'::jsonb
           OR NOT EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging WHERE request_id=NEW.id AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Credential testing requires a live sealed request' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='testing' AND NEW.state='installing' THEN
        IF NEW.resulting_fingerprint IS NULL OR NEW.expires_at<=statement_timestamp() OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Credential installation requires tested fingerprint' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging
            WHERE request_id=NEW.id AND fingerprint=NEW.resulting_fingerprint AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Tested credential must match its sealed intake' USING ERRCODE='23514'; END IF;
    ELSIF OLD.state='installing' AND NEW.state='awaiting_ack' THEN
        IF NEW.actor_id IS NOT NULL THEN RAISE EXCEPTION 'Installation requires system attribution' USING ERRCODE='23514'; END IF;
        NEW.installed_at:=statement_timestamp();
    ELSIF OLD.state IN('staged','testing','installing','awaiting_ack') AND NEW.state='cleanup_pending' THEN
        IF NEW.cleanup_reason='cancelled' THEN
            IF NEW.actor_id IS DISTINCT FROM OLD.requested_by_id THEN
                RAISE EXCEPTION 'Invalid cancellation attribution' USING ERRCODE='23514'; END IF;
        ELSIF NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Cleanup requires system attribution' USING ERRCODE='23514';
        END IF;
        IF NEW.cleanup_reason='expired' AND OLD.expires_at>statement_timestamp() AND NOT (CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install WHERE request_id=NEW.id) AND NOT public.stewardship_setup_install_live_v1(NEW.id) ELSE false END) THEN
            RAISE EXCEPTION 'Secret request is not expired' USING ERRCODE='23514';
        END IF;
        IF NEW.cleanup_reason='applied' THEN
            IF OLD.state<>'awaiting_ack' OR (OLD.expires_at<=statement_timestamp() AND CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN public.stewardship_setup_install_completed_v1(NEW.id) IS NULL ELSE true END)
               OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.required_consumers) c
                    WHERE NOT EXISTS(SELECT 1 FROM stewardship_credential_consumer_ack a
                        WHERE a.request_id=NEW.id AND a.consumer=c AND a.fingerprint=NEW.resulting_fingerprint)) THEN
                RAISE EXCEPTION 'Credential consumers have not acknowledged' USING ERRCODE='23514';
            END IF;
            NEW.acknowledged_at:=CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN coalesce(public.stewardship_setup_install_completed_v1(NEW.id),statement_timestamp()) ELSE statement_timestamp() END;
        END IF;
    ELSIF OLD.state='cleanup_pending' AND NEW.state=OLD.cleanup_reason AND NEW.cleanup_reason=OLD.cleanup_reason THEN
        IF NEW.actor_id IS NOT NULL THEN RAISE EXCEPTION 'Cleanup requires system attribution' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM stewardship_sealed_credential_staging WHERE request_id=NEW.id AND ciphertext IS NOT NULL) THEN
            RAISE EXCEPTION 'Credential ciphertext must be scrubbed before completion' USING ERRCODE='23514'; END IF;
        NEW.scrubbed_at:=statement_timestamp();
    ELSE
        RAISE EXCEPTION 'Invalid secret request transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- stewardship_setup_secret_guard_v1: 0111_setup_slack_off.sql's body without the sign-in age.
CREATE OR REPLACE FUNCTION public.stewardship_setup_secret_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
        stamp timestamptz:=clock_timestamp(); keys text[]; item jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup credential tombstones are retained'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup credentials require ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup credentials cannot be repopulated'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF NEW.ciphertext IS NOT NULL OR NEW.settings<>'{}'::jsonb
           OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
            RAISE EXCEPTION 'Setup credential scrub requires its terminal fence'
                USING ERRCODE='23514';
        END IF;
        -- Turning Slack off during setup discards the staged Slack token at
        -- once (#1499): only the web, for the attempt's owner, while it is
        -- collecting and its saved Slack step is off. Every other scrub
        -- needs the terminal fence, by the system.
        IF attempt.state='collecting' THEN
            IF current_user<>'pk_stewardship_web' OR NEW.target<>'slack'
               OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
               OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_draft_section slack
                    WHERE slack.attempt_id=attempt.id AND slack.step='slack'
                        AND slack.scrubbed_at IS NULL
                        AND slack.values->'enabled'='false'::jsonb) THEN
                RAISE EXCEPTION 'Only a setup with Slack turned off discards its Slack token'
                    USING ERRCODE='23514';
            END IF;
        ELSIF attempt.state NOT IN ('expired','completed')
           OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Setup credential scrub requires its terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema IN ('bootstrap-policy-v1', 'bootstrap-users-v10')
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '60 minutes'
            -- Any sign-in age (#1547): the session is live.
            AND login.authenticated_at<=stamp
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup credential requires its original freshly signed-in Admin'
            USING ERRCODE='23514';
    END IF;
    IF NEW.ciphertext IS NULL OR length(NEW.ciphertext)=0
       OR octet_length(NEW.ciphertext)>2097152
       OR jsonb_typeof(NEW.settings) IS DISTINCT FROM 'object'
       OR octet_length(NEW.settings::text)>2048 THEN
        RAISE EXCEPTION 'Invalid sealed setup credential shape' USING ERRCODE='23514';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO keys
        FROM jsonb_object_keys(NEW.settings) key;
    IF NEW.target='parishsoft' THEN
        IF keys IS DISTINCT FROM ARRAY['organization_id']
           OR jsonb_typeof(NEW.settings->'organization_id') <> 'number'
           OR (NEW.settings->>'organization_id') !~ '^[1-9][0-9]{0,9}$'
           OR (NEW.settings->>'organization_id')::numeric >= 2147483648 THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='slack' THEN
        IF keys IS DISTINCT FROM ARRAY['channel_id']
           OR jsonb_typeof(NEW.settings->'channel_id') <> 'string'
           OR (NEW.settings->>'channel_id') !~ '^[CG][A-Z0-9]{1,63}$' THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.target='google_workspace' THEN
        IF keys IS DISTINCT FROM
            ARRAY['delegated_email','recipient','reply_to','sender'] THEN
            RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
        END IF;
        FOR item IN SELECT value FROM jsonb_each(NEW.settings) LOOP
            IF jsonb_typeof(item) <> 'string' OR length(item#>>'{}')>254
               OR (item#>>'{}') <> lower(item#>>'{}')
               OR (item#>>'{}') ~ '[[:space:][:cntrl:]]'
               OR (item#>>'{}') !~ '^[^@]+@[^@]+$' THEN
                RAISE EXCEPTION 'Invalid provider validation context'
                    USING ERRCODE='23514';
            END IF;
        END LOOP;
    ELSE
        RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END $_$;

-- stewardship_source_drop_acceptance_guard_v1: 0080_drop_acceptance.sql's body without the sign-in age.
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
        RAISE EXCEPTION 'Accepting a large ParishSoft change requires fresh Administrator authentication' USING ERRCODE='42501';
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
BEGIN
    -- Each replaced function is its one overload, with the new body.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_automation_session_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%s.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''30 days''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_automation_session_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_backup_request_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_backup_request_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_debug_off_switch_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_debug_off_switch_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_delivery_control_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_delivery_control_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_family_mail_test_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%authenticated_at=NEW.reauthenticated_at%'
          AND p.prosrc LIKE '%last_activity_at>stamp-interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_family_mail_test_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_production_confirmation_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%event.action=''complete''%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_production_confirmation_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_production_withdrawal_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_production_withdrawal_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_publication_confirmation_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_publication_confirmation_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_restore_decision_admitted_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%login.authenticated_at=signed_in%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_restore_decision_admitted_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_sealed_intake_admission_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%Sealed intake requires installer consumers%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_sealed_intake_admission_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_secret_state_v2'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%Credential testing requires a live sealed request%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_secret_state_v2 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_setup_secret_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%login.last_activity_at>stamp-interval ''60 minutes''%'
          AND p.prosrc LIKE '%NEW.actor_id IS DISTINCT FROM attempt.owner_id%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_setup_secret_guard_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_source_drop_acceptance_guard_v1'
          AND p.prosrc LIKE '%#1547%'
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_v1(%'
          AND p.prosrc LIKE '%stewardship_automation_login command%'
          AND p.prosrc LIKE '%login.authenticated_at=NEW.authenticated_at%'
          AND p.prosrc LIKE '%interval ''60 minutes''%'
          AND p.prosrc NOT LIKE '%stewardship_automation_fresh_principal%'
          AND p.prosrc NOT LIKE '%authenticated_at BETWEEN%'
          AND p.prosrc NOT LIKE '%authenticated_at<statement_timestamp()-interval%'
          AND p.prosrc NOT LIKE '%reauthenticated_at<%'
          AND p.prosrc NOT LIKE '%authenticated_at>=stamp-interval%')<>1 THEN
        RAISE EXCEPTION 'Migration 0113: stewardship_source_drop_acceptance_guard_v1 is not installed as declared';
    END IF;
    -- Rights, search_path, language and grants are as before.
    IF current_setting('stewardship.migration_0113_attributes', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default')||'|'||p.prosecdef::text||'|'||coalesce(array_to_string(p.proconfig,','),'')||'|'||p.prolang::text, ';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN (
               'stewardship_automation_session_guard_v1',
               'stewardship_backup_request_guard_v1',
               'stewardship_debug_off_switch_guard_v1',
               'stewardship_delivery_control_guard_v1',
               'stewardship_family_mail_test_guard_v1',
               'stewardship_production_confirmation_guard_v1',
               'stewardship_production_withdrawal_guard_v1',
               'stewardship_publication_confirmation_guard_v1',
               'stewardship_restore_decision_admitted_v1',
               'stewardship_sealed_intake_admission_v1','stewardship_secret_state_v2',
               'stewardship_setup_secret_guard_v1',
               'stewardship_source_drop_acceptance_guard_v1')) THEN
        RAISE EXCEPTION 'Migration 0113: a replaced function changed its rights, configuration or grants';
    END IF;
    -- Every trigger function still guards a table.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prorettype='trigger'::regtype AND p.proname IN (
            'stewardship_automation_session_guard_v1',
            'stewardship_backup_request_guard_v1',
            'stewardship_debug_off_switch_guard_v1',
            'stewardship_delivery_control_guard_v1',
            'stewardship_family_mail_test_guard_v1',
            'stewardship_production_confirmation_guard_v1',
            'stewardship_production_withdrawal_guard_v1',
            'stewardship_publication_confirmation_guard_v1',
            'stewardship_restore_decision_admitted_v1',
            'stewardship_sealed_intake_admission_v1','stewardship_secret_state_v2',
            'stewardship_setup_secret_guard_v1',
            'stewardship_source_drop_acceptance_guard_v1')
          AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgfoid=p.oid AND t.tgenabled<>'D')) THEN
        RAISE EXCEPTION 'Migration 0113: a replaced guard no longer has its trigger';
    END IF;
END
$check$;
