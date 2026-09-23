-- A closed campaign can release one held message type without releasing other
-- types. Retain exact per-message decisions; a later pause version invalidates
-- an earlier release. This journal contains identities, not report/receipt PII.
CREATE TABLE public.stewardship_delivery_message_resolution (
    id uuid NOT NULL PRIMARY KEY,
    created_at timestamptz DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,correlation_id uuid NOT NULL,
    command_id uuid NOT NULL REFERENCES public.stewardship_delivery_control(id) DEFERRABLE INITIALLY DEFERRED,
    message_id uuid NOT NULL REFERENCES public.stewardship_outbox_message(id) DEFERRABLE INITIALLY DEFERRED,
    campaign_id uuid NOT NULL REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    previous_version bigint NOT NULL CHECK(previous_version>=0),
    pause_version bigint NOT NULL CHECK(pause_version>=0),
    decision varchar(8) NOT NULL,
    CONSTRAINT held_resolution_command UNIQUE(command_id,message_id),
    CONSTRAINT held_resolution_pause UNIQUE(message_id,pause_version),
    CONSTRAINT held_resolution_versions CHECK(previous_version>=1 AND pause_version>=1),
    CONSTRAINT held_resolution_decision CHECK(decision::text=ANY(ARRAY[('release'::varchar)::text,('cancel'::varchar)::text]))
);
CREATE INDEX delivery_message_resolution_command ON public.stewardship_delivery_message_resolution(command_id);
CREATE INDEX delivery_message_resolution_message ON public.stewardship_delivery_message_resolution(message_id);
CREATE INDEX delivery_message_resolution_campaign ON public.stewardship_delivery_message_resolution(campaign_id);
CREATE INDEX delivery_message_resolution_correlation ON public.stewardship_delivery_message_resolution(correlation_id);
REVOKE ALL ON public.stewardship_delivery_message_resolution FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_message_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Held-message decisions are immutable' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_delivery_control command
        JOIN public.stewardship_campaign c ON c.id=command.campaign_id
        JOIN public.stewardship_outbox_message m ON m.id=NEW.message_id
        WHERE command.id=NEW.command_id AND command.action='resolve'
            AND NEW.actor_id=command.actor_id AND NEW.correlation_id=command.correlation_id
            AND command.selection->>'decision'=NEW.decision
            AND command.selection->'types' ? m.purpose
            AND c.id=NEW.campaign_id AND c.state='closed' AND c.delivery_paused
            AND c.pause_version=NEW.pause_version AND m.campaign_id=c.id
            AND m.mode='production' AND m.routing='production'
            AND m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL
            AND m.version=NEW.previous_version AND m.pause_version=c.pause_version
            AND m.purpose IN ('receipt','daily_digest','weekly_digest')) THEN
        RAISE EXCEPTION 'Held-message decision requires its exact command and version' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_message_resolution_guard_v1() FROM PUBLIC;
CREATE TRIGGER delivery_message_resolution_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_delivery_message_resolution FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_delivery_message_resolution_guard_v1();

-- Runtime dispatch sees only the release boundary, not session/reason history.
CREATE VIEW public.stewardship_delivery_message_release AS
    SELECT message_id,campaign_id,pause_version FROM public.stewardship_delivery_message_resolution
    WHERE decision='release';
REVOKE ALL ON public.stewardship_delivery_message_release FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_message_released_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_delivery_message_release released
        JOIN public.stewardship_campaign c ON c.id=released.campaign_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        WHERE released.message_id=$1 AND c.state='closed' AND r.mode='production'
            AND c.delivery_paused AND c.pause_version=released.pause_version)
$$;

