-- Internal delivery journal. No runtime role receives a generic mutation grant.
-- Compiled dispatch/cleanup owners must add their narrowly scoped entry points
-- with the later workflow; UUIDs, action names and admission callbacks are not
-- a substitute for those PostgreSQL permission boundaries.

CREATE FUNCTION public.stewardship_family_mail_epoch_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user<>'pk_stewardship_scheduler' THEN RETURN NEW; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736229 AND objid=1 AND objsubid=2 AND granted)
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2 AND granted)
       OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_campaign c
           JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
           JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
           JOIN public.stewardship_campaign_credentials population ON population.campaign_id=c.id
           JOIN public.stewardship_source_current source ON source.snapshot_id=population.source_snapshot_id
           WHERE c.id=NEW.campaign_id AND c.state='draft' AND r.mode='testing'
             AND NOT r.restore_review_required AND NOT population.go_live_gate
             AND NOT population.population_dirty AND population.rehearsal_epoch_id IS NULL
             AND population.source_generation=source.generation
             AND public.stewardship_campaign_now_v1()>=p.starts_at
             AND public.stewardship_campaign_now_v1()<p.ends_at
             AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate
                 WHERE campaign_id=c.id AND state<>'released')
       ) THEN
        RAISE EXCEPTION 'Scheduler rehearsal initialization is not admitted' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='stewardship_rehearsal_epoch' THEN
        IF TG_OP<>'INSERT' OR NEW.state<>'active' OR NEW.invalidated_at IS NOT NULL
           OR NEW.actor_id IS NULL OR NEW.correlation_id<>NEW.campaign_id THEN
            RAISE EXCEPTION 'Scheduler may only create a new active rehearsal' USING ERRCODE='23514';
        END IF;
    ELSE
        IF TG_OP<>'UPDATE' OR OLD.rehearsal_epoch_id IS NOT NULL
           OR NEW.rehearsal_epoch_id IS NULL
           OR (to_jsonb(NEW)-ARRAY['rehearsal_epoch_id','actor_id','correlation_id','version','updated_at'])
                IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['rehearsal_epoch_id','actor_id','correlation_id','version','updated_at'])
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch e
               WHERE e.id=NEW.rehearsal_epoch_id AND e.campaign_id=NEW.campaign_id
                 AND e.state='active' AND e.actor_id=NEW.actor_id
                 AND e.correlation_id=NEW.correlation_id) THEN
            RAISE EXCEPTION 'Scheduler may only select a newly initialized rehearsal' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER family_mail_epoch_initialization BEFORE INSERT OR UPDATE ON public.stewardship_rehearsal_epoch
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_epoch_v1();
CREATE TRIGGER family_mail_epoch_selection BEFORE UPDATE ON public.stewardship_campaign_credentials
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_epoch_v1();

CREATE FUNCTION public.stewardship_family_mail_ticket_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Family preparation tickets are immutable' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736229 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=d.campaign_id
        JOIN public.stewardship_campaign_credentials c ON c.campaign_id=d.campaign_id
        JOIN public.stewardship_task_run t ON t.id=NEW.task_id
        WHERE o.id=NEW.occurrence_id AND o.state='pending' AND o.outbox_id IS NULL
          AND o.mode=NEW.mode AND o.mode=r.mode AND d.current_revision_id=o.revision_id
          AND d.kind IN ('initial','reminder') AND o.target LIKE 'family:%'
          AND NOT r.restore_review_required AND NOT c.go_live_gate
          AND (NEW.mode='production' OR EXISTS (
              SELECT 1 FROM public.stewardship_rehearsal_epoch e
              WHERE e.id=NEW.rehearsal_epoch_id AND e.id=c.rehearsal_epoch_id
                AND e.campaign_id=c.campaign_id AND e.state='active'))
          AND t.root_id=t.id AND t.state='queued' AND t.task_type='family_mail_prepare'
          AND t.domain_request_id=NEW.id AND t.idempotency_key=NEW.id::text
          AND t.initiated_by_id IS NULL AND NEW.actor_id IS NULL
          AND NEW.correlation_id=o.id
       ) THEN
        RAISE EXCEPTION 'Family preparation requires owned current allocation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER family_mail_ticket_guard BEFORE INSERT OR UPDATE OR DELETE
ON public.stewardship_family_mail_preparation
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_ticket_v1();

-- Invoker rights, not a privileged mutation entry point. No caller-controlled
-- session value can stand in for the original task's live database claim.
-- Shared exact current-source projection for invitations and direct receipts.
-- This is invoker-rights reading, not an independent send/admission capability.
CREATE FUNCTION public.stewardship_family_mail_recipients_v1(family uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce(jsonb_agg(address ORDER BY address),'[]'::jsonb) FROM (
        SELECT DISTINCT item->>'value' AS address
        FROM public.stewardship_family_campaign f
        JOIN public.stewardship_source_current s ON true
        JOIN public.stewardship_snapshot_family sf
          ON sf.snapshot_id=s.snapshot_id AND sf.source_key=f.family_duid::text
        JOIN public.stewardship_source_family payload ON payload.id=sf.payload_id
        CROSS JOIN LATERAL jsonb_array_elements(payload.canonical::jsonb->'active_head_duids') head
        JOIN public.stewardship_snapshot_contact sc ON sc.snapshot_id=s.snapshot_id
          AND sc.source_key='member:'||(head#>>'{}')
        JOIN public.stewardship_source_contact contact ON contact.id=sc.payload_id
        CROSS JOIN LATERAL jsonb_array_elements(contact.canonical::jsonb->'emails') item
        WHERE f.id=family AND item->'valid'='true'::jsonb AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_recipient_refusal refusal
            WHERE refusal.organization_id=s.organization_id AND refusal.family_duid=f.family_duid
              AND refusal.address=item->>'value' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_recipient_resolution resolution
                WHERE resolution.refusal_id=refusal.id))
    ) recipients
$$;

