-- Test fixture, frozen: the functions migration 0055 (setup writes users,
-- #993) replaces whose earlier bodies are in no frozen migration file, only
-- in the fresh-install baseline that 0055 updated. Copied byte for byte from
-- src/parishkit/stewardship/schema/functions.sql at c55a0b92d (the #952
-- slice 4 head this change is stacked on), as CREATE OR REPLACE. The other
-- functions 0055 replaces come from frozen files: the policy projection and
-- completeness triggers and the users-form guard from 0047, the refresh slot
-- function from 0014. test_setup_users_postgresql.py reinstalls these to
-- prove 0055's closing check refuses each earlier body;
-- test_schema_migration_files.py pins this file's digest.
SET LOCAL check_function_bodies = false;

CREATE OR REPLACE FUNCTION public.stewardship_audit_ownership_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    DECLARE
        configuration uuid;
        owner uuid;
    BEGIN
        IF left(NEW.event_type, 15) = 'config_request_' THEN
            SELECT base_id INTO configuration FROM stewardship_config_request
            WHERE id = NEW.subject_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Audit configuration request context is missing'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.event_type = 'configuration_activated' THEN
            SELECT configuration_id INTO configuration
            FROM stewardship_config_activation WHERE id = NEW.subject_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Audit activation context is missing'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            -- This is attribution, not authorization or a runtime readiness check.
            -- A concurrent activation may be visible on the next statement;
            -- every selected projection itself is immutable.
            SELECT active_configuration_id INTO configuration
            FROM stewardship_system_configuration;
        END IF;
        IF configuration IS NOT NULL THEN
            SELECT id INTO owner FROM stewardship_parish
            WHERE configuration_id = configuration;
            IF NOT FOUND AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_configuration_version v
            WHERE v.id=configuration AND v.validation_schema='bootstrap-policy-v1'
        ) THEN
                RAISE EXCEPTION 'Audit Parish projection is missing'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF NEW.ownership_scope IS NULL
           OR NEW.ownership_scope NOT IN ('deployment', 'parish')
           OR (NEW.parish_id IS NOT NULL
               AND NEW.parish_id IS DISTINCT FROM owner)
           OR (NEW.ownership_scope = 'parish' AND owner IS NULL) THEN
            RAISE EXCEPTION 'Invalid audit ownership attribution'
                USING ERRCODE = '23514';
        END IF;
        NEW.parish_id := owner;
        NEW.ownership_scope := CASE WHEN owner IS NULL THEN 'deployment'
                                   ELSE 'parish' END;
        RETURN NEW;
    END;
    $$;

CREATE OR REPLACE FUNCTION public.stewardship_bootstrap_empty_database() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE relation record; occupied boolean;
BEGIN
    IF NEW.validation_schema <> 'bootstrap-policy-v1'
       OR NEW.predecessor_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    -- Framework migration/permission metadata and the guarded initial download
    -- policy are seeded by migrations, not evidence of an existing campaign.
    -- All current and future application tables must otherwise be empty.
    FOR relation IN
        SELECT n.nspname, c.relname, c.relrowsecurity FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','f')
          AND NOT (n.nspname='public' AND c.relname IN (
              'django_migrations','django_content_type','auth_permission',
              'stewardship_download_policy'))
        ORDER BY n.nspname, c.relname
    LOOP
        IF relation.relrowsecurity AND NOT (relation.nspname='public'
            AND relation.relname IN ('stewardship_secret_request',
                'stewardship_sealed_credential_staging',
                'stewardship_credential_consumer_ack', 'stewardship_public_credential_handoff', 'stewardship_provider_context', 'stewardship_setup_sealed_credential', 'stewardship_setup_draft_section')) THEN
            RAISE EXCEPTION 'Initial bootstrap requires reviewed row-security admission'
                USING ERRCODE='23514';
        END IF;

        IF relation.nspname='public'
           AND relation.relname='stewardship_source_lease' THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_source_lease
                WHERE (singleton AND version=1 AND actor_id IS NULL
                    AND created_at=updated_at AND owner_id IS NULL
                    AND task_fence=0 AND worker_id IS NULL AND fence=0 AND phase='idle'
                    AND acquired_at IS NULL AND heartbeat_at IS NULL
                    AND expires_at IS NULL AND external_deadline IS NULL)
                    IS NOT TRUE) THEN
                RAISE EXCEPTION 'Initial bootstrap cannot adopt used source ownership'
                    USING ERRCODE='23514';
            END IF;
            CONTINUE;
        END IF;
        IF relation.nspname='public'
           AND relation.relname='stewardship_source_current' THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_source_current
                WHERE (singleton AND version=1 AND actor_id IS NULL
                    AND created_at=updated_at AND snapshot_id IS NULL
                    AND generation=0 AND organization_id IS NULL) IS NOT TRUE) THEN
                RAISE EXCEPTION 'Initial bootstrap cannot adopt a used source pointer'
                    USING ERRCODE='23514';
            END IF;
            CONTINUE;
        END IF;
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I)',
                       relation.nspname, relation.relname) INTO occupied;
        IF occupied THEN
            RAISE EXCEPTION 'Initial bootstrap requires an empty application database'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_bootstrap_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_configuration_version
               WHERE id=NEW.configuration_id
                 AND validation_schema='bootstrap-policy-v1') THEN
        RAISE EXCEPTION 'Bootstrap forbids unrelated projections' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_bootstrap_shape_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    sections jsonb;
    rules jsonb;
    item jsonb;
    vals jsonb;
