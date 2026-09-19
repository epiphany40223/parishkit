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
-- This read-only invoker predicate uses only the caller's existing metadata
-- grants. Unlike the trigger-only definers below, it confers no extra authority.

CREATE FUNCTION public.stewardship_production_tokens_available_v1(transition_uuid uuid, excluded_uuid uuid)
RETURNS boolean LANGUAGE sql VOLATILE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM stewardship_production_tokens preparation
        WHERE preparation.transition_id=transition_uuid
          AND preparation.id IS DISTINCT FROM excluded_uuid
          AND (EXISTS(SELECT 1 FROM stewardship_task_run task
                WHERE task.domain_request_id=preparation.id AND task.task_type='production_tokens'
                    AND task.state IN ('queued','running','retry_wait','abandoned'))
            OR EXISTS(SELECT 1 FROM stewardship_family_token_generation generation
                WHERE generation.operation_id=preparation.id AND (
                    generation.state IN ('building','ready','active') OR EXISTS(
                        SELECT 1 FROM stewardship_family_token token
                        WHERE token.generation_id=generation.id AND token.destroyed_at IS NULL))))
    );
$$;

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
CREATE TRIGGER production_tokens_intake BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_production_tokens
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_tokens_intake_v1();
CREATE TRIGGER production_tokens_intake BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_production_token_cancel
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_tokens_intake_v1();

