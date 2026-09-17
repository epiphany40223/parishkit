-- BG-03 private batch execution. No directly executable definer API is granted
-- to any runtime role. A checkpoint INSERT is the closed, fenced command.

CREATE FUNCTION public.stewardship_cleanup_relation_v1(target_category text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog, public, pg_temp AS $$
    SELECT CASE target_category
        WHEN 'baselines' THEN 'stewardship_family_form_baseline'
        WHEN 'family_sessions' THEN 'stewardship_family_session'
        WHEN 'ministry_requests' THEN 'stewardship_ministry_request'
        WHEN 'occurrences' THEN 'stewardship_schedule_occurrence'
        WHEN 'occurrence_events' THEN 'stewardship_occurrence_transition'
        WHEN 'outbox_events' THEN 'stewardship_outbox_event'
        WHEN 'outbox_messages' THEN 'stewardship_outbox_message'
        WHEN 'outbox_renders' THEN 'stewardship_outbox_render'
        WHEN 'proposals' THEN 'stewardship_proposed_change'
        WHEN 'rehearsal_credentials' THEN 'stewardship_rehearsal_credential'
        WHEN 'rehearsal_macs' THEN 'stewardship_rehearsal_code_mac'
        WHEN 'schedule_fulfillments' THEN 'stewardship_schedule_fulfillment'
        WHEN 'source_pins' THEN 'stewardship_source_pin'
        WHEN 'submission_receipts' THEN 'stewardship_submission_receipt'
        WHEN 'submissions' THEN 'stewardship_submission'
        WHEN 'prior_inventory_targets' THEN 'stewardship_production_target'
        WHEN 'daily_digest_recipients' THEN 'stewardship_daily_digest_recipient'
        WHEN 'daily_digest_ready' THEN 'stewardship_daily_digest_ready'
        WHEN 'daily_digest_snapshots' THEN 'stewardship_daily_digest_snapshot'
        WHEN 'daily_digest_fact_pins' THEN 'stewardship_fact_pin'
        ELSE NULL END
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_relation_v1(text) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_target_exists_v1(target_category text, target_uuid uuid)
RETURNS boolean LANGUAGE plpgsql VOLATILE SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE relation_name text;
    present boolean;
BEGIN
    IF target_category='session_data' THEN
        RETURN EXISTS (SELECT 1 FROM public.stewardship_family_session f
            JOIN public.django_session s ON s.session_key=f.session_id WHERE f.id=target_uuid);
    END IF;
    relation_name := public.stewardship_cleanup_relation_v1(target_category);
    IF relation_name IS NULL THEN
        RAISE EXCEPTION 'Cleanup target category is unavailable' USING ERRCODE='23514';
    END IF;
    -- Only a closed compiled mapping supplies an identifier; the UUID remains
    -- bound data. Absence is stronger than losing membership in a scope query.
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I WHERE id=$1)',relation_name)
        INTO present USING target_uuid;
    RETURN present;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_target_exists_v1(text,uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_claim_v1(request_uuid uuid)
RETURNS boolean LANGUAGE sql VOLATILE SET search_path TO pg_catalog, public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_production_request r
        JOIN public.stewardship_production_manifest m ON m.request_id=r.id
        JOIN public.stewardship_campaign campaign ON campaign.id=r.campaign_id AND campaign.state='draft'
        JOIN public.stewardship_system_configuration s ON s.current_campaign_id=r.campaign_id
        JOIN public.stewardship_campaign_credentials c ON c.campaign_id=r.campaign_id
        JOIN public.stewardship_task_run t ON t.id=r.run_id AND t.root_id=r.task_id
        WHERE r.id=request_uuid AND r.state='cleanup_running'
          AND t.task_type='production_cleanup' AND t.domain_request_id=r.id
          AND t.state='running' AND t.fence=r.task_fence AND t.worker_id=r.worker_id
          AND t.lease_expires_at>clock_timestamp()
          AND s.mode='testing' AND NOT s.restore_review_required
          AND s.active_configuration_id=r.configuration_id
          AND c.go_live_gate AND c.rehearsal_epoch_id IS NULL AND c.version>=r.gate_version
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_production_cancellation WHERE request_id=r.id)
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate WHERE state IN ('preparing','running'))
    ) AND EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted
    )
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_claim_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_effect_v1(target_category text, target_uuid uuid)
RETURNS boolean LANGUAGE sql VOLATILE SET search_path TO pg_catalog, public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_cleanup_effect e
        JOIN public.stewardship_production_checkpoint p ON p.id=e.checkpoint_id AND p.request_id=e.request_id
        JOIN public.stewardship_production_request r ON r.id=e.request_id
        WHERE e.transaction_id=pg_current_xact_id() AND e.category=target_category AND e.target_id=target_uuid
          AND p.run_id=r.run_id AND p.task_fence=r.task_fence AND p.worker_id=r.worker_id
          AND p.sequence=r.checkpoint_sequence+1 AND public.stewardship_cleanup_claim_v1(r.id)
    )
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_effect_v1(text,uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_scan_window_v1(request_uuid uuid, maximum_rows integer)
RETURNS TABLE(category text, target_id uuid, cursor_position bigint)
LANGUAGE sql STABLE SET search_path TO pg_catalog, public, pg_temp AS $$
    WITH previous AS (
        SELECT COALESCE((SELECT scan_position FROM public.stewardship_production_checkpoint
            WHERE request_id=request_uuid ORDER BY sequence DESC LIMIT 1),0) AS position
    ), start AS (
        SELECT CASE WHEN EXISTS (SELECT 1 FROM public.stewardship_production_target
            WHERE request_id=request_uuid AND stewardship_production_target.position>previous.position)
            THEN previous.position ELSE 0 END AS position FROM previous
    )
    SELECT i.category::text,i.target_id,i.position
    FROM public.stewardship_production_target i,start
    WHERE i.request_id=request_uuid AND i.position>start.position
    ORDER BY i.position LIMIT maximum_rows
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_scan_window_v1(uuid,integer) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_batch_targets_v1(request_uuid uuid, maximum_rows integer)
RETURNS TABLE(category text, target_id uuid)
LANGUAGE plpgsql STABLE SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    candidate record;
    companion uuid;
    companion_category text;
    eligible boolean;
    used integer := 0;
    unit_size integer;
BEGIN
    IF maximum_rows NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Cleanup requires a bounded batch' USING ERRCODE='23514';
    END IF;
    -- The indexed durable cursor caps inspected candidates BEFORE evaluating
    -- dependencies. Scan-only checkpoints advance over blocked prefixes; the
    -- next sweep revisits parents whose children were deleted in earlier units.
    FOR candidate IN SELECT i.category,i.target_id
        FROM public.stewardship_cleanup_scan_window_v1(request_uuid,maximum_rows) i
        WHERE CASE i.category
            WHEN 'session_data' THEN false
            WHEN 'baselines' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_submission WHERE baseline_id=i.target_id)
            WHEN 'family_sessions' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_family_form_baseline WHERE family_session_id=i.target_id)
            WHEN 'source_pins' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_source_pin WHERE id=i.target_id
                  AND (parent_kind='form_baseline' OR (parent_kind='digest'
                    AND EXISTS(SELECT 1 FROM public.stewardship_daily_digest_snapshot WHERE id=parent_id))))
            WHEN 'daily_digest_ready' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_daily_digest_recipient WHERE ready_id=i.target_id)
            WHEN 'daily_digest_fact_pins' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_fact_pin p
                JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=p.parent_id
                WHERE p.id=i.target_id)
            WHEN 'daily_digest_snapshots' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_daily_digest_ready WHERE snapshot_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_fact_pin
                    WHERE parent_kind='digest' AND parent_id=i.target_id)
            WHEN 'proposals' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_proposed_change WHERE superseded_by_id=i.target_id)
            WHEN 'ministry_requests' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_ministry_request WHERE superseded_by_id=i.target_id)
            WHEN 'rehearsal_credentials' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_rehearsal_code_mac WHERE credential_id=i.target_id)
            WHEN 'outbox_renders' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_outbox_message WHERE render_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_event WHERE render_id=i.target_id)
            WHEN 'outbox_messages' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_outbox_event WHERE message_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission_receipt WHERE outbox_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_daily_digest_recipient WHERE outbox_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_render r
                    JOIN public.stewardship_outbox_message m ON m.id=r.message_id
                    WHERE m.id=i.target_id AND r.id<>m.render_id)
            WHEN 'submissions' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_proposed_change WHERE submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_request WHERE submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission_receipt WHERE submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message WHERE semantic_key=i.target_id AND purpose='receipt')
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin WHERE parent_kind='submission' AND parent_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission WHERE prior_submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_family_form_baseline WHERE prior_submission_id=i.target_id)
            WHEN 'occurrences' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_occurrence_transition WHERE occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_fulfillment WHERE occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence WHERE replacement_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_restore_delivery_hold WHERE recovery_occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_restore_hold_resolution WHERE recovery_occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_postclose_resolution WHERE occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_outbox_message m ON m.id=o.outbox_id WHERE o.id=i.target_id)
            ELSE true END
        ORDER BY i.cursor_position
    LOOP
        EXIT WHEN used=maximum_rows;
        companion := NULL;
        companion_category := NULL;
        unit_size := 1;
        eligible := true;
        CASE candidate.category
        WHEN 'baselines' THEN
            SELECT id INTO companion FROM public.stewardship_source_pin
                WHERE parent_kind='form_baseline' AND parent_id=candidate.target_id;
            IF companion IS NOT NULL THEN
                companion_category := 'source_pins';
                unit_size := 2;
                eligible := EXISTS (SELECT 1 FROM public.stewardship_production_target i
                    WHERE i.request_id=request_uuid AND i.category=companion_category AND i.target_id=companion);
            END IF;
        WHEN 'family_sessions' THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_production_target i
                WHERE i.request_id=request_uuid AND i.category='session_data' AND i.target_id=candidate.target_id) THEN
                companion := candidate.target_id;
                companion_category := 'session_data';
                unit_size := 2;
            END IF;
        WHEN 'daily_digest_snapshots' THEN
            -- A snapshot and its source pin form one indivisible private unit.
            -- Removing the snapshot alone would orphan the inventory selector.
            SELECT id INTO companion FROM public.stewardship_source_pin
                WHERE parent_kind='digest' AND parent_id=candidate.target_id;
            IF companion IS NOT NULL THEN
                companion_category := 'source_pins';
                unit_size := 2;
                eligible := EXISTS (SELECT 1 FROM public.stewardship_production_target i
                    WHERE i.request_id=request_uuid AND i.category=companion_category AND i.target_id=companion);
            END IF;
        WHEN 'outbox_messages' THEN
            SELECT render_id INTO companion FROM public.stewardship_outbox_message WHERE id=candidate.target_id;
            companion_category := 'outbox_renders';
            unit_size := 2;
            eligible := EXISTS (SELECT 1 FROM public.stewardship_production_target i
                    WHERE i.request_id=request_uuid AND i.category='outbox_renders' AND i.target_id=companion);
        ELSE NULL;
        END CASE;
        IF NOT eligible OR used+unit_size>maximum_rows THEN CONTINUE; END IF;
        IF companion IS NOT NULL THEN
            category := companion_category;
            target_id := companion;
            RETURN NEXT;
        END IF;
        category := candidate.category;
        target_id := candidate.target_id;
        RETURN NEXT;
        used := used+unit_size;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_batch_targets_v1(uuid,integer) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_batch_summary_v1(request_uuid uuid, checkpoint_uuid uuid, scan_position bigint, scan_round bigint)
