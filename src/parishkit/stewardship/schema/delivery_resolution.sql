-- Fresh-install Admin command journal. Ephemeral preparation is consumed by its
-- compiled command trigger, never retained as a second credential payload.
CREATE TABLE public.stewardship_delivery_resolution (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid,
    correlation_id uuid NOT NULL,
    message_id uuid NOT NULL,
    expected_version bigint NOT NULL CHECK(expected_version>=0),
    action varchar(16) NOT NULL,
    evidence_note varchar(2000) NOT NULL,
    duplicate_acknowledged boolean NOT NULL,
    previous_task_id uuid,
    retry_task_id uuid,
    preparation jsonb,
    CONSTRAINT delivery_resolution_action CHECK(action IN ('note','accept','resend','retry_failed','retry_unsent')),
    CONSTRAINT delivery_resolution_version CHECK(expected_version>0),
    CONSTRAINT delivery_resolution_scrubbed CHECK(preparation IS NULL)
);
CREATE INDEX delivery_resolution_history ON public.stewardship_delivery_resolution(message_id,created_at);
CREATE INDEX delivery_resolution_correlation ON public.stewardship_delivery_resolution(correlation_id);
CREATE TRIGGER delivery_resolution_immutable BEFORE UPDATE OR DELETE ON public.stewardship_delivery_resolution
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_recipient_immutable_v1();

-- This is an additional predicate inside the occurrence's existing guarded
-- transition, not a general update entry point. Web has no occurrence UPDATE.
CREATE FUNCTION public.stewardship_delivery_resolution_edge_v1(previous jsonb, proposed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT session_user='pk_stewardship_web'
      AND public.stewardship_export_authorized_v1((proposed->>'actor_id')::uuid,true)
      AND EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_outbox_event e ON e.message_id=m.id
        JOIN public.stewardship_task_run t ON t.id=(previous->>'task_id')::uuid
        WHERE m.id=(previous->>'outbox_id')::uuid AND m.semantic_key=(previous->>'id')::uuid
          AND t.root_id=m.task_id AND t.domain_request_id=m.id
          AND t.state='failed' AND t.task_type='outbox_delivery'
          AND e.command_id=(proposed->>'correlation_id')::uuid
          AND e.actor_id=(proposed->>'actor_id')::uuid AND e.previous_state='delivery_unknown'
          AND ((proposed->>'state'='succeeded' AND m.state='delivered'
                  AND e.action='accept' AND e.reason='admin_external_acceptance')
            OR (proposed->>'state'='pending' AND m.state='pending'
                  AND e.action='authorize_resend' AND e.reason='admin_resend')))
$$;

CREATE FUNCTION public.stewardship_delivery_retry_admitted_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_schedule_occurrence o ON o.id=m.semantic_key AND o.outbox_id=m.id
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id AND d.current_revision_id=o.revision_id
        JOIN public.stewardship_campaign c ON c.id=m.campaign_id
        JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        JOIN public.stewardship_source_current cur ON cur.snapshot_id=k.source_snapshot_id AND cur.generation=k.source_generation
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
        WHERE m.id=message AND r.mode=m.mode AND NOT k.population_dirty
          AND f.active AND f.email_eligible AND f.email_deliverable AND f.source_generation=cur.generation
          AND p.starts_at<=public.stewardship_campaign_now_v1() AND p.ends_at>public.stewardship_campaign_now_v1()
          AND public.stewardship_export_admitted_v1(c.id,true)
          AND ((m.mode='production' AND c.state IN ('scheduled','active') AND NOT c.delivery_paused AND f.effective_submission_id IS NULL)
            OR (m.mode='testing' AND c.state='draft' AND k.rehearsal_epoch_id=m.rehearsal_epoch_id
              AND EXISTS(SELECT 1 FROM public.stewardship_rehearsal_epoch e WHERE e.id=k.rehearsal_epoch_id AND e.state='active')
              AND NOT EXISTS(SELECT 1 FROM public.stewardship_submission s WHERE s.family_id=f.id AND s.mode='test' AND s.rehearsal_epoch_id=k.rehearsal_epoch_id)))
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment s WHERE s.definition_id=o.definition_id AND s.mode=o.mode AND s.target=o.target AND s.slot=o.slot)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h WHERE h.definition_id=o.definition_id AND h.mode=o.mode AND h.target=o.target AND h.slot=o.slot AND h.state IN ('unreviewed','assumed_delivered')))
$$;

