-- Frozen forward migration file 0173: Send a test to me for report emails
-- (#2036, report-emails spec "Send a test to me", delivery plan item 7).
-- This file is installed by its Django migration in the reports app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- An Administrator's Send a test to me records one
-- stewardship_report_email_test: the report (none for an unsaved New
-- form), the email the web compiled from current data (as Preview draws
-- it) and its images, and its report_email_test task. Its guard admits
-- the insert only from the web login, under the work lock, for a current
-- Administrator, under the active configuration, mode and Testing
-- recipient, for the current campaign unless it is archived or its
-- Production mail is paused, and fills nothing from the form it can read
-- itself: the address is the Administrator's own sign-in address. A
-- partial unique index allows one queued test per Administrator and
-- report (NULLS NOT DISTINCT, so the unsaved form's tests share one).
--
-- The worker's report_email_test task (added to the two task-type maps)
-- makes one outbox message of the new admin_report_test owner: no Family,
-- credential namespace none, semantic key the request's id, routed to the
-- Testing recipient in Testing; mail dispatch sends exactly the kept email.
-- So four shared functions are replaced in place, each its 0161 body with
-- only that change (their final bodies are also the baseline's): the
-- worker's outbox insert gate and mail dispatch's write gate (one more
-- owner), the provider result check (the purpose list), and the delivery
-- resolution guard, which settles a test from evidence only, as it does a
-- chosen-Family test. A test is never an occurrence, fulfillment,
-- preparation, snapshot or pause hold: none of those paths names it.
--
-- Once its message is delivered, failed or cancelled (or removed), or its
-- task ends without one, the database settles the request and scrubs the
-- kept email (stewardship_report_email_test_settle_v1, from triggers on the
-- outbox message and the task run), so the next test may be queued and no
-- copy of the email or its images stays on the request; the message's
-- stored render keeps its subject, HTML and text, as every report email's
-- does. A message whose delivery cannot be prepared within its attempts is
-- cancelled as not sent, which settles it. The guard, the settlement and
-- the scrub are SECURITY DEFINER, stated again with ALTER as every definer
-- is (the resolution guard too); every other new function is invoker's
-- rights.
-- Table grants are runtime_grants' (reports/report_email_grants.py). The
-- file ends with a DO block that refuses to commit unless all of it is
-- installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The request table (reports/report_email_models.ReportEmailTest).
CREATE TABLE "stewardship_report_email_test" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "definition_id" uuid NULL, "requested_by_id" uuid NOT NULL, "mode" varchar(16) NOT NULL, "testing_recipient" varchar(254) NULL, "address" varchar(254) NOT NULL, "subject" varchar(254) NOT NULL, "html" text NOT NULL, "text" text NOT NULL, "images" jsonb NOT NULL, "task_id" uuid NOT NULL UNIQUE, "state" varchar(16) DEFAULT 'queued' NOT NULL);
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "stewardship_report_e_campaign_id_985b4554_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "stewardship_report_e_configuration_id_9ae493df_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "stewardship_report_e_task_id_3bb8311a_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "stewardship_reports_reportemailtest_positive_version" CHECK ("version" >= 1);
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "report_email_test_mode" CHECK ((("mode" = 'production' AND "testing_recipient" IS NULL) OR ("mode" = 'testing' AND "testing_recipient" IS NOT NULL)));
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "report_email_test_known_state" CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'settled'::character varying])::text[])));
ALTER TABLE "stewardship_report_email_test" ADD CONSTRAINT "report_email_test_scrubbed" CHECK (("state" = 'queued' OR ("html" = '' AND "images" = '[]'::jsonb AND "subject" = '' AND "text" = '')));
CREATE UNIQUE INDEX "report_email_test_pending" ON "stewardship_report_email_test" ("requested_by_id", "definition_id") NULLS NOT DISTINCT WHERE "state" = 'queued';
CREATE INDEX "stewardship_report_email_test_correlation_id_2288a56c" ON "stewardship_report_email_test" ("correlation_id");
CREATE INDEX "stewardship_report_email_test_campaign_id_985b4554" ON "stewardship_report_email_test" ("campaign_id");
CREATE INDEX "stewardship_report_email_test_configuration_id_9ae493df" ON "stewardship_report_email_test" ("configuration_id");

-- Every update advances the version and keeps the identity and bindings,
-- in the quoted per-column form every generated mutable guard uses, which
-- test_all_concrete_mutable_records_have_enabled_guard checks against the
-- model's immutable fields.
CREATE FUNCTION public.stewardship_report_email_test_mutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id"
       OR NEW."configuration_id" IS DISTINCT FROM OLD."configuration_id"
       OR NEW."definition_id" IS DISTINCT FROM OLD."definition_id"
       OR NEW."requested_by_id" IS DISTINCT FROM OLD."requested_by_id"
       OR NEW."mode" IS DISTINCT FROM OLD."mode"
       OR NEW."testing_recipient" IS DISTINCT FROM OLD."testing_recipient"
       OR NEW."address" IS DISTINCT FROM OLD."address"
       OR NEW."task_id" IS DISTINCT FROM OLD."task_id" THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_report_email_test_mutable_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_report_email_test_mutable_guard_v1 BEFORE UPDATE
    ON public.stewardship_report_email_test
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_test_mutable_v1();

