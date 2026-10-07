-- Frozen forward migration file 0013 (the repository-wide file sequence;
-- Django's stewardship_accounts.0005): fresh-gated Admin actions accept a
-- live, full-scope Admin automation session in place of a Google sign-in
-- within the last five minutes (ADM-11 PR 5, #463). This file is installed by
-- accounts/migrations/0005_automation_fresh_guards.py and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that its copies of the replaced functions still equal the
-- fresh-install baseline's (delivery_control.sql, family_mail_tests.sql,
-- production_confirmation.sql, production_withdrawal.sql and functions.sql).
-- A fresh install runs the baseline, 0002 to 0012 and then this; the baseline
-- files already carry the replaced bodies, so the install ends in the same
-- catalog as an upgraded database.
--
-- The four session-bound guards (delivery controls, chosen-Family tests, the
-- Production confirmation and pre-start withdrawal) admit a row only for a
-- live Admin session whose sign-in instant the row records. Their
-- five-minute clause becomes "within five minutes, or the session is a
-- command session of a live full-scope automation session of the actor"
-- (stewardship_automation_fresh_v1, from 0004); every other clause stays.
-- The Production confirmation's sign-in-after-cleanup clause is met the same
-- way, while cleanup must still be complete. A browser session has no
-- automation link, so for it every guard is exactly as before.
--
-- The two secret request guards check the sign-in instant recorded on the
-- request, not a session row. Each also admits an instant for which
-- stewardship_automation_fresh_principal_v1 (new here, migration-owned) is
-- true: a live full-scope automation session of that principal has exactly
-- that sign-in instant. The credential installers read no automation table,
-- so it has definer rights; EXECUTE comes from the database-grants step
-- (runtime_functions: web and the three credential installers), never from
-- PUBLIC. Each guard calls it in its own statement, reached only on the path
-- that needs it, because PostgreSQL checks EXECUTE when an expression is
-- initialized. The setup-install branch and every other rule stay.
--
-- CREATE OR REPLACE keeps each function's privileges; the header of every
-- copy repeats its rights (SECURITY DEFINER where the baseline has it) and
-- search_path. The DO block at the end refuses to commit unless all of it is
-- installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- True only when a live, full-scope automation session of `principal` has
-- exactly the sign-in instant `instant`; false (never NULL) otherwise.
CREATE FUNCTION public.stewardship_automation_fresh_principal_v1(principal uuid, instant timestamptz) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM public.stewardship_automation_session a
        WHERE a.principal_id=principal AND a.scope='full'
          AND a.authenticated_at=instant
          AND public.stewardship_automation_live_v1(a.id))
$$;
REVOKE ALL ON FUNCTION public.stewardship_automation_fresh_principal_v1(uuid, timestamptz) FROM PUBLIC;

-- stewardship_delivery_control_guard_v1, from delivery_control.sql.
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
              AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp()
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

-- stewardship_family_mail_test_guard_v1, from family_mail_tests.sql.
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
           -- The Admin signed in with Google again within the last five minutes,
           -- or runs a command through a live full-scope automation session.
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session
                WHERE principal_id=NEW.requested_by_id AND revoked_at IS NULL
                    AND authenticated_at=NEW.reauthenticated_at AND expires_at>stamp
                    AND (authenticated_at BETWEEN stamp-interval '5 minutes' AND stamp
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

-- stewardship_production_confirmation_guard_v1, from production_confirmation.sql.
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
              AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp()
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)))
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_production_event event
            WHERE event.request_id=request.id AND event.action='complete'
              AND (event.created_at<=NEW.authenticated_at
                   OR public.stewardship_automation_fresh_v1(NEW.session_id,NEW.actor_id))) THEN
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

-- stewardship_production_withdrawal_guard_v1, from production_withdrawal.sql.
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
              AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp()
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