-- Keep exact semantic cancellation separate from the per-message permission
-- journal. Freeze item versions during capture, and retain each recipient's
-- actual selected subset rather than claiming another recipient's content.
CREATE VIEW public.stewardship_delivery_closed_coverage AS
    SELECT receipt.outbox_id AS message_id,s.campaign_id,'receipt'::text AS purpose,
        'receipt:'||s.id::text AS obligation_key,NULL::uuid AS occurrence_id,
        jsonb_build_object('items',jsonb_build_array(jsonb_build_object(
            'kind','submission','id',s.id,'version',1)),'daily_range',NULL) AS coverage
    FROM public.stewardship_submission_receipt receipt
    JOIN public.stewardship_submission s ON s.id=receipt.submission_id
    WHERE s.mode='live' AND receipt.outbox_id IS NOT NULL
    UNION ALL
    SELECT recipient.outbox_id,p.campaign_id,'daily_digest','schedule:'||p.definition_id::text||':'||o.slot,
        o.id,jsonb_build_object('items','[]'::jsonb,'daily_range',jsonb_build_object(
            'start',s.covered_dates->>0,'end',s.covered_dates->>-1))
    FROM public.stewardship_daily_digest_preparation p
    JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
    JOIN public.stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=s.id
    JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=r.id
    WHERE p.mode='production' AND recipient.outbox_id IS NOT NULL
    UNION ALL
    SELECT recipient.outbox_id,p.campaign_id,'weekly_digest','schedule:'||p.definition_id::text||':'||o.slot,
        o.id,jsonb_build_object('items',(
            SELECT jsonb_agg(jsonb_build_object('kind',selected.kind,'id',selected.item_id,
                    'version',s.item_versions->selected.item_id)
                ORDER BY selected.kind,selected.item_id)
            FROM (SELECT 'item' AS kind,value AS item_id FROM jsonb_array_elements_text(recipient.information)
                UNION ALL SELECT 'correction',value->>0 FROM jsonb_array_elements(recipient.corrections)) selected
        ),'daily_range',NULL)
    FROM public.stewardship_weekly_digest_preparation p
    JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
    JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id
    WHERE p.mode='production' AND recipient.outbox_id IS NOT NULL;
REVOKE ALL ON public.stewardship_delivery_closed_coverage FROM PUBLIC;

-- One semantic resolution owns the union of explicitly cancelled children,
-- never accepted siblings. Keep the individual subsets in the message journal.
CREATE FUNCTION public.stewardship_delivery_closed_slot_coverage_v1(occurrence uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH cancelled AS MATERIALIZED (
        SELECT c.coverage FROM public.stewardship_delivery_closed_coverage c
        JOIN public.stewardship_outbox_message m ON m.id=c.message_id
        WHERE c.occurrence_id=$1 AND m.state='cancelled' AND m.reason='admin_post_close_skip'
          AND EXISTS(SELECT 1 FROM public.stewardship_delivery_message_resolution d
              WHERE d.message_id=m.id AND d.decision='cancel')
    ), items AS (
        SELECT DISTINCT item FROM cancelled
        CROSS JOIN LATERAL jsonb_array_elements(coverage->'items') item
    ) SELECT jsonb_build_object('items',coalesce((
            SELECT jsonb_agg(item ORDER BY item->>'kind',item->>'id') FROM items),'[]'::jsonb),
        'daily_range',(SELECT coverage->'daily_range' FROM cancelled LIMIT 1))
$$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_closed_slot_coverage_v1(uuid) FROM PUBLIC;

-- A semantic skip covers only its frozen inputs. Expose opaque current proof
-- IDs, not the snapshot, item text or versions, to scheduling/dispatch roles.
-- A later version or new live item must remain an obligation, even for the
-- same logical schedule slot after a revision or a future reopen. A later
-- completed automatic snapshot may discharge those new inputs; that must not
-- revive every earlier skipped slot or discard its retained history.
CREATE VIEW public.stewardship_postclose_current AS
    SELECT resolved.id FROM public.stewardship_postclose_resolution resolved
    JOIN public.stewardship_schedule_occurrence o ON o.id=resolved.occurrence_id
    JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
    WHERE (d.kind='daily_digest' AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(resolved.coverage->'items') item
        LEFT JOIN public.stewardship_additional_information i ON i.id=(item->>'id')::uuid
        WHERE item->>'kind' IN ('item','correction')
            AND to_jsonb(stewardship_information_digest_version_v1(i.disposition))
                IS DISTINCT FROM item->'version'))
    OR (d.kind='weekly_digest' AND EXISTS(
        SELECT 1 FROM public.stewardship_weekly_digest_preparation p
        JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
        JOIN public.stewardship_weekly_digest_preparation original ON original.occurrence_id=o.id
        JOIN public.stewardship_weekly_digest_snapshot old ON old.preparation_id=original.id
        WHERE p.campaign_id=resolved.campaign_id AND p.mode=resolved.mode
            AND p.rehearsal_epoch_id IS NOT DISTINCT FROM original.rehearsal_epoch_id
            AND s.submission_watermark>=old.submission_watermark AND s.observed_at>=old.observed_at
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_weekly_manual_request WHERE id=p.id)
            AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                    WHERE f.occurrence_id=p.occurrence_id AND f.mode=p.mode AND f.target='admins'
                        AND f.disposition IN ('delivered','empty'))
                OR EXISTS(SELECT 1 FROM public.stewardship_postclose_resolution later
                    WHERE later.occurrence_id=p.occurrence_id AND later.mode=p.mode))
            AND NOT EXISTS(
                SELECT 1 FROM jsonb_each(s.item_versions) version
                LEFT JOIN public.stewardship_additional_information i ON i.id=version.key::uuid
                WHERE to_jsonb(stewardship_information_digest_version_v1(i.disposition))
                    IS DISTINCT FROM version.value)
            AND NOT EXISTS(
                SELECT 1 FROM public.stewardship_additional_information i
                JOIN public.stewardship_submission submission ON submission.id=i.submission_id
                WHERE submission.campaign_id=resolved.campaign_id AND submission.mode='live'
                    AND submission.campaign_sequence>s.submission_watermark)
    ));
