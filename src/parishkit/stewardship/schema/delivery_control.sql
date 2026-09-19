-- Admin delivery commands expose only aggregate mail metadata. Immutable
-- routing, not a browser exemption flag, distinguishes live campaign messages.
CREATE VIEW public.stewardship_delivery_control_inventory AS
    WITH scope AS (
        SELECT current_campaign_id AS campaign_id FROM public.stewardship_system_configuration
    ), messages AS (
        SELECT id,version,purpose,state,pause_hold_id,
            state='delivery_unknown' OR (state='retry_wait' AND (
                SELECT action FROM public.stewardship_outbox_event e WHERE e.message_id=m.id
                    AND e.action IN ('retry_idempotent','retry_unaccepted','fail_unaccepted','accept','authorize_resend')
                ORDER BY e.version DESC LIMIT 1)='retry_idempotent') AS uncertain
        FROM public.stewardship_outbox_message m
        WHERE campaign_id=(SELECT campaign_id FROM scope)
          AND mode='production' AND routing='production'
          AND purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest')
          AND state IN ('pending','retry_wait','submitting','delivery_unknown')
    ), types AS (
        SELECT purpose,count(*) FILTER(WHERE state IN ('pending','retry_wait')) AS queued,
            count(*) FILTER(WHERE pause_hold_id IS NOT NULL) AS held,
            count(*) FILTER(WHERE state='submitting') AS submitting,
            count(*) FILTER(WHERE uncertain) AS unknown
        FROM messages GROUP BY purpose
    ) SELECT scope.campaign_id,jsonb_build_object(
        'queued',(SELECT count(*) FROM messages WHERE state IN ('pending','retry_wait')),
        'held',(SELECT count(*) FROM messages WHERE pause_hold_id IS NOT NULL),
        'submitting',(SELECT count(*) FROM messages WHERE state='submitting'),
        'unknown',(SELECT count(*) FROM messages WHERE uncertain),
        'types',(SELECT coalesce(jsonb_object_agg(purpose,to_jsonb(types)-'purpose'),'{}') FROM types),
        'fingerprint',encode(sha256(convert_to((SELECT coalesce(
            jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM messages)::text,'UTF8')),'hex')
    ) AS inventory FROM scope;
REVOKE ALL ON public.stewardship_delivery_control_inventory FROM PUBLIC;

-- A successful explicit test after this pause proves the exact current sender
-- and credential, without asking the web process to contact a provider. Failed
-- or unresolved later tests and typed live-provider failures veto that proof.
-- Bound freshness to five minutes; durable acceptance alone is not perpetual
-- permission to release mail. Only this current-campaign projection is public
-- to the restricted web role, never provider evidence or message bodies.
CREATE VIEW public.stewardship_delivery_control_health AS
    WITH scope AS (
        SELECT c.id AS campaign_id,r.active_configuration_id AS configuration_id,
            c.delivery_paused,w.credential_fingerprint,
            public.stewardship_mail_provider_identity_v1(r.active_configuration_id) AS identity,
            (SELECT max(created_at) FROM public.stewardship_campaign_control
                WHERE campaign_id=c.id AND action='pause') AS paused_at
        FROM public.stewardship_system_configuration r
        JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
        JOIN public.stewardship_applied_integration w
            ON w.configuration_id=r.active_configuration_id AND w.kind='google_workspace'
        WHERE r.mode='production'
    ), proof AS (
        SELECT t.id,t.submitted_at,t.finished_at FROM public.stewardship_campaign_mail_test t,scope s
        WHERE s.delivery_paused AND t.campaign_id=s.campaign_id
            AND t.configuration_id=s.configuration_id AND t.fingerprint=s.credential_fingerprint
            AND t.state='accepted' AND t.submitted_at>=s.paused_at
            AND t.submitted_at>clock_timestamp()-interval '5 minutes'
        ORDER BY t.finished_at DESC,t.id DESC LIMIT 1
    ) SELECT s.campaign_id,jsonb_build_object(
        'ready',p.id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_campaign_mail_test t
            WHERE t.campaign_id=s.campaign_id AND t.configuration_id=s.configuration_id
                AND t.created_at>=p.submitted_at
                AND t.state IN ('queued','submitting','not_sent','delivery_unknown'))
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_outbox_event e
                WHERE e.provider_identity=s.identity AND e.created_at>=p.submitted_at
                    AND e.previous_state='submitting' AND e.submitted_at IS NOT NULL
                    AND e.reason IN ('smtp_unavailable','smtp_systemic')
                    AND (e.evidence_note LIKE '{"health":"unavailable",%'
                        OR e.evidence_note LIKE '{"health":"systemic",%')),
        'proof',p.id,'checked_at',p.finished_at,
        'expires_at',p.submitted_at+interval '5 minutes'
    ) AS health FROM scope s LEFT JOIN proof p ON true;
