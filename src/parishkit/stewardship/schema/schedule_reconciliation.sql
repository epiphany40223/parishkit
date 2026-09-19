-- The public read boundary exposes counts only. Underlying rows, recipients,
-- renders and sealed substitutions remain outside web/installer privileges.
CREATE VIEW public.stewardship_daily_digest_work_row AS
SELECT p.occurrence_id,p.task_id,
    coalesce(messages.outbox_versions,0) AS outbox_versions,
    coalesce(messages.outboxes,0) AS outboxes,
    coalesce(tasks.versions,0)+coalesce(finalizers.versions,0)+p.version AS task_versions,
    coalesce(messages.blocking,false) AS blocking
FROM public.stewardship_daily_digest_preparation p
LEFT JOIN LATERAL (
    SELECT sum(m.version) AS outbox_versions,count(m.id) AS outboxes,
        bool_or(m.state IN ('submitting','delivery_unknown')
            OR (m.state IN ('pending','retry_wait') AND resolution.action='retry_idempotent')
            OR m.purpose<>'daily_digest' OR m.mode<>p.mode
            OR m.campaign_id IS DISTINCT FROM p.campaign_id
            OR m.semantic_key IS DISTINCT FROM recipient.id
            OR m.family_id IS NOT NULL OR m.credential_namespace<>'none') AS blocking
    FROM public.stewardship_daily_digest_snapshot s
    JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=s.id
    JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=r.id
    JOIN public.stewardship_outbox_message m ON m.id=recipient.outbox_id
    LEFT JOIN LATERAL (
        SELECT e.action FROM public.stewardship_outbox_event e
        WHERE e.message_id=m.id AND e.action IN
            ('retry_idempotent','retry_unaccepted','fail_unaccepted','accept','authorize_resend')
        ORDER BY e.version DESC LIMIT 1
    ) resolution ON true
    WHERE s.preparation_id=p.id
) messages ON true
LEFT JOIN LATERAL (
    -- Resolve a small, deduplicated root set first, then use TaskRun's root
    -- index. A correlated OR over all TaskRuns scales with unrelated jobs.
    SELECT sum(t.version) AS versions FROM (
        SELECT p.task_id AS task_id
        UNION
        SELECT m.task_id FROM public.stewardship_daily_digest_snapshot s
        JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=s.id
        JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=r.id
        JOIN public.stewardship_outbox_message m ON m.id=recipient.outbox_id
        WHERE s.preparation_id=p.id
    ) roots JOIN public.stewardship_task_run t ON t.root_id=roots.task_id
) tasks ON true
LEFT JOIN (
    -- Finalizer roots cannot be preparation or outbox-delivery roots. This is
    -- one TaskRun scan per view read, not one full scan per preparation.
    SELECT domain_request_id,sum(version) AS versions
    FROM public.stewardship_task_run WHERE task_type='daily_digest_finalize'
    GROUP BY domain_request_id
) finalizers ON finalizers.domain_request_id=p.id
WHERE p.occurrence_id IS NOT NULL;

CREATE VIEW public.stewardship_weekly_digest_work_row AS
SELECT p.occurrence_id,p.task_id,
    coalesce(messages.outbox_versions,0) AS outbox_versions,
    coalesce(messages.outboxes,0) AS outboxes,
    coalesce(tasks.versions,0)+coalesce(finalizers.versions,0)+p.version AS task_versions,
    coalesce(messages.blocking,false) AS blocking
FROM public.stewardship_weekly_digest_preparation p
LEFT JOIN LATERAL (
    SELECT sum(m.version) AS outbox_versions,count(m.id) AS outboxes,
        bool_or(m.state IN ('submitting','delivery_unknown')
            OR (m.state IN ('pending','retry_wait') AND resolution.action='retry_idempotent')
            OR m.purpose<>'weekly_digest' OR m.mode<>p.mode
            OR m.campaign_id IS DISTINCT FROM p.campaign_id
            OR m.semantic_key IS DISTINCT FROM recipient.id
            OR m.family_id IS NOT NULL OR m.credential_namespace<>'none') AS blocking
    FROM public.stewardship_weekly_digest_snapshot s
    JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id
    JOIN public.stewardship_outbox_message m ON m.id=recipient.outbox_id
    LEFT JOIN LATERAL (
        SELECT e.action FROM public.stewardship_outbox_event e
        WHERE e.message_id=m.id AND e.action IN
            ('retry_idempotent','retry_unaccepted','fail_unaccepted','accept','authorize_resend')
        ORDER BY e.version DESC LIMIT 1
    ) resolution ON true
    WHERE s.preparation_id=p.id
) messages ON true
LEFT JOIN LATERAL (
    SELECT sum(t.version) AS versions FROM (
        SELECT p.task_id AS task_id
        UNION
        SELECT m.task_id FROM public.stewardship_weekly_digest_snapshot s
        JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id
        JOIN public.stewardship_outbox_message m ON m.id=recipient.outbox_id
        WHERE s.preparation_id=p.id
    ) roots JOIN public.stewardship_task_run t ON t.root_id=roots.task_id
) tasks ON true
LEFT JOIN (
    SELECT domain_request_id,sum(version) AS versions
    FROM public.stewardship_task_run WHERE task_type='weekly_digest_finalize'
    GROUP BY domain_request_id
) finalizers ON finalizers.domain_request_id=p.id
WHERE p.occurrence_id IS NOT NULL;