RETURNS TABLE(counts jsonb, total bigint, fingerprint text)
LANGUAGE sql STABLE SET search_path TO pg_catalog, public, pg_temp AS $$
    WITH targets AS MATERIALIZED (
        SELECT category,target_id FROM public.stewardship_cleanup_effect
        WHERE transaction_id=pg_current_xact_id() AND request_id=request_uuid AND checkpoint_id=checkpoint_uuid
    ), totals AS (
        SELECT category,count(*) quantity FROM targets GROUP BY category
    )
    SELECT (SELECT COALESCE(jsonb_object_agg(category,quantity),'{}'::jsonb) FROM totals),
        count(*),encode(sha256(convert_to('stewardship-cleanup-batch-v2','UTF8') || decode('00','hex') ||
            uuid_send(request_uuid) || int8send(scan_position) || int8send(scan_round) ||
            int8send((SELECT checkpoint_sequence+1 FROM public.stewardship_production_request WHERE id=request_uuid)) ||
            COALESCE(string_agg(
                convert_to(category,'UTF8') || decode('00','hex') || uuid_send(target_id),''::bytea
                ORDER BY category COLLATE "C",target_id),''::bytea)),'hex')
    FROM targets
$$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_batch_summary_v1(uuid,uuid,bigint,bigint) FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_prepare_batch_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    request public.stewardship_production_request%ROWTYPE;
    planned record;
    previous public.stewardship_production_checkpoint%ROWTYPE;
