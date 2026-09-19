-- Complete Family groups for paused-delivery recovery. Include due definitions
-- which have not yet acquired an occurrence: counting only outboxes can select
-- an obsolete reminder while a newer obligation is still unmaterialized.
-- This private projection contains identities, not recipient addresses/bodies.
CREATE VIEW public.stewardship_delivery_family_recovery AS
WITH scope AS (
    SELECT c.id,c.production_cycle,p.ends_at
    FROM public.stewardship_system_configuration r
    JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
    JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    WHERE r.mode='production' AND c.delivery_paused
), inputs AS (
    SELECT s.id AS campaign_id,f.id AS family_id,f.version AS family_version,
        f.source_generation,f.active,f.email_eligible,f.email_deliverable,
        f.effective_submission_id,s.ends_at,
        d.id AS definition_id,d.version AS definition_version,d.kind,
        d.current_revision_id AS revision_id,v.due_at,
        o.id AS occurrence_id,o.version AS occurrence_version,
        coalesce(o.state,'pending') AS state,o.outbox_id,
        coalesce(w.blocking,false) AS uncertain,
        w.task_versions,w.outbox_version,
        EXISTS(SELECT 1 FROM public.stewardship_schedule_definition initial
            JOIN public.stewardship_restore_delivery_hold h ON h.definition_id=initial.id
            WHERE initial.campaign_id=s.id AND initial.kind='initial'
                AND h.mode='production' AND h.target='family:'||f.id::text
                AND h.slot='once' AND h.state='unreviewed') AS initial_unreviewed
    FROM scope s
    JOIN public.stewardship_family_campaign f ON f.campaign_id=s.id
    JOIN public.stewardship_schedule_definition d ON d.campaign_id=s.id
        AND d.kind IN ('initial','reminder') AND d.current_revision_id IS NOT NULL
    JOIN public.stewardship_schedule_revision v ON v.id=d.current_revision_id
        AND v.due_at<=public.stewardship_campaign_now_v1()
    LEFT JOIN LATERAL (
        SELECT current.* FROM public.stewardship_schedule_occurrence current
        WHERE current.revision_id=d.current_revision_id AND current.mode='production'
            AND current.target='family:'||f.id::text AND current.slot='once'
            AND current.production_cycle=s.production_cycle
        ORDER BY current.recovery_generation DESC LIMIT 1
    ) o ON true
    LEFT JOIN public.stewardship_schedule_work_row w ON w.id=o.id
    WHERE NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment covered
        WHERE covered.definition_id=d.id AND covered.mode='production'
            AND covered.target='family:'||f.id::text AND covered.slot='once')
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h
        WHERE h.definition_id=d.id AND h.mode='production'
            AND h.target='family:'||f.id::text AND h.slot='once'
            AND h.state IN ('unreviewed','assumed_delivered'))
      AND (o.id IS NOT NULL OR (f.active AND f.email_eligible
          AND f.effective_submission_id IS NULL))
), groups AS (
    SELECT inputs.*,
        bool_or(uncertain OR state='delivery_unknown') OVER family AS group_uncertain,
        bool_or(kind='initial' AND state IN ('failed','skipped','coalesced'))
            OVER family AS initial_unfulfilled,
        first_value(definition_id) OVER (
            PARTITION BY family_id ORDER BY
                CASE WHEN state IN ('pending','running') THEN 0 ELSE 1 END,
                CASE kind WHEN 'initial' THEN 0 ELSE 1 END,
                due_at DESC,definition_id DESC,occurrence_id DESC
        ) AS selected_definition
    FROM inputs WINDOW family AS (PARTITION BY family_id)
), reasons AS (
    SELECT groups.*,CASE
        WHEN group_uncertain THEN 'delivery_unresolved'
        WHEN public.stewardship_campaign_now_v1()>=ends_at THEN 'campaign_closed'
        WHEN NOT active OR NOT email_eligible THEN 'family_ineligible'
        WHEN effective_submission_id IS NOT NULL THEN 'family_responded'
        WHEN NOT email_deliverable THEN 'no_deliverable_recipient'
        WHEN initial_unreviewed OR initial_unfulfilled THEN 'initial_unfulfilled'
        ELSE '' END AS reason
    FROM groups
) SELECT reasons.*,CASE
    WHEN state NOT IN ('pending','running','delivery_unknown') THEN 'unchanged'
    WHEN reason IN ('delivery_unresolved','initial_unfulfilled') THEN 'blocked'
    WHEN reason<>'' THEN 'skipped'
    WHEN definition_id=selected_definition THEN 'selected'
    ELSE 'coalesced' END AS disposition