-- These private identity-only projections share the public count/fingerprint
-- boundary. Neither the browser nor installer gains report-content access.
CREATE VIEW public.stewardship_admin_digest_work_row AS
SELECT * FROM public.stewardship_daily_digest_work_row
UNION ALL
SELECT * FROM public.stewardship_weekly_digest_work_row;

CREATE VIEW public.stewardship_schedule_work_row AS
SELECT o.id, o.definition_id, o.revision_id, o.state, o.version, o.outbox_id,
       coalesce(m.version,0)+coalesce(digest.outbox_versions,0) AS outbox_version,
       coalesce(tv.versions,0)+coalesce(digest.task_versions,0) AS task_versions,
       (
           o.state='delivery_unknown' OR coalesce(digest.blocking,false)
           OR (o.task_id IS NOT NULL AND (
               NOT coalesce((t.task_type='schedule_occurrence' AND t.domain_request_id IS NOT DISTINCT FROM o.id)
                   OR (t.task_type='family_mail_prepare' AND EXISTS (
                       SELECT 1 FROM public.stewardship_family_mail_preparation q
                       WHERE q.id=t.domain_request_id AND q.task_id=t.root_id
                         AND q.occurrence_id=o.id AND q.mode=o.mode))
                   OR (t.task_type='outbox_delivery' AND t.domain_request_id=m.id
                       AND t.root_id=m.task_id AND m.semantic_key=o.id)
                   OR (d.kind='daily_digest' AND digest.occurrence_id=o.id AND (
                       (t.task_type='daily_digest_prepare' AND t.root_id=digest.task_id)
                       OR (t.task_type='daily_digest_finalize' AND EXISTS(
                           SELECT 1 FROM public.stewardship_daily_digest_preparation preparation
                           WHERE preparation.id=t.domain_request_id AND preparation.occurrence_id=o.id))
                       OR (t.task_type='outbox_delivery' AND EXISTS (
                           SELECT 1 FROM public.stewardship_outbox_message child
                           JOIN public.stewardship_daily_digest_recipient recipient ON recipient.outbox_id=child.id
                           JOIN public.stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
                           JOIN public.stewardship_daily_digest_snapshot snapshot ON snapshot.id=ready.snapshot_id
                           JOIN public.stewardship_daily_digest_preparation preparation ON preparation.id=snapshot.preparation_id
                           WHERE child.id=t.domain_request_id AND child.task_id=t.root_id
                             AND preparation.occurrence_id=o.id))))
                   OR (d.kind='weekly_digest' AND digest.occurrence_id=o.id AND (
                       (t.task_type='weekly_digest_prepare' AND t.root_id=digest.task_id)
                       OR (t.task_type='weekly_digest_finalize' AND EXISTS(
                           SELECT 1 FROM public.stewardship_weekly_digest_preparation preparation
                           WHERE preparation.id=t.domain_request_id AND preparation.occurrence_id=o.id))
                       OR (t.task_type='outbox_delivery' AND EXISTS (
                           SELECT 1 FROM public.stewardship_outbox_message child
                           JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.outbox_id=child.id
                           JOIN public.stewardship_weekly_digest_snapshot snapshot ON snapshot.id=recipient.snapshot_id
                           JOIN public.stewardship_weekly_digest_preparation preparation ON preparation.id=snapshot.preparation_id
                           WHERE child.id=t.domain_request_id AND child.task_id=t.root_id
                             AND preparation.occurrence_id=o.id)))),false)
           ))
           OR m.state IN ('submitting','delivery_unknown')
           OR (m.state IN ('pending','retry_wait') AND resolution.action='retry_idempotent')
           OR (o.outbox_id IS NOT NULL AND (
               m.id IS NULL OR m.campaign_id IS DISTINCT FROM d.campaign_id
               OR m.mode<>o.mode OR m.routing<>o.routing OR m.purpose<>d.kind
               OR (m.family_id IS NOT NULL AND o.target NOT IN
                   ('family:'||m.family_id::text,'family:'||f.family_duid::text))
               OR EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence other
                   WHERE other.outbox_id=m.id AND other.id<>o.id)
           ))
           OR (o.state='running' AND m.id IS NULL)
           OR (o.state='pending' AND m.id IS NULL AND EXISTS (
               SELECT 1 FROM public.stewardship_task_run owner
               WHERE owner.root_id=t.root_id AND owner.state IN ('running','abandoned')
           ))
           OR public.stewardship_occurrence_delivery_conflict_v1(o.state,m.state)
       ) IS TRUE AS blocking,
       (CASE WHEN m.id IS NULL THEN 0 ELSE 1 END)+coalesce(digest.outboxes,0) AS outbox_count