BEGIN
    -- Foundation-only journal tests are not an executable worker command. The
    -- compiled worker additionally requires this verified manifest at admission.
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.request_id) THEN
        RETURN NEW;
    END IF;
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id FOR UPDATE;
    IF NOT public.stewardship_cleanup_claim_v1(NEW.request_id)
       OR NEW.run_id IS DISTINCT FROM request.run_id OR NEW.task_fence IS DISTINCT FROM request.task_fence
       OR NEW.worker_id IS DISTINCT FROM request.worker_id OR NEW.actor_id IS DISTINCT FROM request.worker_id
       OR NEW.sequence<>request.checkpoint_sequence+1 OR NEW.deleted_count NOT BETWEEN 2 AND 1000
       OR NEW.counts IS DISTINCT FROM '{}'::jsonb OR NEW.batch_digest IS DISTINCT FROM repeat('0',64)
       OR NEW.scanned_count<>0 OR NEW.scan_position<>0 OR NEW.scan_round<>0
       OR NEW.correlation_id IS DISTINCT FROM (SELECT correlation_id FROM public.stewardship_task_run WHERE id=request.run_id) THEN
        RAISE EXCEPTION 'Cleanup batch requires exact current command ownership' USING ERRCODE='23514';
    END IF;
    SELECT count(*),COALESCE(max(cursor_position),0) INTO NEW.scanned_count,NEW.scan_position
        FROM public.stewardship_cleanup_scan_window_v1(NEW.request_id,NEW.deleted_count::integer);
    IF NEW.scanned_count=0 THEN
        RAISE EXCEPTION 'Cleanup inventory has no remaining scan window' USING ERRCODE='23514';
    END IF;
    SELECT * INTO previous FROM public.stewardship_production_checkpoint
        WHERE request_id=NEW.request_id ORDER BY sequence DESC LIMIT 1;
    NEW.scan_round := COALESCE(previous.scan_round,0);
    IF NEW.scan_position<=previous.scan_position THEN
        IF NOT EXISTS (SELECT 1 FROM public.stewardship_production_checkpoint
            WHERE request_id=NEW.request_id AND scan_round=previous.scan_round AND deleted_count>0) THEN
            RAISE EXCEPTION 'Cleanup inventory has no dependency-ready batch' USING ERRCODE='23514';
        END IF;
        NEW.scan_round := NEW.scan_round+1;
    END IF;
    -- Capture the bounded plan once. It grants no deletion authority until the
    -- checkpoint INSERT actually exists and all its binding guards have passed.
    -- Any rejected INSERT rolls this private state back with the statement.
    INSERT INTO public.stewardship_cleanup_effect(transaction_id,checkpoint_id,request_id,category,target_id)
        SELECT pg_current_xact_id(),NEW.id,NEW.request_id,category,target_id
        FROM public.stewardship_cleanup_batch_targets_v1(NEW.request_id,NEW.deleted_count::integer);
    SELECT * INTO planned FROM public.stewardship_cleanup_batch_summary_v1(
        NEW.request_id,NEW.id,NEW.scan_position,NEW.scan_round);
    NEW.counts := planned.counts;
    NEW.deleted_count := planned.total;
    NEW.batch_digest := planned.fingerprint;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_prepare_batch_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_apply_batch_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    request public.stewardship_production_request%ROWTYPE;
    planned record;
    target record;
    relation_name text;
    session_key_value text;
    deleted bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.request_id) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id FOR UPDATE;
    IF NOT public.stewardship_cleanup_claim_v1(NEW.request_id) THEN
        RAISE EXCEPTION 'Cleanup ownership expired before deletion' USING ERRCODE='23514';
    END IF;
    SELECT * INTO planned FROM public.stewardship_cleanup_batch_summary_v1(
        NEW.request_id,NEW.id,NEW.scan_position,NEW.scan_round);
    IF planned.counts IS DISTINCT FROM NEW.counts OR planned.total<>NEW.deleted_count
       OR planned.fingerprint IS DISTINCT FROM NEW.batch_digest THEN
        RAISE EXCEPTION 'Cleanup selection changed before deletion' USING ERRCODE='23514';
    END IF;
    -- Validate immutable campaign/routing ownership once for the entire captured
    -- batch, before any deletion changes the source joins. Work/claim locks and
    -- immutable ownership guards remain held for every following exact delete.
    IF EXISTS (
        (SELECT category,target_id FROM public.stewardship_cleanup_effect
         WHERE checkpoint_id=NEW.id AND transaction_id=pg_current_xact_id() AND request_id=NEW.request_id)
        EXCEPT SELECT category,target_id FROM public.stewardship_cleanup_inventory_v1(request.campaign_id)
    ) THEN
        RAISE EXCEPTION 'Cleanup target is no longer owned by this batch' USING ERRCODE='23514';
    END IF;
    -- Read only the already-captured plan, with companions before their parents.
    -- No newly ready parent can enter this batch as earlier rows disappear.
    FOR target IN SELECT category,target_id FROM public.stewardship_cleanup_effect
        WHERE checkpoint_id=NEW.id AND transaction_id=pg_current_xact_id() AND request_id=NEW.request_id
        ORDER BY CASE WHEN category IN ('session_data','source_pins','outbox_renders') THEN 0 ELSE 1 END,
            category COLLATE "C",target_id
    LOOP
        IF NOT public.stewardship_cleanup_effect_v1(target.category,target.target_id) THEN
            RAISE EXCEPTION 'Cleanup target is no longer owned by this batch' USING ERRCODE='23514';
        END IF;
        IF target.category='session_data' THEN
            SELECT session_id INTO session_key_value FROM public.stewardship_family_session WHERE id=target.target_id;
            DELETE FROM public.django_session WHERE session_key=session_key_value;
        ELSE
            -- Relation names come only from this compiled mapping, never input.
            relation_name := public.stewardship_cleanup_relation_v1(target.category);
            IF relation_name IS NULL THEN
                RAISE EXCEPTION 'Cleanup target category is unavailable' USING ERRCODE='23514';
            END IF;
            EXECUTE format('DELETE FROM public.%I WHERE id=$1',relation_name) USING target.target_id;
        END IF;
        GET DIAGNOSTICS deleted = ROW_COUNT;
        IF deleted<>1 THEN
            RAISE EXCEPTION 'Cleanup did not delete its exact target' USING ERRCODE='23514';
        END IF;
    END LOOP;
    -- Remove private membership only after whole coupled units are gone. The
    -- target guard independently verifies absence; no durable per-target log is
    -- retained. Counts and the fingerprint alone survive in the checkpoint.
    FOR target IN SELECT category,target_id FROM public.stewardship_cleanup_effect
        WHERE checkpoint_id=NEW.id AND transaction_id=pg_current_xact_id()
    LOOP
        DELETE FROM public.stewardship_production_target
            WHERE request_id=NEW.request_id AND category=target.category AND target_id=target.target_id;
        GET DIAGNOSTICS deleted = ROW_COUNT;
        IF deleted<>1 THEN
            RAISE EXCEPTION 'Cleanup did not consume its exact membership' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF NOT public.stewardship_cleanup_claim_v1(NEW.request_id) THEN
        RAISE EXCEPTION 'Cleanup ownership expired before checkpoint' USING ERRCODE='23514';
    END IF;
    UPDATE public.stewardship_production_request SET
        action='checkpoint',command_id=NEW.command_id,version=version+1,
        checkpoint_sequence=NEW.sequence,processed_count=processed_count+NEW.deleted_count,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,failure_reason=''
        WHERE id=NEW.request_id;
    DELETE FROM public.stewardship_cleanup_effect
        WHERE checkpoint_id=NEW.id AND transaction_id=pg_current_xact_id();
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_apply_batch_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_protect_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE target_uuid uuid;
BEGIN
    IF TG_ARGV[0]='session_data' THEN
        SELECT id INTO target_uuid FROM public.stewardship_family_session WHERE session_id=OLD.session_key;
    ELSE
        target_uuid := OLD.id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stewardship_production_target i
        JOIN public.stewardship_production_request r ON r.id=i.request_id
        WHERE i.category=TG_ARGV[0] AND i.target_id=target_uuid AND r.state<>'cancelled'
    ) AND NOT public.stewardship_cleanup_effect_v1(TG_ARGV[0],target_uuid) THEN
        RAISE EXCEPTION 'Inventoried Testing detail belongs to its cleanup worker' USING ERRCODE='23514';
    END IF;
    RETURN OLD;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_protect_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_cleanup_effect_pin_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_cleanup_effect WHERE checkpoint_id=NEW.checkpoint_id) THEN
        RAISE EXCEPTION 'Private cleanup proof must not survive its transaction' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_effect_pin_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER stewardship_cleanup_effect_pin
    AFTER INSERT ON public.stewardship_cleanup_effect DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_effect_pin_v1();

