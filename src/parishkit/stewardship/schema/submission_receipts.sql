-- Fresh-install receipt allocation. Web submits this narrow immutable command,
-- not a generic outbox write and not a request to a provider or broker.
ALTER TABLE public.stewardship_submission_receipt ADD CONSTRAINT submission_receipt_outbox_fk
    FOREIGN KEY(outbox_id) REFERENCES public.stewardship_outbox_message(id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION public.stewardship_receipt_render_admitted_v1(
    proposed jsonb, family uuid, campaign uuid, configuration uuid, mode text
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE expected jsonb; email jsonb; template uuid; test_recipient text; body text;
BEGIN
    SELECT settings INTO email FROM public.stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT id INTO template FROM public.stewardship_content_version
        WHERE configuration_id=configuration AND campaign_id=campaign
          AND kind='email' AND slot='confirmation';
    SELECT testing_recipient INTO test_recipient FROM public.stewardship_system_configuration;
    expected:=public.stewardship_family_mail_recipients_v1(family);
    body:=coalesce(proposed->>'subject','')||coalesce(proposed->>'html','')||coalesce(proposed->>'text','');
    RETURN jsonb_typeof(proposed)='object' AND email IS NOT NULL
       AND (proposed->>'configuration_id')::uuid=configuration
       AND (proposed->>'template_id')::uuid IS NOT DISTINCT FROM template
       AND proposed->>'sender'=email->>'sender' AND proposed->>'reply_to'=email->>'reply_to'
       AND jsonb_array_length(expected)>0 AND proposed->'intended_recipients'=expected
       AND proposed->'routed_recipients'=CASE mode WHEN 'testing'
           THEN jsonb_build_array(test_recipient) ELSE expected END
       AND body NOT LIKE '%PARISHKIT_REDACTED_FAMILY_CODE%'
       AND body NOT LIKE '%https://parishkit.invalid/redacted-family-link%'
       AND body !~ '\{\{[[:space:]]*family_(code|url)[[:space:]]*\}\}'
       AND (mode<>'testing' OR (proposed->>'subject' LIKE '[TEST] %'
           AND proposed->>'html' LIKE '<h2>TEST</h2>%'
           AND proposed->>'text' LIKE 'TEST — sent to %'));
END $$;

CREATE FUNCTION public.stewardship_submission_receipt_command_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE s public.stewardship_submission%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
    c public.stewardship_campaign%ROWTYPE;
    task public.stewardship_task_run%ROWTYPE;
    expected jsonb; content jsonb:=NEW.preparation->'render';
    rendering uuid:=gen_random_uuid(); hold uuid; hold_version bigint;
    mode text; routing text; proof text;
BEGIN
    -- The table owner already has unrestricted DDL/data access (also used by
    -- disposable test setup). No other runtime login inherits this Web port.
    IF session_user<>'pk_stewardship_web' AND session_user<>(
        SELECT pg_get_userbyid(relowner) FROM pg_class
        WHERE oid='public.stewardship_submission_receipt'::regclass)
    THEN RAISE EXCEPTION 'Receipt creation requires the submission owner' USING ERRCODE='42501'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND pg_locks.mode='ExclusiveLock' AND granted)
    THEN RAISE EXCEPTION 'Receipt creation requires ordered submission admission' USING ERRCODE='23514'; END IF;
    SELECT * INTO s FROM public.stewardship_submission WHERE id=NEW.submission_id;
    SELECT * INTO r FROM public.stewardship_system_configuration;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=s.campaign_id;
    mode:=CASE s.mode WHEN 'live' THEN 'production' ELSE 'testing' END;
    routing:=CASE s.mode WHEN 'live' THEN 'production' ELSE 'testing_override' END;
    IF s.id IS NULL OR NEW.actor_id IS DISTINCT FROM s.family_id
       OR NEW.correlation_id IS DISTINCT FROM s.correlation_id
       OR r.current_campaign_id IS DISTINCT FROM s.campaign_id OR r.mode<>mode
       OR r.active_configuration_id IS DISTINCT FROM s.configuration_id
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_family_form_baseline
           WHERE id=s.baseline_id AND state='open')
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_credentials k
           JOIN public.stewardship_source_current cur ON cur.snapshot_id=k.source_snapshot_id
             AND cur.generation=k.source_generation
           JOIN public.stewardship_family_campaign f ON f.campaign_id=k.campaign_id
           WHERE k.campaign_id=c.id AND f.id=s.family_id AND f.source_generation=cur.generation
             AND cur.snapshot_id=s.validation_source_id AND NOT k.population_dirty
             AND ((s.mode='live' AND c.state IN ('scheduled','active'))
               OR (s.mode='test' AND c.state='draft' AND k.rehearsal_epoch_id=s.rehearsal_epoch_id
                 AND EXISTS(SELECT 1 FROM public.stewardship_rehearsal_epoch e
                     WHERE e.id=s.rehearsal_epoch_id AND e.state='active'))))
    THEN RAISE EXCEPTION 'Receipt lacks its current admitted submission' USING ERRCODE='23514'; END IF;
    expected:=public.stewardship_family_mail_recipients_v1(s.family_id);
    IF jsonb_array_length(expected)=0 THEN
        IF NEW.disposition<>'no_deliverable_recipient' OR NEW.outbox_id IS NOT NULL OR NEW.preparation IS NOT NULL
        THEN RAISE EXCEPTION 'Receipt has no deliverable source recipients' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO task FROM public.stewardship_task_run WHERE id=(NEW.preparation->>'task_id')::uuid;
    IF NEW.disposition<>'queued' OR NEW.outbox_id IS NULL
       OR jsonb_typeof(NEW.preparation) IS DISTINCT FROM 'object'
       OR NEW.preparation-ARRAY['render','task_id']<>'{}'::jsonb
       OR task.id IS NULL OR task.root_id<>task.id OR task.state<>'queued' OR task.version<>1
       OR task.task_type<>'outbox_delivery' OR task.domain_request_id IS DISTINCT FROM NEW.outbox_id
       OR task.initiated_by_id IS DISTINCT FROM s.family_id OR task.correlation_id<>s.correlation_id
       OR task.idempotency_key IS DISTINCT FROM NEW.outbox_id::text
       OR public.stewardship_receipt_render_admitted_v1(content,s.family_id,c.id,s.configuration_id,mode) IS NOT TRUE
    THEN RAISE EXCEPTION 'Receipt requires exact local preparation' USING ERRCODE='23514'; END IF;
    IF mode='production' AND c.delivery_paused THEN
        INSERT INTO public.stewardship_delivery_pause_hold(id,actor_id,correlation_id,campaign_id,pause_version)
            VALUES(gen_random_uuid(),s.family_id,s.correlation_id,c.id,c.pause_version)
            ON CONFLICT(campaign_id,pause_version) DO NOTHING;
        SELECT id,pause_version INTO hold,hold_version FROM public.stewardship_delivery_pause_hold
            WHERE campaign_id=c.id AND pause_version=c.pause_version;
    END IF;
    proof:=encode(sha256(convert_to(NEW.id::text||':'||content::text,'UTF8')),'hex');
    INSERT INTO public.stewardship_outbox_message
        (id,actor_id,correlation_id,version,command_id,command_digest,action,
         provider_key_digest,provider_message_digest,evidence_digest,evidence_note,reason,
         scope_id,semantic_key,campaign_id,family_id,mode,routing,purpose,task_id,render_id,
         credential_namespace,pause_hold_id,pause_version)
    VALUES(NEW.outbox_id,s.family_id,s.correlation_id,1,NEW.id,proof,'created',
        '','','','','',c.id,s.id,c.id,s.family_id,mode,routing,'receipt',task.id,rendering,'none',hold,hold_version);
    INSERT INTO public.stewardship_outbox_render
        (id,actor_id,correlation_id,message_id,configuration_id,template_id,sender,reply_to,
         intended_recipients,routed_recipients,subject,html,text,payload_digest)
    VALUES(rendering,s.family_id,s.correlation_id,NEW.outbox_id,s.configuration_id,(content->>'template_id')::uuid,
        content->>'sender',content->>'reply_to',content->'intended_recipients',content->'routed_recipients',
        content->>'subject',content->>'html',content->>'text',content->>'payload_digest');
    NEW.preparation:=NULL;
    RETURN NEW;
