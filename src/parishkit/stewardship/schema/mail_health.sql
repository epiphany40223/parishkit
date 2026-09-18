-- One canonical identity for result origination and recovery; invoker rights
-- preserve the caller's existing configuration-read boundary.
CREATE FUNCTION public.stewardship_mail_provider_identity_v1(configuration uuid) RETURNS text
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT COALESCE((SELECT encode(sha256(convert_to(jsonb_build_array(
        w.settings,w.credential_fingerprint,m.settings)::text,'UTF8')),'hex')
        FROM public.stewardship_applied_integration w
        JOIN public.stewardship_applied_integration m ON m.configuration_id=w.configuration_id
        WHERE w.configuration_id=configuration AND w.kind='google_workspace'
          AND w.credential_fingerprint IS NOT NULL AND m.kind='email'),'');
$$;

CREATE FUNCTION public.stewardship_mail_health_identity_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Never trust a caller-supplied denormalized key. Existing binding/result
    -- guards independently reject forged attempts and malformed SMTP evidence.
    NEW.provider_identity:='';
    IF NEW.previous_state='submitting' AND NEW.submitted_at IS NOT NULL
       AND NEW.reason IN ('smtp_accepted','smtp_transient','smtp_unavailable',
           'smtp_permanent','smtp_delivery_unknown','smtp_systemic') THEN
        SELECT public.stewardship_mail_provider_identity_v1(configuration_id)
            INTO NEW.provider_identity FROM public.stewardship_outbox_render WHERE id=NEW.render_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_mail_health_identity BEFORE INSERT ON public.stewardship_outbox_event
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_mail_health_identity_v1();
REVOKE ALL ON FUNCTION public.stewardship_mail_health_identity_v1() FROM PUBLIC;

-- Outbox guards establish exact submitted-attempt ownership before this trigger.
-- No caller receives log INSERT, history reads, or callable definer authority.
CREATE FUNCTION public.stewardship_mail_health_result_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    result jsonb; previous_result jsonb; candidate record;
    message public.stewardship_outbox_message%ROWTYPE;
    unavailable integer:=0; critical boolean:=false;
BEGIN
    IF NEW.previous_state<>'submitting' OR NEW.submitted_at IS NULL
       OR NEW.reason NOT IN ('smtp_accepted','smtp_transient','smtp_unavailable',
           'smtp_permanent','smtp_delivery_unknown','smtp_systemic') THEN RETURN NULL; END IF;
    SELECT * INTO message FROM public.stewardship_outbox_message WHERE id=NEW.message_id;
    -- Operational sends can break a streak, but can never originate an alert.
    IF message.purpose='operational' THEN RETURN NULL; END IF;
    result:=public.stewardship_family_smtp_result_v1(NEW.id);
    IF result IS NULL THEN RETURN NULL; END IF;
    critical:=result->>'health'='systemic';
    IF result->>'health'='unavailable' THEN
        IF NEW.provider_identity='' THEN RETURN NULL; END IF;
        -- Seek directly into this provider's ordered outcomes, including after
        -- a provider change. LIMIT also bounds complete evidence validation.
        FOR candidate IN
            SELECT e.id FROM public.stewardship_outbox_event e
            WHERE e.provider_identity=NEW.provider_identity
              AND e.previous_state='submitting' AND e.submitted_at IS NOT NULL
              AND e.reason IN ('smtp_accepted','smtp_transient','smtp_unavailable',
                  'smtp_permanent','smtp_delivery_unknown','smtp_systemic')
              AND (e.evidence_note LIKE '{"health":"healthy",%'
                   OR e.evidence_note LIKE '{"health":"unavailable",%'
                   OR e.evidence_note LIKE '{"health":"systemic",%')
            ORDER BY e.created_at DESC,e.id DESC LIMIT 3
        LOOP
            previous_result:=public.stewardship_family_smtp_result_v1(candidate.id);
            -- Unverifiable historical evidence breaks continuity; it must not
            -- roll back a newly settled definitive provider outcome.
            IF previous_result IS NULL OR previous_result->>'health'<>'unavailable' THEN
                RETURN NULL;
            END IF;
            unavailable:=unavailable+1;
        END LOOP;
        critical:=unavailable=3;
    END IF;
    IF critical THEN
        INSERT INTO public.stewardship_operational_log
            (id,actor_id,correlation_id,level,event,schema,context)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'CRITICAL',
            'mail_provider_failed','task',jsonb_build_object('task_id',message.task_id));
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_mail_health_result AFTER INSERT ON public.stewardship_outbox_event
    FOR EACH ROW WHEN (NEW.previous_state='submitting' AND NEW.submitted_at IS NOT NULL
        AND NEW.reason IN ('smtp_accepted','smtp_transient','smtp_unavailable',
            'smtp_permanent','smtp_delivery_unknown','smtp_systemic'))
    EXECUTE FUNCTION public.stewardship_mail_health_result_v1();
REVOKE ALL ON FUNCTION public.stewardship_mail_health_result_v1() FROM PUBLIC;