CREATE FUNCTION public.stewardship_family_mail_render_admitted_v1(
    proposed jsonb, family uuid, configuration uuid, revision uuid, mode text
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE expected jsonb; email jsonb; template uuid; test_recipient text;
BEGIN
    SELECT settings INTO email FROM public.stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT content.id INTO template FROM public.stewardship_content_version content
        JOIN public.stewardship_schedule_revision schedule
          ON content.record_id=(schedule.values->>'template_version')::uuid
            AND content.campaign_id=schedule.campaign_id
        WHERE schedule.id=revision AND content.configuration_id=configuration
          AND content.kind='email';
    SELECT testing_recipient INTO test_recipient FROM public.stewardship_system_configuration;
    expected:=public.stewardship_family_mail_recipients_v1(family);
    RETURN template IS NOT NULL AND email IS NOT NULL
       AND (proposed->>'configuration_id')::uuid=configuration
       AND (proposed->>'template_id')::uuid=template
       AND proposed->>'sender'=email->>'sender' AND proposed->>'reply_to'=email->>'reply_to'
       AND jsonb_array_length(expected)>0 AND proposed->'intended_recipients'=expected
       AND proposed->'routed_recipients'=CASE mode WHEN 'testing'
           THEN jsonb_build_array(test_recipient) ELSE expected END;
END $$;

CREATE FUNCTION public.stewardship_family_mail_write_admitted_v1(
    table_name text, proposed jsonb, previous jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    t public.stewardship_task_run%ROWTYPE;
    q public.stewardship_family_mail_preparation%ROWTYPE;
    o public.stewardship_schedule_occurrence%ROWTYPE;
    d public.stewardship_schedule_definition%ROWTYPE;
    c public.stewardship_campaign%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
BEGIN
    IF table_name NOT IN ('stewardship_schedule_occurrence','stewardship_occurrence_transition',
        'stewardship_schedule_fulfillment','stewardship_outbox_message',
        'stewardship_outbox_render','stewardship_outbox_event',
        'stewardship_rehearsal_credential','stewardship_rehearsal_code_mac') THEN RETURN false; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO t FROM public.stewardship_task_run
        WHERE id=(proposed->>'correlation_id')::uuid;
    IF t.id IS NULL OR t.task_type<>'family_mail_prepare' OR t.state<>'running'
       OR t.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
       OR t.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    SELECT * INTO q FROM public.stewardship_family_mail_preparation
        WHERE id=t.domain_request_id AND task_id=t.root_id;
    SELECT * INTO o FROM public.stewardship_schedule_occurrence WHERE id=q.occurrence_id;
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=o.definition_id;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM public.stewardship_system_configuration;
    IF q.id IS NULL OR o.id IS NULL OR c.id IS DISTINCT FROM r.current_campaign_id
       OR r.restore_review_required OR q.mode<>r.mode OR o.mode<>q.mode
       OR d.current_revision_id IS DISTINCT FROM o.revision_id
       OR (q.mode='production' AND c.delivery_paused)
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration p
           WHERE p.id=c.active_configuration_id
             AND public.stewardship_campaign_now_v1()>=p.starts_at
             AND public.stewardship_campaign_now_v1()<p.ends_at
             AND ((q.mode='testing' AND c.state='draft')
                OR (q.mode='production' AND c.state IN ('scheduled','active'))))
       OR EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate
           WHERE campaign_id=c.id AND state<>'released')
       OR (q.mode='production' AND EXISTS (SELECT 1 FROM public.stewardship_activation_catchup
           WHERE campaign_id=c.id AND completed_at IS NULL))
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials p
           WHERE p.campaign_id=c.id AND NOT p.go_live_gate
             AND (q.mode='production' OR EXISTS (
                 SELECT 1 FROM public.stewardship_rehearsal_epoch e
                 WHERE e.id=q.rehearsal_epoch_id AND e.id=p.rehearsal_epoch_id
                   AND e.campaign_id=c.id AND e.state='active')))
       THEN RETURN false; END IF;
    IF table_name='stewardship_rehearsal_credential' THEN
        RETURN previous IS NULL AND q.mode='testing'
           AND (proposed->>'epoch_id')::uuid=q.rehearsal_epoch_id
           AND o.target='family:'||(proposed->>'family_id')
           AND o.state='pending' AND o.outbox_id IS NULL;
    END IF;
    IF table_name='stewardship_rehearsal_code_mac' THEN
        RETURN previous IS NULL AND q.mode='testing' AND EXISTS (
            SELECT 1 FROM public.stewardship_rehearsal_credential credential
            WHERE credential.id=(proposed->>'credential_id')::uuid
              AND credential.epoch_id=(proposed->>'epoch_id')::uuid
              AND credential.epoch_id=q.rehearsal_epoch_id
              AND o.target='family:'||credential.family_id::text
              AND credential.correlation_id=t.id AND credential.actor_id=t.worker_id);
    END IF;
    IF table_name='stewardship_schedule_occurrence' THEN
        RETURN proposed->>'target'=o.target AND proposed->>'mode'=o.mode
           AND EXISTS (SELECT 1 FROM public.stewardship_schedule_definition other
               WHERE other.id=(proposed->>'definition_id')::uuid
                 AND other.campaign_id=c.id AND other.kind IN ('initial','reminder'))
           AND ((proposed->>'state' IN ('skipped','coalesced')
               AND previous->>'state'='pending' AND previous->>'outbox_id' IS NULL
               AND previous->>'task_id' IS NULL)
             OR ((proposed->>'id')::uuid=o.id
               AND (proposed->>'task_id')::uuid=t.id
               AND (proposed->>'fence')::bigint=t.fence
               AND (proposed->>'worker_id')::uuid=t.worker_id
               AND proposed->>'state' IN ('running','pending'))
             OR (previous IS NULL AND proposed->>'state'='pending'));
    END IF;
    IF table_name IN ('stewardship_occurrence_transition','stewardship_schedule_fulfillment') THEN
        RETURN EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence other
            JOIN public.stewardship_schedule_definition definition ON definition.id=other.definition_id
            WHERE other.id=(proposed->>'occurrence_id')::uuid
              AND definition.campaign_id=c.id AND other.target=o.target
              AND other.mode=q.mode AND definition.kind IN ('initial','reminder')
              AND (table_name<>'stewardship_occurrence_transition' OR (
                  other.version=(proposed->>'version')::bigint
                  AND other.state=proposed->>'after_state'
                  AND other.fence=(proposed->>'fence')::bigint
                  AND other.attempts=(proposed->>'attempts')::bigint
                  AND other.actor_id=(proposed->>'actor_id')::uuid
                  AND other.correlation_id=(proposed->>'correlation_id')::uuid)));
    END IF;
    IF o.task_id IS DISTINCT FROM t.id OR o.state<>'running' OR o.fence<>t.fence
       OR o.worker_id IS DISTINCT FROM t.worker_id OR o.lease_expires_at<=clock_timestamp()
       THEN RETURN false; END IF;
    IF table_name='stewardship_outbox_message' THEN
        RETURN previous IS NULL AND proposed->>'action'='created'
           AND proposed->>'state'='pending' AND (proposed->>'semantic_key')::uuid=o.id
           AND (proposed->>'campaign_id')::uuid=c.id AND proposed->>'mode'=q.mode
           AND proposed->>'purpose'=d.kind AND proposed->>'routing'=o.routing
           AND o.target='family:'||(proposed->>'family_id')
           AND (proposed->>'rehearsal_epoch_id')::uuid IS NOT DISTINCT FROM q.rehearsal_epoch_id
           AND proposed->>'credential_namespace'=CASE q.mode WHEN 'testing' THEN 'rehearsal' ELSE 'production' END
           AND (q.mode='testing' OR EXISTS (
               SELECT 1 FROM public.stewardship_family_token_generation generation
               JOIN public.stewardship_credential_deployment deployment
                 ON generation.credential_epoch=deployment.family_link_epoch
               WHERE generation.id=c.active_token_generation_id AND generation.state='active'
                 AND generation.id=(proposed->>'token_generation_id')::uuid
                 AND generation.credential_epoch=(proposed->>'credential_epoch_id')::uuid))
           AND EXISTS (SELECT 1 FROM public.stewardship_family_campaign f
               JOIN public.stewardship_campaign_credentials p ON p.campaign_id=f.campaign_id
               JOIN public.stewardship_source_current s ON s.snapshot_id=p.source_snapshot_id
               WHERE f.id=(proposed->>'family_id')::uuid AND f.campaign_id=c.id
                 AND f.active AND f.email_eligible AND f.email_deliverable
                 AND NOT p.population_dirty AND f.source_generation=s.generation
                 AND p.source_generation=s.generation
                 AND ((q.mode='production' AND f.effective_submission_id IS NULL)
                   OR (q.mode='testing' AND NOT EXISTS (
                       SELECT 1 FROM public.stewardship_submission sub
                       WHERE sub.family_id=f.id AND sub.mode='test'
                         AND sub.rehearsal_epoch_id=q.rehearsal_epoch_id))));
    END IF;
    RETURN previous IS NULL AND EXISTS (SELECT 1 FROM public.stewardship_outbox_message m
        WHERE m.id=(proposed->>'message_id')::uuid AND m.semantic_key=o.id
          AND m.correlation_id=t.id AND m.actor_id=t.worker_id AND m.state='pending'
          AND m.version=1 AND m.mode=q.mode
          AND ((table_name='stewardship_outbox_render' AND m.render_id=(proposed->>'id')::uuid
              AND public.stewardship_family_mail_render_admitted_v1(
                  proposed,m.family_id,r.active_configuration_id,o.revision_id,q.mode) IS TRUE)
            OR (table_name='stewardship_outbox_event' AND proposed->>'version'='1')));