FROM public.stewardship_schedule_occurrence o
JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
LEFT JOIN public.stewardship_task_run t ON t.id=o.task_id
LEFT JOIN public.stewardship_outbox_message m ON m.id=o.outbox_id
LEFT JOIN public.stewardship_family_campaign f ON f.id=m.family_id
LEFT JOIN public.stewardship_admin_digest_work_row digest ON digest.occurrence_id=o.id
LEFT JOIN LATERAL (
    SELECT e.action FROM public.stewardship_outbox_event e
    WHERE e.message_id=m.id AND e.action IN
        ('retry_idempotent','retry_unaccepted','fail_unaccepted','accept','authorize_resend')
    ORDER BY e.version DESC LIMIT 1
) resolution ON true
LEFT JOIN LATERAL (
    SELECT sum(owner.version) AS versions FROM public.stewardship_task_run owner
    WHERE owner.root_id IN (t.root_id,m.task_id)
) tv ON true;

CREATE VIEW public.stewardship_schedule_work_summary AS
SELECT d.id AS definition_id,d.campaign_id,d.version AS definition_version,
       d.current_revision_id AS revision,
       count(o.id) AS occurrences,coalesce(sum(o.version),0) AS versions,
       coalesce(sum(o.task_versions),0) AS task_versions,
       coalesce(sum(o.outbox_version),0) AS outbox_versions,
       coalesce(sum(o.outbox_count),0)::bigint AS outboxes,
       count(o.id) FILTER(WHERE o.blocking) AS blocking,
       count(o.id) FILTER(WHERE NOT o.blocking AND o.state IN ('pending','running')) AS cancellable,
       count(o.id) FILTER(WHERE o.state='failed') AS failed,
       coalesce(coverage.delivered,0) AS delivered,coalesce(coverage.covered,0) AS covered
FROM public.stewardship_schedule_definition d
LEFT JOIN public.stewardship_schedule_work_row o
    ON o.definition_id=d.id AND o.revision_id=d.current_revision_id
LEFT JOIN LATERAL (
    SELECT count(*) FILTER(WHERE disposition='delivered') AS delivered,
           count(*) AS covered FROM public.stewardship_schedule_fulfillment
    WHERE definition_id=d.id
) coverage ON true
WHERE d.current_revision_id IS NOT NULL
GROUP BY d.id,d.campaign_id,d.version,d.current_revision_id,coverage.delivered,coverage.covered;

-- One common ownership projection covers singular Family mail and each
-- individually addressed digest child. It contains identities only, not bodies.
CREATE FUNCTION public.stewardship_schedule_message_ids_v1(definition uuid,revision uuid)
RETURNS SETOF uuid LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT outbox_id FROM public.stewardship_schedule_occurrence
    WHERE definition_id=$1 AND revision_id=$2 AND outbox_id IS NOT NULL
    UNION
    SELECT recipient.outbox_id FROM public.stewardship_daily_digest_preparation p
    JOIN public.stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=s.id
    JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=r.id
    WHERE p.definition_id=$1 AND p.revision_id=$2
    UNION
    SELECT recipient.outbox_id FROM public.stewardship_weekly_digest_preparation p
    JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id
    WHERE p.definition_id=$1 AND p.revision_id=$2
