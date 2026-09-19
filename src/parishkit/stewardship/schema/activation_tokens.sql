-- Normal go-live prepares inactive links before its final, short transaction.
-- These append-only intents do not grant campaign or runtime mutation authority.
CREATE TABLE "stewardship_production_tokens" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "transition_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "request_key" uuid NOT NULL, "configuration_id" uuid NOT NULL, "source_snapshot_id" uuid NOT NULL, "source_generation" bigint NOT NULL CHECK ("source_generation" >= 0), "credential_epoch" uuid NOT NULL, "key_inventory_digest" varchar(64) NOT NULL, "eligibility_digest" varchar(64) NOT NULL, "eligible_count" bigint NOT NULL CHECK ("eligible_count" >= 0), CONSTRAINT "production_tokens_request_key" UNIQUE ("transition_id", "request_key"), CONSTRAINT "production_tokens_source_generation" CHECK ("source_generation" >= 1), CONSTRAINT "production_tokens_digests" CHECK (("key_inventory_digest"::text ~ '^[0-9a-f]{64}$' AND "eligibility_digest"::text ~ '^[0-9a-f]{64}$')));
CREATE TABLE "stewardship_production_token_cancel" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "preparation_id" uuid NOT NULL UNIQUE, "task_id" uuid NOT NULL UNIQUE, "request_key" uuid NOT NULL UNIQUE);
ALTER TABLE "stewardship_production_tokens" ADD CONSTRAINT "stewardship_producti_transition_id_143fb3d6_fk_stewardsh" FOREIGN KEY ("transition_id") REFERENCES "stewardship_production_request" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_production_tokens" ADD CONSTRAINT "stewardship_producti_task_id_9f5f6c99_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_production_tokens" ADD CONSTRAINT "stewardship_producti_configuration_id_56ac7180_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_campaign_configuration" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_production_tokens_correlation_id_6c07111e" ON "stewardship_production_tokens" ("correlation_id");
CREATE INDEX "stewardship_production_tokens_transition_id_143fb3d6" ON "stewardship_production_tokens" ("transition_id");
CREATE INDEX "stewardship_production_tokens_configuration_id_56ac7180" ON "stewardship_production_tokens" ("configuration_id");
ALTER TABLE "stewardship_production_token_cancel" ADD CONSTRAINT "stewardship_producti_preparation_id_0513c706_fk_stewardsh" FOREIGN KEY ("preparation_id") REFERENCES "stewardship_production_tokens" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_production_token_cancel" ADD CONSTRAINT "stewardship_producti_task_id_4133a2d7_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_production_token_cancel_correlation_id_df34f04b" ON "stewardship_production_token_cancel" ("correlation_id");

CREATE FUNCTION public.stewardship_production_tokens_current_v1(value public.stewardship_production_tokens)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM stewardship_production_request request
        JOIN stewardship_production_manifest manifest ON manifest.request_id=request.id
        JOIN stewardship_campaign campaign ON campaign.id=request.campaign_id
        JOIN stewardship_campaign_configuration configuration ON configuration.id=campaign.active_configuration_id
        JOIN stewardship_system_configuration runtime ON runtime.current_campaign_id=campaign.id
        JOIN stewardship_campaign_credentials population ON population.campaign_id=campaign.id
        CROSS JOIN stewardship_source_current source
        CROSS JOIN stewardship_credential_deployment epoch
        JOIN stewardship_credential_key_state keys ON keys.kind='token_public'
        WHERE request.id=value.transition_id AND request.state='cleanup_complete'
          AND request.processed_count=request.inventory_total
          AND request.configuration_id=runtime.active_configuration_id
          AND configuration.id=value.configuration_id
          AND runtime.mode='testing' AND NOT runtime.restore_review_required
          AND campaign.state='draft' AND stewardship_campaign_now_v1()<configuration.ends_at
          AND population.go_live_gate AND population.version>=request.gate_version
          AND population.rehearsal_epoch_id IS NULL AND NOT population.population_dirty
          AND population.source_snapshot_id=value.source_snapshot_id
          AND population.source_generation=value.source_generation
          AND source.snapshot_id=value.source_snapshot_id AND source.generation=value.source_generation
          AND population.eligibility_digest=value.eligibility_digest
          AND population.eligible_count=value.eligible_count
          AND epoch.family_link_epoch=value.credential_epoch
          AND keys.inventory_digest=value.key_inventory_digest
          AND NOT EXISTS(SELECT 1 FROM stewardship_production_cancellation WHERE request_id=request.id)
          AND NOT EXISTS(SELECT 1 FROM stewardship_production_token_cancel WHERE preparation_id=value.id)
          AND NOT EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))
    );
$$;
REVOKE ALL ON FUNCTION public.stewardship_production_tokens_current_v1(public.stewardship_production_tokens) FROM PUBLIC;

CREATE FUNCTION public.stewardship_production_tokens_intake_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE kind text; preparation public.stewardship_production_tokens%ROWTYPE;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Production link preparation history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NOT pg_has_role(session_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')
       AND (session_user<>'pk_stewardship_web'
            OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE) THEN
        RAISE EXCEPTION 'Production link intent requires a current Admin' USING ERRCODE='42501';
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
        IF EXISTS(SELECT 1 FROM stewardship_production_tokens prior
            JOIN stewardship_task_run task ON task.domain_request_id=prior.id
            WHERE prior.transition_id=NEW.transition_id AND task.task_type=kind
                AND task.state IN ('queued','running','retry_wait','abandoned')) THEN
            RAISE EXCEPTION 'Production link preparation is already running' USING ERRCODE='23514';
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
CREATE TRIGGER production_tokens_intake BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_production_tokens
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_tokens_intake_v1();
CREATE TRIGGER production_tokens_intake BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_production_token_cancel
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_tokens_intake_v1();

CREATE FUNCTION public.stewardship_production_tokens_task_pin_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.task_type NOT IN ('production_tokens','production_token_cleanup') THEN RETURN NULL; END IF;
    IF NEW.task_type='production_tokens' THEN
        IF EXISTS(SELECT 1 FROM stewardship_production_tokens
            WHERE id=NEW.domain_request_id AND task_id=NEW.root_id) THEN RETURN NULL; END IF;
    ELSE
        IF EXISTS(SELECT 1 FROM stewardship_production_token_cancel
            WHERE id=NEW.domain_request_id AND task_id=NEW.root_id) THEN RETURN NULL; END IF;
    END IF;
    RAISE EXCEPTION 'Production link task requires its committed intent' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_tokens_task_pin_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER production_tokens_task_pin AFTER INSERT ON public.stewardship_task_run
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_tokens_task_pin_v1();
