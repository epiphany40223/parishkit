-- Frozen forward migration file 0191 (the repository-wide file sequence):
-- settle a Ministry leader email from evidence (#2300, admin-portal spec
-- "Outcome panel" and "Resolution questions"). This file is installed by
-- the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- A Ministry leader email whose provider answer was lost stays
-- delivery_unknown, and the delivery resolution guard (frozen file 0190)
-- admitted only campaign emails and notices, so nothing could settle it,
-- and every unsettled delivery_unknown email holds a recorded mail halt
-- from being cleared (frozen file 0115). This file replaces the guard in
-- place, its 0190 body with only these changes: leader_email is admitted;
-- it takes only a note, accept or confirm_unsent (it is never sent again
-- from here); it must be Family-less and campaign-less and the message of
-- one Ministry leader email recipient; it is admitted as a notice is
-- (an active configuration and no restore under review), instead of a
-- campaign's export admission; and it is never an occurrence, so neither
-- the occurrence binding nor the occurrence update applies. accept and
-- confirm_unsent then move the message to delivered or permanent_failure
-- through the outbox state guard's existing edges, and the audit event
-- names no campaign. Nothing else changes, so every other email's
-- resolution is as before.
--
-- The campaign-less shape is also the outbox_routing check's, so it is
-- asserted here only because the clause skips the campaign admission on
-- it; the recipient row ties the message to the Email Ministry leaders
-- request that made it, as each digest and report clause ties its own.
--
-- CREATE OR REPLACE resets SECURITY DEFINER unless the header names it, so
-- the ALTER below states it again. The file ends with a DO block that
-- refuses to commit unless the guard is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_resolution_guard_v1() RETURNS trigger
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
       OR m.id IS NULL OR m.purpose NOT IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test','operational','security_event','leader_email')
       OR NEW.expected_version<>m.version OR NEW.correlation_id<>NEW.id
       OR btrim(NEW.evidence_note)=''
       OR (m.purpose NOT IN ('receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test','operational','security_event','leader_email') AND o.outbox_id IS DISTINCT FROM m.id)
       OR (m.purpose='daily_digest' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_daily_digest_recipient recipient
           JOIN public.stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
           JOIN public.stewardship_daily_digest_snapshot snapshot ON snapshot.id=ready.snapshot_id
           JOIN public.stewardship_daily_digest_preparation preparation ON preparation.id=snapshot.preparation_id
           WHERE recipient.outbox_id=m.id AND recipient.id=m.semantic_key
             AND preparation.campaign_id=m.campaign_id AND preparation.mode=m.mode
             AND m.family_id IS NULL AND m.credential_namespace='none'))
       OR (m.purpose='weekly_digest' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_weekly_digest_recipient recipient
           JOIN public.stewardship_weekly_digest_snapshot snapshot ON snapshot.id=recipient.snapshot_id
           JOIN public.stewardship_weekly_digest_preparation preparation ON preparation.id=snapshot.preparation_id
           WHERE recipient.outbox_id=m.id AND recipient.id=m.semantic_key
             AND preparation.campaign_id=m.campaign_id AND preparation.mode=m.mode
             AND m.family_id IS NULL AND m.credential_namespace='none'))
       -- A report email (#1786): one recipient of its send's frozen cohort.
       OR (m.purpose='admin_report' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_report_email_recipient recipient
           JOIN public.stewardship_report_email_snapshot snapshot ON snapshot.id=recipient.snapshot_id
           JOIN public.stewardship_report_email_preparation preparation ON preparation.id=snapshot.preparation_id
           WHERE recipient.outbox_id=m.id AND recipient.id=m.semantic_key
             AND preparation.campaign_id=m.campaign_id AND preparation.mode=m.mode
             AND m.family_id IS NULL AND m.credential_namespace='none'))
       OR (m.purpose='receipt' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_submission_receipt receipt
           JOIN public.stewardship_submission s ON s.id=receipt.submission_id
           WHERE receipt.outbox_id=m.id AND s.id=m.semantic_key
             AND s.family_id=m.family_id AND s.campaign_id=m.campaign_id))
       OR (m.purpose='family_test' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_family_mail_test ticket
           WHERE ticket.outbox_id=m.id AND ticket.id=m.semantic_key
             AND ticket.campaign_id=m.campaign_id AND ticket.state='prepared'))
       -- Send a test to me (#2036): one Administrator's own test of a report.
       OR (m.purpose='admin_report_test' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_report_email_test test
           WHERE test.id=m.semantic_key AND test.campaign_id=m.campaign_id
             AND test.mode=m.mode AND m.family_id IS NULL AND m.credential_namespace='none'))
       -- A chosen-Family test, and a report email test, is settled from
       -- evidence only; it is never resent or retried, so an unknown test
       -- cannot block Testing cleanup (or the next test) forever.
       OR (m.purpose IN ('family_test','admin_report_test') AND NEW.action NOT IN ('note','accept','confirm_unsent'))
       -- An operational or security notice to the Administrators (#2295)
       -- belongs to no campaign and is never sent again: it takes only a
       -- note or the evidence answers, admitted while the deployment has an
       -- active configuration and no restore under review (the part of the
       -- export admission that names no campaign). It is never an
       -- occurrence or fulfillment.
       OR (m.purpose IN ('operational','security_event') AND (
           NEW.action NOT IN ('note','accept','confirm_unsent')
           OR m.family_id IS NOT NULL OR m.campaign_id IS NOT NULL OR m.routing<>'operational'
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_system_configuration r
               WHERE r.active_configuration_id IS NOT NULL AND NOT r.restore_review_required)))
       -- A Ministry leader email (#2300) belongs to no campaign either and
       -- is never sent again from here (applying the Email Ministry leaders
       -- review again sends a new one): it takes only a note or the
       -- evidence answers, under the notices' admission, and must be the
       -- Family-less, campaign-less message of one leader email recipient
       -- it always is. It is never an occurrence or fulfillment.
       OR (m.purpose='leader_email' AND (
           NEW.action NOT IN ('note','accept','confirm_unsent')
           OR m.family_id IS NOT NULL OR m.campaign_id IS NOT NULL
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_leader_email_recipient leader
               WHERE leader.outbox_id=m.id)
           OR NOT EXISTS(SELECT 1 FROM public.stewardship_system_configuration r
               WHERE r.active_configuration_id IS NOT NULL AND NOT r.restore_review_required)))
       OR (m.purpose NOT IN ('operational','security_event','leader_email')
           AND NOT public.stewardship_export_admitted_v1(m.campaign_id,true))
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
        IF NEW.action IN ('accept','confirm_unsent') THEN
            -- Both settle the current unknown attempt from external evidence
            -- alone. Neither sends, so neither consults the resend admission:
            -- they stay available while paused, after the Family submitted or
            -- lost eligibility, and on a campaign closed while paused.
            IF m.state<>'delivery_unknown' OR NEW.preparation IS NOT NULL OR NEW.retry_task_id IS NOT NULL
               OR EXISTS(SELECT 1 FROM public.stewardship_task_run WHERE root_id=m.task_id AND retry_sequence>latest.retry_sequence)
            THEN RAISE EXCEPTION 'Resolution requires the current unresolved attempt' USING ERRCODE='23514'; END IF;
            -- confirm_unsent reuses the dispatcher's definitive non-acceptance
            -- edge (fail_unaccepted to permanent_failure), so every consumer
            -- treats it as the terminal failure it is. A distinct reason keeps
            -- it apart from a provider refusal: no recipient is suppressed.
            -- An Admin report's cohort counts this failure as settled while
            -- its recipient is not an Administrator (see the digest
            -- completion proofs), as it does any provider permanent failure.
            UPDATE public.stewardship_outbox_message SET
                state=CASE WHEN NEW.action='accept' THEN 'delivered' ELSE 'permanent_failure' END,
                action=CASE WHEN NEW.action='accept' THEN 'accept' ELSE 'fail_unaccepted' END,
                command_id=NEW.id,command_digest=command_hash,evidence_note=NEW.evidence_note,
                evidence_digest=proof,provider_key_digest='',provider_message_digest='',
                reason=CASE WHEN NEW.action='accept' THEN 'admin_external_acceptance'
                    ELSE 'admin_confirmed_unsent' END,
                finished_at=statement_timestamp(),
                sealed_substitutions=NULL,sealed_key_id=NULL,pause_hold_id=NULL,
                actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=m.id;
            IF m.purpose NOT IN ('receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test','operational','security_event','leader_email') THEN
                IF NEW.action='accept' THEN
                    UPDATE public.stewardship_schedule_occurrence SET state='succeeded',reason='recovery_complete',
                        actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=o.id;
                    INSERT INTO public.stewardship_schedule_fulfillment
                        (id,actor_id,correlation_id,definition_id,mode,target,slot,disposition,occurrence_id)
                    VALUES(gen_random_uuid(),NEW.actor_id,NEW.id,o.definition_id,o.mode,o.target,o.slot,'delivered',o.id);
                ELSE
                    -- Failed only after definitive non-acceptance, like a provider
                    -- permanent failure: no fulfillment, and retry_failed may later
                    -- reopen it under the ordinary resend admission.
                    UPDATE public.stewardship_schedule_occurrence SET state='failed',reason='recovery_fail',
                        actor_id=NEW.actor_id,correlation_id=NEW.id,version=version+1 WHERE id=o.id;
                END IF;
            END IF;
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
            -- Render, and so admit, before the resend below leaves delivery_unknown:
            -- the admission's pause exception reads the message's current state.
            rendering:=public.stewardship_delivery_retry_render_v1(m.id,NEW.preparation,NEW.actor_id,NEW.id);
            sealed:=NEW.preparation->'sealed';
            -- Resend records two separate facts: explicit duplicate-risk intent,
            -- then current preparation. The first event retains NEW.id for the
            -- occurrence recovery proof. Preparation gets a different command
            -- ID to preserve outbox_command_once; message version advances twice.
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
ALTER FUNCTION public.stewardship_delivery_resolution_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_resolution_guard_v1() FROM PUBLIC;

DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_delivery_resolution_guard_v1()')
          AND p.prosecdef
          AND p.proconfig OPERATOR(pg_catalog.@>) ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND strpos(p.prosrc, 'admin_report_test'',''operational'',''security_event'',''leader_email'')')>0
          AND strpos(p.prosrc, 'A Ministry leader email (#2300)')>0
          AND strpos(p.prosrc, 'SELECT 1 FROM public.stewardship_leader_email_recipient leader')>0
          AND strpos(p.prosrc, 'm.purpose NOT IN (''operational'',''security_event'',''leader_email'')')>0
          AND strpos(p.prosrc, 'An operational or security notice to the Administrators (#2295)')>0
          AND strpos(p.prosrc, 'm.family_id IS NOT NULL OR m.campaign_id IS NOT NULL OR m.routing<>''operational''')>0
          AND strpos(p.prosrc, 'm.purpose IN (''family_test'',''admin_report_test'') AND NEW.action NOT IN')>0
          AND strpos(p.prosrc, 'SELECT 1 FROM public.stewardship_report_email_test test')>0) THEN
        RAISE EXCEPTION 'Migration 0191: the delivery resolution guard does not settle Ministry leader emails';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_delivery_resolution'::regclass
          AND t.tgname='delivery_resolution_command' AND NOT t.tgisinternal AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_delivery_resolution_guard_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0191: the delivery resolution trigger is missing';
    END IF;
END
$check$;