CREATE FUNCTION public.stewardship_cleanup_complete_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.action='complete' AND EXISTS (
        SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.id
    ) AND (
        NOT public.stewardship_cleanup_claim_v1(NEW.id)
        OR NEW.processed_count<>NEW.inventory_total
        OR EXISTS (SELECT 1 FROM public.stewardship_production_target WHERE request_id=NEW.id)
        OR EXISTS (SELECT 1 FROM public.stewardship_cleanup_inventory_v1(NEW.campaign_id))
    ) THEN
        RAISE EXCEPTION 'Cleanup completion requires verified absence of Testing detail' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_complete_v1() FROM PUBLIC;
CREATE TRIGGER production_cleanup_complete BEFORE UPDATE ON public.stewardship_production_request
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_complete_v1();

CREATE FUNCTION public.stewardship_cleanup_recovery_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE request public.stewardship_production_request%ROWTYPE;
BEGIN
    IF NEW.task_type<>'production_cleanup' OR NEW.action<>'recovery_fail' THEN RETURN NULL; END IF;
    SELECT r.* INTO request FROM public.stewardship_production_request r
        JOIN public.stewardship_production_manifest m ON m.request_id=r.id
        WHERE r.id=NEW.domain_request_id AND r.task_id=NEW.root_id FOR UPDATE OF r;
    IF request.id IS NULL OR request.state='cleanup_failed' THEN RETURN NULL; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration s
        JOIN public.stewardship_campaign c ON c.id=s.current_campaign_id
        JOIN public.stewardship_campaign_credentials g ON g.campaign_id=c.id
        WHERE c.id=request.campaign_id AND c.state='draft' AND s.mode='testing'
          AND NOT s.restore_review_required AND s.active_configuration_id=request.configuration_id
          AND g.go_live_gate AND g.rehearsal_epoch_id IS NULL AND g.version>=request.gate_version
    ) OR EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate WHERE state IN ('preparing','running'))
      OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Cleanup recovery requires its current gated scope' USING ERRCODE='23514';
    END IF;
    -- The ordinary task owner has already fenced the dead attempt. Mirror its
    -- exact terminal identity in this transaction, with no scheduler data grants.
    UPDATE public.stewardship_production_request SET state='cleanup_failed',
        action='recovery_fail',command_id=gen_random_uuid(),version=version+1,
        run_id=NEW.id,task_fence=NEW.fence,worker_id=NEW.worker_id,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,failure_reason='cleanup_exhausted'
        WHERE id=request.id;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_recovery_v1() FROM PUBLIC;
