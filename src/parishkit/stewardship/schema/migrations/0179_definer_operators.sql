-- Frozen forward migration file 0179 (the repository-wide file sequence):
-- the SECURITY DEFINER functions write their jsonb and containment
-- operators schema-qualified (#2166). This file is installed by the Django
-- migration that names it in FROZEN_SQL and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks each replaced baseline function's copy here against the
-- baseline.
--
-- An unqualified operator is resolved through search_path like a function
-- name, and PostgreSQL picks the closest argument-type match among every
-- schema on the path, so pg_catalog's place first in the functions' fixed
-- search_path does not by itself keep a look-alike in public from winning.
-- No runtime login can create one (provisioning revokes CREATE, admission
-- and the upgrade check refuse it), so this is defense in depth for the
-- functions that run with their owner's rights: each of the fourteen
-- installed definer functions that used ?, ?|, ?&, @> or <@ unqualified is
-- replaced with its latest body (named above each statement), unchanged
-- except that each such operator is written OPERATOR(pg_catalog.x). The
-- invoker functions are left as they are (#2166 step 4).
--
-- CREATE OR REPLACE keeps each function's owner and grants but resets the
-- attributes it does not name, so each is altered SECURITY DEFINER again;
-- the header keeps its fixed search_path, and EXECUTE stays revoked from
-- PUBLIC. No stored row changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- stewardship_added_member_candidates_v1, as 0141_added_member_links.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_added_member_candidates_v1(
    input_snapshot uuid, input_campaign uuid)
RETURNS TABLE(family_id uuid, proposal_id uuid, proposed_key text, member_key text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
#variable_conflict use_column
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker reads added Member links' USING ERRCODE='42501';
    END IF;
    RETURN QUERY
    WITH entered AS MATERIALIZED (
        SELECT DISTINCT ON (s.family_id,p.entity_key) s.family_id,
            p.id AS proposal_id,p.entity_key::text AS proposed_key,
            (SELECT min(f.family_version) FROM public.stewardship_submission f
                WHERE f.family_id=s.family_id AND f.mode='live'
                  AND (f.answers->'proposed_members') OPERATOR(pg_catalog.?) p.entity_key) AS first_version
        FROM public.stewardship_proposed_change p
        JOIN public.stewardship_submission s ON s.id=p.submission_id
        WHERE s.campaign_id=input_campaign AND s.mode='live'
          AND p.entity_kind='proposed_member' AND p.field='new_member'
          AND p.execution='resolved_external'
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_added_member_link l
              WHERE l.family_id=s.family_id AND l.proposed_key=p.entity_key)
        ORDER BY s.family_id,p.entity_key,s.family_version DESC
    ), single AS MATERIALIZED (
        SELECT e.* FROM entered e
        WHERE e.first_version IS NOT NULL
          AND (SELECT count(*) FROM entered o WHERE o.family_id=e.family_id)=1
    ), appeared AS MATERIALIZED (
        SELECT g.family_id,g.proposal_id,g.proposed_key,
            membership.source_key::text AS member_key
        FROM single g
        JOIN public.stewardship_family_campaign family ON family.id=g.family_id
        JOIN public.stewardship_source_member member
            ON member.family_key=family.family_duid::text
        JOIN public.stewardship_snapshot_member membership
            ON membership.payload_id=member.id AND membership.snapshot_id=input_snapshot
        WHERE member.canonical::jsonb->'active'='true'::jsonb
          AND member.canonical::jsonb->'deceased'='false'::jsonb
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_added_member_link l
              WHERE l.family_id=g.family_id AND l.member_key=membership.source_key::text)
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission h
              WHERE h.family_id=g.family_id AND h.mode='live'
                AND h.family_version<=g.first_version
                AND (h.answers->'members') OPERATOR(pg_catalog.?) membership.source_key::text)
    )
    SELECT a.family_id,a.proposal_id,a.proposed_key,a.member_key FROM appeared a
    WHERE (SELECT count(*) FROM appeared o WHERE o.family_id=a.family_id)=1
    ORDER BY a.family_id;