-- Whether a test is over: no run of its task is still open, and its
-- message, if it has one, is delivered, failed or cancelled. A message
-- whose delivery is unknown is not over: mail dispatch may still retry it
-- idempotently, and an Administrator settles it on Sent mail.
CREATE FUNCTION public.stewardship_report_email_test_settled_v1(request uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_test t WHERE t.id=$1)
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_test t
          JOIN public.stewardship_task_run run ON run.root_id=t.task_id
          WHERE t.id=$1 AND run.state IN ('queued','running','retry_wait','abandoned'))
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_test t
          JOIN public.stewardship_outbox_message m ON m.semantic_key=t.id
              AND m.scope_id=t.campaign_id AND m.purpose='admin_report_test'
          WHERE t.id=$1 AND m.state NOT IN ('delivered','permanent_failure','cancelled'))
$$;

-- The request's guard, with owner rights: web may insert a request but
-- reads neither the users nor the address rules it checks. INSERT: only
-- the web login, under the work lock (web's work transaction), for an
-- enabled Administrator, whose own sign-in address it must name, with the
-- task the same transaction queued for it, under the active configuration,
-- mode and Testing recipient, for the current campaign unless archived or,
-- in Production, while its mail is paused, and for a report of that
-- campaign the applied configuration still holds (or none, for an unsaved
-- New form). The email is bounded as a report snapshot's is. UPDATE: only
-- the settlement, which scrubs the email once the test is over. DELETE:
-- never.
CREATE FUNCTION public.stewardship_report_email_test_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE runtime public.stewardship_system_configuration%ROWTYPE;
    c public.stewardship_campaign%ROWTYPE; own text;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Report email tests are retained' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.state<>'queued' OR NEW.state<>'settled'
           OR NEW.subject<>'' OR NEW.html<>'' OR NEW.text<>'' OR NEW.images<>'[]'::jsonb
           OR public.stewardship_report_email_test_settled_v1(NEW.id) IS NOT TRUE THEN
            RAISE EXCEPTION 'Only a report email test that is over can be settled' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Report email test requires ordered ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO runtime FROM public.stewardship_system_configuration;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    SELECT lower(u.email) INTO own FROM public.stewardship_portal_user u
        WHERE u.id=NEW.requested_by_id AND NOT u.disabled;
    IF session_user<>'pk_stewardship_web'
       OR NEW.actor_id IS DISTINCT FROM NEW.requested_by_id
       OR public.stewardship_export_authorized_v1(NEW.requested_by_id,true) IS NOT TRUE
       OR own IS NULL OR NEW.address IS DISTINCT FROM own
       OR NEW.state<>'queued' OR NEW.version<>1
       OR NEW.configuration_id IS DISTINCT FROM runtime.active_configuration_id
       OR c.id IS NULL OR c.id IS DISTINCT FROM runtime.current_campaign_id
       OR c.state IN ('archived','purging','purge_cleanup_failed','purged')
       OR NEW.mode IS DISTINCT FROM runtime.mode
       -- Parenthesized: PL/pgSQL's IF would end at the CASE's THEN.
       OR NEW.testing_recipient IS DISTINCT FROM
           (CASE WHEN runtime.mode='testing' THEN runtime.testing_recipient END)
       OR (NEW.definition_id IS NOT NULL AND NOT EXISTS(
           SELECT 1 FROM public.stewardship_schedule_definition d
           JOIN public.stewardship_schedule_revision v ON v.record_id=d.id
               AND v.configuration_id=runtime.active_configuration_id
           WHERE d.id=NEW.definition_id AND d.campaign_id=c.id AND d.kind='report'))
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run task
           WHERE task.id=NEW.task_id AND task.root_id=task.id
             AND task.task_type='report_email_test' AND task.state='queued'
             AND task.domain_request_id=NEW.id AND task.idempotency_key=NEW.id::text
             AND task.initiated_by_id=NEW.requested_by_id)
       OR length(NEW.subject)=0 OR NEW.subject ~ '[\r\n]' OR length(NEW.subject)>247
       OR octet_length(NEW.html) NOT BETWEEN 1 AND 8388608
       OR octet_length(NEW.text) NOT BETWEEN 1 AND 8388608
       OR jsonb_typeof(NEW.images) IS DISTINCT FROM 'array'
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.images) image
           WHERE jsonb_typeof(image)<>'array' OR jsonb_array_length(image)<>2
             OR image->>0 !~ '^[A-Za-z0-9._@-]{1,64}$'
             OR substring(decode(image->>1,'base64') FROM 1 FOR 8)<>decode('89504e470d0a1a0a','hex')
             OR octet_length(decode(image->>1,'base64'))>1048576) THEN
        RAISE EXCEPTION 'Report email test requires a current Administrator and the current report'
            USING ERRCODE='23514';
    END IF;
    IF runtime.mode='production' AND c.delivery_paused THEN
        RAISE EXCEPTION 'Report email tests wait while mail is paused' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_report_email_test_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_test_guard_v1() FROM PUBLIC;