REVOKE ALL ON public.stewardship_postclose_current FROM PUBLIC;

CREATE VIEW public.stewardship_delivery_closed_coverage_summary AS
WITH per_type AS (
    SELECT c.campaign_id,c.purpose,count(*) AS messages,
        sum(jsonb_array_length(c.coverage->'items')) AS items,
        count(*) FILTER(WHERE c.coverage->'daily_range'<>'null'::jsonb) AS daily_ranges,
        encode(sha256(convert_to(string_agg(encode(sha256(convert_to(
            jsonb_build_array(c.message_id,c.obligation_key,c.coverage)::text,'UTF8')),'hex'),''
            ORDER BY c.message_id),'UTF8')),'hex') AS fingerprint
    FROM public.stewardship_delivery_closed_coverage c
    JOIN public.stewardship_outbox_message m ON m.id=c.message_id
    JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.campaign_id
    WHERE m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL
    GROUP BY c.campaign_id,c.purpose
) SELECT r.current_campaign_id AS campaign_id,coalesce(jsonb_object_agg(p.purpose,
    to_jsonb(p)-'campaign_id'-'purpose') FILTER(WHERE p.purpose IS NOT NULL),'{}') AS coverage
    ,(SELECT count(*) FROM (
        SELECT campaign_id,revision_id,phase FROM public.stewardship_daily_digest_preparation
            WHERE mode='production'
        UNION ALL SELECT campaign_id,revision_id,phase FROM public.stewardship_weekly_digest_preparation
            WHERE mode='production'
      ) preparing JOIN public.stewardship_schedule_definition d ON d.current_revision_id=preparing.revision_id
      WHERE preparing.campaign_id=r.current_campaign_id AND preparing.phase NOT IN ('complete','cancelled')) AS preparing
FROM public.stewardship_system_configuration r
LEFT JOIN per_type p ON p.campaign_id=r.current_campaign_id GROUP BY r.current_campaign_id;
REVOKE ALL ON public.stewardship_delivery_closed_coverage_summary FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_closed_digest_v1(occurrence uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT (EXISTS(SELECT 1 FROM public.stewardship_daily_digest_preparation
                WHERE occurrence_id=$1 AND mode='production' AND phase='complete')
        OR EXISTS(SELECT 1 FROM public.stewardship_weekly_digest_preparation
                WHERE occurrence_id=$1 AND mode='production' AND phase='complete'))
      AND EXISTS(SELECT 1 FROM public.stewardship_delivery_digest_message link
        JOIN public.stewardship_delivery_message_resolution resolution ON resolution.message_id=link.outbox_id
        WHERE link.occurrence_id=$1 AND resolution.decision='cancel')
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_delivery_digest_message link
        JOIN public.stewardship_outbox_message m ON m.id=link.outbox_id
        WHERE link.occurrence_id=$1 AND NOT (m.state='delivered' OR m.state='cancelled' AND (
            m.reason='recipient_revoked' OR EXISTS(
                SELECT 1 FROM public.stewardship_delivery_message_resolution resolution
                WHERE resolution.message_id=m.id AND resolution.decision='cancel'))
            -- A failed report to a recipient who is not currently an
            -- Administrator: settled like the recipient_revoked cancellation,
            -- as the digest completion proofs count it.
            OR m.state='permanent_failure' AND NOT EXISTS(
                SELECT 1 FROM public.stewardship_system_configuration runtime
                JOIN public.stewardship_address_rule admin
                    ON admin.configuration_id=runtime.active_configuration_id
                WHERE admin.roles @> '["administrator"]'::jsonb AND admin.email IN (
                    SELECT address FROM public.stewardship_daily_digest_recipient WHERE outbox_id=m.id
                    UNION ALL
                    SELECT address FROM public.stewardship_weekly_digest_recipient WHERE outbox_id=m.id))))