-- stewardship_sealed_intake_admission_v1, from functions.sql.
CREATE OR REPLACE FUNCTION public.stewardship_sealed_intake_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE setup_install boolean := false; automation boolean := false;
BEGIN
    IF TG_TABLE_NAME='stewardship_secret_request' THEN
        IF NEW.required_consumers<>'[]'::jsonb
           AND NEW.reauthenticated_at<statement_timestamp()-interval '5 minutes' THEN
            -- An initial setup install is inserted by its target installer when
            -- setup finishes, long after the setup session's sign-in; it relies
            -- on the live frozen setup instead (as the staged->testing guard
            -- does). Its request id is the owner's sealed setup credential id,
            -- and the install binding does not exist yet. The setup query is a
            -- separate statement, run only for the installer: privileges are
            -- checked for every table a statement names, even in an unused
            -- branch, and other roles cannot read these setup tables.
            IF NEW.target IN ('parishsoft','google_workspace','slack')
               AND current_user='pk_stewardship_credential_'||NEW.target THEN
                setup_install := EXISTS (
                    SELECT 1 FROM public.stewardship_setup_sealed_credential credential
                    JOIN public.stewardship_setup_attempt attempt
                        ON attempt.id=credential.attempt_id
                        AND attempt.owner_id=NEW.requested_by_id
                    JOIN public.stewardship_setup_config_intent intent
                        ON intent.attempt_id=credential.attempt_id
                    JOIN public.stewardship_setup_readiness_binding ready
                        ON ready.intent_id=intent.id
                    WHERE credential.id=NEW.id AND credential.target=NEW.target
                        AND credential.scrubbed_at IS NULL
                        AND public.stewardship_setup_install_ready_live_v1(ready.id));
            ELSIF current_user='pk_stewardship_web' OR pg_has_role(current_user,
                    (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
                -- A request made through a live full-scope automation session
                -- records that session's sign-in instant (ADM-11). A separate
                -- statement, reached only by the web (which holds EXECUTE) or
                -- the schema owner: EXECUTE is checked whenever an expression
                -- is initialized, so any other role keeps the plain refusal.
                automation := coalesce(public.stewardship_automation_fresh_principal_v1(
                    NEW.requested_by_id,NEW.reauthenticated_at),false);
            END IF;
            IF NOT setup_install AND NOT automation THEN
                RAISE EXCEPTION 'Sealed intake requires fresh authentication'
                    USING ERRCODE='23514';
            END IF;
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

-- stewardship_secret_state_v2, from functions.sql.
CREATE OR REPLACE FUNCTION public.stewardship_secret_state_v2() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE fresh boolean := true;
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
        IF NEW.reauthenticated_at<NEW.created_at-interval '5 minutes' AND NOT (CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN public.stewardship_setup_install_live_v1(NEW.id) ELSE false END) THEN
            fresh := false;
            IF NEW.target IN ('parishsoft','google_workspace','slack') THEN
                -- A request made through a full-scope automation session
                -- records its sign-in instant; the session must still be live
                -- (ADM-11). Only these targets' installers hold EXECUTE on the
                -- check, so it is its own statement, reached only for them.
                fresh := coalesce(public.stewardship_automation_fresh_principal_v1(
                    NEW.requested_by_id,NEW.reauthenticated_at),false);
            END IF;
        END IF;
        IF NEW.actor_id IS NOT NULL OR NEW.expires_at<=statement_timestamp()
           OR NOT fresh
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

DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_automation_fresh_principal_v1'
                     AND p.prosecdef AND NOT p.proisstrict AND p.provolatile='s'
                     AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND pg_get_function_identity_arguments(p.oid)='principal uuid, instant timestamp with time zone'
                     AND NOT has_function_privilege('public', p.oid, 'EXECUTE')) THEN
        RAISE EXCEPTION 'stewardship_automation_fresh_principal_v1 is not installed with definer rights and no PUBLIC EXECUTE';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.proname IN ('stewardship_delivery_control_guard_v1',
              'stewardship_production_confirmation_guard_v1',
              'stewardship_production_withdrawal_guard_v1')
          AND p.prosrc LIKE '%OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))%')<>3 THEN
        RAISE EXCEPTION 'A session-bound guard was not replaced with its definer rights and automation clause';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_production_confirmation_guard_v1'
                     AND p.prosrc LIKE '%OR public.stewardship_automation_fresh_v1(NEW.session_id,NEW.actor_id)))%') THEN
        RAISE EXCEPTION 'The Production confirmation guard lacks its post-cleanup automation clause';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   WHERE n.nspname='public' AND p.proname='stewardship_family_mail_test_guard_v1'
                     AND NOT p.prosecdef
                     AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
                     AND p.prosrc LIKE '%OR public.stewardship_automation_fresh_v1(id,NEW.requested_by_id))%') THEN
        RAISE EXCEPTION 'The Family test guard was not replaced with its automation clause';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.proname IN ('stewardship_sealed_intake_admission_v1','stewardship_secret_state_v2')
          AND p.prosrc LIKE '%public.stewardship_automation_fresh_principal_v1(%')<>2 THEN
        RAISE EXCEPTION 'A secret request guard was not replaced with its automation clause';
    END IF;
END
$check$;