END $$;

CREATE FUNCTION public.stewardship_family_mail_outbox_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
       public.stewardship_family_mail_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_daily_digest_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_weekly_digest_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_ops_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE THEN
        RAISE EXCEPTION 'Outbox insertion requires current preparation ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER family_mail_outbox_write BEFORE INSERT ON public.stewardship_outbox_message
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_outbox_write_v1();

CREATE FUNCTION public.stewardship_family_mail_receipt_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE q public.stewardship_family_mail_preparation%ROWTYPE;
BEGIN
    SELECT request.* INTO q FROM public.stewardship_family_mail_preparation request
        JOIN public.stewardship_task_run t ON t.root_id=request.task_id
        WHERE t.id=NEW.correlation_id AND t.task_type='family_mail_prepare';
    IF q.id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.id=q.occurrence_id AND o.outbox_id=NEW.id
          AND o.task_id=NEW.correlation_id AND o.mode=q.mode
          AND NEW.semantic_key=o.id AND o.state='pending'
    ) THEN
        RAISE EXCEPTION 'Prepared Family mail requires its atomic occurrence receipt'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER family_mail_receipt
AFTER INSERT ON public.stewardship_outbox_message DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_receipt_v1();
CREATE TRIGGER family_mail_render_write BEFORE INSERT ON public.stewardship_outbox_render
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_outbox_write_v1();
CREATE TRIGGER family_mail_event_write BEFORE INSERT ON public.stewardship_outbox_event
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_outbox_write_v1();
CREATE TRIGGER family_mail_credential_write BEFORE INSERT ON public.stewardship_rehearsal_credential
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_outbox_write_v1();
CREATE TRIGGER family_mail_fingerprint_write BEFORE INSERT ON public.stewardship_rehearsal_code_mac
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_outbox_write_v1();

CREATE FUNCTION public.stewardship_family_mail_reservation_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Reservations deliberately have no retained Family/epoch/actor binding.
    -- The compiled credential owner computes the domain-separated MAC; SQL has
    -- no MAC/decryption key. Require its active task and an actually used key.
    IF current_user='pk_stewardship_worker' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_rehearsal_credential credential
        JOIN public.stewardship_rehearsal_epoch epoch ON epoch.id=credential.epoch_id
        JOIN public.stewardship_task_run task ON task.id=credential.correlation_id
          AND task.state='running' AND task.task_type='family_mail_prepare'
          AND task.worker_id=credential.actor_id AND task.lease_expires_at>clock_timestamp()
        WHERE epoch.campaign_id=NEW.campaign_id
          AND EXISTS (SELECT 1 FROM public.stewardship_task_event claim
              WHERE claim.run_id=task.id AND claim.fence=task.fence
                AND claim.action='claim' AND credential.created_at>=claim.created_at)
          AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_code_mac fingerprint
              WHERE fingerprint.credential_id=credential.id AND fingerprint.key_id=NEW.key_id)
          AND public.stewardship_family_mail_write_admitted_v1(
              'stewardship_rehearsal_credential',jsonb_build_object(
                  'family_id',credential.family_id,'epoch_id',credential.epoch_id,
                  'actor_id',credential.actor_id,'correlation_id',credential.correlation_id),NULL) IS TRUE
    ) THEN
        RAISE EXCEPTION 'Rehearsal reservations require current preparation ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER family_mail_reservation_write BEFORE INSERT ON public.stewardship_rehearsal_reservation
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_reservation_v1();