$$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_closed_digest_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_closed_settle_v1(command uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_delivery_control%ROWTYPE;
BEGIN
    SELECT * INTO STRICT intent FROM public.stewardship_delivery_control WHERE id=$1;
    INSERT INTO public.stewardship_postclose_resolution
        (id,actor_id,correlation_id,campaign_id,mode,obligation_key,coverage_digest,
         coverage,reason,occurrence_id,task_id,outbox_id)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,intent.campaign_id,'production',
            covered.obligation_key,encode(sha256(convert_to(covered.coverage::text,'UTF8')),'hex'),
            covered.coverage,intent.reason,NULL,m.task_id,m.id
        FROM public.stewardship_delivery_message_resolution decision
        JOIN public.stewardship_delivery_closed_coverage covered ON covered.message_id=decision.message_id
        JOIN public.stewardship_outbox_message m ON m.id=decision.message_id
        WHERE decision.command_id=intent.id AND decision.decision='cancel' AND covered.purpose='receipt';
    INSERT INTO public.stewardship_schedule_effect
        SELECT DISTINCT pg_current_xact_id(),pg_backend_pid(),o.id,o.version,
            intent.actor_id,intent.correlation_id,'admin_post_close_skip'
        FROM public.stewardship_delivery_message_resolution decision
        JOIN public.stewardship_delivery_closed_coverage covered ON covered.message_id=decision.message_id
        JOIN public.stewardship_schedule_occurrence o ON o.id=covered.occurrence_id
        WHERE decision.command_id=intent.id AND decision.decision='cancel'
            AND o.state IN ('pending','running') AND public.stewardship_delivery_closed_digest_v1(o.id);
    UPDATE public.stewardship_schedule_occurrence o SET
        state='skipped',reason='admin_post_close_skip',version=o.version+1,lease_expires_at=NULL,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id
    FROM public.stewardship_schedule_effect proof
    WHERE proof.transaction_id=pg_current_xact_id() AND proof.backend=pg_backend_pid()
        AND proof.reason='admin_post_close_skip' AND o.id=proof.occurrence_id;
    WITH covered AS (
        SELECT DISTINCT covered.obligation_key,
            public.stewardship_delivery_closed_slot_coverage_v1(o.id) AS coverage,
            o.id AS occurrence_id,o.task_id
        FROM public.stewardship_schedule_effect proof
        JOIN public.stewardship_schedule_occurrence o ON o.id=proof.occurrence_id
        JOIN public.stewardship_delivery_closed_coverage covered ON covered.occurrence_id=o.id
        WHERE proof.transaction_id=pg_current_xact_id() AND proof.backend=pg_backend_pid()
            AND proof.reason='admin_post_close_skip'
    ) INSERT INTO public.stewardship_postclose_resolution
        (id,actor_id,correlation_id,campaign_id,mode,obligation_key,coverage_digest,
         coverage,reason,occurrence_id,task_id,outbox_id)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,intent.campaign_id,'production',
            covered.obligation_key,encode(sha256(convert_to(covered.coverage::text,'UTF8')),'hex'),
            covered.coverage,intent.reason,covered.occurrence_id,covered.task_id,NULL FROM covered;
    DELETE FROM public.stewardship_schedule_effect WHERE transaction_id=pg_current_xact_id()
        AND backend=pg_backend_pid() AND reason='admin_post_close_skip';
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_closed_settle_v1(uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_resolve_closed_v1(command uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_delivery_control%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
BEGIN
    SELECT * INTO STRICT intent FROM public.stewardship_delivery_control WHERE id=$1;
    SELECT * INTO STRICT campaign FROM public.stewardship_campaign WHERE id=intent.campaign_id;
    IF intent.action<>'resolve' OR campaign.state<>'closed' OR NOT campaign.delivery_paused THEN
        RAISE EXCEPTION 'Held-message resolution requires a paused closed campaign' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
        VALUES(gen_random_uuid(),intent.actor_id,intent.correlation_id,
            'delivery_held_'||(intent.selection->>'decision'),intent.id,campaign.id);
    INSERT INTO public.stewardship_delivery_message_resolution
        (id,actor_id,correlation_id,command_id,message_id,campaign_id,previous_version,pause_version,decision)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,
            intent.id,m.id,campaign.id,m.version,campaign.pause_version,intent.selection->>'decision'
        FROM public.stewardship_outbox_message m
        WHERE m.campaign_id=campaign.id AND m.mode='production' AND m.routing='production'
            AND m.purpose IN ('receipt','daily_digest','weekly_digest')
            AND intent.selection->'types' ? m.purpose
            AND m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL;
    IF intent.selection->>'decision'='release' THEN
        UPDATE public.stewardship_outbox_message m SET
            action='release_hold',version=m.version+1,pause_hold_id=NULL,
            actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'postclose_release',intent.id,m.id,m.version)::text,'UTF8')),'hex')
        FROM public.stewardship_delivery_message_resolution decision
        WHERE decision.command_id=intent.id AND decision.message_id=m.id;
    ELSIF intent.selection->>'decision'='cancel' THEN
        UPDATE public.stewardship_outbox_message m SET
            action='cancel_unsent',state='cancelled',version=m.version+1,pause_hold_id=NULL,
            actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'postclose_cancel',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
            reason='admin_post_close_skip',finished_at=statement_timestamp(),
            sealed_substitutions=NULL,sealed_key_id=NULL
        FROM public.stewardship_delivery_message_resolution decision
        WHERE decision.command_id=intent.id AND decision.message_id=m.id;
        UPDATE public.stewardship_task_run t SET state='cancelled',action='safe_cancel',
            version=t.version+1,actor_id=intent.actor_id,correlation_id=intent.correlation_id,
            lease_expires_at=NULL
        WHERE t.state IN ('queued','retry_wait') AND t.root_id IN (
            SELECT m.task_id FROM public.stewardship_delivery_message_resolution decision
            JOIN public.stewardship_outbox_message m ON m.id=decision.message_id
            WHERE decision.command_id=intent.id);
        PERFORM public.stewardship_delivery_closed_settle_v1(intent.id);
    END IF;
    -- A stranded invitation or reminder (stewardship_delivery_stranded) has no
    -- task left to apply the close policy, so apply it here: cancel the unsent
    -- message as campaign_closed, as the mail worker would. Unlike the worker,
    -- which skips only a pending occurrence, also skip one left running by an
    -- ended attempt, since no worker will return to it; a failed occurrence
    -- keeps its truthful failure. The command ID is derived from this command
    -- and message, so the command identifies every message it cancelled. The
    -- target row is rechecked, so a row that changed since the view read it is
    -- left alone rather than failing the command. The preview counted these
    -- toward clearing the pause.
    WITH cancelled AS (
        UPDATE public.stewardship_outbox_message m SET
            state='cancelled',action='cancel_unsent',version=m.version+1,pause_hold_id=NULL,
            actor_id=intent.actor_id,correlation_id=intent.correlation_id,
            command_id=md5('postclose_stranded:'||intent.id::text||':'||m.id::text)::uuid,
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'postclose_stranded',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
            reason='campaign_closed',finished_at=statement_timestamp(),
            sealed_substitutions=NULL,sealed_key_id=NULL
        FROM public.stewardship_delivery_stranded stranded
        WHERE stranded.message_id=m.id AND stranded.campaign_id=campaign.id
            AND m.version=stranded.version AND m.state IN ('pending','retry_wait')
            AND m.pause_hold_id IS NOT NULL
        RETURNING m.id
    ) INSERT INTO public.stewardship_schedule_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),o.id,o.version,
            intent.actor_id,intent.correlation_id,'campaign_closed'
        FROM cancelled JOIN public.stewardship_schedule_occurrence o ON o.outbox_id=cancelled.id
        WHERE o.state IN ('pending','running');
    UPDATE public.stewardship_schedule_occurrence o SET
        state='skipped',reason='campaign_closed',version=o.version+1,lease_expires_at=NULL,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id
    FROM public.stewardship_schedule_effect proof
    WHERE proof.transaction_id=pg_current_xact_id() AND proof.backend=pg_backend_pid()
        AND proof.reason='campaign_closed' AND o.id=proof.occurrence_id;
    DELETE FROM public.stewardship_schedule_effect WHERE transaction_id=pg_current_xact_id()
        AND backend=pg_backend_pid() AND reason='campaign_closed';
    -- Clear only when every held/submitting/unknown row is gone. Releasing a
    -- selected type does not grant any other held type permission to dispatch.
    IF EXISTS(SELECT 1 FROM public.stewardship_delivery_control_inventory current
        WHERE current.campaign_id=campaign.id
            AND (inventory->>'held')::bigint=0
            AND (inventory->>'submitting')::bigint=0
            AND (inventory->>'unknown')::bigint=0) THEN
        INSERT INTO public.stewardship_campaign_control(id,campaign_id,request_id,action,
            expected_version,expected_runtime_version,reason,occurred_at,actor_id,correlation_id)
        VALUES(intent.control_id,campaign.id,intent.id,'resume',intent.expected_campaign_version,
            intent.expected_runtime_version,intent.reason,public.stewardship_campaign_now_v1(),
            intent.actor_id,intent.correlation_id);
    END IF;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_resolve_closed_v1(uuid) FROM PUBLIC;