CREATE TRIGGER production_cleanup_recovery AFTER UPDATE ON public.stewardship_task_run
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_recovery_v1();

CREATE FUNCTION public.stewardship_cleanup_failure_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.state='cleanup_failed' AND EXISTS (
        SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=NEW.id
    ) THEN
        -- One durable alert per exhausted attempt chain. Explicit retry has a
        -- new run identity and must not suppress a subsequent distinct failure.
        -- Transport is BG-10's owner; no exception or target detail is included.
        INSERT INTO public.stewardship_operational_log(id,actor_id,correlation_id,event,level,schema,context)
        VALUES (NEW.run_id,NEW.actor_id,NEW.correlation_id,'production_cleanup_failed','CRITICAL','task',
            jsonb_build_object('task_id',NEW.task_id::text,'count',NEW.processed_count))
        ON CONFLICT (id) DO NOTHING;
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_failure_v1() FROM PUBLIC;
CREATE TRIGGER production_cleanup_failure AFTER UPDATE ON public.stewardship_production_request
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_failure_v1();

ALTER TABLE public.stewardship_production_cancellation ADD CONSTRAINT production_cancellation_request_fk
    FOREIGN KEY(request_id) REFERENCES public.stewardship_production_request(id) DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION public.stewardship_production_cancellation_immutable_v1() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER stewardship_production_cancellation_immutable_guard_v1
    BEFORE UPDATE OR DELETE ON public.stewardship_production_cancellation
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_production_cancellation_immutable_v1();