BEGIN
    IF NEW.validation_schema <> 'bootstrap-policy-v1' THEN RETURN NEW; END IF;
    sections := NEW.canonical_document->'sections';
    rules := sections->'login_rules';
    IF jsonb_typeof(rules) IS DISTINCT FROM 'array'
       OR sections IS DISTINCT FROM jsonb_build_object('login_rules', rules) THEN
        RAISE EXCEPTION 'Invalid bootstrap configuration shape' USING ERRCODE='23514';
    END IF;
    IF jsonb_array_length(rules) < 1
       OR (NEW.predecessor_id IS NULL AND jsonb_array_length(rules) <> 1)
       OR (NEW.predecessor_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_configuration_version
           WHERE id=NEW.predecessor_id AND validation_schema='bootstrap-policy-v1'
       )) THEN
        RAISE EXCEPTION 'Invalid bootstrap configuration ancestry'
            USING ERRCODE='23514';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(rules) LOOP
        vals := item->'values';
        IF vals->>'kind' IS DISTINCT FROM 'address'
           OR vals->'roles' IS DISTINCT FROM '["administrator"]'::jsonb
           OR vals->>'creation_origin' IS DISTINCT FROM 'manual'
           OR jsonb_typeof(vals->'grants'->'administrator'->'manual')
                IS DISTINCT FROM 'string'
           OR vals->'grants' IS DISTINCT FROM jsonb_build_object(
                'administrator', jsonb_build_object(
                    'manual', vals->'grants'->'administrator'->'manual')) THEN
            RAISE EXCEPTION 'Bootstrap requires exact manual Admin grants'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_branding_bundle_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736230,1);
    IF TG_OP='INSERT' THEN
        NEW.created_at := clock_timestamp();
        NEW.updated_at := NEW.created_at;
        IF NEW.state<>'writing' OR NEW.version<>1
           OR NEW.actor_id IS DISTINCT FROM NEW.owner_id
           OR NEW.expires_at<=NEW.created_at
           OR NEW.expires_at>NEW.created_at+interval '24 hours' THEN
            RAISE EXCEPTION 'Invalid branding intake' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NOT ((OLD.state='writing' AND NEW.state IN('ready','cleanup_pending'))
        OR (OLD.state='ready' AND NEW.state='cleanup_pending')
        OR (OLD.state='cleanup_pending' AND NEW.state='scrubbed')) THEN
        RAISE EXCEPTION 'Invalid branding transition' USING ERRCODE='23514';
    END IF;
    -- A logo bundle holds exactly its four variants; a campaign artwork
    -- bundle (#248) holds exactly one banner or section image.
    IF NEW.state='ready' AND (NEW.expires_at<=clock_timestamp()
       OR NOT (SELECT (count(*)=4 AND bool_and(label IN('large','menu','icon','favicon')))
           OR (count(*)=1 AND bool_and(label IN('banner','section')))
           FROM public.stewardship_branding_asset WHERE bundle_id=NEW.id)) THEN
        RAISE EXCEPTION 'Branding bundle is incomplete or expired'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state IN('cleanup_pending','scrubbed') THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_parish p
            JOIN public.stewardship_branding_asset a ON
                a.id IN(p.large_logo_id,p.menu_logo_id,p.icon_logo_id,p.favicon_id)
            WHERE a.bundle_id=NEW.id AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_config_request q
                JOIN public.stewardship_setup_config_intent i ON i.request_id=q.id
                JOIN public.stewardship_setup_config_abort b ON b.intent_id=i.id
                CROSS JOIN LATERAL (SELECT state,failure_code FROM public.stewardship_config_checkpoint c
                    WHERE c.request_id=q.id ORDER BY sequence DESC LIMIT 1) receipt
                WHERE q.candidate_version_id=p.configuration_id AND q.request_schema='initial-setup-patch-v7'
                    AND receipt.state='failed' AND receipt.failure_code='invalid_candidate'
                    AND NOT EXISTS (SELECT 1 FROM public.stewardship_config_activation WHERE request_id=q.id))) THEN
            RAISE EXCEPTION 'Retained configuration pins branding'
                USING ERRCODE='23514';
        END IF;
        -- Campaign artwork (#248) named by any campaign configuration pins
        -- its image the same way.
        IF EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration c
            CROSS JOIN LATERAL jsonb_each_text(
                coalesce(c.values->'artwork'->'images','{}'::jsonb)) image
            JOIN public.stewardship_branding_asset a ON a.id::text=image.value
            WHERE a.bundle_id=NEW.id) THEN
            RAISE EXCEPTION 'Retained campaign configuration pins artwork'
                USING ERRCODE='23514';
        END IF;
        IF public.stewardship_branding_pending_v1(NEW.id) THEN
            RAISE EXCEPTION 'Pending configuration blocks branding cleanup'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_activation_owner_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF current_user='pk_stewardship_worker' OR EXISTS (
        SELECT 1 FROM public.stewardship_config_request
        WHERE id=NEW.request_id AND request_schema='initial-setup-patch-v7') THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_prepared prepared
            JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
            JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
            JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            WHERE prepared.id=public.stewardship_setup_completion_context_v1()
                AND prepared.configuration_id=NEW.configuration_id
                AND intent.request_id=NEW.request_id AND attempt.owner_id=NEW.actor_id
                AND attempt.base_id=NEW.predecessor_id
        ) THEN
            RAISE EXCEPTION 'Setup activation requires its exact atomic owner'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_attempt_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE stamp timestamptz:=clock_timestamp(); login stewardship_portal_session%ROWTYPE;
        task stewardship_task_run%ROWTYPE; selected uuid; live boolean; reason text;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup history cannot be deleted' USING ERRCODE='23514';
    END IF;

    IF TG_OP='UPDATE' AND NEW.state='completed' THEN
        IF current_user<>'pk_stewardship_worker' OR OLD.state<>'frozen'
            OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
            OR (to_jsonb(NEW)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','actor_id','correlation_id','version','updated_at'])
            OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
                WHERE intent.attempt_id=OLD.id AND intent.attempt_version=OLD.version
                    AND prepared.id=public.stewardship_setup_completion_context_v1()
            ) THEN
            RAISE EXCEPTION 'Setup completion requires its atomic finalization receipt'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_worker' THEN
        IF TG_OP<>'UPDATE' OR OLD.state<>'loading' OR NEW.state<>'collecting'
           OR NEW.actor_id IS DISTINCT FROM OLD.owner_id
           OR NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
           OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange
                    ON exchange.id=result.exchange_id AND exchange.attempt_id=OLD.id
                    AND exchange.scrubbed_at IS NULL
                JOIN public.stewardship_task_run finished
                    ON finished.id=exchange.task_id AND finished.state='succeeded'
                    AND finished.root_id=OLD.source_task_id
                    AND finished.fence=exchange.task_fence
                JOIN public.stewardship_source_snapshot snapshot
                    ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
                    AND snapshot.task_id=finished.id
                    AND snapshot.source_fence=exchange.source_fence
            ) THEN
            RAISE EXCEPTION 'Worker may only acknowledge validated setup staging'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF current_user='pk_stewardship_scheduler' AND
       (TG_OP<>'UPDATE' OR NEW.state<>'expired' OR NEW.actor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Scheduler may only expire setup metadata'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup mutation requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT active_configuration_id INTO selected FROM stewardship_system_configuration
        WHERE mode='testing' AND NOT restore_review_required;
    SELECT s.id,s.principal_id,s.revoked_at,s.expires_at,s.last_activity_at
        INTO login.id,login.principal_id,login.revoked_at,
             login.expires_at,login.last_activity_at
        FROM stewardship_portal_session s
        JOIN stewardship_portal_user u ON u.id=s.principal_id AND NOT u.disabled
        JOIN stewardship_address_rule rule ON rule.configuration_id=selected
            AND rule.email=u.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE s.id=NEW.session_id AND s.principal_id=NEW.owner_id;
    live=login.id IS NOT NULL AND login.revoked_at IS NULL
        AND login.expires_at>stamp AND login.last_activity_at>stamp-interval '60 minutes';
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'collecting' OR NEW.source_task_id IS NOT NULL
           OR NEW.renewed_at IS NOT NULL OR NEW.version<>1 OR NOT live
           OR NEW.actor_id IS DISTINCT FROM NEW.owner_id
           OR selected IS DISTINCT FROM NEW.base_id
           OR NOT EXISTS (SELECT 1 FROM stewardship_configuration_version
                WHERE id=selected AND validation_schema='bootstrap-policy-v1') THEN
            RAISE EXCEPTION 'Setup requires the original live bootstrap Admin session'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at=stamp; NEW.updated_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('expired','completed') OR NEW.state='completed' THEN
        RAISE EXCEPTION 'Setup completion requires its configured-marker owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.source_task_id IS NOT NULL THEN
        SELECT * INTO task FROM stewardship_task_run WHERE id=NEW.source_task_id;
        IF task.task_type IS DISTINCT FROM 'setup_source_load'
           OR task.domain_request_id IS DISTINCT FROM NEW.id
           OR task.initiated_by_id IS DISTINCT FROM NEW.owner_id
           OR task.created_at<NEW.created_at OR task.root_id<>task.id THEN
            RAISE EXCEPTION 'Setup source identity is not the original bound load'
                USING ERRCODE='23514';
        END IF;
    END IF;
    reason=CASE
        WHEN login.id IS NULL OR login.revoked_at IS NOT NULL THEN 'session'
        WHEN stamp>=login.expires_at THEN 'absolute'
        WHEN OLD.state='loading' AND stamp>=task.created_at+interval '2 hours' THEN 'watchdog'
        WHEN stamp>=login.last_activity_at+interval '60 minutes' THEN 'idle'
        ELSE NULL END;
    IF NEW.state='expired' THEN
        IF reason IS NULL THEN
            IF NOT live OR NEW.actor_id IS DISTINCT FROM NEW.owner_id THEN
                RAISE EXCEPTION 'Only the owning live Admin can cancel setup'
                    USING ERRCODE='23514';
            END IF;
            reason='cancelled';
        END IF;
        NEW.expiry_reason=reason; NEW.expired_at=stamp;
        IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at
           OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id THEN
            RAISE EXCEPTION 'Setup expiry cannot change its retained bindings'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF reason IS NOT NULL OR NOT live THEN
        RAISE EXCEPTION 'Expired setup cannot accept later work' USING ERRCODE='23514';
    END IF;
    IF OLD.state<>'frozen' AND (selected IS DISTINCT FROM NEW.base_id
        OR NEW.actor_id IS DISTINCT FROM NEW.owner_id) THEN
        RAISE EXCEPTION 'Setup no longer has its original authorized base'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='frozen' OR (
        (OLD.state=NEW.state AND NEW.state IN ('collecting','loading'))
        OR (OLD.state='collecting' AND NEW.state='loading' AND OLD.source_task_id IS NULL)
        OR (OLD.state='loading' AND NEW.state='collecting' AND task.state='succeeded')
        OR (OLD.state='collecting' AND NEW.state='frozen' AND task.state='succeeded')) IS NOT TRUE THEN
        RAISE EXCEPTION 'Setup transition has no completed owning work'
            USING ERRCODE='23514';
    END IF;
    IF NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
       AND NOT (OLD.state='collecting' AND NEW.state='loading') THEN
        RAISE EXCEPTION 'Setup load binding requires its loading transition'
            USING ERRCODE='23514';
    END IF;
    IF NEW.renewed_at IS DISTINCT FROM OLD.renewed_at THEN
        IF OLD.state<>'loading' OR NEW.state<>'loading' OR task.state<>'running'
           OR task.lease_expires_at<=stamp OR task.heartbeat_at IS NULL
           OR task.heartbeat_at<=stamp-interval '90 seconds'
           OR stamp<coalesce(OLD.renewed_at,task.created_at)+interval '5 minutes'
           OR stamp>=task.created_at+interval '2 hours'
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease lease
               WHERE lease.owner_id=task.id AND lease.task_fence=task.fence
                 AND lease.worker_id=task.worker_id AND lease.expires_at>stamp
                 AND lease.heartbeat_at>stamp-interval '90 seconds') THEN
            RAISE EXCEPTION 'Setup renewal requires live correlated source ownership'
                USING ERRCODE='23514';
        END IF;
        NEW.renewed_at=stamp;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_completion_context_v1() RETURNS uuid
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    SELECT prepared.id FROM public.stewardship_setup_prepared prepared
    JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
    JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
    JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
        AND attempt.state='frozen' AND attempt.version=intent.attempt_version
    JOIN public.stewardship_config_request request ON request.id=intent.request_id
        AND request.request_schema='initial-setup-patch-v7'
        AND request.candidate_version_id=prepared.configuration_id
        AND request.base_id=attempt.base_id AND request.actor_id=attempt.owner_id
    JOIN public.stewardship_system_configuration runtime
        ON runtime.active_configuration_id IN (attempt.base_id,prepared.configuration_id)
        AND runtime.mode='testing' AND NOT runtime.restore_review_required
        AND (runtime.current_campaign_id IS NULL OR runtime.current_campaign_id=attempt.id)
    JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
        AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
        AND login.expires_at>clock_timestamp()
        AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
    JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id AND NOT owner.disabled
    JOIN public.stewardship_address_rule rule ON rule.configuration_id=attempt.base_id
        AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
    JOIN public.stewardship_task_run task ON task.domain_request_id=prepared.id
        AND task.task_type='setup_finalize' AND task.initiated_by_id=attempt.owner_id
        AND task.state='running' AND task.lease_expires_at>clock_timestamp()
        AND task.created_at>=prepared.created_at
    JOIN public.stewardship_task_run root ON root.id=task.root_id
        AND root.task_type=task.task_type AND root.domain_request_id=prepared.id
        AND root.idempotency_key=prepared.id::text AND root.initiated_by_id=attempt.owner_id
    JOIN public.stewardship_source_lease lease ON lease.owner_id=task.id
        AND lease.task_fence=task.fence AND lease.worker_id=task.worker_id
        AND lease.phase='full' AND lease.expires_at>clock_timestamp()
    JOIN public.stewardship_source_snapshot snapshot ON snapshot.task_id=task.id
        AND snapshot.source_fence=lease.fence AND snapshot.state='promoted'
        AND snapshot.started_at>=prepared.created_at AND snapshot.base_id IS NULL
    JOIN public.stewardship_source_current current ON current.snapshot_id=snapshot.id
        AND current.generation=snapshot.generation AND current.organization_id=snapshot.organization_id
    WHERE current_user='pk_stewardship_worker'
        AND public.stewardship_setup_consumers_current_v1(prepared.id)
        AND NOT EXISTS (SELECT 1 FROM public.stewardship_setup_config_abort WHERE intent_id=intent.id)
        AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736212 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted);