END $$;
ALTER FUNCTION public.stewardship_added_member_candidates_v1(input_snapshot uuid, input_campaign uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_added_member_candidates_v1(input_snapshot uuid, input_campaign uuid) FROM PUBLIC;

-- stewardship_address_leader_scope_v1, as 0167_address_leader_scope.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_address_leader_scope_v1(address_text text, actor_uuid uuid)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'A user''s Ministries are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'A user''s Ministries require an Administrator' USING ERRCODE='42501';
    END IF;
    IF address_text IS NULL OR length(address_text) NOT BETWEEN 3 AND 254
       OR strpos(address_text,'@')=0 THEN
        RAISE EXCEPTION 'Invalid email address' USING ERRCODE='22023';
    END IF;
    RETURN (
        SELECT coalesce(jsonb_agg(DISTINCT leader.ministry_duid ORDER BY leader.ministry_duid),
            '[]'::jsonb)
        FROM public.stewardship_system_configuration r
        JOIN public.stewardship_address_rule a
            ON a.configuration_id=r.active_configuration_id AND a.email=lower(address_text)
        JOIN public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(address_text)
        WHERE coalesce(a.roles,'[]'::jsonb) OPERATOR(pg_catalog.?|) ARRAY['administrator','staff','ministry_leader']);
END $$;
ALTER FUNCTION public.stewardship_address_leader_scope_v1(address_text text, actor_uuid uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_address_leader_scope_v1(address_text text, actor_uuid uuid) FROM PUBLIC;

-- stewardship_delivery_address_retries_v1, as 0077_address_retry.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_delivery_address_retries_v1(message_uuid uuid, actor_uuid uuid)
RETURNS TABLE(envelope_position integer, outcome text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE newest public.stewardship_outbox_render%ROWTYPE; latest uuid;
    follow public.stewardship_outbox_address_retry%ROWTYPE;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email address follow-ups are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email address follow-ups require an Administrator' USING ERRCODE='42501';
    END IF;
    -- The attempt 0068's outcomes read reads: the newest rendering's
    -- latest finished attempt.
    SELECT * INTO newest FROM public.stewardship_outbox_render r
     WHERE r.message_id=message_uuid ORDER BY r.created_at DESC, r.id DESC LIMIT 1;
    IF newest.id IS NULL THEN RETURN; END IF;
    SELECT e.id INTO latest FROM public.stewardship_outbox_event e
     WHERE e.render_id=newest.id AND e.message_id=message_uuid
       AND e.previous_state='submitting'
     ORDER BY e.version DESC LIMIT 1;
    IF latest IS NULL THEN RETURN; END IF;
    SELECT * INTO follow FROM public.stewardship_outbox_address_retry a
     WHERE a.source_event_id=latest AND a.message_id=message_uuid AND a.render_id=newest.id;
    IF follow.id IS NULL THEN RETURN; END IF;
    RETURN QUERY
        SELECT item.value::integer,
               CASE WHEN follow.refused OPERATOR(pg_catalog.@>) item.value::jsonb THEN 'refused_permanently'
                    WHEN NOT follow.remaining OPERATOR(pg_catalog.@>) item.value::jsonb THEN 'delivered'
                    WHEN follow.state IN ('waiting','submitting') THEN 'retrying'
                    WHEN follow.state='unknown' THEN 'retry_unknown'
                    ELSE 'refused_for_now' END
          FROM jsonb_array_elements_text(follow.positions) AS item(value)
         ORDER BY 1;
END $$;
ALTER FUNCTION public.stewardship_delivery_address_retries_v1(message_uuid uuid, actor_uuid uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_address_retries_v1(message_uuid uuid, actor_uuid uuid) FROM PUBLIC;

-- stewardship_delivery_control_guard_v1, as 0161_report_conversion.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_delivery_control_guard_v1() RETURNS trigger
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
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Delivery control requires a live Admin session' USING ERRCODE='42501';
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
                WHERE value IN ('receipt','daily_digest','weekly_digest','admin_report')) types;
        SELECT coalesce(sum((current_inventory->'types'->kind->>'held')::bigint),0) INTO selected_count
            FROM jsonb_array_elements_text(selected_types) kind;
        SELECT coverage,s.preparing INTO current_coverage,preparing
            FROM public.stewardship_delivery_closed_coverage_summary s WHERE campaign_id=campaign.id;
        SELECT coalesce(jsonb_object_agg(key,value),'{}') INTO current_coverage
            FROM jsonb_each(current_coverage) WHERE selected_types OPERATOR(pg_catalog.?) key;
        clears_pause:=(current_inventory->>'held')::bigint
                =selected_count+(current_inventory->>'stranded')::bigint
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
ALTER FUNCTION public.stewardship_delivery_control_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_control_guard_v1() FROM PUBLIC;

-- stewardship_delivery_ids_for_address_v1, as 0116_sent_mail_recipients.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_delivery_ids_for_address_v1(address_text text, actor_uuid uuid)
RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email recipients are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email recipients require an Administrator' USING ERRCODE='42501';
    END IF;
    IF address_text IS NULL OR length(address_text) NOT BETWEEN 3 AND 254
       OR strpos(address_text,'@')=0 THEN
        RAISE EXCEPTION 'Invalid email address' USING ERRCODE='22023';
    END IF;
    RETURN QUERY
        SELECT DISTINCT r.message_id
          FROM public.stewardship_outbox_render r
         WHERE (lower((r.intended_recipients)::text))::jsonb
               OPERATOR(pg_catalog.@>) jsonb_build_array(lower(address_text));
END $$;
ALTER FUNCTION public.stewardship_delivery_ids_for_address_v1(address_text text, actor_uuid uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_ids_for_address_v1(address_text text, actor_uuid uuid) FROM PUBLIC;

-- stewardship_delivery_recipient_outcomes_v1, as 0068_mail_message_address_outcomes.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_delivery_recipient_outcomes_v1(message_uuid uuid, actor_uuid uuid)
RETURNS TABLE(address text, outcome text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE newest public.stewardship_outbox_render%ROWTYPE; latest uuid; result jsonb;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email address outcomes are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email address outcomes require an Administrator' USING ERRCODE='42501';
    END IF;
    -- The same newest rendering 0058 reads.
    SELECT * INTO newest FROM public.stewardship_outbox_render r
     WHERE r.message_id=message_uuid ORDER BY r.created_at DESC, r.id DESC LIMIT 1;
    IF newest.id IS NULL THEN RETURN; END IF;
    -- Its latest finished attempt: the newest event that left submitting.
    -- Positions in its evidence refer to exactly this rendering's envelope.
    SELECT e.id INTO latest FROM public.stewardship_outbox_event e
     WHERE e.render_id=newest.id AND e.message_id=message_uuid
       AND e.previous_state='submitting'
     ORDER BY e.version DESC LIMIT 1;
    IF latest IS NULL THEN RETURN; END IF;
    result:=public.stewardship_family_smtp_result_v1(latest);
    IF result IS NULL THEN RETURN; END IF;
    RETURN QUERY
        SELECT item.address,
               CASE WHEN result->'permanent' OPERATOR(pg_catalog.@>) to_jsonb(item.position-1) THEN 'refused_permanently'
                    WHEN result->'transient' OPERATOR(pg_catalog.@>) to_jsonb(item.position-1) THEN 'refused_for_now'
                    WHEN result->>'status'='accepted' THEN 'delivered'
                    WHEN result->>'status'='delivery_unknown' THEN 'unknown'
                    ELSE 'not_delivered' END
          FROM jsonb_array_elements_text(newest.routed_recipients)
               WITH ORDINALITY AS item(address,position)
         ORDER BY item.position;
END $$;
ALTER FUNCTION public.stewardship_delivery_recipient_outcomes_v1(message_uuid uuid, actor_uuid uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_recipient_outcomes_v1(message_uuid uuid, actor_uuid uuid) FROM PUBLIC;

-- stewardship_delivery_resolve_closed_v1, as 0161_report_conversion.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_delivery_resolve_closed_v1(command uuid)
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
            AND m.purpose IN ('receipt','daily_digest','weekly_digest','admin_report')
            AND intent.selection->'types' OPERATOR(pg_catalog.?) m.purpose
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
            command_id=substr(encode(sha256(convert_to(
                'postclose_stranded:'||intent.id::text||':'||m.id::text,'UTF8')),'hex'),1,32)::uuid,
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
ALTER FUNCTION public.stewardship_delivery_resolve_closed_v1(command uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_resolve_closed_v1(command uuid) FROM PUBLIC;

-- stewardship_leader_email_facts_v1, as 0120_leader_emails.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_leader_email_facts_v1(addresses jsonb)
    RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    runtime public.stewardship_system_configuration%ROWTYPE;
    leaders jsonb;
    scope bigint[];
    selection jsonb;
BEGIN
    SELECT * INTO runtime FROM public.stewardship_system_configuration;
    WITH led AS MATERIALIZED (
        SELECT DISTINCT leader.email, leader.ministry_duid::bigint AS duid
        FROM public.stewardship_ministry_leaders_v1() leader
    ), users AS MATERIALIZED (
        SELECT rule.email FROM public.stewardship_address_rule rule
        WHERE rule.configuration_id=runtime.active_configuration_id
          AND rule.role='ministry_leader'
          AND (addresses IS NULL OR addresses OPERATOR(pg_catalog.?) rule.email)
    )
    SELECT coalesce(jsonb_agg(jsonb_build_object('email', users.email, 'duids',
            coalesce((SELECT jsonb_agg(led.duid ORDER BY led.duid) FROM led
                      WHERE led.email=users.email), '[]'::jsonb))
        ORDER BY users.email), '[]'::jsonb)
      INTO leaders FROM users;
    SELECT array_agg(DISTINCT duid.value::bigint) INTO scope
    FROM jsonb_array_elements(leaders) item
    CROSS JOIN LATERAL jsonb_array_elements_text(item->'duids') duid(value);
    IF runtime.current_campaign_id IS NOT NULL AND scope IS NOT NULL THEN
        selection := public.stewardship_ministry_followup_v1(
            runtime.current_campaign_id,
            '{"search": "", "ministry": "", "action": "any", "state": "unresolved", "outcome": "any", "history": "current", "sort": "newest", "assignee": "any", "presence": "current"}'::jsonb,
            false, scope, NULL, NULL, NULL, 0);
    END IF;
    RETURN (
        SELECT coalesce(jsonb_agg(jsonb_build_object('email', item->>'email',
            'ministries', coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'duid', (listed.value->>'duid')::integer,
                    'name', listed.value->>'name',
                    'join', (SELECT count(*) FROM jsonb_array_elements(selection->'rows') hit
                             WHERE hit->'ministry_duid'=listed.value->'duid'
                               AND hit->>'action'='join'),
                    'leave', (SELECT count(*) FROM jsonb_array_elements(selection->'rows') hit
                              WHERE hit->'ministry_duid'=listed.value->'duid'
                                AND hit->>'action'='leave'))
                    ORDER BY listed.position)
                FROM jsonb_array_elements(coalesce(selection->'ministries', '[]'::jsonb))
                    WITH ORDINALITY listed(value, position)
                WHERE item->'duids' OPERATOR(pg_catalog.@>) jsonb_build_array(listed.value->'duid')), '[]'::jsonb))
            ORDER BY item->>'email'), '[]'::jsonb)
        FROM jsonb_array_elements(leaders) item);
END $$;
ALTER FUNCTION public.stewardship_leader_email_facts_v1(addresses jsonb) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_facts_v1(addresses jsonb) FROM PUBLIC;

-- stewardship_leaders_without_user_v1, as 0091_drop_ministry_activity.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_leaders_without_user_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    current_configuration uuid; current_values jsonb; current_snapshot uuid;
    leader_keys text[];
    answer jsonb := jsonb_build_object('leaders', '[]'::jsonb, 'unreachable', 0);
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session whose user is an Administrator now,
    -- exactly as stewardship_member_names_for_v1 (0053) decides it.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, true) THEN
        RAISE EXCEPTION 'Leader list requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- The current campaign, its configuration and the promoted snapshot, as
    -- 0042 reads them; no campaign asking about Ministries, or no promoted
    -- source, means no leaders.
    SELECT runtime.active_configuration_id, campaign.values, pointer.snapshot_id
      INTO current_configuration, current_values, current_snapshot
    FROM public.stewardship_system_configuration runtime
    JOIN public.stewardship_campaign_configuration campaign
        ON campaign.record_id=runtime.current_campaign_id
       AND campaign.configuration_id=runtime.active_configuration_id
    CROSS JOIN public.stewardship_source_current pointer
    WHERE pointer.singleton AND campaign.values->'modules' OPERATOR(pg_catalog.?) 'ministry';
    IF current_snapshot IS NOT NULL THEN
        SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
          INTO leader_keys
        FROM jsonb_array_elements_text(
            public.stewardship_ministry_leader_roles_v1(current_values)) role(name);
        WITH roster AS MATERIALIZED (
            SELECT payload.member_key, payload.ministry_key, payload.canonical::jsonb AS doc
            FROM public.stewardship_snapshot_roster rm
            JOIN public.stewardship_source_roster payload ON payload.id=rm.payload_id
            WHERE rm.snapshot_id=current_snapshot
        ), labels AS MATERIALIZED (
            SELECT DISTINCT roster.doc->>'ministryRoleName' AS label FROM roster
        ), leader_labels AS MATERIALIZED (
            SELECT labels.label FROM labels
            WHERE public.stewardship_ministry_leader_role_key_v1(labels.label)=ANY(leader_keys)
        ), held AS MATERIALIZED (
            SELECT roster.member_key, roster.ministry_key
            FROM roster JOIN leader_labels ON leader_labels.label=roster.doc->>'ministryRoleName'
            WHERE roster.doc->'schema_version'='1'::jsonb
              AND roster.doc->'current'='true'::jsonb
        ), led AS MATERIALIZED (
            -- 0042's joins and conditions, with the contact record optional.
            SELECT member.source_key AS member_key, duid.ministry AS ministry_duid,
                parsed.member_doc, parsed.family_doc,
                CASE WHEN contact.canonical::jsonb->'schema_version'='1'::jsonb
                    THEN contact.canonical::jsonb->'emails' END AS emails
            FROM held
            JOIN public.stewardship_snapshot_member mm
                ON mm.snapshot_id=current_snapshot AND mm.source_key=held.member_key
            JOIN public.stewardship_source_member member ON member.id=mm.payload_id
            JOIN public.stewardship_snapshot_family fm
                ON fm.snapshot_id=current_snapshot AND fm.source_key=member.family_key
            JOIN public.stewardship_source_family family ON family.id=fm.payload_id
            JOIN public.stewardship_snapshot_ministry tm
                ON tm.snapshot_id=current_snapshot AND tm.source_key=held.ministry_key
            JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
            LEFT JOIN public.stewardship_snapshot_contact cm
                ON cm.snapshot_id=current_snapshot AND cm.source_key='member:' || member.source_key
            LEFT JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
            CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
                family.canonical::jsonb AS family_doc,
                ministry.canonical::jsonb AS ministry_doc) parsed
            CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
                THEN ministry.source_key::bigint END AS ministry) duid
            WHERE parsed.member_doc->'schema_version'='1'::jsonb
              AND parsed.family_doc->'schema_version'='1'::jsonb
              AND parsed.ministry_doc->'schema_version'='1'::jsonb
              AND parsed.member_doc->'active'='true'::jsonb
              AND parsed.family_doc->'active'='true'::jsonb
              AND parsed.ministry_doc->'catalog_present'='true'::jsonb
              AND duid.ministry BETWEEN 1 AND 2147483647
              AND current_values->'ministry_duids' OPERATOR(pg_catalog.@>) jsonb_build_array(duid.ministry)
        ), people AS MATERIALIZED (
            -- One row per leader Member: its "Last, first" name, whether it
            -- heads its household, its DUID and its listed addresses.
            SELECT DISTINCT ON (led.member_key) led.member_key,
                concat_ws(', ',
                    nullif(btrim(led.member_doc->>'lastName'), ''),
                    nullif(btrim(led.member_doc->>'firstName'), '')) AS name,
                EXISTS (SELECT 1 FROM jsonb_array_elements_text(
                            CASE WHEN jsonb_typeof(led.family_doc->'active_head_duids')='array'
                                THEN led.family_doc->'active_head_duids' END) head(duid)
                        WHERE head.duid=led.member_key) AS head,
                CASE WHEN led.member_key ~ '^[0-9]{1,18}$'
                    THEN led.member_key::bigint END AS duid,
                CASE WHEN jsonb_typeof(led.emails)='array' THEN led.emails
                    ELSE '[]'::jsonb END AS emails
            FROM led ORDER BY led.member_key
        ), addresses AS MATERIALIZED (
            -- Each valid address a leader Member lists, as sign-in matches it.
            SELECT DISTINCT people.member_key, lower(btrim(listed.value->>'value')) AS email
            FROM people CROSS JOIN LATERAL jsonb_array_elements(people.emails) listed(value)
            WHERE listed.value->'valid'='true'::jsonb
              AND jsonb_typeof(listed.value->'value')='string'
              AND length(btrim(listed.value->>'value')) BETWEEN 3 AND 254
        ), unused AS MATERIALIZED (
            -- Only addresses that are not a user in the active configuration.
            SELECT addresses.member_key, addresses.email FROM addresses
            WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_address_rule rule
                WHERE rule.configuration_id=current_configuration
                  AND rule.email=addresses.email)
        ), ranked AS (
            SELECT unused.email, people.name, people.duid, row_number() OVER (
                PARTITION BY unused.email
                ORDER BY people.name='', people.head DESC, people.name,
                    people.duid NULLS LAST) AS position
            FROM unused JOIN people ON people.member_key=unused.member_key
        ), answered AS (
            SELECT ranked.email, ranked.name, ranked.duid,
                (SELECT jsonb_agg(DISTINCT led.ministry_duid ORDER BY led.ministry_duid)
                 FROM unused JOIN led ON led.member_key=unused.member_key
                 WHERE unused.email=ranked.email) AS ministries
            FROM ranked WHERE ranked.position=1
        )
        SELECT jsonb_build_object(
            'leaders', coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'email', answered.email, 'name', answered.name,
                    'member_duid', answered.duid, 'ministries', answered.ministries)
                ORDER BY answered.name, answered.duid NULLS LAST, answered.email)
                FROM answered), '[]'::jsonb),
            'unreachable', (SELECT count(*) FROM people
                WHERE NOT EXISTS (SELECT 1 FROM addresses
                                  WHERE addresses.member_key=people.member_key)))
          INTO answer;
    END IF;
    -- The trace: who listed and how many rows came back, never an address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'user_leaders_listed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded',
            'count',jsonb_array_length(answer->'leaders')));
    RETURN answer;