END $$;
CREATE TRIGGER submission_receipt_command BEFORE INSERT ON public.stewardship_submission_receipt
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_submission_receipt_command_v1();
REVOKE ALL ON FUNCTION public.stewardship_submission_receipt_command_v1() FROM PUBLIC;

-- Preserve the immutable parent proof on every receipt message, including a
-- raw allocation attempted through a role with generic journal capabilities.
CREATE FUNCTION public.stewardship_submission_receipt_binding_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='receipt' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_submission_receipt receipt
        JOIN public.stewardship_submission s ON s.id=receipt.submission_id
        WHERE receipt.outbox_id=NEW.id AND receipt.disposition='queued'
          AND NEW.semantic_key=s.id AND NEW.family_id=s.family_id AND NEW.campaign_id=s.campaign_id
          AND NEW.mode=CASE s.mode WHEN 'live' THEN 'production' ELSE 'testing' END
          AND NEW.credential_namespace='none' AND NEW.rehearsal_epoch_id IS NULL
          AND NEW.token_generation_id IS NULL AND NEW.credential_epoch_id IS NULL
          AND NEW.sealed_substitutions IS NULL AND NEW.sealed_key_id IS NULL
    ) THEN RAISE EXCEPTION 'Receipt delivery requires its exact immutable submission' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER submission_receipt_binding
    AFTER INSERT OR UPDATE ON public.stewardship_outbox_message DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_submission_receipt_binding_v1();