CREATE FUNCTION public.stewardship_production_tokens_task_pin_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.task_type NOT IN ('production_tokens','production_token_cleanup') THEN RETURN NULL; END IF;
    IF NEW.parent_id IS NOT NULL
       AND NOT pg_has_role(session_user,(SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        -- A retry has already locked its root. Acquiring work order here would
        -- invert lifecycle ordering; the caller must have acquired it first.
        IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Production link retry requires prior work ordering' USING ERRCODE='42501';
        END IF;
        IF session_user<>'pk_stewardship_web'
           OR public.stewardship_export_authorized_v1(NEW.initiated_by_id,true) IS NOT TRUE THEN
            RAISE EXCEPTION 'Production link retry requires a current Admin' USING ERRCODE='42501';
        END IF;
        IF NEW.task_type='production_tokens' AND NOT EXISTS(
            SELECT 1 FROM stewardship_production_tokens p WHERE p.id=NEW.domain_request_id
                AND p.task_id=NEW.root_id AND public.stewardship_production_tokens_current_v1(p)
                AND public.stewardship_production_tokens_available_v1(p.transition_id,p.id)) THEN
            RAISE EXCEPTION 'Production link retry inputs changed' USING ERRCODE='23514';
        END IF;
    END IF;
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

CREATE FUNCTION public.stewardship_production_token_write_v1(relation_name text, proposed jsonb, prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE preparation public.stewardship_production_tokens%ROWTYPE;
        generation public.stewardship_family_token_generation%ROWTYPE;
        can_prepare boolean; can_dispose boolean; preparation_claim boolean;
        claim jsonb;
BEGIN
    IF current_user<>'pk_stewardship_worker'
       OR relation_name NOT IN ('stewardship_family_token_generation','stewardship_family_token')
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
        RETURN false;
    END IF;
    claim:=NULLIF(current_setting('parishkit.production_token_claim',true),'')::jsonb;
    IF claim IS NULL OR jsonb_typeof(claim)<>'object'
       OR NOT claim ?& ARRAY['run','fence','worker'] THEN RETURN false; END IF;
    IF relation_name='stewardship_family_token_generation' THEN
        SELECT * INTO generation FROM jsonb_populate_record(NULL::public.stewardship_family_token_generation,proposed);
    ELSE
        SELECT * INTO generation FROM stewardship_family_token_generation
            WHERE id=(proposed->>'generation_id')::uuid;
    END IF;
    SELECT * INTO preparation FROM stewardship_production_tokens
        WHERE id=generation.operation_id AND task_id=generation.task_id;
    IF preparation.id IS NULL OR generation.campaign_id IS DISTINCT FROM
        (SELECT campaign_id FROM stewardship_production_request WHERE id=preparation.transition_id)
       OR EXISTS(SELECT 1 FROM stewardship_system_configuration WHERE restore_review_required)
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running')) THEN
        RETURN false;
    END IF;
    preparation_claim:=EXISTS(SELECT 1 FROM stewardship_task_run task
        WHERE task.root_id=preparation.task_id AND task.domain_request_id=preparation.id
            AND task.task_type='production_tokens' AND task.state='running'
            AND task.id=(claim->>'run')::uuid AND task.fence=(claim->>'fence')::bigint
            AND task.worker_id=(claim->>'worker')::uuid
            AND task.lease_expires_at>clock_timestamp());
    can_prepare:=public.stewardship_production_tokens_current_v1(preparation);
    can_dispose:=NOT can_prepare AND (preparation_claim OR EXISTS(
        SELECT 1 FROM stewardship_production_token_cancel cancellation
        JOIN stewardship_task_run task ON task.root_id=cancellation.task_id
            AND task.domain_request_id=cancellation.id AND task.task_type='production_token_cleanup'
        WHERE cancellation.preparation_id=preparation.id AND task.state='running'
            AND task.id=(claim->>'run')::uuid AND task.fence=(claim->>'fence')::bigint
            AND task.worker_id=(claim->>'worker')::uuid
            AND task.lease_expires_at>clock_timestamp()
    )) AND generation.state<>'active' AND NOT EXISTS(SELECT 1 FROM stewardship_campaign
        WHERE active_token_generation_id=generation.id);
    IF relation_name='stewardship_family_token_generation' THEN
        IF prior IS NULL THEN
            RETURN preparation_claim AND can_prepare
                AND generation.state='building' AND generation.checkpoint=0
                AND generation.coverage_count=0 AND generation.coverage_digest=''
                AND generation.completed_at IS NULL AND generation.configuration_request_id IS NULL
                AND generation.configuration_id=preparation.configuration_id
                AND generation.source_snapshot_id=preparation.source_snapshot_id
                AND generation.source_generation=preparation.source_generation
                AND generation.credential_epoch=preparation.credential_epoch
                AND generation.key_inventory_digest=preparation.key_inventory_digest
                AND generation.actor_id=preparation.actor_id
                AND generation.restore_id IS NOT DISTINCT FROM
                    (SELECT restore_id FROM stewardship_credential_deployment)
                AND EXISTS(SELECT 1 FROM stewardship_credential_key_state keys,
                    LATERAL jsonb_array_elements(keys.inventory) key
                    WHERE keys.kind='token_public' AND keys.inventory_digest=preparation.key_inventory_digest
                        AND key->>'id'=generation.key_id AND key->>'usage'='active');
        END IF;
        IF can_dispose THEN
            RETURN prior->>'state' IN ('building','ready','failed') AND generation.state='cancelled'
                AND proposed-ARRAY['state','version','updated_at']=prior-ARRAY['state','version','updated_at'];
        END IF;
        RETURN preparation_claim AND can_prepare
            AND prior->>'state' IN ('building','ready') AND generation.state IN ('building','ready')
            AND proposed-ARRAY['state','checkpoint','coverage_count','coverage_digest','completed_at','version','updated_at']
                =prior-ARRAY['state','checkpoint','coverage_count','coverage_digest','completed_at','version','updated_at'];
    END IF;
    IF prior IS NULL THEN
        RETURN preparation_claim AND can_prepare AND generation.state='building'
            AND (proposed->>'campaign_id')::uuid=generation.campaign_id
            AND proposed->>'destroyed_at' IS NULL AND proposed->>'rotated_at' IS NULL
            AND public.stewardship_valid_sealed_candidate_v1(proposed->>'ciphertext')
            AND (proposed->>'ciphertext')::jsonb->>'kid'=generation.key_id
            AND proposed->>'digest' ~ '^[0-9a-f]{64}$';
    END IF;
    RETURN can_dispose AND generation.state IN ('cancelled','failed','superseded')
        AND prior->>'destroyed_at' IS NULL AND proposed->>'ciphertext' IS NULL
        AND proposed->>'digest' IS NULL
        AND (proposed->>'destroyed_at')::timestamptz=public.stewardship_campaign_now_v1()
        AND proposed-ARRAY['ciphertext','digest','destroyed_at','version','updated_at']
            =prior-ARRAY['ciphertext','digest','destroyed_at','version','updated_at'];
END $$;

CREATE FUNCTION public.stewardship_production_token_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME='stewardship_family_token' THEN
        IF EXISTS(SELECT 1 FROM stewardship_family_token_generation
            WHERE id=NEW.generation_id AND state='active') THEN
            -- Existing source-promotion issuance retains its current-live guard.
            RETURN NEW;
        END IF;
    END IF;
    IF NOT public.stewardship_production_token_write_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) THEN
        RAISE EXCEPTION 'Inactive links require their current preparation owner' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_token_insert_v1() FROM PUBLIC;
CREATE TRIGGER aa_production_token_insert BEFORE INSERT ON stewardship_family_token_generation
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_token_insert_v1();
CREATE TRIGGER aa_production_token_insert BEFORE INSERT ON stewardship_family_token
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_token_insert_v1();

CREATE FUNCTION public.stewardship_production_tokens_terminal_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE preparation public.stewardship_production_tokens%ROWTYPE;
        generation public.stewardship_family_token_generation%ROWTYPE;
        clean boolean;
BEGIN
    IF NEW.task_type NOT IN ('production_tokens','production_token_cleanup')
       OR NEW.action NOT IN ('complete','recovery_complete','safe_cancel','recovery_cancel') THEN
        RETURN NEW;
    END IF;
    IF NEW.task_type='production_tokens' THEN
        SELECT * INTO preparation FROM stewardship_production_tokens
            WHERE id=NEW.domain_request_id AND task_id=NEW.root_id;
    ELSE
        SELECT p.* INTO preparation FROM stewardship_production_token_cancel c
            JOIN stewardship_production_tokens p ON p.id=c.preparation_id
            WHERE c.id=NEW.domain_request_id AND c.task_id=NEW.root_id;
    END IF;
    IF preparation.id IS NULL THEN
        RAISE EXCEPTION 'Production link completion has no intent' USING ERRCODE='23514';
    END IF;
    SELECT * INTO generation FROM stewardship_family_token_generation
        WHERE operation_id=preparation.id AND task_id=preparation.task_id;
    clean:=generation.id IS NULL OR (generation.state IN ('cancelled','failed','superseded')
        AND NOT EXISTS(SELECT 1 FROM stewardship_family_token
            WHERE generation_id=generation.id AND destroyed_at IS NULL));
    IF NEW.task_type='production_token_cleanup' THEN
        IF clean AND NEW.action IN ('complete','recovery_complete') THEN RETURN NEW; END IF;
    ELSIF NEW.action IN ('safe_cancel','recovery_cancel') THEN
        IF clean AND NOT public.stewardship_production_tokens_current_v1(preparation) THEN RETURN NEW; END IF;
    ELSIF generation.state='ready' AND public.stewardship_production_tokens_current_v1(preparation)
        AND generation.configuration_id=preparation.configuration_id
        AND generation.source_snapshot_id=preparation.source_snapshot_id
        AND generation.source_generation=preparation.source_generation
        AND generation.credential_epoch=preparation.credential_epoch
        AND generation.key_inventory_digest=preparation.key_inventory_digest
        AND generation.coverage_digest=preparation.eligibility_digest
        AND generation.coverage_count=preparation.eligible_count
        AND generation.completed_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Production link task lacks its terminal domain proof' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_tokens_terminal_v1() FROM PUBLIC;
CREATE TRIGGER production_tokens_terminal BEFORE UPDATE ON stewardship_task_run
FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_tokens_terminal_v1();