-- Only the non-callable command trigger can use this owner-rights preparation
-- helper. A current rerender plus fresh retained token references is mandatory.
CREATE FUNCTION public.stewardship_delivery_retry_render_v1(message uuid, preparation jsonb, actor uuid, command uuid)
RETURNS uuid LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE m public.stewardship_outbox_message%ROWTYPE; configuration uuid; revision uuid;
    content jsonb:=preparation->'render'; sealed jsonb:=preparation->'sealed'; rendering uuid:=gen_random_uuid();
BEGIN
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=message;
    SELECT active_configuration_id INTO configuration FROM public.stewardship_system_configuration;
    SELECT revision_id INTO revision FROM public.stewardship_schedule_occurrence WHERE id=m.semantic_key;
    IF public.stewardship_delivery_retry_admitted_v1(message) IS NOT TRUE
       OR jsonb_typeof(preparation) IS DISTINCT FROM 'object'
       OR preparation-ARRAY['render','sealed']<>'{}'::jsonb
       OR jsonb_typeof(content) IS DISTINCT FROM 'object' OR jsonb_typeof(sealed) IS DISTINCT FROM 'object'
       OR public.stewardship_family_mail_render_admitted_v1(content,m.family_id,configuration,revision,m.mode) IS NOT TRUE
       OR sealed->>'sealed_substitutions' IS NULL OR sealed->>'sealed_key_id' IS NULL
       OR (m.mode='production' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_campaign c
           JOIN public.stewardship_family_token_generation g ON g.id=c.active_token_generation_id
           JOIN public.stewardship_credential_deployment d ON d.family_link_epoch=g.credential_epoch
           WHERE c.id=m.campaign_id AND g.id=(sealed->>'token_generation_id')::uuid
             AND g.state='active' AND g.credential_epoch=(sealed->>'credential_epoch_id')::uuid))
    THEN RAISE EXCEPTION 'Retry requires fresh scoped preparation' USING ERRCODE='23514'; END IF;
    INSERT INTO public.stewardship_outbox_render
        (id,actor_id,correlation_id,message_id,configuration_id,template_id,sender,reply_to,
         intended_recipients,routed_recipients,subject,html,text,payload_digest)
    VALUES(rendering,actor,command,m.id,configuration,(content->>'template_id')::uuid,
        content->>'sender',content->>'reply_to',content->'intended_recipients',content->'routed_recipients',
        content->>'subject',content->>'html',content->>'text',content->>'payload_digest');
    RETURN rendering;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_retry_render_v1(uuid,jsonb,uuid,uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE m public.stewardship_outbox_message%ROWTYPE;
    o public.stewardship_schedule_occurrence%ROWTYPE;
    latest public.stewardship_task_run%ROWTYPE;
    rendering uuid; sealed jsonb; proof text; command_hash text; replacement uuid; next_action text;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=NEW.message_id;
    PERFORM 1 FROM public.stewardship_task_run WHERE id=m.task_id FOR UPDATE;
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=NEW.message_id FOR UPDATE;
    SELECT * INTO o FROM public.stewardship_schedule_occurrence WHERE id=m.semantic_key FOR UPDATE;
    IF session_user<>'pk_stewardship_web'
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,true)
       OR m.id IS NULL OR m.purpose NOT IN ('initial','reminder')
       OR NEW.expected_version<>m.version OR NEW.correlation_id<>NEW.id
       OR btrim(NEW.evidence_note)='' OR o.outbox_id IS DISTINCT FROM m.id
       OR NOT public.stewardship_export_admitted_v1(m.campaign_id,true)
       OR EXISTS(SELECT 1 FROM public.stewardship_campaign WHERE id=m.campaign_id AND state='archived')
       OR NEW.duplicate_acknowledged IS DISTINCT FROM (NEW.action='resend')
    THEN RAISE EXCEPTION 'Delivery resolution lacks current exact Admin authority'
        USING ERRCODE='23514'; END IF;
    proof:=encode(sha256(convert_to(NEW.evidence_note,'UTF8')),'hex');
    command_hash:=encode(sha256(convert_to(NEW.id::text||':'||NEW.action||':'||proof,'UTF8')),'hex');
    IF NEW.action='note' THEN
        IF NEW.previous_task_id IS NOT NULL OR NEW.retry_task_id IS NOT NULL OR NEW.preparation IS NOT NULL
        THEN RAISE EXCEPTION 'A note cannot change execution' USING ERRCODE='23514'; END IF;
    ELSE
        SELECT * INTO latest FROM public.stewardship_task_run WHERE id=NEW.previous_task_id;
        IF latest.id IS NULL OR latest.root_id<>m.task_id OR latest.state<>'failed'
           OR latest.task_type<>'outbox_delivery' OR latest.domain_request_id<>m.id
        THEN RAISE EXCEPTION 'Resolution requires drained failed execution' USING ERRCODE='23514'; END IF;
        IF NEW.action='accept' THEN
            IF m.state<>'delivery_unknown' OR NEW.preparation IS NOT NULL OR NEW.retry_task_id IS NOT NULL
               OR EXISTS(SELECT 1 FROM public.stewardship_task_run WHERE root_id=m.task_id AND retry_sequence>latest.retry_sequence)
            THEN RAISE EXCEPTION 'Acceptance requires the current unresolved attempt' USING ERRCODE='23514'; END IF;
            UPDATE public.stewardship_outbox_message SET state='delivered',action='accept',
                command_id=NEW.id,command_digest=command_hash,evidence_note=NEW.evidence_note,
                evidence_digest=proof,provider_key_digest='',provider_message_digest='',
                reason='admin_external_acceptance',finished_at=statement_timestamp(),
                sealed_substitutions=NULL,sealed_key_id=NULL,pause_hold_id=NULL,
                actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=m.id;
            UPDATE public.stewardship_schedule_occurrence SET state='succeeded',reason='recovery_complete',
                actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=o.id;
            INSERT INTO public.stewardship_schedule_fulfillment
                (id,actor_id,correlation_id,definition_id,mode,target,slot,disposition,occurrence_id)
            VALUES(gen_random_uuid(),NEW.actor_id,NEW.id,o.definition_id,o.mode,o.target,o.slot,'delivered',o.id);
        ELSE
            IF NEW.action NOT IN ('resend','retry_failed','retry_unsent')
               OR (NEW.action='resend' AND m.state<>'delivery_unknown')
               OR (NEW.action='retry_failed' AND m.state<>'permanent_failure')
               OR (NEW.action='retry_unsent' AND m.state NOT IN ('pending','retry_wait'))
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t
                   WHERE t.id=NEW.retry_task_id AND t.parent_id=latest.id AND t.root_id=m.task_id
                     AND t.retry_command_id=NEW.id AND t.initiated_by_id=NEW.actor_id
                     AND t.state='queued' AND t.task_type='outbox_delivery' AND t.domain_request_id=m.id)
               OR EXISTS(SELECT 1 FROM public.stewardship_task_run t
                   WHERE t.root_id=m.task_id AND t.retry_sequence>latest.retry_sequence AND t.id<>NEW.retry_task_id)
            THEN RAISE EXCEPTION 'Retry requires the exact new linked execution' USING ERRCODE='23514'; END IF;
            rendering:=public.stewardship_delivery_retry_render_v1(m.id,NEW.preparation,NEW.actor_id,NEW.id);
            sealed:=NEW.preparation->'sealed';
            IF NEW.action='resend' THEN
                UPDATE public.stewardship_outbox_message SET state='pending',action='authorize_resend',
                    command_id=NEW.id,command_digest=command_hash,evidence_note=NEW.evidence_note,
                    evidence_digest=proof,provider_key_digest='',provider_message_digest='',reason='admin_resend',
                    actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=m.id;
            END IF;
            replacement:=CASE WHEN NEW.action='resend' THEN gen_random_uuid() ELSE NEW.id END;
            next_action:=CASE WHEN NEW.action='retry_failed' THEN 'retry_failed' ELSE 'prepared' END;
            UPDATE public.stewardship_outbox_message SET
                state=CASE WHEN NEW.action='retry_failed' THEN 'pending' ELSE state END,
                action=next_action,command_id=replacement,command_digest=command_hash,
                evidence_note=NEW.evidence_note,evidence_digest=proof,reason='admin_'||NEW.action,
                provider_key_digest='',provider_message_digest='',
                render_id=rendering,sealed_substitutions=sealed->>'sealed_substitutions',
                sealed_key_id=sealed->>'sealed_key_id',token_generation_id=(sealed->>'token_generation_id')::uuid,
                credential_epoch_id=(sealed->>'credential_epoch_id')::uuid,finished_at=NULL,
                actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=m.id;
            IF o.state IN ('failed','delivery_unknown') THEN
                UPDATE public.stewardship_schedule_occurrence SET state='pending',reason='recovery_retry',
                    retry_command_id=NEW.id,actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=o.id;
            END IF;
        END IF;
    END IF;
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.id,'delivery_resolution_'||NEW.action,NEW.id,m.campaign_id);
    NEW.preparation:=NULL;
    RETURN NEW;
END $$;
CREATE TRIGGER delivery_resolution_command BEFORE INSERT ON public.stewardship_delivery_resolution
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_delivery_resolution_guard_v1();
REVOKE ALL ON FUNCTION public.stewardship_delivery_resolution_guard_v1() FROM PUBLIC;