REVOKE ALL ON FUNCTION public.stewardship_submission_receipt_binding_v1() FROM PUBLIC;

-- A receipt survives campaign close and does not rely on reusable access tokens,
-- invitation schedule revisions, catch-up selection or latest-response pointers.
CREATE FUNCTION public.stewardship_receipt_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_submission_receipt receipt ON receipt.outbox_id=m.id
        JOIN public.stewardship_submission s ON s.id=receipt.submission_id AND s.id=m.semantic_key
        JOIN public.stewardship_campaign c ON c.id=m.campaign_id AND c.id=s.campaign_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        JOIN public.stewardship_source_current cur ON cur.snapshot_id=k.source_snapshot_id AND cur.generation=k.source_generation
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.id=s.family_id AND f.campaign_id=c.id
        WHERE m.id=message AND m.purpose='receipt' AND m.credential_namespace='none'
          AND receipt.disposition='queued' AND r.mode=m.mode
          AND NOT r.restore_review_required AND NOT k.go_live_gate AND NOT k.population_dirty
          AND f.source_generation=cur.generation AND f.active AND f.email_eligible AND f.email_deliverable
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
              WHERE gate.campaign_id=c.id AND gate.state<>'released')
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message unresolved
              WHERE unresolved.family_id=f.id AND unresolved.campaign_id=c.id AND unresolved.mode=m.mode
                AND unresolved.id<>m.id AND unresolved.state IN ('submitting','delivery_unknown'))
          AND ((m.mode='production' AND s.mode='live' AND c.state IN ('scheduled','active','closed') AND NOT c.delivery_paused)
            OR (m.mode='testing' AND s.mode='test' AND c.state='draft' AND s.rehearsal_epoch_id=k.rehearsal_epoch_id
              AND EXISTS(SELECT 1 FROM public.stewardship_rehearsal_epoch e
                  WHERE e.id=s.rehearsal_epoch_id AND e.state='active')))
    )
$$;