REVOKE ALL ON FUNCTION public.stewardship_family_mail_epoch_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_mail_ticket_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_mail_outbox_write_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_mail_receipt_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_mail_reservation_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_outbox_state_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    allowed text[];
    claim public.stewardship_task_run%ROWTYPE;
    key_lock boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('outbox_messages',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Delivery history requires its retention owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.sealed_substitutions IS NOT NULL AND
       (TG_OP='INSERT' OR NEW.sealed_substitutions IS DISTINCT FROM OLD.sealed_substitutions
        OR NEW.sealed_key_id IS DISTINCT FROM OLD.sealed_key_id
        OR NEW.token_generation_id IS DISTINCT FROM OLD.token_generation_id
        OR NEW.credential_epoch_id IS DISTINCT FROM OLD.credential_epoch_id) THEN
        -- Same nonblocking inventory lock as credential_keys.key_set_lock.
        -- No retired writer can race a newly retained outbox dependency.
        SELECT pg_try_advisory_xact_lock_shared(736226,1) INTO key_lock;
        IF NOT key_lock OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_credential_key_state k,
                jsonb_array_elements(k.inventory) entry
            WHERE k.kind='token_public' AND entry->>'id'=NEW.sealed_key_id
              AND entry->>'usage'='active'
        ) THEN
            RAISE EXCEPTION 'Delivery encryption key is not currently admitted'
                USING ERRCODE='23514';
        END IF;
        BEGIN
            IF (NEW.sealed_substitutions::jsonb)->>'kid' IS DISTINCT FROM NEW.sealed_key_id
               OR (NEW.sealed_substitutions::jsonb)->>'alg' IS DISTINCT FROM 'sealedbox-v1' THEN
                RAISE EXCEPTION 'Invalid delivery envelope' USING ERRCODE='23514';
            END IF;
        EXCEPTION WHEN invalid_text_representation THEN
            RAISE EXCEPTION 'Invalid delivery envelope' USING ERRCODE='23514';
        END;
    END IF;
    -- Campaign Testing work is never an operational-mail exception. Recheck
    -- both allocation and the last local boundary before external submission.
    IF NEW.routing='testing_override' AND (TG_OP='INSERT' OR NEW.action IN
       ('prepared','submit','retry_failed','authorize_resend','retry_unaccepted','retry_idempotent'))
       AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_campaign_credentials c
           JOIN public.stewardship_system_configuration s ON s.current_campaign_id=c.campaign_id
           WHERE c.campaign_id=NEW.campaign_id AND NOT c.go_live_gate AND s.mode='testing'
             AND (NEW.credential_namespace='none' OR EXISTS (
                 SELECT 1 FROM public.stewardship_rehearsal_epoch e
                 WHERE e.id=NEW.rehearsal_epoch_id AND e.id=c.rehearsal_epoch_id
                   AND e.campaign_id=c.campaign_id AND e.state='active'
             ))
       ) THEN
        RAISE EXCEPTION 'Testing delivery is not currently admitted' USING ERRCODE='23514';
    END IF;
    IF NEW.credential_namespace='production' AND NEW.sealed_substitutions IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_family_token_generation g
           JOIN public.stewardship_family_token t ON t.generation_id=g.id
           WHERE g.id=NEW.token_generation_id AND g.campaign_id=NEW.campaign_id
             AND t.family_id=NEW.family_id
             AND g.credential_epoch=NEW.credential_epoch_id
       ) THEN
        RAISE EXCEPTION 'Invalid delivery credential binding' USING ERRCODE='23514';
    END IF;
    SELECT * INTO claim FROM public.stewardship_task_run WHERE id=NEW.task_id;
    IF claim.id IS NULL OR claim.root_id <> claim.id
       OR claim.task_type <> 'outbox_delivery'
       OR claim.domain_request_id IS DISTINCT FROM NEW.id THEN
        RAISE EXCEPTION 'Invalid delivery task binding' USING ERRCODE='23514';
    END IF;
    IF (NEW.purpose <> 'operational' AND NEW.scope_id IS DISTINCT FROM NEW.campaign_id)
       OR (NEW.purpose = 'operational' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_parish WHERE id=NEW.scope_id
       )) OR (NEW.family_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_family_campaign
           WHERE id=NEW.family_id AND campaign_id=NEW.campaign_id
       )) THEN
        RAISE EXCEPTION 'Invalid delivery scope' USING ERRCODE='23514';
    END IF;
    -- Producers without pause authority must not plan a hold-table read for
    -- an absent binding. SQL AND short-circuiting is not a privilege boundary.
    IF NEW.pause_hold_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_delivery_pause_hold
            WHERE id=NEW.pause_hold_id AND campaign_id=NEW.campaign_id
              AND pause_version=NEW.pause_version
        ) THEN
            RAISE EXCEPTION 'Invalid delivery pause binding' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.version <> 1 OR NEW.state <> 'pending' OR NEW.action <> 'created'
           OR NEW.attempt <> 0 THEN
            RAISE EXCEPTION 'Invalid initial delivery' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    -- Revision selection cancels unsent work under the same work-order lock.
    -- A retained retry/hint cannot bypass that decision, including failure
    -- retries whose original failure and render deliberately remain immutable.
    IF NEW.action IN ('prepared','submit','retry_failed','authorize_resend',
        'retry_unaccepted','retry_idempotent') AND EXISTS (
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN public.stewardship_campaign c ON c.id=d.campaign_id
        WHERE o.outbox_id=NEW.id AND (
            o.revision_id IS DISTINCT FROM d.current_revision_id
            OR (o.mode='production' AND o.production_cycle<>c.production_cycle)
            OR o.state IN ('skipped','coalesced','succeeded')
        )
    ) THEN
        RAISE EXCEPTION 'Delivery schedule is no longer current' USING ERRCODE='23514';
    END IF;
    IF NEW.command_id = OLD.command_id THEN
        RAISE EXCEPTION 'Delivery command has already committed' USING ERRCODE='23514';
    END IF;
    allowed := ARRAY['version','updated_at','actor_id','correlation_id','command_id','command_digest',
        'action','reason','evidence_digest','evidence_note','provider_key_digest',
        'provider_message_digest'];
    IF NEW.action IN ('prepared','cancel_unsent') AND (
        SELECT action FROM public.stewardship_outbox_event
        WHERE message_id=NEW.id AND action IN ('retry_idempotent','retry_unaccepted',
            'fail_unaccepted','accept','authorize_resend') ORDER BY version DESC LIMIT 1
    ) = 'retry_idempotent' THEN
        RAISE EXCEPTION 'An uncertain idempotent retry must retain its payload and outcome'
            USING ERRCODE='23514';
    END IF;
    IF NEW.action IN ('prepared','hold','release_hold') THEN
        IF OLD.state NOT IN ('pending','retry_wait') OR NEW.state <> OLD.state THEN
            RAISE EXCEPTION 'Only unsent delivery may be prepared or held'
                USING ERRCODE='23514';
        END IF;
        IF NEW.action='prepared' THEN
            allowed := allowed || ARRAY['render_id','sealed_substitutions',
                'sealed_key_id','token_generation_id','credential_epoch_id'];
        ELSE
            allowed := allowed || ARRAY['pause_hold_id','pause_version'];
            IF (NEW.action='hold' AND NEW.pause_hold_id IS NULL)
               OR (NEW.action='release_hold' AND
                   (OLD.pause_hold_id IS NULL OR NEW.pause_hold_id IS NOT NULL)) THEN
                RAISE EXCEPTION 'Invalid delivery hold operation' USING ERRCODE='23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.stewardship_campaign c
                WHERE c.id=NEW.campaign_id AND NEW.routing='production'
                  AND ((NEW.action='hold' AND c.delivery_paused AND c.pause_version=NEW.pause_version)
                    OR (NEW.action='release_hold' AND NOT c.delivery_paused
                        AND c.pause_version>OLD.pause_version)
                    OR (NEW.action='release_hold'
                        AND public.stewardship_delivery_message_released_v1(NEW.id)))
            ) THEN
                RAISE EXCEPTION 'Delivery hold must match campaign pause state' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM (VALUES
                ('pending','submit','submitting'),
                ('retry_wait','submit','submitting'),
                ('submitting','accept','delivered'),
                ('delivery_unknown','accept','delivered'),
                ('submitting','retry_unaccepted','retry_wait'),
                ('delivery_unknown','retry_unaccepted','retry_wait'),
                ('submitting','fail_unaccepted','permanent_failure'),
                ('delivery_unknown','fail_unaccepted','permanent_failure'),
                ('submitting','mark_unknown','delivery_unknown'),
                ('pending','cancel_unsent','cancelled'),
                ('retry_wait','cancel_unsent','cancelled'),
                ('permanent_failure','retry_failed','pending'),
                ('delivery_unknown','authorize_resend','pending'),
                ('submitting','retry_idempotent','retry_wait'),
                ('delivery_unknown','retry_idempotent','retry_wait')
            ) AS edge(previous, action, target)
            WHERE edge.previous=OLD.state AND edge.action=NEW.action
              AND edge.target=NEW.state
        ) THEN
            RAISE EXCEPTION 'Invalid delivery transition' USING ERRCODE='23514';
        END IF;
        allowed := allowed || ARRAY['state'];
        CASE NEW.action
        WHEN 'submit' THEN
            allowed := allowed || ARRAY['attempt','run_id','task_fence','worker_id',
                'submitted_at','provider_deadline'];
            SELECT * INTO claim FROM public.stewardship_task_run WHERE id=NEW.run_id;
            IF OLD.pause_hold_id IS NOT NULL THEN
                RAISE EXCEPTION 'Delivery is paused' USING ERRCODE='23514';
            END IF;
            -- Minimum lifecycle fence even before per-message holds are attached.
            -- The later owner also verifies dates, purpose-specific post-close
            -- rules, recipient eligibility and catch-up readiness under this lock.
            IF NEW.routing='production' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_campaign c
                JOIN public.stewardship_system_configuration s ON s.current_campaign_id=c.id
                JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
                WHERE c.id=NEW.campaign_id AND s.mode='production'
                  AND NOT s.restore_review_required AND NOT k.go_live_gate
                  AND c.state IN ('scheduled','active','closed')
                  AND (NOT c.delivery_paused OR public.stewardship_delivery_message_released_v1(NEW.id))
            ) THEN
                RAISE EXCEPTION 'Production delivery is not currently admitted' USING ERRCODE='23514';
            END IF;
            IF OLD.not_before > statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery retry is not yet due' USING ERRCODE='23514';
            END IF;
            IF NEW.attempt <> OLD.attempt+1 OR claim.id IS NULL
               OR claim.root_id <> NEW.task_id OR claim.state <> 'running'
               OR claim.worker_id IS DISTINCT FROM NEW.worker_id
               OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
               OR claim.fence IS DISTINCT FROM NEW.task_fence
               OR claim.lease_expires_at <= statement_timestamp()
               OR NEW.submitted_at IS DISTINCT FROM statement_timestamp()
               OR NEW.provider_deadline <= NEW.submitted_at THEN
                RAISE EXCEPTION 'Delivery attempt does not own a current claim'
                    USING ERRCODE='23514';
            END IF;
        WHEN 'retry_unaccepted', 'retry_idempotent' THEN
            allowed := allowed || ARRAY['not_before'];
            IF NEW.not_before <= statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery retry requires a future schedule'
                    USING ERRCODE='23514';
            END IF;
        WHEN 'retry_failed' THEN
            allowed := allowed || ARRAY['finished_at','render_id','sealed_substitutions',
                'sealed_key_id','token_generation_id','credential_epoch_id','not_before'];
            IF NOT EXISTS (
                SELECT 1 FROM public.stewardship_task_run
                WHERE root_id=NEW.task_id AND retry_sequence>0 AND state='queued'
                  AND parent_id=OLD.run_id
            ) THEN
                RAISE EXCEPTION 'Failed delivery needs its explicit task retry'
                    USING ERRCODE='23514';
            END IF;
        WHEN 'accept', 'fail_unaccepted', 'cancel_unsent' THEN
            allowed := allowed || ARRAY['finished_at','sealed_substitutions','sealed_key_id',
                'pause_hold_id'];
            IF NEW.finished_at IS DISTINCT FROM statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery completion must use the database clock'
                    USING ERRCODE='23514';
            END IF;
        ELSE
            NULL;
        END CASE;
        IF (OLD.state='delivery_unknown' OR NEW.action IN
            ('accept','retry_unaccepted','fail_unaccepted','retry_idempotent'))
           AND (NEW.actor_id IS NULL OR NEW.evidence_digest='' OR NEW.evidence_note='') THEN
            RAISE EXCEPTION 'Delivery resolution requires attributed evidence'
                USING ERRCODE='23514';
        END IF;
        IF OLD.state='submitting' AND NEW.action<>'mark_unknown' THEN
            SELECT * INTO claim FROM public.stewardship_task_run WHERE id=OLD.run_id;
            IF claim.id IS NULL OR claim.root_id<>OLD.task_id OR claim.state<>'running'
               OR claim.fence IS DISTINCT FROM OLD.task_fence
               OR claim.worker_id IS DISTINCT FROM OLD.worker_id
               OR NEW.actor_id IS DISTINCT FROM OLD.worker_id
               OR claim.lease_expires_at<=statement_timestamp() THEN
                RAISE EXCEPTION 'Delivery outcome does not own a current claim'
                    USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    -- A provider outcome/reconciliation can return an in-flight message to
    -- unsent work after the pause began. The earlier hold trigger attaches
    -- the current hold in that same update, never after a clearable gap.
    IF NEW.state IN ('pending','retry_wait') AND NEW.state<>OLD.state
       AND NEW.pause_hold_id IS NOT NULL AND EXISTS(
        SELECT 1 FROM public.stewardship_campaign c
        WHERE c.id=NEW.campaign_id AND c.delivery_paused
            AND c.pause_version=NEW.pause_version AND NEW.routing='production') THEN
        allowed:=allowed||ARRAY['pause_hold_id','pause_version'];
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'Delivery command changed unrelated fields' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_outbox_history_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
BEGIN
    INSERT INTO public.stewardship_outbox_event
        (id,created_at,actor_id,correlation_id,message_id,command_id,command_digest,version,
         previous_state,state,action,attempt,render_id,run_id,task_fence,worker_id,
         provider_key_digest,provider_message_digest,evidence_digest,evidence_note,reason,
         not_before,submitted_at,provider_deadline,finished_at)
    VALUES (gen_random_uuid(),NEW.updated_at,NEW.actor_id,NEW.correlation_id,NEW.id,
        NEW.command_id,NEW.command_digest,NEW.version,CASE WHEN TG_OP='INSERT' THEN '' ELSE OLD.state END,
        NEW.state,NEW.action,NEW.attempt,NEW.render_id,NEW.run_id,NEW.task_fence,NEW.worker_id,
        NEW.provider_key_digest,NEW.provider_message_digest,NEW.evidence_digest,
        NEW.evidence_note,NEW.reason,NEW.not_before,NEW.submitted_at,NEW.provider_deadline,NEW.finished_at);
    INSERT INTO public.stewardship_audit_event
        (id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'outbox_'||NEW.action,NEW.id,NEW.campaign_id);
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_delivery_warning_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.action='mark_unknown' THEN
        -- The event already passed its immutable exact-message binding guard.
        -- This grants no log INSERT or callable definer authority to the sender.
        INSERT INTO public.stewardship_operational_log
            (id,actor_id,correlation_id,level,event,schema,context)
        VALUES(NEW.id,NEW.actor_id,NEW.correlation_id,'WARNING','delivery_unknown',
            'email',jsonb_build_object('message_id',NEW.message_id));
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_delivery_warning AFTER INSERT ON public.stewardship_outbox_event
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_delivery_warning_v1();
REVOKE ALL ON FUNCTION public.stewardship_delivery_warning_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_outbox_event_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    message public.stewardship_outbox_message%ROWTYPE;
    previous public.stewardship_outbox_event%ROWTYPE;
    field text;
