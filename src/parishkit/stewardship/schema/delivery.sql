-- Internal delivery journal. No runtime role receives a generic mutation grant.
-- Compiled dispatch/cleanup owners must add their narrowly scoped entry points
-- with the later workflow; UUIDs, action names and admission callbacks are not
-- a substitute for those PostgreSQL permission boundaries.

CREATE FUNCTION public.stewardship_outbox_state_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    allowed text[];
    claim public.stewardship_task_run%ROWTYPE;
    key_lock boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('outbox_messages',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Delivery history requires its retention owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.sealed_substitutions IS NOT NULL AND
       (TG_OP='INSERT' OR NEW.sealed_substitutions IS DISTINCT FROM OLD.sealed_substitutions
        OR NEW.sealed_key_id IS DISTINCT FROM OLD.sealed_key_id
        OR NEW.token_generation_id IS DISTINCT FROM OLD.token_generation_id
        OR NEW.credential_epoch_id IS DISTINCT FROM OLD.credential_epoch_id) THEN
        -- Same nonblocking inventory lock as credential_keys.key_set_lock.
        -- No retired writer can race a newly retained outbox dependency.
        SELECT pg_try_advisory_xact_lock_shared(736226,1) INTO key_lock;
        IF NOT key_lock OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_credential_key_state k,
                jsonb_array_elements(k.inventory) entry
            WHERE k.kind='token_public' AND entry->>'id'=NEW.sealed_key_id
              AND entry->>'usage'='active'
        ) THEN
            RAISE EXCEPTION 'Delivery encryption key is not currently admitted'
                USING ERRCODE='23514';
        END IF;
        BEGIN
            IF (NEW.sealed_substitutions::jsonb)->>'kid' IS DISTINCT FROM NEW.sealed_key_id
               OR (NEW.sealed_substitutions::jsonb)->>'alg' IS DISTINCT FROM 'sealedbox-v1' THEN
                RAISE EXCEPTION 'Invalid delivery envelope' USING ERRCODE='23514';
            END IF;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Invalid delivery envelope' USING ERRCODE='23514';
        END;
    END IF;
    -- Campaign Testing work is never an operational-mail exception. Recheck
    -- both allocation and the last local boundary before external submission.
    IF NEW.routing='testing_override' AND (TG_OP='INSERT' OR NEW.action IN
       ('prepared','submit','retry_failed','authorize_resend','retry_unaccepted','retry_idempotent'))
       AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_campaign_credentials c
           JOIN public.stewardship_system_configuration s ON s.current_campaign_id=c.campaign_id
           WHERE c.campaign_id=NEW.campaign_id AND NOT c.go_live_gate AND s.mode='testing'
             AND (NEW.credential_namespace='none' OR EXISTS (
                 SELECT 1 FROM public.stewardship_rehearsal_epoch e
                 WHERE e.id=NEW.rehearsal_epoch_id AND e.id=c.rehearsal_epoch_id
                   AND e.campaign_id=c.campaign_id AND e.state='active'
             ))
       ) THEN
        RAISE EXCEPTION 'Testing delivery is not currently admitted' USING ERRCODE='23514';
    END IF;
    IF NEW.credential_namespace='production' AND NEW.sealed_substitutions IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_family_token_generation g
           JOIN public.stewardship_family_token t ON t.generation_id=g.id
           WHERE g.id=NEW.token_generation_id AND g.campaign_id=NEW.campaign_id
             AND t.family_id=NEW.family_id
             AND g.credential_epoch=NEW.credential_epoch_id
       ) THEN
        RAISE EXCEPTION 'Invalid delivery credential binding' USING ERRCODE='23514';
    END IF;
    SELECT * INTO claim FROM public.stewardship_task_run WHERE id=NEW.task_id;
    IF claim.id IS NULL OR claim.root_id <> claim.id
       OR claim.task_type <> 'outbox_delivery'
       OR claim.domain_request_id IS DISTINCT FROM NEW.id THEN
        RAISE EXCEPTION 'Invalid delivery task binding' USING ERRCODE='23514';
    END IF;
    IF (NEW.purpose <> 'operational' AND NEW.scope_id IS DISTINCT FROM NEW.campaign_id)
       OR (NEW.purpose = 'operational' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_parish WHERE id=NEW.scope_id
       )) OR (NEW.family_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_family_campaign
           WHERE id=NEW.family_id AND campaign_id=NEW.campaign_id
       )) THEN
        RAISE EXCEPTION 'Invalid delivery scope' USING ERRCODE='23514';
    END IF;
    IF NEW.pause_hold_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_delivery_pause_hold
        WHERE id=NEW.pause_hold_id AND campaign_id=NEW.campaign_id
          AND pause_version=NEW.pause_version
    ) THEN
        RAISE EXCEPTION 'Invalid delivery pause binding' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.version <> 1 OR NEW.state <> 'pending' OR NEW.action <> 'created'
           OR NEW.attempt <> 0 THEN
            RAISE EXCEPTION 'Invalid initial delivery' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.command_id = OLD.command_id THEN
        RAISE EXCEPTION 'Delivery command has already committed' USING ERRCODE='23514';
    END IF;
    allowed := ARRAY['version','updated_at','actor_id','correlation_id','command_id','command_digest',
        'action','reason','evidence_digest','evidence_note','provider_key_digest',
        'provider_message_digest'];
    IF NEW.action IN ('prepared','cancel_unsent') AND (
        SELECT action FROM public.stewardship_outbox_event
        WHERE message_id=NEW.id AND action IN ('retry_idempotent','retry_unaccepted',
            'fail_unaccepted','accept','authorize_resend') ORDER BY version DESC LIMIT 1
    ) = 'retry_idempotent' THEN
        RAISE EXCEPTION 'An uncertain idempotent retry must retain its payload and outcome'
            USING ERRCODE='23514';
    END IF;
    IF NEW.action IN ('prepared','hold','release_hold') THEN
        IF OLD.state NOT IN ('pending','retry_wait') OR NEW.state <> OLD.state THEN
            RAISE EXCEPTION 'Only unsent delivery may be prepared or held'
                USING ERRCODE='23514';
        END IF;
        IF NEW.action='prepared' THEN
            allowed := allowed || ARRAY['render_id','sealed_substitutions',
                'sealed_key_id','token_generation_id','credential_epoch_id'];
        ELSE
            allowed := allowed || ARRAY['pause_hold_id','pause_version'];
            IF (NEW.action='hold' AND NEW.pause_hold_id IS NULL)
               OR (NEW.action='release_hold' AND
                   (OLD.pause_hold_id IS NULL OR NEW.pause_hold_id IS NOT NULL)) THEN
                RAISE EXCEPTION 'Invalid delivery hold operation' USING ERRCODE='23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.stewardship_campaign c
                WHERE c.id=NEW.campaign_id AND NEW.routing='production'
                  AND ((NEW.action='hold' AND c.delivery_paused AND c.pause_version=NEW.pause_version)
                    OR (NEW.action='release_hold' AND NOT c.delivery_paused
                        AND c.pause_version>OLD.pause_version))
            ) THEN
                RAISE EXCEPTION 'Delivery hold must match campaign pause state' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM (VALUES
                ('pending','submit','submitting'),
                ('retry_wait','submit','submitting'),
                ('submitting','accept','delivered'),
                ('delivery_unknown','accept','delivered'),
                ('submitting','retry_unaccepted','retry_wait'),
                ('delivery_unknown','retry_unaccepted','retry_wait'),
                ('submitting','fail_unaccepted','permanent_failure'),
                ('delivery_unknown','fail_unaccepted','permanent_failure'),
                ('submitting','mark_unknown','delivery_unknown'),
                ('pending','cancel_unsent','cancelled'),
                ('retry_wait','cancel_unsent','cancelled'),
                ('permanent_failure','retry_failed','pending'),
                ('delivery_unknown','authorize_resend','pending'),
                ('submitting','retry_idempotent','retry_wait'),
                ('delivery_unknown','retry_idempotent','retry_wait')
            ) AS edge(previous, action, target)
            WHERE edge.previous=OLD.state AND edge.action=NEW.action
              AND edge.target=NEW.state
        ) THEN
            RAISE EXCEPTION 'Invalid delivery transition' USING ERRCODE='23514';
        END IF;
        allowed := allowed || ARRAY['state'];
        CASE NEW.action
        WHEN 'submit' THEN
            allowed := allowed || ARRAY['attempt','run_id','task_fence','worker_id',
                'submitted_at','provider_deadline'];
            SELECT * INTO claim FROM public.stewardship_task_run WHERE id=NEW.run_id;
            IF OLD.pause_hold_id IS NOT NULL THEN
                RAISE EXCEPTION 'Delivery is paused' USING ERRCODE='23514';
            END IF;
            -- Minimum lifecycle fence even before per-message holds are attached.
            -- The later owner also verifies dates, purpose-specific post-close
            -- rules, recipient eligibility and catch-up readiness under this lock.
            IF NEW.routing='production' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_campaign c
                JOIN public.stewardship_system_configuration s ON s.current_campaign_id=c.id
                JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
                WHERE c.id=NEW.campaign_id AND s.mode='production'
                  AND NOT s.restore_review_required AND NOT k.go_live_gate
                  AND c.state IN ('scheduled','active','closed') AND NOT c.delivery_paused
            ) THEN
                RAISE EXCEPTION 'Production delivery is not currently admitted' USING ERRCODE='23514';
            END IF;
            IF OLD.not_before > statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery retry is not yet due' USING ERRCODE='23514';
            END IF;
            IF NEW.attempt <> OLD.attempt+1 OR claim.id IS NULL
               OR claim.root_id <> NEW.task_id OR claim.state <> 'running'
               OR claim.worker_id IS DISTINCT FROM NEW.worker_id
               OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
               OR claim.fence IS DISTINCT FROM NEW.task_fence
               OR claim.lease_expires_at <= statement_timestamp()
               OR NEW.submitted_at IS DISTINCT FROM statement_timestamp()
               OR NEW.provider_deadline <= NEW.submitted_at THEN
                RAISE EXCEPTION 'Delivery attempt does not own a current claim'
                    USING ERRCODE='23514';
            END IF;
        WHEN 'retry_unaccepted', 'retry_idempotent' THEN
            allowed := allowed || ARRAY['not_before'];
            IF NEW.not_before <= statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery retry requires a future schedule'
                    USING ERRCODE='23514';
            END IF;
        WHEN 'retry_failed' THEN
            allowed := allowed || ARRAY['finished_at','render_id','sealed_substitutions',
                'sealed_key_id','token_generation_id','credential_epoch_id','not_before'];
            IF NOT EXISTS (
                SELECT 1 FROM public.stewardship_task_run
                WHERE root_id=NEW.task_id AND retry_sequence>0 AND state='queued'
                  AND parent_id=OLD.run_id
            ) THEN
                RAISE EXCEPTION 'Failed delivery needs its explicit task retry'
                    USING ERRCODE='23514';
            END IF;
        WHEN 'accept', 'fail_unaccepted', 'cancel_unsent' THEN
            allowed := allowed || ARRAY['finished_at','sealed_substitutions','sealed_key_id',
                'pause_hold_id'];
            IF NEW.finished_at IS DISTINCT FROM statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery completion must use the database clock'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            NULL;
        END CASE;
        IF (OLD.state='delivery_unknown' OR NEW.action IN
            ('accept','retry_unaccepted','fail_unaccepted','retry_idempotent'))
           AND (NEW.actor_id IS NULL OR NEW.evidence_digest='' OR NEW.evidence_note='') THEN
            RAISE EXCEPTION 'Delivery resolution requires attributed evidence'
                USING ERRCODE='23514';
        END IF;
        IF OLD.state='submitting' AND NEW.action<>'mark_unknown' THEN
            SELECT * INTO claim FROM public.stewardship_task_run WHERE id=OLD.run_id;
            IF claim.id IS NULL OR claim.root_id<>OLD.task_id OR claim.state<>'running'
               OR claim.fence IS DISTINCT FROM OLD.task_fence
               OR claim.worker_id IS DISTINCT FROM OLD.worker_id
               OR NEW.actor_id IS DISTINCT FROM OLD.worker_id
               OR claim.lease_expires_at<=statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery outcome does not own a current claim'
                    USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'Delivery command changed unrelated fields' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_outbox_history_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    INSERT INTO public.stewardship_outbox_event
        (id,created_at,actor_id,correlation_id,message_id,command_id,command_digest,version,
         previous_state,state,action,attempt,render_id,run_id,task_fence,worker_id,
         provider_key_digest,provider_message_digest,evidence_digest,evidence_note,reason,
         not_before,submitted_at,provider_deadline,finished_at)
    VALUES (gen_random_uuid(),NEW.updated_at,NEW.actor_id,NEW.correlation_id,NEW.id,
        NEW.command_id,NEW.command_digest,NEW.version,CASE WHEN TG_OP='INSERT' THEN '' ELSE OLD.state END,
        NEW.state,NEW.action,NEW.attempt,NEW.render_id,NEW.run_id,NEW.task_fence,NEW.worker_id,
        NEW.provider_key_digest,NEW.provider_message_digest,NEW.evidence_digest,
        NEW.evidence_note,NEW.reason,NEW.not_before,NEW.submitted_at,NEW.provider_deadline,NEW.finished_at);
    INSERT INTO public.stewardship_audit_event
        (id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'outbox_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_outbox_event_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    message public.stewardship_outbox_message%ROWTYPE;
    previous public.stewardship_outbox_event%ROWTYPE;
    field text;
BEGIN
    SELECT * INTO message FROM public.stewardship_outbox_message WHERE id=NEW.message_id;
    SELECT * INTO previous FROM public.stewardship_outbox_event
        WHERE message_id=NEW.message_id ORDER BY version DESC LIMIT 1;
    IF message.id IS NULL OR NEW.version <> COALESCE(previous.version,0)+1
       OR NEW.previous_state IS DISTINCT FROM COALESCE(previous.state,'')
       OR NEW.created_at IS DISTINCT FROM message.updated_at THEN
        RAISE EXCEPTION 'Invalid delivery history binding' USING ERRCODE='23514';
    END IF;
    FOREACH field IN ARRAY ARRAY['actor_id','correlation_id','command_id','command_digest','version','state',
        'action','attempt','render_id','run_id','task_fence','worker_id',
        'provider_key_digest','provider_message_digest','evidence_digest','evidence_note',
        'reason','not_before','submitted_at','provider_deadline','finished_at'] LOOP
        IF (to_jsonb(NEW)->field) IS DISTINCT FROM (to_jsonb(message)->field) THEN
            RAISE EXCEPTION 'Delivery history must match its operation'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_outbox_render_pin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    rendering public.stewardship_outbox_render%ROWTYPE;
BEGIN
    -- Check every captured selection, not just the final mutable message row:
    -- each intermediate selection already has an immutable history event.
    SELECT * INTO rendering FROM public.stewardship_outbox_render WHERE id=NEW.render_id;
    IF rendering.id IS NULL OR rendering.message_id IS DISTINCT FROM NEW.id THEN
        RAISE EXCEPTION 'Delivery render belongs to another message' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_outbox_render_shape_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    recipients jsonb;
BEGIN
    IF NEW.subject ~ E'[\r\n]' OR btrim(NEW.subject)=''
       OR btrim(NEW.html)='' OR btrim(NEW.text)=''
       OR octet_length(NEW.html)>1048576 OR octet_length(NEW.text)>1048576
       OR NEW.sender ~ E'[\r\n]' OR NEW.sender NOT LIKE '%@%' THEN
        RAISE EXCEPTION 'Invalid delivery render' USING ERRCODE='23514';
    END IF;
    FOREACH recipients IN ARRAY ARRAY[NEW.intended_recipients,NEW.routed_recipients] LOOP
        IF jsonb_typeof(recipients)<>'array' THEN
            RAISE EXCEPTION 'Invalid delivery recipients' USING ERRCODE='23514';
        END IF;
        IF jsonb_array_length(recipients) NOT BETWEEN 1 AND 100 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(recipients) value
            WHERE jsonb_typeof(value)<>'string' OR length(value#>>'{}')>254
               OR (value#>>'{}') ~ E'[\r\n]' OR (value#>>'{}') NOT LIKE '%@%'
        ) OR (SELECT count(*)<>count(DISTINCT lower(value))
            FROM jsonb_array_elements_text(recipients) value) THEN
            RAISE EXCEPTION 'Invalid delivery recipients' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF NEW.template_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_content_version
        WHERE id=NEW.template_id AND configuration_id=NEW.configuration_id AND kind='email'
    ) THEN
        RAISE EXCEPTION 'Invalid delivery template binding' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_outbox_state_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_outbox_message
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_state_v1();
CREATE TRIGGER stewardship_outbox_history
    AFTER INSERT OR UPDATE ON public.stewardship_outbox_message
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_history_v1();
CREATE TRIGGER stewardship_outbox_event_binding
    BEFORE INSERT ON public.stewardship_outbox_event
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_event_binding_v1();
CREATE CONSTRAINT TRIGGER stewardship_outbox_render_pin
    AFTER INSERT OR UPDATE ON public.stewardship_outbox_message DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_render_pin_v1();
CREATE TRIGGER stewardship_outbox_render_shape
    BEFORE INSERT ON public.stewardship_outbox_render
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_render_shape_v1();

REVOKE ALL ON FUNCTION public.stewardship_outbox_state_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_history_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_event_binding_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_render_pin_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_render_shape_v1() FROM PUBLIC;
