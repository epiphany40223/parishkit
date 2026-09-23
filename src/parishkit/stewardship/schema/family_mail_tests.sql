-- Fresh-install chosen-Family Testing sends. An Administrator names up to ten
-- real Families; each gets its real invitation or reminder rendered with that
-- Family's own Testing code and link, routed only to the Testing recipient.
-- The ticket is the outbox message's semantic key, so a test can never create
-- or satisfy a scheduled occurrence. Only a queued ticket names its Family.
CREATE TABLE "stewardship_family_mail_test" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "template_id" uuid NOT NULL, "requested_by_id" uuid NOT NULL, "request_key" uuid NOT NULL, "sequence" bigint NOT NULL CHECK ("sequence" >= 0), "reauthenticated_at" timestamp with time zone NOT NULL, "rehearsal_epoch_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "family_id" uuid NULL, "outbox_id" uuid NULL, "state" varchar(16) DEFAULT 'queued' NOT NULL);
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "stewardship_family_m_campaign_id_ddb4c060_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "stewardship_family_m_configuration_id_b6564a95_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "stewardship_family_m_template_id_6b771ae2_fk_stewardsh" FOREIGN KEY ("template_id") REFERENCES "stewardship_content_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "stewardship_family_m_task_id_5636d29d_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "stewardship_jobs_familymailtest_positive_version" CHECK ("version" >= 1);
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "family_mail_test_request" UNIQUE ("requested_by_id", "request_key", "sequence");
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "family_mail_test_sequence" CHECK (("sequence" >= 1 AND "sequence" <= 10));
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "family_mail_test_known_state" CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'prepared'::character varying, 'cancelled'::character varying, 'failed'::character varying])::text[])));
ALTER TABLE "stewardship_family_mail_test" ADD CONSTRAINT "family_mail_test_family_scrub" CHECK ((((family_id IS NOT NULL) AND (outbox_id IS NULL) AND ((state)::text = 'queued'::text)) OR ((family_id IS NULL) AND (outbox_id IS NOT NULL) AND ((state)::text = 'prepared'::text)) OR ((family_id IS NULL) AND (outbox_id IS NULL) AND ((state)::text = ANY ((ARRAY['cancelled'::character varying, 'failed'::character varying])::text[])))));
CREATE INDEX "stewardship_family_mail_test_correlation_id_f71ae2aa" ON "stewardship_family_mail_test" ("correlation_id");
CREATE INDEX "stewardship_family_mail_test_campaign_id_ddb4c060" ON "stewardship_family_mail_test" ("campaign_id");
CREATE INDEX "stewardship_family_mail_test_configuration_id_b6564a95" ON "stewardship_family_mail_test" ("configuration_id");
CREATE INDEX "stewardship_family_mail_test_template_id_6b771ae2" ON "stewardship_family_mail_test" ("template_id");

CREATE FUNCTION public.stewardship_family_mail_test_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- Written in the quoted per-column form every generated mutable guard
    -- uses, which test_all_concrete_mutable_records_have_enabled_guard checks
    -- against the model's immutable and write-once fields.
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id"
       OR NEW."configuration_id" IS DISTINCT FROM OLD."configuration_id"
       OR NEW."template_id" IS DISTINCT FROM OLD."template_id"
       OR NEW."requested_by_id" IS DISTINCT FROM OLD."requested_by_id"
       OR NEW."request_key" IS DISTINCT FROM OLD."request_key"
       OR NEW."sequence" IS DISTINCT FROM OLD."sequence"
       OR NEW."reauthenticated_at" IS DISTINCT FROM OLD."reauthenticated_at"
       OR NEW."rehearsal_epoch_id" IS DISTINCT FROM OLD."rehearsal_epoch_id"
       OR NEW."task_id" IS DISTINCT FROM OLD."task_id"
       OR (OLD."outbox_id" IS NOT NULL AND NEW."outbox_id" IS DISTINCT FROM OLD."outbox_id") THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;