REVOKE ALL ON public.stewardship_delivery_control_health FROM PUBLIC;

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
    instant timestamptz; current_inventory jsonb; current_health jsonb; family_impact jsonb; digest_impact jsonb;
    selected_types jsonb; selected_count bigint; clears_pause boolean;
    current_coverage jsonb; preparing bigint;
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
    IF NEW.action='resolve' THEN
        IF NOT campaign.delivery_paused OR campaign.state<>'closed'
           OR jsonb_typeof(NEW.selection->'types') IS DISTINCT FROM 'array'
           OR coalesce(NEW.selection->>'decision','') NOT IN ('release','cancel','clear') THEN
            RAISE EXCEPTION 'Held-message resolution requires a closed paused campaign' USING ERRCODE='23514';
        END IF;
        SELECT coalesce(jsonb_agg(kind ORDER BY kind),'[]') INTO selected_types
            FROM (SELECT DISTINCT value AS kind FROM jsonb_array_elements_text(NEW.selection->'types')
                WHERE value IN ('receipt','daily_digest','weekly_digest')) types;
        SELECT coalesce(sum((current_inventory->'types'->kind->>'held')::bigint),0) INTO selected_count
            FROM jsonb_array_elements_text(selected_types) kind;
        SELECT coverage,s.preparing INTO current_coverage,preparing
            FROM public.stewardship_delivery_closed_coverage_summary s WHERE campaign_id=campaign.id;
        SELECT coalesce(jsonb_object_agg(key,value),'{}') INTO current_coverage
            FROM jsonb_each(current_coverage) WHERE selected_types ? key;
        clears_pause:=(current_inventory->>'held')::bigint=selected_count
            AND (current_inventory->>'submitting')::bigint=0 AND (current_inventory->>'unknown')::bigint=0;
        current_health:='null'::jsonb;
        IF NEW.selection->>'decision'='release' THEN
            SELECT health INTO current_health FROM public.stewardship_delivery_control_health
                WHERE campaign_id=campaign.id;
            IF (current_health->>'ready')::boolean IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Held-message release requires current sender health' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.selection IS DISTINCT FROM jsonb_build_object('plan','closed','decision',NEW.selection->>'decision',
                'types',selected_types,'health',current_health,'coverage',current_coverage)
           OR preparing IS DISTINCT FROM 0
           OR (NEW.selection->>'decision'='cancel' AND EXISTS(
                SELECT 1 FROM jsonb_array_elements_text(selected_types) kind
                WHERE (current_inventory->'types'->kind->>'submitting')::bigint>0
                    OR (current_inventory->'types'->kind->>'unknown')::bigint>0))
           OR (NEW.control_id IS NOT NULL) IS DISTINCT FROM clears_pause
           OR (NEW.selection->>'decision'='clear' AND (NOT clears_pause OR selected_types<>'[]'::jsonb))
           OR (NEW.selection->>'decision'<>'clear' AND selected_count=0) THEN
            RAISE EXCEPTION 'Held-message selection changed; review exact current counts' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.control_id IS NULL OR NEW.action NOT IN ('pause','resume') THEN
        RAISE EXCEPTION 'Delivery control action is not admitted' USING ERRCODE='23514';
    END IF;
    IF NEW.action='pause' THEN
        IF campaign.delivery_paused OR NEW.selection<>'{}'::jsonb THEN
            RAISE EXCEPTION 'Delivery pause is not admitted' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT health INTO current_health FROM public.stewardship_delivery_control_health
            WHERE campaign_id=campaign.id;
        IF NOT campaign.delivery_paused
           OR (current_health->>'ready')::boolean IS DISTINCT FROM true
           OR (current_inventory->>'submitting')::bigint<>0
           OR (current_inventory->>'unknown')::bigint<>0 THEN
            RAISE EXCEPTION 'Resume requires current health and resolved uncertainty' USING ERRCODE='23514';
        END IF;
        IF NEW.selection->>'plan'='before_start' THEN
            IF campaign.state<>'scheduled'
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_configuration p
                    WHERE p.id=campaign.active_configuration_id AND instant<p.starts_at)
               OR NEW.selection IS DISTINCT FROM jsonb_build_object('plan','before_start','health',current_health)
               OR EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
                WHERE d.campaign_id=campaign.id AND o.mode='production'
                    AND o.state IN ('pending','running','delivery_unknown') AND o.due_at<=instant) THEN
                RAISE EXCEPTION 'Pre-start resume cannot consume due work' USING ERRCODE='23514';
            END IF;
        ELSIF NEW.selection->>'plan'='family' THEN
            SELECT impact INTO family_impact FROM public.stewardship_delivery_family_recovery_summary
                WHERE campaign_id=campaign.id;
            SELECT impact INTO digest_impact FROM public.stewardship_delivery_digest_recovery_summary
                WHERE campaign_id=campaign.id;
            IF campaign.state NOT IN ('scheduled','active')
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_configuration p
                    WHERE p.id=campaign.active_configuration_id AND instant>=p.starts_at AND instant<p.ends_at)
               OR (family_impact->>'blocked')::bigint IS DISTINCT FROM 0
               OR (digest_impact->>'blocked')::bigint IS DISTINCT FROM 0
               OR NEW.selection IS DISTINCT FROM jsonb_build_object('plan','family',
                    'health',current_health,'family',family_impact,'digests',digest_impact)
               OR EXISTS(SELECT 1 FROM public.stewardship_activation_catchup
                    WHERE campaign_id=campaign.id AND completed_at IS NULL) THEN
                RAISE EXCEPTION 'Resume requires the complete current recovery plan' USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Unknown delivery recovery plan' USING ERRCODE='23514';
        END IF;
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
    IF NEW.action='resolve' THEN
        PERFORM public.stewardship_delivery_resolve_closed_v1(NEW.id);
        RETURN NEW;
    END IF;
    IF NEW.action='resume' AND NEW.selection->>'plan'='family' THEN
        PERFORM public.stewardship_delivery_recover_families_v1(NEW.id);
        PERFORM public.stewardship_delivery_recover_digests_v1(NEW.id);
    END IF;
    -- Both control and all unsent holds commit together. Already-submitting or
    -- unknown outcomes keep their independent reconciliation and immutable data.
    INSERT INTO public.stewardship_campaign_control(id,campaign_id,request_id,action,
        expected_version,expected_runtime_version,reason,occurred_at,actor_id,correlation_id)
    VALUES(NEW.control_id,NEW.campaign_id,NEW.id,NEW.action,NEW.expected_campaign_version,
        NEW.expected_runtime_version,NEW.reason,public.stewardship_campaign_now_v1(),
        NEW.actor_id,NEW.correlation_id);
    SELECT pause_version INTO pause FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    IF NEW.action='resume' THEN
        UPDATE public.stewardship_outbox_message m SET
            action='release_hold',version=m.version+1,pause_hold_id=NULL,
            actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,command_id=gen_random_uuid(),
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'delivery_resume',NEW.id,m.id,m.version)::text,'UTF8')),'hex')
        WHERE m.campaign_id=NEW.campaign_id AND m.mode='production' AND m.routing='production'
            AND m.purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest')
            AND m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL;
        RETURN NEW;
    END IF;
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