CREATE TRIGGER report_email_test_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_report_email_test
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_test_guard_v1();

-- The settlement: once a test is over, its request is settled and its
-- email scrubbed, with owner rights (no login updates the table). Fired by
-- its message's state change or removal and by its task's end; each
-- trigger is limited to report email tests, and a test not yet over is
-- left as it is.
CREATE FUNCTION public.stewardship_report_email_test_settle_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request uuid;
BEGIN
    -- Separate statements, since each table's row has its own fields.
    IF TG_TABLE_NAME='stewardship_task_run' THEN
        request:=NEW.domain_request_id;
    ELSIF TG_OP='DELETE' THEN
        request:=OLD.semantic_key;
    ELSE
        request:=NEW.semantic_key;
    END IF;
    IF public.stewardship_report_email_test_settled_v1(request) THEN
        UPDATE public.stewardship_report_email_test
            SET state='settled',subject='',html='',text='',images='[]'::jsonb,version=version+1
            WHERE id=request AND state='queued';
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION public.stewardship_report_email_test_settle_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_test_settle_v1() FROM PUBLIC;
CREATE TRIGGER report_email_test_settle AFTER UPDATE OF state
    ON public.stewardship_outbox_message FOR EACH ROW
    WHEN (NEW.purpose='admin_report_test' AND NEW.state IN ('delivered','permanent_failure','cancelled'))
    EXECUTE FUNCTION public.stewardship_report_email_test_settle_v1();
CREATE TRIGGER report_email_test_removed AFTER DELETE
    ON public.stewardship_outbox_message FOR EACH ROW
    WHEN (OLD.purpose='admin_report_test')
    EXECUTE FUNCTION public.stewardship_report_email_test_settle_v1();
CREATE TRIGGER report_email_test_ended AFTER UPDATE OF state
    ON public.stewardship_task_run FOR EACH ROW
    WHEN (NEW.task_type='report_email_test' AND NEW.state IN ('succeeded','failed','cancelled'))
    EXECUTE FUNCTION public.stewardship_report_email_test_settle_v1();

-- Whether a queued test may still be made and sent: its campaign is the
-- current one in the test's mode (in Testing, to the same Testing
-- recipient), no restore, go-live or work gate holds it, the campaign may
-- send in that mode as a report send may, and its address is still an
-- Administrator's in the active configuration.
CREATE FUNCTION public.stewardship_report_email_test_live_v1(request uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_test t
        JOIN public.stewardship_campaign c ON c.id=t.campaign_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        WHERE t.id=$1 AND t.state='queued' AND r.mode=t.mode
          AND t.testing_recipient IS NOT DISTINCT FROM
              CASE WHEN r.mode='testing' THEN r.testing_recipient END
          AND NOT r.restore_review_required AND NOT k.go_live_gate
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_work_gate g
              WHERE g.campaign_id=c.id AND g.state<>'released')
          AND ((t.mode='production' AND c.state IN ('scheduled','active','closed'))
              OR (t.mode='testing' AND c.state='draft'))
          AND public.stewardship_report_email_current_v1(NULL,'group','administrators',t.address))
$$;

-- Whether a render (as JSON) is a queued test's exact email: the active
-- configuration's sender, the Administrator's address as the one intended
-- recipient, the Testing recipient instead in Testing, "[TEST] " before
-- the subject and the test's first line before the kept email.
CREATE FUNCTION public.stewardship_report_email_test_render_v1(proposed jsonb, request uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_test t
        CROSS JOIN public.stewardship_system_configuration runtime
        JOIN public.stewardship_applied_integration email
            ON email.configuration_id=runtime.active_configuration_id AND email.kind='email'
        WHERE t.id=$2 AND t.state='queued'
          AND (proposed->>'configuration_id')::uuid=runtime.active_configuration_id
          AND proposed->>'template_id' IS NULL
          AND proposed->>'sender'=email.settings->>'sender'
          AND proposed->>'reply_to'=email.settings->>'reply_to'
          AND proposed->'intended_recipients'=jsonb_build_array(t.address)
          AND proposed->'routed_recipients'=jsonb_build_array(coalesce(t.testing_recipient,t.address))
          AND proposed->>'subject'='[TEST] '||t.subject
          AND starts_with(proposed->>'html','<h2>TEST</h2><p>')
          AND right(proposed->>'html',length(t.html))=t.html
          AND proposed->>'text'=CASE t.mode WHEN 'testing'
              THEN 'TEST — sent to '||t.testing_recipient||' instead of you ('||t.address
                  ||'). It is a test of this report email, not a send of the report.'
              ELSE 'TEST — a test of this report email, sent only to you. It is not a send of the report.'
              END||E'\n\n'||t.text)
$$;