BEGIN
    SELECT * INTO message FROM public.stewardship_outbox_message WHERE id=NEW.message_id;
    SELECT * INTO previous FROM public.stewardship_outbox_event
        WHERE message_id=NEW.message_id ORDER BY version DESC LIMIT 1;
    IF message.id IS NULL OR NEW.version <> COALESCE(previous.version,0)+1
       OR NEW.previous_state IS DISTINCT FROM COALESCE(previous.state,'')
       OR NEW.created_at IS DISTINCT FROM message.updated_at THEN
        RAISE EXCEPTION 'Invalid delivery history binding' USING ERRCODE='23514';
    END IF;
    FOREACH field IN ARRAY ARRAY['actor_id','correlation_id','command_id','command_digest','version','state',
        'action','attempt','render_id','run_id','task_fence','worker_id',
        'provider_key_digest','provider_message_digest','evidence_digest','evidence_note',
        'reason','not_before','submitted_at','provider_deadline','finished_at'] LOOP
        IF (to_jsonb(NEW)->field) IS DISTINCT FROM (to_jsonb(message)->field) THEN
            RAISE EXCEPTION 'Delivery history must match its operation'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_outbox_render_pin_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    rendering public.stewardship_outbox_render%ROWTYPE;
BEGIN
    -- Check every captured selection, not just the final mutable message row:
    -- each intermediate selection already has an immutable history event.
    SELECT * INTO rendering FROM public.stewardship_outbox_render WHERE id=NEW.render_id;
    IF rendering.id IS NULL OR rendering.message_id IS DISTINCT FROM NEW.id THEN
        RAISE EXCEPTION 'Delivery render belongs to another message' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_outbox_render_shape_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    recipients jsonb;
    body_limit integer;