-- Report preparation may continue during a live pause. Attach the same durable
-- hold before a new outbox exists, so count previews never depend on whether a
-- mail worker happened to inspect that message. Operational/Testing routing is
-- immutable and exempt; this trigger cannot reroute any message.
CREATE FUNCTION public.stewardship_delivery_new_hold_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign public.stewardship_campaign%ROWTYPE; hold uuid;
BEGIN
    IF NEW.mode<>'production' OR NEW.routing<>'production'
       OR NEW.purpose NOT IN ('initial','reminder','receipt','daily_digest','weekly_digest') THEN RETURN NEW; END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.state NOT IN ('pending','retry_wait') OR NEW.state=OLD.state
           OR public.stewardship_delivery_message_released_v1(NEW.id) THEN RETURN NEW; END IF;
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO campaign FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    IF NOT campaign.delivery_paused THEN RETURN NEW; END IF;
    INSERT INTO public.stewardship_delivery_pause_hold(id,campaign_id,pause_version,actor_id,correlation_id)
        VALUES(gen_random_uuid(),campaign.id,campaign.pause_version,NEW.actor_id,NEW.correlation_id)
        ON CONFLICT(campaign_id,pause_version) DO NOTHING;
    SELECT id INTO hold FROM public.stewardship_delivery_pause_hold
        WHERE campaign_id=campaign.id AND pause_version=campaign.pause_version;
    IF NEW.pause_hold_id IS NOT NULL AND (NEW.pause_hold_id<>hold
        OR NEW.pause_version IS DISTINCT FROM campaign.pause_version) THEN
        RAISE EXCEPTION 'New delivery must retain the current pause hold' USING ERRCODE='23514';
    END IF;
    NEW.pause_hold_id:=hold; NEW.pause_version:=campaign.pause_version;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_new_hold_v1() FROM PUBLIC;
CREATE TRIGGER aa_delivery_new_hold BEFORE INSERT OR UPDATE ON public.stewardship_outbox_message
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_delivery_new_hold_v1();