CREATE FUNCTION public.stewardship_cleanup_cancel_intent_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.actor_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_production_request r
        JOIN public.stewardship_production_manifest m ON m.request_id=r.id
        WHERE r.id=NEW.request_id AND r.version=NEW.expected_version
          AND r.state NOT IN ('cancelled','activated')
    ) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Cleanup cancellation requires current ordered intent' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
        SELECT gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'production_cancel_requested',NEW.id,campaign_id
        FROM public.stewardship_production_request WHERE id=NEW.request_id;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_cancel_intent_v1() FROM PUBLIC;
CREATE TRIGGER production_cleanup_cancel_intent BEFORE INSERT ON public.stewardship_production_cancellation
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_cancel_intent_v1();

CREATE FUNCTION public.stewardship_cleanup_cancel_finish_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE intent public.stewardship_production_cancellation%ROWTYPE;
    request public.stewardship_production_request%ROWTYPE;
BEGIN
    IF NEW.task_type<>'production_cleanup' OR NEW.state<>'cancelled' THEN RETURN NULL; END IF;
    SELECT r.* INTO request FROM public.stewardship_production_request r
        WHERE r.id=NEW.domain_request_id AND r.task_id=NEW.root_id FOR UPDATE;
    SELECT * INTO intent FROM public.stewardship_production_cancellation WHERE request_id=request.id;
    IF intent.id IS NULL OR request.state='cancelled' THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Cleanup cancellation requires a safe batch boundary' USING ERRCODE='23514';
    END IF;
    -- The task owner has stopped/fenced execution; release only this request's
    -- go-live gate, never restore, lifecycle, or any already-deleted target.
    UPDATE public.stewardship_production_request SET state='cancelled',action='cancel',
        command_id=intent.command_id,version=version+1,actor_id=intent.actor_id,
        correlation_id=intent.correlation_id,failure_reason='' WHERE id=request.id;
    UPDATE public.stewardship_campaign_credentials SET go_live_gate=false,version=version+1
        WHERE campaign_id=request.campaign_id AND go_live_gate AND rehearsal_epoch_id IS NULL;
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,'rehearsal_gate_released',id,request.campaign_id
        FROM public.stewardship_campaign_credentials WHERE campaign_id=request.campaign_id;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_cancel_finish_v1() FROM PUBLIC;
