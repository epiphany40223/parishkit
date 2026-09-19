-- Admin delivery commands expose only aggregate mail metadata. Immutable
-- routing, not a browser exemption flag, distinguishes live campaign messages.
CREATE VIEW public.stewardship_delivery_control_inventory AS
    WITH scope AS (
        SELECT current_campaign_id AS campaign_id FROM public.stewardship_system_configuration
    ), messages AS (
        SELECT id,version,purpose,state,pause_hold_id FROM public.stewardship_outbox_message
        WHERE campaign_id=(SELECT campaign_id FROM scope)
          AND mode='production' AND routing='production'
          AND purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest')
          AND state IN ('pending','retry_wait','submitting','delivery_unknown')
    ), types AS (
        SELECT purpose,count(*) FILTER(WHERE state IN ('pending','retry_wait')) AS queued,
            count(*) FILTER(WHERE pause_hold_id IS NOT NULL) AS held,
            count(*) FILTER(WHERE state='submitting') AS submitting,
            count(*) FILTER(WHERE state='delivery_unknown') AS unknown
        FROM messages GROUP BY purpose
    ) SELECT scope.campaign_id,jsonb_build_object(
        'queued',(SELECT count(*) FROM messages WHERE state IN ('pending','retry_wait')),
        'held',(SELECT count(*) FROM messages WHERE pause_hold_id IS NOT NULL),
        'submitting',(SELECT count(*) FROM messages WHERE state='submitting'),
        'unknown',(SELECT count(*) FROM messages WHERE state='delivery_unknown'),
        'types',(SELECT coalesce(jsonb_object_agg(purpose,to_jsonb(types)-'purpose'),'{}') FROM types),
        'fingerprint',encode(sha256(convert_to((SELECT coalesce(
            jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM messages)::text,'UTF8')),'hex')
    ) AS inventory FROM scope;
REVOKE ALL ON public.stewardship_delivery_control_inventory FROM PUBLIC;

CREATE TABLE public.stewardship_delivery_control (
    id uuid NOT NULL PRIMARY KEY,
    created_at timestamptz DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    control_id uuid UNIQUE REFERENCES public.stewardship_campaign_control(id) DEFERRABLE INITIALLY DEFERRED,
    action varchar(24) NOT NULL,
    session_id uuid NOT NULL,
    authenticated_at timestamptz NOT NULL,
    preview_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    expected_campaign_version bigint NOT NULL CHECK(expected_campaign_version>=0),
    expected_runtime_version bigint NOT NULL CHECK(expected_runtime_version>=0),
    inventory jsonb NOT NULL,
    selection jsonb NOT NULL,
    reason varchar(1024) NOT NULL,
    CONSTRAINT delivery_control_versions CHECK(expected_campaign_version>=1 AND expected_runtime_version>=1),
    CONSTRAINT delivery_control_action CHECK(action::text = ANY(ARRAY[
        ('pause'::varchar)::text,('resume'::varchar)::text,('resolve'::varchar)::text]))
);
CREATE INDEX stewardship_delivery_control_campaign ON public.stewardship_delivery_control(campaign_id);
CREATE INDEX stewardship_delivery_control_correlation ON public.stewardship_delivery_control(correlation_id);

CREATE FUNCTION public.stewardship_delivery_control_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign public.stewardship_campaign%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
    instant timestamptz; current_inventory jsonb;
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
              AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              AND login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp()) THEN
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
    IF NEW.action<>'pause' OR campaign.delivery_paused OR NEW.control_id IS NULL
       OR NEW.selection<>'{}'::jsonb THEN
        RAISE EXCEPTION 'Delivery control action is not admitted' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_control_guard_v1() FROM PUBLIC;
CREATE TRIGGER delivery_control_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_delivery_control FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_delivery_control_guard_v1();

CREATE FUNCTION public.stewardship_delivery_control_effect_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE hold uuid; pause bigint;
BEGIN
    -- Both control and all unsent holds commit together. Already-submitting or
    -- unknown outcomes keep their independent reconciliation and immutable data.
    INSERT INTO public.stewardship_campaign_control(id,campaign_id,request_id,action,
        expected_version,expected_runtime_version,reason,occurred_at,actor_id,correlation_id)
    VALUES(NEW.control_id,NEW.campaign_id,NEW.id,'pause',NEW.expected_campaign_version,
        NEW.expected_runtime_version,NEW.reason,public.stewardship_campaign_now_v1(),
        NEW.actor_id,NEW.correlation_id);
    SELECT pause_version INTO pause FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    INSERT INTO public.stewardship_delivery_pause_hold(id,campaign_id,pause_version,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.campaign_id,pause,NEW.actor_id,NEW.correlation_id) RETURNING id INTO hold;
    UPDATE public.stewardship_outbox_message m SET
        action='hold',version=m.version+1,pause_hold_id=hold,pause_version=pause,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,command_id=gen_random_uuid(),
        command_digest=encode(sha256(convert_to(jsonb_build_array(
            'delivery_pause',NEW.id,m.id,m.version)::text,'UTF8')),'hex'),
        reason='pause_'||pause::text
    WHERE m.campaign_id=NEW.campaign_id AND m.mode='production' AND m.routing='production'
        AND m.purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest')
        AND m.state IN ('pending','retry_wait');
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_control_effect_v1() FROM PUBLIC;
CREATE TRIGGER delivery_control_effect AFTER INSERT ON public.stewardship_delivery_control
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_delivery_control_effect_v1();
