-- Initial unreleased stewardship schema. See docs/guides/stewardship-schema.md.

-- FUNCTION: stewardship_aborted_activation_v1()
CREATE FUNCTION public.stewardship_aborted_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_campaign_config_abort b JOIN stewardship_campaign_config_intent i ON i.id=b.intent_id
              WHERE i.request_id=NEW.request_id) THEN
        RAISE EXCEPTION 'Aborted exceptional candidate cannot be activated' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_activation_catchup_mutable_v1()
CREATE FUNCTION public.stewardship_activation_catchup_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."activation_id" IS DISTINCT FROM OLD."activation_id" OR NEW."cutoff" IS DISTINCT FROM OLD."cutoff" OR NEW."configuration_id" IS DISTINCT FROM OLD."configuration_id" OR (OLD."source_snapshot_id" IS NOT NULL AND NEW."source_snapshot_id" IS DISTINCT FROM OLD."source_snapshot_id") OR (OLD."task_root_id" IS NOT NULL AND NEW."task_root_id" IS DISTINCT FROM OLD."task_root_id") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_activation_effects_v1()
CREATE FUNCTION public.stewardship_activation_effects_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    BEGIN
        UPDATE stewardship_system_configuration
        SET active_configuration_id = NEW.configuration_id,
            testing_recipient = coalesce((SELECT ready.testing_recipient
                FROM public.stewardship_setup_prepared prepared
                JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
                WHERE prepared.configuration_id=NEW.configuration_id),testing_recipient),
            version = version + 1, configuration_sequence = configuration_sequence + 1, actor_id = NEW.actor_id,
            correlation_id = NEW.correlation_id;
        IF NEW.request_id IS NOT NULL THEN
            INSERT INTO stewardship_config_checkpoint
                (id, created_at, actor_id, correlation_id, request_id,
                 sequence, state)
            SELECT gen_random_uuid(), NEW.created_at, NEW.actor_id,
                   NEW.correlation_id, NEW.request_id, MAX(sequence) + 1, 'applied'
            FROM stewardship_config_checkpoint WHERE request_id = NEW.request_id;
        END IF;
        INSERT INTO stewardship_audit_event
            (id, created_at, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.created_at, NEW.actor_id,
                NEW.correlation_id, 'configuration_activated', NEW.id);
        RETURN NEW;
    END;
    $$;

-- FUNCTION: stewardship_activation_global_v1()
CREATE FUNCTION public.stewardship_activation_global_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    PERFORM pg_advisory_xact_lock(736220,1); RETURN NEW;
END $$;

-- FUNCTION: stewardship_activation_guard_v1()
CREATE FUNCTION public.stewardship_activation_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    DECLARE
        runtime stewardship_system_configuration%ROWTYPE;
        candidate stewardship_configuration_version%ROWTYPE;
        intent stewardship_config_request%ROWTYPE;
        checkpoint stewardship_config_checkpoint%ROWTYPE;
    BEGIN
        SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
        IF NOT FOUND OR NEW.sequence <> runtime.configuration_sequence
           OR NEW.predecessor_id IS DISTINCT FROM runtime.active_configuration_id
           OR NEW.created_at < runtime.created_at THEN
            RAISE EXCEPTION 'Activation requires current runtime predecessor'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO candidate FROM stewardship_configuration_version
        WHERE id = NEW.configuration_id;
        IF NOT FOUND OR candidate.predecessor_id IS DISTINCT FROM NEW.predecessor_id
           OR NEW.created_at < candidate.created_at THEN
            RAISE EXCEPTION 'Activation requires matching prepared predecessor'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.request_id IS NOT NULL THEN
            SELECT * INTO intent FROM stewardship_config_request
            WHERE id = NEW.request_id FOR UPDATE;
            IF NOT FOUND OR intent.candidate_version_id <> NEW.configuration_id
               OR intent.candidate_digest <> candidate.digest
               OR intent.base_id <> NEW.predecessor_id
               OR intent.actor_id IS DISTINCT FROM NEW.actor_id THEN
                RAISE EXCEPTION 'Activation request does not match candidate'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO checkpoint FROM stewardship_config_checkpoint
            WHERE request_id = NEW.request_id ORDER BY sequence DESC LIMIT 1;
            IF NOT FOUND OR checkpoint.state <> 'yaml_activated'
               OR NEW.created_at < checkpoint.created_at THEN
                RAISE EXCEPTION 'Activation requires YAML checkpoint'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;

-- FUNCTION: stewardship_address_grant_immutable_v1()
CREATE FUNCTION public.stewardship_address_grant_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_address_rule_immutable_v1()
CREATE FUNCTION public.stewardship_address_rule_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_admin_revocation_immutable_v1()
CREATE FUNCTION public.stewardship_admin_revocation_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_applied_integration_immutable_v1()
CREATE FUNCTION public.stewardship_applied_integration_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_assignment_overlay_mutable_v1()
CREATE FUNCTION public.stewardship_assignment_overlay_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."assignment_record_id" IS DISTINCT FROM OLD."assignment_record_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_audit_context_immutable_v1()
CREATE FUNCTION public.stewardship_audit_context_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_audit_event_immutable_v1()
CREATE FUNCTION public.stewardship_audit_event_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_audit_ownership_v1()
CREATE FUNCTION public.stewardship_audit_ownership_v1() RETURNS trigger
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

-- FUNCTION: stewardship_auth_incident_mutable_v1()
CREATE FUNCTION public.stewardship_auth_incident_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."kind" IS DISTINCT FROM OLD."kind" OR NEW."window" IS DISTINCT FROM OLD."window" OR NEW."level" IS DISTINCT FROM OLD."level" OR NEW."attempts" IS DISTINCT FROM OLD."attempts" OR NEW."sources" IS DISTINCT FROM OLD."sources" OR NEW."identities" IS DISTINCT FROM OLD."identities" OR NEW."candidates" IS DISTINCT FROM OLD."candidates" OR (OLD."resolved_at" IS NOT NULL AND NEW."resolved_at" IS DISTINCT FROM OLD."resolved_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_bootstrap_empty_database()
CREATE FUNCTION public.stewardship_bootstrap_empty_database() RETURNS trigger
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

-- FUNCTION: stewardship_bootstrap_projection_v1()
CREATE FUNCTION public.stewardship_bootstrap_projection_v1() RETURNS trigger
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

-- FUNCTION: stewardship_bootstrap_shape_v1()
CREATE FUNCTION public.stewardship_bootstrap_shape_v1() RETURNS trigger
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

-- FUNCTION: stewardship_boundary_audit_v1()
CREATE FUNCTION public.stewardship_boundary_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid := gen_random_uuid(); before_state text; after_state text;
BEGIN
    IF NEW.state IN ('succeeded','skipped') THEN
        INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
        VALUES(event_id,NEW.actor_id,NEW.correlation_id,
            CASE NEW.state WHEN 'skipped' THEN 'campaign_boundary_skipped' ELSE 'campaign_boundary_completed' END,
            NEW.id,NEW.campaign_id);
        IF NEW.transition_id IS NOT NULL THEN
            SELECT t.before_state,t.after_state INTO before_state,after_state
                FROM stewardship_campaign_transition t WHERE t.id=NEW.transition_id;
        ELSE
            SELECT c.state,c.state INTO before_state,after_state
                FROM stewardship_campaign c WHERE c.id=NEW.campaign_id;
        END IF;
        INSERT INTO stewardship_audit_context(id,actor_id,correlation_id,event_id,actor_kind,schema,context)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,event_id,
            CASE NEW.reason WHEN 'boundary_replaced' THEN 'portal_user' ELSE 'system' END,
            'boundary',jsonb_build_object(
                'occurrence_id',NEW.id,'kind',NEW.kind,
                'intended_unix_microseconds',(extract(epoch FROM NEW.due_at)*1000000)::bigint,
                'actual_unix_microseconds',(extract(epoch FROM NEW.completed_at)*1000000)::bigint,
                'lag_microseconds',greatest(0,(extract(epoch FROM NEW.completed_at-NEW.due_at)*1000000)::bigint),
                'before_state',before_state,'after_state',after_state));
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_boundary_guard_v1()
CREATE FUNCTION public.stewardship_boundary_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
    p stewardship_campaign_configuration%ROWTYPE; due timestamptz;
    previous stewardship_campaign_boundary%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Boundary history cannot be deleted' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    due:=CASE WHEN NEW.kind='start' THEN p.starts_at ELSE p.ends_at END;
    IF TG_OP='INSERT' THEN
        SELECT * INTO previous FROM stewardship_campaign_boundary
            WHERE campaign_id=NEW.campaign_id AND kind=NEW.kind
            ORDER BY execution_revision DESC LIMIT 1;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.due_at<>due
           OR NEW.execution_revision<>coalesce(previous.execution_revision,0)+1
           OR previous.state='pending'
           OR (previous.due_at=NEW.due_at AND previous.reason<>'boundary_replaced')
           OR NEW.task_id IS NOT NULL OR NEW.task_fence IS NOT NULL OR NEW.reason<>''
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
            RAISE EXCEPTION 'Invalid boundary allocation' USING ERRCODE='23514'; END IF;
    ELSE
        IF OLD.state<>'pending' THEN RAISE EXCEPTION 'Boundary result is immutable' USING ERRCODE='23514'; END IF;
        IF NEW.state='succeeded' AND NOT EXISTS(
            SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.transition_id
            AND t.boundary_id=NEW.id AND t.campaign_id=NEW.campaign_id AND t.action=NEW.kind
            AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id
        ) THEN RAISE EXCEPTION 'Boundary success requires exact transition' USING ERRCODE='23514'; END IF;
        -- Scheduled close remains an obligation: its worker must apply Start
        -- first, not erase Close because that prerequisite has not run yet.
        IF NEW.state='skipped' AND (NEW.reason NOT IN ('not_applicable','boundary_replaced') OR (
            NEW.due_at=due AND c.id=r.current_campaign_id AND r.mode='production'
            AND ((NEW.kind='start' AND c.state='scheduled') OR (NEW.kind='close' AND c.state IN ('scheduled','active')))
        )) THEN RAISE EXCEPTION 'Applicable boundary cannot be skipped' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND NEW.reason='not_applicable' AND NOT EXISTS(
            SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id AND t.task_type='campaign_boundary'
            AND t.domain_request_id=c.id AND t.state='running' AND t.worker_id=NEW.actor_id
            AND t.fence=NEW.task_fence AND t.lease_expires_at>clock_timestamp()
        ) THEN RAISE EXCEPTION 'Boundary skip requires current fenced worker' USING ERRCODE='23514'; END IF;
        IF (NEW.task_id,NEW.task_fence) IS DISTINCT FROM (OLD.task_id,OLD.task_fence) THEN
            IF NEW.state<>'pending' OR NOT EXISTS(
                SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id AND t.task_type='campaign_boundary'
                AND t.domain_request_id=c.id AND t.state='running' AND t.worker_id=NEW.actor_id
                AND t.fence=NEW.task_fence AND t.lease_expires_at>clock_timestamp()
            ) OR EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=OLD.task_id AND fence=OLD.task_fence AND state='running' AND lease_expires_at>clock_timestamp()) THEN
                RAISE EXCEPTION 'Boundary task binding requires current worker ownership' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_branding_asset_immutable_v1()
CREATE FUNCTION public.stewardship_branding_asset_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_branding_asset_insert_v1()
CREATE FUNCTION public.stewardship_branding_asset_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE selected public.stewardship_branding_bundle%ROWTYPE;
        maximum integer;
BEGIN
    PERFORM pg_advisory_xact_lock(736230,1);
    SELECT * INTO selected FROM public.stewardship_branding_bundle
        WHERE id=NEW.bundle_id;
    maximum := CASE NEW.label
        WHEN 'large' THEN 1024 WHEN 'favicon' THEN 32 ELSE 128 END;
    IF NOT FOUND OR selected.state<>'writing'
       OR selected.expires_at<=clock_timestamp()
       OR NEW.actor_id IS DISTINCT FROM selected.owner_id
       OR NEW.width>maximum OR NEW.height>maximum THEN
        RAISE EXCEPTION 'Invalid branding asset receipt' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_branding_bundle_guard_v1()
CREATE FUNCTION public.stewardship_branding_bundle_guard_v1() RETURNS trigger
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
    IF NEW.state='ready' AND (NEW.expires_at<=clock_timestamp()
       OR (SELECT count(*) FROM public.stewardship_branding_asset
           WHERE bundle_id=NEW.id)<>4) THEN
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
        IF public.stewardship_branding_pending_v1(NEW.id) THEN
            RAISE EXCEPTION 'Pending configuration blocks branding cleanup'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_branding_bundle_mutable_v1()
CREATE FUNCTION public.stewardship_branding_bundle_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."owner_id" IS DISTINCT FROM OLD."owner_id" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."setup_attempt_id" IS DISTINCT FROM OLD."setup_attempt_id" OR NEW."base_id" IS DISTINCT FROM OLD."base_id" OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_branding_pending_v1(uuid)
CREATE FUNCTION public.stewardship_branding_pending_v1(identifier uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_branding_asset a
        JOIN public.stewardship_config_request r ON r.patch @>
            jsonb_build_array(jsonb_build_object('section','parish','values',
                jsonb_build_object('branding',jsonb_build_object(a.label,a.id::text))))
        WHERE a.bundle_id=identifier AND coalesce((
            SELECT c.state FROM public.stewardship_config_checkpoint c
            WHERE c.request_id=r.id ORDER BY c.sequence DESC LIMIT 1
        ),'pending') NOT IN ('applied','failed','cancelled')
    );
$$;

-- FUNCTION: stewardship_campaign_activate_v1()
CREATE FUNCTION public.stewardship_campaign_activate_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE projection uuid; event_name text;
BEGIN
    IF NEW.current_campaign_id IS NULL OR NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    SELECT id INTO projection FROM public.stewardship_campaign_configuration
    WHERE configuration_id = NEW.active_configuration_id AND record_id = NEW.current_campaign_id;
    event_name := 'campaign_configured';
    IF EXISTS (
        SELECT 1 FROM public.stewardship_configuration_version old_version,
                      public.stewardship_configuration_version new_version
        WHERE old_version.id = OLD.active_configuration_id
          AND new_version.id = NEW.active_configuration_id
          AND old_version.canonical_document->'sections'->'campaigns'
              IS NOT DISTINCT FROM new_version.canonical_document->'sections'->'campaigns'
          AND old_version.canonical_document->'sections'->'schedules'
              IS NOT DISTINCT FROM new_version.canonical_document->'sections'->'schedules'
    ) THEN event_name := 'campaign_reprojected'; END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_campaign WHERE id = NEW.current_campaign_id) THEN
        UPDATE public.stewardship_campaign SET active_configuration_id = projection,
            version = version + 1, actor_id = NEW.actor_id, correlation_id = NEW.correlation_id
        WHERE id = NEW.current_campaign_id;
    ELSE
        INSERT INTO public.stewardship_campaign
            (id, state, version, active_configuration_id, actor_id, correlation_id)
        VALUES (NEW.current_campaign_id, 'draft', 1, projection, NEW.actor_id, NEW.correlation_id);
    END IF;
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id, campaign_reference)
    VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id, event_name,
            NEW.current_campaign_id, NEW.current_campaign_id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_boundary_mutable_v1()
CREATE FUNCTION public.stewardship_campaign_boundary_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."kind" IS DISTINCT FROM OLD."kind" OR NEW."due_at" IS DISTINCT FROM OLD."due_at" OR NEW."execution_revision" IS DISTINCT FROM OLD."execution_revision" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_campaign_complete_v1()
CREATE FUNCTION public.stewardship_campaign_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.validation_schema NOT IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8') THEN RETURN NULL; END IF;
    IF (SELECT count(*) FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'campaigns'), 0)
       OR (SELECT count(*) FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'schedules'), 0) THEN
        RAISE EXCEPTION 'Campaign projections are incomplete' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT campaign_id FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind IN ('initial', 'reminder')
        GROUP BY campaign_id
        HAVING count(*) FILTER (WHERE kind = 'initial') <> 1
           OR count(DISTINCT due_at) <> count(*)
           OR min(due_at) FILTER (WHERE kind = 'reminder') <=
              min(due_at) FILTER (WHERE kind = 'initial')
    ) OR EXISTS (
        SELECT campaign_id, kind FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind IN ('daily_digest', 'weekly_digest')
        GROUP BY campaign_id, kind HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Invalid schedule relationships' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_campaign_config_abort_immutable_v1()
CREATE FUNCTION public.stewardship_campaign_config_abort_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_campaign_config_intent_immutable_v1()
CREATE FUNCTION public.stewardship_campaign_config_intent_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_campaign_configuration_immutable_v1()
CREATE FUNCTION public.stewardship_campaign_configuration_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_campaign_control_immutable_v1()
CREATE FUNCTION public.stewardship_campaign_control_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_campaign_credentials_mutable_v1()
CREATE FUNCTION public.stewardship_campaign_credentials_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_campaign_end_admission_v1()
CREATE FUNCTION public.stewardship_campaign_end_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; prior stewardship_campaign_configuration%ROWTYPE;
    proposed stewardship_campaign_configuration%ROWTYPE; i stewardship_campaign_config_intent%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id OR OLD.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO c FROM stewardship_campaign WHERE id=OLD.current_campaign_id FOR UPDATE;
    SELECT * INTO prior FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    SELECT * INTO proposed FROM stewardship_campaign_configuration WHERE configuration_id=NEW.active_configuration_id AND record_id=c.id;
    IF c.structural_locked AND proposed.ends_at<>prior.ends_at THEN
        SELECT intent.* INTO i FROM stewardship_campaign_config_intent intent
            JOIN stewardship_config_activation a ON a.request_id=intent.request_id WHERE a.configuration_id=NEW.active_configuration_id;
        IF i.id IS NULL OR i.campaign_id<>c.id OR i.expected_version<>c.version OR i.expected_runtime_version<>OLD.version
           OR i.prior_projection_id<>prior.id OR i.actor_id IS DISTINCT FROM NEW.actor_id OR NEW.restore_review_required
           OR (i.action='edit_end' AND (c.state NOT IN ('scheduled','active') OR stewardship_campaign_now_v1()>=prior.ends_at))
           OR (i.action='reopen' AND (c.state<>'closed' OR proposed.ends_at<=prior.ends_at))
           OR proposed.ends_at<=stewardship_campaign_now_v1()
           OR EXISTS(SELECT 1 FROM stewardship_campaign_boundary b
               JOIN stewardship_task_run t ON t.domain_request_id=b.campaign_id AND t.task_type='campaign_boundary'
               JOIN stewardship_task_run root ON root.id=t.root_id
               WHERE b.campaign_id=c.id AND b.kind='close' AND b.state='pending'
                   AND (t.id=b.task_id OR root.idempotency_key=b.id::text)
                   AND t.state IN ('running','abandoned'))
           OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'End change requires current quiescent exceptional intent' USING ERRCODE='23514'; END IF;
    ELSIF EXISTS(SELECT 1 FROM stewardship_campaign_config_intent intent_row JOIN stewardship_config_activation a ON a.request_id=intent_row.request_id
        WHERE a.configuration_id=NEW.active_configuration_id) THEN
        RAISE EXCEPTION 'Exceptional end intent must change the end date' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_end_effect_v1()
CREATE FUNCTION public.stewardship_campaign_end_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; p stewardship_campaign_configuration%ROWTYPE;
    i stewardship_campaign_config_intent%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id OR NEW.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.current_campaign_id;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    UPDATE stewardship_campaign_boundary SET state='skipped',reason='boundary_replaced',completed_at=stewardship_campaign_now_v1(),
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE campaign_id=c.id AND kind='close' AND due_at<>p.ends_at AND state='pending';
    SELECT intent.* INTO i FROM stewardship_campaign_config_intent intent
        JOIN stewardship_config_activation a ON a.request_id=intent.request_id WHERE a.configuration_id=NEW.active_configuration_id;
    IF i.action IN ('edit_end','reopen') THEN
        INSERT INTO stewardship_campaign_boundary(id,campaign_id,kind,due_at,execution_revision,
            state,reason,version,actor_id,correlation_id)
        SELECT gen_random_uuid(),c.id,'close',p.ends_at,coalesce(max(execution_revision),0)+1,
            'pending','',1,NEW.actor_id,NEW.correlation_id
        FROM stewardship_campaign_boundary WHERE campaign_id=c.id AND kind='close';
    END IF;
    IF i.action='reopen' THEN
        INSERT INTO stewardship_campaign_transition(id,campaign_id,request_id,action,expected_version,expected_runtime_version,
            before_state,after_state,before_mode,after_mode,configuration_id,prior_projection_id,token_generation_id,reason,actor_id,correlation_id)
        VALUES(gen_random_uuid(),c.id,i.request_id,'reopen',c.version,NEW.version,'closed','active',NEW.mode,'production',
            NEW.active_configuration_id,i.prior_projection_id,i.token_generation_id,'',NEW.actor_id,NEW.correlation_id);
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_intent_v1()
CREATE FUNCTION public.stewardship_campaign_intent_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    IF c.id IS DISTINCT FROM r.current_campaign_id OR c.version<>NEW.expected_version OR r.version<>NEW.expected_runtime_version
       OR c.active_configuration_id<>NEW.prior_projection_id OR r.restore_review_required OR NEW.actor_id IS NULL
       OR (NEW.action='reopen' AND (c.state<>'closed' OR NEW.token_generation_id IS NULL OR NOT stewardship_campaign_quiet_v1(c.id)))
       OR (NEW.action='edit_end' AND (c.state NOT IN ('scheduled','active') OR NEW.token_generation_id IS NOT NULL))
       OR NEW.action NOT IN ('edit_end','reopen')
       OR NOT EXISTS(SELECT 1 FROM stewardship_config_request q
           WHERE q.id=NEW.request_id AND q.actor_id=NEW.actor_id AND q.base_id=r.active_configuration_id)
    THEN RAISE EXCEPTION 'Invalid exceptional campaign configuration intent' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_mail_audit_v1()
CREATE FUNCTION public.stewardship_campaign_mail_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        'campaign_mail_'||NEW.state,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',CASE
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN NEW.state IN ('not_sent','delivery_unknown') THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_campaign_mail_guard_v1()
CREATE FUNCTION public.stewardship_campaign_mail_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE owned boolean; live boolean; keys text[]; stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Campaign test outcomes are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Campaign test requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    live=public.stewardship_campaign_mail_live_v1(NEW.configuration_id,
        NEW.campaign_id,NEW.template_id,NEW.fingerprint,NEW.requested_by_id);
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web' OR NOT live
           OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.state<>'queued' OR NEW.version<>1
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.task_id AND task.root_id=task.id
                    AND task.task_type='campaign_mail_test' AND task.state='queued'
                    AND task.domain_request_id=NEW.id
                    AND task.idempotency_key=NEW.id::text
                    AND task.initiated_by_id=NEW.requested_by_id) THEN
            RAISE EXCEPTION 'Campaign mail requires explicit current Admin intent'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.mail) IS DISTINCT FROM 'object'
           OR octet_length(NEW.mail::text)>1048576 THEN
            RAISE EXCEPTION 'Invalid campaign mail sample' USING ERRCODE='23514';
        END IF;
        SELECT array_agg(key ORDER BY key) INTO keys
            FROM jsonb_object_keys(NEW.mail) key;
        IF keys IS DISTINCT FROM ARRAY['delivery_id','html','recipient','reply_to',
                                      'sender','subject','text']
           OR EXISTS (SELECT 1 FROM jsonb_each(NEW.mail) pair
                WHERE jsonb_typeof(pair.value)<>'string')
           OR NEW.mail->>'delivery_id' IS DISTINCT FROM NEW.id::text
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration runtime
                JOIN public.stewardship_applied_integration email
                    ON email.configuration_id=runtime.active_configuration_id
                    AND email.kind='email'
                WHERE runtime.testing_recipient=NEW.mail->>'recipient'
                    AND email.settings->>'sender'=NEW.mail->>'sender'
                    AND email.settings->>'reply_to'=NEW.mail->>'reply_to') THEN
            RAISE EXCEPTION 'Campaign test may address only the Testing recipient'
                USING ERRCODE='23514';
        END IF;
        NEW.created_at=stamp; NEW.updated_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state NOT IN ('queued','submitting') THEN
        RAISE EXCEPTION 'Terminal campaign test cannot be rewritten'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='queued' AND NEW.state='cancelled' THEN
        IF current_user<>'pk_stewardship_scheduler' OR NEW.actor_id IS NOT NULL
           OR (live AND NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=OLD.task_id
                    AND task.state IN ('failed','cancelled'))) THEN
            RAISE EXCEPTION 'Only stale unsent campaign tests can be cancelled'
                USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT EXISTS (SELECT 1 FROM public.stewardship_task_run task
            WHERE task.id=NEW.run_id AND task.id=NEW.task_id
                AND task.root_id=NEW.task_id
                AND task.task_type='campaign_mail_test'
                AND task.domain_request_id=NEW.id
                AND task.initiated_by_id=NEW.requested_by_id AND task.state='running'
                AND task.fence=NEW.task_fence AND task.worker_id=NEW.worker_id
                AND task.lease_expires_at>stamp) INTO owned;
        IF OLD.state='queued' AND NEW.state='submitting' THEN
            IF current_user<>'pk_stewardship_mail_dispatch' OR NOT live OR NOT owned
               OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
               OR NEW.mail IS DISTINCT FROM OLD.mail
               OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                    WHERE task.id=NEW.run_id
                        AND task.lease_expires_at>stamp+interval '30 seconds') THEN
                RAISE EXCEPTION 'Only the live mail worker may begin a campaign test'
                    USING ERRCODE='23514';
            END IF;
            NEW.submitted_at=stamp; NEW.deadline_at=stamp+interval '30 seconds';
            RETURN NEW;
        ELSIF OLD.state='submitting'
              AND NEW.state IN ('accepted','not_sent','delivery_unknown') THEN
            IF current_user='pk_stewardship_mail_dispatch' AND owned
               AND NEW.actor_id=NEW.worker_id THEN
                IF stamp>=OLD.deadline_at AND NEW.state<>'delivery_unknown' THEN
                    RAISE EXCEPTION 'Late campaign test result remains unknown'
                        USING ERRCODE='23514';
                END IF;
            ELSIF current_user='pk_stewardship_scheduler' AND NOT owned
                  AND stamp>=OLD.deadline_at AND NEW.state='delivery_unknown'
                  AND NEW.actor_id IS NULL THEN
                NULL;
            ELSE
                RAISE EXCEPTION 'Campaign test outcome requires live or drained owner'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Invalid campaign test transition' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.finished_at=stamp; NEW.mail='{}'::jsonb;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_mail_live_v1(uuid, uuid, uuid, text, uuid)
CREATE FUNCTION public.stewardship_campaign_mail_live_v1(configuration_id uuid, campaign_id uuid, template_id uuid, fingerprint text, requested_by uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_campaign campaign
            ON campaign.id=runtime.current_campaign_id
        JOIN public.stewardship_content_version template
            ON template.configuration_id=runtime.active_configuration_id
            AND template.campaign_id=campaign.id AND template.kind='email'
        JOIN public.stewardship_applied_integration workspace
            ON workspace.configuration_id=runtime.active_configuration_id
            AND workspace.kind='google_workspace'
        JOIN public.stewardship_portal_user owner ON owner.id=$5 AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=runtime.active_configuration_id
            AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.active_configuration_id=$1 AND campaign.id=$2
            AND template.id=$3 AND workspace.credential_fingerprint=$4
            AND ((runtime.mode='testing' AND campaign.state='draft')
                OR (runtime.mode='production' AND campaign.delivery_paused
                    AND campaign.state IN ('scheduled','active','closed')))
            AND NOT runtime.restore_review_required
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
                WHERE gate.state IN ('preparing','running'))
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials c
                WHERE c.go_live_gate)
    );
$_$;

-- FUNCTION: stewardship_campaign_mail_test_mutable_v1()
CREATE FUNCTION public.stewardship_campaign_mail_test_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."configuration_id" IS DISTINCT FROM OLD."configuration_id" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."template_id" IS DISTINCT FROM OLD."template_id" OR NEW."requested_by_id" IS DISTINCT FROM OLD."requested_by_id" OR NEW."request_key" IS DISTINCT FROM OLD."request_key" OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint" OR NEW."task_id" IS DISTINCT FROM OLD."task_id" OR (OLD."submitted_at" IS NOT NULL AND NEW."submitted_at" IS DISTINCT FROM OLD."submitted_at") OR (OLD."deadline_at" IS NOT NULL AND NEW."deadline_at" IS DISTINCT FROM OLD."deadline_at") OR (OLD."finished_at" IS NOT NULL AND NEW."finished_at" IS DISTINCT FROM OLD."finished_at") OR (OLD."run_id" IS NOT NULL AND NEW."run_id" IS DISTINCT FROM OLD."run_id") OR (OLD."task_fence" IS NOT NULL AND NEW."task_fence" IS DISTINCT FROM OLD."task_fence") OR (OLD."worker_id" IS NOT NULL AND NEW."worker_id" IS DISTINCT FROM OLD."worker_id") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_campaign_mutable_v1()
CREATE FUNCTION public.stewardship_campaign_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_campaign_now_v1()
CREATE FUNCTION public.stewardship_campaign_now_v1() RETURNS timestamp with time zone
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT statement_timestamp() $$;

-- FUNCTION: stewardship_campaign_pointer_v1()
CREATE FUNCTION public.stewardship_campaign_pointer_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE target uuid; candidate stewardship_campaign_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Configuration cannot replace the current pointer' USING ERRCODE='23514';
    END IF;
    target := OLD.current_campaign_id;
    IF target IS NOT NULL AND EXISTS (
        SELECT 1 FROM stewardship_campaign_configuration c WHERE c.configuration_id=NEW.active_configuration_id
        AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)
    ) THEN RAISE EXCEPTION 'Current campaign prevents successor creation' USING ERRCODE='23514'; END IF;
    IF target IS NULL THEN
        IF (SELECT count(*) FROM stewardship_campaign_configuration c
            WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)) > 1 THEN
            RAISE EXCEPTION 'Only one successor can be created' USING ERRCODE='23514';
        END IF;
        SELECT record_id INTO target FROM stewardship_campaign_configuration c
        WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id);
        IF target IS NOT NULL AND (NEW.mode<>'testing' OR NEW.restore_review_required
            OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state NOT IN ('archived','purged'))
            OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))) THEN
            RAISE EXCEPTION 'Successor creation is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM stewardship_campaign old_campaign
        JOIN stewardship_campaign_configuration old_c ON old_c.id=old_campaign.active_configuration_id
        LEFT JOIN stewardship_campaign_configuration new_c ON new_c.record_id=old_campaign.id AND new_c.configuration_id=NEW.active_configuration_id
        WHERE new_c.id IS NULL OR (old_campaign.id IS DISTINCT FROM target AND old_c.values IS DISTINCT FROM new_c.values)
    ) THEN RAISE EXCEPTION 'Historical campaigns cannot be removed or edited' USING ERRCODE='23514'; END IF;
    IF target IS NOT NULL THEN
        SELECT * INTO candidate FROM stewardship_campaign_configuration WHERE record_id=target AND configuration_id=NEW.active_configuration_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Current campaign cannot be removed' USING ERRCODE='23514'; END IF;
        IF NEW.restore_review_required AND EXISTS (
            SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values IS DISTINCT FROM candidate.values
        ) THEN RAISE EXCEPTION 'Restore review holds campaign configuration changes' USING ERRCODE='23514'; END IF;
        IF OLD.current_campaign_id IS NULL AND candidate.timezone<>(SELECT timezone FROM stewardship_parish WHERE configuration_id=NEW.active_configuration_id) THEN
            RAISE EXCEPTION 'New draft must copy the parish timezone' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions','end_date']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions','end_date'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_projection_v1()
CREATE FUNCTION public.stewardship_campaign_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; section text; owner_zone text; expected_due timestamptz;
    owner_start timestamptz; owner_end timestamptz;
BEGIN
    section := CASE WHEN TG_TABLE_NAME = 'stewardship_campaign_configuration'
                    THEN 'campaigns' ELSE 'schedules' END;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version v,
        jsonb_array_elements(v.canonical_document->'sections'->section) item
    WHERE v.id = NEW.configuration_id AND item->>'id' = NEW.record_id::text
      AND v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8');
    IF expected IS NULL OR expected IS DISTINCT FROM NEW.values THEN
        RAISE EXCEPTION 'Campaign projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    IF section = 'campaigns' THEN
        IF NEW.name IS DISTINCT FROM expected->>'name'
           OR NEW.timezone IS DISTINCT FROM expected->>'timezone'
           OR NEW.start_date IS DISTINCT FROM (expected->>'start_date')::date
           OR NEW.end_date IS DISTINCT FROM (expected->>'end_date')::date
           OR jsonb_typeof(expected->'modules') IS DISTINCT FROM 'array'
           OR coalesce(jsonb_array_length(expected->'modules'), 0) < 1
           OR expected->'modules' IS DISTINCT FROM (
               SELECT jsonb_agg(value ORDER BY value COLLATE "C")
               FROM (SELECT DISTINCT value
                     FROM jsonb_array_elements_text(expected->'modules')) canonical
           )
           OR NOT expected->'modules' <@ '["census", "ministry", "financial"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid indexed campaign projection' USING ERRCODE = '23514';
        END IF;
        IF NEW.starts_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                NEW.start_date::timestamp, NEW.timezone)
           OR NEW.ends_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                (NEW.end_date + 1)::timestamp, NEW.timezone) THEN
            RAISE EXCEPTION 'Invalid resolved campaign boundary' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb AND (
            (expected->'financial'->>'end')::date IS DISTINCT FROM
                ((expected->'financial'->>'start')::date + interval '1 year -1 day')::date
            OR (expected->'financial'->>'comparison_end')::date IS DISTINCT FROM
                ((expected->'financial'->>'comparison_start')::date + interval '1 year -1 day')::date
        ) THEN
            RAISE EXCEPTION 'Invalid financial period' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb
           AND (expected->'financial'->>'start')::date <= NEW.end_date
           AND (expected->'financial'->>'end')::date >= NEW.start_date
           AND expected->'financial'->'overlap_confirmed' IS DISTINCT FROM 'true'::jsonb THEN
            RAISE EXCEPTION 'Financial overlap requires confirmation' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.campaign_id::text IS DISTINCT FROM expected->>'campaign_id'
           OR NEW.kind IS DISTINCT FROM expected->>'kind'
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                          WHERE configuration_id = NEW.configuration_id
                            AND record_id = NEW.campaign_id) THEN
            RAISE EXCEPTION 'Invalid schedule ownership' USING ERRCODE = '23514';
        END IF;
        SELECT timezone, starts_at, ends_at INTO owner_zone, owner_start, owner_end
        FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.configuration_id AND record_id = NEW.campaign_id;
        IF NEW.kind IN ('initial', 'reminder') THEN
            expected_due := public.stewardship_resolve_local_v1(
                (expected->>'date')::date + (expected->>'time')::time, owner_zone);
        END IF;
        IF NEW.due_at IS DISTINCT FROM expected_due THEN
            RAISE EXCEPTION 'Invalid resolved schedule boundary' USING ERRCODE = '23514';
        END IF;
        IF NEW.due_at < owner_start OR NEW.due_at >= owner_end THEN
            RAISE EXCEPTION 'Schedule is outside campaign interval' USING ERRCODE = '23514';
        END IF;
        -- Match preparation's global lock even for direct competing inserts.
        -- PL/pgSQL's following READ COMMITTED query sees the previous winner.
        PERFORM pg_advisory_xact_lock(736210, 1);
        IF EXISTS (SELECT 1 FROM public.stewardship_schedule_revision
                   WHERE record_id = NEW.record_id
                     AND (campaign_id <> NEW.campaign_id OR kind <> NEW.kind)) THEN
            RAISE EXCEPTION 'Logical schedule identity is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_quiet_v1(uuid)
CREATE FUNCTION public.stewardship_campaign_quiet_v1(target uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT NOT EXISTS (SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=$1 AND completed_at IS NULL)
       AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=$1 AND delivery_paused)
       AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_occurrence o
           JOIN stewardship_schedule_definition d ON d.id=o.definition_id
           WHERE d.campaign_id=$1 AND o.state IN ('pending','running','delivery_unknown'))
$_$;

-- FUNCTION: stewardship_campaign_runtime_v1()
CREATE FUNCTION public.stewardship_campaign_runtime_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Campaign deletion requires exceptional purge' USING ERRCODE='23514'; END IF;
    IF (TG_OP='INSERT' AND NEW.production_cycle<>0) OR (TG_OP='UPDATE' AND
        NEW.production_cycle<>OLD.production_cycle+(CASE WHEN EXISTS (
            SELECT 1 FROM stewardship_campaign_transition t WHERE t.campaign_id=NEW.id
                AND t.expected_version=OLD.version AND t.action='withdraw'
                AND t.before_state=OLD.state AND t.after_state=NEW.state
                AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id
        ) THEN 1 ELSE 0 END)) THEN
        RAISE EXCEPTION 'Production cycle requires withdrawal evidence' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.active_configuration_id=OLD.active_configuration_id THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_campaign_transition t
            WHERE t.campaign_id=NEW.id AND t.expected_version=OLD.version
              AND t.before_state=OLD.state AND t.after_state=NEW.state
              AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id)
            AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_control k WHERE k.campaign_id=NEW.id AND k.expected_version=OLD.version
                AND k.actor_id IS NOT DISTINCT FROM NEW.actor_id AND k.correlation_id=NEW.correlation_id AND NEW.state=OLD.state) THEN
            RAISE EXCEPTION 'Campaign writes require lifecycle evidence' USING ERRCODE='23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration r
        JOIN stewardship_campaign_configuration c ON c.configuration_id=r.active_configuration_id
        JOIN stewardship_config_activation a ON a.configuration_id=r.active_configuration_id
        WHERE r.current_campaign_id=NEW.id AND c.id=NEW.active_configuration_id AND c.record_id=NEW.id
          AND a.actor_id IS NOT DISTINCT FROM NEW.actor_id AND a.correlation_id=NEW.correlation_id
    ) THEN RAISE EXCEPTION 'Campaign projection requires activation evidence' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND NEW.active_configuration_id<>OLD.active_configuration_id
       AND (to_jsonb(NEW)-ARRAY['active_configuration_id','version','updated_at','actor_id','correlation_id'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['active_configuration_id','version','updated_at','actor_id','correlation_id']) THEN
        RAISE EXCEPTION 'Configuration cannot mutate campaign runtime' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_transition_effect_v1()
CREATE FUNCTION public.stewardship_campaign_transition_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE stewardship_campaign SET state=NEW.after_state, version=version+1,
        structural_locked=CASE WHEN NEW.action='withdraw' THEN false WHEN NEW.action='activate' THEN true ELSE structural_locked END,
        ever_active=ever_active OR NEW.after_state='active',
        active_token_generation_id=CASE WHEN NEW.action IN ('activate','reopen') THEN NEW.token_generation_id
            WHEN NEW.action IN ('close','withdraw') THEN NULL ELSE active_token_generation_id END,
        readiness_revision=readiness_revision+CASE WHEN NEW.action IN ('activate','withdraw','reopen') THEN 1 ELSE 0 END,
        production_cycle=production_cycle+CASE WHEN NEW.action='withdraw' THEN 1 ELSE 0 END,
        actor_id=NEW.actor_id, correlation_id=NEW.correlation_id WHERE id=NEW.campaign_id;
    INSERT INTO stewardship_runtime_transition(id,request_id,expected_version,action,before_mode,after_mode,
        before_campaign_id,after_campaign_id,campaign_transition_id,reason,actor_id,correlation_id)
    VALUES (gen_random_uuid(),NEW.request_id,NEW.expected_runtime_version,'campaign',NEW.before_mode,NEW.after_mode,
        NEW.campaign_id,NEW.campaign_id,NEW.id,NEW.reason,NEW.actor_id,NEW.correlation_id);
    IF NEW.action='activate' AND NEW.after_state='active' THEN
        INSERT INTO stewardship_activation_catchup(id,campaign_id,activation_id,cutoff,configuration_id,
            phase,cursor,groups_completed,items_completed,failure_code,version,actor_id,correlation_id)
        VALUES (gen_random_uuid(),NEW.campaign_id,NEW.id,stewardship_campaign_now_v1(),NEW.configuration_id,
            'pending','',0,0,'',1,NEW.actor_id,NEW.correlation_id);
    END IF;
    IF NEW.boundary_id IS NOT NULL THEN
        UPDATE stewardship_campaign_boundary SET state='succeeded',transition_id=NEW.id,
            completed_at=stewardship_campaign_now_v1(),version=version+1,actor_id=NEW.actor_id,
            correlation_id=NEW.correlation_id WHERE id=NEW.boundary_id;
    END IF;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'campaign_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_campaign_transition_immutable_v1()
CREATE FUNCTION public.stewardship_campaign_transition_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_campaign_transition_v1()
CREATE FUNCTION public.stewardship_campaign_transition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
    p stewardship_campaign_configuration%ROWTYPE; expected_state text; expected_mode text;
    instant timestamptz := stewardship_campaign_now_v1();
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
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

-- FUNCTION: stewardship_campaign_work_gate_mutable_v1()
CREATE FUNCTION public.stewardship_campaign_work_gate_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."request_id" IS DISTINCT FROM OLD."request_id" OR NEW."initiated_by_id" IS DISTINCT FROM OLD."initiated_by_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_catchup_checkpoint_immutable_v1()
CREATE FUNCTION public.stewardship_catchup_checkpoint_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_catchup_failure_effect_v1()
CREATE FUNCTION public.stewardship_catchup_failure_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE stewardship_activation_catchup SET failure_code=NEW.code,version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.demand_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    SELECT gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'catchup_failed',NEW.id,d.campaign_id
    FROM stewardship_activation_catchup d WHERE d.id=NEW.demand_id;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_catchup_failure_guard_v1()
CREATE FUNCTION public.stewardship_catchup_failure_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE d stewardship_activation_catchup%ROWTYPE; t stewardship_task_run%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO d FROM stewardship_activation_catchup WHERE id=NEW.demand_id FOR UPDATE;
    SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
    IF d.id IS NULL OR d.completed_at IS NOT NULL OR NEW.expected_version<>d.version OR NEW.actor_id IS NULL
       OR NEW.code NOT IN ('source_unavailable','invalid_source','enumeration_failed','outcome_failed','recovery_required')
       OR t.id IS NULL OR d.task_root_id IS NULL OR t.root_id<>d.task_root_id OR t.state<>'running'
       OR t.fence<>NEW.fence OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp()
       OR r.restore_review_required OR r.current_campaign_id IS DISTINCT FROM d.campaign_id
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=d.campaign_id AND state IN ('preparing','running','tombstone')) THEN
        RAISE EXCEPTION 'Catch-up failure requires exact fenced evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_catchup_failure_immutable_v1()
CREATE FUNCTION public.stewardship_catchup_failure_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_catchup_guard_v1()
CREATE FUNCTION public.stewardship_catchup_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Catch-up history cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.phase<>'pending' OR NEW.cursor<>'' OR NEW.groups_completed<>0 OR NEW.items_completed<>0
           OR NEW.completed_at IS NOT NULL OR NEW.failure_code<>'' OR NEW.task_root_id IS NOT NULL OR NEW.source_snapshot_id IS NOT NULL
           OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_transition t WHERE t.id=NEW.activation_id
               AND t.campaign_id=NEW.campaign_id AND t.configuration_id=NEW.configuration_id AND t.action='activate' AND t.after_state='active'
               AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id)
        THEN RAISE EXCEPTION 'Catch-up demand requires activation evidence' USING ERRCODE='23514'; END IF;
    ELSE
        IF OLD.completed_at IS NOT NULL THEN RAISE EXCEPTION 'Completed catch-up is immutable' USING ERRCODE='23514'; END IF;
        IF OLD.task_root_id IS NULL AND NEW.task_root_id IS NOT NULL THEN
            IF NEW.source_snapshot_id IS NULL OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t
                WHERE t.id=NEW.task_root_id AND t.root_id=t.id AND t.task_type='activation_catchup' AND t.domain_request_id=NEW.id)
               OR (to_jsonb(NEW)-ARRAY['task_root_id','source_snapshot_id','version','updated_at','actor_id','correlation_id'])
                 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['task_root_id','source_snapshot_id','version','updated_at','actor_id','correlation_id']) THEN
                RAISE EXCEPTION 'Invalid catch-up input binding' USING ERRCODE='23514'; END IF;
        ELSIF EXISTS(SELECT 1 FROM stewardship_catchup_failure f WHERE f.demand_id=NEW.id
            AND f.expected_version=OLD.version AND NEW.version=OLD.version+1 AND f.code=NEW.failure_code
            AND f.actor_id IS NOT DISTINCT FROM NEW.actor_id AND f.correlation_id=NEW.correlation_id)
            AND (to_jsonb(NEW)-ARRAY['failure_code','version','updated_at','actor_id','correlation_id'])
                = (to_jsonb(OLD)-ARRAY['failure_code','version','updated_at','actor_id','correlation_id']) THEN
            RETURN NEW;
        ELSIF NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k WHERE k.demand_id=NEW.id
            AND k.sequence=OLD.groups_completed+1 AND NEW.groups_completed=k.sequence
            AND NEW.items_completed=OLD.items_completed+k.items AND NEW.cursor=k.cursor AND NEW.phase=k.phase
            AND (NEW.completed_at IS NOT NULL)=k.complete AND NEW.failure_code=''
            AND k.actor_id IS NOT DISTINCT FROM NEW.actor_id AND k.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Catch-up progress requires exact checkpoint' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- Recheck the final row at commit: the INSERT event's original task pointer is
-- null while activation's owning transaction allocates and binds its root.
CREATE FUNCTION public.stewardship_catchup_allocation_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_activation_catchup d
        JOIN public.stewardship_campaign_transition a ON a.id=d.activation_id
        JOIN public.stewardship_family_token_generation g ON g.id=a.token_generation_id
        JOIN public.stewardship_task_run t ON t.id=d.task_root_id
        WHERE d.id=NEW.id AND t.root_id=t.id
          AND t.task_type='activation_catchup' AND t.domain_request_id=d.id
          AND t.idempotency_key=d.id::text
          AND t.initiated_by_id IS NOT DISTINCT FROM a.actor_id
          AND d.source_snapshot_id=g.source_snapshot_id
          AND g.campaign_id=d.campaign_id
    ) THEN RAISE EXCEPTION 'Activation requires its atomic catch-up task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_chair_decisions_v1(uuid)
CREATE FUNCTION public.stewardship_chair_decisions_v1(configuration uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'assignment_record_id',record_id::text,
        'active',reason='current_chair','reason',reason) ORDER BY record_id),
        '[]'::jsonb)
    FROM (
        SELECT assignment.record_id, CASE
            WHEN evidence.id IS NULL THEN 'missing_binding'
            WHEN evidence.organization_id::text IS DISTINCT FROM
                integration.settings->>'organization_id' THEN 'organization_changed'
            WHEN EXISTS (SELECT 1 FROM stewardship_ministry_activity activity
                WHERE activity.configuration_id=configuration
                  AND activity.organization_id=evidence.organization_id
                  AND activity.ministry_duid=assignment.ministry_duid
                  AND NOT activity.active) THEN 'ministry_inactive'
            WHEN EXISTS (SELECT 1 FROM stewardship_current_chair chair
                WHERE chair.organization_id=evidence.organization_id
                  AND chair.member_duid=evidence.member_duid
                  AND chair.ministry_duid=assignment.ministry_duid
                  AND chair.email=assignment.email) THEN 'current_chair'
            ELSE 'relationship_missing' END AS reason
        FROM stewardship_ministry_assignment assignment
        LEFT JOIN stewardship_chair_seed_evidence evidence
            ON evidence.assignment_record_id=assignment.record_id
        LEFT JOIN stewardship_applied_integration integration
            ON integration.configuration_id=configuration
            AND integration.kind='parishsoft'
        WHERE assignment.configuration_id=configuration
            AND assignment.source='chair-seed'
    ) decisions;
$$;

-- FUNCTION: stewardship_chair_reconciliation_effects_v1()
CREATE FUNCTION public.stewardship_chair_reconciliation_effects_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE decision jsonb;
        assignment uuid;
BEGIN
    FOR decision IN SELECT * FROM jsonb_array_elements(NEW.decisions) LOOP
        assignment := (decision->>'assignment_record_id')::uuid;
        INSERT INTO stewardship_assignment_overlay
            (id,assignment_record_id,active,source_snapshot_id,reason,version,
             actor_id,correlation_id)
        VALUES (gen_random_uuid(),assignment,(decision->>'active')::boolean,
            NEW.snapshot_id,decision->>'reason',1,NEW.actor_id,NEW.correlation_id)
        ON CONFLICT (assignment_record_id) DO UPDATE SET
            active=EXCLUDED.active,source_snapshot_id=EXCLUDED.source_snapshot_id,
            reason=EXCLUDED.reason,version=stewardship_assignment_overlay.version+1,
            actor_id=EXCLUDED.actor_id,correlation_id=EXCLUDED.correlation_id;
        IF (decision->>'active')::boolean THEN
            UPDATE stewardship_chair_review SET closed_by_id=NEW.id,
                latest_by_id=NEW.id,close_reason='relationship_returned',
                version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
                WHERE assignment_record_id=assignment AND closed_by_id IS NULL;
        ELSE
            UPDATE stewardship_chair_review SET latest_by_id=NEW.id,version=version+1,
                actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
                WHERE assignment_record_id=assignment AND closed_by_id IS NULL;
            IF NOT FOUND THEN
                INSERT INTO stewardship_chair_review
                    (id,assignment_record_id,opened_by_id,latest_by_id,close_reason,
                     version,actor_id,correlation_id)
                VALUES (gen_random_uuid(),assignment,NEW.id,NEW.id,'',1,
                        NEW.actor_id,NEW.correlation_id);
            END IF;
        END IF;
    END LOOP;
    UPDATE stewardship_chair_review review SET closed_by_id=NEW.id,latest_by_id=NEW.id,
        close_reason='assignment_removed',version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
        WHERE review.closed_by_id IS NULL AND NOT EXISTS (
            SELECT 1 FROM stewardship_ministry_assignment assignment
            WHERE assignment.configuration_id=NEW.configuration_id
              AND assignment.source='chair-seed'
              AND assignment.record_id=review.assignment_record_id);
    INSERT INTO stewardship_audit_event
        (id,event_type,subject_id,actor_id,correlation_id,ownership_scope,parish_id)
    SELECT NEW.id,'chair_reconciled',NEW.id,NEW.actor_id,NEW.correlation_id,
        'parish',id FROM stewardship_parish WHERE configuration_id=NEW.configuration_id;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_chair_reconciliation_guard_v1()
CREATE FUNCTION public.stewardship_chair_reconciliation_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.owner_transaction IS DISTINCT FROM txid_current()
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM stewardship_system_configuration
          WHERE active_configuration_id=NEW.configuration_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_source_current
          WHERE snapshot_id=NEW.snapshot_id) THEN
        RAISE EXCEPTION 'Chair reconciliation requires current ordered inputs'
            USING ERRCODE='23514';
    END IF;
    IF NEW.activation_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_config_activation
            WHERE id=NEW.activation_id AND configuration_id=NEW.configuration_id
              AND actor_id IS NOT DISTINCT FROM NEW.actor_id
              AND correlation_id=NEW.correlation_id
              AND xmin::text::numeric=mod(pg_current_xact_id()::text::numeric,4294967296)) THEN
            RAISE EXCEPTION 'Chair reconciliation requires its owning activation'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM stewardship_source_snapshot snapshot
            JOIN stewardship_source_lease lease ON lease.owner_id=snapshot.task_id
                AND lease.fence=snapshot.source_fence
            JOIN stewardship_task_run task ON task.id=lease.owner_id
            WHERE snapshot.id=NEW.snapshot_id AND snapshot.task_id=NEW.source_owner_id
              AND snapshot.source_fence=NEW.source_fence
              AND lease.worker_id=NEW.actor_id AND lease.phase IN ('full','delta')
              AND lease.expires_at>clock_timestamp() AND task.state='running'
              AND task.fence=lease.task_fence AND task.worker_id=lease.worker_id
              AND task.correlation_id=NEW.correlation_id
              AND task.lease_expires_at>clock_timestamp()) THEN
            RAISE EXCEPTION 'Chair reconciliation requires its live source owner'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.decisions IS DISTINCT FROM
        stewardship_chair_decisions_v1(NEW.configuration_id) THEN
        RAISE EXCEPTION 'Chair reconciliation decisions differ from exact evidence'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_chair_reconciliation_immutable_v1()
CREATE FUNCTION public.stewardship_chair_reconciliation_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_chair_review_mutable_v1()
CREATE FUNCTION public.stewardship_chair_review_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."assignment_record_id" IS DISTINCT FROM OLD."assignment_record_id" OR NEW."opened_by_id" IS DISTINCT FROM OLD."opened_by_id" OR (OLD."closed_by_id" IS NOT NULL AND NEW."closed_by_id" IS DISTINCT FROM OLD."closed_by_id") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_chair_review_owner_v1()
CREATE FUNCTION public.stewardship_chair_review_owner_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE receipt stewardship_chair_reconciliation%ROWTYPE;
        decision jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Chair review history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.closed_by_id IS NOT NULL THEN
        RAISE EXCEPTION 'Closed Chair review history cannot change'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO receipt FROM stewardship_chair_reconciliation
        WHERE id=NEW.latest_by_id AND owner_transaction=txid_current();
    IF receipt.id IS NULL THEN
        RAISE EXCEPTION 'Chair review requires its current reconciliation owner'
            USING ERRCODE='23514';
    END IF;
    SELECT value INTO decision FROM jsonb_array_elements(receipt.decisions)
        WHERE (value->>'assignment_record_id')::uuid=NEW.assignment_record_id;
    IF (TG_OP='INSERT' AND (
        NEW.opened_by_id<>receipt.id OR NEW.closed_by_id IS NOT NULL))
       OR (NEW.closed_by_id IS NULL
           AND decision->'active' IS DISTINCT FROM 'false'::jsonb)
       OR (NEW.closed_by_id IS NOT NULL AND (
           NEW.closed_by_id<>receipt.id
           OR (NEW.close_reason='relationship_returned'
               AND decision->'active' IS DISTINCT FROM 'true'::jsonb)
           OR (NEW.close_reason='assignment_removed' AND decision IS NOT NULL))) THEN
        RAISE EXCEPTION 'Chair review differs from its exact reconciliation decision'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_chair_seed_evidence_guard_v1()
CREATE FUNCTION public.stewardship_chair_seed_evidence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE assignment stewardship_ministry_assignment%ROWTYPE;
        evidence jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Chair seed evidence requires work ownership'
            USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM stewardship_source_current
        WHERE snapshot_id=NEW.snapshot_id AND organization_id=NEW.organization_id
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Chair seed evidence requires current source ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT a.* INTO assignment FROM stewardship_ministry_assignment a
        JOIN stewardship_system_configuration runtime
            ON runtime.active_configuration_id=a.configuration_id
        JOIN stewardship_applied_integration integration
            ON integration.configuration_id=a.configuration_id
            AND integration.kind='parishsoft'
            AND integration.settings->>'organization_id'=NEW.organization_id::text
        WHERE a.id=NEW.assignment_id;
    IF assignment.id IS NULL OR assignment.source<>'chair-seed'
       OR assignment.record_id IS DISTINCT FROM NEW.assignment_record_id THEN
        RAISE EXCEPTION 'Chair seed evidence requires its applied seeded assignment'
            USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(roster_key ORDER BY roster_key) INTO evidence
        FROM stewardship_current_chair
        WHERE snapshot_id=NEW.snapshot_id AND organization_id=NEW.organization_id
          AND member_duid=NEW.member_duid AND ministry_duid=assignment.ministry_duid
          AND email=assignment.email;
    IF evidence IS NULL OR evidence IS DISTINCT FROM NEW.roster_keys
       OR EXISTS (SELECT 1 FROM stewardship_ministry_activity
          WHERE configuration_id=assignment.configuration_id
            AND organization_id=NEW.organization_id
            AND ministry_duid=assignment.ministry_duid AND NOT active) THEN
        RAISE EXCEPTION 'Chair seed evidence requires the exact current relationship'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_chair_seed_evidence_immutable_v1()
CREATE FUNCTION public.stewardship_chair_seed_evidence_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_checkpoint_effect_v1()
CREATE FUNCTION public.stewardship_checkpoint_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE stewardship_activation_catchup SET groups_completed=NEW.sequence,items_completed=items_completed+NEW.items,
        cursor=NEW.cursor,phase=NEW.phase,failure_code='',completed_at=CASE WHEN NEW.complete THEN stewardship_campaign_now_v1() END,
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.demand_id;
    RETURN NEW;
END $$;

-- Semantic coverage and independent restore decisions exclude a slot from
-- preparation, not from history. Keep every SQL proof on the same input set.
CREATE FUNCTION public.stewardship_schedule_slot_excluded_v1(
    definition uuid,mode text,target text,slot text
) RETURNS boolean LANGUAGE sql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
        WHERE f.definition_id=$1 AND f.mode=$2 AND f.target=$3 AND f.slot=$4)
    OR EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h
        WHERE h.definition_id=$1 AND h.mode=$2 AND h.target=$3 AND h.slot=$4
            AND h.state IN ('unreviewed','assumed_delivered'))
$$;

-- FUNCTION: stewardship_checkpoint_guard_v1()
CREATE FUNCTION public.stewardship_checkpoint_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE d stewardship_activation_catchup%ROWTYPE; t stewardship_task_run%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
        projection uuid; prefix text; family uuid; pending_count integer;
BEGIN
    -- Count-only group observations are immutable with their existing fenced
    -- checkpoint. Empty metadata belongs to intermediate traversal pages.
    IF jsonb_typeof(NEW.outcome_counts)<>'object' THEN
        RAISE EXCEPTION 'Catch-up outcomes require count-only metadata' USING ERRCODE='23514';
    END IF;
    IF NEW.outcome_counts<>'{}'::jsonb AND (
        NOT NEW.outcome_counts ?& ARRAY['active_families','eligible_families','no_email_families',
            'family_messages','daily_messages','weekly_messages','coalesced_slots']
        OR NEW.outcome_counts - ARRAY['active_families','eligible_families','no_email_families',
            'family_messages','daily_messages','weekly_messages','coalesced_slots']<>'{}'::jsonb
        OR EXISTS(SELECT 1 FROM jsonb_each(NEW.outcome_counts) item
            WHERE jsonb_typeof(item.value)<>'number' OR item.value::text !~ '^(0|[1-9][0-9]{0,14})$')
    ) THEN
        RAISE EXCEPTION 'Catch-up outcomes require exact nonnegative count fields' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO d FROM stewardship_activation_catchup WHERE id=NEW.demand_id FOR UPDATE;
    SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
    IF d.completed_at IS NOT NULL OR NEW.sequence<>d.groups_completed+1 OR NEW.group_key='' OR NEW.cursor='' OR NEW.phase=''
       OR t.id IS NULL OR d.task_root_id IS NULL OR t.root_id<>d.task_root_id OR t.state<>'running'
       OR t.fence<>NEW.fence OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp()
       OR r.restore_review_required OR r.current_campaign_id IS DISTINCT FROM d.campaign_id
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=d.campaign_id AND state IN ('preparing','running','tombstone')) THEN
        RAISE EXCEPTION 'Catch-up checkpoint requires current fenced input' USING ERRCODE='23514'; END IF;
    IF current_user='pk_stewardship_worker' THEN
        SELECT active_configuration_id INTO projection FROM stewardship_campaign WHERE id=d.campaign_id;
        prefix:=replace(projection::text,'-','')||':';
        IF NEW.items>100 OR NEW.phase NOT IN ('families','digests','complete')
            OR NOT starts_with(NEW.cursor,prefix) OR NOT starts_with(NEW.group_key,prefix)
            OR NEW.correlation_id<>t.id OR r.mode<>'production'
            OR NEW.complete<>(NEW.phase='complete') THEN
            RAISE EXCEPTION 'Catch-up requires a bounded compiled checkpoint' USING ERRCODE='23514';
        END IF;
        IF NEW.phase='families' THEN
            family:=split_part(NEW.group_key,':',3)::uuid;
            SELECT count(*) INTO pending_count FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_schedule_definition s ON s.current_revision_id=o.revision_id
                WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                    AND o.mode='production' AND o.target='family:'||family::text
                    AND o.due_at<=d.cutoff AND o.state='pending'
                    AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot);
            IF NEW.group_key<>prefix||'family:'||family::text
                OR NEW.cursor<>prefix||'families:'||replace(family::text,'-','')
                OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f
                    WHERE f.id=family AND f.campaign_id=d.campaign_id AND f.created_at<=d.created_at)
                OR EXISTS (
                    SELECT 1 FROM stewardship_schedule_definition s
                    JOIN stewardship_schedule_revision v ON v.id=s.current_revision_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                        AND v.due_at<=d.cutoff
                        AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
                            WHERE o.revision_id=v.id AND o.mode='production'
                                AND o.target='family:'||family::text AND o.slot='once')
                        AND NOT public.stewardship_schedule_slot_excluded_v1(s.id,'production','family:'||family::text,'once')
                )
                OR pending_count>1
                OR (pending_count>0 AND EXISTS(SELECT 1 FROM public.stewardship_family_campaign f
                    JOIN public.stewardship_campaign_configuration p ON p.id=projection
                    WHERE f.id=family AND (NOT f.active OR NOT f.email_eligible OR NOT f.email_deliverable
                        OR f.effective_submission_id IS NOT NULL OR public.stewardship_campaign_now_v1()>=p.ends_at)))
                -- A current selection must carry all interrupted predecessor
                -- coverage. With no selection (removal/ineligibility), retain
                -- the old immutable history without inventing replacement mail.
                OR (pending_count>0 AND EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_schedule_definition s ON s.id=o.definition_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                        AND o.mode='production' AND o.target='family:'||family::text
                        AND o.state='skipped' AND o.reason IN ('schedule_replaced','schedule_removed')
                        AND NOT EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e WHERE e.previous_id=o.id)
                        AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                                WHERE f.mode=o.mode AND f.target=o.target AND f.disposition='coalesced' AND f.occurrence_id=o.id)
                            OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e
                                WHERE e.demand_id=d.id AND e.replacement_id=o.id))))
                OR EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_schedule_definition s ON s.current_revision_id=o.revision_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                        AND o.mode='production' AND o.target='family:'||family::text AND o.due_at<=d.cutoff
                        AND ((NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
                                AND (o.state IN ('running','delivery_unknown')
                                    OR (o.state='pending' AND (o.task_id IS NOT NULL OR o.outbox_id IS NOT NULL))))
                            -- Coverage is the evidence being checked here, not
                            -- permission to skip its integrity check. Only an
                            -- independent restore hold excludes this outcome.
                            OR (o.state='coalesced'
                                AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h
                                    WHERE h.definition_id=o.definition_id AND h.mode=o.mode AND h.target=o.target
                                        AND h.slot=o.slot AND h.state IN ('unreviewed','assumed_delivered'))
                                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                                WHERE f.definition_id=o.definition_id AND f.mode=o.mode AND f.target=o.target
                                    AND f.slot=o.slot AND f.disposition='coalesced' AND f.occurrence_id=o.replacement_id))))
                THEN RAISE EXCEPTION 'Catch-up Family receipt lacks cutoff outcomes' USING ERRCODE='23514'; END IF;
        END IF;
        IF NEW.phase='digests' AND NOT public.stewardship_catchup_digest_receipt_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Catch-up digest receipt lacks bounded inventory proof' USING ERRCODE='23514';
        END IF;
        IF NEW.complete THEN
            IF NEW.group_key<>prefix||'complete' OR NEW.cursor<>prefix||'complete:'
                OR NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k
                    WHERE k.demand_id=d.id AND k.group_key=prefix||'families:end')
                OR EXISTS(SELECT 1 FROM stewardship_family_campaign f
                    WHERE f.campaign_id=d.campaign_id AND f.created_at<=d.created_at
                    AND NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k
                        WHERE k.demand_id=d.id AND k.group_key=prefix||'family:'||f.id::text))
                OR EXISTS(SELECT 1 FROM stewardship_schedule_definition s
                    WHERE s.campaign_id=d.campaign_id AND s.current_revision_id IS NOT NULL
                      AND s.kind IN ('daily_digest','weekly_digest')
                      AND NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k
                          WHERE k.demand_id=d.id AND k.group_key=prefix||'digest:'||s.id::text))
            THEN RAISE EXCEPTION 'Catch-up completion requires current cohort and digest coverage'
                USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_complete_consumers_v1()
CREATE FUNCTION public.stewardship_complete_consumers_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.required_consumers<>'[]'::jsonb
       AND NOT NEW.required_consumers @>
           stewardship_credential_consumers_v1(NEW.target) THEN
        RAISE EXCEPTION 'Credential consumer inventory must be complete'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_config_activation_immutable_v1()
CREATE FUNCTION public.stewardship_config_activation_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_config_checkpoint_immutable_v1()
CREATE FUNCTION public.stewardship_config_checkpoint_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_config_request_immutable_v1()
CREATE FUNCTION public.stewardship_config_request_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_configuration_version_immutable_v1()
CREATE FUNCTION public.stewardship_configuration_version_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_content_complete_v1()
CREATE FUNCTION public.stewardship_content_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE records jsonb; actual jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'content', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('campaign-content-v5', 'source-cadence-v8') THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Content requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid content shape' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_content_version
        WHERE configuration_id=NEW.id AND NOT (
            (kind='page' AND slot IN ('welcome', 'login_help', 'pre_start', 'post_end',
                'census', 'member_census', 'ministry', 'financial', 'additional',
                'review', 'thank_you', 'access_denied', 'submission_confirmation'))
            OR (kind='email' AND slot IN ('initial', 'reminder', 'confirmation',
                'daily_digest', 'weekly_digest', 'critical_alert'))
        )) THEN
        RAISE EXCEPTION 'Invalid content slot' USING ERRCODE='23514';
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id, 'values',
        jsonb_build_object('campaign_id', campaign_id, 'kind', kind, 'slot', slot,
            'subject', subject, 'html', html, 'text', text)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_content_version WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Content projections are incomplete' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_content_projection_v1()
CREATE FUNCTION public.stewardship_content_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'content') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('campaign-content-v5', 'source-cadence-v8')
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object('id', NEW.record_id,
        'values', jsonb_build_object('campaign_id', NEW.campaign_id,
            'kind', NEW.kind, 'slot', NEW.slot, 'subject', NEW.subject,
            'html', NEW.html, 'text', NEW.text))
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                      WHERE configuration_id=NEW.configuration_id
                        AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Content differs from YAML' USING ERRCODE='23514';
    END IF;
    SELECT predecessor_id INTO predecessor
    FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF EXISTS (
        WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM public.stewardship_configuration_version
            WHERE id=predecessor
            UNION
            SELECT p.id, p.predecessor_id
            FROM public.stewardship_configuration_version p
            JOIN chain c ON p.id=c.predecessor_id
        ) SELECT 1 FROM public.stewardship_content_version v
          JOIN chain c ON c.id=v.configuration_id
          WHERE v.record_id=NEW.record_id AND
              (v.campaign_id, v.kind, v.slot, v.subject, v.html, v.text)
              IS DISTINCT FROM
              (NEW.campaign_id, NEW.kind, NEW.slot, NEW.subject, NEW.html, NEW.text)
    ) THEN
        RAISE EXCEPTION 'Content revision identities must remain immutable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_content_version_immutable_v1()
CREATE FUNCTION public.stewardship_content_version_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_control_effect_v1()
CREATE FUNCTION public.stewardship_control_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE stewardship_campaign SET
        delivery_paused=CASE WHEN NEW.action IN ('pause','resume') THEN NEW.action='pause' ELSE delivery_paused END,
        pause_version=pause_version+(CASE WHEN NEW.action IN ('pause','resume') THEN 1 ELSE 0 END),
        pause_actor_id=CASE WHEN NEW.action IN ('pause','resume') THEN NEW.actor_id ELSE pause_actor_id END,
        pause_reason=CASE WHEN NEW.action IN ('pause','resume') THEN NEW.reason ELSE pause_reason END,
        paused_at=CASE WHEN NEW.action='pause' THEN NEW.occurred_at ELSE paused_at END,
        resumed_at=CASE WHEN NEW.action='pause' THEN NULL WHEN NEW.action='resume' THEN NEW.occurred_at ELSE resumed_at END,
        first_live_delivery_at=CASE WHEN NEW.action='first_delivery' THEN NEW.occurred_at ELSE first_live_delivery_at END,
        first_live_submission_at=CASE WHEN NEW.action='first_submission' THEN NEW.occurred_at ELSE first_live_submission_at END,
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.campaign_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'campaign_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_control_guard_v1()
CREATE FUNCTION public.stewardship_control_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE; p stewardship_campaign_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    IF c.id IS DISTINCT FROM r.current_campaign_id OR c.version<>NEW.expected_version OR r.version<>NEW.expected_runtime_version
       OR NEW.actor_id IS NULL OR r.mode<>'production' OR r.restore_review_required
       OR c.state NOT IN ('scheduled','active','closed') OR NEW.occurred_at>stewardship_campaign_now_v1()
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
        RAISE EXCEPTION 'Campaign control has stale or blocked inputs' USING ERRCODE='23514'; END IF;
    CASE NEW.action
        WHEN 'pause' THEN
            IF c.delivery_paused OR NEW.reason='' OR NEW.evidence_id IS NOT NULL THEN RAISE EXCEPTION 'Invalid delivery pause' USING ERRCODE='23514'; END IF;
        WHEN 'resume' THEN
            IF NOT c.delivery_paused OR NEW.reason='' OR NEW.evidence_id IS NOT NULL THEN RAISE EXCEPTION 'Invalid delivery resume' USING ERRCODE='23514'; END IF;
        WHEN 'first_delivery','first_submission' THEN
            IF NEW.evidence_id IS NULL OR NEW.occurred_at<p.starts_at OR NEW.occurred_at>=p.ends_at
               OR (NEW.action='first_delivery' AND c.first_live_delivery_at IS NOT NULL)
               OR (NEW.action='first_submission' AND c.first_live_submission_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Invalid first-live-effect evidence' USING ERRCODE='23514'; END IF;
        ELSE RAISE EXCEPTION 'Unknown campaign control' USING ERRCODE='23514';
    END CASE;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_control_time_v1()
CREATE FUNCTION public.stewardship_control_time_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Runs after the existing guard has serialized runtime and campaign rows.
    IF NEW.action IN('pause','resume') AND EXISTS(
        SELECT 1 FROM stewardship_campaign WHERE id=NEW.campaign_id
        AND NEW.occurred_at<greatest(paused_at,resumed_at)) THEN
        RAISE EXCEPTION 'Delivery control time cannot regress' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_coverage_valid_v1(jsonb)
CREATE FUNCTION public.stewardship_coverage_valid_v1(manifest jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE item jsonb; days jsonb; canonical jsonb;
BEGIN
    IF jsonb_typeof(manifest)<>'object' OR NOT (manifest ?& ARRAY['items','daily_range']) OR manifest-ARRAY['items','daily_range']<>'{}'::jsonb
       OR jsonb_typeof(manifest->'items')<>'array' OR jsonb_array_length(manifest->'items')>10000 THEN RETURN false; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(manifest->'items') LOOP
        IF jsonb_typeof(item)<>'object' OR NOT (item ?& ARRAY['kind','id','version']) OR item-ARRAY['kind','id','version']<>'{}'::jsonb
           OR jsonb_typeof(item->'kind')<>'string' OR jsonb_typeof(item->'id')<>'string'
           OR item->>'kind' NOT IN ('submission','item','correction') OR (item->>'id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(item->'version')<>'number' OR (item->>'version') !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
    END LOOP;
    SELECT coalesce(jsonb_agg(value ORDER BY value->>'kind',value->>'id',(value->>'version')::numeric),'[]'::jsonb)
    INTO canonical FROM (SELECT DISTINCT value FROM jsonb_array_elements(manifest->'items')) entries;
    IF canonical<>manifest->'items' THEN RETURN false; END IF;
    days:=manifest->'daily_range';
    IF days='null'::jsonb THEN RETURN jsonb_array_length(canonical)>0; END IF;
    IF jsonb_typeof(days)<>'object' OR NOT(days ?& ARRAY['start','end']) OR days-ARRAY['start','end']<>'{}'::jsonb
       OR jsonb_typeof(days->'start')<>'string' OR jsonb_typeof(days->'end')<>'string'
       OR (days->>'start') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR (days->>'end') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN false; END IF;
    RETURN (days->>'start')::date <= (days->>'end')::date;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
END $_$;

-- FUNCTION: stewardship_credential_ack_guard_v1()
CREATE FUNCTION public.stewardship_credential_ack_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE owner stewardship_secret_request%ROWTYPE;
BEGIN
    SELECT * INTO owner FROM stewardship_secret_request WHERE id=NEW.request_id FOR SHARE;
    IF NOT FOUND OR current_user<>'pk_stewardship_'||replace(NEW.consumer,'-','_')
       OR NOT owner.required_consumers ? NEW.consumer OR owner.state<>'awaiting_ack'
       OR owner.resulting_fingerprint IS DISTINCT FROM NEW.fingerprint
       OR owner.expires_at<=statement_timestamp() OR NEW.actor_id IS NOT NULL THEN
        RAISE EXCEPTION 'Consumer acknowledgement is not authorized' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    INSERT INTO stewardship_audit_event(id,created_at,actor_id,correlation_id,event_type,subject_id)
    VALUES(gen_random_uuid(),NEW.created_at,NULL,NEW.correlation_id,'credential_consumer_acknowledged',NEW.request_id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_credential_consumer_ack_immutable_v1()
CREATE FUNCTION public.stewardship_credential_consumer_ack_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_credential_consumers_v1(text)
CREATE FUNCTION public.stewardship_credential_consumers_v1(target text) RETURNS jsonb
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
SELECT CASE target
    WHEN 'django_signing' THEN '["web"]'::jsonb
    WHEN 'general_encryption' THEN '["web","worker"]'::jsonb
    WHEN 'family_code_mac' THEN '["web","worker"]'::jsonb
    WHEN 'token_public' THEN '["web","worker","scheduler","mail-dispatch","token-key-rotation"]'::jsonb
    WHEN 'token_private' THEN '["mail-dispatch","token-key-rotation"]'::jsonb
    WHEN 'google_oauth' THEN '["web"]'::jsonb
    WHEN 'google_workspace' THEN '["mail-dispatch"]'::jsonb
    WHEN 'parishsoft' THEN '["worker"]'::jsonb
    WHEN 'slack' THEN '["worker"]'::jsonb
    WHEN 'backup_target' THEN '["backup-worker"]'::jsonb
    WHEN 'backup_data' THEN '["backup-worker"]'::jsonb
    WHEN 'metrics' THEN '["web"]'::jsonb
    ELSE '[]'::jsonb END;
$$;

-- FUNCTION: stewardship_credential_deployment_mutable_v1()
CREATE FUNCTION public.stewardship_credential_deployment_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_credential_key_state_mutable_v1()
CREATE FUNCTION public.stewardship_credential_key_state_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."kind" IS DISTINCT FROM OLD."kind" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_credential_scope_v1()
CREATE FUNCTION public.stewardship_credential_scope_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE family_campaign uuid; epoch_campaign uuid; generation_campaign uuid;
BEGIN
    IF TG_TABLE_NAME='stewardship_family_code_mac' THEN
        IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'MAC fingerprints are immutable' USING ERRCODE='23514'; END IF;
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        IF NEW.campaign_id IS DISTINCT FROM family_campaign THEN
            RAISE EXCEPTION 'Fingerprint campaign must match Family' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_family_token' THEN
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        SELECT campaign_id INTO generation_campaign FROM stewardship_family_token_generation WHERE id=NEW.generation_id;
        IF NEW.campaign_id IS DISTINCT FROM family_campaign OR NEW.campaign_id IS DISTINCT FROM generation_campaign THEN
            RAISE EXCEPTION 'Token campaign scopes must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_rehearsal_credential' THEN
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        SELECT campaign_id INTO epoch_campaign FROM stewardship_rehearsal_epoch WHERE id=NEW.epoch_id;
        IF family_campaign IS DISTINCT FROM epoch_campaign THEN
            RAISE EXCEPTION 'Rehearsal Family/campaign scopes must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_rehearsal_code_mac' THEN
        IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'MAC fingerprints are immutable' USING ERRCODE='23514'; END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_rehearsal_credential WHERE id=NEW.credential_id AND epoch_id=NEW.epoch_id) THEN
            RAISE EXCEPTION 'Rehearsal fingerprint scopes must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_family_session' THEN
        IF NEW.mode<>'testing' THEN RETURN NEW; END IF;
        SELECT campaign_id INTO family_campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
        SELECT campaign_id INTO epoch_campaign FROM stewardship_rehearsal_epoch WHERE id=NEW.rehearsal_epoch_id;
        IF family_campaign IS DISTINCT FROM epoch_campaign THEN
            RAISE EXCEPTION 'Family session epoch scope must agree' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_campaign_credentials' THEN
        IF NEW.rehearsal_epoch_id IS NULL THEN RETURN NEW; END IF;
        IF NOT EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch WHERE id=NEW.rehearsal_epoch_id AND campaign_id=NEW.campaign_id AND state='active') THEN
            RAISE EXCEPTION 'Current rehearsal epoch must be active in this campaign' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_daily_fact_set_mutable_v1()
CREATE FUNCTION public.stewardship_daily_fact_set_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."population_scope" IS DISTINCT FROM OLD."population_scope" OR NEW."source_id" IS DISTINCT FROM OLD."source_id" OR NEW."source_generation" IS DISTINCT FROM OLD."source_generation" OR NEW."submission_watermark" IS DISTINCT FROM OLD."submission_watermark" OR NEW."timezone_configuration_id" IS DISTINCT FROM OLD."timezone_configuration_id" OR NEW."through_date" IS DISTINCT FROM OLD."through_date" OR NEW."first_date" IS DISTINCT FROM OLD."first_date" OR NEW."last_date" IS DISTINCT FROM OLD."last_date" OR NEW."expected_count" IS DISTINCT FROM OLD."expected_count" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_domain_rule_immutable_v1()
CREATE FUNCTION public.stewardship_domain_rule_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_download_budget_v1()
CREATE FUNCTION public.stewardship_download_budget_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE slot integer;
BEGIN
    IF TG_OP = 'DELETE' OR NEW.id <> OLD.id OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Download budget requires a versioned operator update'
            USING ERRCODE = '23514';
    END IF;
    -- The row lock serializes new slot claims with resizing. Session-owned
    -- slots cannot be taken over because a timer or heartbeat expired.
    IF EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory'
        AND pid=pg_backend_pid() AND classid=736222 AND objsubid=2 AND granted) THEN
        RAISE EXCEPTION 'A download-owning session cannot resize capacity'
            USING ERRCODE = '23514';
    END IF;
    FOR slot IN 0..31 LOOP
        IF NOT pg_try_advisory_xact_lock(736222, slot) THEN
            RAISE EXCEPTION 'Active downloads prevent capacity changes'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_exceptional_abort_v1()
CREATE FUNCTION public.stewardship_exceptional_abort_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE i stewardship_campaign_config_intent%ROWTYPE; q stewardship_config_request%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; checkpoint_state text;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO i FROM stewardship_campaign_config_intent WHERE id=NEW.intent_id;
    SELECT * INTO q FROM stewardship_config_request WHERE id=i.request_id FOR UPDATE;
    SELECT state INTO checkpoint_state FROM stewardship_config_checkpoint WHERE request_id=q.id ORDER BY sequence DESC LIMIT 1;
    IF q.id IS NULL OR r.active_configuration_id IS DISTINCT FROM q.base_id OR NEW.actor_id IS NULL OR btrim(NEW.reason)=''
       OR checkpoint_state IS NULL OR checkpoint_state NOT IN ('validating','prepared','yaml_activated')
       OR NOT EXISTS(SELECT 1 FROM stewardship_configuration_version v WHERE v.id=q.candidate_version_id
           AND v.digest=q.candidate_digest AND v.predecessor_id=q.base_id)
       OR EXISTS(SELECT 1 FROM stewardship_config_activation WHERE request_id=q.id) THEN
        RAISE EXCEPTION 'Only unapplied exceptional configuration can be aborted' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_fact_compaction_guard()
CREATE FUNCTION public.stewardship_fact_compaction_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE target stewardship_daily_fact_set%ROWTYPE;
BEGIN
    SELECT * INTO target FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF NOT FOUND OR NOT stewardship_fact_disposable(target.id)
        OR NOT stewardship_fact_live(NEW.task_id,NEW.task_fence,NEW.worker_id)
        OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_generation,
            NEW.submission_watermark,NEW.timezone_configuration_id,
            NEW.through_date,NEW.row_count) IS DISTINCT FROM
            ROW(target.campaign_id,target.population_scope,target.source_generation,
            target.submission_watermark,target.timezone_configuration_id,
            target.through_date,target.expected_count) THEN
        RAISE EXCEPTION 'Fact cleanup evidence requires exact disposable inputs'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_fact_compaction_immutable_v1()
CREATE FUNCTION public.stewardship_fact_compaction_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_fact_compaction_pair_guard()
CREATE FUNCTION public.stewardship_fact_compaction_pair_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_TABLE_NAME='stewardship_daily_fact_set' THEN
        IF NOT EXISTS(SELECT 1 FROM stewardship_fact_compaction
            WHERE fact_set_id=OLD.id) THEN
            RAISE EXCEPTION 'Fact deletion requires retained cleanup evidence'
                USING ERRCODE='23514';
        END IF;
    ELSIF EXISTS(SELECT 1 FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id) THEN
        RAISE EXCEPTION 'Fact cleanup evidence requires complete deletion'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_fact_day_guard()
CREATE FUNCTION public.stewardship_fact_day_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE parent stewardship_daily_fact_set%ROWTYPE;
        source stewardship_source_snapshot%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'Fact rows are immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN
        SELECT * INTO parent FROM stewardship_daily_fact_set
            WHERE id=OLD.fact_set_id FOR UPDATE;
        IF NOT pg_try_advisory_xact_lock(736231, hashtext(OLD.fact_set_id::text))
            OR NOT stewardship_fact_disposable(parent.id) THEN
            RAISE EXCEPTION 'Fact rows are protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    SELECT * INTO parent FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF parent.state IS DISTINCT FROM 'building' OR NOT
        stewardship_fact_live(parent.task_id,parent.task_fence,parent.worker_id)
        OR parent.expected_count=0 OR NEW.local_date<parent.first_date
        OR NEW.local_date>parent.last_date THEN
        RAISE EXCEPTION 'Fact row is outside its owned building generation'
            USING ERRCODE='23514';
    END IF;
    IF NEW.population_available THEN
        SELECT * INTO source FROM stewardship_source_snapshot
            WHERE generation=NEW.source_generation AND state='promoted';
        IF NOT FOUND OR source.promoted_at IS DISTINCT FROM NEW.source_as_of
            OR source.generation>parent.source_generation
            OR (parent.population_scope='current' AND source.id<>parent.source_id) THEN
            RAISE EXCEPTION 'Fact row source cutoff is inconsistent'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_fact_delete_pair_guard()
CREATE FUNCTION public.stewardship_fact_delete_pair_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_daily_fact_set WHERE id=OLD.fact_set_id) THEN
        RAISE EXCEPTION 'Fact compaction must remove a whole disposable generation'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_fact_demand_guard()
CREATE FUNCTION public.stewardship_fact_demand_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE requested_changed boolean;
        claim_changed boolean;
        target stewardship_daily_fact_set%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Fact demand requires an owning retention workflow'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stewardship_source_snapshot
        WHERE id=NEW.requested_source_id AND state='promoted'
            AND generation=NEW.requested_source_generation)
        OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration
            WHERE id=NEW.requested_timezone_configuration_id
                AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Fact demand inputs are inconsistent' USING ERRCODE='23514';
    END IF;
    IF NEW.pending_due_at IS NOT NULL AND NEW.pending_due_at IS DISTINCT FROM
        LEAST(NEW.pending_last_at+interval '5 seconds',
              NEW.pending_first_at+interval '30 seconds') THEN
        RAISE EXCEPTION 'Fact demand debounce window is invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.pending_revision<>1 OR NEW.pending_first_at IS NULL
            OR NEW.pending_first_at<>NEW.pending_last_at
            OR NEW.claimed_revision<>0 OR NEW.claimed_generation_id IS NOT NULL THEN
            RAISE EXCEPTION 'Fact demand must begin pending' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    requested_changed := ROW(NEW.requested_source_id,
        NEW.requested_source_generation,NEW.requested_submission_watermark,
        NEW.requested_timezone_configuration_id,NEW.requested_through_date)
        IS DISTINCT FROM ROW(OLD.requested_source_id,
        OLD.requested_source_generation,OLD.requested_submission_watermark,
        OLD.requested_timezone_configuration_id,OLD.requested_through_date);
    claim_changed := ROW(NEW.claimed_revision,NEW.claimed_generation_id,
        NEW.claimed_task_id,NEW.claimed_task_fence,NEW.claimed_worker_id)
        IS DISTINCT FROM ROW(OLD.claimed_revision,OLD.claimed_generation_id,
        OLD.claimed_task_id,OLD.claimed_task_fence,OLD.claimed_worker_id);
    IF requested_changed AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_campaign WHERE id=NEW.campaign_id
                AND active_configuration_id=NEW.requested_timezone_configuration_id
        ) THEN
            RAISE EXCEPTION 'Fact demand requires current campaign inputs'
                USING ERRCODE='23514';
        END IF;
    IF requested_changed THEN
        IF claim_changed OR NEW.pending_revision<>OLD.pending_revision+1
            OR NEW.requested_source_generation<OLD.requested_source_generation
            OR NEW.requested_submission_watermark<OLD.requested_submission_watermark
            OR (NEW.requested_timezone_configuration_id=OLD.requested_timezone_configuration_id
                AND NEW.requested_through_date<OLD.requested_through_date)
            OR NEW.pending_first_at IS DISTINCT FROM
                COALESCE(OLD.pending_first_at,NEW.pending_last_at)
            OR NEW.pending_last_at IS NULL
            OR NEW.pending_last_at<OLD.pending_last_at THEN
            RAISE EXCEPTION 'Fact event must preserve independent claimed work'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.pending_revision<>OLD.pending_revision OR NOT claim_changed THEN
        RAISE EXCEPTION 'Fact demand change requires a new event or claim transition'
            USING ERRCODE='23514';
    ELSIF OLD.claimed_generation_id IS NULL THEN
        SELECT * INTO target FROM stewardship_daily_fact_set
            WHERE id=NEW.claimed_generation_id FOR UPDATE;
        IF NEW.claimed_revision<>OLD.pending_revision
            OR OLD.pending_due_at IS NULL OR OLD.pending_due_at>clock_timestamp()
            OR NEW.pending_first_at IS NOT NULL
            OR NOT stewardship_fact_live(NEW.claimed_task_id,
                NEW.claimed_task_fence,NEW.claimed_worker_id)
            OR ROW(target.campaign_id,target.population_scope,target.source_id,
                target.submission_watermark,target.timezone_configuration_id,
                target.through_date) IS DISTINCT FROM ROW(NEW.campaign_id,
                NEW.population_scope,NEW.requested_source_id,
                NEW.requested_submission_watermark,
                NEW.requested_timezone_configuration_id,NEW.requested_through_date)
            OR (target.state<>'ready' AND (target.state<>'building' OR
                ROW(target.task_id,target.task_fence,target.worker_id)
                IS DISTINCT FROM ROW(NEW.claimed_task_id,
                    NEW.claimed_task_fence,NEW.claimed_worker_id))) THEN
            RAISE EXCEPTION 'Fact claim must freeze exactly its due input window'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.claimed_generation_id IS NOT NULL THEN
        SELECT * INTO target FROM stewardship_daily_fact_set
            WHERE id=NEW.claimed_generation_id FOR UPDATE;
        IF NEW.claimed_generation_id<>OLD.claimed_generation_id
            OR NEW.claimed_revision<>OLD.claimed_revision
            OR ROW(NEW.pending_first_at,NEW.pending_last_at,NEW.pending_due_at)
                IS DISTINCT FROM
                ROW(OLD.pending_first_at,OLD.pending_last_at,OLD.pending_due_at)
            OR stewardship_fact_live(OLD.claimed_task_id,
                OLD.claimed_task_fence,OLD.claimed_worker_id)
            OR NOT stewardship_fact_live(NEW.claimed_task_id,
                NEW.claimed_task_fence,NEW.claimed_worker_id)
            OR (target.state<>'ready' AND (target.state<>'building' OR
                ROW(target.task_id,target.task_fence,target.worker_id)
                IS DISTINCT FROM ROW(NEW.claimed_task_id,
                    NEW.claimed_task_fence,NEW.claimed_worker_id))) THEN
            RAISE EXCEPTION 'Fact recovery must transfer only abandoned exact work'
                USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.claimed_generation_id IS NOT NULL
            OR NEW.claimed_revision<>OLD.claimed_revision
            OR ROW(NEW.pending_first_at,NEW.pending_last_at,NEW.pending_due_at)
                IS DISTINCT FROM
                ROW(OLD.pending_first_at,OLD.pending_last_at,OLD.pending_due_at)
            OR NOT stewardship_fact_live(OLD.claimed_task_id,
                OLD.claimed_task_fence,OLD.claimed_worker_id)
            OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set
                WHERE id=OLD.claimed_generation_id AND state='ready') THEN
            RAISE EXCEPTION 'Fact completion must preserve newer pending demand'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_fact_demand_mutable_v1()
CREATE FUNCTION public.stewardship_fact_demand_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."population_scope" IS DISTINCT FROM OLD."population_scope" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_fact_disposable(uuid)
CREATE FUNCTION public.stewardship_fact_disposable(identifier uuid) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_fact_set old
        JOIN stewardship_daily_fact_set newer ON newer.campaign_id=old.campaign_id
            AND newer.population_scope=old.population_scope AND newer.state='ready'
            AND newer.id<>old.id AND newer.created_at>=old.created_at
            AND newer.source_generation>=old.source_generation
            AND newer.submission_watermark>=old.submission_watermark
            AND ((newer.timezone_configuration_id=old.timezone_configuration_id
                    AND newer.through_date>=old.through_date)
                OR (newer.timezone_configuration_id<>old.timezone_configuration_id
                    AND EXISTS (SELECT 1 FROM public.stewardship_campaign c
                        WHERE c.id=newer.campaign_id
                            AND c.active_configuration_id=newer.timezone_configuration_id)))
        WHERE old.id=identifier AND old.state='ready')
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pointer WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
        JOIN stewardship_task_run t ON t.root_id=r.task_id
        WHERE r.fact_set_id=identifier
            AND t.state IN ('queued','running','retry_wait','abandoned'))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date))
    AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand
        WHERE claimed_generation_id=identifier);
$$;

-- FUNCTION: stewardship_fact_live(uuid, bigint, uuid)
CREATE FUNCTION public.stewardship_fact_live(task uuid, fence bigint, worker uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=$1
        AND t.state='running' AND t.fence=$2 AND t.worker_id=$3
        AND t.lease_expires_at > clock_timestamp());
$_$;

-- FUNCTION: stewardship_fact_pin_mutable_v1()
CREATE FUNCTION public.stewardship_fact_pin_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."fact_set_id" IS DISTINCT FROM OLD."fact_set_id" OR NEW."parent_kind" IS DISTINCT FROM OLD."parent_kind" OR NEW."parent_id" IS DISTINCT FROM OLD."parent_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_fact_pointer_mutable_v1()
CREATE FUNCTION public.stewardship_fact_pointer_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."population_scope" IS DISTINCT FROM OLD."population_scope" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_fact_reference_guard()
CREATE FUNCTION public.stewardship_fact_reference_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE target stewardship_daily_fact_set%ROWTYPE;
        prior stewardship_daily_fact_set%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    SELECT * INTO target FROM stewardship_daily_fact_set
        WHERE id=NEW.fact_set_id FOR UPDATE;
    IF target.state IS DISTINCT FROM 'ready' THEN
        RAISE EXCEPTION 'Fact reference requires a retained ready generation'
            USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_fact_pointer' THEN
        IF target.campaign_id<>NEW.campaign_id
            OR target.population_scope<>NEW.population_scope
            OR NOT EXISTS(SELECT 1 FROM stewardship_campaign
                WHERE id=NEW.campaign_id
                    AND active_configuration_id=target.timezone_configuration_id) THEN
            RAISE EXCEPTION 'Fact pointer requires current campaign inputs'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' THEN
            SELECT * INTO prior FROM stewardship_daily_fact_set
                WHERE id=OLD.fact_set_id;
            IF target.source_generation<prior.source_generation
                OR target.submission_watermark<prior.submission_watermark
                OR (target.timezone_configuration_id=prior.timezone_configuration_id
                    AND target.through_date<prior.through_date) THEN
                RAISE EXCEPTION 'Fact pointer cannot regress' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_fact_set_guard()
CREATE FUNCTION public.stewardship_fact_set_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE projection stewardship_campaign_configuration%ROWTYPE;
        input_source stewardship_source_snapshot%ROWTYPE;
        first_day date;
        last_day date;
        expected integer;
        actual bigint;
BEGIN
    IF TG_OP='DELETE' THEN
        IF NOT pg_try_advisory_xact_lock(736231, hashtext(OLD.id::text))
            OR NOT stewardship_fact_disposable(OLD.id) THEN
            RAISE EXCEPTION 'Fact generation is protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NOT stewardship_fact_live(NEW.task_id,NEW.task_fence,NEW.worker_id) THEN
        RAISE EXCEPTION 'Fact generation requires its live fenced builder'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.state<>'building' THEN
        RAISE EXCEPTION 'Fact generation must begin building' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.state='ready' OR NOT (
            (OLD.state='building' AND NEW.state IN ('building','ready','failed'))
            OR (OLD.state='failed' AND NEW.state='building')) THEN
            RAISE EXCEPTION 'Fact generation transition is invalid'
                USING ERRCODE='23514';
        END IF;
        IF ROW(NEW.task_id,NEW.task_fence,NEW.worker_id)
            IS DISTINCT FROM ROW(OLD.task_id,OLD.task_fence,OLD.worker_id)
            AND stewardship_fact_live(OLD.task_id,OLD.task_fence,OLD.worker_id) THEN
            RAISE EXCEPTION 'Live fact ownership cannot be replaced'
                USING ERRCODE='23514';
        END IF;
    END IF;
    SELECT * INTO projection FROM stewardship_campaign_configuration
        WHERE id=NEW.timezone_configuration_id;
    SELECT * INTO input_source FROM stewardship_source_snapshot WHERE id=NEW.source_id;
    IF projection.record_id IS DISTINCT FROM NEW.campaign_id
        OR input_source.state IS DISTINCT FROM 'promoted'
        OR input_source.generation IS DISTINCT FROM NEW.source_generation THEN
        RAISE EXCEPTION 'Fact inputs are inconsistent' USING ERRCODE='23514';
    END IF;
    last_day := LEAST(projection.end_date,NEW.through_date);
    first_day := projection.start_date;
    expected := GREATEST(0,last_day-first_day+1);
    IF expected=0 THEN first_day:=NULL; last_day:=NULL; END IF;
    IF NEW.expected_count<>expected OR NEW.first_date IS DISTINCT FROM first_day
        OR NEW.last_date IS DISTINCT FROM last_day THEN
        RAISE EXCEPTION 'Fact date bounds differ from frozen campaign inputs'
            USING ERRCODE='23514';
    END IF;
    IF NEW.population_scope='current' AND (
        input_source.compacted_at IS NOT NULL OR NOT EXISTS(
            SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=NEW.source_id
                AND parent_kind='facts' AND parent_id=NEW.id)) THEN
        RAISE EXCEPTION 'Current facts require a retained exact source input'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state='ready' THEN
        SELECT count(*) INTO actual FROM stewardship_daily_fact
            WHERE fact_set_id=NEW.id;
        IF actual<>NEW.expected_count OR NEW.ready_at > clock_timestamp()
            OR NEW.failure_code<>'' OR EXISTS (
                SELECT 1 FROM (
                    SELECT cumulative_responses, sum(first_responses) OVER (
                        ORDER BY local_date) AS expected_cumulative
                    FROM stewardship_daily_fact WHERE fact_set_id=NEW.id
                ) counts WHERE cumulative_responses<>expected_cumulative
            ) THEN
            RAISE EXCEPTION 'Fact generation is incomplete or inconsistent'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_fact_source_pin_guard()
CREATE FUNCTION public.stewardship_fact_source_pin_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.parent_kind='facts' AND EXISTS(SELECT 1 FROM stewardship_daily_fact_set
        WHERE id=OLD.parent_id AND source_id=OLD.snapshot_id) THEN
        RAISE EXCEPTION 'Retained facts still require their exact source input'
            USING ERRCODE='23514';
    END IF;
    RETURN OLD;
END;
$$;

-- FUNCTION: stewardship_family_campaign_mutable_v1()
CREATE FUNCTION public.stewardship_family_campaign_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."family_duid" IS DISTINCT FROM OLD."family_duid" OR (OLD."first_eligible_at" IS NOT NULL AND NEW."first_eligible_at" IS DISTINCT FROM OLD."first_eligible_at") OR (OLD."first_eligible_source_generation" IS NOT NULL AND NEW."first_eligible_source_generation" IS DISTINCT FROM OLD."first_eligible_source_generation") OR (OLD."first_live_submission_id" IS NOT NULL AND NEW."first_live_submission_id" IS DISTINCT FROM OLD."first_live_submission_id") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_family_code_mac_immutable_v1()
CREATE FUNCTION public.stewardship_family_code_mac_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_family_cohort_v1()
CREATE FUNCTION public.stewardship_family_cohort_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF TG_OP='UPDATE' AND (NEW.source_generation<OLD.source_generation
       OR NEW.eligibility_changed_at<OLD.eligibility_changed_at
       OR (OLD.code_ciphertext IS NOT NULL AND NEW.code_ciphertext IS NULL)) THEN
        RAISE EXCEPTION 'Family identity cannot rewind source/cohort/code state' USING ERRCODE='23514';
    END IF;
    IF NEW.first_eligible_at IS NOT NULL AND (NEW.first_eligible_at>NEW.eligibility_changed_at
       OR NEW.first_eligible_source_generation>NEW.source_generation) THEN
        RAISE EXCEPTION 'Invalid Family cohort provenance' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_family_eligibility_immutable_v1()
CREATE FUNCTION public.stewardship_family_eligibility_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_family_history_v1()
CREATE FUNCTION public.stewardship_family_history_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF NEW.status_reason='population_pending' THEN RETURN NEW; END IF;
    IF TG_OP='INSERT' OR (NEW.active,NEW.portal_eligible,NEW.email_eligible,NEW.email_deliverable,NEW.status_reason,NEW.deliverability_reason)
       IS DISTINCT FROM (OLD.active,OLD.portal_eligible,OLD.email_eligible,OLD.email_deliverable,OLD.status_reason,OLD.deliverability_reason) THEN
        INSERT INTO stewardship_family_eligibility(id,created_at,actor_id,correlation_id,family_id,family_version,source_generation,
            active,portal_eligible,email_eligible,email_deliverable,status_reason,deliverability_reason)
        VALUES(gen_random_uuid(),statement_timestamp(),NEW.actor_id,NEW.correlation_id,NEW.id,NEW.version,NEW.source_generation,
            NEW.active,NEW.portal_eligible,NEW.email_eligible,NEW.email_deliverable,NEW.status_reason,NEW.deliverability_reason);
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_family_presence_v1()
CREATE FUNCTION public.stewardship_family_presence_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE instant timestamptz := clock_timestamp();
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.presence_at IS NOT NULL OR NEW.presence_section<>'' THEN
            RAISE EXCEPTION 'Presence starts after session authentication'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.presence_at, NEW.presence_section) IS NOT DISTINCT FROM
       (OLD.presence_at, OLD.presence_section) THEN
        RETURN NEW;
    END IF;
    IF NEW.presence_at IS NULL OR NEW.revoked_at IS NOT NULL
       OR (NEW.last_activity_at, NEW.last_keepalive_at, NEW.expires_at)
          IS DISTINCT FROM
          (OLD.last_activity_at, OLD.last_keepalive_at, OLD.expires_at)
       OR instant >= OLD.expires_at
       OR instant >= OLD.last_activity_at + interval '60 minutes'
       OR (OLD.presence_at IS NOT NULL
           AND instant < OLD.presence_at + interval '30 seconds') THEN
        RAISE EXCEPTION 'Presence cannot renew or outlive its Family session'
            USING ERRCODE='23514';
    END IF;
    NEW.presence_at := instant;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_family_session_epoch_v1()
CREATE FUNCTION public.stewardship_family_session_epoch_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.credential_epoch IS DISTINCT FROM OLD.credential_epoch THEN
            RAISE EXCEPTION 'Family session epoch is immutable' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(SELECT 1 FROM stewardship_credential_deployment WHERE family_link_epoch=NEW.credential_epoch) THEN
        RAISE EXCEPTION 'Family session requires the current deployment epoch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_family_session_mutable_v1()
CREATE FUNCTION public.stewardship_family_session_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."family_id" IS DISTINCT FROM OLD."family_id" OR NEW."mode" IS DISTINCT FROM OLD."mode" OR NEW."rehearsal_epoch_id" IS DISTINCT FROM OLD."rehearsal_epoch_id" OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at" OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at" OR NEW."credential_epoch" IS DISTINCT FROM OLD."credential_epoch" OR (OLD."revoked_at" IS NOT NULL AND NEW."revoked_at" IS DISTINCT FROM OLD."revoked_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_family_token_generation_mutable_v1()
CREATE FUNCTION public.stewardship_family_token_generation_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."operation_id" IS DISTINCT FROM OLD."operation_id" OR NEW."credential_epoch" IS DISTINCT FROM OLD."credential_epoch" OR NEW."restore_id" IS DISTINCT FROM OLD."restore_id" OR NEW."preparation_revision" IS DISTINCT FROM OLD."preparation_revision" OR NEW."source_snapshot_id" IS DISTINCT FROM OLD."source_snapshot_id" OR NEW."source_generation" IS DISTINCT FROM OLD."source_generation" OR NEW."configuration_id" IS DISTINCT FROM OLD."configuration_id" OR NEW."key_id" IS DISTINCT FROM OLD."key_id" OR NEW."key_inventory_digest" IS DISTINCT FROM OLD."key_inventory_digest" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_family_token_mutable_v1()
CREATE FUNCTION public.stewardship_family_token_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."family_id" IS DISTINCT FROM OLD."family_id" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."generation_id" IS DISTINCT FROM OLD."generation_id" OR (OLD."destroyed_at" IS NOT NULL AND NEW."destroyed_at" IS DISTINCT FROM OLD."destroyed_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_fulfillment_guard_v1()
CREATE FUNCTION public.stewardship_fulfillment_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Keep weekly private metadata out of query plans for unrelated mail roles.
    IF NEW.disposition='empty' AND EXISTS(SELECT 1 FROM stewardship_schedule_definition
        WHERE id=NEW.definition_id AND kind='weekly_digest') THEN
        IF NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot=''
          OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
            JOIN stewardship_weekly_digest_preparation p ON p.occurrence_id=o.id
            JOIN stewardship_weekly_digest_completion_ready proof ON proof.preparation_id=p.id
            WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
              AND o.mode=NEW.mode AND o.target=NEW.target AND o.slot=NEW.slot
              AND o.state='succeeded' AND proof.disposition='empty' AND o.reason=proof.empty_reason) THEN
            RAISE EXCEPTION 'Fulfillment requires exact semantic outcome evidence' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot='' OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence o JOIN stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN stewardship_schedule_definition wanted ON wanted.id=NEW.definition_id
        WHERE o.id=NEW.occurrence_id AND o.mode=NEW.mode AND d.campaign_id=wanted.campaign_id
          AND ((NEW.disposition='delivered' AND o.state='succeeded'
                AND o.reason IS DISTINCT FROM 'daily_digest_no_current_recipients'
                AND o.reason IS DISTINCT FROM 'weekly_digest_empty'
                AND o.reason IS DISTINCT FROM 'weekly_digest_no_current_recipients'
                AND o.definition_id=NEW.definition_id AND o.target=NEW.target AND o.slot=NEW.slot)
            OR (NEW.disposition='empty' AND d.kind='daily_digest' AND o.state='succeeded'
                AND o.reason='daily_digest_no_current_recipients'
                AND o.definition_id=NEW.definition_id AND o.target=NEW.target AND o.slot=NEW.slot
                AND EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation p
                    JOIN stewardship_daily_digest_completion_ready proof ON proof.preparation_id=p.id
                    WHERE p.occurrence_id=o.id AND proof.disposition='empty'))
            OR (NEW.disposition='coalesced' AND EXISTS (SELECT 1 FROM stewardship_schedule_occurrence original
                WHERE original.definition_id=NEW.definition_id AND original.mode=NEW.mode AND original.target=NEW.target AND original.slot=NEW.slot
                  AND original.state='coalesced' AND original.replacement_id=o.id)))
    ) THEN RAISE EXCEPTION 'Fulfillment requires exact semantic outcome evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_limiter_health_mutable_v1()
CREATE FUNCTION public.stewardship_limiter_health_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."namespace_fingerprint" IS DISTINCT FROM OLD."namespace_fingerprint" OR NEW."marker" IS DISTINCT FROM OLD."marker" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_ministry_activation_v1()
CREATE FUNCTION public.stewardship_ministry_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE before_records jsonb; after_records jsonb; snapshot uuid; seeded boolean;
BEGIN
    SELECT coalesce(canonical_document->'sections'->'ministries','[]'::jsonb)
        INTO before_records FROM stewardship_configuration_version
        WHERE id=NEW.predecessor_id;
    SELECT coalesce(canonical_document->'sections'->'ministries','[]'::jsonb)
        INTO after_records FROM stewardship_configuration_version
        WHERE id=NEW.configuration_id;
    SELECT snapshot_id INTO snapshot FROM stewardship_source_current;
    seeded := EXISTS (SELECT 1 FROM stewardship_ministry_assignment
        WHERE configuration_id=NEW.configuration_id AND source='chair-seed');
    IF snapshot IS NULL THEN
        IF seeded AND coalesce(before_records,'[]'::jsonb)
            IS DISTINCT FROM after_records THEN
            RAISE EXCEPTION 'Ministry activity requires seeded reconciliation'
                USING ERRCODE='23514';
        END IF;
    ELSIF seeded OR EXISTS (
        SELECT 1 FROM stewardship_chair_review WHERE closed_by_id IS NULL
    ) OR EXISTS (SELECT 1 FROM stewardship_chair_reconciliation
                 WHERE activation_id=NEW.id) THEN
        PERFORM stewardship_require_chair_receipt_v1(NEW.configuration_id,snapshot);
    END IF;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_ministry_activity_immutable_v1()
CREATE FUNCTION public.stewardship_ministry_activity_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_ministry_assignment_immutable_v1()
CREATE FUNCTION public.stewardship_ministry_assignment_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_ministry_complete_v1()
CREATE FUNCTION public.stewardship_ministry_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE records jsonb; actual jsonb; vals jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'ministries', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8') THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Ministry activity requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid Ministry activity shape' USING ERRCODE='23514';
    END IF;
    FOR vals IN SELECT item->'values' FROM jsonb_array_elements(records) item LOOP
        IF jsonb_typeof(vals->'organization_id') IS DISTINCT FROM 'number'
           OR jsonb_typeof(vals->'ministry_duid') IS DISTINCT FROM 'number'
           OR vals->>'organization_id' !~ '^[1-9][0-9]*$'
           OR vals->>'ministry_duid' !~ '^[1-9][0-9]*$'
           OR jsonb_typeof(vals->'active') IS DISTINCT FROM 'boolean' THEN
            RAISE EXCEPTION 'Invalid Ministry activity types' USING ERRCODE='23514';
        END IF;
    END LOOP;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id,
        'values', jsonb_build_object('organization_id', organization_id,
             'ministry_duid', ministry_duid, 'active', active)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_ministry_activity WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Ministry activity projections are incomplete'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $_$;

-- FUNCTION: stewardship_ministry_projection_v1()
CREATE FUNCTION public.stewardship_ministry_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'ministries') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8')
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object(
        'id', NEW.record_id, 'values', jsonb_build_object(
            'organization_id', NEW.organization_id,
            'ministry_duid', NEW.ministry_duid, 'active', NEW.active)) THEN
        RAISE EXCEPTION 'Ministry activity differs from YAML' USING ERRCODE='23514';
    END IF;
    SELECT predecessor_id INTO predecessor
    FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF EXISTS (
        WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM public.stewardship_configuration_version
            WHERE id=predecessor
            UNION
            SELECT p.id, p.predecessor_id
            FROM public.stewardship_configuration_version p
            JOIN chain c ON p.id=c.predecessor_id
        ) SELECT 1 FROM public.stewardship_ministry_activity m
          JOIN chain c ON m.configuration_id=c.id
          WHERE (m.record_id=NEW.record_id AND
                 (m.organization_id, m.ministry_duid) IS DISTINCT FROM
                 (NEW.organization_id, NEW.ministry_duid))
             OR (m.record_id<>NEW.record_id AND
                 m.organization_id=NEW.organization_id AND
                 m.ministry_duid=NEW.ministry_duid)
    ) THEN
        RAISE EXCEPTION 'Ministry activity identities must remain stable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- One pure predicate shared by write admission and replacement inventory.
-- A later truthful provider result is never suppressed by this occurrence rule.
CREATE FUNCTION public.stewardship_occurrence_delivery_conflict_v1(
    occurrence_state text,message_state text
) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT ((occurrence_state IN ('succeeded','skipped','coalesced')
                AND message_state IN ('pending','retry_wait'))
        OR (message_state='delivered' AND occurrence_state<>'succeeded')
        OR (message_state='permanent_failure' AND occurrence_state<>'failed')
        OR (message_state='cancelled' AND occurrence_state='succeeded')) IS TRUE
$$;

-- Verify a digest cursor against one exact, at-most-100-date page. Final
-- receipts require the exhausted page chain and settled original outcomes.
-- This checks retained data, not trust in a predictable checkpoint key.
CREATE FUNCTION public.stewardship_catchup_digest_receipt_v1(proposed jsonb)
RETURNS boolean LANGUAGE plpgsql
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand public.stewardship_activation_catchup%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE;
        d public.stewardship_schedule_definition%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE;
        prefix text; position text; stage text; after_day text; candidate date;
        last_day text; due timestamptz; exhausted boolean:=false; step integer;
        pending_count integer; i integer;
BEGIN
    SELECT * INTO demand FROM public.stewardship_activation_catchup WHERE id=(proposed->>'demand_id')::uuid;
    SELECT configuration.* INTO p FROM public.stewardship_campaign c
        JOIN public.stewardship_campaign_configuration configuration ON configuration.id=c.active_configuration_id
        WHERE c.id=demand.campaign_id;
    IF p.id IS NULL THEN RETURN false; END IF;
    prefix:=replace(p.id::text,'-','')||':';
    IF proposed->>'group_key'=prefix||'families:end' THEN
        RETURN proposed->>'cursor'=prefix||'digests:' AND (proposed->>'items')::bigint=0
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_family_campaign f
                WHERE f.campaign_id=demand.campaign_id AND f.created_at<=demand.created_at
                  AND NOT EXISTS(SELECT 1 FROM public.stewardship_catchup_checkpoint k
                      WHERE k.demand_id=demand.id AND k.group_key=prefix||'family:'||f.id::text));
    END IF;
    IF NOT starts_with(demand.cursor,prefix||'digests:') THEN RETURN false; END IF;
    SELECT * INTO d FROM public.stewardship_schedule_definition s
        WHERE s.campaign_id=demand.campaign_id AND s.current_revision_id IS NOT NULL
            AND s.kind IN ('daily_digest','weekly_digest')
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_catchup_checkpoint k
                WHERE k.demand_id=demand.id AND k.group_key=prefix||'digest:'||s.id::text)
        ORDER BY s.id LIMIT 1;
    IF d.id IS NULL THEN RETURN false; END IF;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=d.current_revision_id;
    position:=substring(demand.cursor FROM length(prefix||'digests:')+1);
    IF position='' THEN stage:='dates'; after_day:='';
    ELSE
        IF split_part(position,':',1)<>replace(d.id::text,'-','') THEN RETURN false; END IF;
        stage:=split_part(position,':',2); after_day:=split_part(position,':',3);
    END IF;
    IF stage='dates' THEN
        candidate:=greatest(p.start_date,CASE WHEN after_day<>'' THEN after_day::date+1 ELSE p.start_date END);
        step:=CASE d.kind WHEN 'weekly_digest' THEN 7 ELSE 1 END;
        IF step=7 THEN candidate:=candidate+((v.values->>'weekday')::integer
            -(extract(isodow FROM candidate)::integer-1)+7)%7; END IF;
        last_day:=after_day;
        FOR i IN 1..100 LOOP
            IF candidate>p.end_date THEN exhausted:=true; EXIT; END IF;
            due:=public.stewardship_resolve_local_v1(
                (candidate+CASE d.kind WHEN 'daily_digest' THEN 1 ELSE 0 END)
                    +(v.values->>'time')::time,p.timezone);
            IF due>least(demand.cutoff,public.stewardship_campaign_now_v1()) THEN exhausted:=true; EXIT; END IF;
            last_day:=candidate::text;
            IF public.stewardship_catchup_due_v1(d.id,last_day) IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins'
                        AND o.slot=last_day AND o.due_at=due)
                AND NOT public.stewardship_schedule_slot_excluded_v1(d.id,'production','admins',last_day)
                THEN RETURN false; END IF;
            candidate:=candidate+step;
        END LOOP;
        stage:=CASE WHEN exhausted THEN 'cover' ELSE 'dates' END;
        RETURN proposed->>'group_key'=prefix||'page:'||d.id::text||':'||coalesce(nullif(last_day,''),'empty')||':'||stage
            AND proposed->>'cursor'=prefix||'digests:'||replace(d.id::text,'-','')||':'||stage||':'||last_day;
    END IF;
    IF stage<>'cover' THEN RETURN false; END IF;
    IF proposed->>'group_key'=prefix||'cover:'||d.id::text||':'||(demand.groups_completed+1)::text THEN
        RETURN proposed->>'cursor'=demand.cursor AND (proposed->>'items')::bigint>0;
    END IF;
    IF proposed->>'group_key'<>prefix||'digest:'||d.id::text
        OR proposed->>'cursor'<>prefix||'digests:' THEN RETURN false; END IF;
    -- No bound or uncertain original can be treated as prepared. Exactly one
    -- unbound selection may remain; every other date needs a terminal outcome.
    IF EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins' AND o.due_at<=demand.cutoff
            AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
            AND (o.state IN ('running','delivery_unknown')
                OR (o.state='pending' AND (o.task_id IS NOT NULL OR o.outbox_id IS NOT NULL)))) THEN RETURN false; END IF;
    SELECT count(*) INTO pending_count FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins' AND o.due_at<=demand.cutoff
            AND o.state='pending'
            AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot);
    RETURN pending_count<=1 AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins'
            AND o.due_at<=demand.cutoff AND o.state='coalesced'
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                WHERE f.definition_id=d.id AND f.mode=o.mode AND f.target=o.target AND f.slot=o.slot
                    AND f.disposition='coalesced' AND f.occurrence_id=o.replacement_id)
    ) AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.definition_id=d.id AND o.mode='production' AND o.target='admins'
            AND o.revision_id<>v.id AND o.state='skipped' AND o.reason='schedule_replaced'
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e WHERE e.previous_id=o.id)
            AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                    WHERE f.definition_id=d.id AND f.mode='production' AND f.target='admins'
                        AND f.occurrence_id=o.id AND f.disposition='coalesced')
                OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e
                    WHERE e.demand_id=demand.id AND e.replacement_id=o.id)));
END $$;

-- Canonical slot validation is shared by worker writes and bounded page proofs.
CREATE FUNCTION public.stewardship_catchup_due_v1(definition uuid,slot text)
RETURNS timestamptz LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE d public.stewardship_schedule_definition%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE; day date;
BEGIN
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=$1;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=d.current_revision_id;
    SELECT configuration.* INTO p FROM public.stewardship_campaign c
        JOIN public.stewardship_campaign_configuration configuration ON configuration.id=c.active_configuration_id
        WHERE c.id=d.campaign_id;
    IF v.id IS NULL OR p.id IS NULL THEN RETURN NULL; END IF;
    IF d.kind IN ('initial','reminder') THEN
        RETURN CASE WHEN slot='once' THEN v.due_at END;
    END IF;
    IF slot !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN NULL; END IF;
    day:=slot::date;
    IF day<p.start_date OR day>p.end_date OR day::text<>slot THEN RETURN NULL; END IF;
    IF d.kind='weekly_digest' THEN
        IF extract(isodow FROM day)::integer-1<>(v.values->>'weekday')::integer THEN RETURN NULL; END IF;
    ELSIF d.kind='daily_digest' THEN
        IF public.stewardship_resolve_local_v1(day::timestamp,p.timezone)
            =public.stewardship_resolve_local_v1((day+1)::timestamp,p.timezone) THEN RETURN NULL; END IF;
        day:=day+1;
    ELSE RETURN NULL;
    END IF;
    RETURN public.stewardship_resolve_local_v1(day+(v.values->>'time')::time,p.timezone);
END $$;

CREATE FUNCTION public.stewardship_catchup_occurrence_shape_v1(proposed jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE d public.stewardship_schedule_definition%ROWTYPE;
        demand public.stewardship_activation_catchup%ROWTYPE;
        c public.stewardship_campaign%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE;
        family public.stewardship_family_campaign%ROWTYPE;
        selected public.stewardship_schedule_occurrence%ROWTYPE;
        selected_kind text; expected timestamptz; reason text;
BEGIN
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=(proposed->>'definition_id')::uuid;
    SELECT * INTO demand FROM public.stewardship_activation_catchup WHERE campaign_id=d.campaign_id AND completed_at IS NULL;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO p FROM public.stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    IF d.id IS NULL OR demand.id IS NULL OR p.id IS NULL THEN RETURN false; END IF;
    IF d.kind IN ('initial','reminder') THEN
        IF proposed->>'target' !~ '^family:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        SELECT * INTO family FROM public.stewardship_family_campaign
            WHERE id=substring(proposed->>'target' FROM 8)::uuid AND campaign_id=d.campaign_id
                AND created_at<=demand.created_at;
        IF family.id IS NULL THEN RETURN false; END IF;
    ELSIF d.kind IN ('daily_digest','weekly_digest') THEN
        IF proposed->>'target'<>'admins' THEN RETURN false; END IF;
    ELSE RETURN false;
    END IF;
    expected:=public.stewardship_catchup_due_v1(d.id,proposed->>'slot');
    IF proposed->>'slot'='recovery:'||demand.id::text AND d.kind IN ('daily_digest','weekly_digest') THEN
        -- An aggregate must be dated from a real staged or cancelled covered
        -- predecessor; neither an arbitrary timestamp nor a caller flag is proof.
        SELECT max(o.due_at) INTO expected FROM public.stewardship_schedule_occurrence o
        WHERE o.definition_id=d.id AND o.mode='production' AND o.target='admins'
            AND o.id<>(proposed->>'id')::uuid AND o.due_at<=demand.cutoff
            AND ((o.revision_id=d.current_revision_id AND o.state='pending' AND o.production_cycle=c.production_cycle
                    AND o.task_id IS NULL AND o.outbox_id IS NULL
                    AND NOT starts_with(o.slot,'recovery:')
                    AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot))
                OR (o.state='skipped' AND o.reason='schedule_replaced'
                    AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                            WHERE f.occurrence_id=o.id AND f.disposition='coalesced')
                        OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e
                            WHERE e.replacement_id=o.id AND e.demand_id=demand.id))));
    END IF;
    IF expected IS NULL OR expected IS DISTINCT FROM (proposed->>'due_at')::timestamptz
        OR expected>demand.cutoff
        OR proposed->>'occurrence_key'<>encode(sha256(convert_to(
            '["'||(proposed->>'revision_id')||'","production","'||(proposed->>'target')
            ||'","'||(proposed->>'slot')||'"'
            ||CASE WHEN coalesce((proposed->>'recovery_generation')::bigint,0)>0
                THEN ','||(proposed->>'recovery_generation') ELSE '' END
            ||CASE WHEN coalesce((proposed->>'production_cycle')::bigint,0)>0
                THEN ',["production_cycle",'||(proposed->>'production_cycle')||']' ELSE '' END
            ||']','UTF8')),'hex') THEN RETURN false; END IF;
    IF proposed->>'state'='pending' THEN
        RETURN proposed->>'reason'='' AND proposed->>'replacement_id' IS NULL;
    END IF;
    IF proposed->>'state'='skipped' THEN
        IF family.id IS NULL THEN RETURN false; END IF;
        reason:=CASE WHEN public.stewardship_campaign_now_v1()>=p.ends_at THEN 'campaign_closed'
            WHEN NOT family.active OR NOT family.email_eligible THEN 'family_ineligible'
            WHEN family.effective_submission_id IS NOT NULL THEN 'family_responded'
            WHEN NOT family.email_deliverable THEN 'no_deliverable_recipient' ELSE '' END;
        RETURN reason<>'' AND proposed->>'reason'=reason AND proposed->>'replacement_id' IS NULL;
    END IF;
    SELECT * INTO selected FROM public.stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    SELECT kind INTO selected_kind FROM public.stewardship_schedule_definition s
        WHERE s.id=selected.definition_id AND s.campaign_id=d.campaign_id
            AND s.current_revision_id=selected.revision_id;
    RETURN selected.id IS NOT NULL AND selected.id<>(proposed->>'id')::uuid
        AND selected.state='pending' AND selected.mode='production'
        AND selected.target=proposed->>'target' AND selected.task_id IS NULL AND selected.outbox_id IS NULL
        AND selected.due_at<=demand.cutoff
        AND CASE WHEN family.id IS NOT NULL THEN
            d.kind='reminder' AND selected_kind IN ('initial','reminder')
            AND proposed->>'reason'='missed_family_recovery'
            AND family.active AND family.email_eligible AND family.email_deliverable
            AND family.effective_submission_id IS NULL AND public.stewardship_campaign_now_v1()<p.ends_at
            AND (selected_kind='initial' OR (
                (selected.due_at,selected.definition_id,selected.id)>=
                    (expected,d.id,(proposed->>'id')::uuid)
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition s
                    JOIN public.stewardship_schedule_revision v ON v.id=s.current_revision_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind='initial' AND v.due_at<=demand.cutoff
                        AND NOT public.stewardship_schedule_slot_excluded_v1(s.id,'production',proposed->>'target','once'))))
        ELSE selected.definition_id=d.id AND selected.due_at>=expected
            AND (d.kind<>'daily_digest' OR selected.slot='recovery:'||demand.id::text)
            AND proposed->>'reason'=CASE d.kind
                WHEN 'daily_digest' THEN 'missed_daily_recovery' ELSE 'missed_weekly_recovery' END END;
END $$;

-- Bounded preparation alone may materialize work under its own demand hold.
-- The caller's correlation is the immutable claim event, not a campaign-wide
-- bypass flag. It binds the exact fence even if a worker reclaims the same run.
-- The Python owner also locks/rechecks the full TaskClaim fence around
-- every effect; occurrence claims and provider submission retain the hold.
CREATE FUNCTION public.stewardship_catchup_materializing_v1(
    campaign uuid,actor uuid,claim_event uuid
) RETURNS boolean LANGUAGE sql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_activation_catchup d
        JOIN public.stewardship_task_run t ON t.root_id=d.task_root_id
        JOIN public.stewardship_task_event e ON e.run_id=t.id
        WHERE d.campaign_id=$1 AND d.completed_at IS NULL
          AND e.id=$3 AND e.action='claim' AND e.state='running'
          AND e.fence=t.fence AND e.worker_id=t.worker_id
          AND t.domain_request_id=d.id
          AND t.task_type='activation_catchup' AND t.state='running'
          AND t.worker_id=$2 AND t.lease_expires_at>clock_timestamp()
    )
$$;

-- FUNCTION: stewardship_initial_recovery_valid_v1()
-- A Family recovery uses the immutable eligibility version as its execution
-- generation, not another semantic slot. The ordinary occurrence guard retains
-- lifecycle/current-revision/fulfillment admission; this proof adds the exact
-- false-to-true edge after a terminal attempt and excludes unresolved siblings.
CREATE FUNCTION public.stewardship_initial_recovery_valid_v1(proposed jsonb)
RETURNS boolean LANGUAGE plpgsql
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE family uuid; campaign uuid; event_time timestamptz; prior_deliverable boolean;
    generation bigint := (proposed->>'recovery_generation')::bigint;
    predecessor public.stewardship_schedule_occurrence%ROWTYPE;
BEGIN
    IF generation=0 THEN RETURN true; END IF;
    SELECT f.id,f.campaign_id INTO family,campaign
    FROM public.stewardship_family_campaign f
    JOIN public.stewardship_schedule_definition d ON d.campaign_id=f.campaign_id
    WHERE d.id=(proposed->>'definition_id')::uuid AND d.kind='initial'
      AND proposed->>'target'='family:'||f.id::text AND proposed->>'slot'='once'
      AND f.active AND f.email_eligible AND f.email_deliverable
      AND (proposed->>'mode'<>'production' OR f.effective_submission_id IS NULL);
    IF family IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_schedule_revision v
        JOIN public.stewardship_campaign c ON c.id=v.campaign_id
        JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        WHERE v.id=(proposed->>'revision_id')::uuid
          AND v.due_at=(proposed->>'due_at')::timestamptz
          AND v.due_at<=public.stewardship_campaign_now_v1()
          AND public.stewardship_campaign_now_v1()<p.ends_at
    ) OR EXISTS (
        SELECT 1 FROM public.stewardship_submission s
        JOIN public.stewardship_campaign_credentials c ON c.campaign_id=campaign
        WHERE proposed->>'mode'='testing' AND s.family_id=family
          AND s.mode='test' AND s.rehearsal_epoch_id=c.rehearsal_epoch_id
    ) OR EXISTS (
        SELECT 1 FROM public.stewardship_restore_delivery_hold h
        WHERE h.definition_id=(proposed->>'definition_id')::uuid
          AND h.mode=proposed->>'mode' AND h.target=proposed->>'target'
          AND h.slot=proposed->>'slot' AND h.state IN ('unreviewed','assumed_delivered')
    ) THEN RETURN false; END IF;
    SELECT h.created_at INTO event_time FROM public.stewardship_family_eligibility h
    WHERE h.family_id=family AND h.family_version=generation AND h.email_deliverable;
    SELECT h.email_deliverable INTO prior_deliverable
    FROM public.stewardship_family_eligibility h
    WHERE h.family_id=family AND h.family_version<generation
    ORDER BY h.family_version DESC LIMIT 1;
    IF event_time IS NULL OR prior_deliverable IS DISTINCT FROM false THEN RETURN false; END IF;
    SELECT * INTO predecessor FROM public.stewardship_schedule_occurrence o
    WHERE o.definition_id=(proposed->>'definition_id')::uuid
      AND o.revision_id=(proposed->>'revision_id')::uuid
      AND o.mode=proposed->>'mode' AND o.target=proposed->>'target'
      AND o.slot=proposed->>'slot'
      AND o.production_cycle=(proposed->>'production_cycle')::bigint
    ORDER BY o.recovery_generation DESC LIMIT 1;
    IF predecessor.id IS NULL OR predecessor.state NOT IN ('failed','skipped')
       OR (predecessor.state='skipped' AND predecessor.reason NOT IN ('no_deliverable_recipient','family_ineligible'))
       OR predecessor.recovery_generation>=generation OR predecessor.updated_at>=event_time
       OR EXISTS (
           SELECT 1 FROM public.stewardship_schedule_occurrence o
           WHERE o.definition_id=(proposed->>'definition_id')::uuid
             AND o.mode=proposed->>'mode' AND o.target=proposed->>'target'
             AND o.slot=proposed->>'slot'
             AND o.state IN ('pending','running','delivery_unknown','succeeded')
       ) THEN RETURN false; END IF;
    RETURN proposed->>'occurrence_key'=encode(sha256(convert_to(
        '["'||(proposed->>'revision_id')||'","'||(proposed->>'mode')||'","'
        ||(proposed->>'target')||'","'||(proposed->>'slot')||'",'||generation::text
        ||CASE WHEN coalesce((proposed->>'production_cycle')::bigint,0)>0
            THEN ',["production_cycle",'||(proposed->>'production_cycle')||']' ELSE '' END||']',
        'UTF8')),'hex');
END $$;

-- FUNCTION: stewardship_occurrence_guard_v1()
CREATE FUNCTION public.stewardship_occurrence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE d stewardship_schedule_definition%ROWTYPE; c stewardship_campaign%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; t stewardship_task_run%ROWTYPE;
    instant timestamptz := stewardship_campaign_now_v1();
    digest_completed boolean := false;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('occurrences',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Occurrence history requires exceptional retention' USING ERRCODE='23514';
    END IF;
    SELECT * INTO d FROM stewardship_schedule_definition WHERE id=NEW.definition_id FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF NOT EXISTS(SELECT 1 FROM stewardship_schedule_revision WHERE id=NEW.revision_id AND record_id=d.id AND campaign_id=d.campaign_id)
       OR NEW.target='' OR NEW.slot='' OR NEW.occurrence_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid occurrence identity' USING ERRCODE='23514'; END IF;
    IF (TG_OP='INSERT' OR NEW.state IN ('pending','running'))
       AND NEW.production_cycle IS DISTINCT FROM (CASE WHEN NEW.mode='production' THEN c.production_cycle ELSE 0 END) THEN
        RAISE EXCEPTION 'Occurrence belongs to a retired Production cycle' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('succeeded','failed','skipped','coalesced') AND EXISTS (
        SELECT 1 FROM stewardship_outbox_message m WHERE m.id=NEW.outbox_id
          AND public.stewardship_occurrence_delivery_conflict_v1(NEW.state,m.state)
    ) THEN RAISE EXCEPTION 'Occurrence outcome contradicts terminal delivery'
        USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NOT public.stewardship_initial_recovery_valid_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Initial recovery requires a new deliverability transition'
                USING ERRCODE='23514';
        END IF;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.fence<>0 OR NEW.attempts<>0
           OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR NEW.mode<>r.mode OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
           OR NOT EXISTS (SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
               AND ((instant>=p.starts_at AND instant<p.ends_at
                   AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                   OR (NEW.mode='production' AND c.state='closed'
                       AND (d.kind IN ('daily_digest','weekly_digest')
                           OR (NEW.due_at>=p.starts_at AND NEW.due_at<p.ends_at)))))
           OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
               AND NOT public.stewardship_catchup_materializing_v1(c.id,NEW.actor_id,NEW.correlation_id))
           OR EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot) THEN
            RAISE EXCEPTION 'Occurrence creation is not admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF session_user='pk_stewardship_scheduler' AND (
            OLD.state<>'pending' OR NEW.state NOT IN ('skipped','coalesced')
            OR OLD.task_id IS NOT NULL OR OLD.outbox_id IS NOT NULL
            OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
            OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode
            OR r.restore_review_required
            OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate
                WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
            OR (NEW.state='coalesced' AND NOT EXISTS(
                SELECT 1 FROM stewardship_schedule_occurrence selected
                WHERE selected.id=NEW.replacement_id AND selected.target=NEW.target
                  AND selected.mode=NEW.mode AND selected.state='pending'
                  AND selected.task_id IS NULL AND selected.outbox_id IS NULL
            ))
        ) THEN RAISE EXCEPTION 'Scheduler may only reconcile unallocated pending work'
            USING ERRCODE='23514'; END IF;
        IF (OLD.state='pending' AND NEW.state NOT IN ('pending','running','skipped','coalesced'))
           OR (OLD.state='running' AND NEW.state NOT IN ('running','pending','delivery_unknown','succeeded','failed','skipped','coalesced'))
           OR (OLD.state='delivery_unknown' AND NEW.state NOT IN ('succeeded','pending','failed'))
           OR (OLD.state='failed' AND NEW.state NOT IN ('pending','skipped'))
           OR OLD.state IN ('succeeded','skipped','coalesced') THEN
            RAISE EXCEPTION 'Invalid occurrence transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='running' THEN
            -- Branch before planning private report reads: SQL boolean order is
            -- not a privilege boundary for unrelated Family/daily mail owners.
            IF d.kind='weekly_digest' THEN
                digest_completed:=public.stewardship_weekly_digest_completion_v1(to_jsonb(NEW));
            ELSE
                digest_completed:=public.stewardship_daily_digest_completion_v1(to_jsonb(NEW));
            END IF;
        END IF;
        IF NEW.state='running' AND NOT digest_completed THEN
            SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
            IF NOT FOUND OR t.state<>'running' OR t.worker_id<>NEW.worker_id OR t.fence<>NEW.fence OR t.lease_expires_at<=clock_timestamp()
               OR NEW.lease_expires_at>t.lease_expires_at OR NEW.heartbeat_at>clock_timestamp()
               OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
               OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode OR r.restore_review_required
               OR NOT ((t.task_type='schedule_occurrence' AND t.domain_request_id IS NOT DISTINCT FROM NEW.id)
                   OR (t.task_type='family_mail_prepare' AND EXISTS (
                       SELECT 1 FROM public.stewardship_family_mail_preparation q
                       WHERE q.id=t.domain_request_id AND q.task_id=t.root_id
                         AND q.occurrence_id=NEW.id AND q.mode=NEW.mode))
                   OR (t.task_type='outbox_delivery' AND EXISTS (
                       SELECT 1 FROM public.stewardship_outbox_message mail
                       WHERE mail.id=t.domain_request_id AND mail.task_id=t.root_id
                         AND mail.id=NEW.outbox_id AND mail.semantic_key=NEW.id
                         AND mail.mode=NEW.mode AND mail.family_id::text=substring(NEW.target FROM 8))))
               OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
                   AND ((instant>=p.starts_at AND instant<p.ends_at
                       AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                       OR (NEW.mode='production' AND c.state='closed' AND d.kind IN ('daily_digest','weekly_digest'))))
               OR NEW.due_at>instant
               OR (NEW.mode='production' AND c.delivery_paused)
               OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
               OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
               OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h WHERE h.definition_id=d.id AND h.mode=NEW.mode
                   AND h.target=NEW.target AND h.slot=NEW.slot AND h.state IN ('unreviewed','assumed_delivered'))
               OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
                RAISE EXCEPTION 'Occurrence claim requires current fenced work' USING ERRCODE='23514'; END IF;
        END IF;
        IF OLD.state='running' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NEW.fence<>OLD.fence) THEN
            RAISE EXCEPTION 'Occurrence worker identity changed' USING ERRCODE='23514'; END IF;
        IF OLD.state='running' AND NOT (
            (NEW.state='skipped' OR (NEW.state='coalesced' AND NEW.reason IN (
                'missed_family_recovery','missed_daily_recovery','missed_weekly_recovery')))
            AND public.stewardship_schedule_effect_v1(
                OLD.id,OLD.version,NEW.actor_id,NEW.correlation_id,NEW.reason)
        ) AND NOT EXISTS(SELECT 1 FROM stewardship_task_run owner_task WHERE owner_task.id=OLD.task_id
            AND ((owner_task.state='running' AND owner_task.worker_id=NEW.actor_id AND owner_task.fence=NEW.fence AND owner_task.lease_expires_at>clock_timestamp())
                OR (owner_task.state IN ('abandoned','cancelled','succeeded','failed') AND owner_task.fence>=OLD.fence AND NEW.actor_id IS NOT NULL
                    AND NEW.reason IN ('recovery_retry','recovery_unknown','recovery_complete','recovery_fail','recovery_skip','recovery_coalesce')))) THEN
            RAISE EXCEPTION 'Occurrence write requires live worker fencing' USING ERRCODE='23514'; END IF;
        IF NEW.state<>'running' AND OLD.state<>'running' AND NEW.fence<>OLD.fence THEN
            RAISE EXCEPTION 'Only a new claim advances occurrence fencing' USING ERRCODE='23514'; END IF;
        IF OLD.state='delivery_unknown' AND (
            NEW.actor_id IS NULL
            OR NEW.reason IS DISTINCT FROM CASE NEW.state WHEN 'pending' THEN 'recovery_retry'
                WHEN 'succeeded' THEN 'recovery_complete' WHEN 'failed' THEN 'recovery_fail' END
            OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
            OR (NOT public.stewardship_delivery_resolution_edge_v1(to_jsonb(OLD),to_jsonb(NEW))
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run reconciled WHERE reconciled.id=OLD.task_id
                AND reconciled.task_type='schedule_occurrence' AND reconciled.domain_request_id=OLD.id
                AND reconciled.fence>=OLD.fence AND reconciled.state IN ('abandoned','cancelled','succeeded','failed')))
        ) THEN RAISE EXCEPTION 'Unknown delivery requires attributed reconciled ownership' USING ERRCODE='23514'; END IF;
        IF NEW.state='pending' AND OLD.state='failed' AND (NEW.retry_command_id IS NULL OR NEW.revision_id IS DISTINCT FROM d.current_revision_id) THEN
            RAISE EXCEPTION 'Occurrence retry requires explicit current identity' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND OLD.state='failed' AND NEW.reason NOT IN ('schedule_removed','schedule_replaced') THEN
            RAISE EXCEPTION 'Failed occurrence cannot be silently discarded' USING ERRCODE='23514'; END IF;
        IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state<>'running' AND NEW.state='running' THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Occurrence attempt count is inconsistent' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='coalesced' AND (NEW.replacement_id=NEW.id OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence replacement JOIN stewardship_schedule_definition rd ON rd.id=replacement.definition_id
        WHERE replacement.id=NEW.replacement_id AND rd.campaign_id=d.campaign_id AND replacement.mode=NEW.mode
          AND replacement.state NOT IN ('skipped','coalesced','failed')
    )) THEN RAISE EXCEPTION 'Invalid occurrence coalescing target' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $_$;

-- FUNCTION: stewardship_occurrence_history_v1()
CREATE FUNCTION public.stewardship_occurrence_history_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE before_state text;
BEGIN
    IF TG_OP='UPDATE' THEN before_state:=OLD.state; END IF;
    INSERT INTO stewardship_occurrence_transition(id,occurrence_id,version,before_state,after_state,fence,attempts,reason,retry_command_id,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.id,NEW.version,before_state,NEW.state,NEW.fence,NEW.attempts,NEW.reason,
        CASE WHEN TG_OP='UPDATE' AND OLD.state='failed' AND NEW.state='pending' THEN NEW.retry_command_id ELSE NULL END,
        NEW.actor_id,NEW.correlation_id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_occurrence_transition_immutable_v1()
CREATE FUNCTION public.stewardship_occurrence_transition_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP='DELETE' AND public.stewardship_cleanup_effect_v1('occurrence_events',OLD.id) THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_operational_log_immutable_v1()
CREATE FUNCTION public.stewardship_operational_log_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_parish_branding_v1()
CREATE FUNCTION public.stewardship_parish_branding_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE predecessor uuid; previous public.stewardship_parish%ROWTYPE;
        selected public.stewardship_branding_bundle%ROWTYPE;
        bundle_count integer; asset_count integer;
BEGIN
    SELECT predecessor_id INTO predecessor
        FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    -- Pre-feature root snapshots retain their historical UUID vocabulary.
    -- Operational bootstrap is separately restricted to bootstrap-policy-v1.
    IF predecessor IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO previous FROM public.stewardship_parish
        WHERE configuration_id=predecessor;
    IF FOUND AND (NEW.large_logo_id,NEW.menu_logo_id,NEW.icon_logo_id,NEW.favicon_id)
        IS NOT DISTINCT FROM
        (previous.large_logo_id,previous.menu_logo_id,previous.icon_logo_id,
            previous.favicon_id) THEN RETURN NEW; END IF;
    PERFORM pg_advisory_xact_lock(736230,1);
    SELECT count(*),count(DISTINCT bundle_id) INTO asset_count,bundle_count
        FROM public.stewardship_branding_asset WHERE
            (id=NEW.large_logo_id AND label='large')
            OR (id=NEW.menu_logo_id AND label='menu')
            OR (id=NEW.icon_logo_id AND label='icon')
            OR (id=NEW.favicon_id AND label='favicon');
    IF asset_count<>4 OR bundle_count<>1 THEN
        RAISE EXCEPTION 'Parish branding requires a complete normalized bundle'
            USING ERRCODE='23514';
    END IF;
    SELECT b.* INTO selected FROM public.stewardship_branding_bundle b
        JOIN public.stewardship_branding_asset a ON a.bundle_id=b.id
        WHERE a.id=NEW.large_logo_id;
    IF selected.state<>'ready' THEN
        RAISE EXCEPTION 'Branding is not ready' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_parish p
        JOIN public.stewardship_config_activation c
            ON c.configuration_id=p.configuration_id
        WHERE p.large_logo_id=NEW.large_logo_id) AND
        (selected.expires_at<=clock_timestamp() OR selected.base_id<>predecessor
         OR selected.owner_id IS DISTINCT FROM NEW.actor_id) THEN
        RAISE EXCEPTION 'Staged branding ownership or configuration changed'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_parish_immutable_v1()
CREATE FUNCTION public.stewardship_parish_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_policy_activation_v1()
CREATE FUNCTION public.stewardship_policy_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    entry record;
    prior jsonb;
    recipients jsonb;
    alert_kind text;
    expanded boolean := false;
    event_id uuid;
BEGIN
    SELECT COALESCE(jsonb_agg(email ORDER BY email), '[]'::jsonb) INTO recipients
    FROM public.stewardship_address_rule
    WHERE configuration_id = NEW.predecessor_id AND roles ? 'administrator';
    FOR entry IN
        SELECT record_id, 'address' AS kind, email AS target, roles
        FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.configuration_id
        UNION ALL
        SELECT record_id, 'domain', domain, roles
        FROM public.stewardship_domain_rule
        WHERE configuration_id = NEW.configuration_id
    LOOP
        prior := NULL;
        alert_kind := NULL;
        IF entry.kind = 'address' THEN
            SELECT roles INTO prior FROM public.stewardship_address_rule
            WHERE configuration_id = NEW.predecessor_id AND email = entry.target;
            IF entry.roles ? 'administrator'
               AND NOT COALESCE(prior ? 'administrator', false) THEN
                alert_kind := 'administrator_granted';
            END IF;
        ELSE
            SELECT roles INTO prior FROM public.stewardship_domain_rule
            WHERE configuration_id = NEW.predecessor_id AND domain = entry.target;
            IF prior IS NULL THEN
                alert_kind := 'domain_created';
            ELSIF entry.roles ? 'staff' AND NOT prior ? 'staff' THEN
                alert_kind := 'domain_staff_granted';
            END IF;
        END IF;
        -- A new domain or any additional exact/domain role broadens access.
        -- Removing an exact denial may expose a domain grant, checked below.
        expanded := expanded OR NOT entry.roles <@ COALESCE(prior, '[]'::jsonb);
        IF alert_kind IS NOT NULL THEN
            event_id := gen_random_uuid();
            INSERT INTO public.stewardship_policy_security_event
                (id, created_at, actor_id, correlation_id, activation_id,
                 rule_record_id, target, kind, before_roles, after_roles, recipients)
            VALUES (event_id, NEW.created_at, NEW.actor_id, NEW.correlation_id, NEW.id,
                entry.record_id, entry.target, alert_kind, COALESCE(prior, '[]'::jsonb),
                entry.roles, recipients);
            INSERT INTO public.stewardship_audit_event
                (id, actor_id, correlation_id, event_type, subject_id)
            VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
                'policy_security_event', event_id);
        END IF;
    END LOOP;
    expanded := expanded OR EXISTS (
        SELECT 1 FROM public.stewardship_address_rule previous_rule
        JOIN public.stewardship_domain_rule domain
          ON domain.configuration_id = NEW.configuration_id
         AND domain.domain = split_part(previous_rule.email, '@', 2)
        WHERE previous_rule.configuration_id = NEW.predecessor_id
          AND NOT domain.roles <@ previous_rule.roles
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_address_rule current
                          WHERE current.configuration_id = NEW.configuration_id
                            AND current.email = previous_rule.email)
    );
    -- New manual scope or an independent manual Minister origin may unsuppress
    -- a seeded login without changing any configured role-name array.
    expanded := expanded OR EXISTS (
        SELECT 1 FROM public.stewardship_ministry_assignment assignment
        WHERE assignment.configuration_id = NEW.configuration_id
          AND assignment.source = 'manual'
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_ministry_assignment previous_assignment
              WHERE previous_assignment.configuration_id = NEW.predecessor_id
                AND previous_assignment.email = assignment.email
                AND previous_assignment.ministry_duid = assignment.ministry_duid
                AND previous_assignment.source = 'manual')
    ) OR EXISTS (
        SELECT 1 FROM public.stewardship_address_rule rule
        JOIN public.stewardship_address_grant grant_row ON grant_row.rule_id = rule.id
        WHERE rule.configuration_id = NEW.configuration_id
          AND grant_row.role = 'ministry_leader' AND grant_row.origins ? 'manual'
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_address_rule previous_rule
              JOIN public.stewardship_address_grant previous_grant
                ON previous_grant.rule_id = previous_rule.id
              WHERE previous_rule.configuration_id = NEW.predecessor_id
                AND previous_rule.email = rule.email
                AND previous_grant.role = 'ministry_leader'
                AND previous_grant.origins ? 'manual')
    );
    IF expanded THEN
        INSERT INTO public.stewardship_policy_epoch
            (id, created_at, actor_id, correlation_id, activation_id, sequence)
        SELECT gen_random_uuid(), NEW.created_at, NEW.actor_id, NEW.correlation_id,
            NEW.id, COALESCE(MAX(sequence), 0) + 1 FROM public.stewardship_policy_epoch;
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
            'policy_denial_namespace_reset', NEW.id);
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_policy_complete_v1()
CREATE FUNCTION public.stewardship_policy_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected bigint;
    actual bigint;
    rule public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF NEW.validation_schema NOT IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8') THEN RETURN NULL; END IF;
    expected := jsonb_array_length(NEW.canonical_document->'sections'->'login_rules');
    SELECT (SELECT count(*) FROM public.stewardship_domain_rule
            WHERE configuration_id = NEW.id)
         + (SELECT count(*) FROM public.stewardship_address_rule
            WHERE configuration_id = NEW.id)
         + (SELECT count(*) FROM public.stewardship_ministry_assignment
            WHERE configuration_id = NEW.id)
    INTO actual;
    IF expected IS NULL OR actual <> expected OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id AND roles ? 'administrator'
    ) THEN
        RAISE EXCEPTION 'Policy must be complete and retain an Administrator'
            USING ERRCODE = '23514';
    END IF;
    FOR rule IN SELECT * FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id LOOP
        IF (SELECT count(*) FROM public.stewardship_address_grant
            WHERE rule_id = rule.id)
           <> jsonb_array_length(rule.roles) THEN
            RAISE EXCEPTION 'Policy grant provenance is incomplete'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_policy_epoch_immutable_v1()
CREATE FUNCTION public.stewardship_policy_epoch_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_policy_projection_v1()
CREATE FUNCTION public.stewardship_policy_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected jsonb;
    actual jsonb;
    config uuid;
    record uuid;
    parent public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'stewardship_address_grant' THEN
        SELECT * INTO parent FROM public.stewardship_address_rule
        WHERE id = NEW.rule_id;
        config := parent.configuration_id;
        record := parent.record_id;
    ELSE
        config := NEW.configuration_id;
        record := NEW.record_id;
    END IF;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version version,
        jsonb_array_elements(version.canonical_document->'sections'->'login_rules') item
    WHERE version.id = config AND version.validation_schema IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8')
        AND item->>'id' = record::text;
    IF expected IS NULL THEN
        RAISE EXCEPTION 'Policy projection requires matching YAML record'
            USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'stewardship_domain_rule' THEN
        actual := jsonb_build_object('kind', 'domain', 'domain', NEW.domain,
            'roles', NEW.roles);
        IF NEW.domain <> lower(NEW.domain) OR NEW.domain = 'gmail.com'
           OR NEW.roles = '[]'::jsonb
           OR NOT NEW.roles <@ '["staff", "ministry_leader"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid hosted domain grant' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_rule' THEN
        actual := jsonb_build_object('kind', 'address', 'email', NEW.email,
            'roles', NEW.roles, 'creation_origin', NEW.creation_origin,
            'creation_operation', NEW.creation_operation);
        expected := expected - 'grants';
        IF NEW.email <> lower(NEW.email)
           OR NEW.creation_origin NOT IN ('manual', 'chair-seed')
           OR NOT NEW.roles <@
               '["administrator", "staff", "ministry_leader"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid address grant' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_grant' THEN
        actual := NEW.origins;
        expected := expected->'grants'->NEW.role;
        IF expected IS NULL OR NEW.origins = '{}'::jsonb
           OR NEW.role NOT IN ('administrator', 'staff', 'ministry_leader')
           OR (NEW.origins ? 'chair-seed' AND NEW.role <> 'ministry_leader') THEN
            RAISE EXCEPTION 'Invalid grant provenance' USING ERRCODE = '23514';
        END IF;
    ELSE
        actual := jsonb_build_object('kind', 'assignment', 'email', NEW.email,
            'ministry_duid', NEW.ministry_duid, 'source', NEW.source,
            'operation_id', NEW.operation_id);
        IF NEW.email <> lower(NEW.email) OR NEW.source NOT IN ('manual', 'chair-seed')
           OR NEW.ministry_duid < 1 THEN
            RAISE EXCEPTION 'Invalid ministry assignment' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Policy projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_policy_security_event_immutable_v1()
CREATE FUNCTION public.stewardship_policy_security_event_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_population_insert_dirty_v1()
CREATE FUNCTION public.stewardship_population_insert_dirty_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    UPDATE stewardship_campaign_credentials SET population_dirty=true,version=version+1
    WHERE campaign_id IN(SELECT DISTINCT campaign_id FROM new_families);
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_population_manifest_v1()
CREATE FUNCTION public.stewardship_population_manifest_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected_digest text; expected_count bigint;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NOT NEW.population_dirty THEN RAISE EXCEPTION 'Population starts unverified' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NOT NEW.population_dirty AND (OLD.population_dirty OR
       (NEW.source_snapshot_id,NEW.source_generation,NEW.eligibility_digest,NEW.eligible_count)
       IS DISTINCT FROM (OLD.source_snapshot_id,OLD.source_generation,OLD.eligibility_digest,OLD.eligible_count)) THEN
        SELECT count(*),encode(sha256(convert_to('family-token-coverage-v1','UTF8')||decode('00','hex')||
            coalesce(string_agg(uuid_send(id),''::bytea ORDER BY family_duid),''::bytea)),'hex')
        INTO expected_count,expected_digest FROM stewardship_family_campaign WHERE campaign_id=NEW.campaign_id AND portal_eligible;
        IF NEW.source_snapshot_id IS NULL OR NEW.source_generation IS NULL OR NEW.source_generation<1
           OR NEW.eligible_count<>expected_count OR NEW.eligibility_digest<>expected_digest
           OR EXISTS(SELECT 1 FROM stewardship_family_campaign WHERE campaign_id=NEW.campaign_id AND source_generation<>NEW.source_generation) THEN
            RAISE EXCEPTION 'Population manifest must match the complete committed generation' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_population_update_dirty_v1()
CREATE FUNCTION public.stewardship_population_update_dirty_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    UPDATE stewardship_campaign_credentials SET population_dirty=true,version=version+1
    WHERE campaign_id IN(
        SELECT n.campaign_id FROM new_families n JOIN old_families o ON o.id=n.id
        WHERE (n.portal_eligible,n.source_generation) IS DISTINCT FROM (o.portal_eligible,o.source_generation));
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_portal_session_mutable_v1()
CREATE FUNCTION public.stewardship_portal_session_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."principal_id" IS DISTINCT FROM OLD."principal_id" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."authenticated_at" IS DISTINCT FROM OLD."authenticated_at" OR (OLD."revoked_at" IS NOT NULL AND NEW."revoked_at" IS DISTINCT FROM OLD."revoked_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_portal_user_mutable_v1()
CREATE FUNCTION public.stewardship_portal_user_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."google_subject" IS DISTINCT FROM OLD."google_subject" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_postclose_guard_v1()
CREATE FUNCTION public.stewardship_postclose_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.actor_id IS NULL OR NEW.mode NOT IN ('testing','production') OR NEW.obligation_key='' OR btrim(NEW.reason)=''
       OR stewardship_coverage_valid_v1(NEW.coverage) IS DISTINCT FROM true
       OR NEW.coverage_digest<>encode(sha256(convert_to(NEW.coverage::text,'UTF8')),'hex')
       OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c JOIN stewardship_system_configuration r ON r.current_campaign_id=c.id
           WHERE c.id=NEW.campaign_id AND c.state='closed' AND NEW.mode=r.mode AND NOT r.restore_review_required)
       OR NEW.occurrence_id IS NULL OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
           JOIN stewardship_schedule_definition d ON d.id=o.definition_id WHERE o.id=NEW.occurrence_id
           AND d.campaign_id=NEW.campaign_id AND d.kind IN ('daily_digest','weekly_digest')
           AND o.mode=NEW.mode AND o.state='skipped' AND o.reason='admin_post_close_skip'
           AND NEW.obligation_key='schedule:'||d.id::text||':'||o.slot
           AND NEW.task_id IS NOT DISTINCT FROM o.task_id AND NEW.outbox_id IS NOT DISTINCT FROM o.outbox_id)
       OR (NEW.task_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stewardship_task_run t
           WHERE t.id=NEW.task_id AND t.state='cancelled' AND t.domain_request_id=NEW.occurrence_id)) THEN
        RAISE EXCEPTION 'Post-close skip requires exact coverage and cancellation evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_postclose_resolution_immutable_v1()
CREATE FUNCTION public.stewardship_postclose_resolution_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_provider_context_immutable_v1()
CREATE FUNCTION public.stewardship_provider_context_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_provider_context_insert_v1()
CREATE FUNCTION public.stewardship_provider_context_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE receipt public.stewardship_secret_request%ROWTYPE;
        keys text[];
        item jsonb;
BEGIN
    SELECT * INTO receipt FROM public.stewardship_secret_request
        WHERE id=NEW.request_id FOR SHARE;
    IF NOT FOUND OR receipt.target IS DISTINCT FROM NEW.target
       OR receipt.state <> 'staged' OR receipt.expires_at <= clock_timestamp()
       OR jsonb_array_length(receipt.required_consumers)=0
       OR NEW.actor_id IS DISTINCT FROM receipt.requested_by_id
       OR jsonb_typeof(NEW.settings) IS DISTINCT FROM 'object'
       OR octet_length(NEW.settings::text)>2048 THEN
        RAISE EXCEPTION 'Invalid provider validation context' USING ERRCODE='23514';
    END IF;
    -- Same transaction as intake: context cannot be attached after another
    -- process could have claimed a request which originally had no context.
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_secret_request
        WHERE id=NEW.request_id
          AND xmin::text::numeric=
              mod(pg_current_xact_id()::text::numeric,4294967296)) THEN
        RAISE EXCEPTION 'Provider context requires original intake transaction'
            USING ERRCODE='23514';
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
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$_$;

-- FUNCTION: stewardship_public_credential_handoff_immutable_v1()
CREATE FUNCTION public.stewardship_public_credential_handoff_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_public_handoff_insert_v1()
CREATE FUNCTION public.stewardship_public_handoff_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF current_user <> session_user
       OR current_user <> 'pk_stewardship_credential_' || NEW.target
       OR octet_length(NEW.public_key) <> 32
       OR NEW.actor_id IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid public handoff publication' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_recovery_activation_v1()
CREATE FUNCTION public.stewardship_recovery_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    intent public.stewardship_config_request%ROWTYPE;
BEGIN
    SELECT * INTO intent FROM public.stewardship_config_request
    WHERE id = NEW.request_id AND authority = 'operator_recovery';
    IF NOT FOUND THEN RETURN NEW; END IF;
    INSERT INTO public.stewardship_admin_revocation
        (id, created_at, actor_id, correlation_id, activation_id)
    VALUES (gen_random_uuid(), NEW.created_at, NULL, NEW.correlation_id, NEW.id);
    UPDATE public.stewardship_portal_session
    SET revoked_at = GREATEST(statement_timestamp(), last_activity_at),
        version = version + 1,
        actor_id = NULL, correlation_id = NEW.correlation_id
    WHERE revoked_at IS NULL;
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id)
    VALUES (gen_random_uuid(), NULL, NEW.correlation_id,
        'operator_admin_recovered', intent.id);
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_recovery_recipient_v1()
CREATE FUNCTION public.stewardship_recovery_recipient_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    target text;
BEGIN
    SELECT intent.recovery_target INTO target
    FROM public.stewardship_config_activation activation
    JOIN public.stewardship_config_request intent ON intent.id = activation.request_id
    WHERE activation.id = NEW.activation_id AND intent.authority = 'operator_recovery';
    IF FOUND AND NOT NEW.recipients ? target THEN
        SELECT jsonb_agg(value ORDER BY value) INTO NEW.recipients
        FROM jsonb_array_elements_text(NEW.recipients || to_jsonb(target));
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_recovery_request_v1()
CREATE FUNCTION public.stewardship_recovery_request_v1() RETURNS trigger
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
       OR (operation ->> 'operation' = 'add' AND operation -> 'values'
           ->> 'creation_operation' IS DISTINCT FROM NEW.request_key::text)
       OR operation -> 'values' -> 'grants' -> 'administrator'
          IS DISTINCT FROM jsonb_build_object('manual', NEW.request_key::text) THEN
        RAISE EXCEPTION 'Invalid offline recovery attribution'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_refresh_attempt_guard_v1()
CREATE FUNCTION public.stewardship_refresh_attempt_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_source_refresh_request r
        JOIN stewardship_source_snapshot s ON s.id=NEW.snapshot_id
        JOIN stewardship_task_run t ON t.id=NEW.task_id
        JOIN stewardship_source_lease l ON l.owner_id=t.id
        JOIN stewardship_system_configuration c ON c.active_configuration_id=
            NEW.configuration_id
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        WHERE r.id=NEW.request_id AND i.kind='parishsoft'
          AND i.credential_fingerprint=NEW.credential_fingerprint
          AND i.settings->>'organization_id'=r.organization_id::text
          AND c.current_campaign_id IS NOT DISTINCT FROM r.campaign_id
          AND NOT c.restore_review_required
          AND r.window_canonical=stewardship_source_current_window_v1(r.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.root_id=r.task_root_id AND t.domain_request_id=r.id
          AND t.task_type='source_refresh' AND t.state='running'
          AND t.fence=NEW.task_fence AND t.lease_expires_at > clock_timestamp()
          AND s.task_id=t.id AND s.state='staging' AND s.kind=r.kind
          AND s.organization_id=r.organization_id AND s.source_fence=l.fence
          AND l.phase=r.kind AND l.task_fence=t.fence AND l.worker_id=t.worker_id
          AND l.expires_at > clock_timestamp() AND NEW.actor_id=t.worker_id
       ) THEN
        RAISE EXCEPTION 'Source attempt requires current scope/credential/fences'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_refresh_command_guard_v1()
CREATE FUNCTION public.stewardship_refresh_command_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM stewardship_source_refresh_request r
           JOIN stewardship_task_run t ON t.root_id=r.task_root_id
           WHERE r.id=NEW.request_id AND (r.kind='full' OR r.kind=NEW.kind)
             AND t.state IN ('queued','retry_wait','abandoned')) THEN
        RAISE EXCEPTION 'Refresh command requires waiting compatible work'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_refresh_fallback_guard_v1()
CREATE FUNCTION public.stewardship_refresh_fallback_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_source_refresh_request r
        JOIN stewardship_task_run t ON t.id=NEW.task_id
        JOIN stewardship_source_refresh_command command ON command.id=NEW.command_id
        JOIN stewardship_source_refresh_request target ON target.id=command.request_id
        JOIN stewardship_system_configuration c
          ON c.current_campaign_id IS NOT DISTINCT FROM r.campaign_id
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        WHERE r.id=NEW.request_id AND r.kind='delta'
          AND i.kind='parishsoft'
          AND i.settings->>'organization_id'=r.organization_id::text
          AND NOT c.restore_review_required
          AND r.window_canonical=stewardship_source_current_window_v1(r.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.root_id=r.task_root_id AND t.domain_request_id=r.id
          AND t.task_type='source_refresh' AND t.state='running'
          AND t.fence=NEW.task_fence AND t.lease_expires_at > clock_timestamp()
          AND NEW.actor_id=t.worker_id
          AND command.kind='full' AND command.cause='fallback'
          AND command.actor_id IS NULL AND target.kind='full'
          AND target.organization_id=r.organization_id
          AND target.campaign_id IS NOT DISTINCT FROM r.campaign_id
          AND target.window_digest=r.window_digest AND target.id<>r.id
       ) THEN
        RAISE EXCEPTION 'Source fallback requires current request/worker/dependency'
            USING ERRCODE='23514';
    END IF;
    IF NEW.reason='no_base' THEN
        IF NEW.attempt_id IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_source_current WHERE snapshot_id IS NULL
        ) THEN
            RAISE EXCEPTION 'No-base fallback requires absent current source'
                USING ERRCODE='23514';
        END IF;
    ELSIF NEW.reason='incomplete_delta' THEN
        IF NOT EXISTS (
            SELECT 1 FROM stewardship_source_refresh_attempt a
            JOIN stewardship_source_snapshot s ON s.id=a.snapshot_id
            WHERE a.id=NEW.attempt_id AND a.request_id=NEW.request_id
              AND a.task_id=NEW.task_id AND a.task_fence=NEW.task_fence
              AND s.state='rejected' AND s.kind='delta'
        ) THEN
            RAISE EXCEPTION 'Delta fallback requires its rejected concrete attempt'
                USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Unknown source fallback reason' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_refresh_request_guard_v1()
CREATE FUNCTION public.stewardship_refresh_request_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        expected text;
        organization text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Refresh requires its owning work order' USING ERRCODE='23514';
    END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
    IF NOT FOUND OR runtime.active_configuration_id
       IS DISTINCT FROM NEW.configuration_id
       OR runtime.current_campaign_id IS DISTINCT FROM NEW.campaign_id
       OR runtime.restore_review_required
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
                  WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Refresh configuration is not current' USING ERRCODE='23514';
    END IF;
    SELECT settings->>'organization_id' INTO organization
      FROM stewardship_applied_integration
      WHERE configuration_id=NEW.configuration_id AND kind='parishsoft';
    IF organization IS DISTINCT FROM NEW.organization_id::text
       OR EXISTS (SELECT 1 FROM stewardship_source_current
           WHERE organization_id IS NOT NULL AND organization_id <> NEW.organization_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_root_id
           AND root_id=id AND parent_id IS NULL AND task_type='source_refresh'
           AND domain_request_id=NEW.id AND state='queued' AND attempt=0
           AND initiated_by_id IS NOT DISTINCT FROM NEW.actor_id) THEN
        RAISE EXCEPTION 'Refresh tenant or task binding is invalid'
            USING ERRCODE='23514';
    END IF;
    expected := public.stewardship_source_current_window_v1(NEW.campaign_id);
    IF octet_length(NEW.window_canonical) > 1048576
       OR NEW.window_canonical IS DISTINCT FROM expected
       OR NEW.window_digest IS DISTINCT FROM
           encode(sha256(convert_to(NEW.window_canonical,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'Refresh window is not the current exact scope'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_refresh_snapshot_completion_v1()
CREATE FUNCTION public.stewardship_refresh_snapshot_completion_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE attempt stewardship_source_refresh_attempt%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
        base_cursor jsonb;
BEGIN
    IF NEW.state NOT IN ('ready','promoted') OR OLD.state='promoted'
       OR NOT EXISTS (SELECT 1 FROM stewardship_task_run WHERE id=NEW.task_id
           AND task_type='source_refresh') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO attempt FROM stewardship_source_refresh_attempt
        WHERE snapshot_id=NEW.id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Source completion requires its bound attempt'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO request FROM stewardship_source_refresh_request
        WHERE id=attempt.request_id;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_system_configuration c
        JOIN stewardship_applied_integration i
          ON i.configuration_id=c.active_configuration_id
        JOIN stewardship_task_run t ON t.id=attempt.task_id
        WHERE i.kind='parishsoft'
          AND i.credential_fingerprint=attempt.credential_fingerprint
          AND i.settings->>'organization_id'=request.organization_id::text
          AND c.current_campaign_id IS NOT DISTINCT FROM request.campaign_id
          AND NOT c.restore_review_required
          AND request.window_canonical=
              stewardship_source_current_window_v1(request.campaign_id)
          AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
              WHERE state IN ('preparing','running'))
          AND t.state='running' AND t.fence=attempt.task_fence
          AND t.lease_expires_at > clock_timestamp()
       ) OR NEW.cursor->>'schema' IS DISTINCT FROM 'source-refresh-v1'
       OR NEW.cursor->>'window_digest' IS DISTINCT FROM request.window_digest
       OR NEW.cursor->>'watermark' IS NULL
       OR (NEW.cursor->>'watermark')::timestamptz IS DISTINCT FROM NEW.started_at
    THEN
        RAISE EXCEPTION 'Source completion scope/credential/cursor is stale'
            USING ERRCODE='23514';
    END IF;
    IF NEW.kind='full' THEN
        IF NEW.cursor->>'full_snapshot_id' IS DISTINCT FROM NEW.id::text
           OR NEW.cursor->>'full_started_at' IS NULL
           OR (NEW.cursor->>'full_started_at')::timestamptz
              IS DISTINCT FROM NEW.started_at THEN
            RAISE EXCEPTION 'Full source cursor is not its own observation'
                USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT cursor INTO base_cursor FROM stewardship_source_snapshot
            WHERE id=NEW.base_id AND state='promoted';
        IF NOT FOUND OR base_cursor->>'schema' IS DISTINCT FROM 'source-refresh-v1'
           OR base_cursor->>'window_digest' IS DISTINCT FROM request.window_digest
           OR NEW.cursor->>'full_snapshot_id'
              IS DISTINCT FROM base_cursor->>'full_snapshot_id'
           OR NEW.cursor->>'full_started_at'
              IS DISTINCT FROM base_cursor->>'full_started_at' THEN
            RAISE EXCEPTION 'Delta source cursor lacks coherent full coverage'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_refresh_tick_guard_v1()
CREATE FUNCTION public.stewardship_refresh_tick_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE runtime stewardship_system_configuration%ROWTYPE;
        command stewardship_source_refresh_command%ROWTYPE;
        request stewardship_source_refresh_request%ROWTYPE;
        zone text;
        nightly text;
        scope_digest text;
        expected_key text;
        local_day date;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736229 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Refresh tick requires scheduler and work ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration FOR UPDATE;
    IF NOT FOUND OR runtime.active_configuration_id
       IS DISTINCT FROM NEW.configuration_id
       OR runtime.restore_review_required
       OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate
                  WHERE state IN ('preparing','running')) THEN
        RAISE EXCEPTION 'Refresh tick requires current admitted configuration'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO command FROM stewardship_source_refresh_command
        WHERE id=NEW.command_id;
    SELECT * INTO request FROM stewardship_source_refresh_request
        WHERE id=command.request_id;
    IF command.id IS NULL OR request.id IS NULL OR NEW.actor_id IS NOT NULL
       OR command.actor_id IS NOT NULL OR command.cause NOT IN ('nightly','delta')
       OR request.campaign_id IS DISTINCT FROM runtime.current_campaign_id
       OR request.window_canonical IS DISTINCT FROM
          stewardship_source_current_window_v1(request.campaign_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_applied_integration
          WHERE configuration_id=NEW.configuration_id AND kind='parishsoft'
            AND settings->>'organization_id'=request.organization_id::text) THEN
        RAISE EXCEPTION 'Refresh tick command does not match its current source scope'
            USING ERRCODE='23514';
    END IF;
    IF runtime.current_campaign_id IS NULL THEN
        SELECT timezone INTO zone FROM stewardship_parish
            WHERE configuration_id=NEW.configuration_id;
    ELSE
        SELECT cfg.timezone INTO zone FROM stewardship_campaign c
            JOIN stewardship_campaign_configuration cfg
                ON cfg.id=c.active_configuration_id
            WHERE c.id=runtime.current_campaign_id;
    END IF;
    SELECT coalesce(settings->>'nightly_time','02:00') INTO nightly
        FROM stewardship_applied_integration
        WHERE configuration_id=NEW.configuration_id AND kind='parishsoft';
    IF NEW.timezone IS DISTINCT FROM zone OR NEW.nightly_time IS DISTINCT FROM nightly
       OR NEW.due_at > clock_timestamp()
       OR NEW.due_at <> date_trunc('second',NEW.due_at) THEN
        RAISE EXCEPTION 'Refresh tick is not due under its applied cadence'
            USING ERRCODE='23514';
    END IF;
    IF command.cause='delta' THEN
        IF extract(second FROM NEW.due_at) <> 0
           OR mod(extract(minute FROM NEW.due_at AT TIME ZONE 'UTC')::int,15) <> 0 THEN
            RAISE EXCEPTION 'Delta tick must be a quarter-hour UTC slot'
                USING ERRCODE='23514';
        END IF;
    ELSE
        local_day := (NEW.due_at AT TIME ZONE public.stewardship_timezone_name_v1(zone))::date;
        IF NEW.due_at <> stewardship_resolve_local_v1(local_day+nightly::time,zone)
           AND NEW.due_at <> stewardship_resolve_local_v1(
               (local_day-1)+nightly::time,zone) THEN
            RAISE EXCEPTION 'Nightly tick must use canonical local-time resolution'
                USING ERRCODE='23514';
        END IF;
    END IF;
    scope_digest := encode(sha256(convert_to(stewardship_source_canonical(
        jsonb_build_object('organization_id',request.organization_id,
                           'window_digest',request.window_digest)),'UTF8')),'hex');
    expected_key := encode(sha256(convert_to(stewardship_source_canonical(
        jsonb_build_object('schema','source-refresh-slot-v1',
            'scope_fingerprint',scope_digest,'timezone',zone,
            'nightly_time',CASE WHEN command.cause='nightly' THEN nightly ELSE NULL END,
            'cause',command.cause,'due_at',
            to_char(NEW.due_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||'+00:00')
        ),'UTF8')),'hex');
    IF NEW.slot_key IS DISTINCT FROM expected_key THEN
        RAISE EXCEPTION 'Refresh tick identity does not match its exact inputs'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_rehearsal_code_mac_retention_v1()
CREATE FUNCTION public.stewardship_rehearsal_code_mac_retention_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF TG_OP='DELETE' AND EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch
            WHERE id=OLD.epoch_id AND state='invalidated') THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'Rehearsal MACs cannot change before invalidated cleanup'
        USING ERRCODE = '23514';
END $$;

-- FUNCTION: stewardship_rehearsal_credential_mutable_v1()
CREATE FUNCTION public.stewardship_rehearsal_credential_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."epoch_id" IS DISTINCT FROM OLD."epoch_id" OR NEW."family_id" IS DISTINCT FROM OLD."family_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_rehearsal_delete_v1()
CREATE FUNCTION public.stewardship_rehearsal_delete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch
                  WHERE id=OLD.epoch_id AND state='invalidated') THEN
        RAISE EXCEPTION 'Rehearsal details require invalidation before cleanup'
            USING ERRCODE='23514';
    END IF;
    RETURN OLD;
END $$;

-- FUNCTION: stewardship_rehearsal_epoch_mutable_v1()
CREATE FUNCTION public.stewardship_rehearsal_epoch_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR (OLD."invalidated_at" IS NOT NULL AND NEW."invalidated_at" IS DISTINCT FROM OLD."invalidated_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_rehearsal_reservation_immutable_v1()
CREATE FUNCTION public.stewardship_rehearsal_reservation_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_request_audit_v1()
CREATE FUNCTION public.stewardship_request_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
                BEGIN
                    INSERT INTO stewardship_audit_event
                        (id, created_at, actor_id, correlation_id,
                         event_type, subject_id)
                    VALUES (gen_random_uuid(), NEW.created_at, NEW.actor_id,
                            NEW.correlation_id, 'config_request_' || NEW.state,
                            NEW.request_id);
                    RETURN NEW;
                END;
                $$;

-- FUNCTION: stewardship_request_checkpoint_v1()
CREATE FUNCTION public.stewardship_request_checkpoint_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
                DECLARE
                    owner_id uuid;
                    requested_at timestamptz;
                    previous stewardship_config_checkpoint%ROWTYPE;
                BEGIN
                    SELECT actor_id, created_at INTO owner_id, requested_at
                    FROM stewardship_config_request WHERE id = NEW.request_id
                    FOR UPDATE;
                    IF NOT FOUND OR NEW.actor_id IS DISTINCT FROM owner_id
                       OR NEW.created_at < requested_at THEN
                        RAISE EXCEPTION 'Invalid configuration checkpoint attribution'
                            USING ERRCODE = '23514';
                    END IF;
                    SELECT * INTO previous FROM stewardship_config_checkpoint
                    WHERE request_id = NEW.request_id
                    ORDER BY sequence DESC LIMIT 1;
                    IF NOT FOUND THEN
                        IF NEW.sequence <> 1 OR NEW.state <> 'staged' THEN
                            RAISE EXCEPTION 'Configuration intake must start staged'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF previous.sequence <> 1 OR previous.state <> 'staged'
                          OR NEW.sequence <> 2 OR NEW.state <> 'cancelled'
                          OR NEW.created_at < previous.created_at THEN
                        RAISE EXCEPTION 'Invalid configuration intake transition'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$;

-- FUNCTION: stewardship_request_checkpoint_v2()
CREATE FUNCTION public.stewardship_request_checkpoint_v2() RETURNS trigger
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
                       AND (NEW.state = 'applied' OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
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

-- FUNCTION: stewardship_request_stage_v1()
CREATE FUNCTION public.stewardship_request_stage_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
                BEGIN
                    INSERT INTO stewardship_config_checkpoint
                        (id, created_at, actor_id, correlation_id, request_id,
                         sequence, state)
                    VALUES (gen_random_uuid(), NEW.created_at, NEW.actor_id,
                            NEW.correlation_id, NEW.id, 1, 'staged');
                    RETURN NEW;
                END;
                $$;

-- FUNCTION: stewardship_require_chair_receipt_v1(uuid, uuid)
CREATE FUNCTION public.stewardship_require_chair_receipt_v1(configuration uuid, snapshot uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE receipt stewardship_chair_reconciliation%ROWTYPE;
BEGIN
    SELECT * INTO receipt FROM stewardship_chair_reconciliation
        WHERE configuration_id=configuration AND snapshot_id=snapshot;
    IF receipt.id IS NULL OR receipt.decisions IS DISTINCT FROM
        stewardship_chair_decisions_v1(configuration) THEN
        RAISE EXCEPTION 'Seeded assignment reconciliation is incomplete'
            USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(receipt.decisions) decision
        LEFT JOIN stewardship_assignment_overlay overlay
          ON overlay.assignment_record_id=(decision->>'assignment_record_id')::uuid
        WHERE overlay.id IS NULL OR overlay.active IS DISTINCT FROM
                  (decision->>'active')::boolean
          OR overlay.source_snapshot_id IS DISTINCT FROM snapshot
          OR overlay.reason IS DISTINCT FROM decision->>'reason'
          OR (decision->>'active')::boolean = EXISTS (
              SELECT 1 FROM stewardship_chair_review review
              WHERE review.assignment_record_id=
                  (decision->>'assignment_record_id')::uuid
                AND review.closed_by_id IS NULL))
       OR EXISTS (SELECT 1 FROM stewardship_chair_review review
           WHERE review.closed_by_id IS NULL AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(receipt.decisions) decision
               WHERE (decision->>'assignment_record_id')::uuid=
                   review.assignment_record_id
                 AND decision->'active'='false'::jsonb)) THEN
        RAISE EXCEPTION 'Seeded assignment reconciliation effects are incomplete'
            USING ERRCODE='23514';
    END IF;
END;
$$;

-- FUNCTION: stewardship_resolve_local_v1(timestamp without time zone, text)
CREATE FUNCTION public.stewardship_resolve_local_v1(wall timestamp without time zone, zone text) RETURNS timestamp with time zone
    LANGUAGE plpgsql STABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE nominal timestamptz; earliest timestamptz; low timestamptz;
    high timestamptz; middle timestamptz; width bigint;
BEGIN
    -- Frozen IANA 2026c links, matching the v1 accepted-name catalog. Debian's
    -- PostgreSQL image omits some backward aliases; abbreviations such as EET
    -- may instead resolve to fixed offsets. Normalize names before all SQL
    -- conversions without changing the stored parish/campaign timezone name.
    zone := public.stewardship_timezone_name_v1(zone);
    nominal := wall AT TIME ZONE zone;
    -- Collect adjacent offset regimes, including non-hour shifts and a skipped
    -- whole day. Round trips distinguish folds from hypothetical gap offsets.
    WITH offsets AS (
        SELECT DISTINCT (probe AT TIME ZONE zone) - (probe AT TIME ZONE 'UTC') AS delta
        FROM generate_series(nominal - interval '48 hours',
                             nominal + interval '48 hours', interval '1 hour') probe
    ), candidates AS (
        SELECT (wall - delta) AT TIME ZONE 'UTC' AS instant FROM offsets
    )
    SELECT min(instant) FILTER (WHERE instant AT TIME ZONE zone = wall),
           min(instant), max(instant)
    INTO earliest, low, high FROM candidates;
    IF earliest IS NOT NULL THEN RETURN earliest; END IF;
    IF low IS NULL OR high IS NULL OR low >= high
       OR low AT TIME ZONE zone >= wall OR high AT TIME ZONE zone <= wall THEN
        RAISE EXCEPTION 'Local boundary cannot be resolved' USING ERRCODE = '23514';
    END IF;
    -- The candidate offsets bracket the transition. Select its first valid
    -- microsecond, not the PostgreSQL default that preserves minutes in a gap.
    LOOP
        width := extract(epoch FROM high - low) * 1000000;
        EXIT WHEN width <= 1;
        middle := low + (width / 2) * interval '1 microsecond';
        IF middle AT TIME ZONE zone < wall THEN low := middle;
        ELSE high := middle; END IF;
    END LOOP;
    RETURN high;
END $$;

-- FUNCTION: stewardship_restore_delivery_hold_mutable_v1()
CREATE FUNCTION public.stewardship_restore_delivery_hold_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."restore_id" IS DISTINCT FROM OLD."restore_id" OR NEW."definition_id" IS DISTINCT FROM OLD."definition_id" OR NEW."mode" IS DISTINCT FROM OLD."mode" OR NEW."target" IS DISTINCT FROM OLD."target" OR NEW."slot" IS DISTINCT FROM OLD."slot" OR NEW."backup_at" IS DISTINCT FROM OLD."backup_at" OR NEW."window_start" IS DISTINCT FROM OLD."window_start" OR NEW."window_end" IS DISTINCT FROM OLD."window_end" OR NEW."discovery" IS DISTINCT FROM OLD."discovery" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_restore_hold_resolution_immutable_v1()
CREATE FUNCTION public.stewardship_restore_hold_resolution_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_restore_hold_v1()
CREATE FUNCTION public.stewardship_restore_hold_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE r stewardship_system_configuration%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Restore hold history cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        PERFORM pg_advisory_xact_lock(736220,1);
        SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
        IF NEW.actor_id IS NULL OR NEW.state<>'unreviewed' OR NEW.version<>1 OR NEW.resolved_at IS NOT NULL OR NEW.recovery_occurrence_id IS NOT NULL
           OR NEW.evidence<>'' OR NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot='' OR NEW.discovery=''
           OR NEW.window_start<>NEW.backup_at
           OR r.id IS NULL OR NOT r.restore_review_required OR r.restore_released_at IS NOT NULL
           OR r.restore_id IS NULL OR r.restore_backup_at IS NULL OR r.restore_activated_at IS NULL
           OR NEW.restore_id IS DISTINCT FROM r.restore_id OR NEW.backup_at IS DISTINCT FROM r.restore_backup_at
           OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_definition d WHERE d.id=NEW.definition_id
               AND d.campaign_id=r.current_campaign_id) THEN
            RAISE EXCEPTION 'Invalid restore uncertainty inventory' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(SELECT 1 FROM stewardship_restore_hold_resolution k WHERE k.hold_id=NEW.id AND k.version=NEW.version
        AND k.state=NEW.state AND k.evidence=NEW.evidence AND k.recovery_occurrence_id IS NOT DISTINCT FROM NEW.recovery_occurrence_id
        AND k.actor_id IS NOT DISTINCT FROM NEW.actor_id AND k.correlation_id=NEW.correlation_id AND NEW.resolved_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Restore hold mutation requires exact resolution' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_restore_resolution_effect_v1()
CREATE FUNCTION public.stewardship_restore_resolution_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE stewardship_restore_delivery_hold SET state=NEW.state,evidence=NEW.evidence,recovery_occurrence_id=NEW.recovery_occurrence_id,
        resolved_at=stewardship_campaign_now_v1(),version=NEW.version,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.hold_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    SELECT gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'restore_hold_resolved',NEW.id,d.campaign_id
    FROM stewardship_restore_delivery_hold h JOIN stewardship_schedule_definition d ON d.id=h.definition_id WHERE h.id=NEW.hold_id;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_restore_resolution_v1()
CREATE FUNCTION public.stewardship_restore_resolution_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE h stewardship_restore_delivery_hold%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO h FROM stewardship_restore_delivery_hold WHERE id=NEW.hold_id FOR UPDATE;
    IF h.id IS NULL OR NEW.version<>h.version+1 OR NEW.actor_id IS NULL OR btrim(NEW.evidence)=''
       OR NEW.state NOT IN ('assumed_delivered','resend_authorized','not_applicable')
       OR h.state IN ('resend_authorized','not_applicable')
       OR (NEW.state='resend_authorized' AND NOT EXISTS(
           SELECT 1 FROM stewardship_schedule_occurrence o WHERE o.id=NEW.recovery_occurrence_id
           AND o.definition_id=h.definition_id AND o.mode=h.mode AND o.target=h.target AND o.slot=h.slot AND o.state='pending'
       )) OR (NEW.state<>'resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Invalid restore hold review or resend binding' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_runtime_activation_required_v1()
CREATE FUNCTION public.stewardship_runtime_activation_required_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM stewardship_system_configuration runtime
            CROSS JOIN stewardship_config_activation activation
            WHERE runtime.id = NEW.id
              AND runtime.active_configuration_id IS NOT NULL
              AND activation.sequence = 1
        ) THEN
            RAISE EXCEPTION 'Runtime creation requires atomic root activation'
                USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END;
    $$;

-- FUNCTION: stewardship_runtime_guard_v1()
CREATE FUNCTION public.stewardship_runtime_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Runtime configuration cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.configuration_sequence<>1 OR NEW.active_configuration_id IS NOT NULL OR NEW.mode<>'testing' OR NEW.restore_review_required
           OR NEW.restore_id IS NOT NULL OR NEW.restore_backup_at IS NOT NULL OR NEW.restore_activated_at IS NOT NULL OR NEW.restore_released_at IS NOT NULL THEN
            RAISE EXCEPTION 'Runtime must start unconfigured in Testing' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS DISTINCT FROM OLD.active_configuration_id THEN
        IF NEW.configuration_sequence<>OLD.configuration_sequence+1
           OR NEW.mode<>OLD.mode OR NEW.restore_review_required<>OLD.restore_review_required
           OR NOT EXISTS (SELECT 1 FROM stewardship_config_activation
               WHERE configuration_id=NEW.active_configuration_id AND predecessor_id IS NOT DISTINCT FROM OLD.active_configuration_id
                 AND sequence=OLD.configuration_sequence AND actor_id IS NOT DISTINCT FROM NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Runtime pointer requires exact activation evidence' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.configuration_sequence<>OLD.configuration_sequence OR NOT EXISTS (
            SELECT 1 FROM stewardship_runtime_transition t
            WHERE t.expected_version=OLD.version AND t.before_mode=OLD.mode AND t.after_mode=NEW.mode
              AND t.before_campaign_id IS NOT DISTINCT FROM OLD.current_campaign_id
              AND t.after_campaign_id IS NOT DISTINCT FROM NEW.current_campaign_id
              AND t.actor_id IS NOT DISTINCT FROM NEW.actor_id AND t.correlation_id=NEW.correlation_id
        ) THEN RAISE EXCEPTION 'Runtime mutation requires transition evidence' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_runtime_transition_effect_v1()
CREATE FUNCTION public.stewardship_runtime_transition_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE stewardship_system_configuration SET mode=NEW.after_mode, current_campaign_id=NEW.after_campaign_id,
        version=version+1, actor_id=NEW.actor_id, correlation_id=NEW.correlation_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'runtime_transition',NEW.id,NEW.before_campaign_id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_runtime_transition_immutable_v1()
CREATE FUNCTION public.stewardship_runtime_transition_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_runtime_transition_v1()
CREATE FUNCTION public.stewardship_runtime_transition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    IF r.version<>NEW.expected_version OR r.mode<>NEW.before_mode OR r.current_campaign_id IS DISTINCT FROM NEW.before_campaign_id THEN
        RAISE EXCEPTION 'Runtime transition is stale' USING ERRCODE='23514'; END IF;
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
    ELSE RAISE EXCEPTION 'Unsupported runtime transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_safe_context_v1(text, jsonb)
CREATE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome','reason']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'member_source' THEN ARRAY['family_duid','member_duid','field']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count']
        WHEN 'boundary' THEN ARRAY['occurrence_id','kind','intended_unix_microseconds','actual_unix_microseconds','lag_microseconds','before_state','after_state']
        WHEN 'schedule' THEN ARRAY['definition_id','previous_revision_id','selected_revision_id','cancelled_messages','skipped_occurrences','failed_occurrences','delivered_slots']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        IF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value<>'no_deliverable_recipient' THEN RETURN false; END IF;
        ELSIF key='kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('start','close') THEN RETURN false; END IF;
        ELSIF key IN ('before_state','after_state') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('draft','scheduled','active','closed','archived','purged') THEN RETURN false; END IF;
        ELSIF key='field' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname',
                'maiden_name','birth_date','gender','email','home_phone',
                'mobile_phone','work_phone','marital_status','language','death_date'
            ) THEN RETURN false; END IF;
        ELSIF key IN ('family_duid','member_duid') THEN
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,10}$' THEN RETURN false; END IF;
            IF text_value::numeric NOT BETWEEN 1 AND 2147483647 THEN RETURN false; END IF;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key='retryable' THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

-- FUNCTION: stewardship_schedule_definition_mutable_v1()
CREATE FUNCTION public.stewardship_schedule_definition_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR NEW."kind" IS DISTINCT FROM OLD."kind" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_schedule_definition_v1()
CREATE FUNCTION public.stewardship_schedule_definition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE r stewardship_system_configuration%ROWTYPE; chosen stewardship_schedule_revision%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Schedules are removed through configuration' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF r.restore_review_required THEN
        RAISE EXCEPTION 'Restore review holds schedule selection changes' USING ERRCODE='23514'; END IF;
    SELECT * INTO chosen FROM stewardship_schedule_revision
        WHERE configuration_id=r.active_configuration_id AND record_id=NEW.id;
    IF NEW.campaign_id IS DISTINCT FROM r.current_campaign_id
       OR NEW.actor_id IS DISTINCT FROM r.actor_id OR NEW.correlation_id<>r.correlation_id
       OR (NEW.current_revision_id IS NOT NULL AND (chosen.id IS NULL OR chosen.id<>NEW.current_revision_id OR chosen.kind<>NEW.kind OR chosen.campaign_id<>NEW.campaign_id))
       OR (NEW.current_revision_id IS NULL AND chosen.id IS NOT NULL) THEN
        RAISE EXCEPTION 'Schedule selection requires exact applied configuration' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND NEW.current_revision_id IS NOT DISTINCT FROM OLD.current_revision_id THEN
        RAISE EXCEPTION 'Schedule selection must change' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND EXISTS (
        SELECT 1 FROM stewardship_schedule_work_row o
        WHERE o.definition_id=NEW.id AND o.revision_id=OLD.current_revision_id AND o.blocking
    ) THEN RAISE EXCEPTION 'In-flight schedule work blocks replacement' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_schedule_fulfillment_immutable_v1()
CREATE FUNCTION public.stewardship_schedule_fulfillment_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF TG_OP='DELETE' AND public.stewardship_cleanup_effect_v1('schedule_fulfillments',OLD.id) THEN RETURN OLD; END IF;
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_schedule_occurrence_mutable_v1()
CREATE FUNCTION public.stewardship_schedule_occurrence_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."definition_id" IS DISTINCT FROM OLD."definition_id" OR NEW."revision_id" IS DISTINCT FROM OLD."revision_id" OR NEW."mode" IS DISTINCT FROM OLD."mode" OR NEW."routing" IS DISTINCT FROM OLD."routing" OR NEW."target" IS DISTINCT FROM OLD."target" OR NEW."slot" IS DISTINCT FROM OLD."slot" OR NEW."due_at" IS DISTINCT FROM OLD."due_at" OR NEW."occurrence_key" IS DISTINCT FROM OLD."occurrence_key" OR NEW."recovery_generation" IS DISTINCT FROM OLD."recovery_generation" OR NEW."production_cycle" IS DISTINCT FROM OLD."production_cycle" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_schedule_revision_immutable_v1()
CREATE FUNCTION public.stewardship_schedule_revision_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_schedule_selection_effect_v1()
CREATE FUNCTION public.stewardship_schedule_selection_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE config uuid; prior uuid; evidence jsonb; event uuid:=gen_random_uuid();
BEGIN
    SELECT active_configuration_id INTO config FROM stewardship_system_configuration;
    IF TG_OP='UPDATE' THEN prior:=OLD.current_revision_id; END IF;
    INSERT INTO stewardship_schedule_selection(id,definition_id,configuration_id,previous_revision_id,selected_revision_id,version,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.id,config,prior,NEW.current_revision_id,NEW.version,NEW.actor_id,NEW.correlation_id);
    IF prior IS NOT NULL THEN
        evidence:=public.stewardship_schedule_reconcile_v1(NEW.id,prior,NEW.actor_id,NEW.correlation_id);
    END IF;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(event,NEW.actor_id,NEW.correlation_id,'schedule_selected',NEW.id,NEW.campaign_id);
    IF evidence IS NOT NULL THEN
        INSERT INTO stewardship_audit_context(id,actor_id,correlation_id,event_id,actor_kind,schema,context)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,event,'portal_user','schedule',evidence);
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_schedule_selection_immutable_v1()
CREATE FUNCTION public.stewardship_schedule_selection_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_schedules_activate_v1()
CREATE FUNCTION public.stewardship_schedules_activate_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE revision stewardship_schedule_revision%ROWTYPE; previous stewardship_schedule_definition%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id OR NEW.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    FOR revision IN SELECT * FROM stewardship_schedule_revision
        WHERE configuration_id=NEW.active_configuration_id AND campaign_id=NEW.current_campaign_id ORDER BY record_id LOOP
        SELECT * INTO previous FROM stewardship_schedule_definition WHERE id=revision.record_id FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO stewardship_schedule_definition(id,campaign_id,kind,current_revision_id,version,actor_id,correlation_id)
            VALUES(revision.record_id,revision.campaign_id,revision.kind,revision.id,1,NEW.actor_id,NEW.correlation_id);
        ELSIF previous.current_revision_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_schedule_revision old_revision
            JOIN stewardship_campaign_configuration old_campaign
              ON old_campaign.configuration_id=old_revision.configuration_id
             AND old_campaign.record_id=old_revision.campaign_id
            JOIN stewardship_campaign_configuration new_campaign
              ON new_campaign.configuration_id=revision.configuration_id
             AND new_campaign.record_id=revision.campaign_id
            WHERE old_revision.id=previous.current_revision_id
              AND old_revision.values=revision.values
              AND old_campaign.timezone=new_campaign.timezone
              AND (revision.kind NOT IN ('daily_digest','weekly_digest')
                  OR (old_campaign.start_date=new_campaign.start_date
                      AND old_campaign.end_date=new_campaign.end_date))
        ) THEN
            UPDATE stewardship_schedule_definition SET current_revision_id=revision.id,removed_at=NULL,
                version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=previous.id;
        END IF;
    END LOOP;
    UPDATE stewardship_schedule_definition d SET current_revision_id=NULL,removed_at=statement_timestamp(),version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE d.campaign_id=NEW.current_campaign_id AND d.current_revision_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_revision WHERE configuration_id=NEW.active_configuration_id AND record_id=d.id);
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_sealed_intake_admission_v1()
CREATE FUNCTION public.stewardship_sealed_intake_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_TABLE_NAME='stewardship_secret_request' THEN
        IF NEW.required_consumers<>'[]'::jsonb
           AND NEW.reauthenticated_at<statement_timestamp()-interval '5 minutes' THEN
            RAISE EXCEPTION 'Sealed intake requires fresh authentication'
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

-- FUNCTION: stewardship_sealed_staging_guard_v1()
CREATE FUNCTION public.stewardship_sealed_staging_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE owner stewardship_secret_request%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        IF OLD.ciphertext IS NOT NULL THEN RAISE EXCEPTION 'Sealed staging must be scrubbed first' USING ERRCODE='23514'; END IF;
        RETURN OLD;
    END IF;
    SELECT * INTO owner FROM stewardship_secret_request WHERE id=NEW.request_id FOR UPDATE;
    IF NOT FOUND OR NEW.target<>owner.target OR NEW.reference<>owner.staging_reference THEN
        RAISE EXCEPTION 'Sealed staging requires its exact request target' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF owner.state<>'staged' OR owner.expires_at<=statement_timestamp()
           OR NOT coalesce(stewardship_valid_sealed_candidate_v1(NEW.ciphertext),false) THEN
            RAISE EXCEPTION 'Invalid sealed credential payload' USING ERRCODE='23514'; END IF;
    ELSIF (NEW.reference,NEW.request_id,NEW.target,NEW.fingerprint) IS DISTINCT FROM(OLD.reference,OLD.request_id,OLD.target,OLD.fingerprint)
        OR NEW.ciphertext IS NOT NULL OR OLD.ciphertext IS NULL OR owner.state NOT IN('awaiting_ack','cleanup_pending') THEN
        RAISE EXCEPTION 'Sealed staging permits only post-install or terminal scrubbing' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_secret_checkpoint_immutable_v1()
CREATE FUNCTION public.stewardship_secret_checkpoint_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_secret_checkpoint_v1()
CREATE FUNCTION public.stewardship_secret_checkpoint_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
                DECLARE owner stewardship_secret_request%ROWTYPE;
                BEGIN
                    SELECT * INTO owner FROM stewardship_secret_request
                    WHERE id = NEW.request_id FOR UPDATE;
                    IF NOT FOUND OR NEW.sequence <> owner.version
                       OR NEW.state <> owner.state
                       OR NEW.actor_id IS DISTINCT FROM owner.actor_id
                       OR NEW.correlation_id <> owner.correlation_id
                       OR NEW.created_at <> owner.updated_at THEN
                        RAISE EXCEPTION 'Invalid secret checkpoint binding'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$;

-- FUNCTION: stewardship_secret_history_v1()
CREATE FUNCTION public.stewardship_secret_history_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
                BEGIN
                    INSERT INTO stewardship_secret_checkpoint
                        (id, created_at, actor_id, correlation_id,
                         request_id, sequence, state)
                    VALUES (gen_random_uuid(), NEW.updated_at,
                            NEW.actor_id, NEW.correlation_id,
                            NEW.id, NEW.version, NEW.state);
                    INSERT INTO stewardship_audit_event
                        (id, created_at, actor_id, correlation_id,
                         event_type, subject_id)
                    VALUES (gen_random_uuid(), NEW.updated_at,
                            NEW.actor_id, NEW.correlation_id,
                            'secret_request_' || NEW.state, NEW.id);
                    RETURN NEW;
                END;
                $$;

-- FUNCTION: stewardship_secret_request_mutable_v1()
CREATE FUNCTION public.stewardship_secret_request_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."target" IS DISTINCT FROM OLD."target" OR NEW."staging_reference" IS DISTINCT FROM OLD."staging_reference" OR NEW."requested_by_id" IS DISTINCT FROM OLD."requested_by_id" OR NEW."reauthenticated_at" IS DISTINCT FROM OLD."reauthenticated_at" OR NEW."expires_at" IS DISTINCT FROM OLD."expires_at" OR NEW."expected_fingerprint" IS DISTINCT FROM OLD."expected_fingerprint" OR NEW."required_consumers" IS DISTINCT FROM OLD."required_consumers" OR (OLD."resulting_fingerprint" IS NOT NULL AND NEW."resulting_fingerprint" IS DISTINCT FROM OLD."resulting_fingerprint") OR (OLD."installed_at" IS NOT NULL AND NEW."installed_at" IS DISTINCT FROM OLD."installed_at") OR (OLD."acknowledged_at" IS NOT NULL AND NEW."acknowledged_at" IS DISTINCT FROM OLD."acknowledged_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_secret_state_v1()
CREATE FUNCTION public.stewardship_secret_state_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'Secret request history cannot be deleted'
                            USING ERRCODE = '23514';
                    ELSIF TG_OP = 'INSERT' THEN
                        -- Caller timestamps cannot move the lifetime ceiling.
                        NEW.created_at := statement_timestamp();
                        NEW.updated_at := NEW.created_at;
                        IF NEW.state <> 'staged' OR NEW.version <> 1
                           OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
                           OR NEW.expires_at <= statement_timestamp() THEN
                            RAISE EXCEPTION 'Invalid secret request intake'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF OLD.state = 'staged' AND NEW.state = 'cleanup_pending' THEN
                        IF NEW.cleanup_reason = 'expired'
                           AND NEW.actor_id IS NOT NULL THEN
                            RAISE EXCEPTION 'Expiry requires system attribution'
                                USING ERRCODE = '23514';
                        END IF;
                        IF NEW.cleanup_reason = 'expired'
                           AND OLD.expires_at > statement_timestamp() THEN
                            RAISE EXCEPTION 'Secret request is not expired'
                                USING ERRCODE = '23514';
                        END IF;
                        IF NEW.cleanup_reason = 'cancelled'
                           AND NEW.actor_id IS DISTINCT FROM OLD.requested_by_id THEN
                            RAISE EXCEPTION 'Invalid cancellation attribution'
                                USING ERRCODE = '23514';
                        END IF;
                    ELSIF OLD.state = 'cleanup_pending'
                          AND NEW.state = OLD.cleanup_reason
                          AND NEW.cleanup_reason = OLD.cleanup_reason THEN
                        IF NEW.actor_id IS NOT NULL THEN
                            RAISE EXCEPTION 'Cleanup requires system attribution'
                                USING ERRCODE = '23514';
                        END IF;
                        NEW.scrubbed_at := statement_timestamp();
                    ELSE
                        RAISE EXCEPTION 'Invalid secret request transition'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$;

-- FUNCTION: stewardship_secret_state_v2()
CREATE FUNCTION public.stewardship_secret_state_v2() RETURNS trigger
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
        IF NEW.actor_id IS NOT NULL OR NEW.expires_at<=statement_timestamp()
           OR (NEW.reauthenticated_at<NEW.created_at-interval '5 minutes' AND NOT (CASE WHEN NEW.target IN ('parishsoft','google_workspace','slack') THEN public.stewardship_setup_install_live_v1(NEW.id) ELSE false END))
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

-- FUNCTION: stewardship_setup_activation_owner_v1()
CREATE FUNCTION public.stewardship_setup_activation_owner_v1() RETURNS trigger
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

-- FUNCTION: stewardship_setup_attempt_audit_v1()
CREATE FUNCTION public.stewardship_setup_attempt_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid(); kind text;
BEGIN
    IF TG_OP='UPDATE' AND NEW.state=OLD.state THEN RETURN NULL; END IF;
    kind=CASE NEW.state WHEN 'collecting' THEN
            CASE WHEN TG_OP='INSERT' THEN 'setup_started' ELSE 'setup_source_completed' END
        WHEN 'loading' THEN 'setup_source_started'
        WHEN 'frozen' THEN 'setup_frozen'
        WHEN 'completed' THEN 'setup_completed' WHEN 'expired' THEN 'setup_expired'
        ELSE NULL END;
    IF kind IS NULL THEN
        RAISE EXCEPTION 'Setup audit requires an implemented lifecycle state'
            USING ERRCODE='23514';
    END IF;
    INSERT INTO stewardship_audit_event(id,created_at,actor_id,correlation_id,
        event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,kind,NEW.id,'deployment');
    INSERT INTO stewardship_audit_context(id,created_at,actor_id,correlation_id,
        event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',
            CASE WHEN NEW.state='completed' THEN 'succeeded' WHEN NEW.state='expired' THEN 'cancelled' ELSE 'changed' END));
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_attempt_guard_v1()
CREATE FUNCTION public.stewardship_setup_attempt_guard_v1() RETURNS trigger
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
        AND login.expires_at>stamp AND login.last_activity_at>stamp-interval '30 minutes';
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
        WHEN stamp>=login.last_activity_at+interval '30 minutes' THEN 'idle'
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

-- FUNCTION: stewardship_setup_attempt_mutable_v1()
CREATE FUNCTION public.stewardship_setup_attempt_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."session_id" IS DISTINCT FROM OLD."session_id" OR NEW."owner_id" IS DISTINCT FROM OLD."owner_id" OR NEW."base_id" IS DISTINCT FROM OLD."base_id" OR (OLD."source_task_id" IS NOT NULL AND NEW."source_task_id" IS DISTINCT FROM OLD."source_task_id") OR (OLD."expired_at" IS NOT NULL AND NEW."expired_at" IS DISTINCT FROM OLD."expired_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_completion_cleanup_write_v1()
CREATE FUNCTION public.stewardship_setup_completion_cleanup_write_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE attempt_id uuid;
BEGIN
    IF current_user='pk_stewardship_worker' THEN
        IF TG_TABLE_NAME='stewardship_setup_mail_exchange' THEN
            SELECT delivery.attempt_id INTO attempt_id FROM public.stewardship_setup_mail_delivery delivery
                WHERE delivery.id=NEW.delivery_id;
        ELSE attempt_id=NEW.attempt_id;
        END IF;
        IF NEW.scrubbed_at IS NULL OR NOT public.stewardship_setup_completion_scrub_v1(attempt_id) THEN
            RAISE EXCEPTION 'Worker setup scrub requires its atomic completed owner'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_completion_context_v1()
CREATE FUNCTION public.stewardship_setup_completion_context_v1() RETURNS uuid
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
        AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
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

-- FUNCTION: stewardship_setup_completion_immutable_v1()
CREATE FUNCTION public.stewardship_setup_completion_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_completion_insert_v1()
CREATE FUNCTION public.stewardship_setup_completion_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.preparation_id IS DISTINCT FROM public.stewardship_setup_completion_context_v1()
        OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_prepared prepared
            JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
            JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
            JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            JOIN public.stewardship_config_activation activation
                ON activation.id=NEW.activation_id AND activation.request_id=intent.request_id
                AND activation.configuration_id=prepared.configuration_id
            JOIN public.stewardship_system_configuration runtime
                ON runtime.active_configuration_id=prepared.configuration_id
                AND runtime.current_campaign_id=attempt.id AND runtime.mode='testing'
                AND runtime.testing_recipient=ready.testing_recipient
            JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=NEW.snapshot_id
                AND snapshot.task_id=NEW.task_id AND snapshot.source_fence=NEW.source_fence
                AND snapshot.state='promoted' AND snapshot.kind='full'
            JOIN public.stewardship_source_current current ON current.snapshot_id=snapshot.id
            JOIN public.stewardship_source_lease lease ON lease.owner_id=NEW.task_id
                AND lease.fence=NEW.source_fence AND lease.task_fence=NEW.task_fence
            JOIN public.stewardship_campaign_credentials population
                ON population.campaign_id=attempt.id AND population.source_snapshot_id=snapshot.id
                AND population.source_generation=snapshot.generation AND NOT population.population_dirty
                AND population.eligible_count=(SELECT count(*) FROM public.stewardship_family_campaign
                    WHERE campaign_id=attempt.id AND portal_eligible)
            WHERE prepared.id=NEW.preparation_id AND NEW.actor_id=attempt.owner_id
                AND snapshot.cursor->>'window_digest'=encode(sha256(convert_to(
                    public.stewardship_source_current_window_v1(attempt.id),'UTF8')),'hex')
                AND (SELECT count(*) FROM public.stewardship_family_campaign WHERE campaign_id=attempt.id)
                    = (SELECT count(*) FROM public.stewardship_snapshot_family WHERE snapshot_id=snapshot.id)
                AND NOT EXISTS (
                    SELECT 1 FROM public.stewardship_snapshot_family member
                    JOIN public.stewardship_source_family payload ON payload.id=member.payload_id
                    LEFT JOIN public.stewardship_family_campaign family ON family.campaign_id=attempt.id
                        AND family.family_duid=member.source_key::bigint
                    WHERE member.snapshot_id=snapshot.id AND (
                        family.id IS NULL OR family.source_generation IS DISTINCT FROM snapshot.generation
                        OR family.active IS DISTINCT FROM (payload.canonical::jsonb->>'active')::boolean
                        OR family.portal_eligible IS DISTINCT FROM (payload.canonical::jsonb->>'portal_eligible')::boolean
                        OR family.email_eligible IS DISTINCT FROM (payload.canonical::jsonb->>'email_eligible')::boolean
                    )
                )
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign family
                    WHERE family.campaign_id=attempt.id AND family.portal_eligible
                        AND (coalesce(family.code_ciphertext,'')='' OR NOT EXISTS (
                            SELECT 1 FROM public.stewardship_family_code_mac mac WHERE mac.family_id=family.id)))
        ) THEN
        RAISE EXCEPTION 'Setup completion requires exact activation, source and population'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_completion_required_v1()
CREATE FUNCTION public.stewardship_setup_completion_required_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE prepared_id uuid;
BEGIN
    IF TG_TABLE_NAME='stewardship_config_activation' THEN
        SELECT prepared.id INTO prepared_id FROM public.stewardship_setup_prepared prepared
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        WHERE intent.request_id=NEW.request_id;
    ELSIF TG_TABLE_NAME='stewardship_source_current' THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_source_snapshot snapshot
            JOIN public.stewardship_task_run task ON task.id=snapshot.task_id
            WHERE snapshot.id=NEW.snapshot_id AND task.task_type='setup_source_load') THEN
            RAISE EXCEPTION 'Initial catalog is never promoted source truth' USING ERRCODE='23514';
        END IF;
        SELECT task.domain_request_id INTO prepared_id FROM public.stewardship_source_snapshot snapshot
        JOIN public.stewardship_task_run task ON task.id=snapshot.task_id AND task.task_type='setup_finalize'
        WHERE snapshot.id=NEW.snapshot_id;
    ELSE
        prepared_id=NEW.preparation_id;
    END IF;
    IF prepared_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_completion completed
        JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id AND attempt.state='completed'
        JOIN public.stewardship_task_run task ON task.id=completed.task_id
            AND task.state='succeeded' AND task.fence=completed.task_fence
        WHERE completed.preparation_id=prepared_id
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_lease WHERE owner_id=task.id)
    ) THEN
        RAISE EXCEPTION 'Initial configuration and source require atomic completion'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_completion_scrub_v1(uuid)
CREATE FUNCTION public.stewardship_setup_completion_scrub_v1(attempt_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN false; END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.stewardship_setup_completion completed
        JOIN public.stewardship_setup_prepared prepared ON prepared.id=completed.preparation_id
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='completed'
        JOIN public.stewardship_task_run task ON task.id=completed.task_id
            AND task.fence=completed.task_fence AND task.state='running'
            AND task.lease_expires_at>clock_timestamp()
        JOIN public.stewardship_source_lease lease ON lease.owner_id=task.id
            AND lease.task_fence=task.fence AND lease.worker_id=task.worker_id
            AND lease.fence=completed.source_fence AND lease.expires_at>clock_timestamp()
        WHERE attempt.id=$1
    );
END $_$;

-- FUNCTION: stewardship_boundary_write_admitted_v1(text, jsonb, jsonb)
CREATE FUNCTION public.stewardship_boundary_write_admitted_v1(
    relation_name text, proposed jsonb, prior jsonb
) RETURNS boolean
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE target_campaign uuid; claim public.stewardship_task_run%ROWTYPE;
        transition public.stewardship_campaign_transition%ROWTYPE;
BEGIN
    -- Read-only evidence, never a caller-selected execution-context flag.
    PERFORM pg_advisory_xact_lock(736220,1);
    IF relation_name='stewardship_runtime_transition' THEN
        SELECT event.* INTO transition FROM public.stewardship_campaign_transition event
            WHERE event.id=(proposed->>'campaign_transition_id')::uuid;
        RETURN transition.id IS NOT NULL AND proposed->>'action'='campaign'
            AND proposed->>'reason'='' AND proposed->>'restore_id' IS NULL
            AND proposed->>'backup_at' IS NULL
            AND (proposed->>'request_id')::uuid=transition.request_id
            AND (proposed->>'actor_id')::uuid=transition.actor_id
            AND (proposed->>'correlation_id')::uuid=transition.correlation_id
            AND public.stewardship_boundary_write_admitted_v1(
                'stewardship_campaign_transition',to_jsonb(transition),NULL);
    END IF;
    IF relation_name NOT IN ('stewardship_campaign_boundary', 'stewardship_campaign',
        'stewardship_system_configuration', 'stewardship_campaign_transition') THEN
        RETURN false;
    END IF;
    target_campaign := CASE relation_name
        WHEN 'stewardship_campaign' THEN (proposed->>'id')::uuid
        WHEN 'stewardship_system_configuration' THEN (proposed->>'current_campaign_id')::uuid
        ELSE (proposed->>'campaign_id')::uuid END;
    SELECT task.* INTO claim FROM public.stewardship_task_run task
        JOIN public.stewardship_task_run root ON root.id=task.root_id
        JOIN public.stewardship_campaign_boundary target
            ON root.idempotency_key=target.id::text AND target.campaign_id=target_campaign
        JOIN public.stewardship_campaign c ON c.id=target_campaign
        JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_credentials credentials
            ON credentials.campaign_id=c.id AND NOT credentials.go_live_gate
        WHERE task.task_type='campaign_boundary' AND task.domain_request_id=target_campaign
            AND root.task_type=task.task_type AND root.domain_request_id=target_campaign
            AND task.state='running' AND task.lease_expires_at>clock_timestamp()
            AND task.worker_id=(proposed->>'actor_id')::uuid
            AND task.correlation_id=(proposed->>'correlation_id')::uuid
            AND r.mode='production' AND NOT r.restore_review_required
            AND c.state IN ('scheduled','active','closed')
            AND target.due_at=CASE target.kind WHEN 'start' THEN p.starts_at ELSE p.ends_at END
            AND target.due_at<=public.stewardship_campaign_now_v1()
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_boundary newer
                WHERE newer.campaign_id=target.campaign_id AND newer.kind=target.kind
                    AND newer.execution_revision>target.execution_revision)
            AND (target.state='pending' OR (target.task_id=task.id AND target.task_fence=task.fence))
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_work_gate g
                WHERE g.campaign_id=c.id AND g.state<>'released');
    IF claim.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_campaign_boundary' THEN
        -- Initial predecessor allocation has no execution binding yet; its
        -- ordinary guard still checks exact kind/date and immutable identity.
        IF prior IS NULL THEN
            RETURN proposed->>'state'='pending' AND proposed->>'task_id' IS NULL;
        END IF;
        IF (proposed->>'task_id')::uuid IS DISTINCT FROM claim.id
            OR (proposed->>'task_fence')::bigint IS DISTINCT FROM claim.fence THEN
            RETURN false;
        END IF;
        IF proposed->>'state'='pending' THEN
            RETURN (proposed-ARRAY['task_id','task_fence','version','updated_at',
                       'actor_id','correlation_id'])
                = (prior-ARRAY['task_id','task_fence','version','updated_at',
                       'actor_id','correlation_id']);
        END IF;
        RETURN (proposed-ARRAY['state','reason','completed_at','transition_id',
                    'version','updated_at','actor_id','correlation_id'])
                = (prior-ARRAY['state','reason','completed_at','transition_id',
                    'version','updated_at','actor_id','correlation_id'])
            AND ((proposed->>'state'='succeeded' AND proposed->>'reason'='')
                OR (proposed->>'state'='skipped' AND proposed->>'reason'='not_applicable'))
            AND (proposed->>'completed_at')::timestamptz=public.stewardship_campaign_now_v1();
    ELSIF relation_name='stewardship_campaign_transition' THEN
        RETURN proposed->>'action' IN ('start','close')
            AND proposed->>'reason'='' AND proposed->>'prior_projection_id' IS NULL
            AND proposed->>'token_generation_id' IS NULL
            AND (proposed->>'task_fence')::bigint=claim.fence
            AND EXISTS(SELECT 1 FROM public.stewardship_campaign_boundary b
                WHERE b.id=(proposed->>'boundary_id')::uuid AND b.campaign_id=target_campaign
                    AND b.task_id=claim.id AND b.task_fence=claim.fence AND b.state='pending');
    END IF;
    IF prior IS NULL THEN RETURN false; END IF;
    SELECT event.* INTO transition FROM public.stewardship_campaign_transition event
        WHERE event.campaign_id=target_campaign AND event.action IN ('start','close')
            AND event.actor_id=claim.worker_id AND event.correlation_id=claim.correlation_id
            AND event.task_fence=claim.fence
            AND CASE relation_name WHEN 'stewardship_campaign'
                THEN event.expected_version=(prior->>'version')::bigint
                ELSE event.expected_runtime_version=(prior->>'version')::bigint END;
    IF transition.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_campaign' THEN
        RETURN proposed->>'state'=transition.after_state
            AND (proposed-ARRAY['state','ever_active','active_token_generation_id',
                'version','updated_at','actor_id','correlation_id'])
                = (prior-ARRAY['state','ever_active','active_token_generation_id',
                'version','updated_at','actor_id','correlation_id']);
    END IF;
    RETURN (proposed-ARRAY['version','updated_at','actor_id','correlation_id'])
        = (prior-ARRAY['version','updated_at','actor_id','correlation_id']);
END $$;

-- FUNCTION: stewardship_boundary_worker_transition_v1()
CREATE FUNCTION public.stewardship_boundary_worker_transition_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    -- Background roles have neither Admin control nor configuration-authoring
    -- capabilities. Their added journal INSERT grant cannot activate or reopen.
    IF NOT has_table_privilege(current_user,'public.stewardship_campaign_control','INSERT')
       AND NOT has_table_privilege(current_user,'public.stewardship_configuration_version','INSERT')
       AND public.stewardship_boundary_write_admitted_v1(
           TG_TABLE_NAME,to_jsonb(NEW),
           CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
        RAISE EXCEPTION 'Worker lifecycle requires exact boundary ownership'
            USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_boundary_scrub_v1()
CREATE FUNCTION public.stewardship_boundary_scrub_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE target_campaign uuid; event public.stewardship_campaign_transition%ROWTYPE;
        permitted boolean;
BEGIN
    -- Full credential owners retain their existing workflows. The general
    -- worker has closing-boundary or exact inactive-preparation ownership,
    -- never private secret read/replacement authority.
    IF has_column_privilege(current_user,'public.stewardship_family_token','ciphertext','SELECT') THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    IF public.stewardship_production_token_write_v1(TG_TABLE_NAME,to_jsonb(NEW),to_jsonb(OLD)) THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='stewardship_family_session' THEN
        SELECT campaign_id INTO target_campaign FROM public.stewardship_family_campaign
            WHERE id=NEW.family_id;
        permitted := OLD.revoked_at IS NULL
            AND NEW.revoked_at=greatest(public.stewardship_campaign_now_v1(),OLD.last_activity_at)
            AND (to_jsonb(NEW)-ARRAY['revoked_at','version','updated_at'])
                = (to_jsonb(OLD)-ARRAY['revoked_at','version','updated_at']);
    ELSIF TG_TABLE_NAME='stewardship_family_token' THEN
        target_campaign := NEW.campaign_id;
        permitted := OLD.destroyed_at IS NULL AND NEW.ciphertext IS NULL AND NEW.digest IS NULL
            AND NEW.destroyed_at=public.stewardship_campaign_now_v1()
            AND (to_jsonb(NEW)-ARRAY['ciphertext','digest','destroyed_at','version','updated_at'])
                = (to_jsonb(OLD)-ARRAY['ciphertext','digest','destroyed_at','version','updated_at']);
    ELSE
        target_campaign := NEW.campaign_id;
        permitted := OLD.state NOT IN ('superseded','cancelled') AND NEW.state='superseded'
            AND (to_jsonb(NEW)-ARRAY['state','version','updated_at'])
                = (to_jsonb(OLD)-ARRAY['state','version','updated_at']);
    END IF;
    SELECT t.* INTO event FROM public.stewardship_campaign_transition t
        JOIN public.stewardship_campaign c ON c.id=t.campaign_id
        WHERE c.id=target_campaign AND c.state='closed' AND t.action='close'
            AND c.version=t.expected_version+1 AND c.actor_id=t.actor_id
            AND c.correlation_id=t.correlation_id;
    IF permitted IS NOT TRUE OR event.id IS NULL OR
        public.stewardship_boundary_write_admitted_v1(
            'stewardship_campaign_transition',to_jsonb(event),NULL) IS NOT TRUE THEN
        RAISE EXCEPTION 'Worker credential scrub requires its exact closing boundary'
            USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_catchup_write_admitted_v1(
    relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target public.stewardship_schedule_definition%ROWTYPE;
        occurrence public.stewardship_schedule_occurrence%ROWTYPE;
BEGIN
    IF relation_name='stewardship_occurrence_transition' THEN
        SELECT * INTO occurrence FROM public.stewardship_schedule_occurrence
            WHERE id=(proposed->>'occurrence_id')::uuid;
        RETURN prior IS NULL AND occurrence.id IS NOT NULL
            AND (proposed->>'version')::bigint=occurrence.version
            AND proposed->>'after_state'=occurrence.state
            AND (proposed->>'actor_id')::uuid=occurrence.actor_id
            AND (proposed->>'correlation_id')::uuid=occurrence.correlation_id
            AND public.stewardship_catchup_write_admitted_v1(
                'stewardship_schedule_occurrence',to_jsonb(occurrence),
                CASE WHEN occurrence.state='pending' THEN NULL
                    ELSE to_jsonb(occurrence)||jsonb_build_object('state','pending') END);
    END IF;
    IF relation_name<>'stewardship_schedule_occurrence' THEN RETURN false; END IF;
    SELECT * INTO target FROM public.stewardship_schedule_definition
        WHERE id=(proposed->>'definition_id')::uuid;
    RETURN target.id IS NOT NULL
        AND (proposed->>'revision_id')::uuid=target.current_revision_id
        AND proposed->>'mode'='production' AND proposed->>'routing'='production'
        AND proposed->>'task_id' IS NULL AND proposed->>'outbox_id' IS NULL
        AND proposed->>'state' IN ('pending','skipped','coalesced')
        AND ((prior IS NULL AND proposed->>'state'='pending')
            OR (prior IS NOT NULL AND proposed->>'state' IN ('skipped','coalesced')))
        AND (prior IS NULL OR (prior->>'state'='pending'
            AND prior->>'task_id' IS NULL AND prior->>'outbox_id' IS NULL))
        AND EXISTS (
            SELECT 1 FROM public.stewardship_activation_catchup d
            JOIN public.stewardship_system_configuration r ON r.current_campaign_id=d.campaign_id
            JOIN public.stewardship_campaign c ON c.id=d.campaign_id
            JOIN public.stewardship_campaign_credentials credentials
                ON credentials.campaign_id=c.id AND NOT credentials.go_live_gate
            WHERE d.campaign_id=target.campaign_id AND d.completed_at IS NULL
              AND (proposed->>'due_at')::timestamptz<=d.cutoff
              AND r.mode='production' AND NOT r.restore_review_required
              AND c.state IN ('active','closed')
              AND NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_work_gate g
                  WHERE g.campaign_id=c.id AND g.state<>'released')
        )
        AND public.stewardship_catchup_occurrence_shape_v1(proposed)
        AND public.stewardship_catchup_materializing_v1(target.campaign_id,
            (proposed->>'actor_id')::uuid,(proposed->>'correlation_id')::uuid);
END $$;

-- FUNCTION: stewardship_setup_completion_write_v1()
CREATE FUNCTION public.stewardship_setup_completion_write_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
        public.stewardship_setup_completion_context_v1() IS NULL AND
        public.stewardship_boundary_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        public.stewardship_catchup_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        public.stewardship_family_mail_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        public.stewardship_daily_digest_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        public.stewardship_weekly_digest_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
        RAISE EXCEPTION 'Worker configuration effects require atomic setup ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_config_abort_immutable_v1()
CREATE FUNCTION public.stewardship_setup_config_abort_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_config_abort_v1()
CREATE FUNCTION public.stewardship_setup_config_abort_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE a public.stewardship_setup_attempt%ROWTYPE;
    q public.stewardship_config_request%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736212 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup abort requires configuration serialization' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT s.* INTO a FROM public.stewardship_setup_attempt s
        JOIN public.stewardship_setup_config_intent i ON i.attempt_id=s.id
        WHERE i.id=NEW.intent_id;
    SELECT request.* INTO q FROM public.stewardship_config_request request
        JOIN public.stewardship_setup_config_intent i ON i.request_id=request.id
        WHERE i.id=NEW.intent_id FOR UPDATE OF request;
    IF a.id IS NULL OR q.id IS NULL OR a.state<>'expired'
        OR NEW.reason IS DISTINCT FROM a.expiry_reason
        OR (NEW.actor_id IS NOT NULL AND NEW.actor_id IS DISTINCT FROM a.owner_id)
        OR (NEW.reason='cancelled' AND NEW.actor_id IS DISTINCT FROM a.owner_id)
        OR r.active_configuration_id IS DISTINCT FROM a.base_id
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_configuration_version v
            WHERE v.id=q.candidate_version_id AND v.digest=q.candidate_digest
                AND v.predecessor_id=q.base_id)
        OR EXISTS (SELECT 1 FROM public.stewardship_config_activation WHERE request_id=q.id)
        OR coalesce((SELECT state FROM public.stewardship_config_checkpoint
            WHERE request_id=q.id ORDER BY sequence DESC LIMIT 1)
            NOT IN ('validating','prepared','yaml_activated'),true) THEN
        RAISE EXCEPTION 'Setup abort requires its expired never-applied candidate'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_config_intent_immutable_v1()
CREATE FUNCTION public.stewardship_setup_config_intent_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_config_intent_v1()
CREATE FUNCTION public.stewardship_setup_config_intent_v1() RETURNS trigger
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
                AND s.last_activity_at>clock_timestamp()-interval '30 minutes') THEN
        RAISE EXCEPTION 'Setup intent requires its frozen original Admin attempt'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_consumers_current_v1(uuid)
CREATE FUNCTION public.stewardship_setup_consumers_current_v1(preparation_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE readiness public.stewardship_setup_readiness_binding%ROWTYPE;
    candidate public.stewardship_configuration_version%ROWTYPE;
    integration jsonb; bound public.stewardship_setup_credential_install%ROWTYPE;
    installed public.stewardship_secret_request%ROWTYPE; target_count integer=0;
BEGIN
    -- Serialize the observation through activation against the existing secret
    -- state owner. This follows setup's installation/work/secret lock order;
    -- no credential file or provider IO occurs while this transaction is held.
    PERFORM pg_advisory_xact_lock(736213,1);
    SELECT ready.* INTO readiness FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_prepared prepared ON prepared.readiness_id=ready.id
        WHERE prepared.id=$1;
    SELECT version.* INTO candidate FROM public.stewardship_configuration_version version
        JOIN public.stewardship_setup_prepared prepared ON prepared.configuration_id=version.id
        WHERE prepared.id=$1;
    IF readiness.id IS NULL OR candidate.id IS NULL THEN RETURN false; END IF;
    FOR integration IN SELECT value->'values' FROM jsonb_array_elements(
        candidate.canonical_document->'sections'->'integrations')
        WHERE value->'values'->>'kind' IN ('parishsoft','google_workspace','slack')
    LOOP
        target_count=target_count+1;
        SELECT id,request_id,target,fingerprint INTO bound.id,bound.request_id,bound.target,bound.fingerprint
            FROM public.stewardship_setup_credential_install
            WHERE readiness_id=readiness.id AND target=integration->>'kind';
        SELECT * INTO installed FROM public.stewardship_secret_request WHERE id=bound.request_id;
        IF bound.id IS NULL OR installed.id IS NULL
            OR installed.target IS DISTINCT FROM bound.target
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
            RETURN false;
        END IF;
    END LOOP;
    RETURN target_count=(CASE WHEN readiness.slack_delivery_id IS NULL THEN 2 ELSE 3 END)
        AND target_count=(SELECT count(*) FROM public.stewardship_setup_credential_install
            WHERE readiness_id=readiness.id);
END $_$;

-- FUNCTION: stewardship_setup_credential_install_immutable_v1()
CREATE FUNCTION public.stewardship_setup_credential_install_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_disposable_payload_v1(text, uuid)
CREATE FUNCTION public.stewardship_setup_disposable_payload_v1(kind text, identifier uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE permitted boolean;
BEGIN
    IF kind NOT IN ('family','member','contact','address','ministry','roster',
                    'fund','pledge','contribution') THEN RETURN false; END IF;
    -- Each payload must still have an exact expired-setup membership. The
    -- service deletes payloads before those memberships in the same transaction;
    -- deferred foreign keys reject any attempt to leave a dangling reference.
    EXECUTE format('SELECT count(*)>0 AND bool_and('
        'public.stewardship_setup_disposable_snapshot_v1(snapshot_id)) '
        'FROM public.%I WHERE payload_id=$1', 'stewardship_snapshot_'||kind)
        INTO permitted USING identifier;
    RETURN COALESCE(permitted,false);
END $_$;

-- FUNCTION: stewardship_setup_disposable_snapshot_v1(uuid)
CREATE FUNCTION public.stewardship_setup_disposable_snapshot_v1(identifier uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    SELECT current_user='pk_stewardship_worker' AND EXISTS (
        SELECT 1 FROM public.stewardship_source_lease lease
        JOIN public.stewardship_task_run task ON task.id=lease.owner_id
        JOIN public.stewardship_setup_attempt attempt
            ON attempt.id=task.domain_request_id
        JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=identifier
        JOIN public.stewardship_task_run original ON original.id=snapshot.task_id
        WHERE task.task_type='setup_source_cleanup' AND task.state='running'
            AND task.worker_id=lease.worker_id AND task.fence=lease.task_fence
            AND task.lease_expires_at>clock_timestamp()
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
            AND lease.fence>snapshot.source_fence
            AND (attempt.state='expired' OR (attempt.state='completed' AND EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared
                    ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready
                    ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent
                    ON intent.id=ready.intent_id AND intent.attempt_id=attempt.id
            ))) AND (
                (original.root_id=attempt.source_task_id
                 AND original.task_type='setup_source_load'
                 AND original.domain_request_id=attempt.id)
                OR EXISTS (
                    SELECT 1 FROM public.stewardship_setup_prepared prepared
                    JOIN public.stewardship_setup_readiness_binding readiness
                        ON readiness.id=prepared.readiness_id
                    JOIN public.stewardship_setup_config_intent intent
                        ON intent.id=readiness.intent_id
                        AND intent.attempt_id=attempt.id
                    JOIN public.stewardship_task_run root ON root.id=original.root_id
                        AND root.task_type='setup_finalize'
                        AND root.domain_request_id=prepared.id
                        AND root.idempotency_key=prepared.id::text
                        AND root.initiated_by_id=attempt.owner_id
                    WHERE original.task_type='setup_finalize'
                        AND original.domain_request_id=prepared.id
                        AND original.initiated_by_id=attempt.owner_id
                )
            )
            AND snapshot.state='rejected' AND snapshot.generation IS NULL
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_current current
                WHERE current.snapshot_id=snapshot.id)
            AND EXISTS (SELECT 1 FROM public.stewardship_system_configuration
                WHERE NOT restore_review_required)
    ) AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted);
$$;

-- FUNCTION: stewardship_setup_draft_guard_v1()
CREATE FUNCTION public.stewardship_setup_draft_guard_v1() RETURNS trigger
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
            AND login.last_activity_at>stamp-interval '30 minutes'
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
        WHEN 'parish' THEN ARRAY['name','phone','timezone','website']
        WHEN 'branding' THEN ARRAY['bundle_id']
        WHEN 'access' THEN ARRAY['admin_addresses','ministry_addresses',
            'ministry_domains','staff_addresses','staff_domains']
        WHEN 'mail' THEN ARRAY['delegated_email','reply_to','sender']
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

-- FUNCTION: stewardship_setup_draft_section_mutable_v1()
CREATE FUNCTION public.stewardship_setup_draft_section_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id" OR NEW."step" IS DISTINCT FROM OLD."step" OR (OLD."scrubbed_at" IS NOT NULL AND NEW."scrubbed_at" IS DISTINCT FROM OLD."scrubbed_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_exchange_guard_v1()
CREATE FUNCTION public.stewardship_setup_exchange_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup exchange tombstones are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup exchange requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF OLD.scrubbed_at IS NOT NULL OR NEW.ciphertext IS NOT NULL
           OR NEW.actor_id IS NOT NULL
           OR NEW.replied_at IS DISTINCT FROM OLD.replied_at
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_attempt
                WHERE id=NEW.attempt_id AND state IN ('expired','completed')) THEN
            RAISE EXCEPTION 'Setup exchange scrub needs its terminal attempt'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=clock_timestamp();
        RETURN NEW;
    END IF;
    IF NEW.scrubbed_at IS NOT NULL OR octet_length(NEW.public_key)<>32
       OR NOT public.stewardship_setup_exchange_live_v1(
            NEW.attempt_id,NEW.credential_id,NEW.credential_version,NEW.fingerprint,
            NEW.task_id,NEW.task_fence,NEW.worker_id,NEW.source_fence) THEN
        RAISE EXCEPTION 'Setup exchange requires exact live source ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_worker'
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR NEW.ciphertext IS NOT NULL OR NEW.replied_at IS NOT NULL THEN
            RAISE EXCEPTION 'Only the source worker can request an exchange'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_credential_parishsoft'
       OR OLD.scrubbed_at IS NOT NULL OR OLD.replied_at IS NOT NULL
       OR NEW.actor_id IS NOT NULL OR NEW.ciphertext IS NULL
       OR octet_length(NEW.ciphertext) NOT BETWEEN 1 AND 2097152
       OR NEW.replied_at IS NULL THEN
        RAISE EXCEPTION 'Only the target installer may reply once'
            USING ERRCODE='23514';
    END IF;
    NEW.replied_at=clock_timestamp();
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_exchange_live_v1(uuid, uuid, bigint, text, uuid, bigint, uuid, bigint)
CREATE FUNCTION public.stewardship_setup_exchange_live_v1(attempt_id uuid, credential_id uuid, credential_version bigint, fingerprint text, task_id uuid, task_fence bigint, worker_id uuid, source_fence bigint) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_attempt attempt
        JOIN public.stewardship_setup_sealed_credential candidate
            ON candidate.attempt_id=attempt.id AND candidate.id=$2
            AND candidate.version=$3 AND candidate.fingerprint=$4
            AND candidate.target='parishsoft' AND candidate.scrubbed_at IS NULL
        JOIN public.stewardship_system_configuration runtime
            ON runtime.active_configuration_id=attempt.base_id
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND runtime.current_campaign_id IS NULL
        JOIN public.stewardship_portal_session login
            ON login.id=attempt.session_id AND login.principal_id=attempt.owner_id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_portal_user owner
            ON owner.id=attempt.owner_id AND NOT owner.disabled
        JOIN public.stewardship_task_run original
            ON original.id=attempt.source_task_id AND original.root_id=original.id
            AND original.created_at>clock_timestamp()-interval '2 hours'
        JOIN public.stewardship_task_run task
            ON task.id=$5 AND task.root_id=original.id
            AND task.task_type='setup_source_load'
            AND task.domain_request_id=attempt.id
            AND task.initiated_by_id=attempt.owner_id
            AND task.state='running' AND task.fence=$6 AND task.worker_id=$7
            AND task.lease_expires_at>clock_timestamp()
        JOIN public.stewardship_source_lease lease
            ON lease.owner_id=task.id AND lease.task_fence=task.fence
            AND lease.worker_id=task.worker_id AND lease.fence=$8
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
        WHERE attempt.id=$1 AND attempt.state='loading'
    );
$_$;

-- FUNCTION: stewardship_setup_final_receipt_read_v1(uuid)
CREATE FUNCTION public.stewardship_setup_final_receipt_read_v1(request_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN false; END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.stewardship_setup_credential_install binding
        JOIN public.stewardship_setup_prepared prepared ON prepared.readiness_id=binding.readiness_id
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='frozen' AND attempt.version=intent.attempt_version
        JOIN public.stewardship_portal_session login ON login.id=attempt.session_id
            AND login.principal_id=attempt.owner_id AND login.revoked_at IS NULL
            AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_task_run task ON task.domain_request_id=prepared.id
            AND task.task_type='setup_finalize' AND task.state='running'
            AND task.initiated_by_id=attempt.owner_id AND task.lease_expires_at>clock_timestamp()
        JOIN public.stewardship_task_run root ON root.id=task.root_id
            AND root.domain_request_id=prepared.id AND root.task_type=task.task_type
            AND root.idempotency_key=prepared.id::text
            AND root.initiated_by_id=attempt.owner_id
        JOIN public.stewardship_source_lease lease ON lease.owner_id=task.id
            AND lease.task_fence=task.fence AND lease.worker_id=task.worker_id
            AND lease.phase='full' AND lease.expires_at>clock_timestamp()
        WHERE binding.request_id=$1 AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_config_abort WHERE intent_id=intent.id)
    );
END $_$;

-- FUNCTION: stewardship_setup_initial_hold_v1()
CREATE FUNCTION public.stewardship_setup_initial_hold_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.target NOT IN ('parishsoft','google_workspace','slack')
        OR NEW.state<>'cleanup_pending' OR NEW.cleanup_reason='applied' THEN RETURN NEW; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install WHERE request_id=NEW.id)
        THEN RETURN NEW; END IF;
    IF (OLD.state='awaiting_ack' OR NEW.cleanup_reason='cancelled')
        AND public.stewardship_setup_install_live_v1(NEW.id) THEN
        RAISE EXCEPTION 'Initial rollback requires original setup cancellation or expiry'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_install_binding_v1()
CREATE FUNCTION public.stewardship_setup_install_binding_v1() RETURNS trigger
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
        OR request.reauthenticated_at IS DISTINCT FROM login.authenticated_at
        OR request.expires_at>login.expires_at OR request.expires_at<=clock_timestamp()
        OR request.required_consumers IS DISTINCT FROM stewardship_credential_consumers_v1(NEW.target)
        THEN
        RAISE EXCEPTION 'Initial credential request differs from its original setup'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_install_completed_v1(uuid)
CREATE FUNCTION public.stewardship_setup_install_completed_v1(request_id uuid) RETURNS timestamp with time zone
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
BEGIN
    -- Ordinary requests never require privileges on private setup proof tables.
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install binding
        WHERE binding.request_id=$1) THEN RETURN NULL; END IF;
    RETURN (SELECT attempt.updated_at
        FROM public.stewardship_setup_credential_install binding
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=binding.readiness_id
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_attempt attempt ON attempt.id=intent.attempt_id
            AND attempt.state='completed'
        JOIN public.stewardship_secret_request request ON request.id=binding.request_id
            AND attempt.updated_at<request.expires_at
        WHERE binding.request_id=$1);
END $_$;

-- FUNCTION: stewardship_setup_install_intake_v1()
CREATE FUNCTION public.stewardship_setup_install_intake_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- A separate statement avoids planning private-column queries for web
    -- intake. SQL boolean short-circuiting is not a privilege boundary.
    IF current_user<>'pk_stewardship_credential_'||NEW.target OR NEW.target NOT IN
        ('parishsoft','google_workspace','slack') THEN RETURN NULL; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_credential_install binding
        JOIN public.stewardship_setup_sealed_credential credential ON credential.id=binding.credential_id
        JOIN public.stewardship_sealed_credential_staging staging ON staging.request_id=NEW.id
            AND staging.ciphertext=credential.ciphertext AND staging.fingerprint=binding.fingerprint
        JOIN public.stewardship_provider_context context ON context.request_id=NEW.id
            AND context.settings=credential.settings AND context.target=NEW.target
        WHERE binding.request_id=NEW.id AND binding.target=NEW.target) THEN
        RAISE EXCEPTION 'Isolated intake requires its atomic initial credential binding'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_install_live_v1(uuid)
CREATE FUNCTION public.stewardship_setup_install_live_v1(request_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE readiness uuid;
BEGIN
    SELECT binding.readiness_id INTO readiness
        FROM public.stewardship_setup_credential_install binding WHERE binding.request_id=$1;
    IF readiness IS NULL THEN RETURN false; END IF;
    RETURN public.stewardship_setup_install_ready_live_v1(readiness);
END $_$;

-- FUNCTION: stewardship_setup_install_progress_v1()
CREATE FUNCTION public.stewardship_setup_install_progress_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE completed timestamptz;
BEGIN
    IF NEW.target NOT IN ('parishsoft','google_workspace','slack') THEN RETURN NEW; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_setup_credential_install
        WHERE request_id=NEW.id) THEN RETURN NEW; END IF;
    completed=public.stewardship_setup_install_completed_v1(NEW.id);
    IF NEW.state IN ('testing','installing','awaiting_ack')
        AND NOT public.stewardship_setup_install_live_v1(NEW.id) THEN
        RAISE EXCEPTION 'Initial credential setup ownership has ended' USING ERRCODE='23514';
    END IF;
    IF NEW.state='cleanup_pending' THEN
        IF NEW.cleanup_reason='applied' AND completed IS NULL THEN
            RAISE EXCEPTION 'Initial rollback must remain until setup commits' USING ERRCODE='23514';
        ELSIF NEW.cleanup_reason<>'applied' AND completed IS NOT NULL THEN
            RAISE EXCEPTION 'Configured credentials cannot roll back' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_install_ready_live_v1(uuid)
CREATE FUNCTION public.stewardship_setup_install_ready_live_v1(ready_id uuid) RETURNS boolean
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
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        JOIN public.stewardship_portal_user owner ON owner.id=attempt.owner_id
            AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.configuration_id=attempt.base_id
            AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE ready.id=$1
    );
$_$;

-- FUNCTION: stewardship_setup_install_selected_v1(uuid, uuid, bigint, text)
CREATE FUNCTION public.stewardship_setup_install_selected_v1(ready_id uuid, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_readiness_binding ready
        JOIN public.stewardship_setup_config_intent intent ON intent.id=ready.intent_id
        JOIN public.stewardship_setup_sealed_credential credential
            ON credential.id=$2 AND credential.attempt_id=intent.attempt_id
            AND credential.version=$3 AND credential.fingerprint=$4
            AND credential.scrubbed_at IS NULL
        WHERE ready.id=$1 AND CASE credential.target
            WHEN 'parishsoft' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_source_result result
                JOIN public.stewardship_setup_source_exchange exchange ON exchange.id=result.exchange_id
                WHERE result.id=ready.source_result_id AND exchange.credential_id=$2
                    AND exchange.credential_version=$3 AND exchange.fingerprint=$4)
            WHEN 'google_workspace' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
                WHERE delivery.id=ready.mail_delivery_id AND delivery.credential_id=$2
                    AND delivery.credential_version=$3 AND delivery.fingerprint=$4
                    AND delivery.state='accepted')
            WHEN 'slack' THEN EXISTS (
                SELECT 1 FROM public.stewardship_setup_slack_delivery delivery
                WHERE delivery.id=ready.slack_delivery_id AND delivery.credential_id=$2
                    AND delivery.credential_version=$3 AND delivery.fingerprint=$4
                    AND delivery.state='accepted')
            ELSE false END
    );
$_$;

-- FUNCTION: stewardship_setup_mail_audit_v1()
CREATE FUNCTION public.stewardship_setup_mail_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid(); event text;
BEGIN
    event='setup_mail_' || CASE WHEN NEW.scrubbed_at IS NOT NULL
        AND (TG_OP='INSERT' OR OLD.scrubbed_at IS NULL) THEN 'scrubbed'
        ELSE NEW.state END;
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,event,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome', CASE
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN event='setup_mail_scrubbed' THEN 'changed'
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state IN ('not_sent','delivery_unknown') THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_mail_delivery_mutable_v1()
CREATE FUNCTION public.stewardship_setup_mail_delivery_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id" OR NEW."attempt_version" IS DISTINCT FROM OLD."attempt_version" OR NEW."request_key" IS DISTINCT FROM OLD."request_key" OR NEW."candidate_digest" IS DISTINCT FROM OLD."candidate_digest" OR NEW."credential_id" IS DISTINCT FROM OLD."credential_id" OR NEW."credential_version" IS DISTINCT FROM OLD."credential_version" OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint" OR NEW."task_id" IS DISTINCT FROM OLD."task_id" OR (OLD."submitted_at" IS NOT NULL AND NEW."submitted_at" IS DISTINCT FROM OLD."submitted_at") OR (OLD."deadline_at" IS NOT NULL AND NEW."deadline_at" IS DISTINCT FROM OLD."deadline_at") OR (OLD."finished_at" IS NOT NULL AND NEW."finished_at" IS DISTINCT FROM OLD."finished_at") OR (OLD."scrubbed_at" IS NOT NULL AND NEW."scrubbed_at" IS DISTINCT FROM OLD."scrubbed_at") OR (OLD."run_id" IS NOT NULL AND NEW."run_id" IS DISTINCT FROM OLD."run_id") OR (OLD."task_fence" IS NOT NULL AND NEW."task_fence" IS DISTINCT FROM OLD."task_fence") OR (OLD."worker_id" IS NOT NULL AND NEW."worker_id" IS DISTINCT FROM OLD."worker_id") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_mail_exchange_guard_v1()
CREATE FUNCTION public.stewardship_setup_mail_exchange_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup mail exchange receipts are retained'
            USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup mail exchange requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS NOT NULL THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler','pk_stewardship_worker')
           OR OLD.scrubbed_at IS NOT NULL OR NEW.ciphertext IS NOT NULL
           OR NEW.actor_id IS NOT NULL
           OR NEW.replied_at IS DISTINCT FROM OLD.replied_at
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
                JOIN public.stewardship_setup_attempt attempt
                    ON attempt.id=delivery.attempt_id
                WHERE delivery.id=NEW.delivery_id
                    AND attempt.state IN ('expired','completed')) THEN
            RAISE EXCEPTION 'Mail relay scrub requires its terminal original setup'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=clock_timestamp();
        RETURN NEW;
    END IF;
    IF NEW.scrubbed_at IS NOT NULL OR octet_length(NEW.public_key)<>32
       OR NOT public.stewardship_setup_mail_exchange_live_v1(
            NEW.delivery_id,NEW.run_id,NEW.task_fence,NEW.worker_id) THEN
        RAISE EXCEPTION 'Mail relay requires exact live original ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_mail_dispatch'
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR NEW.ciphertext IS NOT NULL OR NEW.replied_at IS NOT NULL THEN
            RAISE EXCEPTION 'Only the mail worker may request a Workspace relay'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_credential_google_workspace'
       OR OLD.scrubbed_at IS NOT NULL OR OLD.replied_at IS NOT NULL
       OR NEW.actor_id IS NOT NULL OR NEW.ciphertext IS NULL
       OR octet_length(NEW.ciphertext) NOT BETWEEN 1 AND 2097152
       OR NEW.replied_at IS NULL THEN
        RAISE EXCEPTION 'Only the Workspace installer may reply once'
            USING ERRCODE='23514';
    END IF;
    NEW.replied_at=clock_timestamp();
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_mail_exchange_live_v1(uuid, uuid, bigint, uuid)
CREATE FUNCTION public.stewardship_setup_mail_exchange_live_v1(delivery_id uuid, run_id uuid, task_fence bigint, worker_id uuid) RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_setup_mail_delivery delivery
        JOIN public.stewardship_task_run task ON task.id=$2
            AND task.root_id=delivery.task_id AND task.task_type='setup_mail_test'
            AND task.domain_request_id=delivery.id
            AND task.state='running' AND task.fence=$3 AND task.worker_id=$4
            AND task.lease_expires_at>clock_timestamp()
        WHERE delivery.id=$1 AND delivery.state='queued'
            AND public.stewardship_setup_mail_live_v1(
                delivery.attempt_id,delivery.attempt_version,delivery.credential_id,
                delivery.credential_version,delivery.fingerprint)
    );
$_$;

-- FUNCTION: stewardship_setup_mail_exchange_mutable_v1()
CREATE FUNCTION public.stewardship_setup_mail_exchange_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."delivery_id" IS DISTINCT FROM OLD."delivery_id" OR NEW."run_id" IS DISTINCT FROM OLD."run_id" OR NEW."task_fence" IS DISTINCT FROM OLD."task_fence" OR NEW."worker_id" IS DISTINCT FROM OLD."worker_id" OR NEW."public_key" IS DISTINCT FROM OLD."public_key" OR (OLD."replied_at" IS NOT NULL AND NEW."replied_at" IS DISTINCT FROM OLD."replied_at") OR (OLD."scrubbed_at" IS NOT NULL AND NEW."scrubbed_at" IS DISTINCT FROM OLD."scrubbed_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_mail_guard_v1()
CREATE FUNCTION public.stewardship_setup_mail_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE attempt public.stewardship_setup_attempt%ROWTYPE;
        owned boolean; live boolean; keys text[]; stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup delivery outcomes are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup delivery requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO attempt FROM public.stewardship_setup_attempt WHERE id=NEW.attempt_id;
    IF TG_OP='UPDATE' AND NEW.scrubbed_at IS DISTINCT FROM OLD.scrubbed_at THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler','pk_stewardship_worker')
           OR OLD.scrubbed_at IS NOT NULL OR NEW.scrubbed_at IS NULL
           OR NEW.mail<>'{}'::jsonb OR NEW.actor_id IS NOT NULL
           OR attempt.state NOT IN ('expired','completed')
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
           OR NEW.deadline_at IS DISTINCT FROM OLD.deadline_at
           OR NEW.run_id IS DISTINCT FROM OLD.run_id
           OR NEW.task_fence IS DISTINCT FROM OLD.task_fence
           OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
           OR (OLD.state='queued' AND NEW.state<>'cancelled')
           OR (OLD.state<>'queued' AND NEW.state<>OLD.state) THEN
            RAISE EXCEPTION 'Setup delivery scrub requires terminal setup'
                USING ERRCODE='23514';
        END IF;
        IF OLD.state='queued' THEN NEW.finished_at=stamp;
        ELSIF NEW.finished_at IS DISTINCT FROM OLD.finished_at THEN
            RAISE EXCEPTION 'Scrubbing cannot rewrite a delivery result'
                USING ERRCODE='23514';
        END IF;
        NEW.scrubbed_at=stamp;
        RETURN NEW;
    END IF;
    IF TG_OP='UPDATE' AND NEW.mail IS DISTINCT FROM OLD.mail THEN
        RAISE EXCEPTION 'Setup delivery content is immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' OR (OLD.state='queued' AND NEW.state='submitting') THEN
        live=public.stewardship_setup_mail_live_v1(NEW.attempt_id,NEW.attempt_version,
            NEW.credential_id,NEW.credential_version,NEW.fingerprint);
        IF NOT live OR NEW.scrubbed_at IS NOT NULL THEN
            RAISE EXCEPTION 'Setup delivery requires exact current draft and credential'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web'
           OR NEW.actor_id IS DISTINCT FROM attempt.owner_id
           OR NEW.state<>'queued' OR NEW.version<>1
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.task_id AND task.root_id=task.id
                    AND task.task_type='setup_mail_test' AND task.state='queued'
                    AND task.domain_request_id=NEW.id
                    AND task.initiated_by_id=attempt.owner_id) THEN
            RAISE EXCEPTION 'Setup mail requires its explicit original Admin request'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.mail) IS DISTINCT FROM 'object'
           OR octet_length(NEW.mail::text)>1048576 THEN
            RAISE EXCEPTION 'Invalid setup mail payload' USING ERRCODE='23514';
        END IF;
        SELECT array_agg(key ORDER BY key) INTO keys
            FROM jsonb_object_keys(NEW.mail) key;
        IF keys IS DISTINCT FROM ARRAY['delivery_id','html','recipient','reply_to',
                                      'sender','subject','text']
           OR EXISTS (SELECT 1 FROM jsonb_each(NEW.mail) pair
                WHERE jsonb_typeof(pair.value)<>'string')
           OR NEW.mail->>'delivery_id' IS DISTINCT FROM NEW.id::text
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_setup_sealed_credential key
                WHERE key.id=NEW.credential_id
                    AND key.settings->>'recipient'=NEW.mail->>'recipient'
                    AND key.settings->>'sender'=NEW.mail->>'sender'
                    AND key.settings->>'reply_to'=NEW.mail->>'reply_to') THEN
            RAISE EXCEPTION 'Setup mail may address only its exact Testing recipient'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state NOT IN ('queued','submitting') THEN
        RAISE EXCEPTION 'Terminal setup delivery cannot be replayed or rewritten'
            USING ERRCODE='23514';
    END IF;
    IF OLD.state='queued' AND NEW.state='cancelled' THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler',
                               'pk_stewardship_mail_dispatch')
           OR NEW.actor_id IS NOT NULL
           OR (public.stewardship_setup_mail_live_v1(
                NEW.attempt_id,NEW.attempt_version,
                NEW.credential_id,NEW.credential_version,NEW.fingerprint)
               AND NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                    WHERE task.id=NEW.task_id
                        AND task.state IN ('failed','cancelled'))) THEN
            RAISE EXCEPTION 'Only stale unsent setup work can be cancelled'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    SELECT EXISTS (SELECT 1 FROM public.stewardship_task_run task
        WHERE task.id=NEW.run_id AND task.root_id=NEW.task_id
            AND task.task_type='setup_mail_test' AND task.domain_request_id=NEW.id
            AND task.initiated_by_id=attempt.owner_id AND task.state='running'
            AND task.fence=NEW.task_fence AND task.worker_id=NEW.worker_id
            AND task.lease_expires_at>stamp) INTO owned;
    IF OLD.state='queued' AND NEW.state='submitting' THEN
        IF current_user<>'pk_stewardship_mail_dispatch' OR NOT owned
           OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.run_id
                    AND task.lease_expires_at>stamp+interval '30 seconds') THEN
            RAISE EXCEPTION 'Only the live mail worker may begin submission'
                USING ERRCODE='23514';
        END IF;
        NEW.submitted_at=stamp;
        NEW.deadline_at=stamp+interval '30 seconds';
        RETURN NEW;
    END IF;
    IF OLD.state='submitting'
       AND NEW.state IN ('accepted','not_sent','delivery_unknown') THEN
        IF current_user='pk_stewardship_mail_dispatch' AND owned
           AND NEW.actor_id=NEW.worker_id THEN
            IF stamp>=OLD.deadline_at AND NEW.state<>'delivery_unknown' THEN
                RAISE EXCEPTION 'A late setup delivery result remains unknown'
                    USING ERRCODE='23514';
            END IF;
        ELSIF current_user IN
              ('pk_stewardship_mail_dispatch','pk_stewardship_scheduler')
              AND NOT owned AND stamp>=OLD.deadline_at
              AND NEW.state='delivery_unknown' AND NEW.actor_id IS NULL THEN
            NULL;
        ELSE
            RAISE EXCEPTION 'Setup outcome needs live ownership or expired uncertainty'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid setup delivery transition' USING ERRCODE='23514';
END $$;

-- FUNCTION: stewardship_setup_mail_live_v1(uuid, bigint, uuid, bigint, text)
CREATE FUNCTION public.stewardship_setup_mail_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
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
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
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

-- FUNCTION: stewardship_setup_prepared_guard_v1()
CREATE FUNCTION public.stewardship_setup_prepared_guard_v1() RETURNS trigger
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

-- FUNCTION: stewardship_setup_prepared_immutable_v1()
CREATE FUNCTION public.stewardship_setup_prepared_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_readiness_binding_immutable_v1()
CREATE FUNCTION public.stewardship_setup_readiness_binding_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_readiness_guard_v1()
CREATE FUNCTION public.stewardship_setup_readiness_guard_v1() RETURNS trigger
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
                AND login.last_activity_at>clock_timestamp()-interval '30 minutes')
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

-- FUNCTION: stewardship_setup_request_binding_v1()
CREATE FUNCTION public.stewardship_setup_request_binding_v1() RETURNS trigger
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

-- FUNCTION: stewardship_setup_scrub_drafts_v1()
CREATE FUNCTION public.stewardship_setup_scrub_drafts_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE stewardship_setup_draft_section SET values='{}'::jsonb,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_scrub_exchanges_v1()
CREATE FUNCTION public.stewardship_setup_scrub_exchanges_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_source_exchange SET ciphertext=NULL,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_scrub_mail_exchanges_v1()
CREATE FUNCTION public.stewardship_setup_scrub_mail_exchanges_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_mail_exchange SET ciphertext=NULL,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE delivery_id IN (SELECT id FROM public.stewardship_setup_mail_delivery
                WHERE attempt_id=NEW.id) AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_scrub_mail_v1()
CREATE FUNCTION public.stewardship_setup_scrub_mail_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_mail_delivery SET mail='{}'::jsonb,
            state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,
            scrubbed_at=clock_timestamp(),actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_scrub_secrets_v1()
CREATE FUNCTION public.stewardship_setup_scrub_secrets_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.state IN ('expired','completed') AND NEW.state<>OLD.state THEN
        UPDATE public.stewardship_setup_sealed_credential SET ciphertext=NULL,
            settings='{}'::jsonb,
            scrubbed_at=clock_timestamp(), actor_id=NULL,
            correlation_id=NEW.correlation_id,version=version+1
            WHERE attempt_id=NEW.id AND scrubbed_at IS NULL;
    END IF;
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_sealed_credential_mutable_v1()
CREATE FUNCTION public.stewardship_setup_sealed_credential_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id" OR NEW."target" IS DISTINCT FROM OLD."target" OR (OLD."scrubbed_at" IS NOT NULL AND NEW."scrubbed_at" IS DISTINCT FROM OLD."scrubbed_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_secret_audit_v1()
CREATE FUNCTION public.stewardship_setup_secret_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        CASE WHEN NEW.scrubbed_at IS NULL THEN 'setup_credential_staged'
            ELSE 'setup_credential_scrubbed' END,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN NEW.actor_id IS NULL THEN 'system' ELSE 'portal_user' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',
            CASE WHEN NEW.scrubbed_at IS NULL THEN 'changed' ELSE 'cancelled' END));
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_secret_guard_v1()
CREATE FUNCTION public.stewardship_setup_secret_guard_v1() RETURNS trigger
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
            AND login.last_activity_at>stamp-interval '30 minutes'
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

-- FUNCTION: stewardship_setup_slack_audit_v1()
CREATE FUNCTION public.stewardship_setup_slack_audit_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        'setup_slack_'||NEW.state,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome', CASE
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN NEW.state IN ('not_sent','delivery_unknown') THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;

-- FUNCTION: stewardship_setup_slack_delivery_mutable_v1()
CREATE FUNCTION public.stewardship_setup_slack_delivery_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id" OR NEW."attempt_version" IS DISTINCT FROM OLD."attempt_version" OR NEW."request_key" IS DISTINCT FROM OLD."request_key" OR NEW."candidate_digest" IS DISTINCT FROM OLD."candidate_digest" OR NEW."credential_id" IS DISTINCT FROM OLD."credential_id" OR NEW."credential_version" IS DISTINCT FROM OLD."credential_version" OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint" OR (OLD."worker_id" IS NOT NULL AND NEW."worker_id" IS DISTINCT FROM OLD."worker_id") OR (OLD."submitted_at" IS NOT NULL AND NEW."submitted_at" IS DISTINCT FROM OLD."submitted_at") OR (OLD."deadline_at" IS NOT NULL AND NEW."deadline_at" IS DISTINCT FROM OLD."deadline_at") OR (OLD."finished_at" IS NOT NULL AND NEW."finished_at" IS DISTINCT FROM OLD."finished_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_slack_guard_v1()
CREATE FUNCTION public.stewardship_setup_slack_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Setup notification history is retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup notification requires ordered ownership'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' OR (OLD.state='queued' AND NEW.state='submitting') THEN
        IF NOT public.stewardship_setup_slack_live_v1(
            NEW.attempt_id,NEW.attempt_version,NEW.credential_id,
            NEW.credential_version,NEW.fingerprint) THEN
            RAISE EXCEPTION 'Setup notification needs its exact live draft'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web' OR NEW.state<>'queued'
           OR NEW.version<>1 OR NOT EXISTS (
                SELECT 1 FROM public.stewardship_setup_attempt attempt
                WHERE attempt.id=NEW.attempt_id AND attempt.owner_id=NEW.actor_id) THEN
            RAISE EXCEPTION 'Setup notification requires original Admin intent'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state='queued' AND NEW.state='cancelled' THEN
        IF current_user NOT IN ('pk_stewardship_web','pk_stewardship_scheduler',
                                'pk_stewardship_credential_slack')
           OR NEW.actor_id IS NOT NULL
           OR public.stewardship_setup_slack_live_v1(
                NEW.attempt_id,NEW.attempt_version,NEW.credential_id,
                NEW.credential_version,NEW.fingerprint) THEN
            RAISE EXCEPTION 'Only stale unsent notifications can be cancelled'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state='queued' AND NEW.state='submitting' THEN
        IF current_user<>'pk_stewardship_credential_slack'
           OR NEW.worker_id IS NULL OR NEW.actor_id IS DISTINCT FROM NEW.worker_id THEN
            RAISE EXCEPTION 'Only the isolated Slack owner may submit'
                USING ERRCODE='23514';
        END IF;
        NEW.submitted_at=stamp;
        NEW.deadline_at=stamp+interval '30 seconds';
        RETURN NEW;
    END IF;
    IF OLD.state='submitting'
       AND NEW.state IN ('accepted','not_sent','delivery_unknown') THEN
        IF current_user='pk_stewardship_credential_slack'
           AND NEW.actor_id=OLD.worker_id THEN
            IF stamp>=OLD.deadline_at AND NEW.state<>'delivery_unknown' THEN
                RAISE EXCEPTION 'Late notification results remain unknown'
                    USING ERRCODE='23514';
            END IF;
        ELSIF current_user IN (
            'pk_stewardship_scheduler','pk_stewardship_credential_slack')
            AND NEW.actor_id IS NULL AND stamp>=OLD.deadline_at
            AND NEW.state='delivery_unknown' THEN
            NULL;
        ELSE
            RAISE EXCEPTION 'Notification outcome needs original ownership'
                USING ERRCODE='23514';
        END IF;
        NEW.finished_at=stamp;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Notification cannot be retried or rewritten' USING ERRCODE='23514';
END $$;

-- FUNCTION: stewardship_setup_slack_live_v1(uuid, bigint, uuid, bigint, text)
CREATE FUNCTION public.stewardship_setup_slack_live_v1(attempt_id uuid, attempt_version bigint, credential_id uuid, credential_version bigint, fingerprint text) RETURNS boolean
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
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
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

-- FUNCTION: stewardship_setup_source_exchange_mutable_v1()
CREATE FUNCTION public.stewardship_setup_source_exchange_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."attempt_id" IS DISTINCT FROM OLD."attempt_id" OR NEW."credential_id" IS DISTINCT FROM OLD."credential_id" OR NEW."credential_version" IS DISTINCT FROM OLD."credential_version" OR NEW."fingerprint" IS DISTINCT FROM OLD."fingerprint" OR NEW."task_id" IS DISTINCT FROM OLD."task_id" OR NEW."task_fence" IS DISTINCT FROM OLD."task_fence" OR NEW."worker_id" IS DISTINCT FROM OLD."worker_id" OR NEW."source_fence" IS DISTINCT FROM OLD."source_fence" OR NEW."public_key" IS DISTINCT FROM OLD."public_key" OR (OLD."replied_at" IS NOT NULL AND NEW."replied_at" IS DISTINCT FROM OLD."replied_at") OR (OLD."scrubbed_at" IS NOT NULL AND NEW."scrubbed_at" IS DISTINCT FROM OLD."scrubbed_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_setup_source_result_guard_v1()
CREATE FUNCTION public.stewardship_setup_source_result_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE exchange public.stewardship_setup_source_exchange%ROWTYPE;
BEGIN
    IF current_user<>'pk_stewardship_worker'
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup result requires its ordered source worker'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO exchange FROM public.stewardship_setup_source_exchange
        WHERE id=NEW.exchange_id;
    IF NOT FOUND OR exchange.replied_at IS NULL OR exchange.scrubbed_at IS NOT NULL
       OR NEW.actor_id IS DISTINCT FROM exchange.worker_id
       OR NOT public.stewardship_setup_exchange_live_v1(
            exchange.attempt_id,exchange.credential_id,exchange.credential_version,
            exchange.fingerprint,exchange.task_id,exchange.task_fence,
            exchange.worker_id,exchange.source_fence)
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_snapshot snapshot
            JOIN public.stewardship_setup_sealed_credential candidate
                ON candidate.id=exchange.credential_id
                AND snapshot.organization_id=
                    (candidate.settings->>'organization_id')::bigint
            WHERE snapshot.id=NEW.snapshot_id AND snapshot.state='ready'
                AND snapshot.kind='full' AND snapshot.base_id IS NULL
                AND snapshot.task_id=exchange.task_id
                AND snapshot.source_fence=exchange.source_fence) THEN
        RAISE EXCEPTION 'Setup result requires its exact validated source snapshot'
            USING ERRCODE='23514';
    END IF;
    NEW.created_at=clock_timestamp();
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_source_result_immutable_v1()
CREATE FUNCTION public.stewardship_setup_source_result_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_setup_testing_recipient_v1()
CREATE FUNCTION public.stewardship_setup_testing_recipient_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.testing_recipient IS DISTINCT FROM OLD.testing_recipient AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_setup_prepared prepared
        JOIN public.stewardship_setup_readiness_binding ready ON ready.id=prepared.readiness_id
        WHERE prepared.id=public.stewardship_setup_completion_context_v1()
            AND NEW.active_configuration_id=prepared.configuration_id
            AND NEW.testing_recipient=ready.testing_recipient
    ) THEN
        RAISE EXCEPTION 'Testing recipient requires the exact initial activation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_setup_timezone_guard_v1()
CREATE FUNCTION public.stewardship_setup_timezone_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Scrubbing a terminal attempt still erases the whole public profile.
    IF NEW.step='parish' AND NEW.scrubbed_at IS NULL
       AND NEW.values->>'timezone' IS DISTINCT FROM OLD.values->>'timezone'
       AND EXISTS (SELECT 1 FROM public.stewardship_setup_attempt
                   WHERE id=NEW.attempt_id AND source_task_id IS NOT NULL) THEN
        RAISE EXCEPTION 'The original source load fixes the setup timezone'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_source_canonical(jsonb, integer)
CREATE FUNCTION public.stewardship_source_canonical(value jsonb, depth integer DEFAULT 0) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE result text;
BEGIN
    IF depth > 16 THEN
        RAISE EXCEPTION 'Source JSON nesting exceeds its bound' USING ERRCODE='23514';
    END IF;
    CASE jsonb_typeof(value)
    WHEN 'object' THEN
        SELECT '{' || COALESCE(string_agg(to_jsonb(key)::text || ':' ||
            public.stewardship_source_canonical(item, depth+1), ','
            ORDER BY key COLLATE "C"), '')
            || '}' INTO result FROM jsonb_each(value) AS pairs(key,item);
    WHEN 'array' THEN
        SELECT '[' || COALESCE(string_agg(public.stewardship_source_canonical(item,depth+1),
            ',' ORDER BY ordinal), '') || ']' INTO result
            FROM jsonb_array_elements(value) WITH ORDINALITY AS items(item,ordinal);
    WHEN 'number' THEN
        IF value::text !~ '^-?(0|[1-9][0-9]*)$'
           OR value::numeric < -9223372036854775808
           OR value::numeric > 9223372036854775807 THEN
            RAISE EXCEPTION 'Source JSON requires bounded integers'
                USING ERRCODE='23514';
        END IF;
        result := value::text;
    ELSE result := value::text;
    END CASE;
    RETURN result;
END;
$_$;

-- FUNCTION: stewardship_source_chair_effects_v1()
CREATE FUNCTION public.stewardship_source_chair_effects_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE configuration uuid;
BEGIN
    SELECT active_configuration_id INTO configuration
        FROM stewardship_system_configuration;
    IF EXISTS (SELECT 1 FROM stewardship_ministry_assignment
               WHERE configuration_id=configuration AND source='chair-seed')
       OR EXISTS (SELECT 1 FROM stewardship_chair_review
                  WHERE closed_by_id IS NULL) THEN
        PERFORM stewardship_require_chair_receipt_v1(configuration,NEW.snapshot_id);
    END IF;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_source_compaction_immutable_v1()
CREATE FUNCTION public.stewardship_source_compaction_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_source_corpus_evidence(uuid)
CREATE FUNCTION public.stewardship_source_corpus_evidence(snapshot_id uuid) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$
DECLARE kind text;
        items jsonb;
        item_count bigint;
        manifest jsonb := '{}'::jsonb;
        counts jsonb := '{}'::jsonb;
        relation text[];
        invalid boolean;
BEGIN
    FOREACH kind IN ARRAY ARRAY['family','member','contact','address','ministry',
        'roster','fund','pledge','contribution'] LOOP
        EXECUTE format('SELECT COALESCE(jsonb_object_agg(m.source_key,p.digest),'
            '''{}''::jsonb),count(*) FROM %I m JOIN %I p ON p.id=m.payload_id '
            'WHERE m.snapshot_id=$1', 'stewardship_snapshot_'||kind,
            'stewardship_source_'||kind) INTO items,item_count USING snapshot_id;
        manifest := manifest || jsonb_build_object(kind,items);
        counts := counts || jsonb_build_object(kind,item_count);
    END LOOP;
    FOREACH relation SLICE 1 IN ARRAY ARRAY[
        ['member','family_key','family'], ['roster','member_key','member'],
        ['roster','ministry_key','ministry'], ['pledge','family_key','family'],
        ['pledge','fund_key','fund'], ['contribution','family_key','family'],
        ['contribution','fund_key','fund']
    ] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I m '
            'JOIN %I p ON p.id=m.payload_id '
            'WHERE m.snapshot_id=$1 AND NOT EXISTS(SELECT 1 FROM %I parent '
            'WHERE parent.snapshot_id=$1 AND parent.source_key=p.%I))',
            'stewardship_snapshot_'||relation[1], 'stewardship_source_'||relation[1],
            'stewardship_snapshot_'||relation[3], relation[2])
            INTO invalid USING snapshot_id;
        IF invalid THEN
            RAISE EXCEPTION 'Source corpus has unresolved relationships'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    FOREACH kind IN ARRAY ARRAY['contact','address'] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I m '
            'JOIN %I p ON p.id=m.payload_id '
            'LEFT JOIN stewardship_snapshot_family f ON f.snapshot_id=$1 '
            'AND f.source_key=p.owner_key AND p.owner_kind=''family'' '
            'LEFT JOIN stewardship_snapshot_member b ON b.snapshot_id=$1 '
            'AND b.source_key=p.owner_key AND p.owner_kind=''member'' '
            'WHERE m.snapshot_id=$1 AND f.id IS NULL AND b.id IS NULL)',
            'stewardship_snapshot_'||kind,'stewardship_source_'||kind)
            INTO invalid USING snapshot_id;
        IF invalid THEN
            RAISE EXCEPTION 'Source corpus has an unresolved contact owner'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN jsonb_build_object('counts', counts, 'digest', encode(sha256(convert_to(
        stewardship_source_canonical(manifest),'UTF8')),'hex'));
END;
$_$;

-- FUNCTION: stewardship_source_current_guard()
CREATE FUNCTION public.stewardship_source_current_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.snapshot_id IS NOT NULL THEN
            RAISE EXCEPTION 'Source current pointer must start empty'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Source current pointer cannot be deleted'
            USING ERRCODE='23514';
    END IF;
    IF NEW.generation <> OLD.generation+1
       OR (OLD.organization_id IS NOT NULL AND
           NEW.organization_id IS DISTINCT FROM OLD.organization_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_source_snapshot s
            WHERE s.id=NEW.snapshot_id AND s.state='promoted' AND s.compacted_at IS NULL
              AND s.generation=NEW.generation AND s.organization_id=NEW.organization_id
              AND s.base_id IS NOT DISTINCT FROM OLD.snapshot_id) THEN
        RAISE EXCEPTION 'Source pointer requires the next coherent promotion'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_source_current_mutable_v1()
CREATE FUNCTION public.stewardship_source_current_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."singleton" IS DISTINCT FROM OLD."singleton" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_source_current_window_v1(uuid)
CREATE FUNCTION public.stewardship_source_current_window_v1(campaign_id uuid) RETURNS text
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE campaign stewardship_campaign%ROWTYPE;
        values jsonb;
        periods jsonb := '[]'::jsonb;
BEGIN
    IF campaign_id IS NOT NULL THEN
        SELECT * INTO campaign FROM stewardship_campaign WHERE id=campaign_id;
        IF NOT FOUND OR campaign.state NOT IN
            ('draft','scheduled','active','closed','archived') THEN
            RAISE EXCEPTION 'Source campaign is unavailable' USING ERRCODE='23514';
        END IF;
        SELECT c.values INTO values FROM stewardship_campaign_configuration c
            WHERE c.id=campaign.active_configuration_id;
        IF campaign.state <> 'archived' AND values->'modules' ? 'financial'
           AND values->'financial' <> 'null'::jsonb THEN
            periods := jsonb_build_array(
                jsonb_build_object('start',values#>'{financial,start}',
                    'end',values#>'{financial,end}',
                    'funds',values#>'{financial,fund_duids}'),
                jsonb_build_object('start',values#>'{financial,comparison_start}',
                    'end',values#>'{financial,comparison_end}',
                    'funds',values#>'{financial,comparison_fund_duids}'));
        END IF;
    END IF;
    RETURN stewardship_source_canonical(
        jsonb_build_object('campaign_id',campaign_id,'periods',periods));
END;
$$;

-- FUNCTION: stewardship_source_lease_mutable_v1()
CREATE FUNCTION public.stewardship_source_lease_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."singleton" IS DISTINCT FROM OLD."singleton" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_source_lease_owner_guard()
CREATE FUNCTION public.stewardship_source_lease_owner_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE instant timestamptz := clock_timestamp();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Source ownership cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.owner_id IS NOT NULL OR NEW.fence <> 0 OR NEW.task_fence <> 0
           OR NEW.external_deadline IS NOT NULL OR NEW.acquired_at IS NOT NULL
           OR NEW.heartbeat_at IS NOT NULL THEN
            RAISE EXCEPTION 'Source ownership must start idle' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.fence = OLD.fence + 1 AND NEW.owner_id IS NOT NULL THEN
        IF OLD.expires_at > instant OR OLD.external_deadline > instant
           OR NEW.acquired_at < COALESCE(OLD.acquired_at, NEW.acquired_at)
           OR NEW.heartbeat_at <> NEW.acquired_at
           OR NEW.external_deadline IS NOT NULL THEN
            RAISE EXCEPTION 'Source takeover is not safe' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.fence = OLD.fence AND OLD.owner_id IS NOT NULL THEN
        IF OLD.expires_at <= instant
           OR NEW.task_fence <> OLD.task_fence
           OR NEW.acquired_at IS DISTINCT FROM OLD.acquired_at
           OR NEW.heartbeat_at IS NULL OR NEW.heartbeat_at < OLD.heartbeat_at
           OR (OLD.external_deadline IS NOT NULL AND
               (NEW.external_deadline IS NULL OR
                NEW.external_deadline < OLD.external_deadline))
           OR (NEW.owner_id IS NOT NULL AND (
               NEW.owner_id <> OLD.owner_id OR NEW.worker_id <> OLD.worker_id
               OR NEW.phase <> OLD.phase OR NEW.expires_at < OLD.expires_at)) THEN
            RAISE EXCEPTION 'Source fence is no longer current' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'Source fence transition is invalid' USING ERRCODE='23514';
    END IF;
    IF NEW.owner_id IS NOT NULL THEN
        IF NEW.heartbeat_at > instant OR NEW.expires_at <= instant
           OR NOT EXISTS (
            SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.owner_id
              AND t.state='running' AND t.worker_id=NEW.worker_id
              AND t.fence=NEW.task_fence AND t.lease_expires_at > instant
           ) THEN
            RAISE EXCEPTION 'Source owner requires a live task' USING ERRCODE='23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM stewardship_task_run t WHERE t.id=OLD.owner_id
          AND t.state='running' AND t.worker_id=OLD.worker_id
          AND t.fence=OLD.task_fence AND t.lease_expires_at > instant
    ) THEN
        RAISE EXCEPTION 'Only a live source owner may release' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_source_membership_guard()
CREATE FUNCTION public.stewardship_source_membership_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE snapshot public.stewardship_source_snapshot%ROWTYPE;
        payload_key text;
        payload_org bigint;
        snapshot_id uuid;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'Snapshot membership is immutable' USING ERRCODE='23514';
    END IF;
    snapshot_id := CASE WHEN TG_OP='DELETE'
        THEN OLD.snapshot_id ELSE NEW.snapshot_id END;
    SELECT * INTO snapshot FROM public.stewardship_source_snapshot WHERE id=snapshot_id
        FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Snapshot membership requires its manifest'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF current_user='pk_stewardship_worker'
           AND NOT public.stewardship_setup_disposable_snapshot_v1(OLD.snapshot_id)
        THEN
            RAISE EXCEPTION 'Worker deletion requires exact expired setup membership'
                USING ERRCODE='23514';
        END IF;
        IF snapshot.compacted_at IS NULL AND snapshot.state <> 'rejected' THEN
            RAISE EXCEPTION 'Reconstructable snapshot membership is protected'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF snapshot.state <> 'staging' OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_source_lease l JOIN public.stewardship_task_run t
          ON t.id=l.owner_id WHERE l.owner_id=snapshot.task_id
          AND l.fence=snapshot.source_fence AND l.phase IN ('full','delta')
          AND l.expires_at > clock_timestamp() AND t.state='running'
          AND t.fence=l.task_fence AND t.worker_id=l.worker_id
          AND t.lease_expires_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Snapshot membership requires live staging ownership'
            USING ERRCODE='23514';
    END IF;
    CASE TG_ARGV[0]
    WHEN 'stewardship_source_family' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_family WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_member' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_member WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_contact' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_contact WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_address' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_address WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_ministry' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_ministry WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_roster' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_roster WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_fund' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_fund WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_pledge' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_pledge WHERE id=NEW.payload_id;
    WHEN 'stewardship_source_contribution' THEN
        SELECT source_key,organization_id INTO payload_key,payload_org
        FROM public.stewardship_source_contribution WHERE id=NEW.payload_id;
    ELSE
        RAISE EXCEPTION 'Unknown membership payload type' USING ERRCODE='23514';
    END CASE;
    IF payload_key IS DISTINCT FROM NEW.source_key
       OR payload_org IS DISTINCT FROM snapshot.organization_id THEN
        RAISE EXCEPTION 'Snapshot membership identity differs from its payload'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_source_payload_guard()
CREATE FUNCTION public.stewardship_source_payload_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE field text; parsed jsonb; row_values jsonb;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'Source payload versions are immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF current_user='pk_stewardship_worker' THEN
            IF NOT public.stewardship_setup_disposable_payload_v1(
                substring(TG_TABLE_NAME from length('stewardship_source_')+1), OLD.id)
            THEN
                RAISE EXCEPTION 'Worker deletion requires exact expired setup payload'
                    USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.stewardship_source_lease l
            JOIN public.stewardship_task_run t ON t.id=l.owner_id
            WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
              AND t.state='running' AND t.fence=l.task_fence AND t.worker_id=l.worker_id
              AND t.lease_expires_at > clock_timestamp()) THEN
            RAISE EXCEPTION 'Source deletion requires compaction ownership'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF octet_length(NEW.canonical) > 1048576 THEN
        RAISE EXCEPTION 'Source payload exceeds its bound' USING ERRCODE='23514';
    END IF;
    parsed := NEW.canonical::jsonb;
    row_values := to_jsonb(NEW);
    IF jsonb_typeof(parsed) <> 'object'
       OR NEW.canonical <> public.stewardship_source_canonical(parsed)
       OR NEW.digest <> encode(sha256(convert_to(NEW.canonical,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'Source payload digest or canonical form is invalid'
            USING ERRCODE='23514';
    END IF;
    FOREACH field IN ARRAY COALESCE(TG_ARGV, ARRAY[]::text[]) LOOP
        IF (row_values->>field) IS DISTINCT FROM (parsed->>field)
           OR COALESCE(row_values->>field,'') = '' THEN
            RAISE EXCEPTION 'Source relationship differs from its payload'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_source_pin_guard()
-- Narrow retention authority for disposable responses after epoch invalidation.
CREATE FUNCTION public.stewardship_test_response_cleanup_v1(response_mode text, epoch_id uuid)
    RETURNS boolean LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT response_mode='test' AND epoch_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
          AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
          AND mode='ExclusiveLock' AND granted)
      AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch
          WHERE id=epoch_id AND state='invalidated')
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials
          WHERE rehearsal_epoch_id=epoch_id)
$$;

-- Reconciliation shares the refresh owner's two live fences and work ordering.
-- This is invoker-rights metadata, not a capability to impersonate another role.
CREATE FUNCTION public.stewardship_response_source_owner_v1(snapshot_id uuid)
    RETURNS boolean LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_source_current current_source
        JOIN public.stewardship_source_snapshot source ON source.id=current_source.snapshot_id
        JOIN public.stewardship_source_lease lease ON lease.owner_id=source.task_id
            AND lease.fence=source.source_fence
        JOIN public.stewardship_task_run task ON task.id=lease.owner_id
        WHERE source.id=stewardship_response_source_owner_v1.snapshot_id AND source.state='promoted'
          AND source.compacted_at IS NULL AND lease.phase IN ('full','delta')
          AND lease.expires_at>clock_timestamp() AND task.state='running'
          AND task.fence=lease.task_fence AND task.worker_id=lease.worker_id
          AND task.lease_expires_at>clock_timestamp()
    ) AND EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
          AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
          AND mode='ExclusiveLock' AND granted
    )
$$;

CREATE FUNCTION public.stewardship_response_pin_required_v1(response_id uuid, source_id uuid)
    RETURNS boolean LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_submission response
        WHERE response.id=stewardship_response_pin_required_v1.response_id
          AND (stewardship_response_pin_required_v1.source_id IN (response.reviewed_source_id,response.validation_source_id)
            OR EXISTS (SELECT 1 FROM public.stewardship_proposed_change
                WHERE submission_id=response.id AND current_source_id=stewardship_response_pin_required_v1.source_id)
            OR EXISTS (SELECT 1 FROM public.stewardship_ministry_request
                WHERE submission_id=response.id AND resolution_source_id=stewardship_response_pin_required_v1.source_id))
    )
$$;

-- Numeric phone identity mirrors the Python comparator with explicit US
-- national context. Invalid retained source text remains opaque, never blank.
CREATE FUNCTION public.stewardship_response_phone_key_v1(input_value text)
    RETURNS jsonb LANGUAGE plpgsql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE value text; matched text[]; digits text; international boolean;
BEGIN
    value := public.stewardship_response_comparison_v1('first_name',to_jsonb(input_value));
    IF value IS NULL OR length(value)>256 THEN
        RAISE EXCEPTION 'Invalid phone comparison input' USING ERRCODE='23514';
    END IF;
    matched := regexp_match(value,
        '^(\+?[0-9][0-9 ().-]*|\([0-9][0-9 ().-]*)(?:\s*(?:ext\.?|x|#|;ext=)\s*([0-9]{1,12}))?$', 'i');
    IF matched IS NULL THEN RETURN jsonb_build_array('opaque',value); END IF;
    digits := regexp_replace(matched[1],'[^0-9]','','g');
    IF length(digits) NOT BETWEEN 1 AND 15 THEN RETURN jsonb_build_array('opaque',value); END IF;
    international := left(matched[1],1)='+';
    IF NOT international AND length(digits)=10 THEN
        digits := '1'||digits; international := true;
    ELSIF NOT international AND length(digits)=11 AND left(digits,1)='1' THEN
        international := true;
    END IF;
    RETURN jsonb_build_array(CASE WHEN international THEN 'international' ELSE 'national' END,
        digits,coalesce(matched[2],''));
END;
$$;

CREATE FUNCTION public.stewardship_response_phone_record_v1(input_value text)
    RETURNS jsonb LANGUAGE plpgsql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE key jsonb; value text;
BEGIN
    key := public.stewardship_response_phone_key_v1(input_value);
    IF key->>0='international' THEN
        value := '+'||(key->>1)||CASE WHEN key->>2='' THEN '' ELSE ';ext='||(key->>2) END;
    END IF;
    RETURN jsonb_build_object('normalized',value,'display',
        public.stewardship_response_comparison_v1('first_name',to_jsonb(input_value)));
END;
$$;

-- SQL parity with the closed census portion of family-comparison-v1.
-- Explicit Unicode whitespace and default case folding avoid database-locale
-- changes turning a no-change answer into a pending/conflicting proposal.
CREATE FUNCTION public.stewardship_response_comparison_v1(input_field text, input_value jsonb)
    RETURNS text LANGUAGE plpgsql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE normalized_value text; component text; normalized_address jsonb;
BEGIN
    IF input_field IS NULL OR input_field NOT IN (
        'prefix','first_name','middle_name','last_name','suffix','nickname','maiden_name',
        'birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language',
        'home_address','mailing_address','email_opt_out','moved_household','deceased_status','death_date','new_member') THEN
        RAISE EXCEPTION 'Unsupported response comparison field' USING ERRCODE='23514';
    END IF;
    IF input_value IS NULL OR input_value='null'::jsonb THEN RETURN NULL; END IF;
    IF input_field='new_member' THEN
        IF jsonb_typeof(input_value) <> 'object'
           OR NOT (input_value ?& ARRAY['prefix','first_name','middle_name','last_name','suffix','nickname',
               'maiden_name','birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language'])
           OR input_value-ARRAY['prefix','first_name','middle_name','last_name','suffix','nickname',
               'maiden_name','birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'Invalid proposed Member comparison' USING ERRCODE='23514';
        END IF;
        SELECT jsonb_object_agg(key,public.stewardship_response_comparison_v1(key,value))
          INTO normalized_address FROM jsonb_each(input_value);
        RETURN normalized_address::text;
    END IF;
    IF input_field IN ('home_phone','mobile_phone','work_phone') THEN
        IF jsonb_typeof(input_value)='object' THEN
            IF jsonb_typeof(input_value->'display') IS DISTINCT FROM 'string'
               OR input_value IS DISTINCT FROM public.stewardship_response_phone_record_v1(input_value->>'display') THEN
                RAISE EXCEPTION 'Invalid phone display/comparison record' USING ERRCODE='23514';
            END IF;
            RETURN public.stewardship_response_phone_key_v1(input_value->>'display')::text;
        ELSIF input_value='""'::jsonb THEN
            RETURN public.stewardship_response_phone_key_v1('')::text;
        ELSE
            RAISE EXCEPTION 'Invalid phone response value' USING ERRCODE='23514';
        END IF;
    END IF;
    IF input_field IN ('email_opt_out','moved_household','deceased_status') THEN
        IF jsonb_typeof(input_value) <> 'boolean' THEN
            RAISE EXCEPTION 'Invalid response boolean value' USING ERRCODE='23514';
        END IF;
        RETURN input_value::text;
    END IF;
    IF input_field IN ('home_address','mailing_address') THEN
        IF jsonb_typeof(input_value) <> 'object' THEN
            RAISE EXCEPTION 'Invalid response address value' USING ERRCODE='23514';
        END IF;
        IF NOT (input_value ?& ARRAY['line1','line2','city','region','postal_code','country'])
           OR input_value-ARRAY['line1','line2','city','region','postal_code','country'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'Invalid response address components' USING ERRCODE='23514';
        END IF;
        normalized_address := '{}'::jsonb;
        FOREACH component IN ARRAY ARRAY['line1','line2','city','region','postal_code','country'] LOOP
            IF jsonb_typeof(input_value->component) <> 'string' THEN
                RAISE EXCEPTION 'Invalid response address component' USING ERRCODE='23514';
            END IF;
            normalized_address := normalized_address || jsonb_build_object(component,
                public.stewardship_response_comparison_v1('email',input_value->component));
        END LOOP;
        RETURN normalized_address::text;
    END IF;
    IF jsonb_typeof(input_value) <> 'string' OR length(input_value#>>'{}')>8192 THEN
        RAISE EXCEPTION 'Invalid response comparison value' USING ERRCODE='23514';
    END IF;
    normalized_value := btrim(normalize(input_value#>>'{}',NFC),
        U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000');
    IF input_field IN ('birth_date','death_date') THEN
        IF input_value#>>'{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
            RAISE EXCEPTION 'Invalid response civil date' USING ERRCODE='23514';
        END IF;
        BEGIN
            PERFORM (normalized_value::date);
        EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN
            RAISE EXCEPTION 'Invalid response civil date' USING ERRCODE='23514';
        END;
    END IF;
    IF input_field='email' THEN
        RETURN casefold(normalized_value COLLATE pg_catalog.pg_unicode_fast);
    END IF;
    RETURN normalized_value;
END;
$$;

-- Guard the normalized persisted address as well as its comparison type. The
-- ISO/US vocabularies mirror the pinned Python dataset; exhaustive parity tests
-- make a dependency update fail until both closed schemas are deliberately updated.
CREATE FUNCTION public.stewardship_response_address_guard_v1(input_value jsonb)
    RETURNS void LANGUAGE plpgsql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE component text; value text; maximum integer;
BEGIN
    IF input_value IS NULL THEN
        RAISE EXCEPTION 'Missing response address' USING ERRCODE='23514';
    END IF;
    IF input_value='null'::jsonb THEN RETURN; END IF;
    PERFORM public.stewardship_response_comparison_v1('home_address',input_value);
    FOR component,value IN SELECT * FROM jsonb_each_text(input_value) LOOP
        maximum := CASE component WHEN 'line1' THEN 200 WHEN 'line2' THEN 200
            WHEN 'city' THEN 100 WHEN 'region' THEN 100 WHEN 'postal_code' THEN 32
            WHEN 'country' THEN 2 END;
        IF length(value)>maximum
           OR value IS DISTINCT FROM public.stewardship_response_comparison_v1('first_name',to_jsonb(value))
           OR value ~ U&'[\0001-\001F\007F\0085\2028\2029]' THEN
            RAISE EXCEPTION 'Invalid normalized response address' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF input_value->>'line1'='' OR input_value->>'city'=''
       OR NOT (input_value->>'country'=ANY(string_to_array(
           'AD,AE,AF,AG,AI,AL,AM,AO,AQ,AR,AS,AT,AU,AW,AX,AZ,BA,BB,BD,BE,BF,BG,BH,BI,BJ,BL,BM,BN,BO,BQ,BR,BS,BT,BV,BW,BY,BZ,CA,CC,CD,CF,CG,CH,CI,CK,CL,CM,CN,CO,CR,CU,CV,CW,CX,CY,CZ,DE,DJ,DK,DM,DO,DZ,EC,EE,EG,EH,ER,ES,ET,FI,FJ,FK,FM,FO,FR,GA,GB,GD,GE,GF,GG,GH,GI,GL,GM,GN,GP,GQ,GR,GS,GT,GU,GW,GY,HK,HM,HN,HR,HT,HU,ID,IE,IL,IM,IN,IO,IQ,IR,IS,IT,JE,JM,JO,JP,KE,KG,KH,KI,KM,KN,KP,KR,KW,KY,KZ,LA,LB,LC,LI,LK,LR,LS,LT,LU,LV,LY,MA,MC,MD,ME,MF,MG,MH,MK,ML,MM,MN,MO,MP,MQ,MR,MS,MT,MU,MV,MW,MX,MY,MZ,NA,NC,NE,NF,NG,NI,NL,NO,NP,NR,NU,NZ,OM,PA,PE,PF,PG,PH,PK,PL,PM,PN,PR,PS,PT,PW,PY,QA,RE,RO,RS,RU,RW,SA,SB,SC,SD,SE,SG,SH,SI,SJ,SK,SL,SM,SN,SO,SR,SS,ST,SV,SX,SY,SZ,TC,TD,TF,TG,TH,TJ,TK,TL,TM,TN,TO,TR,TT,TV,TW,TZ,UA,UG,UM,US,UY,UZ,VA,VC,VE,VG,VI,VN,VU,WF,WS,YE,YT,ZA,ZM,ZW',',')))
       OR (input_value->>'country'='US' AND (
           NOT (input_value->>'region'=ANY(string_to_array(
               'AA,AE,AK,AL,AP,AR,AS,AZ,CA,CO,CT,DC,DE,FL,GA,GU,HI,IA,ID,IL,IN,KS,KY,LA,MA,MD,ME,MI,MN,MO,MP,MS,MT,NC,ND,NE,NH,NJ,NM,NV,NY,OH,OK,OR,PA,PR,RI,SC,SD,TN,TX,UM,UT,VA,VI,VT,WA,WI,WV,WY',',')))
           OR input_value->>'postal_code' !~ '^[0-9]{5}(-[0-9]{4})?$')) THEN
        RAISE EXCEPTION 'Incomplete or invalid country-aware response address' USING ERRCODE='23514';
    END IF;
END;
$$;

-- The verified loader does not establish separate home/mailing or all-parish
-- email suppression semantics. Prove the exact Family/snapshot identity and
-- retain explicit unavailability, never reinterpret a primary address or flag.
CREATE FUNCTION public.stewardship_response_household_source_v1(
    input_snapshot uuid, input_family uuid, input_key text, input_field text)
    RETURNS jsonb LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT jsonb_build_object('available',false,'value',NULL)
    FROM public.stewardship_snapshot_family membership
    JOIN public.stewardship_family_campaign family ON membership.source_key=family.family_duid::text
    WHERE membership.snapshot_id=stewardship_response_household_source_v1.input_snapshot
      AND family.id=stewardship_response_household_source_v1.input_family
      AND family.family_duid::text=stewardship_response_household_source_v1.input_key
      AND stewardship_response_household_source_v1.input_field IN ('home_address','mailing_address','email_opt_out')
$$;

-- Independent SQL reconstruction of the closed Phase 3A field vocabulary.
-- New proposals must match this scoped validation source, not browser values.
CREATE FUNCTION public.stewardship_response_field_source_v1(
    input_snapshot uuid, input_family uuid, input_member text, input_field text)
    RETURNS jsonb LANGUAGE plpgsql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    member_value jsonb;
    contact_value jsonb;
    source_name text;
    email_value text;
BEGIN
    SELECT member.canonical::jsonb INTO member_value
    FROM public.stewardship_snapshot_member membership
    JOIN public.stewardship_source_member member ON member.id=membership.payload_id
    JOIN public.stewardship_family_campaign family ON member.family_key=family.family_duid::text
    WHERE membership.snapshot_id=input_snapshot AND membership.source_key=input_member
      AND family.id=input_family
      AND (input_field='death_date' OR (member.canonical::jsonb->'active'='true'::jsonb
          AND member.canonical::jsonb->'deceased'='false'::jsonb));
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF input_field IN ('moved_household','deceased_status') THEN
        RETURN jsonb_build_object('available',true,'value',false);
    END IF;
    IF input_field IN ('email','home_phone','mobile_phone','work_phone') THEN
        SELECT contact.canonical::jsonb INTO contact_value
        FROM public.stewardship_snapshot_contact membership
        JOIN public.stewardship_source_contact contact ON contact.id=membership.payload_id
        WHERE membership.snapshot_id=input_snapshot
          AND contact.owner_kind='member' AND contact.owner_key=input_member;
        source_name := CASE input_field WHEN 'email' THEN 'email' WHEN 'home_phone' THEN 'home'
            WHEN 'mobile_phone' THEN 'mobile' WHEN 'work_phone' THEN 'work' END;
        IF contact_value IS NULL OR NOT (contact_value->'available' ? source_name) THEN
            RETURN jsonb_build_object('available',false,'value',NULL);
        END IF;
        IF input_field <> 'email' THEN
            email_value := contact_value#>>ARRAY['phones',source_name];
            RETURN jsonb_build_object('available',true,'value',CASE
                WHEN email_value IS NULL THEN 'null'::jsonb
                WHEN email_value='' THEN '""'::jsonb
                ELSE public.stewardship_response_phone_record_v1(email_value) END);
        END IF;
        SELECT coalesce(string_agg(item->>'value',', ' ORDER BY (item->>'value') COLLATE "C"),'')
            INTO email_value FROM jsonb_array_elements(contact_value->'emails') item;
        RETURN jsonb_build_object('available',true,'value',email_value);
    END IF;
    source_name := CASE input_field WHEN 'first_name' THEN 'firstName'
        WHEN 'middle_name' THEN 'middleName' WHEN 'last_name' THEN 'lastName'
        WHEN 'prefix' THEN 'salutation' WHEN 'suffix' THEN 'suffix'
        WHEN 'nickname' THEN 'nickName' WHEN 'maiden_name' THEN 'maidenName'
        WHEN 'birth_date' THEN 'birthdate' WHEN 'death_date' THEN 'dateOfDeath' WHEN 'gender' THEN 'sex'
        WHEN 'marital_status' THEN 'maritalStatus' WHEN 'language' THEN 'language' END;
    IF source_name IS NULL THEN RETURN NULL; END IF;
    IF input_field IN ('gender','marital_status') AND jsonb_typeof(member_value->source_name)='string' THEN
        SELECT choice INTO email_value FROM unnest(CASE WHEN input_field='gender'
            THEN ARRAY['Male','Female','Unspecified']
            ELSE ARRAY['','Annulled','Divorced','Married','Single','Separated','Widowed'] END) choice
        WHERE public.stewardship_response_comparison_v1('email',to_jsonb(choice))=
            public.stewardship_response_comparison_v1('email',member_value->source_name);
        IF FOUND THEN RETURN jsonb_build_object('available',true,'value',email_value); END IF;
    END IF;
    RETURN jsonb_build_object('available',member_value ? source_name,'value',member_value->source_name);
END;
$$;

CREATE FUNCTION public.stewardship_source_pin_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE snapshot record;
BEGIN
    -- Separate role branches before planning their private table queries.
    -- A boolean AND does not prevent PostgreSQL privilege checks on subqueries.
    IF current_user='pk_stewardship_worker' THEN
      IF TG_OP='DELETE' THEN
        IF OLD.parent_kind <> 'submission'
           OR NOT public.stewardship_response_source_owner_v1(
               (SELECT snapshot_id FROM public.stewardship_source_current))
           OR public.stewardship_response_pin_required_v1(OLD.parent_id,OLD.snapshot_id)
        THEN
            RAISE EXCEPTION 'Worker may release only unused response comparison inputs'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='digest' THEN
        IF NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_daily_digest_snapshot s
            WHERE s.id=NEW.parent_id AND s.source_id=NEW.snapshot_id
              AND stewardship_daily_digest_live_v1(s.preparation_id,s.run_id,s.fence,s.worker_id)
        ) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Daily source protection requires its live exact snapshot'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='facts' THEN
        IF NEW.expires_at IS NOT NULL OR NOT (EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_fact_demand d
                ON d.id=t.domain_request_id
            WHERE t.task_type='report_facts' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp()
                AND d.population_scope='current'
                AND d.requested_source_id=NEW.snapshot_id
                AND d.claimed_generation_id IS NULL
        ) OR EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_exact_export_request r
                ON r.id=t.domain_request_id AND r.task_id=t.root_id
            WHERE t.task_type='report_exact_export' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp() AND r.population_scope='current'
                AND r.source_id=NEW.snapshot_id
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
                AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=r.id)
        )) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Worker fact input protection requires its live demand'
                USING ERRCODE='23514';
        END IF;
      ELSE
      IF (
        TG_OP <> 'INSERT' OR NEW.parent_kind <> 'submission' OR NEW.expires_at IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT (EXISTS (
            SELECT 1 FROM public.stewardship_proposed_change change
            JOIN public.stewardship_submission response ON response.id=change.submission_id
            WHERE response.id=NEW.parent_id
              AND change.execution IN ('pending','conflict','queued','failed')
        ) OR EXISTS (
            SELECT 1 FROM public.stewardship_ministry_request request
            WHERE request.submission_id=NEW.parent_id AND request.entity_kind='member'
              AND request.state IN ('new','assigned','in_progress')
        ))
        OR NOT public.stewardship_response_source_owner_v1(NEW.snapshot_id)
    ) THEN
        RAISE EXCEPTION 'Worker source protection requires current response reconciliation'
            USING ERRCODE='23514';
      END IF;
      END IF;
    END IF;
    IF current_user='pk_stewardship_web' THEN
      IF (
        (TG_OP <> 'INSERT' AND OLD.parent_kind <> 'form_baseline') OR
        (TG_OP <> 'DELETE' AND NEW.parent_kind NOT IN ('form_baseline','submission','report'))
    ) THEN
        RAISE EXCEPTION 'Web source protection is limited to Family forms'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='report' AND (
        NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_exact_export_request r
            WHERE r.id=NEW.parent_id AND r.source_id=NEW.snapshot_id
                AND r.population_scope='current'
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
        )) THEN
        RAISE EXCEPTION 'Web report protection requires its exact retained request'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='form_baseline' AND NOT EXISTS (
        SELECT 1 FROM stewardship_family_form_baseline
        WHERE id=NEW.parent_id AND source_id=NEW.snapshot_id AND state='open'
          AND expires_at=NEW.expires_at
    ) THEN
        RAISE EXCEPTION 'Web form protection requires its owned baseline'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='submission' AND NOT EXISTS (
        SELECT 1 FROM stewardship_submission response
        JOIN stewardship_family_form_baseline baseline ON baseline.id=response.baseline_id
        WHERE response.id=NEW.parent_id AND baseline.state='open'
          AND NEW.snapshot_id IN (response.reviewed_source_id,response.validation_source_id)
          AND NEW.expires_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Web response protection requires its final submission'
            USING ERRCODE='23514';
    END IF;
    END IF;
    IF TG_OP='DELETE' THEN
        PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id FOR UPDATE;
        RETURN OLD;
    END IF;
    SELECT state, compacted_at INTO snapshot FROM stewardship_source_snapshot WHERE id=NEW.snapshot_id
        FOR UPDATE;
    IF NOT FOUND OR snapshot.state <> 'promoted'
       OR snapshot.compacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Only reconstructable promoted snapshots can be protected'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_source_pin_mutable_v1()
CREATE FUNCTION public.stewardship_source_pin_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."snapshot_id" IS DISTINCT FROM OLD."snapshot_id" OR NEW."parent_kind" IS DISTINCT FROM OLD."parent_kind" OR NEW."parent_id" IS DISTINCT FROM OLD."parent_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_source_promotion_pair_guard()
CREATE FUNCTION public.stewardship_source_promotion_pair_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.state <> 'promoted' AND NEW.state='promoted' AND NOT EXISTS (
        SELECT 1 FROM stewardship_source_current
        WHERE snapshot_id=NEW.id AND generation=NEW.generation
    ) THEN
        RAISE EXCEPTION 'Source promotion and pointer must commit together'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;

-- FUNCTION: stewardship_source_refresh_attempt_immutable_v1()
CREATE FUNCTION public.stewardship_source_refresh_attempt_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_source_refresh_command_immutable_v1()
CREATE FUNCTION public.stewardship_source_refresh_command_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_source_refresh_fallback_immutable_v1()
CREATE FUNCTION public.stewardship_source_refresh_fallback_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_source_refresh_request_immutable_v1()
CREATE FUNCTION public.stewardship_source_refresh_request_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_source_refresh_tick_immutable_v1()
CREATE FUNCTION public.stewardship_source_refresh_tick_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_source_snapshot_guard()
CREATE FUNCTION public.stewardship_source_snapshot_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE current_source stewardship_source_current%ROWTYPE;
        evidence jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Source manifests are permanent' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state <> 'staging' OR NEW.completed_at IS NOT NULL
           OR NEW.content_digest <> '' OR NEW.compacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Source snapshots must begin in staging'
                USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='promoted' THEN
        IF (to_jsonb(NEW)-ARRAY['version','updated_at','actor_id','correlation_id',
                'compacted_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['version','updated_at','actor_id','correlation_id',
                'compacted_at']) OR OLD.compacted_at IS NOT NULL
           OR NEW.compacted_at IS NULL THEN
            RAISE EXCEPTION 'Promoted source manifests are immutable'
                USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_source_current WHERE snapshot_id=OLD.id)
           OR EXISTS (SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=OLD.id
                AND (expires_at IS NULL OR expires_at > clock_timestamp()))
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
                JOIN stewardship_task_run t ON t.id=l.owner_id
                WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
                  AND t.state='running' AND t.fence=l.task_fence
                  AND t.worker_id=l.worker_id
                  AND t.lease_expires_at > clock_timestamp())
        THEN
            RAISE EXCEPTION 'Source snapshot is protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    ELSIF NOT ((OLD.state='staging' AND NEW.state IN ('staging','ready','rejected'))
        OR (OLD.state='ready' AND NEW.state IN ('promoted','rejected'))) THEN
        RAISE EXCEPTION 'Source snapshot transition is invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.state='ready' AND (
        NEW.counts IS DISTINCT FROM OLD.counts
        OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
        OR NEW.cursor IS DISTINCT FROM OLD.cursor
        OR NEW.validation IS DISTINCT FROM OLD.validation
    ) THEN
        RAISE EXCEPTION 'Validated source evidence is immutable' USING ERRCODE='23514';
    END IF;

    IF TG_OP='UPDATE' AND NEW.state='rejected' THEN
        IF (to_jsonb(NEW)-ARRAY[
                'version','updated_at','actor_id','correlation_id','state'])
           IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['version','updated_at','actor_id','correlation_id','state'])
           OR NOT EXISTS (
            SELECT 1 FROM stewardship_source_lease l
            JOIN stewardship_task_run t ON t.id=l.owner_id
            WHERE l.phase IN ('full','delta') AND l.fence >= OLD.source_fence
              AND l.expires_at > clock_timestamp() AND t.state='running'
              AND t.fence=l.task_fence AND t.worker_id=l.worker_id
              AND t.lease_expires_at > clock_timestamp()
              AND NEW.actor_id=l.worker_id
              AND (l.fence > OLD.source_fence OR (
                   l.owner_id=OLD.task_id AND l.phase=OLD.kind))
           ) THEN
            RAISE EXCEPTION 'Source rejection requires unchanged evidence/live owner'
                USING ERRCODE='23514';
        END IF;
        -- A newer source fence is possible only after prior external drainage.
        -- Keep the old task/fence binding; never rebind its payloads to recovery.
        RETURN NEW;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
        JOIN stewardship_task_run t ON t.id=l.owner_id
        WHERE l.owner_id=NEW.task_id AND l.fence=NEW.source_fence AND l.phase=NEW.kind
          AND l.expires_at > clock_timestamp() AND t.state='running'
          AND t.fence=l.task_fence AND t.worker_id=l.worker_id
          AND t.lease_expires_at > clock_timestamp()) THEN
        RAISE EXCEPTION 'Source snapshot requires its live fenced owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('ready','promoted') AND (
        NEW.completed_at IS NULL OR NEW.completed_at < NEW.started_at
        OR NEW.completed_at > clock_timestamp() OR NEW.content_digest=''
        OR NEW.validation->>'schema' IS DISTINCT FROM 'source-corpus-v1'
        OR NEW.validation->'complete' IS DISTINCT FROM 'true'::jsonb
    ) THEN
        RAISE EXCEPTION 'Source snapshot is not validated' USING ERRCODE='23514';
    END IF;
    IF NEW.state='promoted' THEN
        SELECT * INTO current_source FROM stewardship_source_current
            WHERE singleton FOR UPDATE;
        IF NOT FOUND OR NEW.generation <> current_source.generation+1
           OR NEW.base_id IS DISTINCT FROM current_source.snapshot_id
           OR (current_source.organization_id IS NOT NULL AND
               NEW.organization_id <> current_source.organization_id)
           OR NEW.promoted_at < NEW.completed_at OR NEW.promoted_at > clock_timestamp()
        THEN
            RAISE EXCEPTION 'Source promotion has a stale or inconsistent base'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.state='ready' THEN
        evidence := stewardship_source_corpus_evidence(NEW.id);
        IF NEW.counts IS DISTINCT FROM evidence->'counts'
           OR NEW.content_digest IS DISTINCT FROM evidence->>'digest' THEN
            RAISE EXCEPTION 'Source evidence differs from its complete corpus'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- FUNCTION: stewardship_source_snapshot_mutable_v1()
CREATE FUNCTION public.stewardship_source_snapshot_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."organization_id" IS DISTINCT FROM OLD."organization_id" OR NEW."kind" IS DISTINCT FROM OLD."kind" OR NEW."task_id" IS DISTINCT FROM OLD."task_id" OR NEW."source_fence" IS DISTINCT FROM OLD."source_fence" OR NEW."base_id" IS DISTINCT FROM OLD."base_id" OR NEW."started_at" IS DISTINCT FROM OLD."started_at" OR (OLD."completed_at" IS NOT NULL AND NEW."completed_at" IS DISTINCT FROM OLD."completed_at") OR (OLD."promoted_at" IS NOT NULL AND NEW."promoted_at" IS DISTINCT FROM OLD."promoted_at") OR (OLD."generation" IS NOT NULL AND NEW."generation" IS DISTINCT FROM OLD."generation") OR (OLD."compacted_at" IS NOT NULL AND NEW."compacted_at" IS DISTINCT FROM OLD."compacted_at") THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_system_configuration_mutable_v1()
CREATE FUNCTION public.stewardship_system_configuration_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR false /* initial setup recipient has its own guard */ OR NEW."restore_review_required" IS DISTINCT FROM OLD."restore_review_required" OR NEW."restore_id" IS DISTINCT FROM OLD."restore_id" OR NEW."restore_backup_at" IS DISTINCT FROM OLD."restore_backup_at" OR NEW."restore_activated_at" IS DISTINCT FROM OLD."restore_activated_at" OR NEW."restore_released_at" IS DISTINCT FROM OLD."restore_released_at" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_task_event_binding_v1()
CREATE FUNCTION public.stewardship_task_event_binding_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    DECLARE
        run_record public.stewardship_task_run%ROWTYPE;
        previous public.stewardship_task_event%ROWTYPE;
    BEGIN
        SELECT * INTO run_record FROM public.stewardship_task_run WHERE id = NEW.run_id;
        SELECT * INTO previous FROM public.stewardship_task_event
        WHERE run_id = NEW.run_id ORDER BY version DESC LIMIT 1;
        IF run_record.id IS NULL OR NEW.version IS DISTINCT FROM run_record.version
           OR NEW.created_at IS DISTINCT FROM run_record.updated_at
           OR NEW.actor_id IS DISTINCT FROM run_record.actor_id
           OR NEW.correlation_id IS DISTINCT FROM run_record.correlation_id
           OR NEW.state IS DISTINCT FROM run_record.state
           OR NEW.action IS DISTINCT FROM run_record.action
           OR NEW.attempt IS DISTINCT FROM run_record.attempt
           OR NEW.fence IS DISTINCT FROM run_record.fence
           OR NEW.worker_id IS DISTINCT FROM run_record.worker_id
           OR NEW.heartbeat_at IS DISTINCT FROM run_record.heartbeat_at
           OR NEW.lease_expires_at IS DISTINCT FROM run_record.lease_expires_at
           OR NEW.not_before IS DISTINCT FROM run_record.not_before
           OR NEW.progress_current IS DISTINCT FROM run_record.progress_current
           OR NEW.progress_total IS DISTINCT FROM run_record.progress_total
           OR NEW.version <> COALESCE(previous.version, 0) + 1
           OR NEW.previous_state IS DISTINCT FROM COALESCE(previous.state, '') THEN
            RAISE EXCEPTION 'Invalid task transition evidence' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;

-- FUNCTION: stewardship_task_event_immutable_v1()
CREATE FUNCTION public.stewardship_task_event_immutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION 'Historical records are append-only'
                    USING ERRCODE = '23514';
            END;
            $$;

-- FUNCTION: stewardship_task_event_phase_v1()
CREATE FUNCTION public.stewardship_task_event_phase_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.phase IS DISTINCT FROM (
        SELECT phase FROM stewardship_task_run WHERE id=NEW.run_id
    ) THEN
        RAISE EXCEPTION 'Task phase history must match its execution'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_task_history_v1()
CREATE FUNCTION public.stewardship_task_history_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    BEGIN
        INSERT INTO public.stewardship_task_event
            (id, created_at, actor_id, correlation_id, run_id, version,
             previous_state, state, action, attempt, fence, worker_id,
             heartbeat_at, lease_expires_at, not_before,
             progress_current, progress_total, phase)
        VALUES (gen_random_uuid(), NEW.updated_at, NEW.actor_id, NEW.correlation_id,
                NEW.id, NEW.version,
                CASE WHEN TG_OP = 'INSERT' THEN '' ELSE OLD.state END,
                NEW.state, NEW.action, NEW.attempt, NEW.fence, NEW.worker_id,
                NEW.heartbeat_at, NEW.lease_expires_at, NEW.not_before,
                NEW.progress_current, NEW.progress_total, NEW.phase);
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id,
                'task_' || NEW.action, NEW.id);
        RETURN NEW;
    END;
    $$;

-- FUNCTION: stewardship_task_phase_v1()
CREATE FUNCTION public.stewardship_task_phase_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.phase<>'unspecified' THEN
            RAISE EXCEPTION 'New task phase must be unspecified' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.action='claim' THEN
        IF NEW.phase<>'starting' THEN
            RAISE EXCEPTION 'Task claim must reset its phase' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.phase IS DISTINCT FROM OLD.phase THEN
        IF NEW.action<>'progress' OR NEW.phase='unspecified' THEN
            RAISE EXCEPTION 'Task phase requires progress' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_task_run_mutable_v1()
CREATE FUNCTION public.stewardship_task_run_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."task_type" IS DISTINCT FROM OLD."task_type" OR NEW."idempotency_key" IS DISTINCT FROM OLD."idempotency_key" OR NEW."root_id" IS DISTINCT FROM OLD."root_id" OR NEW."parent_id" IS DISTINCT FROM OLD."parent_id" OR NEW."retry_sequence" IS DISTINCT FROM OLD."retry_sequence" OR NEW."retry_command_id" IS DISTINCT FROM OLD."retry_command_id" OR NEW."domain_request_id" IS DISTINCT FROM OLD."domain_request_id" OR NEW."initiated_by_id" IS DISTINCT FROM OLD."initiated_by_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_task_scheduler_v1()
CREATE FUNCTION public.stewardship_task_scheduler_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Capability-based, so the same boundary covers independently named test
    -- and provisioned logins. Full worker writers retain the existing guards.
    IF NOT has_column_privilege(current_user,
            'public.stewardship_task_run', 'worker_id', 'UPDATE') THEN
        IF OLD.task_type <> 'source_refresh'
           OR OLD.state NOT IN ('queued', 'retry_wait', 'abandoned')
           OR NEW.state <> 'cancelled'
           OR NEW.action NOT IN ('safe_cancel', 'recovery_cancel')
           OR NEW.lease_expires_at IS NOT NULL
           OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
                AND locktype='advisory' AND classid=736229 AND objid=1
                AND objsubid=2 AND granted)
           OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
                AND locktype='advisory' AND classid=736220 AND objid=1
                AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Scheduler may only cancel waiting source work'
                USING ERRCODE='42501';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_task_state_v1()
CREATE FUNCTION public.stewardship_task_state_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
    DECLARE
        root_record public.stewardship_task_run%ROWTYPE;
        previous public.stewardship_task_run%ROWTYPE;
        instant timestamptz := statement_timestamp();
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Task history cannot be deleted' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            NEW.created_at := instant;
            NEW.updated_at := instant;
            IF NEW.version <> 1 OR NEW.state <> 'queued' OR NEW.attempt <> 0
               OR NEW.fence <> 0 OR NEW.worker_id IS NOT NULL
               OR NEW.heartbeat_at IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
               OR NEW.progress_current <> 0 OR NEW.progress_total <> 0
               OR NEW.actor_id IS DISTINCT FROM NEW.initiated_by_id THEN
                RAISE EXCEPTION 'Invalid initial task state' USING ERRCODE = '23514';
            END IF;
            IF NEW.retry_sequence = 0 THEN
                IF NEW.root_id IS DISTINCT FROM NEW.id OR NEW.parent_id IS NOT NULL
                   OR NEW.retry_command_id IS NOT NULL OR NEW.action <> 'created'
                   OR (NEW.idempotency_key IS NOT NULL AND NEW.idempotency_key !~
                       '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
                THEN
                    RAISE EXCEPTION 'Invalid task root identity'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT * INTO root_record FROM public.stewardship_task_run
                WHERE id = NEW.root_id FOR UPDATE;
                SELECT * INTO previous FROM public.stewardship_task_run
                WHERE root_id = NEW.root_id ORDER BY retry_sequence DESC LIMIT 1;
                IF root_record.id IS NULL OR root_record.retry_sequence <> 0
                   OR previous.state <> 'failed'
                   OR NEW.parent_id IS DISTINCT FROM previous.id
                   OR NEW.retry_sequence <> previous.retry_sequence + 1
                   OR NEW.task_type IS DISTINCT FROM root_record.task_type
                   OR NEW.domain_request_id IS DISTINCT FROM
                      root_record.domain_request_id
                   OR NEW.initiated_by_id IS NULL OR NEW.retry_command_id IS NULL
                   OR NEW.action <> 'explicit_retry'
                   OR NEW.idempotency_key IS DISTINCT FROM
                       'retry:' || NEW.root_id::text || ':' || NEW.retry_sequence::text
                THEN
                    RAISE EXCEPTION 'Invalid explicit task retry'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.state IN ('succeeded', 'failed', 'cancelled') THEN
            RAISE EXCEPTION 'Terminal tasks cannot be reopened' USING ERRCODE = '23514';
        END IF;
        -- Every action names its precise permitted edge. Codes carry no raw
        -- provider errors, arguments, output text or private values.
        IF NOT (
            (NEW.action = 'claim' AND OLD.state IN ('queued', 'retry_wait')
                AND NEW.state = 'running' AND OLD.not_before <= instant)
            OR (OLD.state = 'running' AND (
                (NEW.action IN ('heartbeat', 'progress') AND NEW.state = 'running')
                OR (NEW.action = 'complete' AND NEW.state = 'succeeded')
                OR (NEW.action = 'retryable_failure' AND NEW.state = 'retry_wait')
                OR (NEW.action = 'permanent_failure' AND NEW.state = 'failed')
                OR (NEW.action = 'lease_expired' AND NEW.state = 'abandoned'
                    AND OLD.lease_expires_at <= instant)))
            OR (NEW.action = 'safe_cancel' AND NEW.state = 'cancelled'
                AND OLD.state IN ('queued', 'running', 'retry_wait'))
            OR (OLD.state = 'abandoned' AND (
                (NEW.action = 'recovery_retry' AND NEW.state = 'retry_wait')
                OR (NEW.action = 'recovery_complete' AND NEW.state = 'succeeded')
                OR (NEW.action = 'recovery_fail' AND NEW.state = 'failed')
                OR (NEW.action = 'recovery_cancel' AND NEW.state = 'cancelled')))
        ) THEN
            RAISE EXCEPTION 'Invalid task transition or timing' USING ERRCODE = '23514';
        END IF;
        IF NEW.action = 'claim' THEN
            IF NEW.worker_id IS NULL OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
               OR NEW.fence <> OLD.fence + 1 OR NEW.attempt <> OLD.attempt + 1 THEN
                RAISE EXCEPTION 'Invalid task claim' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.worker_id IS DISTINCT FROM OLD.worker_id
               OR NEW.attempt <> OLD.attempt
               OR NEW.fence <> OLD.fence + (CASE WHEN NEW.action = 'lease_expired'
                                               THEN 1 ELSE 0 END) THEN
                RAISE EXCEPTION 'Invalid task claim binding' USING ERRCODE = '23514';
            END IF;
        END IF;
        IF OLD.state = 'running' AND NEW.action <> 'lease_expired'
           AND (OLD.lease_expires_at <= instant
                OR NEW.actor_id IS DISTINCT FROM OLD.worker_id) THEN
            RAISE EXCEPTION 'Task lease is expired or not owned'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.action = 'lease_expired' AND NEW.actor_id IS NOT NULL THEN
            RAISE EXCEPTION 'Lease expiry has no worker actor' USING ERRCODE = '23514';
        END IF;
        IF NEW.action IN ('claim', 'heartbeat') THEN
            IF NEW.heartbeat_at IS NULL OR NEW.heartbeat_at > instant
               OR NEW.heartbeat_at < NEW.created_at
               OR NEW.lease_expires_at IS NULL OR NEW.lease_expires_at <= instant
               OR NEW.lease_expires_at > instant + interval '300 seconds' THEN
                RAISE EXCEPTION 'Invalid task lease interval' USING ERRCODE = '23514';
            END IF;
            NEW.heartbeat_at := instant;
        ELSIF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
              OR (NEW.state = 'running'
                  AND NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at)
              OR (NEW.state <> 'running' AND NEW.lease_expires_at IS NOT NULL) THEN
            RAISE EXCEPTION 'Task lease fields require claim or heartbeat'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.action IN ('retryable_failure', 'recovery_retry') THEN
            IF NEW.not_before <= instant
               OR NEW.not_before > instant + interval '86400 seconds' THEN
                RAISE EXCEPTION 'Invalid task retry time' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.not_before IS DISTINCT FROM OLD.not_before THEN
            RAISE EXCEPTION 'Task retry time is bound to retry transitions'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.action = 'claim' THEN
            IF NEW.progress_current <> 0 OR NEW.progress_total <> 0 THEN
                RAISE EXCEPTION 'Task claims must reset attempt progress'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.action = 'progress' THEN
            IF NEW.progress_current < OLD.progress_current
               OR NEW.progress_total < OLD.progress_total THEN
                RAISE EXCEPTION 'Task progress cannot move backwards'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.progress_current <> OLD.progress_current
              OR NEW.progress_total <> OLD.progress_total THEN
            RAISE EXCEPTION 'Task progress requires its own action'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $_$;

-- FUNCTION: stewardship_timezone_name_v1(text)
CREATE FUNCTION public.stewardship_timezone_name_v1(zone text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE zone
        WHEN 'Africa/Accra' THEN 'Africa/Abidjan'
        WHEN 'Africa/Addis_Ababa' THEN 'Africa/Nairobi'
        WHEN 'Africa/Asmara' THEN 'Africa/Nairobi'
        WHEN 'Africa/Asmera' THEN 'Africa/Nairobi'
        WHEN 'Africa/Bamako' THEN 'Africa/Abidjan'
        WHEN 'Africa/Bangui' THEN 'Africa/Lagos'
        WHEN 'Africa/Banjul' THEN 'Africa/Abidjan'
        WHEN 'Africa/Blantyre' THEN 'Africa/Maputo'
        WHEN 'Africa/Brazzaville' THEN 'Africa/Lagos'
        WHEN 'Africa/Bujumbura' THEN 'Africa/Maputo'
        WHEN 'Africa/Conakry' THEN 'Africa/Abidjan'
        WHEN 'Africa/Dakar' THEN 'Africa/Abidjan'
        WHEN 'Africa/Dar_es_Salaam' THEN 'Africa/Nairobi'
        WHEN 'Africa/Djibouti' THEN 'Africa/Nairobi'
        WHEN 'Africa/Douala' THEN 'Africa/Lagos'
        WHEN 'Africa/Freetown' THEN 'Africa/Abidjan'
        WHEN 'Africa/Gaborone' THEN 'Africa/Maputo'
        WHEN 'Africa/Harare' THEN 'Africa/Maputo'
        WHEN 'Africa/Kampala' THEN 'Africa/Nairobi'
        WHEN 'Africa/Kigali' THEN 'Africa/Maputo'
        WHEN 'Africa/Kinshasa' THEN 'Africa/Lagos'
        WHEN 'Africa/Libreville' THEN 'Africa/Lagos'
        WHEN 'Africa/Lome' THEN 'Africa/Abidjan'
        WHEN 'Africa/Luanda' THEN 'Africa/Lagos'
        WHEN 'Africa/Lubumbashi' THEN 'Africa/Maputo'
        WHEN 'Africa/Lusaka' THEN 'Africa/Maputo'
        WHEN 'Africa/Malabo' THEN 'Africa/Lagos'
        WHEN 'Africa/Maseru' THEN 'Africa/Johannesburg'
        WHEN 'Africa/Mbabane' THEN 'Africa/Johannesburg'
        WHEN 'Africa/Mogadishu' THEN 'Africa/Nairobi'
        WHEN 'Africa/Niamey' THEN 'Africa/Lagos'
        WHEN 'Africa/Nouakchott' THEN 'Africa/Abidjan'
        WHEN 'Africa/Ouagadougou' THEN 'Africa/Abidjan'
        WHEN 'Africa/Porto-Novo' THEN 'Africa/Lagos'
        WHEN 'Africa/Timbuktu' THEN 'Africa/Abidjan'
        WHEN 'America/Anguilla' THEN 'America/Puerto_Rico'
        WHEN 'America/Antigua' THEN 'America/Puerto_Rico'
        WHEN 'America/Argentina/ComodRivadavia' THEN 'America/Argentina/Catamarca'
        WHEN 'America/Aruba' THEN 'America/Puerto_Rico'
        WHEN 'America/Atikokan' THEN 'America/Panama'
        WHEN 'America/Atka' THEN 'America/Adak'
        WHEN 'America/Blanc-Sablon' THEN 'America/Puerto_Rico'
        WHEN 'America/Buenos_Aires' THEN 'America/Argentina/Buenos_Aires'
        WHEN 'America/Catamarca' THEN 'America/Argentina/Catamarca'
        WHEN 'America/Cayman' THEN 'America/Panama'
        WHEN 'America/Coral_Harbour' THEN 'America/Panama'
        WHEN 'America/Cordoba' THEN 'America/Argentina/Cordoba'
        WHEN 'America/Creston' THEN 'America/Phoenix'
        WHEN 'America/Curacao' THEN 'America/Puerto_Rico'
        WHEN 'America/Dominica' THEN 'America/Puerto_Rico'
        WHEN 'America/Ensenada' THEN 'America/Tijuana'
        WHEN 'America/Fort_Wayne' THEN 'America/Indiana/Indianapolis'
        WHEN 'America/Godthab' THEN 'America/Nuuk'
        WHEN 'America/Grenada' THEN 'America/Puerto_Rico'
        WHEN 'America/Guadeloupe' THEN 'America/Puerto_Rico'
        WHEN 'America/Indianapolis' THEN 'America/Indiana/Indianapolis'
        WHEN 'America/Jujuy' THEN 'America/Argentina/Jujuy'
        WHEN 'America/Knox_IN' THEN 'America/Indiana/Knox'
        WHEN 'America/Kralendijk' THEN 'America/Puerto_Rico'
        WHEN 'America/Louisville' THEN 'America/Kentucky/Louisville'
        WHEN 'America/Lower_Princes' THEN 'America/Puerto_Rico'
        WHEN 'America/Marigot' THEN 'America/Puerto_Rico'
        WHEN 'America/Mendoza' THEN 'America/Argentina/Mendoza'
        WHEN 'America/Montreal' THEN 'America/Toronto'
        WHEN 'America/Montserrat' THEN 'America/Puerto_Rico'
        WHEN 'America/Nassau' THEN 'America/Toronto'
        WHEN 'America/Nipigon' THEN 'America/Toronto'
        WHEN 'America/Pangnirtung' THEN 'America/Iqaluit'
        WHEN 'America/Port_of_Spain' THEN 'America/Puerto_Rico'
        WHEN 'America/Porto_Acre' THEN 'America/Rio_Branco'
        WHEN 'America/Rainy_River' THEN 'America/Winnipeg'
        WHEN 'America/Rosario' THEN 'America/Argentina/Cordoba'
        WHEN 'America/Santa_Isabel' THEN 'America/Tijuana'
        WHEN 'America/Shiprock' THEN 'America/Denver'
        WHEN 'America/St_Barthelemy' THEN 'America/Puerto_Rico'
        WHEN 'America/St_Kitts' THEN 'America/Puerto_Rico'
        WHEN 'America/St_Lucia' THEN 'America/Puerto_Rico'
        WHEN 'America/St_Thomas' THEN 'America/Puerto_Rico'
        WHEN 'America/St_Vincent' THEN 'America/Puerto_Rico'
        WHEN 'America/Thunder_Bay' THEN 'America/Toronto'
        WHEN 'America/Tortola' THEN 'America/Puerto_Rico'
        WHEN 'America/Virgin' THEN 'America/Puerto_Rico'
        WHEN 'America/Yellowknife' THEN 'America/Edmonton'
        WHEN 'Antarctica/DumontDUrville' THEN 'Pacific/Port_Moresby'
        WHEN 'Antarctica/McMurdo' THEN 'Pacific/Auckland'
        WHEN 'Antarctica/South_Pole' THEN 'Pacific/Auckland'
        WHEN 'Antarctica/Syowa' THEN 'Asia/Riyadh'
        WHEN 'Arctic/Longyearbyen' THEN 'Europe/Berlin'
        WHEN 'Asia/Aden' THEN 'Asia/Riyadh'
        WHEN 'Asia/Ashkhabad' THEN 'Asia/Ashgabat'
        WHEN 'Asia/Bahrain' THEN 'Asia/Qatar'
        WHEN 'Asia/Brunei' THEN 'Asia/Kuching'
        WHEN 'Asia/Calcutta' THEN 'Asia/Kolkata'
        WHEN 'Asia/Choibalsan' THEN 'Asia/Ulaanbaatar'
        WHEN 'Asia/Chongqing' THEN 'Asia/Shanghai'
        WHEN 'Asia/Chungking' THEN 'Asia/Shanghai'
        WHEN 'Asia/Dacca' THEN 'Asia/Dhaka'
        WHEN 'Asia/Harbin' THEN 'Asia/Shanghai'
        WHEN 'Asia/Istanbul' THEN 'Europe/Istanbul'
        WHEN 'Asia/Kashgar' THEN 'Asia/Urumqi'
        WHEN 'Asia/Katmandu' THEN 'Asia/Kathmandu'
        WHEN 'Asia/Kuala_Lumpur' THEN 'Asia/Singapore'
        WHEN 'Asia/Kuwait' THEN 'Asia/Riyadh'
        WHEN 'Asia/Macao' THEN 'Asia/Macau'
        WHEN 'Asia/Muscat' THEN 'Asia/Dubai'
        WHEN 'Asia/Phnom_Penh' THEN 'Asia/Bangkok'
        WHEN 'Asia/Rangoon' THEN 'Asia/Yangon'
        WHEN 'Asia/Saigon' THEN 'Asia/Ho_Chi_Minh'
        WHEN 'Asia/Tel_Aviv' THEN 'Asia/Jerusalem'
        WHEN 'Asia/Thimbu' THEN 'Asia/Thimphu'
        WHEN 'Asia/Ujung_Pandang' THEN 'Asia/Makassar'
        WHEN 'Asia/Ulan_Bator' THEN 'Asia/Ulaanbaatar'
        WHEN 'Asia/Vientiane' THEN 'Asia/Bangkok'
        WHEN 'Atlantic/Faeroe' THEN 'Atlantic/Faroe'
        WHEN 'Atlantic/Jan_Mayen' THEN 'Europe/Berlin'
        WHEN 'Atlantic/Reykjavik' THEN 'Africa/Abidjan'
        WHEN 'Atlantic/St_Helena' THEN 'Africa/Abidjan'
        WHEN 'Australia/ACT' THEN 'Australia/Sydney'
        WHEN 'Australia/Canberra' THEN 'Australia/Sydney'
        WHEN 'Australia/Currie' THEN 'Australia/Hobart'
        WHEN 'Australia/LHI' THEN 'Australia/Lord_Howe'
        WHEN 'Australia/North' THEN 'Australia/Darwin'
        WHEN 'Australia/NSW' THEN 'Australia/Sydney'
        WHEN 'Australia/Queensland' THEN 'Australia/Brisbane'
        WHEN 'Australia/South' THEN 'Australia/Adelaide'
        WHEN 'Australia/Tasmania' THEN 'Australia/Hobart'
        WHEN 'Australia/Victoria' THEN 'Australia/Melbourne'
        WHEN 'Australia/West' THEN 'Australia/Perth'
        WHEN 'Australia/Yancowinna' THEN 'Australia/Broken_Hill'
        WHEN 'Brazil/Acre' THEN 'America/Rio_Branco'
        WHEN 'Brazil/DeNoronha' THEN 'America/Noronha'
        WHEN 'Brazil/East' THEN 'America/Sao_Paulo'
        WHEN 'Brazil/West' THEN 'America/Manaus'
        WHEN 'Canada/Atlantic' THEN 'America/Halifax'
        WHEN 'Canada/Central' THEN 'America/Winnipeg'
        WHEN 'Canada/Eastern' THEN 'America/Toronto'
        WHEN 'Canada/Mountain' THEN 'America/Edmonton'
        WHEN 'Canada/Newfoundland' THEN 'America/St_Johns'
        WHEN 'Canada/Pacific' THEN 'America/Vancouver'
        WHEN 'Canada/Saskatchewan' THEN 'America/Regina'
        WHEN 'Canada/Yukon' THEN 'America/Whitehorse'
        WHEN 'CET' THEN 'Europe/Brussels'
        WHEN 'Chile/Continental' THEN 'America/Santiago'
        WHEN 'Chile/EasterIsland' THEN 'Pacific/Easter'
        WHEN 'CST6CDT' THEN 'America/Chicago'
        WHEN 'Cuba' THEN 'America/Havana'
        WHEN 'EET' THEN 'Europe/Athens'
        WHEN 'Egypt' THEN 'Africa/Cairo'
        WHEN 'Eire' THEN 'Europe/Dublin'
        WHEN 'EST' THEN 'America/Panama'
        WHEN 'EST5EDT' THEN 'America/New_York'
        WHEN 'Etc/GMT-0' THEN 'Etc/GMT'
        WHEN 'Etc/GMT+0' THEN 'Etc/GMT'
        WHEN 'Etc/GMT0' THEN 'Etc/GMT'
        WHEN 'Etc/Greenwich' THEN 'Etc/GMT'
        WHEN 'Etc/UCT' THEN 'Etc/UTC'
        WHEN 'Etc/Universal' THEN 'Etc/UTC'
        WHEN 'Etc/Zulu' THEN 'Etc/UTC'
        WHEN 'Europe/Amsterdam' THEN 'Europe/Brussels'
        WHEN 'Europe/Belfast' THEN 'Europe/London'
        WHEN 'Europe/Bratislava' THEN 'Europe/Prague'
        WHEN 'Europe/Busingen' THEN 'Europe/Zurich'
        WHEN 'Europe/Copenhagen' THEN 'Europe/Berlin'
        WHEN 'Europe/Guernsey' THEN 'Europe/London'
        WHEN 'Europe/Isle_of_Man' THEN 'Europe/London'
        WHEN 'Europe/Jersey' THEN 'Europe/London'
        WHEN 'Europe/Kiev' THEN 'Europe/Kyiv'
        WHEN 'Europe/Ljubljana' THEN 'Europe/Belgrade'
        WHEN 'Europe/Luxembourg' THEN 'Europe/Brussels'
        WHEN 'Europe/Mariehamn' THEN 'Europe/Helsinki'
        WHEN 'Europe/Monaco' THEN 'Europe/Paris'
        WHEN 'Europe/Nicosia' THEN 'Asia/Nicosia'
        WHEN 'Europe/Oslo' THEN 'Europe/Berlin'
        WHEN 'Europe/Podgorica' THEN 'Europe/Belgrade'
        WHEN 'Europe/San_Marino' THEN 'Europe/Rome'
        WHEN 'Europe/Sarajevo' THEN 'Europe/Belgrade'
        WHEN 'Europe/Skopje' THEN 'Europe/Belgrade'
        WHEN 'Europe/Stockholm' THEN 'Europe/Berlin'
        WHEN 'Europe/Tiraspol' THEN 'Europe/Chisinau'
        WHEN 'Europe/Uzhgorod' THEN 'Europe/Kyiv'
        WHEN 'Europe/Vaduz' THEN 'Europe/Zurich'
        WHEN 'Europe/Vatican' THEN 'Europe/Rome'
        WHEN 'Europe/Zagreb' THEN 'Europe/Belgrade'
        WHEN 'Europe/Zaporozhye' THEN 'Europe/Kyiv'
        WHEN 'GB' THEN 'Europe/London'
        WHEN 'GB-Eire' THEN 'Europe/London'
        WHEN 'GMT' THEN 'Etc/GMT'
        WHEN 'GMT-0' THEN 'Etc/GMT'
        WHEN 'GMT+0' THEN 'Etc/GMT'
        WHEN 'GMT0' THEN 'Etc/GMT'
        WHEN 'Greenwich' THEN 'Etc/GMT'
        WHEN 'Hongkong' THEN 'Asia/Hong_Kong'
        WHEN 'HST' THEN 'Pacific/Honolulu'
        WHEN 'Iceland' THEN 'Africa/Abidjan'
        WHEN 'Indian/Antananarivo' THEN 'Africa/Nairobi'
        WHEN 'Indian/Christmas' THEN 'Asia/Bangkok'
        WHEN 'Indian/Cocos' THEN 'Asia/Yangon'
        WHEN 'Indian/Comoro' THEN 'Africa/Nairobi'
        WHEN 'Indian/Kerguelen' THEN 'Indian/Maldives'
        WHEN 'Indian/Mahe' THEN 'Asia/Dubai'
        WHEN 'Indian/Mayotte' THEN 'Africa/Nairobi'
        WHEN 'Indian/Reunion' THEN 'Asia/Dubai'
        WHEN 'Iran' THEN 'Asia/Tehran'
        WHEN 'Israel' THEN 'Asia/Jerusalem'
        WHEN 'Jamaica' THEN 'America/Jamaica'
        WHEN 'Japan' THEN 'Asia/Tokyo'
        WHEN 'Kwajalein' THEN 'Pacific/Kwajalein'
        WHEN 'Libya' THEN 'Africa/Tripoli'
        WHEN 'MET' THEN 'Europe/Brussels'
        WHEN 'Mexico/BajaNorte' THEN 'America/Tijuana'
        WHEN 'Mexico/BajaSur' THEN 'America/Mazatlan'
        WHEN 'Mexico/General' THEN 'America/Mexico_City'
        WHEN 'MST' THEN 'America/Phoenix'
        WHEN 'MST7MDT' THEN 'America/Denver'
        WHEN 'Navajo' THEN 'America/Denver'
        WHEN 'NZ' THEN 'Pacific/Auckland'
        WHEN 'NZ-CHAT' THEN 'Pacific/Chatham'
        WHEN 'Pacific/Chuuk' THEN 'Pacific/Port_Moresby'
        WHEN 'Pacific/Enderbury' THEN 'Pacific/Kanton'
        WHEN 'Pacific/Funafuti' THEN 'Pacific/Tarawa'
        WHEN 'Pacific/Johnston' THEN 'Pacific/Honolulu'
        WHEN 'Pacific/Majuro' THEN 'Pacific/Tarawa'
        WHEN 'Pacific/Midway' THEN 'Pacific/Pago_Pago'
        WHEN 'Pacific/Pohnpei' THEN 'Pacific/Guadalcanal'
        WHEN 'Pacific/Ponape' THEN 'Pacific/Guadalcanal'
        WHEN 'Pacific/Saipan' THEN 'Pacific/Guam'
        WHEN 'Pacific/Samoa' THEN 'Pacific/Pago_Pago'
        WHEN 'Pacific/Truk' THEN 'Pacific/Port_Moresby'
        WHEN 'Pacific/Wake' THEN 'Pacific/Tarawa'
        WHEN 'Pacific/Wallis' THEN 'Pacific/Tarawa'
        WHEN 'Pacific/Yap' THEN 'Pacific/Port_Moresby'
        WHEN 'Poland' THEN 'Europe/Warsaw'
        WHEN 'Portugal' THEN 'Europe/Lisbon'
        WHEN 'PRC' THEN 'Asia/Shanghai'
        WHEN 'PST8PDT' THEN 'America/Los_Angeles'
        WHEN 'ROC' THEN 'Asia/Taipei'
        WHEN 'ROK' THEN 'Asia/Seoul'
        WHEN 'Singapore' THEN 'Asia/Singapore'
        WHEN 'Turkey' THEN 'Europe/Istanbul'
        WHEN 'UCT' THEN 'Etc/UTC'
        WHEN 'Universal' THEN 'Etc/UTC'
        WHEN 'US/Alaska' THEN 'America/Anchorage'
        WHEN 'US/Aleutian' THEN 'America/Adak'
        WHEN 'US/Arizona' THEN 'America/Phoenix'
        WHEN 'US/Central' THEN 'America/Chicago'
        WHEN 'US/East-Indiana' THEN 'America/Indiana/Indianapolis'
        WHEN 'US/Eastern' THEN 'America/New_York'
        WHEN 'US/Hawaii' THEN 'Pacific/Honolulu'
        WHEN 'US/Indiana-Starke' THEN 'America/Indiana/Knox'
        WHEN 'US/Michigan' THEN 'America/Detroit'
        WHEN 'US/Mountain' THEN 'America/Denver'
        WHEN 'US/Pacific' THEN 'America/Los_Angeles'
        WHEN 'US/Samoa' THEN 'Pacific/Pago_Pago'
        WHEN 'UTC' THEN 'Etc/UTC'
        WHEN 'W-SU' THEN 'Europe/Moscow'
        WHEN 'WET' THEN 'Europe/Lisbon'
        WHEN 'Zulu' THEN 'Etc/UTC'
        ELSE zone END $$;

-- FUNCTION: stewardship_token_activation_v1()
CREATE FUNCTION public.stewardship_token_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE generation stewardship_family_token_generation%ROWTYPE;
    population stewardship_campaign_credentials%ROWTYPE;
    deployment stewardship_credential_deployment%ROWTYPE;
    current_key_digest text;
BEGIN
    IF NEW.active_token_generation_id IS NULL OR NEW.active_token_generation_id IS NOT DISTINCT FROM OLD.active_token_generation_id THEN RETURN NEW; END IF;
    IF NOT pg_try_advisory_xact_lock_shared(736226,1) THEN
        RAISE EXCEPTION 'Token key rotation is busy; retry' USING ERRCODE='23514'; END IF;
    SELECT * INTO deployment FROM stewardship_credential_deployment FOR SHARE;
    SELECT * INTO generation FROM stewardship_family_token_generation WHERE id=NEW.active_token_generation_id FOR UPDATE;
    SELECT * INTO population FROM stewardship_campaign_credentials WHERE campaign_id=NEW.id FOR UPDATE;
    SELECT inventory_digest INTO current_key_digest FROM stewardship_credential_key_state WHERE kind='token_public';
    IF generation.id IS NULL OR population.id IS NULL OR deployment.id IS NULL
       OR generation.campaign_id<>NEW.id OR generation.state<>'ready'
       OR generation.credential_epoch IS DISTINCT FROM deployment.family_link_epoch
       OR generation.key_inventory_digest IS DISTINCT FROM current_key_digest
       OR population.population_dirty OR population.rehearsal_epoch_id IS NOT NULL
       OR generation.coverage_digest IS DISTINCT FROM population.eligibility_digest
       OR generation.coverage_count IS DISTINCT FROM population.eligible_count
       OR generation.source_snapshot_id IS DISTINCT FROM population.source_snapshot_id
       OR generation.source_generation IS DISTINCT FROM population.source_generation
       OR EXISTS(SELECT 1 FROM stewardship_rehearsal_credential c
                 JOIN stewardship_rehearsal_epoch e ON e.id=c.epoch_id WHERE e.campaign_id=NEW.id)
       OR EXISTS(SELECT 1 FROM stewardship_submission WHERE campaign_id=NEW.id AND mode='test')
       -- A scalar PK lookup keeps this a baseline-driven existence probe. A
       -- join may instead scan every Family when cleanup left stale statistics.
       OR EXISTS(SELECT 1 FROM stewardship_family_form_baseline baseline
                 WHERE baseline.mode='test' AND (SELECT family.campaign_id
                     FROM stewardship_family_campaign family WHERE family.id=baseline.family_id)=NEW.id)
       OR EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot snapshot
                 JOIN stewardship_daily_digest_preparation p ON p.id=snapshot.preparation_id
                 WHERE snapshot.campaign_id=NEW.id AND p.mode='testing')
       OR EXISTS(SELECT 1 FROM stewardship_weekly_digest_snapshot snapshot
                 JOIN stewardship_weekly_digest_preparation p ON p.id=snapshot.preparation_id
                 WHERE snapshot.campaign_id=NEW.id AND p.mode='testing') THEN
        RAISE EXCEPTION 'Campaign requires a complete current token generation and rehearsal cleanup' USING ERRCODE='23514'; END IF;
    IF generation.configuration_request_id IS NULL THEN
        IF generation.configuration_id IS DISTINCT FROM NEW.active_configuration_id THEN
            RAISE EXCEPTION 'Prepared token configuration changed' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(
        SELECT 1 FROM stewardship_campaign_config_intent i
        JOIN stewardship_config_request r ON r.id=i.request_id
        JOIN stewardship_campaign_configuration p ON p.configuration_id=r.candidate_version_id AND p.record_id=NEW.id
        WHERE i.request_id=generation.configuration_request_id AND i.campaign_id=NEW.id
          AND i.action='reopen' AND i.prior_projection_id=generation.configuration_id
          AND i.token_generation_id=generation.id AND p.id=NEW.active_configuration_id
    ) THEN
        RAISE EXCEPTION 'Prepared tokens do not match the reviewed reopen candidate' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_token_campaign_effects_v1()
CREATE FUNCTION public.stewardship_token_campaign_effects_v1() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF NEW.active_token_generation_id IS DISTINCT FROM OLD.active_token_generation_id AND NEW.active_token_generation_id IS NOT NULL THEN
        UPDATE stewardship_family_token_generation SET state='active',version=version+1
        WHERE id=NEW.active_token_generation_id AND state='ready';
    END IF;
    IF (NEW.state='closed' AND OLD.state<>'closed') OR (NEW.state='draft' AND OLD.state='scheduled') THEN
        UPDATE stewardship_family_token SET ciphertext=NULL,digest=NULL,destroyed_at=stewardship_campaign_now_v1(),version=version+1
        WHERE campaign_id=NEW.id AND destroyed_at IS NULL;
        UPDATE stewardship_family_token_generation SET state='superseded',version=version+1
        WHERE campaign_id=NEW.id AND state NOT IN ('superseded','cancelled');
        UPDATE stewardship_family_session SET revoked_at=greatest(stewardship_campaign_now_v1(),last_activity_at),version=version+1
        WHERE family_id IN(SELECT id FROM stewardship_family_campaign WHERE campaign_id=NEW.id) AND revoked_at IS NULL;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_token_gate_release_v1()
CREATE FUNCTION public.stewardship_token_gate_release_v1() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF NEW.active_token_generation_id IS NOT NULL AND NEW.active_token_generation_id IS DISTINCT FROM OLD.active_token_generation_id THEN
        UPDATE stewardship_campaign_credentials SET go_live_gate=false,version=version+1
        WHERE campaign_id=NEW.id AND go_live_gate;
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_token_generation_state_v1()
CREATE FUNCTION public.stewardship_token_generation_state_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE population stewardship_campaign_credentials%ROWTYPE; current_epoch uuid; actual_count bigint;
BEGIN
    SELECT family_link_epoch INTO current_epoch FROM stewardship_credential_deployment;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'building' OR NEW.credential_epoch IS DISTINCT FROM current_epoch THEN
            RAISE EXCEPTION 'A token generation starts inactive in the current epoch' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF (OLD.state IN ('cancelled','superseded') AND NEW.state<>OLD.state)
       OR (OLD.state='active' AND NEW.state NOT IN ('active','superseded'))
       OR (OLD.state='ready' AND NEW.state NOT IN ('ready','active','cancelled','superseded'))
       OR (OLD.completed_at IS NOT NULL AND (NEW.completed_at,NEW.coverage_digest,NEW.coverage_count)
           IS DISTINCT FROM (OLD.completed_at,OLD.coverage_digest,OLD.coverage_count)) THEN
        RAISE EXCEPTION 'Token generation cannot revive or rewrite a ready manifest' USING ERRCODE='23514'; END IF;
    IF NEW.state='ready' AND OLD.state<>'ready' THEN
        SELECT * INTO population FROM stewardship_campaign_credentials WHERE campaign_id=NEW.campaign_id FOR UPDATE;
        SELECT count(*) INTO actual_count FROM stewardship_family_token WHERE generation_id=NEW.id AND destroyed_at IS NULL;
        IF NEW.credential_epoch IS DISTINCT FROM current_epoch OR population.population_dirty
           OR NEW.source_snapshot_id IS DISTINCT FROM population.source_snapshot_id OR NEW.source_generation IS DISTINCT FROM population.source_generation
           OR NEW.coverage_digest IS DISTINCT FROM population.eligibility_digest OR NEW.coverage_count IS DISTINCT FROM population.eligible_count
           OR actual_count<>population.eligible_count OR EXISTS(
               SELECT 1 FROM stewardship_family_token t JOIN stewardship_family_campaign f ON f.id=t.family_id
               WHERE t.generation_id=NEW.id AND (NOT f.portal_eligible OR t.destroyed_at IS NOT NULL)) THEN
            RAISE EXCEPTION 'Token readiness requires complete current coverage' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='active' AND NOT EXISTS(SELECT 1 FROM stewardship_campaign WHERE id=NEW.campaign_id AND active_token_generation_id=NEW.id) THEN
        RAISE EXCEPTION 'Only the campaign pointer activates a token generation' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_token_issuance_v1()
CREATE FUNCTION public.stewardship_token_issuance_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE generation stewardship_family_token_generation%ROWTYPE;
    current_epoch uuid;
BEGIN
    SELECT family_link_epoch INTO current_epoch
        FROM stewardship_credential_deployment FOR SHARE;
    SELECT * INTO generation FROM stewardship_family_token_generation
        WHERE id=NEW.generation_id FOR SHARE;
    IF generation.id IS NULL OR generation.state NOT IN('building','active')
       OR generation.credential_epoch IS DISTINCT FROM current_epoch
       OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign
                     WHERE id=NEW.family_id AND portal_eligible FOR SHARE) THEN
        RAISE EXCEPTION 'Token issuance requires a current eligible generation'
            USING ERRCODE='23514';
    END IF;
    IF generation.state='active' AND NOT EXISTS(
        SELECT 1 FROM stewardship_campaign c
        JOIN stewardship_system_configuration s ON s.current_campaign_id=c.id
        WHERE c.id=generation.campaign_id AND c.active_token_generation_id=generation.id
          AND c.state IN('scheduled','active') AND s.mode='production'
          AND NOT s.restore_review_required
        FOR SHARE OF c,s
    ) THEN
        RAISE EXCEPTION 'Live token issuance is not admitted'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_token_request_binding_v1()
CREATE FUNCTION public.stewardship_token_request_binding_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ BEGIN
    IF NEW.configuration_request_id IS DISTINCT FROM OLD.configuration_request_id THEN
        RAISE EXCEPTION 'Token proposed configuration is immutable' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- FUNCTION: stewardship_valid_sealed_candidate_v1(text)
CREATE FUNCTION public.stewardship_valid_sealed_candidate_v1(value text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE j jsonb;
BEGIN
    IF value IS NULL OR octet_length(value)>262144 THEN RETURN false; END IF;
    BEGIN j:=value::jsonb; EXCEPTION WHEN OTHERS THEN RETURN false; END;
    RETURN jsonb_typeof(j)='object' AND j ?& ARRAY['v','alg','kid','body']
        AND j-ARRAY['v','alg','kid','body']='{}'::jsonb AND j->'v'='1'::jsonb
        AND j->>'alg'='sealedbox-v1' AND j->>'kid' ~ '^[A-Za-z0-9_-]{1,48}$'
        AND j->>'body' ~ '^[A-Za-z0-9_-]+$';
END $_$;

-- FUNCTION: stewardship_work_gate_v1()
CREATE FUNCTION public.stewardship_work_gate_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE c stewardship_campaign%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Work gate history cannot be deleted' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    IF NEW.actor_id IS NULL OR r.current_campaign_id IS NOT NULL OR r.mode<>'testing' OR r.restore_review_required THEN
        RAISE EXCEPTION 'Work gate requires the exclusive maintenance window' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'preparing' OR NEW.version<>1 OR c.state<>'archived' OR NEW.initiated_by_id IS DISTINCT FROM NEW.actor_id
           OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
            RAISE EXCEPTION 'Only one global purge preparation is admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF NOT ((OLD.state='preparing' AND NEW.state='released' AND c.state='archived')
            OR (OLD.state='preparing' AND NEW.state='running' AND c.state='purging')
            OR (OLD.state='running' AND NEW.state='tombstone' AND c.state='purged')) THEN
            RAISE EXCEPTION 'Invalid work gate transition' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;
-- A cancelled aggregate remains immutable; only this forward lineage may carry
-- its original coalesced obligations to another ordinary scheduled occurrence.
CREATE FUNCTION public.stewardship_recovery_replacement_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE previous public.stewardship_schedule_occurrence%ROWTYPE;
        replacement public.stewardship_schedule_occurrence%ROWTYPE;
        demand public.stewardship_activation_catchup%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO demand FROM public.stewardship_activation_catchup WHERE id=NEW.demand_id;
    SELECT * INTO previous FROM public.stewardship_schedule_occurrence WHERE id=NEW.previous_id FOR UPDATE;
    SELECT * INTO replacement FROM public.stewardship_schedule_occurrence WHERE id=NEW.replacement_id FOR UPDATE;
    IF NEW.preparation_id IS NOT NULL THEN
        IF NEW.demand_id IS NOT NULL OR NOT (
            public.stewardship_daily_digest_replacement_v1(to_jsonb(NEW))
            OR public.stewardship_weekly_digest_replacement_v1(to_jsonb(NEW))) THEN
            RAISE EXCEPTION 'Recovery replacement requires current digest ownership' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF demand.id IS NULL OR demand.completed_at IS NOT NULL
        OR previous.id IS NULL OR replacement.id IS NULL
        OR previous.revision_id=replacement.revision_id
        OR previous.mode<>'production' OR replacement.mode<>'production'
        OR previous.target<>replacement.target
        OR previous.state<>'skipped' OR previous.reason NOT IN ('schedule_replaced','schedule_removed')
        OR replacement.state<>'pending' OR replacement.task_id IS NOT NULL
        OR replacement.outbox_id IS NOT NULL
        OR public.stewardship_schedule_slot_excluded_v1(
            replacement.definition_id,replacement.mode,replacement.target,replacement.slot)
        OR NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition s
            JOIN public.stewardship_schedule_definition old_s ON old_s.id=previous.definition_id
            WHERE s.id=replacement.definition_id AND s.campaign_id=demand.campaign_id
                AND old_s.campaign_id=s.campaign_id AND s.current_revision_id=replacement.revision_id
                AND ((previous.target='admins' AND s.kind IN ('daily_digest','weekly_digest')
                        AND previous.definition_id=s.id AND previous.reason='schedule_replaced')
                    OR (starts_with(previous.target,'family:') AND s.kind IN ('initial','reminder')
                        AND old_s.kind IN ('initial','reminder'))))
        OR NOT (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                       WHERE f.occurrence_id=previous.id AND f.disposition='coalesced')
                OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement link
                          WHERE link.replacement_id=previous.id AND link.demand_id=demand.id))
        OR NOT public.stewardship_catchup_write_admitted_v1(
            'stewardship_schedule_occurrence',
            to_jsonb(replacement)||jsonb_build_object('actor_id',NEW.actor_id,'correlation_id',NEW.correlation_id),NULL)
    THEN RAISE EXCEPTION 'Recovery replacement requires current owned cancelled coverage'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
