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
    WHEN reason='delivery_unresolved' THEN 'blocked'
    WHEN reason='initial_unfulfilled' THEN 'deferred'
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
    'deferred',count(DISTINCT family_id) FILTER(WHERE disposition='deferred'),
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

-- Complete digest groups include original days which the bounded scheduler has
-- not reached yet and already prepared aggregates. Never infer completeness
-- from a page of outbox rows. Manual weekly requests are independent intents.
CREATE VIEW public.stewardship_delivery_digest_recovery AS
WITH definitions AS (
    SELECT c.id AS campaign_id,c.production_cycle,d.id AS definition_id,
        d.version AS definition_version,d.current_revision_id AS revision_id,d.kind,
        p.start_date,p.end_date
    FROM public.stewardship_system_configuration r
    JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
    JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    JOIN public.stewardship_schedule_definition d ON d.campaign_id=c.id
    WHERE r.mode='production' AND c.delivery_paused
        AND d.kind IN ('daily_digest','weekly_digest') AND d.current_revision_id IS NOT NULL
), originals AS (
    SELECT d.*,day::date::text AS slot,
        public.stewardship_catchup_due_v1(d.definition_id,day::date::text) AS due_at
    FROM definitions d CROSS JOIN LATERAL generate_series(
        d.start_date::timestamp,d.end_date::timestamp,interval '1 day') day
), inputs AS (
    SELECT d.campaign_id,d.definition_id,d.definition_version,d.revision_id,d.kind,
        o.id AS occurrence_id,o.version AS occurrence_version,o.slot,o.due_at,
        o.state,w.task_versions,w.outbox_version,
        coalesce(w.blocking,false) OR o.state='delivery_unknown' AS uncertain,
        daily.occurrence_id IS NOT NULL OR weekly.occurrence_id IS NOT NULL AS prepared
    FROM definitions d
    JOIN public.stewardship_schedule_occurrence o ON o.revision_id=d.revision_id
        AND o.production_cycle=d.production_cycle AND o.mode='production' AND o.target='admins'
        AND o.due_at<=public.stewardship_campaign_now_v1()
        AND o.state IN ('pending','running','delivery_unknown')
    LEFT JOIN public.stewardship_schedule_work_row w ON w.id=o.id
    LEFT JOIN public.stewardship_daily_digest_work_row daily ON daily.occurrence_id=o.id
    LEFT JOIN public.stewardship_weekly_digest_work_row weekly ON weekly.occurrence_id=o.id
    WHERE NOT public.stewardship_schedule_slot_excluded_v1(d.definition_id,'production','admins',o.slot)
      AND o.slot NOT LIKE 'manual:%'
    UNION ALL
    SELECT d.campaign_id,d.definition_id,d.definition_version,d.revision_id,d.kind,
        NULL::uuid,NULL::bigint,d.slot,d.due_at,'pending',NULL::numeric,NULL::numeric,false,false
    FROM originals d
    WHERE d.due_at<=public.stewardship_campaign_now_v1()
        AND NOT public.stewardship_schedule_slot_excluded_v1(d.definition_id,'production','admins',d.slot)
        AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
            WHERE o.revision_id=d.revision_id AND o.production_cycle=d.production_cycle
                AND o.mode='production' AND o.target='admins' AND o.slot=d.slot)
), blocked AS (
    -- A report still discovering/capturing/fanning out must reach its ordinary
    -- safe point. The Admin never steals a worker lease or freezes a partial
    -- recipient set. Include its version in the exact confirmation fingerprint.
    SELECT p.definition_id,sum(p.version) AS versions,bool_or(p.phase NOT IN ('complete','cancelled')) AS preparing
    FROM (
        SELECT definition_id,revision_id,version,phase FROM public.stewardship_daily_digest_preparation
        WHERE mode='production'
        UNION ALL
        SELECT definition_id,revision_id,version,phase FROM public.stewardship_weekly_digest_preparation p
        WHERE mode='production' AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_weekly_manual_request m WHERE m.id=p.id)
    ) p JOIN definitions d ON d.definition_id=p.definition_id AND d.revision_id=p.revision_id
    GROUP BY p.definition_id
)
SELECT i.*,coalesce(b.versions,0) AS preparation_versions,
    coalesce(b.preparing,false) OR bool_or(i.uncertain) OVER (PARTITION BY i.definition_id) AS blocked
FROM inputs i LEFT JOIN blocked b ON b.definition_id=i.definition_id;
REVOKE ALL ON public.stewardship_delivery_digest_recovery FROM PUBLIC;