CREATE TRIGGER production_cleanup_cancel_finish AFTER UPDATE ON public.stewardship_task_run
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_cancel_finish_v1();

-- The binding trigger only verifies control metadata; caller roles need not
-- acquire direct EXECUTE rights on its private verification helper functions.
ALTER FUNCTION public.stewardship_production_checkpoint_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_production_state_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_production_history_v1() SECURITY DEFINER;
CREATE FUNCTION public.stewardship_cleanup_runtime_command_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE request_uuid uuid;
BEGIN
    -- A worker may bind a claim or record its outcome, not manufacture Admin
    -- retry/cancel intent. Only the actual schema owner (including private
    -- definer triggers) may use the journal without an operational manifest.
    -- Granting a runtime unrelated table rights cannot widen this exception.
    IF NOT pg_has_role(current_user,(SELECT nspowner FROM pg_namespace
            WHERE nspname='public'),'USAGE') THEN
        IF TG_TABLE_NAME='stewardship_production_request' THEN
            request_uuid := NEW.id;
        ELSE
            request_uuid := NEW.request_id;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.stewardship_production_manifest WHERE request_id=request_uuid) THEN
            RAISE EXCEPTION 'Cleanup runtime requires a sealed manifest' USING ERRCODE='42501';
        END IF;
        IF TG_TABLE_NAME='stewardship_production_request' THEN
            IF NEW.action NOT IN ('start','recover','retry_later','fail','complete') THEN
                RAISE EXCEPTION 'Cleanup worker cannot perform Admin commands' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_cleanup_runtime_command_v1() FROM PUBLIC;