END
$$;
ALTER FUNCTION public.stewardship_leaders_without_user_v1(admin_session text, correlation uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_leaders_without_user_v1(admin_session text, correlation uuid) FROM PUBLIC;

-- stewardship_ministry_leader_coverage_v1, as 0169_ministry_leader_coverage.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_leader_coverage_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    answer jsonb;
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session, exactly as 0054's leaders read (since
    -- 0057, by the key's digest) decides it, whose user is Staff or an
    -- Administrator now.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, false) THEN
        RAISE EXCEPTION 'Leader coverage requires a live Staff or Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- The leaders read once (it parses the current roster), then the few
    -- leader addresses matched against users and sign-ins.
    WITH leaders AS MATERIALIZED (
        SELECT DISTINCT leader.ministry_duid, leader.email
        FROM public.stewardship_ministry_leaders_v1() leader
    ), users AS MATERIALIZED (
        SELECT DISTINCT leaders.email
        FROM leaders
        CROSS JOIN public.stewardship_system_configuration runtime
        JOIN public.stewardship_address_rule rule
            ON rule.configuration_id=runtime.active_configuration_id
           AND rule.email=leaders.email
        WHERE coalesce(rule.roles,'[]'::jsonb) OPERATOR(pg_catalog.?|) ARRAY['administrator','staff','ministry_leader']
    ), signed AS MATERIALIZED (
        SELECT users.email FROM users
        WHERE EXISTS (SELECT 1 FROM public.stewardship_portal_user identity
            WHERE lower(identity.email)=users.email AND NOT identity.disabled
              AND EXISTS (SELECT 1 FROM public.stewardship_audit_event login_event
                  WHERE login_event.event_type='admin_login'
                    AND login_event.actor_id=identity.id))
    ), counted AS (
        SELECT leaders.ministry_duid, count(*) AS leaders,
            count(*) FILTER (WHERE leaders.email IN (SELECT users.email FROM users)) AS users,
            count(*) FILTER (WHERE leaders.email IN (SELECT signed.email FROM signed)) AS signed_in
        FROM leaders GROUP BY leaders.ministry_duid
    )
    SELECT jsonb_build_object('ministries', coalesce(jsonb_agg(jsonb_build_object(
            'ministry', counted.ministry_duid, 'leaders', counted.leaders,
            'users', counted.users, 'signed_in', counted.signed_in)
        ORDER BY counted.ministry_duid), '[]'::jsonb))
      INTO answer
    FROM counted;
    -- The trace: who read and how many Ministries came back, never an address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'ministry_leader_coverage_viewed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded',
            'count',jsonb_array_length(answer->'ministries')));
    RETURN answer;