CREATE VIEW public.stewardship_delivery_digest_recovery_summary AS
WITH rows AS (SELECT * FROM public.stewardship_delivery_digest_recovery), groups AS (
    SELECT definition_id,kind,count(*) AS slots,bool_or(blocked) AS blocked,
        count(*) FILTER(WHERE occurrence_id IS NULL) AS unmaterialized,
        count(*) FILTER(WHERE prepared) AS prepared
    FROM rows GROUP BY definition_id,kind
)
SELECT r.current_campaign_id AS campaign_id,jsonb_build_object(
    'selected',(SELECT count(*) FROM groups WHERE NOT blocked),
    'slots',(SELECT count(*) FROM rows),
    'blocked',(SELECT count(*) FROM groups WHERE blocked),
    'unmaterialized',(SELECT count(*) FROM rows WHERE occurrence_id IS NULL),
    'prepared',(SELECT count(*) FROM rows WHERE prepared),
    'types',(SELECT coalesce(jsonb_object_agg(kind,to_jsonb(groups)-'definition_id'-'kind'),'{}') FROM groups),
    'fingerprint',encode(sha256(convert_to(coalesce((
        SELECT string_agg(encode(sha256(convert_to(to_jsonb(rows)::text,'UTF8')),'hex'),''
            ORDER BY definition_id,due_at,slot) FROM rows),''),'UTF8')),'hex')
) AS impact FROM public.stewardship_system_configuration r;
REVOKE ALL ON public.stewardship_delivery_digest_recovery_summary FROM PUBLIC;

CREATE TABLE public.stewardship_delivery_digest_effect (
    transaction_id xid8 NOT NULL,backend integer NOT NULL,
    definition_id uuid NOT NULL,revision_id uuid NOT NULL,kind text NOT NULL,
    occurrence_id uuid NOT NULL,existing boolean NOT NULL,slot text NOT NULL,
    due_at timestamptz NOT NULL,selected_id uuid NOT NULL,
    PRIMARY KEY(transaction_id,backend,occurrence_id)
);
REVOKE ALL ON public.stewardship_delivery_digest_effect FROM PUBLIC;

-- Recipient identity joins, shared by cancellation and task-root selection.
-- Keeping the exact selected occurrence boundary avoids cancelling unrelated
-- work which happens to share a correlation identifier or schedule revision.
CREATE VIEW public.stewardship_delivery_digest_message AS
    SELECT p.occurrence_id,recipient.outbox_id FROM public.stewardship_daily_digest_preparation p
    JOIN public.stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_daily_digest_ready ready ON ready.snapshot_id=s.id
    JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=ready.id
    UNION ALL
    SELECT p.occurrence_id,recipient.outbox_id FROM public.stewardship_weekly_digest_preparation p
    JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id;
