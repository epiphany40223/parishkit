-- Only the compiled current-readiness web owner creates this immutable intent.
-- Its private effect repeats scope/version/session/manifest guards and commits
-- lifecycle state plus the existing catch-up demand/task protocol atomically.
CREATE TABLE public.stewardship_production_confirmation (
    id uuid NOT NULL PRIMARY KEY,
    created_at timestamptz DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    request_id uuid NOT NULL UNIQUE,
    preparation_id uuid NOT NULL UNIQUE,
    activation_id uuid NOT NULL UNIQUE,
    generation_id uuid NOT NULL UNIQUE,
    request_key uuid NOT NULL UNIQUE,
    session_id uuid NOT NULL,
    authenticated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    preview_at timestamptz NOT NULL,
    preview_counts jsonb NOT NULL,
    expected_request_version bigint NOT NULL CHECK(expected_request_version>=0),
    expected_campaign_version bigint NOT NULL CHECK(expected_campaign_version>=0),
    expected_runtime_version bigint NOT NULL CHECK(expected_runtime_version>=0),
    impact_revision bigint NOT NULL CHECK(impact_revision>=0),
    readiness_digest varchar(64) NOT NULL,
    target_state varchar(9) NOT NULL,
    CONSTRAINT production_confirmation_versions CHECK(expected_request_version>=1
        AND expected_campaign_version>=1 AND expected_runtime_version>=1 AND impact_revision>=1),
    CONSTRAINT production_confirmation_digest CHECK(readiness_digest::text ~ '^[0-9a-f]{64}$'),
    CONSTRAINT production_confirmation_target CHECK(target_state::text = ANY(
        ARRAY[('scheduled'::varchar)::text,('active'::varchar)::text]))
);
ALTER TABLE "stewardship_production_confirmation" ADD CONSTRAINT "stewardship_producti_request_id_6d8960f2_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_production_request" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_production_confirmation" ADD CONSTRAINT "stewardship_producti_preparation_id_dec93d84_fk_stewardsh" FOREIGN KEY ("preparation_id") REFERENCES "stewardship_production_tokens" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_production_confirmation" ADD CONSTRAINT "stewardship_producti_activation_id_eae020d4_fk_stewardsh" FOREIGN KEY ("activation_id") REFERENCES "stewardship_campaign_transition" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_production_confirmation" ADD CONSTRAINT "stewardship_producti_generation_id_0fac5c25_fk_stewardsh" FOREIGN KEY ("generation_id") REFERENCES "stewardship_family_token_generation" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_production_confirmation_correlation_id_2fdd4d7c" ON "stewardship_production_confirmation" ("correlation_id");

CREATE FUNCTION public.stewardship_production_confirmation_guard_v1() RETURNS trigger
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
              AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              AND login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp())
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_production_event event
            WHERE event.request_id=request.id AND event.action='complete'
              AND event.created_at<=NEW.authenticated_at) THEN
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
REVOKE ALL ON FUNCTION public.stewardship_production_confirmation_guard_v1() FROM PUBLIC;
CREATE TRIGGER production_confirmation_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_production_confirmation FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_production_confirmation_guard_v1();

CREATE FUNCTION public.stewardship_production_confirmation_effect_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request public.stewardship_production_request%ROWTYPE;
    demand public.stewardship_activation_catchup%ROWTYPE; task_uuid uuid;
BEGIN
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id;
    INSERT INTO public.stewardship_campaign_transition(id,campaign_id,request_id,action,
        expected_version,expected_runtime_version,before_state,after_state,before_mode,after_mode,
        configuration_id,token_generation_id,reason,actor_id,correlation_id)
    VALUES(NEW.activation_id,request.campaign_id,NEW.request_key,'activate',
        NEW.expected_campaign_version,NEW.expected_runtime_version,'draft',NEW.target_state,'testing','production',
        request.configuration_id,NEW.generation_id,'',NEW.actor_id,NEW.correlation_id);
    -- Match the existing allocate_activation contract: one opaque root, one
    -- demand, pinned source, no Family/occurrence/message enumeration here.
    IF NEW.target_state='active' THEN
        SELECT * INTO STRICT demand FROM public.stewardship_activation_catchup WHERE activation_id=NEW.activation_id;
        task_uuid:=gen_random_uuid();
        INSERT INTO public.stewardship_task_run(id,root_id,version,retry_sequence,task_type,
            idempotency_key,domain_request_id,initiated_by_id,actor_id,correlation_id)
        VALUES(task_uuid,task_uuid,1,0,'activation_catchup',demand.id::text,demand.id,
            NEW.actor_id,NEW.actor_id,NEW.correlation_id);
        UPDATE public.stewardship_activation_catchup SET task_root_id=task_uuid,
            source_snapshot_id=(SELECT source_snapshot_id FROM public.stewardship_production_tokens WHERE id=NEW.preparation_id),
            version=version+1 WHERE id=demand.id;
    END IF;
    UPDATE public.stewardship_production_request SET state='activated',action='activate',
        command_id=NEW.request_key,version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,
        activated_at=public.stewardship_campaign_now_v1(),activation_digest=NEW.readiness_digest
        WHERE id=NEW.request_id;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_confirmation_effect_v1() FROM PUBLIC;
CREATE TRIGGER production_confirmation_effect AFTER INSERT ON public.stewardship_production_confirmation
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_confirmation_effect_v1();