-- Durable scope of a ticket: the active configuration, current Testing draft,
-- a template a current invitation/reminder schedule uses, the requesting
-- Administrator's live authority, a public Workspace identity and the active
-- rehearsal epoch. None of these returns once lost, so losing any of them
-- cancels the ticket. Temporary gates are deliberately not part of it.
CREATE FUNCTION public.stewardship_family_test_scope_v1(
    configuration uuid, campaign uuid, template uuid, requested_by uuid, epoch uuid
) RETURNS boolean LANGUAGE sql SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_campaign c ON c.id=runtime.current_campaign_id
        JOIN public.stewardship_campaign_credentials credentials ON credentials.campaign_id=c.id
        JOIN public.stewardship_rehearsal_epoch e ON e.id=credentials.rehearsal_epoch_id
        JOIN public.stewardship_content_version content ON content.id=$3
            AND content.configuration_id=runtime.active_configuration_id
            AND content.campaign_id=c.id AND content.kind='email'
        JOIN public.stewardship_schedule_revision revision
            ON revision.values->>'template_version'=content.record_id::text
            AND revision.campaign_id=c.id
        JOIN public.stewardship_schedule_definition definition
            ON definition.current_revision_id=revision.id AND definition.kind IN ('initial','reminder')
        JOIN public.stewardship_applied_integration workspace
            ON workspace.configuration_id=runtime.active_configuration_id
            AND workspace.kind='google_workspace'
        JOIN public.stewardship_portal_user owner ON owner.id=$4 AND NOT owner.disabled
        JOIN public.stewardship_address_rule rule ON rule.configuration_id=runtime.active_configuration_id
            AND rule.email=owner.email AND rule.roles @> '["administrator"]'::jsonb
        WHERE runtime.active_configuration_id=$1 AND c.id=$2 AND runtime.mode='testing'
          AND c.state='draft' AND NOT credentials.go_live_gate
          AND e.id=$5 AND e.campaign_id=c.id AND e.state='active')
$$;

-- Liveness adds the temporary gates: a restore under review or unreleased
-- work on this campaign. Intake refuses while they hold; a queued ticket waits.
CREATE FUNCTION public.stewardship_family_test_live_v1(
    configuration uuid, campaign uuid, template uuid, requested_by uuid, epoch uuid
) RETURNS boolean LANGUAGE sql SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_family_test_scope_v1($1,$2,$3,$4,$5)
       AND NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration
           WHERE restore_review_required)
       AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
           WHERE gate.campaign_id=$2 AND gate.state<>'released')
$$;