CREATE TRIGGER aaa_production_cleanup_runtime_command BEFORE UPDATE ON public.stewardship_production_request
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_runtime_command_v1();
CREATE TRIGGER aaaa_production_cleanup_runtime_command BEFORE INSERT ON public.stewardship_production_checkpoint
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_runtime_command_v1();
CREATE TRIGGER aaa_production_cleanup_prepare
    BEFORE INSERT ON public.stewardship_production_checkpoint
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_prepare_batch_v1();
CREATE TRIGGER stewardship_production_cleanup_apply
    AFTER INSERT ON public.stewardship_production_checkpoint
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_apply_batch_v1();

CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_family_form_baseline FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('baselines');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_family_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('family_sessions');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.django_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('session_data');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_submission FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('submissions');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_submission_receipt FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('submission_receipts');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_proposed_change FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('proposals');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_ministry_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('ministry_requests');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_source_pin FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('source_pins');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_outbox_message FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('outbox_messages');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_outbox_render FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('outbox_renders');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_outbox_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('outbox_events');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('occurrences');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_occurrence_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('occurrence_events');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_schedule_fulfillment FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('schedule_fulfillments');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_rehearsal_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('rehearsal_credentials');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_rehearsal_code_mac FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('rehearsal_macs');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_production_target FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('prior_inventory_targets');