END
$$;
ALTER FUNCTION public.stewardship_ministry_leader_coverage_v1(admin_session text, correlation uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leader_coverage_v1(admin_session text, correlation uuid) FROM PUBLIC;

-- stewardship_ministry_leader_scope_v1, as 0052_users_swap.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_leader_scope_v1(user_uuid uuid)
    RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT coalesce(jsonb_agg(DISTINCT leader.ministry_duid ORDER BY leader.ministry_duid),
        '[]'::jsonb)
    FROM public.stewardship_portal_user u
    CROSS JOIN public.stewardship_system_configuration r
    LEFT JOIN public.stewardship_address_rule a
        ON a.configuration_id=r.active_configuration_id AND a.email=lower(u.email)
    JOIN public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(u.email)
    WHERE u.id=user_uuid AND NOT u.disabled
      AND coalesce(a.roles,'[]'::jsonb) OPERATOR(pg_catalog.?|) ARRAY['administrator','staff','ministry_leader']
$$;
ALTER FUNCTION public.stewardship_ministry_leader_scope_v1(user_uuid uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leader_scope_v1(user_uuid uuid) FROM PUBLIC;

-- stewardship_occurrence_guard_v1, as 0161_report_conversion.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_occurrence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE d stewardship_schedule_definition%ROWTYPE; c stewardship_campaign%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; t stewardship_task_run%ROWTYPE;
    instant timestamptz := stewardship_campaign_now_v1();
    digest_completed boolean := false;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('occurrences',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Occurrence history requires exceptional retention' USING ERRCODE='23514';
    END IF;
    SELECT * INTO d FROM stewardship_schedule_definition WHERE id=NEW.definition_id FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF NOT EXISTS(SELECT 1 FROM stewardship_schedule_revision WHERE id=NEW.revision_id AND record_id=d.id AND campaign_id=d.campaign_id)
       OR NEW.target='' OR NEW.slot='' OR NEW.occurrence_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid occurrence identity' USING ERRCODE='23514'; END IF;
    IF (TG_OP='INSERT' OR NEW.state IN ('pending','running'))
       AND NEW.production_cycle IS DISTINCT FROM (CASE WHEN NEW.mode='production' THEN c.production_cycle ELSE 0 END) THEN
        RAISE EXCEPTION 'Occurrence belongs to a retired Production cycle' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('succeeded','failed','skipped','coalesced') AND EXISTS (
        SELECT 1 FROM stewardship_outbox_message m WHERE m.id=NEW.outbox_id
          AND public.stewardship_occurrence_delivery_conflict_v1(NEW.state,m.state)
    ) THEN RAISE EXCEPTION 'Occurrence outcome contradicts terminal delivery'
        USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NOT public.stewardship_initial_recovery_valid_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Initial recovery requires a new deliverability transition'
                USING ERRCODE='23514';
        END IF;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.fence<>0 OR NEW.attempts<>0
           OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR NEW.mode<>r.mode OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
           -- A report email (#1786) may send before its campaign starts and
           -- after it closes, until it is archived (decision 9).
           OR (d.kind='report' AND NOT ((NEW.mode='testing' AND c.state='draft')
               OR (NEW.mode='production' AND c.state IN ('scheduled','active','closed'))))
           OR (d.kind<>'report' AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
               AND ((instant>=p.starts_at AND instant<p.ends_at
                   AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                   OR (NEW.mode='production' AND c.state='closed'
                       AND NEW.due_at>=p.starts_at AND NEW.due_at<p.ends_at))))
           OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
               AND NOT public.stewardship_catchup_materializing_v1(c.id,NEW.actor_id,NEW.correlation_id))
           OR EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
           OR EXISTS (SELECT 1 FROM stewardship_postclose_resolution resolved
               JOIN public.stewardship_postclose_current covered ON covered.id=resolved.id
               WHERE resolved.campaign_id=c.id AND resolved.mode=NEW.mode
                   AND resolved.obligation_key='schedule:'||d.id::text||':'||NEW.slot)
           -- A report's skipped send (#1658) is never planned, by any writer.
           OR (d.kind='report' AND EXISTS (
               SELECT 1 FROM stewardship_schedule_revision skipping WHERE skipping.id=NEW.revision_id
                   AND coalesce(skipping.values->'skip_dates' OPERATOR(pg_catalog.?) NEW.slot,false))) THEN
            RAISE EXCEPTION 'Occurrence creation is not admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF session_user='pk_stewardship_scheduler' AND (
            OLD.state<>'pending' OR NEW.state NOT IN ('skipped','coalesced')
            OR OLD.task_id IS NOT NULL OR OLD.outbox_id IS NOT NULL
            OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
            OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode
            OR r.restore_review_required
            OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate
                WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
            OR (NEW.state='coalesced' AND NOT EXISTS(
                SELECT 1 FROM stewardship_schedule_occurrence selected
                WHERE selected.id=NEW.replacement_id AND selected.target=NEW.target
                  AND selected.mode=NEW.mode AND selected.state='pending'
                  AND selected.task_id IS NULL AND selected.outbox_id IS NULL
            ))
        ) THEN RAISE EXCEPTION 'Scheduler may only reconcile unallocated pending work'
            USING ERRCODE='23514'; END IF;
        IF (OLD.state='pending' AND NEW.state NOT IN ('pending','running','skipped','coalesced'))
           OR (OLD.state='running' AND NEW.state NOT IN ('running','pending','delivery_unknown','succeeded','failed','skipped','coalesced'))
           OR (OLD.state='delivery_unknown' AND NEW.state NOT IN ('succeeded','pending','failed'))
           OR (OLD.state='failed' AND NEW.state NOT IN ('pending','skipped'))
           OR OLD.state IN ('succeeded','skipped','coalesced') THEN
            RAISE EXCEPTION 'Invalid occurrence transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='running' THEN
            -- Branch before planning private report reads: SQL boolean order is
            -- not a privilege boundary for unrelated Family mail owners.
            IF d.kind='report' THEN
                digest_completed:=public.stewardship_report_email_completion_v1(to_jsonb(NEW));
            END IF;
        END IF;
        IF NEW.state='running' AND NOT digest_completed THEN
            SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
            IF NOT FOUND OR t.state<>'running' OR t.worker_id<>NEW.worker_id OR t.fence<>NEW.fence OR t.lease_expires_at<=clock_timestamp()
               OR NEW.lease_expires_at>t.lease_expires_at OR NEW.heartbeat_at>clock_timestamp()
               OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
               OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode OR r.restore_review_required
               OR NOT ((t.task_type='schedule_occurrence' AND t.domain_request_id IS NOT DISTINCT FROM NEW.id)
                   OR (t.task_type='family_mail_prepare' AND EXISTS (
                       SELECT 1 FROM public.stewardship_family_mail_preparation q
                       WHERE q.id=t.domain_request_id AND q.task_id=t.root_id
                         AND q.occurrence_id=NEW.id AND q.mode=NEW.mode))
                   OR (t.task_type='outbox_delivery' AND EXISTS (
                       SELECT 1 FROM public.stewardship_outbox_message mail
                       WHERE mail.id=t.domain_request_id AND mail.task_id=t.root_id
                         AND mail.id=NEW.outbox_id AND mail.semantic_key=NEW.id
                         AND mail.mode=NEW.mode AND mail.family_id::text=substring(NEW.target FROM 8))))
               OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
                   AND ((instant>=p.starts_at AND instant<p.ends_at
                       AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                       OR (NEW.mode='production' AND c.state='closed' AND d.kind='report')))
               -- Not yet due. Only Family mail preparation of a Production
               -- occurrence may claim it early, within the two-hour lead
               -- window (BG-12, #447). Every other claim, including a
               -- delivery claim, and the dispatch guard still wait for it.
               OR (NEW.due_at>instant AND NOT (t.task_type='family_mail_prepare'
                   AND NEW.mode='production' AND NEW.due_at<=instant+interval '2 hours'))
               OR (NEW.mode='production' AND c.delivery_paused)
               OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
               OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
               OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h WHERE h.definition_id=d.id AND h.mode=NEW.mode
                   AND h.target=NEW.target AND h.slot=NEW.slot AND h.state IN ('unreviewed','assumed_delivered'))
               OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
                RAISE EXCEPTION 'Occurrence claim requires current fenced work' USING ERRCODE='23514'; END IF;
        END IF;
        IF OLD.state='running' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NEW.fence<>OLD.fence) THEN
            RAISE EXCEPTION 'Occurrence worker identity changed' USING ERRCODE='23514'; END IF;
        IF OLD.state='running' AND NOT (
            (NEW.state='skipped' OR (NEW.state='coalesced' AND NEW.reason IN (
                'missed_family_recovery','missed_daily_recovery','missed_weekly_recovery',
                'missed_report_recovery')))
            AND public.stewardship_schedule_effect_v1(
                OLD.id,OLD.version,NEW.actor_id,NEW.correlation_id,NEW.reason)
        ) AND NOT EXISTS(SELECT 1 FROM stewardship_task_run owner_task WHERE owner_task.id=OLD.task_id
            AND ((owner_task.state='running' AND owner_task.worker_id=NEW.actor_id AND owner_task.fence=NEW.fence AND owner_task.lease_expires_at>clock_timestamp())
                OR (owner_task.state IN ('abandoned','cancelled','succeeded','failed') AND owner_task.fence>=OLD.fence AND NEW.actor_id IS NOT NULL
                    AND NEW.reason IN ('recovery_retry','recovery_unknown','recovery_complete','recovery_fail','recovery_skip','recovery_coalesce')))) THEN
            RAISE EXCEPTION 'Occurrence write requires live worker fencing' USING ERRCODE='23514'; END IF;
        IF NEW.state<>'running' AND OLD.state<>'running' AND NEW.fence<>OLD.fence THEN
            RAISE EXCEPTION 'Only a new claim advances occurrence fencing' USING ERRCODE='23514'; END IF;
        IF OLD.state='delivery_unknown' AND (
            NEW.actor_id IS NULL
            OR NEW.reason IS DISTINCT FROM CASE NEW.state WHEN 'pending' THEN 'recovery_retry'
                WHEN 'succeeded' THEN 'recovery_complete' WHEN 'failed' THEN 'recovery_fail' END
            OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
            OR (NOT public.stewardship_delivery_resolution_edge_v1(to_jsonb(OLD),to_jsonb(NEW))
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run reconciled WHERE reconciled.id=OLD.task_id
                AND reconciled.task_type='schedule_occurrence' AND reconciled.domain_request_id=OLD.id
                AND reconciled.fence>=OLD.fence AND reconciled.state IN ('abandoned','cancelled','succeeded','failed')))
        ) THEN RAISE EXCEPTION 'Unknown delivery requires attributed reconciled ownership' USING ERRCODE='23514'; END IF;
        IF NEW.state='pending' AND OLD.state='failed' AND (NEW.retry_command_id IS NULL OR NEW.revision_id IS DISTINCT FROM d.current_revision_id) THEN
            RAISE EXCEPTION 'Occurrence retry requires explicit current identity' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND OLD.state='failed' AND NEW.reason NOT IN ('schedule_removed','schedule_replaced') THEN
            RAISE EXCEPTION 'Failed occurrence cannot be silently discarded' USING ERRCODE='23514'; END IF;
        IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state<>'running' AND NEW.state='running' THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Occurrence attempt count is inconsistent' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='coalesced' AND (NEW.replacement_id=NEW.id OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence replacement JOIN stewardship_schedule_definition rd ON rd.id=replacement.definition_id
        WHERE replacement.id=NEW.replacement_id AND rd.campaign_id=d.campaign_id AND replacement.mode=NEW.mode
          AND replacement.state NOT IN ('skipped','coalesced','failed')
    )) THEN RAISE EXCEPTION 'Invalid occurrence coalescing target' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $_$;