BEGIN
    -- Only a weekly message may retain the separately bounded multi-Family
    -- compiled report. Unknown owners and other purposes keep the original cap.
    body_limit:=CASE WHEN EXISTS(SELECT 1 FROM stewardship_outbox_message
        WHERE id=NEW.message_id AND purpose='weekly_digest') THEN 8388608 ELSE 1048576 END;
    IF NEW.subject ~ E'[\r\n]' OR btrim(NEW.subject)=''
       OR btrim(NEW.html)='' OR btrim(NEW.text)=''
       OR octet_length(NEW.html)>body_limit OR octet_length(NEW.text)>body_limit
       OR NEW.sender ~ E'[\r\n]' OR NEW.sender NOT LIKE '%@%'
       OR NEW.reply_to ~ E'[\r\n]' OR NEW.reply_to NOT LIKE '%@%' THEN
        RAISE EXCEPTION 'Invalid delivery render' USING ERRCODE='23514';
    END IF;
    FOREACH recipients IN ARRAY ARRAY[NEW.intended_recipients,NEW.routed_recipients] LOOP
        IF jsonb_typeof(recipients)<>'array' THEN
            RAISE EXCEPTION 'Invalid delivery recipients' USING ERRCODE='23514';
        END IF;
        IF jsonb_array_length(recipients) NOT BETWEEN 1 AND 100 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(recipients) value
            WHERE jsonb_typeof(value)<>'string' OR length(value#>>'{}')>254
               OR (value#>>'{}') ~ E'[\r\n]' OR (value#>>'{}') NOT LIKE '%@%'
        ) OR (SELECT count(*)<>count(DISTINCT lower(value))
            FROM jsonb_array_elements_text(recipients) value) THEN
            RAISE EXCEPTION 'Invalid delivery recipients' USING ERRCODE='23514';
        END IF;
    END LOOP;
    IF NEW.template_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_content_version
        WHERE id=NEW.template_id AND configuration_id=NEW.configuration_id AND kind='email'
    ) THEN
        RAISE EXCEPTION 'Invalid delivery template binding' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_outbox_state_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_outbox_message
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_state_v1();
CREATE TRIGGER stewardship_outbox_history
    AFTER INSERT OR UPDATE ON public.stewardship_outbox_message
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_history_v1();
CREATE TRIGGER stewardship_outbox_event_binding
    BEFORE INSERT ON public.stewardship_outbox_event
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_event_binding_v1();
CREATE CONSTRAINT TRIGGER stewardship_outbox_render_pin
    AFTER INSERT OR UPDATE ON public.stewardship_outbox_message DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_render_pin_v1();
CREATE TRIGGER stewardship_outbox_render_shape
    BEFORE INSERT ON public.stewardship_outbox_render
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_outbox_render_shape_v1();

REVOKE ALL ON FUNCTION public.stewardship_outbox_state_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_history_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_event_binding_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_render_pin_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_outbox_render_shape_v1() FROM PUBLIC;

-- Family-scoped refusal history is not removed when the provider can be tried
-- again. Source correction appends a separate resolution under the source fence.
CREATE TABLE public.stewardship_recipient_refusal (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid,
    correlation_id uuid NOT NULL,
    family_id uuid NOT NULL,
    event_id uuid NOT NULL,
    address varchar(254) NOT NULL,
    organization_id bigint NOT NULL CHECK(organization_id>=0),
    family_duid bigint NOT NULL CHECK(family_duid>=0),
    CONSTRAINT recipient_refusal_event_address UNIQUE(event_id,address),
    CONSTRAINT recipient_refusal_identity_positive CHECK(organization_id>0 AND family_duid>0)
);
CREATE INDEX recipient_refusal_family ON public.stewardship_recipient_refusal(family_id);
CREATE INDEX recipient_refusal_identity ON public.stewardship_recipient_refusal(organization_id,family_duid);
CREATE INDEX recipient_refusal_correlation ON public.stewardship_recipient_refusal(correlation_id);
CREATE TABLE public.stewardship_recipient_resolution (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid,
    correlation_id uuid NOT NULL,
    refusal_id uuid NOT NULL UNIQUE,
    source_snapshot_id uuid NOT NULL,
    source_generation bigint NOT NULL CHECK(source_generation>=0),
    reason varchar(32) NOT NULL,
    evidence_note varchar(2000) NOT NULL,
    CONSTRAINT recipient_resolution_reason CHECK(
        (evidence_note='' AND reason='source_changed') OR
        (actor_id IS NOT NULL AND reason='verified_admin' AND NOT evidence_note='')),
    CONSTRAINT recipient_resolution_generation CHECK(source_generation>0)
);
CREATE INDEX recipient_resolution_correlation ON public.stewardship_recipient_resolution(correlation_id);

