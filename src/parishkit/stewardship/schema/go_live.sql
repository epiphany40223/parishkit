-- ADM-05 Admin intake/control authority; Production activation stays disabled.
-- Private schema-owner fixtures and worker-owned completion retain their ports.

CREATE FUNCTION public.stewardship_go_live_admin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE campaign_uuid uuid;
BEGIN
    IF pg_has_role(current_user,(SELECT nspowner FROM pg_namespace
            WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_web' THEN
        -- The existing runtime-command trigger admits only fenced worker
        -- updates; it does not admit creating Admin intent.
        IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'Go-live intent requires its Admin web owner' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='stewardship_production_request' THEN
        campaign_uuid := NEW.campaign_id;
        IF TG_OP='UPDATE' AND NEW.action NOT IN ('cancel','retry_failed') THEN
            RAISE EXCEPTION 'Web may only cancel or retry cleanup' USING ERRCODE='42501';
        END IF;
    ELSE
        SELECT campaign_id INTO campaign_uuid FROM public.stewardship_production_request
            WHERE id=NEW.request_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_campaign campaign ON campaign.id=runtime.current_campaign_id
        JOIN public.stewardship_portal_user actor ON actor.id=NEW.actor_id AND NOT actor.disabled
        JOIN public.stewardship_address_rule rule ON rule.configuration_id=runtime.active_configuration_id
            AND rule.email=actor.email AND rule.roles @> '["administrator"]'::jsonb
        JOIN public.stewardship_portal_session login ON login.principal_id=actor.id
            AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
            AND login.last_activity_at>clock_timestamp()-interval '30 minutes'
        WHERE campaign.id=campaign_uuid AND campaign.state='draft'
            AND runtime.mode='testing' AND NOT runtime.restore_review_required
            AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate
                WHERE state IN ('preparing','running'))
    ) THEN
        RAISE EXCEPTION 'Go-live command requires current Admin scope' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_production_request' AND TG_OP='INSERT' THEN
        IF NEW.initiated_by_id IS DISTINCT FROM NEW.actor_id
           OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_portal_session
            WHERE principal_id=NEW.actor_id AND revoked_at IS NULL
                AND authenticated_at=NEW.reauthenticated_at AND expires_at>clock_timestamp()
                AND last_activity_at>clock_timestamp()-interval '30 minutes'
        ) OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_system_configuration runtime
            JOIN public.stewardship_campaign_mail_test mail
                ON mail.configuration_id=runtime.active_configuration_id
                AND mail.campaign_id=runtime.current_campaign_id AND mail.state='accepted'
            JOIN public.stewardship_content_version content ON content.id=mail.template_id
            JOIN public.stewardship_schedule_revision revision
                ON revision.values->>'template_version'=content.record_id::text
                AND revision.campaign_id=mail.campaign_id
            JOIN public.stewardship_schedule_definition definition
                ON definition.current_revision_id=revision.id
                AND definition.kind IN ('initial','reminder')
            JOIN public.stewardship_applied_integration workspace
                ON workspace.configuration_id=runtime.active_configuration_id
                AND workspace.kind='google_workspace'
                AND workspace.credential_fingerprint=mail.fingerprint
            WHERE runtime.active_configuration_id=NEW.configuration_id
                AND runtime.current_campaign_id=campaign_uuid
        ) THEN
            RAISE EXCEPTION 'Go-live requires authenticated current Family-test evidence' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_go_live_admin_v1() FROM PUBLIC;
CREATE TRIGGER aa_go_live_admin BEFORE INSERT OR UPDATE ON public.stewardship_production_request
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_go_live_admin_v1();
CREATE TRIGGER aa_go_live_admin BEFORE INSERT ON public.stewardship_production_cancellation
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_go_live_admin_v1();

CREATE FUNCTION public.stewardship_go_live_manifest_pin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_web' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.id
    ) THEN
        RAISE EXCEPTION 'Go-live intent must commit with its exact sealed inventory' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_go_live_manifest_pin_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER go_live_manifest_pin AFTER INSERT ON public.stewardship_production_request
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_go_live_manifest_pin_v1();

-- Narrow column grants must not allow an unjournaled web writer to acquire a
-- gate or invalidate a rehearsal. Test the committed request/manifest, since
-- intake deliberately invalidates the old epoch before inserting its request.
CREATE FUNCTION public.stewardship_go_live_gate_pin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_web' THEN RETURN NULL; END IF;
    IF TG_TABLE_NAME='stewardship_campaign_credentials' THEN
        IF (NEW.go_live_gate,NEW.rehearsal_epoch_id) IS NOT DISTINCT FROM
           (OLD.go_live_gate,OLD.rehearsal_epoch_id) THEN RETURN NULL; END IF;
        IF OLD.go_live_gate AND NOT NEW.go_live_gate AND NEW.rehearsal_epoch_id IS NULL
           AND EXISTS (
            SELECT 1 FROM public.stewardship_production_request request
            JOIN public.stewardship_production_manifest manifest ON manifest.request_id=request.id
            WHERE request.campaign_id=NEW.campaign_id AND request.state='cancelled'
                AND request.gate_version<=OLD.version
           ) AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_production_request
            WHERE campaign_id=NEW.campaign_id AND state NOT IN ('cancelled','activated')
           ) THEN RETURN NULL; END IF;
        IF NOT NEW.go_live_gate OR NEW.rehearsal_epoch_id IS NOT NULL
           OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_production_request request
            JOIN public.stewardship_production_manifest manifest ON manifest.request_id=request.id
            WHERE request.campaign_id=NEW.campaign_id AND request.gate_version=NEW.version
                AND request.invalidated_epoch_id IS NOT DISTINCT FROM OLD.rehearsal_epoch_id
        ) THEN
            RAISE EXCEPTION 'Web gate acquisition requires sealed cleanup intent' USING ERRCODE='23514';
        END IF;
    ELSIF NOT EXISTS (
        SELECT 1 FROM public.stewardship_production_request request
        JOIN public.stewardship_production_manifest manifest ON manifest.request_id=request.id
        WHERE request.campaign_id=NEW.campaign_id AND request.invalidated_epoch_id=NEW.id
    ) THEN
        RAISE EXCEPTION 'Web rehearsal invalidation requires sealed cleanup intent' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_go_live_gate_pin_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER go_live_gate_pin AFTER UPDATE ON public.stewardship_campaign_credentials
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_go_live_gate_pin_v1();
CREATE CONSTRAINT TRIGGER go_live_epoch_pin AFTER UPDATE ON public.stewardship_rehearsal_epoch
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_go_live_gate_pin_v1();

-- These existing trigger-only verifiers need private inventory helper calls,
-- not new directly executable APIs or private payload SELECT grants for web.
ALTER FUNCTION public.stewardship_production_target_guard_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_production_manifest_guard_v1() SECURITY DEFINER;