ALTER FUNCTION public.stewardship_occurrence_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_occurrence_guard_v1() FROM PUBLIC;

-- stewardship_production_checkpoint_v1, as production.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_production_checkpoint_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    request public.stewardship_production_request%ROWTYPE;
    entry record;
    prior numeric;
BEGIN
    SELECT * INTO request FROM public.stewardship_production_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR request.state<>'cleanup_running'
       OR NEW.sequence<>request.checkpoint_sequence+1
       OR NEW.deleted_count<>public.stewardship_cleanup_counts_v1(NEW.counts)
       OR NEW.run_id IS DISTINCT FROM request.run_id
       OR NEW.task_fence IS DISTINCT FROM request.task_fence
       OR NEW.worker_id IS DISTINCT FROM request.worker_id
       OR NEW.actor_id IS DISTINCT FROM request.worker_id THEN
        RAISE EXCEPTION 'Invalid Production cleanup checkpoint' USING ERRCODE='23514';
    END IF;
    FOR entry IN SELECT * FROM jsonb_each(NEW.counts) LOOP
        SELECT COALESCE(sum((counts->>entry.key)::numeric),0) INTO prior
            FROM public.stewardship_production_checkpoint WHERE request_id=NEW.request_id;
        IF NOT (request.inventory_counts OPERATOR(pg_catalog.?) entry.key)
           OR prior+entry.value::text::numeric>(request.inventory_counts->>entry.key)::numeric THEN
            RAISE EXCEPTION 'Cleanup batch exceeds its inventory' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_production_checkpoint_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_production_checkpoint_v1() FROM PUBLIC;