REVOKE ALL ON public.stewardship_delivery_digest_message FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_recover_digests_v1(command uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_delivery_control%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
BEGIN
    SELECT * INTO STRICT intent FROM public.stewardship_delivery_control WHERE id=command;
    SELECT * INTO STRICT campaign FROM public.stewardship_campaign WHERE id=intent.campaign_id;
    IF intent.action<>'resume' OR intent.selection->>'plan'<>'family'
       OR NOT campaign.delivery_paused THEN
        RAISE EXCEPTION 'Digest recovery requires exact paused resume ownership' USING ERRCODE='23514';
    END IF;
    WITH rows AS MATERIALIZED (
        SELECT p.*,coalesce(p.occurrence_id,gen_random_uuid()) AS planned_id
        FROM public.stewardship_delivery_digest_recovery p WHERE p.campaign_id=campaign.id
    ), selected AS MATERIALIZED (
        SELECT definition_id,coalesce((array_agg(planned_id ORDER BY due_at DESC,slot DESC)
            FILTER(WHERE NOT prepared))[1],gen_random_uuid()) AS id
        FROM rows GROUP BY definition_id
    ) INSERT INTO public.stewardship_delivery_digest_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),p.definition_id,p.revision_id,p.kind,
            p.planned_id,p.occurrence_id IS NOT NULL,p.slot,p.due_at,s.id
        FROM rows p JOIN selected s USING(definition_id);
    -- If every due occurrence already has frozen report inputs, allocate a new
    -- aggregate. Its predecessor edges retain original coverage; accepted
    -- recipient evidence remains immutable and is reused by normal fanout.
    INSERT INTO public.stewardship_delivery_digest_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),p.definition_id,p.revision_id,p.kind,
            p.selected_id,false,'recovery:'||intent.id::text,max(p.due_at),p.selected_id
        FROM public.stewardship_delivery_digest_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_delivery_digest_effect chosen
                WHERE chosen.transaction_id=p.transaction_id AND chosen.backend=p.backend
                    AND chosen.occurrence_id=p.selected_id)
        GROUP BY p.definition_id,p.revision_id,p.kind,p.selected_id;
    INSERT INTO public.stewardship_schedule_occurrence
        (id,actor_id,correlation_id,version,mode,routing,target,slot,due_at,occurrence_key,
         state,fence,attempts,reason,pause_version,definition_id,revision_id,production_cycle)
        SELECT p.occurrence_id,intent.actor_id,intent.correlation_id,1,'production','production',
            'admins',p.slot,p.due_at,
            encode(sha256(convert_to('["'||p.revision_id::text||'","production","admins","'
                ||p.slot||'"'||CASE WHEN campaign.production_cycle>0 THEN ',["production_cycle",'
                    ||campaign.production_cycle::text||']' ELSE '' END||']','UTF8')),'hex'),
            'pending',0,0,'',campaign.pause_version,p.definition_id,p.revision_id,campaign.production_cycle
        FROM public.stewardship_delivery_digest_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND NOT p.existing;
    UPDATE public.stewardship_outbox_message m SET
        state='cancelled',action='cancel_unsent',version=m.version+1,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
        command_digest=encode(sha256(convert_to(jsonb_build_array(
            'delivery_digest_recovery',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
        reason=CASE p.kind WHEN 'daily_digest' THEN 'missed_daily_recovery' ELSE 'missed_weekly_recovery' END,
        finished_at=statement_timestamp(),sealed_substitutions=NULL,sealed_key_id=NULL,pause_hold_id=NULL
    FROM public.stewardship_delivery_digest_effect p
    JOIN public.stewardship_delivery_digest_message messages ON messages.occurrence_id=p.occurrence_id
    WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
        AND p.occurrence_id<>p.selected_id AND m.id=messages.outbox_id
        AND m.state IN ('pending','retry_wait');
    INSERT INTO public.stewardship_schedule_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),o.id,o.version,
            intent.actor_id,intent.correlation_id,
            CASE p.kind WHEN 'daily_digest' THEN 'missed_daily_recovery' ELSE 'missed_weekly_recovery' END
        FROM public.stewardship_delivery_digest_effect p
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.occurrence_id<>p.selected_id;
    UPDATE public.stewardship_schedule_occurrence o SET
        state='coalesced',version=o.version+1,lease_expires_at=NULL,replacement_id=p.selected_id,
        reason=CASE p.kind WHEN 'daily_digest' THEN 'missed_daily_recovery' ELSE 'missed_weekly_recovery' END,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id
    FROM public.stewardship_delivery_digest_effect p
    WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
        AND p.occurrence_id<>p.selected_id AND o.id=p.occurrence_id;
    INSERT INTO public.stewardship_schedule_fulfillment
        (id,actor_id,correlation_id,definition_id,mode,target,slot,disposition,occurrence_id)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,p.definition_id,
            'production','admins',p.slot,'coalesced',p.selected_id
        FROM public.stewardship_delivery_digest_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.occurrence_id<>p.selected_id;
    -- Do not fabricate terminal states for active workers. A queued obsolete
    -- delivery/finalizer can be cancelled; a running one sees coalesced scope at
    -- its next admission point and drains with its real lease/history.
    UPDATE public.stewardship_task_run t SET state='cancelled',action='safe_cancel',
        version=t.version+1,actor_id=intent.actor_id,correlation_id=intent.correlation_id,
        lease_expires_at=NULL
    WHERE t.state IN ('queued','retry_wait') AND (
        t.root_id IN (SELECT m.task_id FROM public.stewardship_outbox_message m
            JOIN public.stewardship_delivery_digest_message link ON link.outbox_id=m.id
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=link.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
                AND p.occurrence_id<>p.selected_id AND m.state='cancelled')
        OR t.task_type IN ('daily_digest_finalize','weekly_digest_finalize') AND t.domain_request_id IN (
            SELECT d.id FROM public.stewardship_daily_digest_preparation d
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=d.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND p.occurrence_id<>p.selected_id
            UNION ALL
            SELECT d.id FROM public.stewardship_weekly_digest_preparation d
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=d.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND p.occurrence_id<>p.selected_id
        ));
    DELETE FROM public.stewardship_schedule_effect
        WHERE transaction_id=pg_current_xact_id() AND backend=pg_backend_pid();
    DELETE FROM public.stewardship_delivery_digest_effect
        WHERE transaction_id=pg_current_xact_id() AND backend=pg_backend_pid();
END $$;
REVOKE ALL ON FUNCTION public.stewardship_delivery_recover_digests_v1(uuid) FROM PUBLIC;