CREATE FUNCTION public.stewardship_refusal_address_present_v1(snapshot uuid,family_duid bigint,address text)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_source_member m
        JOIN public.stewardship_snapshot_member sm ON sm.payload_id=m.id AND sm.snapshot_id=$1
        JOIN public.stewardship_snapshot_contact sc ON sc.snapshot_id=$1 AND sc.source_key='member:'||sm.source_key
        JOIN public.stewardship_source_contact c ON c.id=sc.payload_id
        CROSS JOIN LATERAL jsonb_array_elements(c.canonical::jsonb->'emails') email
        WHERE m.family_key=$2::text AND email->>'value'=$3 AND email->'valid'='true'::jsonb
    )
$$;

-- The immutable event carries a closed helper result. Integer positions refer
-- only to its own exact routed envelope; response prose and credential values
-- are never accepted as provider evidence. This validates recorded observations,
-- not SMTP itself: the isolated compiled mail worker remains the provider owner.
CREATE FUNCTION public.stewardship_family_smtp_result_v1(event uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE e public.stewardship_outbox_event%ROWTYPE;
    r public.stewardship_outbox_render%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
    result jsonb; item jsonb; count_recipients integer; indices integer[]:=ARRAY[]::integer[];
    position integer; previous integer; field text;
BEGIN
    SELECT * INTO e FROM public.stewardship_outbox_event WHERE id=event;
    SELECT * INTO r FROM public.stewardship_outbox_render WHERE id=e.render_id;
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=e.message_id;
    IF e.id IS NULL OR r.id IS NULL OR m.id IS NULL OR e.previous_state<>'submitting'
       OR m.purpose NOT IN ('initial','reminder','receipt','daily_digest','weekly_digest','operational') OR e.attempt<1
       OR e.evidence_digest<>encode(sha256(convert_to(e.evidence_note,'UTF8')),'hex')
       OR e.provider_key_digest<>encode(sha256(convert_to(m.semantic_key::text,'UTF8')),'hex')
       THEN RETURN NULL; END IF;
    BEGIN result:=e.evidence_note::jsonb;
    EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END;
    IF jsonb_typeof(result)<>'object' OR NOT result ?& ARRAY[
        'protocol','status','recipient_count','permanent','transient','health']
       OR result-ARRAY['protocol','status','recipient_count','permanent','transient','health']<>'{}'::jsonb
       OR result->>'protocol' IS DISTINCT FROM 'workspace_smtp_v1'
       OR result->>'recipient_count'!~'^[1-9][0-9]{0,2}$'
       OR jsonb_typeof(result->'recipient_count')<>'number'
       OR jsonb_typeof(result->'permanent')<>'array'
       OR jsonb_typeof(result->'transient')<>'array'
       OR NOT EXISTS (SELECT 1 FROM (VALUES
           ('accepted','healthy'),('transient','healthy'),('transient','unobserved'),
           ('transient','unavailable'),('permanent','healthy'),('permanent','unobserved'),
           ('unavailable','unavailable'),('systemic','systemic'),
           ('delivery_unknown','unavailable'),('delivery_unknown','systemic')
       ) pair(status,health) WHERE pair.status=result->>'status' AND pair.health=result->>'health')
       OR (result->>'health'='unobserved' AND
           (result->'permanent'<>'[]'::jsonb OR result->'transient'<>'[]'::jsonb))
       OR e.reason IS DISTINCT FROM 'smtp_'||(result->>'status')
       OR NOT EXISTS (SELECT 1 FROM (VALUES
           ('accepted','delivered'),('transient','retry_wait'),
           ('unavailable','retry_wait'),('unavailable','permanent_failure'),
           ('transient','permanent_failure'),('permanent','permanent_failure'),
           ('delivery_unknown','delivery_unknown'),('systemic','permanent_failure')
       ) pair(status,state) WHERE pair.status=result->>'status' AND pair.state=e.state)
       THEN RETURN NULL; END IF;
    count_recipients:=(result->>'recipient_count')::integer;
    IF count_recipients>100 OR count_recipients<>jsonb_array_length(r.routed_recipients)
       THEN RETURN NULL; END IF;
    FOREACH field IN ARRAY ARRAY['permanent','transient'] LOOP
        previous:=-1;
        FOR item IN SELECT value FROM jsonb_array_elements(result->field) LOOP
            IF jsonb_typeof(item)<>'number' OR item::text!~'^[0-9]{1,2}$' THEN RETURN NULL; END IF;
            position:=item::text::integer;
            IF position<=previous OR position>=count_recipients OR position=ANY(indices)
               THEN RETURN NULL; END IF;
            indices:=array_append(indices,position); previous:=position;
        END LOOP;
    END LOOP;
    IF result->>'status' IN ('accepted','delivery_unknown') AND cardinality(indices)=count_recipients
       THEN RETURN NULL; END IF;
    RETURN result;
END $$;

CREATE FUNCTION public.stewardship_recipient_refusal_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.address<>lower(NEW.address) OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_outbox_event e
        JOIN public.stewardship_outbox_message m ON m.id=e.message_id
        JOIN public.stewardship_outbox_render r ON r.id=e.render_id
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=f.campaign_id
        JOIN public.stewardship_source_snapshot s ON s.id=k.source_snapshot_id
        WHERE e.id=NEW.event_id AND m.family_id=NEW.family_id
          AND f.family_duid=NEW.family_duid AND s.organization_id=NEW.organization_id
          AND e.actor_id=NEW.actor_id
          AND m.mode='production' AND m.routing='production'
          AND ((e.state='permanent_failure' AND e.reason='recipient_refused') OR EXISTS (
              SELECT 1 FROM jsonb_array_elements(
                  public.stewardship_family_smtp_result_v1(e.id)->'permanent') item
              WHERE r.routed_recipients->>(item::text::integer)=NEW.address
          ))
          AND r.routed_recipients ? NEW.address AND r.intended_recipients ? NEW.address
    ) THEN RAISE EXCEPTION 'Refusal requires exact Production recipient evidence'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_recipient_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE refusal public.stewardship_recipient_refusal%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO refusal FROM public.stewardship_recipient_refusal WHERE id=NEW.refusal_id;
    IF NEW.reason='verified_admin' THEN
        -- An Admin attestation is not a fabricated source correction or SMTP
        -- receipt. Only the web command owner can create this kind of evidence.
        IF session_user<>'pk_stewardship_web' OR refusal.id IS NULL
           OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,true)
           OR btrim(NEW.evidence_note)='' OR NOT EXISTS (
               SELECT 1 FROM public.stewardship_source_current cur
               JOIN public.stewardship_source_snapshot s ON s.id=cur.snapshot_id
               CROSS JOIN public.stewardship_system_configuration r
               JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
               JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
               WHERE cur.snapshot_id=NEW.source_snapshot_id
                 AND cur.generation=NEW.source_generation
                 AND s.organization_id=refusal.organization_id AND s.state='promoted'
                 AND k.source_snapshot_id=cur.snapshot_id
                 AND k.source_generation=cur.generation AND NOT k.population_dirty
                 AND c.state<>'archived'
                 AND public.stewardship_export_admitted_v1(c.id,true)
           ) THEN RAISE EXCEPTION 'Refusal clearance requires current verified Admin evidence'
               USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF session_user='pk_stewardship_web' THEN
        RAISE EXCEPTION 'Web cannot claim source-owned refusal correction' USING ERRCODE='23514';
    END IF;
    IF NEW.reason<>'source_changed' OR NEW.evidence_note<>'' THEN
        RAISE EXCEPTION 'Invalid refusal resolution kind' USING ERRCODE='23514';
    END IF;
    IF refusal.id IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_source_current c
        JOIN public.stewardship_source_snapshot s ON s.id=c.snapshot_id
        JOIN public.stewardship_source_lease l ON l.owner_id=s.task_id AND l.fence=s.source_fence
        JOIN public.stewardship_task_run t ON t.id=l.owner_id AND t.fence=l.task_fence
        WHERE s.id=NEW.source_snapshot_id AND c.generation=NEW.source_generation
          AND s.organization_id=refusal.organization_id AND NEW.actor_id=l.worker_id
          AND s.state='promoted' AND l.expires_at>clock_timestamp()
          AND l.phase IN ('full','delta') AND t.state='running'
          AND t.worker_id=l.worker_id AND t.lease_expires_at>clock_timestamp()
    ) OR public.stewardship_refusal_address_present_v1(
        NEW.source_snapshot_id,refusal.family_duid,refusal.address
    ) THEN RAISE EXCEPTION 'Refusal resolution requires current corrected source'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_recipient_refusal_guard
    BEFORE INSERT ON public.stewardship_recipient_refusal
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_recipient_refusal_guard_v1();
CREATE TRIGGER stewardship_recipient_resolution_guard
    BEFORE INSERT ON public.stewardship_recipient_resolution
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_recipient_resolution_guard_v1();
REVOKE ALL ON FUNCTION public.stewardship_recipient_refusal_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_recipient_resolution_guard_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_recipient_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$ BEGIN
    RAISE EXCEPTION 'Recipient evidence is immutable' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER recipient_immutable BEFORE UPDATE OR DELETE ON public.stewardship_recipient_refusal
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_recipient_immutable_v1();
CREATE TRIGGER recipient_immutable BEFORE UPDATE OR DELETE ON public.stewardship_recipient_resolution
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_recipient_immutable_v1();
REVOKE ALL ON FUNCTION public.stewardship_recipient_immutable_v1() FROM PUBLIC;