-- stewardship_setup_load_organization_v1, as 0177_setup_load_definer.sql left it.
CREATE OR REPLACE FUNCTION public.stewardship_setup_load_organization_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- SECURITY DEFINER (#2163): the scheduler and worker also update setup
    -- attempts, and may not read the lookup table themselves.
    IF NEW.state='loading' AND OLD.state IS DISTINCT FROM 'loading' AND (
        EXISTS (SELECT 1 FROM public.stewardship_setup_sealed_credential c
            WHERE c.attempt_id=NEW.id AND c.target='parishsoft' AND c.scrubbed_at IS NULL
              AND NOT c.settings OPERATOR(pg_catalog.?) 'organization_id')
        OR EXISTS (SELECT 1 FROM public.stewardship_setup_parishsoft_lookup l
            WHERE l.attempt_id=NEW.id AND l.state IN ('queued','running','ready','failed'))) THEN
        RAISE EXCEPTION 'The setup load needs a chosen ParishSoft organization (#1554)'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_setup_load_organization_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_setup_load_organization_v1() FROM PUBLIC;

DO $check$
DECLARE
    -- Each replaced function and the md5 of its body as this file writes it.
    expected CONSTANT text[] := ARRAY[
        ['public.stewardship_added_member_candidates_v1(uuid, uuid)', 'e6a509e2e6090e53b7adcaf50174414e'],
        ['public.stewardship_address_leader_scope_v1(text, uuid)', 'e4f0e565db11537c5eb2b87e26686f75'],
        ['public.stewardship_delivery_address_retries_v1(uuid, uuid)', '87a6a233d2e7935420477f2f537e63a3'],
        ['public.stewardship_delivery_control_guard_v1()', 'c2f8a48c1926ddfb6bf7484e6d03c850'],
        ['public.stewardship_delivery_ids_for_address_v1(text, uuid)', '39019926a70994ee9612462c59528e17'],
        ['public.stewardship_delivery_recipient_outcomes_v1(uuid, uuid)', '3e6fe72cf1e4dc0bbeeb7572d29ec172'],
        ['public.stewardship_delivery_resolve_closed_v1(uuid)', 'ec9dbf912d42f0ac43bdf650af9c7372'],
        ['public.stewardship_leader_email_facts_v1(jsonb)', '40a040b2b0c2e5ba6be078d88acfbce6'],
        ['public.stewardship_leaders_without_user_v1(text, uuid)', 'f01b72679f738af523ef513e0cbbb94d'],
        ['public.stewardship_ministry_leader_coverage_v1(text, uuid)', 'add02bfb037c93ff1f546fde9026b3d2'],
        ['public.stewardship_ministry_leader_scope_v1(uuid)', '738e1f8c53da60c89c4c63f880911711'],
        ['public.stewardship_occurrence_guard_v1()', '2df53ec5648dfdc3e814c5b662f8c9f2'],
        ['public.stewardship_production_checkpoint_v1()', '92fb35afc17231c98ab0f096f0dcb7b6'],
        ['public.stewardship_setup_load_organization_v1()', '814f605d827934ffe813b3501b3c65e7']
    ];
    item text[];
BEGIN
    -- Each function runs with its owner's rights and the fixed search path,
    -- PUBLIC cannot execute it, and its body is exactly this file's, so no
    -- unqualified operator is left in it.
    FOREACH item SLICE 1 IN ARRAY expected LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_proc p
            WHERE p.oid=to_regprocedure(item[1])
              AND p.prosecdef
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
              AND md5(p.prosrc)=item[2]) THEN
            RAISE EXCEPTION 'Migration 0179: % is not installed as declared', item[1];
        END IF;
    END LOOP;
END
$check$;