$$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_config_intent_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE a public.stewardship_setup_attempt%ROWTYPE;
    q public.stewardship_config_request%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup intent requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO r FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO a FROM public.stewardship_setup_attempt WHERE id=NEW.attempt_id FOR UPDATE;
    SELECT * INTO q FROM public.stewardship_config_request WHERE id=NEW.request_id FOR UPDATE;
    IF a.id IS NULL OR q.id IS NULL OR a.state<>'frozen'
        OR a.version IS DISTINCT FROM NEW.attempt_version
        OR a.owner_id IS DISTINCT FROM NEW.actor_id OR a.owner_id IS DISTINCT FROM q.actor_id
        OR q.authority<>'admin' OR q.request_schema<>'initial-setup-patch-v7'
        OR q.base_id IS DISTINCT FROM a.base_id OR r.active_configuration_id IS DISTINCT FROM a.base_id
        OR r.mode<>'testing' OR r.restore_review_required OR r.current_campaign_id IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_configuration_version
            WHERE id=a.base_id AND validation_schema='bootstrap-policy-v1')
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=q.id ORDER BY sequence DESC LIMIT 1) IS DISTINCT FROM 'staged'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session s
            JOIN public.stewardship_portal_user u ON u.id=s.principal_id AND NOT u.disabled
            JOIN public.stewardship_address_rule p ON p.configuration_id=a.base_id
                AND p.email=u.email AND p.roles @> '["administrator"]'::jsonb
            WHERE s.id=a.session_id AND s.principal_id=a.owner_id AND s.revoked_at IS NULL
                AND s.expires_at>clock_timestamp()
                AND s.last_activity_at>clock_timestamp()-interval '60 minutes') THEN
        RAISE EXCEPTION 'Setup intent requires its frozen original Admin attempt'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_draft_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE attempt stewardship_setup_attempt%ROWTYPE; stamp timestamptz:=clock_timestamp();
        expected text[]; actual text[];
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup draft tombstones are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup drafts require ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM stewardship_setup_attempt WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND OLD.scrubbed_at IS NOT NULL THEN
        RAISE EXCEPTION 'Scrubbed setup cannot be repopulated' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF attempt.state NOT IN ('expired','completed') OR NEW.values<>'{}'::jsonb
           OR NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Setup scrubbing needs a terminal fence'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_scheduler' OR NEW.scrubbed_at IS NOT NULL
       OR attempt.state IS DISTINCT FROM 'collecting'
       OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration runtime
        JOIN stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '60 minutes'
        JOIN stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN stewardship_address_rule rule ON rule.email=owner.email
            AND rule.configuration_id=runtime.active_configuration_id
            AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.active_configuration_id=attempt.base_id) THEN
        RAISE EXCEPTION 'Setup draft requires its original live Admin'
            USING ERRCODE='23514';
    END IF;
    IF jsonb_typeof(NEW.values)<>'object' OR octet_length(NEW.values::text)>(CASE WHEN NEW.step IN ('campaign','schedules') OR left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' THEN 1048576 ELSE 65536 END) THEN
        RAISE EXCEPTION 'Invalid public setup shape' USING ERRCODE='23514';
    END IF;
    expected=CASE NEW.step
        WHEN 'parish' THEN ARRAY['name','online_giving_url','phone','timezone','website']
        WHEN 'branding' THEN ARRAY['bundle_id']
        WHEN 'access' THEN ARRAY['admin_addresses','ministry_addresses',
            'ministry_domains','staff_addresses','staff_domains']
        WHEN 'mail' THEN ARRAY['delegated_email','reply_to','sender','sender_name']
        WHEN 'slack' THEN ARRAY['channel_id','enabled']
        WHEN 'campaign' THEN ARRAY['campaign','source_result']
        WHEN 'testing' THEN ARRAY['testing_recipient']
        WHEN 'schedules' THEN ARRAY['records']
        ELSE ARRAY['id','values'] END;
    SELECT array_agg(key ORDER BY key) INTO actual
        FROM jsonb_object_keys(NEW.values) key;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Only declared public setup fields can be staged'
            USING ERRCODE='23514';
    END IF;

    IF NEW.step='campaign' AND (
        jsonb_typeof(NEW.values->'campaign') IS DISTINCT FROM 'object'
        OR jsonb_typeof(NEW.values->'source_result') IS DISTINCT FROM 'string'
        OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_source_result result
            JOIN public.stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
                AND exchange.scrubbed_at IS NULL
            JOIN public.stewardship_task_run task ON task.id=exchange.task_id
                AND task.state='succeeded' AND task.root_id=attempt.source_task_id
                AND task.fence=exchange.task_fence
            JOIN public.stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN public.stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
            WHERE result.id::text=NEW.values->>'source_result'
        )
    ) THEN
        RAISE EXCEPTION 'Setup campaign requires its validated original source result'
            USING ERRCODE='23514';
    END IF;

    IF NEW.step='schedules' OR left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' THEN
        IF NEW.step<>'schedules' AND NEW.values<>'{"id":null,"values":null}'::jsonb AND (
            NEW.values->'values'->>'campaign_id' IS DISTINCT FROM attempt.id::text
            OR NEW.values->'values'->>'kind' IS DISTINCT FROM split_part(NEW.step,'_',1)
            OR NEW.values->'values'->>'slot' IS DISTINCT FROM
                substring(NEW.step from position('_' in NEW.step)+1)
        ) THEN
            RAISE EXCEPTION 'Setup content belongs to its original campaign and slot'
                USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_draft_section draft
            JOIN public.stewardship_setup_source_result result
                ON result.id::text=draft.values->>'source_result'
            JOIN public.stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
                AND exchange.scrubbed_at IS NULL
            JOIN public.stewardship_task_run task ON task.id=exchange.task_id
                AND task.state='succeeded' AND task.root_id=attempt.source_task_id
                AND task.fence=exchange.task_fence
            JOIN public.stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN public.stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
            WHERE draft.attempt_id=attempt.id AND draft.step='campaign'
                AND draft.scrubbed_at IS NULL
        ) THEN
            RAISE EXCEPTION 'Setup content requires its validated staged campaign'
                USING ERRCODE='23514';
        END IF;
    END IF;

    IF NEW.step='schedules' THEN
        IF jsonb_typeof(NEW.values->'records') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Setup schedules require a bounded record inventory'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_array_length(NEW.values->'records')>100 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.values->'records') record
            WHERE record->'values'->>'campaign_id' IS DISTINCT FROM attempt.id::text
        ) THEN
            RAISE EXCEPTION 'Setup schedules belong to their original campaign'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_install_ready_live_v1(ready_id uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='frozen' AND attempt.version=intent.attempt_version
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base ON base.id=attempt.base_id
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.configuration_id=attempt.base_id
            AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE ready.id=$1
    );