CREATE FUNCTION public.stewardship_refusal_family_effect_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE deliverable boolean; target_family uuid;
    refusal_organization bigint; refusal_duid bigint;
BEGIN
    -- This executes under the refusal guard's work-order lock. Update only
    -- deliverability, never source eligibility, credentials, or response state.
    -- Retain all old refusal rows: an unresolved row is the suppression fact.
    IF TG_TABLE_NAME='stewardship_recipient_resolution' THEN
        -- Source promotion already owns its complete population recalculation.
        -- Admin clearance instead restores this one identity in the same commit.
        IF NEW.reason<>'verified_admin' THEN RETURN NULL; END IF;
        SELECT organization_id,family_duid INTO refusal_organization,refusal_duid
            FROM public.stewardship_recipient_refusal WHERE id=NEW.refusal_id;
        INSERT INTO public.stewardship_audit_event
            (id,actor_id,correlation_id,event_type,subject_id)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,
            'recipient_refusal_cleared',NEW.id);
    ELSE
        refusal_organization:=NEW.organization_id;
        refusal_duid:=NEW.family_duid;
    END IF;
    SELECT f.id INTO target_family FROM public.stewardship_family_campaign f
    JOIN public.stewardship_system_configuration r ON r.current_campaign_id=f.campaign_id
    JOIN public.stewardship_source_current cur ON cur.organization_id=refusal_organization
    WHERE f.family_duid=refusal_duid;
    IF target_family IS NULL THEN RETURN NULL; END IF;
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_family_campaign f
        JOIN public.stewardship_source_current cur ON cur.singleton
        JOIN public.stewardship_snapshot_family sf
            ON sf.snapshot_id=cur.snapshot_id AND sf.source_key=f.family_duid::text
        JOIN public.stewardship_source_family source ON source.id=sf.payload_id
        CROSS JOIN LATERAL jsonb_array_elements_text(source.canonical::jsonb->'active_head_duids') head
        JOIN public.stewardship_snapshot_contact sc
            ON sc.snapshot_id=cur.snapshot_id AND sc.source_key='member:'||head
        JOIN public.stewardship_source_contact contact ON contact.id=sc.payload_id
        CROSS JOIN LATERAL jsonb_array_elements(contact.canonical::jsonb->'emails') email
        WHERE f.id=target_family AND f.active AND f.email_eligible
          AND email->'valid'='true'::jsonb
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_recipient_refusal refusal
              WHERE refusal.organization_id=refusal_organization AND refusal.family_duid=f.family_duid
                AND refusal.address=email->>'value'
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_recipient_resolution resolution
                    WHERE resolution.refusal_id=refusal.id)
          )
    ) INTO deliverable;
    UPDATE public.stewardship_family_campaign SET email_deliverable=deliverable,
        deliverability_reason=CASE WHEN NOT portal_eligible THEN 'ineligible'
            WHEN NOT email_eligible THEN 'no_eligible_email'
            WHEN deliverable THEN 'deliverable' ELSE 'provider_suppressed' END,
        eligibility_changed_at=public.stewardship_campaign_now_v1(),
        version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE id=target_family AND email_deliverable IS DISTINCT FROM deliverable;
    RETURN NULL;
END $$;
CREATE TRIGGER stewardship_refusal_family_effect
    AFTER INSERT ON public.stewardship_recipient_refusal
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_refusal_family_effect_v1();
CREATE TRIGGER stewardship_resolution_family_effect
    AFTER INSERT ON public.stewardship_recipient_resolution
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_refusal_family_effect_v1();
REVOKE ALL ON FUNCTION public.stewardship_refusal_family_effect_v1() FROM PUBLIC;