-- The worker's outbox inserts for one test (the outbox insert gate admits
-- them through this): under the work lock and the test task's live claim,
-- one pending admin_report_test message whose semantic key is the request,
-- its creation event and its render, the request's exact email.
CREATE FUNCTION public.stewardship_report_email_test_write_v1(relation_name text, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE t public.stewardship_task_run%ROWTYPE; r public.stewardship_report_email_test%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    IF t.id IS NULL OR t.task_type<>'report_email_test' OR t.state<>'running'
      OR t.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
      OR t.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    SELECT * INTO r FROM public.stewardship_report_email_test
      WHERE id=t.domain_request_id AND task_id=t.root_id;
    IF r.id IS NULL OR public.stewardship_report_email_test_live_v1(r.id) IS NOT TRUE THEN
      RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
      RETURN proposed->>'purpose'='admin_report_test' AND (proposed->>'semantic_key')::uuid=r.id
        AND proposed->>'routing'=CASE r.mode WHEN 'testing' THEN 'testing_override' ELSE 'production' END
        AND proposed->>'credential_namespace'='none' AND proposed->>'family_id' IS NULL
        AND (proposed->>'campaign_id')::uuid=r.campaign_id AND (proposed->>'scope_id')::uuid=r.campaign_id
        AND proposed->>'mode'=r.mode AND proposed->>'state'='pending'
        AND proposed->>'action'='created' AND proposed->>'version'='1';
    END IF;
    SELECT * INTO m FROM public.stewardship_outbox_message
      WHERE id=(proposed->>'message_id')::uuid AND correlation_id=t.id AND actor_id=t.worker_id
        AND purpose='admin_report_test' AND semantic_key=r.id;
    IF m.id IS NULL OR m.state<>'pending' OR m.version<>1 THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_event' THEN
      RETURN proposed->>'action'='created' AND proposed->>'state'='pending'
        AND (proposed->>'version')::bigint=1;
    END IF;
    RETURN (proposed->>'id')::uuid=m.render_id
      AND public.stewardship_report_email_test_render_v1(proposed,r.id);
END $$;

-- A test message may be submitted only while its test is live and, in
-- Production, its campaign's mail is not paused: a paused test waits (it
-- is in no pause hold) and is sent after Resume.
CREATE FUNCTION public.stewardship_report_email_test_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_report_email_test t ON t.id=m.semantic_key
        JOIN public.stewardship_campaign c ON c.id=t.campaign_id
        WHERE m.id=$1 AND m.purpose='admin_report_test' AND m.credential_namespace='none'
          AND m.family_id IS NULL AND m.campaign_id=t.campaign_id AND m.mode=t.mode
          AND NOT (m.mode='production' AND c.delivery_paused)
          AND public.stewardship_report_email_test_live_v1(t.id))
$$;