CREATE FUNCTION public.stewardship_family_mail_test_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE stamp timestamptz:=clock_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Family test tickets are retained' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Family test requires ordered ownership' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF current_user<>'pk_stewardship_web'
           OR NOT public.stewardship_family_test_live_v1(NEW.configuration_id,NEW.campaign_id,
                NEW.template_id,NEW.requested_by_id,NEW.rehearsal_epoch_id)
           OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
           OR NEW.state<>'queued' OR NEW.version<>1
           OR NEW.family_id IS NULL OR NEW.outbox_id IS NOT NULL
           -- The Admin signed in with Google again within the last five minutes.
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_portal_session
                WHERE principal_id=NEW.requested_by_id AND revoked_at IS NULL
                    AND authenticated_at=NEW.reauthenticated_at AND expires_at>stamp
                    AND authenticated_at BETWEEN stamp-interval '5 minutes' AND stamp
                    AND last_activity_at>stamp-interval '30 minutes')
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=NEW.task_id AND task.root_id=task.id
                    AND task.task_type='family_mail_test' AND task.state='queued'
                    AND task.domain_request_id=NEW.id
                    AND task.idempotency_key=NEW.id::text
                    AND task.initiated_by_id=NEW.requested_by_id)
           -- Only a template a current invitation/reminder schedule really uses.
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_content_version content
                JOIN public.stewardship_schedule_revision revision
                    ON revision.values->>'template_version'=content.record_id::text
                    AND revision.campaign_id=NEW.campaign_id
                JOIN public.stewardship_schedule_definition definition
                    ON definition.current_revision_id=revision.id
                    AND definition.kind IN ('initial','reminder')
                WHERE content.id=NEW.template_id AND content.configuration_id=NEW.configuration_id)
           -- A real, currently deliverable Family of this campaign.
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign f
                JOIN public.stewardship_campaign_credentials k ON k.campaign_id=f.campaign_id
                JOIN public.stewardship_source_current s ON s.snapshot_id=k.source_snapshot_id
                    AND s.generation=k.source_generation
                WHERE f.id=NEW.family_id AND f.campaign_id=NEW.campaign_id
                    AND f.active AND f.portal_eligible AND f.email_eligible AND f.email_deliverable
                    AND NOT k.population_dirty AND f.source_generation=s.generation) THEN
            RAISE EXCEPTION 'Family test requires explicit current Admin intent'
                USING ERRCODE='23514';
        END IF;
        -- Bound unsettled tests per campaign: queued tickets plus prepared
        -- messages that have not reached a terminal delivery state.
        IF (SELECT count(*) FROM public.stewardship_family_mail_test
                WHERE campaign_id=NEW.campaign_id AND state='queued')
           +(SELECT count(*) FROM public.stewardship_outbox_message
                WHERE campaign_id=NEW.campaign_id AND purpose='family_test'
                    AND state NOT IN ('delivered','permanent_failure','cancelled'))>=10 THEN
            RAISE EXCEPTION 'Too many Family tests are in progress' USING ERRCODE='23514';
        END IF;
        NEW.created_at:=stamp; NEW.updated_at:=stamp;
        RETURN NEW;
    END IF;
    IF OLD.state<>'queued' THEN
        RAISE EXCEPTION 'Terminal Family test cannot be rewritten' USING ERRCODE='23514';
    END IF;
    IF NEW.state='prepared' THEN
        -- Only the general worker, under its live claim, with the outbox
        -- message already allocated for this exact ticket and Family.
        IF current_user<>'pk_stewardship_worker' OR NEW.family_id IS NOT NULL
           OR NEW.outbox_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_task_run t
            JOIN public.stewardship_outbox_message m ON m.id=NEW.outbox_id
            WHERE t.id=NEW.correlation_id AND t.root_id=OLD.task_id
              AND t.task_type='family_mail_test' AND t.state='running'
              AND t.worker_id=NEW.actor_id AND t.lease_expires_at>stamp
              AND t.domain_request_id=OLD.id
              AND m.semantic_key=OLD.id AND m.campaign_id=OLD.campaign_id
              AND m.family_id=OLD.family_id AND m.purpose='family_test' AND m.mode='testing'
              AND m.rehearsal_epoch_id=OLD.rehearsal_epoch_id AND m.state='pending'
              AND m.correlation_id=t.id AND m.actor_id=t.worker_id) THEN
            RAISE EXCEPTION 'Only the live preparation worker may complete a Family test'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    -- The scheduler settles stale tickets: failed when the task failed without
    -- a message, cancelled when the durable scope is gone or the worker safely
    -- cancelled the task (a lastingly ineligible Family). Temporary gates
    -- (restore review, campaign work) leave a queued ticket waiting.
    IF current_user<>'pk_stewardship_scheduler' OR NEW.actor_id IS NOT NULL
       OR NEW.family_id IS NOT NULL OR NEW.outbox_id IS NOT NULL
       OR NOT ((NEW.state='failed' AND EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=OLD.task_id AND task.state='failed'))
           OR (NEW.state='cancelled' AND (EXISTS (SELECT 1 FROM public.stewardship_task_run task
                WHERE task.id=OLD.task_id AND task.state='cancelled')
             OR NOT public.stewardship_family_test_scope_v1(
                OLD.configuration_id,OLD.campaign_id,OLD.template_id,OLD.requested_by_id,
                OLD.rehearsal_epoch_id)))) THEN
        RAISE EXCEPTION 'Only stale unsent Family tests can be settled' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_family_mail_test_audit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE event_id uuid:=gen_random_uuid();
BEGIN
    -- No Family, template content or recipient enters the audit record.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event_id,clock_timestamp(),NEW.actor_id,NEW.correlation_id,
        'family_mail_test_'||NEW.state,NEW.id,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.correlation_id,event_id,
        CASE WHEN TG_OP='INSERT' THEN 'portal_user' ELSE 'system' END,'action',
        jsonb_build_object('version',NEW.version,'outcome',CASE
            WHEN NEW.state='prepared' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN NEW.state='failed' THEN 'failed'
            ELSE 'started' END));
    RETURN NULL;