FROM reasons;
REVOKE ALL ON public.stewardship_delivery_family_recovery FROM PUBLIC;

-- The Web role gets exact aggregate impact, not the private planning rows.
CREATE VIEW public.stewardship_delivery_family_recovery_summary AS
WITH rows AS (SELECT * FROM public.stewardship_delivery_family_recovery)
SELECT c.id AS campaign_id,jsonb_build_object(
    'selected',count(*) FILTER(WHERE disposition='selected'),
    'coalesced',count(*) FILTER(WHERE disposition='coalesced'),
    'skipped',count(*) FILTER(WHERE disposition='skipped'),
    'blocked',count(DISTINCT family_id) FILTER(WHERE disposition='blocked'),
    'unmaterialized',count(*) FILTER(WHERE definition_id IS NOT NULL AND occurrence_id IS NULL),
    -- Aggregate fixed-width row hashes, not a potentially huge JSON document
    -- for every Family/definition pair. The ordered framing remains exact.
    'fingerprint',encode(sha256(convert_to(coalesce(
        string_agg(encode(sha256(convert_to(to_jsonb(rows)::text,'UTF8')),'hex'),''
            ORDER BY family_id,definition_id) FILTER(WHERE definition_id IS NOT NULL),
        ''),'UTF8')),'hex')
) AS impact
FROM public.stewardship_system_configuration r
JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
LEFT JOIN rows ON rows.campaign_id=c.id GROUP BY c.id;
REVOKE ALL ON public.stewardship_delivery_family_recovery_summary FROM PUBLIC;

