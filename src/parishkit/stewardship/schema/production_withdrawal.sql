-- Current metadata only. The preview exposes counts and an opaque fingerprint,
-- never recipients or message bodies. Work-order serialization makes the same
-- snapshot authoritative during confirmation; every change advances a version.
CREATE VIEW public.stewardship_withdrawal_inventory AS
    WITH scope AS (
        SELECT current_campaign_id AS campaign_id FROM public.stewardship_system_configuration
    ), definitions AS (
        SELECT id,version FROM public.stewardship_schedule_definition
        WHERE campaign_id=(SELECT campaign_id FROM scope)
    ), work AS (
        SELECT w.* FROM public.stewardship_schedule_work_row w
        JOIN definitions d ON d.id=w.definition_id
        JOIN public.stewardship_schedule_occurrence o ON o.id=w.id WHERE o.mode='production'
    ), messages AS (
        SELECT m.id,m.version,m.state FROM public.stewardship_outbox_message m
        WHERE m.campaign_id=(SELECT campaign_id FROM scope) AND m.mode='production'
          AND m.purpose<>'operational'
    ), owned AS (
        SELECT outbox_id AS id FROM work WHERE outbox_id IS NOT NULL
        UNION SELECT recipient.outbox_id FROM public.stewardship_daily_digest_recipient recipient
        JOIN public.stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
        JOIN public.stewardship_daily_digest_snapshot snapshot ON snapshot.id=ready.snapshot_id
        JOIN public.stewardship_daily_digest_preparation preparation ON preparation.id=snapshot.preparation_id
        JOIN work ON work.id=preparation.occurrence_id WHERE recipient.outbox_id IS NOT NULL
        UNION SELECT recipient.outbox_id FROM public.stewardship_weekly_digest_recipient recipient
        JOIN public.stewardship_weekly_digest_snapshot snapshot ON snapshot.id=recipient.snapshot_id
        JOIN public.stewardship_weekly_digest_preparation preparation ON preparation.id=snapshot.preparation_id
        JOIN work ON work.id=preparation.occurrence_id WHERE recipient.outbox_id IS NOT NULL
    ), totals AS (
        SELECT jsonb_build_object(
            'occurrences',(SELECT count(*) FROM work),
            'cancellable',(SELECT count(*) FROM work WHERE state IN ('pending','running') AND NOT blocking),
            'messages',(SELECT count(*) FROM messages WHERE state IN ('pending','retry_wait')),
            'failed',(SELECT count(*) FROM work WHERE state='failed'),
            'delivered',(SELECT count(*) FROM messages WHERE state='delivered'),
            'blocking',(SELECT count(*) FROM work WHERE blocking)
                +(SELECT count(*) FROM messages WHERE state IN ('submitting','delivery_unknown')
                    OR (state IN ('pending','retry_wait') AND id NOT IN (SELECT id FROM owned)))
                -- Cleanup must have removed Testing work. Never let a shared
                -- definition cancellation touch an unexpected Testing row.
                +(SELECT count(*) FROM public.stewardship_schedule_occurrence o
                    JOIN definitions d ON d.id=o.definition_id WHERE o.mode<>'production')
                +(SELECT count(*) FROM public.stewardship_task_run t
                    WHERE t.task_type='campaign_boundary'
                        AND t.domain_request_id=(SELECT campaign_id FROM scope)
                        AND t.state IN ('queued','retry_wait','running','abandoned')),
            'fingerprint',encode(sha256(convert_to(jsonb_build_array(
                (SELECT coalesce(jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM definitions),
                (SELECT coalesce(jsonb_agg(jsonb_build_array(id,version,task_versions,outbox_version) ORDER BY id),'[]') FROM work),
                (SELECT coalesce(jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM messages)
            )::text,'UTF8')),'hex')
        ) AS value
    ) SELECT scope.campaign_id,totals.value AS inventory FROM scope CROSS JOIN totals;
REVOKE ALL ON public.stewardship_withdrawal_inventory FROM PUBLIC;
CREATE FUNCTION public.stewardship_withdrawal_inventory_v1(campaign uuid)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT inventory FROM public.stewardship_withdrawal_inventory WHERE campaign_id=$1
$$;
REVOKE ALL ON FUNCTION public.stewardship_withdrawal_inventory_v1(uuid) FROM PUBLIC;

CREATE TABLE public.stewardship_production_withdrawal (
    id uuid NOT NULL PRIMARY KEY,
    created_at timestamptz DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    confirmation_id uuid NOT NULL UNIQUE REFERENCES public.stewardship_production_confirmation(id) DEFERRABLE INITIALLY DEFERRED,
    transition_id uuid NOT NULL UNIQUE REFERENCES public.stewardship_campaign_transition(id) DEFERRABLE INITIALLY DEFERRED,
    request_key uuid NOT NULL UNIQUE,
    session_id uuid NOT NULL,
    authenticated_at timestamptz NOT NULL,
    preview_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    expected_campaign_version bigint NOT NULL CHECK(expected_campaign_version>=0),
    expected_runtime_version bigint NOT NULL CHECK(expected_runtime_version>=0),
    inventory jsonb NOT NULL,
    reason varchar(2000) NOT NULL,
    cleanup_acknowledged boolean NOT NULL,
    CONSTRAINT production_withdrawal_versions CHECK(expected_campaign_version>=1 AND expected_runtime_version>=1),
    CONSTRAINT production_withdrawal_acknowledged CHECK(cleanup_acknowledged)
);
CREATE INDEX stewardship_production_withdrawal_correlation ON public.stewardship_production_withdrawal(correlation_id);

CREATE FUNCTION public.stewardship_production_withdrawal_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE confirmation public.stewardship_production_confirmation%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
    instant timestamptz; current_inventory jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Production withdrawal is immutable' USING ERRCODE='23514';
    END IF;
    IF current_setting('transaction_isolation')<>'read committed'
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736212 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR (session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
            (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')) THEN
        RAISE EXCEPTION 'Withdrawal requires its ordered web owner' USING ERRCODE='42501';
    END IF;
    SELECT * INTO runtime FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO confirmation FROM public.stewardship_production_confirmation WHERE id=NEW.confirmation_id;
    SELECT c.* INTO campaign FROM public.stewardship_campaign c
        JOIN public.stewardship_production_request request ON request.campaign_id=c.id
        WHERE request.id=confirmation.request_id FOR UPDATE OF c;
    instant:=public.stewardship_campaign_now_v1();
    IF NEW.actor_id IS NULL OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              AND login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp()) THEN
        RAISE EXCEPTION 'Withdrawal requires fresh Admin authentication' USING ERRCODE='42501';
    END IF;
    IF campaign.id IS NULL OR confirmation.target_state<>'scheduled'
       OR runtime.current_campaign_id IS DISTINCT FROM campaign.id
       OR runtime.mode<>'production' OR runtime.restore_review_required
       OR runtime.version IS DISTINCT FROM NEW.expected_runtime_version
       OR campaign.version IS DISTINCT FROM NEW.expected_campaign_version
       OR campaign.state<>'scheduled' OR campaign.ever_active OR campaign.delivery_paused
       OR campaign.active_token_generation_id IS DISTINCT FROM confirmation.generation_id
       OR instant >= (SELECT starts_at FROM public.stewardship_campaign_configuration WHERE id=campaign.active_configuration_id)
       OR NEW.preview_at>instant OR NEW.preview_at>=NEW.expires_at OR NEW.expires_at<=instant
       OR NEW.expires_at>NEW.preview_at+interval '5 minutes'
       OR btrim(NEW.reason)='' OR NOT NEW.cleanup_acknowledged THEN
        RAISE EXCEPTION 'Withdrawal is no longer admitted' USING ERRCODE='23514';
    END IF;
    current_inventory:=public.stewardship_withdrawal_inventory_v1(campaign.id);
    IF NEW.inventory IS DISTINCT FROM current_inventory OR (current_inventory->>'blocking')::bigint<>0 THEN
        RAISE EXCEPTION 'Withdrawal work changed or remains uncertain' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_withdrawal_guard_v1() FROM PUBLIC;
CREATE TRIGGER production_withdrawal_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_production_withdrawal FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_production_withdrawal_guard_v1();

CREATE FUNCTION public.stewardship_production_withdrawal_effect_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid; revision record;
BEGIN
    SELECT request.campaign_id INTO campaign
        FROM public.stewardship_production_confirmation confirmation
        JOIN public.stewardship_production_request request ON request.id=confirmation.request_id
        WHERE confirmation.id=NEW.confirmation_id;
    FOR revision IN SELECT DISTINCT o.definition_id,o.revision_id
        FROM public.stewardship_schedule_occurrence o
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        WHERE d.campaign_id=campaign AND o.mode='production' ORDER BY o.definition_id,o.revision_id
    LOOP
        PERFORM public.stewardship_schedule_cancel_v1(revision.definition_id,revision.revision_id,
            NEW.actor_id,NEW.correlation_id,'production_withdrawn');
    END LOOP;
    INSERT INTO public.stewardship_campaign_transition(id,campaign_id,request_id,action,
        expected_version,expected_runtime_version,before_state,after_state,before_mode,after_mode,
        configuration_id,reason,actor_id,correlation_id)
    VALUES(NEW.transition_id,campaign,NEW.request_key,'withdraw',NEW.expected_campaign_version,
        NEW.expected_runtime_version,'scheduled','draft','production','testing',
        (SELECT active_configuration_id FROM public.stewardship_system_configuration),
        NEW.reason,NEW.actor_id,NEW.correlation_id);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_production_withdrawal_effect_v1() FROM PUBLIC;
CREATE TRIGGER production_withdrawal_effect AFTER INSERT ON public.stewardship_production_withdrawal
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_withdrawal_effect_v1();