-- Mail dispatch's writes to one test message, under its live delivery
-- claim: its render (the kept email), its provider outcome and its
-- cancellation. No hold or release: a test is in no pause hold.
CREATE FUNCTION public.stewardship_report_email_test_dispatch_write_v1(relation_name text,proposed jsonb,prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task public.stewardship_task_run%ROWTYPE; message public.stewardship_outbox_message%ROWTYPE;
        r public.stewardship_report_email_test%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO task FROM public.stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    SELECT * INTO message FROM public.stewardship_outbox_message
        WHERE id=task.domain_request_id AND task_id=task.root_id AND purpose='admin_report_test';
    SELECT * INTO r FROM public.stewardship_report_email_test WHERE id=message.semantic_key;
    IF task.id IS NULL OR task.task_type<>'outbox_delivery' OR message.id IS NULL OR r.id IS NULL
      OR message.campaign_id IS DISTINCT FROM r.campaign_id OR message.mode IS DISTINCT FROM r.mode
      OR (CASE WHEN relation_name='stewardship_outbox_message' THEN proposed->>'id'
            ELSE proposed->>'message_id' END)::uuid IS DISTINCT FROM message.id
    THEN RETURN false; END IF;
    -- Recovery under the abandoned task: an uncertain submission past its
    -- deadline is marked unknown; and a test whose delivery spent its
    -- preparation budget is cancelled, as a chosen-Family test is
    -- (family_dispatch_write_v1): nothing else would settle it, its test
    -- would stay pending and keep its email, and in Testing an unsent
    -- message blocks go-live's Production cleanup. Only a definitely unsent
    -- message qualifies (never submitted, or waiting after the provider's
    -- definite non-acceptance); the literal 5 mirrors MAX_ATTEMPTS in
    -- jobs/family_mail_dispatch.py, which a pure test pins.
    IF task.state='abandoned' THEN
        RETURN (task.id=message.run_id AND task.fence>=message.task_fence
          AND message.provider_deadline<=clock_timestamp() AND proposed->>'actor_id' IS NOT NULL
          AND proposed->>'action'='mark_unknown' AND proposed->>'reason'='recovery_unknown'
          AND ((relation_name='stewardship_outbox_message' AND prior->>'state'='submitting')
            OR (relation_name='stewardship_outbox_event' AND message.state='delivery_unknown')))
        OR (task.attempt>=5 AND proposed->>'actor_id' IS NOT NULL
          AND proposed->>'action'='cancel_unsent' AND proposed->>'reason'='preparation_failed'
          AND ((relation_name='stewardship_outbox_message'
                AND ((message.state='pending' AND message.attempt=0)
                  OR (message.state='retry_wait' AND message.action='retry_unaccepted')))
            OR (relation_name='stewardship_outbox_event' AND message.state='cancelled'
                AND message.action='cancel_unsent' AND message.reason='preparation_failed')));
    END IF;
    IF task.state<>'running' OR task.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
      OR task.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
        IF prior IS NULL THEN RETURN false; END IF;
        IF proposed->>'action' IN ('prepared','submit') THEN
            RETURN public.stewardship_report_email_test_dispatch_live_v1(message.id) AND EXISTS(
                SELECT 1 FROM public.stewardship_outbox_render rendering
                WHERE rendering.id=(proposed->>'render_id')::uuid AND rendering.message_id=message.id
                  AND public.stewardship_report_email_test_render_v1(to_jsonb(rendering),r.id));
        ELSIF proposed->>'action' IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
            RETURN prior->>'state'='submitting' AND (prior->>'run_id')::uuid=task.id
              AND (prior->>'task_fence')::bigint=task.fence AND (prior->>'worker_id')::uuid=task.worker_id
              AND proposed->>'reason' LIKE 'smtp_%';
        END IF;
        RETURN proposed->>'action'='cancel_unsent';
    ELSIF relation_name='stewardship_outbox_render' THEN
        RETURN prior IS NULL AND message.state IN ('pending','retry_wait')
          AND (proposed->>'message_id')::uuid=message.id
          AND public.stewardship_report_email_test_dispatch_live_v1(message.id)
          AND public.stewardship_report_email_test_render_v1(proposed,r.id);
    END IF;
    RETURN relation_name='stewardship_outbox_event' AND prior IS NULL
        AND proposed->>'action'=message.action AND (proposed->>'version')::bigint=message.version;
END $$;

-- The task-type maps gain report_email_test: web creates it, the general
-- worker runs it (frozen file 0161's bodies, with that one line each).
CREATE OR REPLACE FUNCTION public.stewardship_task_type_creators_v1(task_type text) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE task_type
        -- The logins whose code creates each type, by enqueue or explicit
        -- retry (tests/stewardship/test_task_creators.py pins every call
        -- site). NULL is an unknown type.
        WHEN 'activation_catchup' THEN ARRAY['pk_stewardship_web']
        WHEN 'automation_maintenance' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'branding_cleanup' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'campaign_boundary' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'campaign_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'census_publication' THEN ARRAY['pk_stewardship_web']
        WHEN 'census_publication_preflight' THEN ARRAY['pk_stewardship_web']
        WHEN 'family_address_retry' THEN ARRAY[]::text[]
        WHEN 'family_mail_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'family_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'leader_email_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_collect' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_slack' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'outbox_delivery' THEN ARRAY['pk_stewardship_web','pk_stewardship_worker']
        WHEN 'production_cleanup' THEN ARRAY['pk_stewardship_web']
        WHEN 'production_token_cleanup' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'production_tokens' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_email_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_email_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_email_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'report_exact_export' THEN ARRAY['pk_stewardship_web']
        WHEN 'report_export' THEN ARRAY['pk_stewardship_web','pk_stewardship_worker']
        WHEN 'report_export_cleanup' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_fact_verification' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'report_facts' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'security_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_finalize' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'setup_source_cleanup' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_source_load' THEN ARRAY['pk_stewardship_web']
        WHEN 'source_refresh' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web','pk_stewardship_worker']
    END $$;

CREATE OR REPLACE FUNCTION public.stewardship_task_type_login_v1(task_type text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE
        -- The login whose compiled registry executes each task type
        -- (runtime_background.py): general-queue types run in the worker,
        -- mail-queue types in mail dispatch. NULL is an unknown type.
        WHEN task_type IN (
            'activation_catchup',
            'automation_maintenance',
            'branding_cleanup',
            'campaign_boundary',
            'census_publication',
            'census_publication_preflight',
            'family_mail_prepare',
            'family_mail_test',
            'leader_email_prepare',
            'operational_collect',
            'operational_prepare',
            'operational_slack',
            'production_cleanup',
            'production_token_cleanup',
            'production_tokens',
            'report_email_finalize',
            'report_email_prepare',
            'report_email_test',
            'report_exact_export',
            'report_export',
            'report_export_cleanup',
            'report_fact_verification',
            'report_facts',
            'security_prepare',
            'setup_finalize',
            'setup_source_cleanup',
            'setup_source_load',
            'source_refresh')
        THEN 'pk_stewardship_worker'
        WHEN task_type IN (
            'campaign_mail_test',
            'family_address_retry',
            'outbox_delivery',
            'setup_mail_test')
        THEN 'pk_stewardship_mail_dispatch'
    END $$;

-- The worker's outbox insert gate gains the test's owner.
CREATE OR REPLACE FUNCTION public.stewardship_family_mail_outbox_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
       public.stewardship_family_mail_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_family_test_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_report_email_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_ops_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_security_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_leader_email_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_report_email_test_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE THEN
        RAISE EXCEPTION 'Outbox insertion requires current preparation ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- Mail dispatch's write gate gains the test's owner.
CREATE OR REPLACE FUNCTION public.stewardship_family_dispatch_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE t public.stewardship_task_run%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
    own public.stewardship_outbox_message%ROWTYPE;
    proposed jsonb; rendering jsonb; recovering boolean;
BEGIN
    IF current_user<>'pk_stewardship_mail_dispatch' THEN RETURN NEW; END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Family dispatch requires work ownership' USING ERRCODE='23514'; END IF;
    proposed:=to_jsonb(NEW);
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=NEW.correlation_id;
    SELECT * INTO own FROM public.stewardship_outbox_message
        WHERE id=t.domain_request_id AND task_id=t.root_id AND purpose IN ('initial','reminder','receipt','family_test','admin_report','admin_report_test','operational','security_event','leader_email');
    IF own.purpose='operational' THEN
        IF public.stewardship_ops_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Operational dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF own.purpose='security_event' THEN
        IF public.stewardship_security_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Security dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF own.purpose='leader_email' THEN
        IF public.stewardship_leader_email_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Ministry leader email dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    -- A report email (#1786); the digest messages it replaced are all settled.
    IF own.purpose='admin_report' THEN
        IF public.stewardship_report_email_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Report email dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    -- Send a test to me (#2036): one Administrator's test of a report,
    -- which has no occurrence, fulfillment or pause hold.
    IF own.purpose='admin_report_test' THEN
        IF public.stewardship_report_email_test_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Report email test dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    recovering:=t.state='abandoned' AND own.provider_deadline<=clock_timestamp()
        AND own.run_id=t.id AND own.task_fence<=t.fence AND NEW.actor_id IS NOT NULL
        AND proposed->>'reason'='recovery_unknown'
        AND ((TG_TABLE_NAME='stewardship_outbox_message' AND proposed->>'id'=own.id::text
                AND proposed->>'action'='mark_unknown' AND own.state='submitting')
            OR (TG_TABLE_NAME='stewardship_outbox_event' AND proposed->>'message_id'=own.id::text
                AND proposed->>'action'='mark_unknown' AND own.state='delivery_unknown')
            OR (TG_TABLE_NAME='stewardship_schedule_occurrence' AND proposed->>'id'=own.semantic_key::text
                AND proposed->>'state'='delivery_unknown' AND own.state='delivery_unknown'));
    -- A chosen-Family test whose abandoned dispatch exhausted its preparation
    -- budget is cancelled by recovery: nothing else would ever settle it, and
    -- an unsent Testing message blocks Testing cleanup for good. Only a
    -- definitely unsent message qualifies: never submitted, or waiting after
    -- the provider's definite non-acceptance. An uncertain (idempotent) retry
    -- keeps its payload and outcome. The literal 5 mirrors MAX_ATTEMPTS in
    -- jobs/family_mail_delivery_tasks.py; a pure test pins the two together.
    recovering:=recovering OR (t.state='abandoned' AND t.attempt>=5 AND own.purpose='family_test'
        AND NEW.actor_id IS NOT NULL AND proposed->>'reason'='preparation_failed'
        AND proposed->>'action'='cancel_unsent'
        AND ((TG_TABLE_NAME='stewardship_outbox_message' AND proposed->>'id'=own.id::text
                AND ((own.state='pending' AND own.attempt=0)
                    OR (own.state='retry_wait' AND own.action='retry_unaccepted')))
            OR (TG_TABLE_NAME='stewardship_outbox_event' AND proposed->>'message_id'=own.id::text
                AND own.state='cancelled' AND own.action='cancel_unsent'
                AND own.reason='preparation_failed')));
    IF recovering THEN RETURN NEW; END IF;
    IF t.id IS NULL OR t.task_type<>'outbox_delivery' OR t.state<>'running'
       OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp()
       THEN RAISE EXCEPTION 'Family dispatch requires a live exact claim' USING ERRCODE='23514'; END IF;
    IF own.id IS NULL THEN RAISE EXCEPTION 'Family dispatch root differs' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='stewardship_outbox_message' THEN
        IF TG_OP<>'UPDATE' THEN RAISE EXCEPTION 'Mail cannot allocate Family messages' USING ERRCODE='23514'; END IF;
        m:=NEW;
        -- Only scheduled invitations/reminders cancel each other as a Family
        -- group; receipts and chosen-Family tests touch nothing but themselves.
        IF m.id<>own.id AND (m.action<>'cancel_unsent' OR m.family_id<>own.family_id
            OR m.purpose NOT IN ('initial','reminder') OR own.purpose NOT IN ('initial','reminder')
            OR m.campaign_id<>own.campaign_id OR m.mode<>own.mode) THEN
            RAISE EXCEPTION 'Family dispatch cannot mutate another delivery' USING ERRCODE='23514'; END IF;
        IF m.action NOT IN ('prepared','submit','accept','retry_unaccepted','fail_unaccepted','mark_unknown',
            'cancel_unsent','hold','release_hold') THEN
            RAISE EXCEPTION 'Family dispatch action requires another owner' USING ERRCODE='23514'; END IF;
        IF m.action IN ('prepared','submit') THEN
            IF public.stewardship_family_dispatch_live_v1(m.id) IS NOT TRUE THEN
                RAISE EXCEPTION 'Family dispatch live scope is unavailable' USING ERRCODE='23514'; END IF;
            SELECT to_jsonb(r) INTO rendering FROM public.stewardship_outbox_render r WHERE id=m.render_id;
            IF public.stewardship_family_dispatch_render_v1(rendering,m.id) IS NOT TRUE THEN
                RAISE EXCEPTION 'Family dispatch render differs from current scope' USING ERRCODE='23514'; END IF;
        ELSIF m.action IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
            IF OLD.state<>'submitting' OR OLD.run_id<>t.id OR OLD.task_fence<>t.fence
               OR OLD.worker_id IS DISTINCT FROM t.worker_id OR m.reason NOT LIKE 'smtp_%' THEN
                RAISE EXCEPTION 'Family outcome claim differs' USING ERRCODE='23514'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME='stewardship_outbox_render' THEN
        IF NEW.message_id<>own.id OR own.state NOT IN ('pending','retry_wait')
           OR public.stewardship_family_dispatch_live_v1(own.id) IS NOT TRUE
           OR public.stewardship_family_dispatch_render_v1(proposed,own.id) IS NOT TRUE THEN
            RAISE EXCEPTION 'Family dispatch rendering is not admitted' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_outbox_event' THEN
        IF NEW.message_id<>own.id AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_outbox_message sibling
            WHERE sibling.id=NEW.message_id AND sibling.family_id=own.family_id
              AND sibling.campaign_id=own.campaign_id AND sibling.mode=own.mode
              AND sibling.purpose IN ('initial','reminder') AND own.purpose IN ('initial','reminder')
              AND sibling.state='cancelled' AND NEW.action='cancel_unsent'
        ) THEN RAISE EXCEPTION 'Family event scope differs' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_schedule_occurrence' THEN
        -- Only a scheduled sender owns occurrences: a receipt or test sender
        -- must never move schedule state or fulfill the Family's real mail.
        IF own.purpose NOT IN ('initial','reminder') OR NEW.target<>'family:'||own.family_id::text OR NEW.mode<>own.mode OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_schedule_definition d
            WHERE d.id=NEW.definition_id AND d.campaign_id=own.campaign_id AND d.kind IN ('initial','reminder')
        ) THEN RAISE EXCEPTION 'Family occurrence scope differs' USING ERRCODE='23514'; END IF;
    ELSIF TG_TABLE_NAME='stewardship_schedule_fulfillment' THEN
        IF own.purpose NOT IN ('initial','reminder') OR NEW.target<>'family:'||own.family_id::text OR NEW.mode<>own.mode OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_schedule_definition d
            WHERE d.id=NEW.definition_id AND d.campaign_id=own.campaign_id AND d.kind IN ('initial','reminder')
        ) THEN RAISE EXCEPTION 'Family fulfillment scope differs' USING ERRCODE='23514'; END IF;
    ELSE RAISE EXCEPTION 'Unknown Family dispatch write' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- The provider result check admits the test's purpose.
CREATE OR REPLACE FUNCTION public.stewardship_family_smtp_result_v1(event uuid) RETURNS jsonb
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
       OR m.purpose NOT IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test','operational','security_event','leader_email') OR e.attempt<1
       OR e.evidence_digest<>encode(sha256(convert_to(e.evidence_note,'UTF8')),'hex')
       OR e.provider_key_digest<>encode(sha256(convert_to(m.semantic_key::text,'UTF8')),'hex')
       THEN RETURN NULL; END IF;
    BEGIN result:=e.evidence_note::jsonb;
    EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END;
    -- Optional send statistics (#284). They are observations, not provider
    -- evidence, and can hold nothing personal: at most 48 keys, each a
    -- lowercase identifier, whose values are booleans, non-negative integers
    -- of at most 12 digits, a helper id, or one of the few closed words for
    -- their key below (family_delivery.STAT_WORDS mirrors this list; a unit
    -- test compares them). Strip them before validating the result.
    IF jsonb_typeof(result)='object' AND result ? 'stats' THEN
        IF jsonb_typeof(result->'stats')<>'object'
           OR (SELECT count(*) FROM jsonb_object_keys(result->'stats'))>48
           OR EXISTS (
            SELECT 1 FROM jsonb_each(result->'stats') stat
            WHERE stat.key!~'^[a-z][a-z0-9_]{0,39}$'
               OR NOT (jsonb_typeof(stat.value)='boolean'
                   OR (jsonb_typeof(stat.value)='number'
                       AND stat.value::text~'^[0-9]{1,12}$')
                   OR (jsonb_typeof(stat.value)='string' AND (
                       (stat.key='helper_id'
                        AND stat.value#>>'{}'~'^h[0-9a-f]{12}$')
                       OR (stat.key,stat.value#>>'{}') IN (VALUES
                           ('transport','batched'),('transport','per_message'),
                           ('limit','daily'),('limit','rate'),('limit','message'),
                           ('conn_replaced','cap_messages'),('conn_replaced','cap_age'),
                           ('conn_replaced','stale'),
                           ('conn_end','non_accepted'),('conn_end','connect_failed'),
                           ('conn_end','token_failed'),
                           ('helper_end','limit'),('helper_end','outage'),
                           ('helper_end','systemic'),('helper_end','unknown'),
                           ('helper_end','kill'),
                           ('prev_helper_end','cap_messages'),
                           ('prev_helper_end','cap_age'),('prev_helper_end','idle'),
                           ('prev_helper_end','key_change'),
                           ('prev_helper_end','exited'),('prev_helper_end','close'))))))
           THEN RETURN NULL; END IF;
        result:=result-'stats';
    END IF;
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

-- The delivery resolution guard settles a test from evidence only, as it
-- does a chosen-Family test: a note, external acceptance or confirmed
-- non-delivery; never a resend or retry.
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
       OR m.id IS NULL OR m.purpose NOT IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test')
       OR NEW.expected_version<>m.version OR NEW.correlation_id<>NEW.id
       OR btrim(NEW.evidence_note)=''
       OR (m.purpose NOT IN ('receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test') AND o.outbox_id IS DISTINCT FROM m.id)
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
       OR NOT public.stewardship_export_admitted_v1(m.campaign_id,true)
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
            IF m.purpose NOT IN ('receipt','family_test','daily_digest','weekly_digest','admin_report','admin_report_test') THEN
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

DO $check$
DECLARE missing text;
BEGIN
    IF to_regclass('public.stewardship_report_email_test') IS NULL
       OR to_regclass('public.report_email_test_pending') IS NULL
       OR strpos(pg_get_indexdef('public.report_email_test_pending'::regclass),
           'NULLS NOT DISTINCT WHERE ((state)::text = ''queued''::text)')=0 THEN
        RAISE EXCEPTION 'Migration 0173: the report email test table is not installed as declared';
    END IF;
    -- Every new function, with its declared rights and fixed search path;
    -- the definers and trigger functions with no EXECUTE for PUBLIC.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_report_email_test_mutable_v1()', false, true),
        ('stewardship_report_email_test_settled_v1(uuid)', false, false),
        ('stewardship_report_email_test_guard_v1()', true, true),
        ('stewardship_report_email_test_settle_v1()', true, true),
        ('stewardship_report_email_test_live_v1(uuid)', false, false),
        ('stewardship_report_email_test_render_v1(jsonb,uuid)', false, false),
        ('stewardship_report_email_test_write_v1(text,jsonb)', false, false),
        ('stewardship_report_email_test_dispatch_live_v1(uuid)', false, false),
        ('stewardship_report_email_test_dispatch_write_v1(text,jsonb,jsonb)', false, false)
    ) expected(name, definer, revoked)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND p.prosecdef=expected.definer
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND (NOT expected.revoked
              OR NOT has_function_privilege('public', p.oid, 'EXECUTE')));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0173: not installed as declared: %', missing;
    END IF;
    -- Each trigger is in place and enabled.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_report_email_test', 'stewardship_report_email_test_mutable_guard_v1'),
        ('stewardship_report_email_test', 'report_email_test_guard'),
        ('stewardship_outbox_message', 'report_email_test_settle'),
        ('stewardship_outbox_message', 'report_email_test_removed'),
        ('stewardship_task_run', 'report_email_test_ended')
    ) expected(relation, name)
    WHERE NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid=to_regclass('public.'||expected.relation) AND t.tgname=expected.name
          AND NOT t.tgisinternal AND t.tgenabled='O');
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0173: a report email test trigger is missing: %', missing;
    END IF;
    -- Each replaced function carries the test's owner, with its rights.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_family_mail_outbox_write_v1()', 'stewardship_report_email_test_write_v1(TG_TABLE_NAME,to_jsonb(NEW))', false),
        ('stewardship_family_dispatch_write_v1()', 'stewardship_report_email_test_dispatch_write_v1(TG_TABLE_NAME,proposed,', false),
        ('stewardship_family_dispatch_write_v1()', '''admin_report'',''admin_report_test'',''operational''', false),
        ('stewardship_family_smtp_result_v1(uuid)', '''admin_report'',''admin_report_test'',''operational''', false),
        ('stewardship_delivery_resolution_guard_v1()', 'SELECT 1 FROM public.stewardship_report_email_test test', true),
        ('stewardship_delivery_resolution_guard_v1()', 'm.purpose IN (''family_test'',''admin_report_test'') AND NEW.action NOT IN', true)
    ) expected(name, marker, definer)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND strpos(p.prosrc, expected.marker)>0 AND p.prosecdef=expected.definer);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0173: a replaced function lacks the report email test: %', missing;
    END IF;
    IF public.stewardship_task_type_login_v1('report_email_test') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('report_email_test')
           IS DISTINCT FROM ARRAY['pk_stewardship_web']
       OR public.stewardship_task_type_login_v1('report_email_prepare') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('report_email_prepare')
           IS DISTINCT FROM ARRAY['pk_stewardship_scheduler','pk_stewardship_web'] THEN
        RAISE EXCEPTION 'Migration 0173: the task-type maps do not carry report_email_test';
    END IF;
    -- The outbox already admits the purpose (frozen file 0161).
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.stewardship_outbox_message'::regclass
          AND conname IN ('outbox_purpose','outbox_family_purpose')
          AND strpos(pg_get_constraintdef(oid),'admin_report_test')>0)<>2 THEN
        RAISE EXCEPTION 'Migration 0173: the outbox checks do not admit admin_report_test';
    END IF;
END
$check$;