-- Freeze the exact private plan before creating occurrences changes the view.
-- No runtime role can read or write these transaction-local effect identities.
CREATE TABLE public.stewardship_delivery_family_effect (
    transaction_id xid8 NOT NULL,backend integer NOT NULL,
    family_id uuid NOT NULL,definition_id uuid NOT NULL,revision_id uuid NOT NULL,
    occurrence_id uuid NOT NULL,existing boolean NOT NULL,
    selected_definition uuid NOT NULL,disposition text NOT NULL,reason text NOT NULL,
    due_at timestamptz NOT NULL,outbox_id uuid,task_id uuid,
    PRIMARY KEY(transaction_id,backend,family_id,definition_id)
);
REVOKE ALL ON public.stewardship_delivery_family_effect FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_recover_families_v1(command uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_delivery_control%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
BEGIN
    SELECT * INTO STRICT intent FROM public.stewardship_delivery_control WHERE id=command;
    SELECT * INTO STRICT campaign FROM public.stewardship_campaign WHERE id=intent.campaign_id;
    IF intent.action<>'resume' OR intent.selection->>'plan'<>'family'
       OR NOT campaign.delivery_paused THEN
        RAISE EXCEPTION 'Family recovery requires exact paused resume ownership' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_delivery_family_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),p.family_id,p.definition_id,p.revision_id,
            coalesce(p.occurrence_id,gen_random_uuid()),p.occurrence_id IS NOT NULL,
            p.selected_definition,p.disposition,
            CASE WHEN p.reason='' THEN 'missed_family_recovery' ELSE p.reason END,
            p.due_at,p.outbox_id,o.task_id
        FROM public.stewardship_delivery_family_recovery p
        LEFT JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE p.campaign_id=campaign.id AND p.disposition IN ('selected','coalesced','skipped');
    INSERT INTO public.stewardship_schedule_occurrence
        (id,actor_id,correlation_id,version,mode,routing,target,slot,due_at,occurrence_key,
         state,fence,attempts,reason,pause_version,definition_id,revision_id,production_cycle)
        SELECT p.occurrence_id,intent.actor_id,intent.correlation_id,1,'production','production',
            'family:'||p.family_id::text,'once',p.due_at,
            encode(sha256(convert_to('["'||p.revision_id::text||'","production","family:'
                ||p.family_id::text||'","once"'
                ||CASE WHEN campaign.production_cycle>0 THEN ',["production_cycle",'
                    ||campaign.production_cycle::text||']' ELSE '' END||']','UTF8')),'hex'),
            'pending',0,0,'',campaign.pause_version,p.definition_id,p.revision_id,campaign.production_cycle
        FROM public.stewardship_delivery_family_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND NOT p.existing;
    -- Cancel only proven unsent redundant work. Unknown/idempotent-uncertain
    -- attempts were rejected by the exact preview guard before any effect.
    UPDATE public.stewardship_outbox_message m SET
        state='cancelled',action='cancel_unsent',version=m.version+1,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
        command_digest=encode(sha256(convert_to(jsonb_build_array(
            'delivery_recovery',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
        reason=p.reason,finished_at=statement_timestamp(),sealed_substitutions=NULL,
        sealed_key_id=NULL,pause_hold_id=NULL
    FROM public.stewardship_delivery_family_effect p
    WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
        AND p.disposition IN ('coalesced','skipped') AND m.id=p.outbox_id
        AND m.state IN ('pending','retry_wait');
    INSERT INTO public.stewardship_schedule_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),o.id,o.version,
            intent.actor_id,intent.correlation_id,p.reason
        FROM public.stewardship_delivery_family_effect p
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.disposition IN ('coalesced','skipped');
    UPDATE public.stewardship_schedule_occurrence o SET
        state=CASE p.disposition WHEN 'coalesced' THEN 'coalesced' ELSE 'skipped' END,
        version=o.version+1,reason=p.reason,lease_expires_at=NULL,
        replacement_id=CASE WHEN p.disposition='coalesced' THEN (
            SELECT chosen.occurrence_id FROM public.stewardship_delivery_family_effect chosen
            WHERE chosen.transaction_id=p.transaction_id AND chosen.backend=p.backend
                AND chosen.family_id=p.family_id AND chosen.definition_id=p.selected_definition
        ) END,actor_id=intent.actor_id,correlation_id=intent.correlation_id
    FROM public.stewardship_delivery_family_effect p
    WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
        AND p.disposition IN ('coalesced','skipped') AND o.id=p.occurrence_id;
    INSERT INTO public.stewardship_schedule_fulfillment
        (id,actor_id,correlation_id,definition_id,mode,target,slot,disposition,occurrence_id)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,p.definition_id,
            'production','family:'||p.family_id::text,'once','coalesced',o.replacement_id
        FROM public.stewardship_delivery_family_effect p
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.disposition='coalesced';
    -- Running tasks keep their truthful lifetime and drain at their next safe
    -- point. Only waiting tasks can be terminally cancelled by this owner.
    UPDATE public.stewardship_task_run t SET state='cancelled',action='safe_cancel',
        version=t.version+1,actor_id=intent.actor_id,correlation_id=intent.correlation_id,
        lease_expires_at=NULL
    WHERE t.state IN ('queued','retry_wait') AND t.root_id IN (
        SELECT original.root_id FROM public.stewardship_delivery_family_effect p
        JOIN public.stewardship_task_run original ON original.id=p.task_id
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.disposition IN ('coalesced','skipped')
        UNION
        SELECT m.task_id FROM public.stewardship_delivery_family_effect p
        JOIN public.stewardship_outbox_message m ON m.id=p.outbox_id
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.disposition IN ('coalesced','skipped')
    );
    DELETE FROM public.stewardship_schedule_effect
        WHERE transaction_id=pg_current_xact_id() AND backend=pg_backend_pid();
    DELETE FROM public.stewardship_delivery_family_effect
        WHERE transaction_id=pg_current_xact_id() AND backend=pg_backend_pid();
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_recover_families_v1(uuid) FROM PUBLIC;