$_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_mail_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        JOIN public.stewardship_task_run original ON original.id=attempt.source_task_id
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='google_workspace' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section mail
            ON mail.attempt_id=attempt.id AND mail.step='mail'
            AND mail.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        WHERE attempt.id=$1 AND attempt.version=$2 AND attempt.state='collecting'
            AND mail.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'sender',candidate.settings->>'sender',
                'reply_to',candidate.settings->>'reply_to',
                'delegated_email',candidate.settings->>'delegated_email'))),'hex')
            AND testing.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'testing_recipient',candidate.settings->>'recipient'))),'hex')
    );
$_$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_prepared_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE intent public.stewardship_setup_config_intent%ROWTYPE;
    request public.stewardship_config_request%ROWTYPE;
    candidate public.stewardship_configuration_version%ROWTYPE;
    readiness public.stewardship_setup_readiness_binding%ROWTYPE;
    integration jsonb; bound public.stewardship_setup_credential_install%ROWTYPE;
    installed public.stewardship_secret_request%ROWTYPE;
    target_count integer=0;
BEGIN
    IF current_user<>'pk_stewardship_config_installer'
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736212 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT public.stewardship_setup_install_ready_live_v1(NEW.readiness_id) THEN
        RAISE EXCEPTION 'Prepared setup requires its live configuration installer'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO readiness FROM public.stewardship_setup_readiness_binding WHERE id=NEW.readiness_id;
    SELECT * INTO intent FROM public.stewardship_setup_config_intent WHERE id=readiness.intent_id;
    SELECT * INTO request FROM public.stewardship_config_request WHERE id=intent.request_id;
    SELECT * INTO candidate FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF request.request_schema IS DISTINCT FROM 'initial-setup-patch-v7'
        OR NEW.actor_id IS DISTINCT FROM request.actor_id
        OR candidate.id IS DISTINCT FROM request.candidate_version_id
        OR candidate.digest IS DISTINCT FROM request.candidate_digest
        OR candidate.predecessor_id IS DISTINCT FROM request.base_id
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=request.id ORDER BY sequence DESC LIMIT 1) IS DISTINCT FROM 'yaml_activated'
        THEN
        RAISE EXCEPTION 'Prepared setup requires its exact selected candidate' USING ERRCODE='23514';
    END IF;
    FOR integration IN SELECT value->'values' FROM jsonb_array_elements(
        candidate.canonical_document->'sections'->'integrations')
        WHERE value->'values'->>'kind' IN ('parishsoft','google_workspace','slack')
    LOOP
        target_count=target_count+1;
        SELECT * INTO bound FROM public.stewardship_setup_credential_install
            WHERE readiness_id=NEW.readiness_id AND target=integration->>'kind';
        SELECT * INTO installed FROM public.stewardship_secret_request WHERE id=bound.request_id;
        IF bound.id IS NULL OR installed.id IS NULL
            OR bound.fingerprint IS DISTINCT FROM integration->>'credential_fingerprint'
            OR installed.resulting_fingerprint IS DISTINCT FROM bound.fingerprint
            OR installed.state IS DISTINCT FROM 'awaiting_ack'
            OR installed.expires_at<=clock_timestamp()
            OR installed.required_consumers IS DISTINCT FROM
                public.stewardship_credential_consumers_v1(bound.target)
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(installed.required_consumers) required(consumer_name)
                WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_credential_consumer_ack ack
                    WHERE ack.request_id=installed.id AND ack.consumer=required.consumer_name
                        AND ack.fingerprint=bound.fingerprint)) THEN
            RAISE EXCEPTION 'Prepared setup requires all exact initial consumer acknowledgements'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF target_count<>(CASE WHEN readiness.slack_delivery_id IS NULL THEN 2 ELSE 3 END)
        OR target_count<>(SELECT count(*) FROM public.stewardship_setup_credential_install
            WHERE readiness_id=NEW.readiness_id) THEN
        RAISE EXCEPTION 'Prepared setup has an incomplete credential set' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_readiness_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
    intent public.stewardship_setup_config_intent%ROWTYPE;
    request public.stewardship_config_request%ROWTYPE;
    slack_enabled boolean;