$$;
CREATE FUNCTION public.stewardship_schedule_task_roots_v1(definition uuid,revision uuid)
RETURNS SETOF uuid LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT t.root_id FROM public.stewardship_schedule_occurrence o
    JOIN public.stewardship_task_run t ON t.id=o.task_id
    WHERE o.definition_id=$1 AND o.revision_id=$2
    UNION SELECT m.task_id FROM public.stewardship_outbox_message m
    WHERE m.id IN (SELECT public.stewardship_schedule_message_ids_v1($1,$2))
    UNION SELECT p.task_id FROM public.stewardship_daily_digest_preparation p
    WHERE p.definition_id=$1 AND p.revision_id=$2
    UNION SELECT t.root_id FROM public.stewardship_task_run t
    JOIN public.stewardship_daily_digest_preparation p ON p.id=t.domain_request_id
    WHERE t.task_type='daily_digest_finalize' AND p.definition_id=$1 AND p.revision_id=$2
    UNION SELECT p.task_id FROM public.stewardship_weekly_digest_preparation p
    WHERE p.definition_id=$1 AND p.revision_id=$2
    UNION SELECT t.root_id FROM public.stewardship_task_run t
    JOIN public.stewardship_weekly_digest_preparation p ON p.id=t.domain_request_id
    WHERE t.task_type='weekly_digest_finalize' AND p.definition_id=$1 AND p.revision_id=$2
$$;
REVOKE ALL ON FUNCTION public.stewardship_schedule_message_ids_v1(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_schedule_task_roots_v1(uuid,uuid) FROM PUBLIC;

-- Short-lived exact-effect proofs, invisible to every runtime role. Rows are
-- inserted and removed inside the same selection transaction, never retained
-- as a second domain journal or accepted from a caller-controlled session GUC.
CREATE TABLE public.stewardship_schedule_effect (
    transaction_id xid8 NOT NULL,
    backend integer NOT NULL,
    occurrence_id uuid NOT NULL,
    previous_version bigint NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    reason text NOT NULL,
    PRIMARY KEY(transaction_id,backend,occurrence_id)
);

CREATE FUNCTION public.stewardship_schedule_effect_v1(
    occurrence uuid,previous_version bigint,actor uuid,correlation uuid,reason text
) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_schedule_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
          AND p.occurrence_id=$1 AND p.previous_version=$2
          AND p.actor_id IS NOT DISTINCT FROM $3 AND p.correlation_id=$4 AND p.reason=$5)
$$;

CREATE FUNCTION public.stewardship_schedule_reconcile_v1(
    definition uuid,prior uuid,actor uuid,correlation uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    d public.stewardship_schedule_definition%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
    v_reason text;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM public.stewardship_system_configuration;
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=definition FOR UPDATE;
    IF d.campaign_id IS DISTINCT FROM r.current_campaign_id
       OR d.actor_id IS DISTINCT FROM actor OR d.correlation_id<>correlation
       OR actor IS DISTINCT FROM r.actor_id OR correlation<>r.correlation_id
       OR r.restore_review_required OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_schedule_selection s
           WHERE s.definition_id=d.id AND s.version=d.version
             AND s.configuration_id=r.active_configuration_id
             AND s.previous_revision_id=prior
             AND s.selected_revision_id IS NOT DISTINCT FROM d.current_revision_id
             AND s.actor_id IS NOT DISTINCT FROM actor AND s.correlation_id=correlation
       ) THEN
        RAISE EXCEPTION 'Schedule reconciliation requires exact selected configuration'
            USING ERRCODE='23514';
    END IF;
    v_reason:=CASE WHEN d.current_revision_id IS NULL THEN 'schedule_removed' ELSE 'schedule_replaced' END;
    RETURN public.stewardship_schedule_cancel_v1(definition,prior,actor,correlation,v_reason);
END $$;