END $$;

CREATE TRIGGER family_mail_test_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_family_mail_test
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_test_guard_v1();
CREATE TRIGGER family_mail_test_audit AFTER INSERT OR UPDATE ON public.stewardship_family_mail_test
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_test_audit_v1();
CREATE TRIGGER stewardship_family_mail_test_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_family_mail_test
FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_mail_test_mutable_v1();
REVOKE ALL ON FUNCTION public.stewardship_family_mail_test_guard_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_mail_test_audit_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_family_mail_test_mutable_v1() FROM PUBLIC;

-- A test render is the ticket's template record, taken from the currently
-- active configuration as scheduled mail does, with the mandatory Testing
-- banner in every part. Used by preparation and by every later dispatch.
CREATE FUNCTION public.stewardship_family_test_render_admitted_v1(proposed jsonb, message uuid)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE m public.stewardship_outbox_message%ROWTYPE;
    q public.stewardship_family_mail_test%ROWTYPE;
    configuration uuid; template uuid;
BEGIN
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=message;
    SELECT * INTO q FROM public.stewardship_family_mail_test
        WHERE id=m.semantic_key AND campaign_id=m.campaign_id;
    IF m.id IS NULL OR q.id IS NULL OR m.purpose<>'family_test' OR m.mode<>'testing' THEN
        RETURN false; END IF;
    SELECT active_configuration_id INTO configuration FROM public.stewardship_system_configuration;
    SELECT current.id INTO template FROM public.stewardship_content_version current
        JOIN public.stewardship_content_version original
          ON original.record_id=current.record_id AND original.campaign_id=current.campaign_id
        WHERE original.id=q.template_id AND current.configuration_id=configuration
          AND current.campaign_id=m.campaign_id AND current.kind='email';
    RETURN public.stewardship_family_mail_render_core_v1(proposed,m.family_id,configuration,template,'testing') IS TRUE
       AND proposed->>'subject' LIKE '[TEST] %'
       AND proposed->>'html' LIKE '<h2>TEST</h2>%'
       AND proposed->>'text' LIKE 'TEST — sent to %';
END $$;