BEGIN
    IF current_user<>'pk_stewardship_web' OR NOT EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup readiness needs its ordered original web owner'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO intent FROM public.stewardship_setup_config_intent
        WHERE id=NEW.intent_id;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt
        WHERE id=intent.attempt_id;
    SELECT * INTO request FROM public.stewardship_config_request
        WHERE id=intent.request_id;
    IF attempt.state IS DISTINCT FROM 'frozen'
        OR attempt.version IS DISTINCT FROM intent.attempt_version
        OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.actor_id IS DISTINCT FROM attempt.owner_id
        OR request.request_schema IS DISTINCT FROM 'initial-setup-patch-v7'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration runtime
            WHERE runtime.active_configuration_id=attempt.base_id
                AND runtime.mode='testing' AND NOT runtime.restore_review_required
                AND runtime.current_campaign_id IS NULL)
        OR (SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=request.id ORDER BY sequence DESC LIMIT 1)
                IS DISTINCT FROM 'staged'
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session login
            JOIN public.stewardship_portal_user owner
                ON owner.id=login.principal_id AND NOT owner.disabled
            JOIN public.stewardship_address_rule rule
                ON rule.configuration_id=attempt.base_id AND rule.email=owner.email
                AND rule.roles @> '["administrator"]'::jsonb
            WHERE login.id=attempt.session_id AND login.principal_id=attempt.owner_id
                AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
                AND login.last_activity_at>clock_timestamp()-interval '60 minutes')
        THEN
        RAISE EXCEPTION 'Setup readiness requires a live frozen original request'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_source_result result
        JOIN public.stewardship_setup_source_exchange exchange
            ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
            AND exchange.scrubbed_at IS NULL
        JOIN public.stewardship_task_run task ON task.id=exchange.task_id
            AND task.root_id=attempt.source_task_id AND task.state='succeeded'
            AND task.fence=exchange.task_fence
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=exchange.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=exchange.credential_version
            AND credential.fingerprint=exchange.fingerprint
        JOIN public.stewardship_source_snapshot snapshot
            ON snapshot.id=result.snapshot_id
            AND snapshot.state='ready' AND snapshot.task_id=task.id
            AND snapshot.source_fence=exchange.source_fence
        WHERE result.id=NEW.source_result_id) THEN
        RAISE EXCEPTION 'Setup readiness source is not the exact reviewed catalog'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=delivery.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=delivery.credential_version
            AND credential.fingerprint=delivery.fingerprint
        JOIN public.stewardship_setup_draft_section testing
            ON testing.attempt_id=attempt.id AND testing.step='testing'
            AND testing.scrubbed_at IS NULL
        WHERE delivery.id=NEW.mail_delivery_id AND delivery.attempt_id=attempt.id
            AND delivery.attempt_version=intent.attempt_version-1
            AND delivery.candidate_digest=request.candidate_digest
            AND delivery.state='accepted'
            AND NEW.testing_recipient=testing.values->>'testing_recipient'
            AND NEW.testing_recipient=credential.settings->>'recipient') THEN
        RAISE EXCEPTION 'Setup readiness requires exact accepted Testing mail'
            USING ERRCODE='23514';
    END IF;
    SELECT (values->>'enabled')::boolean INTO slack_enabled
        FROM public.stewardship_setup_draft_section
        WHERE attempt_id=attempt.id AND step='slack' AND scrubbed_at IS NULL;
    IF slack_enabled IS NULL OR (slack_enabled AND NEW.slack_delivery_id IS NULL)
       OR (NOT slack_enabled AND NEW.slack_delivery_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Setup Slack readiness differs from reviewed settings'
            USING ERRCODE='23514';
    END IF;
    IF slack_enabled AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_slack_delivery delivery
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=delivery.credential_id AND credential.scrubbed_at IS NULL
            AND credential.version=delivery.credential_version
            AND credential.fingerprint=delivery.fingerprint
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL
        WHERE delivery.id=NEW.slack_delivery_id AND delivery.attempt_id=attempt.id
            AND delivery.attempt_version=intent.attempt_version-1
            AND delivery.candidate_digest=request.candidate_digest
            AND delivery.state='accepted'
            AND credential.settings->>'channel_id'=slack.values->>'channel_id') THEN
        RAISE EXCEPTION 'Setup readiness requires the exact accepted Slack test'
            USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_mail_delivery
        WHERE attempt_id=attempt.id AND state IN ('queued','submitting'))
       OR EXISTS (SELECT 1 FROM public.stewardship_setup_slack_delivery
        WHERE attempt_id=attempt.id AND state IN ('queued','submitting')) THEN
        RAISE EXCEPTION 'Setup tests must finish before confirmation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_request_binding_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.request_schema='initial-setup-patch-v7' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_config_intent WHERE request_id=NEW.id) THEN
        RAISE EXCEPTION 'Setup request requires its atomic original-attempt binding'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

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
        IF attempt.state NOT IN ('expired','completed')
           OR NEW.ciphertext IS NOT NULL OR NEW.actor_id IS NOT NULL
           OR NEW.settings<>'{}'::jsonb
           OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint THEN
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
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>stamp
            AND login.last_activity_at>stamp-interval '60 minutes'
            AND login.authenticated_at>stamp-interval '5 minutes'
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

CREATE OR REPLACE FUNCTION public.stewardship_setup_slack_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_configuration_version base
            ON base.id=runtime.active_configuration_id
            AND base.validation_schema='bootstrap-policy-v1'
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=base.id AND rule.email=owner.email
            AND rule.roles @> '["administrator"]'::jsonb
        JOIN public.stewardship_task_run original ON original.id=attempt.source_task_id
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.id=$3 AND candidate.attempt_id=attempt.id
            AND candidate.version=$4 AND candidate.fingerprint=$5
            AND candidate.target='slack' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_setup_draft_section slack
            ON slack.attempt_id=attempt.id AND slack.step='slack'
            AND slack.scrubbed_at IS NULL
        WHERE attempt.id=$1 AND attempt.version=$2 AND attempt.state='collecting'
            AND slack.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'enabled',true,'channel_id',candidate.settings->>'channel_id'))),'hex')
    );
$_$;