-- Private cancellation mechanics shared by exact configuration selection and
-- pre-start withdrawal. Neither runtime role may call this function directly;
-- its two compiled owners perform their distinct intent/scope checks first.
CREATE FUNCTION public.stewardship_schedule_cancel_v1(
    definition uuid,prior uuid,actor uuid,correlation uuid,v_reason text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    d public.stewardship_schedule_definition%ROWTYPE;
    cancelled_messages bigint; skipped_occurrences bigint;
    failed_occurrences bigint; delivered_slots bigint; evidence jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO STRICT d FROM public.stewardship_schedule_definition WHERE id=definition FOR UPDATE;
    IF v_reason NOT IN ('schedule_removed','schedule_replaced','production_withdrawn') THEN
        RAISE EXCEPTION 'Invalid private schedule cancellation reason' USING ERRCODE='23514';
    END IF;
    -- Every producer/claim joins work order first. Lock concrete execution and
    -- message rows too, so an already allocated pre-provider hint cannot race.
    PERFORM 1 FROM public.stewardship_schedule_occurrence
        WHERE definition_id=definition AND revision_id=prior ORDER BY id FOR UPDATE;
    PERFORM 1 FROM public.stewardship_task_run t WHERE t.root_id IN (
        SELECT public.stewardship_schedule_task_roots_v1(definition,prior)
    ) ORDER BY t.id FOR UPDATE;
    PERFORM 1 FROM public.stewardship_outbox_message m WHERE m.id IN (
        SELECT public.stewardship_schedule_message_ids_v1(definition,prior)
    ) ORDER BY m.id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM public.stewardship_schedule_work_row
        WHERE definition_id=definition AND revision_id=prior AND blocking) THEN
        RAISE EXCEPTION 'In-flight schedule work blocks replacement' USING ERRCODE='23514';
    END IF;
    SELECT count(*) INTO failed_occurrences FROM public.stewardship_schedule_occurrence
        WHERE definition_id=definition AND revision_id=prior AND state='failed';
    SELECT count(*) INTO delivered_slots FROM public.stewardship_schedule_fulfillment
        WHERE definition_id=definition AND disposition='delivered';
    UPDATE public.stewardship_outbox_message m SET
        state='cancelled',action='cancel_unsent',version=m.version+1,
        actor_id=actor,correlation_id=correlation,command_id=gen_random_uuid(),
        command_digest=encode(sha256(convert_to(jsonb_build_array(
            'schedule_cancel',d.id,d.version,m.id,m.version)::text,'UTF8')),'hex'),
        reason=v_reason,finished_at=statement_timestamp(),sealed_substitutions=NULL,
        sealed_key_id=NULL,pause_hold_id=NULL
    WHERE m.state IN ('pending','retry_wait') AND m.id IN (
        SELECT public.stewardship_schedule_message_ids_v1(definition,prior)
    );
    GET DIAGNOSTICS cancelled_messages=ROW_COUNT;
    INSERT INTO public.stewardship_schedule_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),id,version,actor,correlation,v_reason
        FROM public.stewardship_schedule_occurrence
        WHERE definition_id=definition AND revision_id=prior AND state IN ('pending','running');
    UPDATE public.stewardship_schedule_occurrence SET
        state='skipped',version=version+1,reason=v_reason,lease_expires_at=NULL,
        actor_id=actor,correlation_id=correlation
    WHERE definition_id=definition AND revision_id=prior AND state IN ('pending','running');
    GET DIAGNOSTICS skipped_occurrences=ROW_COUNT;
    DELETE FROM public.stewardship_schedule_effect WHERE transaction_id=pg_current_xact_id()
        AND backend=pg_backend_pid();
    -- Waiting tasks have no worker to drain them. Running tasks retain their
    -- lease and must observe terminal/revision admission at their safe point;
    -- we never impersonate that worker or pretend its execution has finished.
    UPDATE public.stewardship_task_run t SET state='cancelled',action='safe_cancel',
        version=t.version+1,actor_id=actor,correlation_id=correlation,lease_expires_at=NULL
    WHERE t.state IN ('queued','retry_wait') AND t.root_id IN (
        SELECT public.stewardship_schedule_task_roots_v1(definition,prior)
    );
    evidence:=jsonb_build_object('definition_id',d.id,'previous_revision_id',prior,
        'cancelled_messages',cancelled_messages,'skipped_occurrences',skipped_occurrences,
        'failed_occurrences',failed_occurrences,'delivered_slots',delivered_slots);
    IF d.current_revision_id IS NOT NULL THEN
        evidence:=evidence||jsonb_build_object('selected_revision_id',d.current_revision_id);
    END IF;
    RETURN evidence;
END $$;

REVOKE ALL ON public.stewardship_schedule_work_row FROM PUBLIC;
REVOKE ALL ON public.stewardship_daily_digest_work_row FROM PUBLIC;
REVOKE ALL ON public.stewardship_schedule_work_summary FROM PUBLIC;
REVOKE ALL ON public.stewardship_schedule_effect FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_schedule_effect_v1(uuid,bigint,uuid,uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_schedule_reconcile_v1(uuid,uuid,uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_schedule_cancel_v1(uuid,uuid,uuid,uuid,text) FROM PUBLIC;

-- These compiled trigger owners can inspect the private journal/proofs, without
-- granting their callers direct delivery writes or arbitrary function execution.
ALTER FUNCTION public.stewardship_schedule_definition_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_schedule_selection_effect_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_occurrence_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_schedule_definition_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_schedule_selection_effect_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_occurrence_guard_v1() FROM PUBLIC;