-- The general worker's admission for every row a Family test preparation
-- inserts: the Family's rehearsal credential and code fingerprints, then the
-- outbox message, its first render and its first history event.
CREATE FUNCTION public.stewardship_family_test_write_admitted_v1(table_name text, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE t public.stewardship_task_run%ROWTYPE;
    q public.stewardship_family_mail_test%ROWTYPE;
    c public.stewardship_campaign%ROWTYPE;
    r public.stewardship_system_configuration%ROWTYPE;
    k public.stewardship_campaign_credentials%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
BEGIN
    IF table_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render',
        'stewardship_outbox_event','stewardship_rehearsal_credential',
        'stewardship_rehearsal_code_mac') THEN RETURN false; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    IF t.id IS NULL OR t.task_type<>'family_mail_test' OR t.state<>'running'
       OR t.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
       OR t.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    SELECT * INTO q FROM public.stewardship_family_mail_test
        WHERE id=t.domain_request_id AND task_id=t.root_id;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=q.campaign_id;
    SELECT * INTO r FROM public.stewardship_system_configuration;
    SELECT * INTO k FROM public.stewardship_campaign_credentials WHERE campaign_id=c.id;
    -- The durable scope repeats the requesting Administrator's live authority
    -- and the active-configuration binding; a revoked Admin's ticket is not
    -- prepared even before the scheduler sweep has cancelled it.
    IF q.id IS NULL OR q.state<>'queued' OR c.id IS DISTINCT FROM r.current_campaign_id
       OR public.stewardship_family_test_scope_v1(q.configuration_id,q.campaign_id,
            q.template_id,q.requested_by_id,q.rehearsal_epoch_id) IS NOT TRUE
       OR r.restore_review_required OR k.population_dirty
       OR EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate
           WHERE campaign_id=c.id AND state<>'released')
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_current s
           WHERE s.snapshot_id=k.source_snapshot_id AND s.generation=k.source_generation)
       THEN RETURN false; END IF;
    IF table_name='stewardship_rehearsal_credential' THEN
        RETURN (proposed->>'epoch_id')::uuid=q.rehearsal_epoch_id
           AND (proposed->>'family_id')::uuid=q.family_id;
    END IF;
    IF table_name='stewardship_rehearsal_code_mac' THEN
        RETURN EXISTS (SELECT 1 FROM public.stewardship_rehearsal_credential credential
            WHERE credential.id=(proposed->>'credential_id')::uuid
              AND credential.epoch_id=(proposed->>'epoch_id')::uuid
              AND credential.epoch_id=q.rehearsal_epoch_id AND credential.family_id=q.family_id
              AND credential.correlation_id=t.id AND credential.actor_id=t.worker_id);
    END IF;
    IF table_name='stewardship_outbox_message' THEN
        RETURN proposed->>'action'='created' AND proposed->>'state'='pending'
           AND (proposed->>'semantic_key')::uuid=q.id
           AND (proposed->>'campaign_id')::uuid=c.id AND proposed->>'mode'='testing'
           AND proposed->>'purpose'='family_test' AND proposed->>'routing'='testing_override'
           AND (proposed->>'family_id')::uuid=q.family_id
           AND (proposed->>'rehearsal_epoch_id')::uuid=q.rehearsal_epoch_id
           AND proposed->>'credential_namespace'='rehearsal'
           AND EXISTS (SELECT 1 FROM public.stewardship_family_campaign f
               JOIN public.stewardship_source_current s ON s.snapshot_id=k.source_snapshot_id
               WHERE f.id=q.family_id AND f.campaign_id=c.id
                 AND f.active AND f.portal_eligible AND f.email_eligible AND f.email_deliverable
                 AND f.source_generation=s.generation);
    END IF;
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=(proposed->>'message_id')::uuid;
    IF m.id IS NULL OR m.semantic_key<>q.id OR m.purpose<>'family_test'
       OR m.correlation_id<>t.id OR m.actor_id IS DISTINCT FROM t.worker_id
       OR m.state<>'pending' OR m.version<>1 THEN RETURN false; END IF;
    IF table_name='stewardship_outbox_event' THEN RETURN proposed->>'version'='1'; END IF;
    RETURN m.render_id=(proposed->>'id')::uuid
       AND public.stewardship_family_test_render_admitted_v1(proposed,m.id) IS TRUE;
END $$;

-- Dispatch admission for a prepared test: the same Testing draft/epoch/source
-- scope as scheduled Testing mail, minus campaign dates, occurrences,
-- fulfillment and the responded-Family skip. Cancelled by scope replacement.
CREATE FUNCTION public.stewardship_family_test_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_family_mail_test q ON q.id=m.semantic_key AND q.outbox_id=m.id
            AND q.campaign_id=m.campaign_id AND q.rehearsal_epoch_id=m.rehearsal_epoch_id
        JOIN public.stewardship_campaign c ON c.id=m.campaign_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        JOIN public.stewardship_source_current source ON source.snapshot_id=k.source_snapshot_id
            AND source.generation=k.source_generation
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
        WHERE m.id=message AND m.purpose='family_test' AND q.state='prepared'
          AND m.mode='testing' AND m.routing='testing_override' AND r.mode='testing' AND c.state='draft'
          AND m.credential_namespace='rehearsal' AND m.rehearsal_epoch_id=k.rehearsal_epoch_id
          AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch e
              WHERE e.id=m.rehearsal_epoch_id AND e.campaign_id=c.id AND e.state='active')
          AND NOT r.restore_review_required AND NOT k.go_live_gate AND NOT k.population_dirty
          AND f.source_generation=source.generation
          AND f.active AND f.portal_eligible AND f.email_eligible AND f.email_deliverable
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
              WHERE gate.campaign_id=c.id AND gate.state<>'released')
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message unresolved
              WHERE unresolved.family_id=f.id AND unresolved.campaign_id=c.id AND unresolved.mode=m.mode
                AND unresolved.id<>m.id AND unresolved.state IN ('submitting','delivery_unknown'))
    )
$$;
