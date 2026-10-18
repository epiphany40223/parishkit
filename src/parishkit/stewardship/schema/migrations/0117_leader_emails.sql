-- Frozen forward migration file 0117 (the repository-wide file sequence):
-- Ministry leader emails, sent by hand (#1592). This file is installed by
-- its Django migration in the jobs app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. Reversing needs its own forward migration.
--
-- An Administrator's Email Ministry leaders Apply on the Users page records
-- one stewardship_leader_email_request. Its guard admits it only from the
-- web login for a current Administrator, under the reviewed configuration
-- and mode, for 1 to 100 distinct Ministry leader users who each lead one
-- of the current (not archived) campaign's Ministries, and records each
-- leader's Ministries and open request counts itself
-- (stewardship_leader_email_facts_v1), so no form supplies them. The web
-- review reads the same facts through stewardship_leader_emails_v1, which
-- answers only a live Administrator session and takes no address. Both
-- record a count-only audit event.
--
-- The request is delivered by the Administrator-notice engine under its
-- own owner (jobs/leader_email_owner.py): the scheduler queues one
-- leader_email_prepare Task (added to the two task-type maps), which
-- freezes the request's addresses into stewardship_leader_email_cohort and
-- writes one outbox message (the new purpose leader_email) with its
-- stewardship_leader_email_recipient row, which keeps the compiled subject
-- and bodies; mail dispatch sends exactly those. A leader email belongs to
-- no campaign (its scope is the parish) but is routed like campaign mail:
-- testing_override, to the request's Testing recipient, in Testing and
-- production in Production. So the outbox's purpose, routing and Family
-- checks gain the purpose, and four baseline functions are replaced in
-- place, each its baseline body with only that change: the outbox state
-- guard (the parish scope, and Testing and Production admission by the
-- deployment's mode rather than a campaign's), the provider result check
-- (the purpose list), the worker's outbox insert gate and mail dispatch's
-- write gate (each one more owner). Their final bodies are also the
-- baseline's. Every new function is invoker's rights except the facts
-- function, the review read and the request guard, which are SECURITY
-- DEFINER, stated again with ALTER as every definer is. Table grants are
-- runtime_grants' (jobs/leader_email_grants.py). The file ends with a DO
-- block that refuses to commit unless all of it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The three records: one Administrator's request, its frozen cohort and one
-- recipient row per leader (jobs/leader_email_models.py).
CREATE TABLE "stewardship_leader_email_request" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "configuration_id" uuid NOT NULL, "campaign_id" uuid NOT NULL,
    "mode" varchar(16) NOT NULL, "testing_recipient" varchar(254) NULL,
    "addresses" jsonb NOT NULL, "recipients" jsonb NOT NULL,
    "recipient_count" integer NOT NULL CHECK ("recipient_count" >= 0),
    CONSTRAINT "leader_email_request_mode" CHECK ((("mode" = 'production' AND "testing_recipient" IS NULL) OR ("mode" = 'testing' AND "testing_recipient" IS NOT NULL))),
    CONSTRAINT "leader_email_request_count" CHECK (("recipient_count" >= 1 AND "recipient_count" <= 100))
);
CREATE TABLE "stewardship_leader_email_cohort" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "request_id" uuid NOT NULL UNIQUE, "configuration_id" uuid NOT NULL,
    "parish_id" uuid NOT NULL, "mode" varchar(16) NOT NULL,
    "testing_recipient" varchar(254) NULL, "addresses" jsonb NOT NULL,
    "recipient_count" integer NOT NULL CHECK ("recipient_count" >= 0),
    "run_id" uuid NOT NULL,
    "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL,
    CONSTRAINT "leader_email_cohort_mode" CHECK ("mode"::text = ANY(ARRAY[('testing'::varchar)::text, ('production'::varchar)::text])),
    CONSTRAINT "leader_email_cohort_counts" CHECK (("fence" >= 1 AND "recipient_count" >= 1))
);
CREATE TABLE "stewardship_leader_email_recipient" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "cohort_id" uuid NOT NULL, "address" varchar(254) NOT NULL,
    "outbox_id" uuid NOT NULL UNIQUE, "subject" varchar(254) NOT NULL,
    "html" text NOT NULL, "text" text NOT NULL,
    CONSTRAINT "leader_email_recipient_address" UNIQUE ("cohort_id", "address")
);
ALTER TABLE "stewardship_leader_email_request" ADD CONSTRAINT "stewardship_leader_e_configuration_id_de5c6cbd_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_request" ADD CONSTRAINT "stewardship_leader_e_campaign_id_d5d09cdf_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_cohort" ADD CONSTRAINT "stewardship_leader_e_request_id_f68c9ecc_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_leader_email_request" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_cohort" ADD CONSTRAINT "stewardship_leader_e_configuration_id_8778f7c2_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_cohort" ADD CONSTRAINT "stewardship_leader_e_parish_id_a3c78323_fk_stewardsh" FOREIGN KEY ("parish_id") REFERENCES "stewardship_parish" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_cohort" ADD CONSTRAINT "stewardship_leader_e_run_id_0314bba7_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_recipient" ADD CONSTRAINT "stewardship_leader_e_cohort_id_b904127d_fk_stewardsh" FOREIGN KEY ("cohort_id") REFERENCES "stewardship_leader_email_cohort" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_leader_email_recipient" ADD CONSTRAINT "stewardship_leader_e_outbox_id_49a0b8ea_fk_stewardsh" FOREIGN KEY ("outbox_id") REFERENCES "stewardship_outbox_message" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_leader_email_request_correlation_id_e90e4f7d" ON "stewardship_leader_email_request" ("correlation_id");
CREATE INDEX "stewardship_leader_email_request_configuration_id_de5c6cbd" ON "stewardship_leader_email_request" ("configuration_id");
CREATE INDEX "stewardship_leader_email_request_campaign_id_d5d09cdf" ON "stewardship_leader_email_request" ("campaign_id");
CREATE INDEX "stewardship_leader_email_cohort_correlation_id_b74d5fb7" ON "stewardship_leader_email_cohort" ("correlation_id");
CREATE INDEX "stewardship_leader_email_cohort_configuration_id_8778f7c2" ON "stewardship_leader_email_cohort" ("configuration_id");
CREATE INDEX "stewardship_leader_email_cohort_parish_id_a3c78323" ON "stewardship_leader_email_cohort" ("parish_id");
CREATE INDEX "stewardship_leader_email_cohort_run_id_0314bba7" ON "stewardship_leader_email_cohort" ("run_id");
CREATE INDEX "stewardship_leader_email_recipient_correlation_id_c1cdf7e8" ON "stewardship_leader_email_recipient" ("correlation_id");
CREATE INDEX "stewardship_leader_email_recipient_cohort_id_b904127d" ON "stewardship_leader_email_recipient" ("cohort_id");

-- The one purpose that is routed like campaign mail yet belongs to no
-- campaign: its scope is the parish, its Campaign and Family are empty.
ALTER TABLE "stewardship_outbox_message" DROP CONSTRAINT "outbox_purpose";
ALTER TABLE "stewardship_outbox_message" DROP CONSTRAINT "outbox_routing";
ALTER TABLE "stewardship_outbox_message" DROP CONSTRAINT "outbox_family_purpose";
ALTER TABLE "stewardship_outbox_message" ADD CONSTRAINT "outbox_purpose" CHECK (((purpose)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'receipt'::character varying, 'family_test'::character varying, 'daily_digest'::character varying, 'weekly_digest'::character varying, 'operational'::character varying, 'security_event'::character varying, 'leader_email'::character varying])::text[])));
ALTER TABLE "stewardship_outbox_message" ADD CONSTRAINT "outbox_routing" CHECK ((((family_id IS NULL) AND ((purpose)::text = ANY ((ARRAY['operational'::character varying, 'security_event'::character varying])::text[])) AND ((routing)::text = 'operational'::text)) OR ((campaign_id IS NULL) AND (family_id IS NULL) AND ((purpose)::text = 'leader_email'::text) AND ((((mode)::text = 'testing'::text) AND ((routing)::text = 'testing_override'::text)) OR (((mode)::text = 'production'::text) AND ((routing)::text = 'production'::text)))) OR ((NOT ((purpose)::text = ANY ((ARRAY['operational'::character varying, 'security_event'::character varying, 'leader_email'::character varying])::text[]))) AND (campaign_id IS NOT NULL) AND ((((mode)::text = 'testing'::text) AND ((routing)::text = 'testing_override'::text)) OR (((mode)::text = 'production'::text) AND ((routing)::text = 'production'::text))))));
ALTER TABLE "stewardship_outbox_message" ADD CONSTRAINT "outbox_family_purpose" CHECK ((((family_id IS NOT NULL) AND ((purpose)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'receipt'::character varying, 'family_test'::character varying])::text[]))) OR ((family_id IS NULL) AND ((purpose)::text = ANY ((ARRAY['daily_digest'::character varying, 'weekly_digest'::character varying, 'operational'::character varying, 'security_event'::character varying, 'leader_email'::character varying])::text[])))));

-- Each Ministry leader user's campaign Ministries and their open requests
-- (#1592): every user whose role is Ministry leader, or only those of
-- ``addresses`` (a JSON array of addresses) when it is not NULL. The leaders
-- are the leader definition's (stewardship_ministry_leaders_v1, #922); the
-- counts are the Ministry follow-up queue's own selection with its default
-- filters (open, New or In progress, requests of Members still in
-- ParishSoft: reports/ministry_followup.selection_filters of FollowupQuery(),
-- which a test holds equal to this literal), in the leaders' scope only, as
-- Home's My Ministries panel counts them. Each answer item is
-- {"email", "ministries": [{"duid", "name", "join", "leave"}]}, in address
-- order, its Ministries in the queue's name order; a leader who leads none
-- of the campaign's Ministries has none. Names are ParishSoft's as stored
-- (the email repairs them for display). Nothing about a parishioner leaves
-- this function: only counts. It is a definer so that no login needs the
-- leader definition or the selection's reads; only the definers below call
-- it.
CREATE FUNCTION public.stewardship_leader_email_facts_v1(addresses jsonb)
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
          AND (addresses IS NULL OR addresses ? rule.email)
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
                WHERE item->'duids' @> jsonb_build_array(listed.value->'duid')), '[]'::jsonb))
            ORDER BY item->>'email'), '[]'::jsonb)
        FROM jsonb_array_elements(leaders) item);
END $$;
ALTER FUNCTION public.stewardship_leader_email_facts_v1(jsonb) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_facts_v1(jsonb) FROM PUBLIC;

-- The Users page's Email Ministry leaders review: every Ministry leader
-- user's facts, for a live Administrator session only (the same session
-- check as stewardship_leaders_without_user_v1, 0057), with one count-only
-- user_leader_emails_reviewed audit event per read. It takes no address.
CREATE FUNCTION public.stewardship_leader_emails_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    answer jsonb;
    event uuid := gen_random_uuid();
BEGIN
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, true) THEN
        RAISE EXCEPTION 'Leader email review requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    answer := public.stewardship_leader_email_facts_v1(NULL);
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'user_leader_emails_reviewed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded','count',jsonb_array_length(answer)));
    RETURN answer;
END $$;
ALTER FUNCTION public.stewardship_leader_emails_v1(text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_leader_emails_v1(text, uuid) FROM PUBLIC;

-- One Administrator's Apply. Only the web login inserts it, for a current
-- Administrator, under the reviewed configuration and mode (a change since
-- the review is refused, never silently applied), for the current campaign
-- unless it is archived, and for 1 to 100 distinct addresses that are each
-- a Ministry leader user leading one of the campaign's Ministries. The guard
-- records each leader's facts itself, so no form supplies them, and the
-- count-only user_leader_emails_requested audit event. History is immutable.
CREATE FUNCTION public.stewardship_leader_email_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    runtime public.stewardship_system_configuration%ROWTYPE;
    c public.stewardship_campaign%ROWTYPE;
    facts jsonb;
    testing text;
    event uuid := gen_random_uuid();
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry leader email requests are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO runtime FROM public.stewardship_system_configuration;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    -- The Testing recipient every email goes to instead, set exactly in Testing.
    IF runtime.mode='testing' THEN testing := runtime.testing_recipient; END IF;
    IF session_user<>'pk_stewardship_web' OR NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NEW.correlation_id<>NEW.id
       OR NEW.configuration_id IS DISTINCT FROM runtime.active_configuration_id
       OR c.id IS NULL OR c.id IS DISTINCT FROM runtime.current_campaign_id
       OR c.state='archived'
       OR NEW.mode IS DISTINCT FROM runtime.mode
       OR NEW.testing_recipient IS DISTINCT FROM testing
       OR jsonb_typeof(NEW.addresses)<>'array'
       OR NEW.recipient_count<>jsonb_array_length(NEW.addresses)
       OR NEW.recipient_count NOT BETWEEN 1 AND 100
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.addresses) value
                  WHERE jsonb_typeof(value)<>'string')
       OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(NEW.addresses) value)
           <>NEW.recipient_count THEN
        RAISE EXCEPTION 'Ministry leader email request requires a current Administrator and the reviewed configuration'
            USING ERRCODE='23514';
    END IF;
    facts := public.stewardship_leader_email_facts_v1(NEW.addresses);
    IF jsonb_array_length(facts)<>NEW.recipient_count
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(facts) item
                  WHERE jsonb_array_length(item->'ministries')=0) THEN
        RAISE EXCEPTION 'Each address must be a Ministry leader user who leads one of the campaign''s Ministries'
            USING ERRCODE='23514';
    END IF;
    NEW.recipients := facts;
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(event,clock_timestamp(),NEW.actor_id,NEW.id,'user_leader_emails_requested',NEW.id,c.id);
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),NEW.actor_id,NEW.id,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded','count',NEW.recipient_count));
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_leader_email_request_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_request_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_leader_email_request_guard
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_leader_email_request
FOR EACH ROW EXECUTE FUNCTION public.stewardship_leader_email_request_guard_v1();

CREATE FUNCTION public.stewardship_leader_email_cohort_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_cohort_immutable_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_leader_email_cohort_immutable_guard_v1
BEFORE UPDATE OR DELETE ON public.stewardship_leader_email_cohort
FOR EACH ROW EXECUTE FUNCTION public.stewardship_leader_email_cohort_immutable_v1();
CREATE FUNCTION public.stewardship_leader_email_recipient_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_recipient_immutable_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_leader_email_recipient_immutable_guard_v1
BEFORE UPDATE OR DELETE ON public.stewardship_leader_email_recipient
FOR EACH ROW EXECUTE FUNCTION public.stewardship_leader_email_recipient_immutable_v1();

-- The cohort is exactly its request's addresses, mode and Testing recipient,
-- captured under the fenced preparation Task that owns the request, in the
-- current configuration and the mode the request was made in.
CREATE FUNCTION public.stewardship_leader_email_cohort_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request public.stewardship_leader_email_request%ROWTYPE;
BEGIN
    SELECT * INTO request FROM public.stewardship_leader_email_request WHERE id=NEW.request_id;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
      OR request.id IS NULL
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t
        CROSS JOIN public.stewardship_system_configuration runtime
        JOIN public.stewardship_parish p ON p.configuration_id=runtime.active_configuration_id
        WHERE t.id=NEW.run_id AND t.id=NEW.correlation_id
          AND t.task_type='leader_email_prepare' AND t.domain_request_id=NEW.request_id
          AND t.state='running' AND t.fence=NEW.fence AND t.worker_id=NEW.worker_id
          AND NEW.actor_id=NEW.worker_id AND t.lease_expires_at>clock_timestamp()
          AND NEW.configuration_id=runtime.active_configuration_id
          AND NEW.mode=runtime.mode AND NEW.parish_id=p.id)
      THEN RAISE EXCEPTION 'Ministry leader email cohort requires current fenced ownership'
        USING ERRCODE='23514'; END IF;
    IF NEW.mode<>request.mode
      OR NEW.testing_recipient IS DISTINCT FROM request.testing_recipient
      OR jsonb_typeof(NEW.addresses)<>'array'
      OR NOT (NEW.addresses <@ request.addresses AND request.addresses <@ NEW.addresses)
      OR NEW.recipient_count<>jsonb_array_length(NEW.addresses)
      OR NEW.recipient_count<>request.recipient_count THEN
        RAISE EXCEPTION 'Ministry leader email cohort must match its request'
          USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_cohort_binding_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_leader_email_cohort_binding
BEFORE INSERT ON public.stewardship_leader_email_cohort
FOR EACH ROW EXECUTE FUNCTION public.stewardship_leader_email_cohort_binding_v1();

-- Whether a render (as JSON) is a cohort's exact envelope for one address:
-- its configuration's sender, the address as the one intended recipient,
-- the Testing recipient instead in Testing, where the subject and text say
-- so first, and the given subject and bodies when they are named.
CREATE FUNCTION public.stewardship_leader_email_render_v1(proposed jsonb, cohort uuid,
    configuration uuid, address text, kept jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c public.stewardship_leader_email_cohort%ROWTYPE; email jsonb;
BEGIN
    SELECT * INTO c FROM public.stewardship_leader_email_cohort WHERE id=cohort;
    SELECT settings INTO email FROM public.stewardship_applied_integration
      WHERE configuration_id=configuration AND kind='email';
    IF c.id IS NULL OR email IS NULL OR address IS NULL OR NOT c.addresses ? address THEN
        RETURN false;
    END IF;
    RETURN (proposed->>'configuration_id')::uuid=configuration
      AND proposed->>'template_id' IS NULL
      AND proposed->>'sender'=email->>'sender'
      AND proposed->>'reply_to'=email->>'reply_to'
      AND proposed->'intended_recipients'=jsonb_build_array(address)
      AND proposed->'routed_recipients'=jsonb_build_array(
          CASE WHEN c.mode='testing' THEN c.testing_recipient ELSE address END)
      AND (c.mode<>'testing' OR (starts_with(proposed->>'subject','[TEST] ')
          AND starts_with(proposed->>'text','TEST — sent to '||c.testing_recipient
              ||' instead of Ministry leader '||address||'.')))
      AND (kept IS NULL OR (proposed->>'subject'=kept->>'subject'
          AND proposed->>'html'=kept->>'html' AND proposed->>'text'=kept->>'text'));
END $$;

-- A recipient row is created with its outbox message in one preparation
-- page, and keeps exactly the subject and bodies of the message's render.
CREATE FUNCTION public.stewardship_leader_email_recipient_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
      AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
      AND mode='ExclusiveLock' AND granted)
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_leader_email_cohort c
      JOIN public.stewardship_task_run captured ON captured.id=c.run_id
      JOIN public.stewardship_task_run t ON t.root_id=captured.root_id
      JOIN public.stewardship_outbox_message m ON m.id=NEW.outbox_id
      JOIN public.stewardship_outbox_render r ON r.id=m.render_id
      WHERE c.id=NEW.cohort_id AND c.addresses ? NEW.address
        AND t.id=NEW.correlation_id AND t.task_type='leader_email_prepare'
        AND t.domain_request_id=c.request_id AND t.state='running'
        AND t.worker_id=NEW.actor_id AND t.lease_expires_at>clock_timestamp()
        AND m.correlation_id=t.id AND m.actor_id=t.worker_id
        AND m.semantic_key=NEW.id AND m.scope_id=c.parish_id
        AND m.mode=c.mode AND m.purpose='leader_email'
        AND m.routing=CASE c.mode WHEN 'testing' THEN 'testing_override' ELSE 'production' END
        AND m.credential_namespace='none' AND m.campaign_id IS NULL
        AND m.family_id IS NULL AND m.state='pending' AND m.version=1
        AND public.stewardship_leader_email_render_v1(to_jsonb(r),c.id,c.configuration_id,
            NEW.address,jsonb_build_object('subject',NEW.subject,'html',NEW.html,'text',NEW.text)))
    THEN RAISE EXCEPTION 'Ministry leader email recipient requires its atomic exact delivery'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_recipient_binding_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_leader_email_recipient_binding
BEFORE INSERT ON public.stewardship_leader_email_recipient
FOR EACH ROW EXECUTE FUNCTION public.stewardship_leader_email_recipient_binding_v1();

-- The worker's outbox inserts for one preparation page
-- (stewardship_family_mail_outbox_write_v1 admits them through this).
CREATE FUNCTION public.stewardship_leader_email_prepare_write_v1(relation_name text, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE t public.stewardship_task_run%ROWTYPE; c public.stewardship_leader_email_cohort%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    IF t.id IS NULL OR t.task_type<>'leader_email_prepare' OR t.state<>'running'
      OR t.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
      OR t.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    SELECT cohort.* INTO c FROM public.stewardship_leader_email_cohort cohort
      JOIN public.stewardship_task_run captured ON captured.id=cohort.run_id
      WHERE cohort.request_id=t.domain_request_id AND captured.root_id=t.root_id;
    IF c.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
      RETURN proposed->>'purpose'='leader_email'
        AND proposed->>'routing'=CASE c.mode WHEN 'testing' THEN 'testing_override' ELSE 'production' END
        AND proposed->>'credential_namespace'='none' AND proposed->>'campaign_id' IS NULL
        AND proposed->>'family_id' IS NULL AND (proposed->>'scope_id')::uuid=c.parish_id
        AND proposed->>'mode'=c.mode AND proposed->>'state'='pending'
        AND proposed->>'action'='created' AND proposed->>'version'='1';
    END IF;
    SELECT * INTO m FROM public.stewardship_outbox_message
      WHERE id=(proposed->>'message_id')::uuid AND correlation_id=t.id AND actor_id=t.worker_id
        AND purpose='leader_email' AND scope_id=c.parish_id AND mode=c.mode
        AND state='pending' AND version=1;
    IF m.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_render' THEN
      RETURN (proposed->>'id')::uuid=m.render_id
        AND public.stewardship_leader_email_render_v1(proposed,c.id,c.configuration_id,
          proposed->'intended_recipients'->>0,NULL);
    END IF;
    RETURN proposed->>'version'='1' AND proposed->>'action'='created';
END $$;

-- No leader email message exists without its recipient row.
CREATE FUNCTION public.stewardship_leader_email_prepare_receipt_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='leader_email' AND NOT EXISTS(SELECT 1
        FROM public.stewardship_leader_email_recipient recipient
        JOIN public.stewardship_leader_email_cohort c ON c.id=recipient.cohort_id
        JOIN public.stewardship_task_run t ON t.id=NEW.correlation_id
        WHERE recipient.outbox_id=NEW.id AND recipient.id=NEW.semantic_key
          AND recipient.correlation_id=t.id AND t.task_type='leader_email_prepare'
          AND c.request_id=t.domain_request_id) THEN
      RAISE EXCEPTION 'Ministry leader email outbox requires its atomic recipient receipt'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_leader_email_prepare_receipt_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER stewardship_leader_email_prepare_receipt
AFTER INSERT ON public.stewardship_outbox_message DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_leader_email_prepare_receipt_v1();

-- Whether a leader email may be sent now: its cohort is complete, its
-- address is still a Ministry leader user, and the deployment is still in
-- the mode it was prepared in (a Production one also waits while a restore
-- is under review).
CREATE FUNCTION public.stewardship_leader_email_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_outbox_message m
      JOIN public.stewardship_leader_email_recipient recipient
        ON recipient.outbox_id=m.id AND recipient.id=m.semantic_key
      JOIN public.stewardship_leader_email_cohort c ON c.id=recipient.cohort_id
      CROSS JOIN public.stewardship_system_configuration runtime
      JOIN public.stewardship_address_rule rule
        ON rule.configuration_id=runtime.active_configuration_id
       AND rule.email=recipient.address AND rule.role='ministry_leader'
      WHERE m.id=$1 AND m.purpose='leader_email'
        AND m.routing=CASE c.mode WHEN 'testing' THEN 'testing_override' ELSE 'production' END
        AND m.scope_id=c.parish_id AND m.mode=c.mode AND runtime.mode=m.mode
        AND (m.mode='testing' OR NOT runtime.restore_review_required)
        AND m.family_id IS NULL AND m.campaign_id IS NULL AND m.credential_namespace='none'
        AND (SELECT count(*) FROM public.stewardship_leader_email_recipient
             WHERE cohort_id=c.id)=c.recipient_count)
$$;

-- Mail dispatch's writes for one leader email
-- (stewardship_family_dispatch_write_v1 admits them through this). A render
-- made at dispatch carries the kept subject and bodies under the current
-- configuration's sender; outcomes need the exact submitted claim; an
-- abandoned submission past its deadline only becomes uncertain.
CREATE FUNCTION public.stewardship_leader_email_dispatch_write_v1(relation_name text,proposed jsonb,prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task public.stewardship_task_run%ROWTYPE; message public.stewardship_outbox_message%ROWTYPE;
    recipient public.stewardship_leader_email_recipient%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO task FROM public.stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    SELECT * INTO message FROM public.stewardship_outbox_message
      WHERE id=task.domain_request_id AND task_id=task.root_id AND purpose='leader_email';
    SELECT r.* INTO recipient FROM public.stewardship_leader_email_recipient r
      JOIN public.stewardship_leader_email_cohort c ON c.id=r.cohort_id
      WHERE r.outbox_id=message.id AND r.id=message.semantic_key
        AND message.scope_id=c.parish_id AND message.mode=c.mode;
    SELECT * INTO runtime FROM public.stewardship_system_configuration;
    IF task.id IS NULL OR task.task_type<>'outbox_delivery' OR message.id IS NULL
      OR recipient.id IS NULL
      OR (CASE WHEN relation_name='stewardship_outbox_message' THEN proposed->>'id'
        ELSE proposed->>'message_id' END)::uuid IS DISTINCT FROM message.id THEN RETURN false; END IF;
    IF task.state='abandoned' THEN
      RETURN task.id=message.run_id AND task.fence>=message.task_fence
        AND message.provider_deadline<=clock_timestamp() AND proposed->>'actor_id' IS NOT NULL
        AND proposed->>'action'='mark_unknown' AND proposed->>'reason'='recovery_unknown'
        AND ((relation_name='stewardship_outbox_message' AND prior->>'state'='submitting')
          OR (relation_name='stewardship_outbox_event' AND message.state='delivery_unknown'));
    END IF;
    IF task.state<>'running' OR task.worker_id IS DISTINCT FROM (proposed->>'actor_id')::uuid
      OR task.lease_expires_at<=clock_timestamp() THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
      IF prior IS NULL THEN RETURN false; END IF;
      IF proposed->>'action' IN ('prepared','submit') THEN
        RETURN public.stewardship_leader_email_dispatch_live_v1(message.id) AND EXISTS(
          SELECT 1 FROM public.stewardship_outbox_render r WHERE r.id=(proposed->>'render_id')::uuid
            AND r.message_id=message.id
            AND public.stewardship_leader_email_render_v1(to_jsonb(r),recipient.cohort_id,
              runtime.active_configuration_id,recipient.address,
              jsonb_build_object('subject',recipient.subject,'html',recipient.html,'text',recipient.text)));
      END IF;
      IF proposed->>'action' IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
        RETURN prior->>'state'='submitting' AND (prior->>'run_id')::uuid=task.id
          AND (prior->>'task_fence')::bigint=task.fence AND (prior->>'worker_id')::uuid=task.worker_id
          AND proposed->>'reason' LIKE 'smtp_%';
      END IF;
      -- Cancelled unsent only when the address is no Ministry leader user any
      -- more or the mode changed, or when its preparation failed for good.
      IF proposed->>'action'='cancel_unsent' AND proposed->>'reason'='recipient_revoked' THEN
        RETURN runtime.mode<>message.mode OR NOT EXISTS(SELECT 1
          FROM public.stewardship_address_rule rule
          WHERE rule.configuration_id=runtime.active_configuration_id
            AND rule.email=recipient.address AND rule.role='ministry_leader');
      END IF;
      IF proposed->>'action'='cancel_unsent' AND proposed->>'reason'='preparation_failed' THEN
        RETURN EXISTS(SELECT 1 FROM public.stewardship_leader_email_cohort c
          JOIN public.stewardship_task_run original ON original.id=c.run_id
          WHERE c.id=recipient.cohort_id
            AND (SELECT count(*) FROM public.stewardship_leader_email_recipient WHERE cohort_id=c.id)<c.recipient_count
            AND (SELECT state FROM public.stewardship_task_run WHERE root_id=original.root_id
              ORDER BY retry_sequence DESC LIMIT 1) IN ('failed','cancelled'));
      END IF;
      RETURN false;
    END IF;
    IF relation_name='stewardship_outbox_render' THEN
      RETURN prior IS NULL AND message.state IN ('pending','retry_wait')
        AND (proposed->>'message_id')::uuid=message.id
        AND public.stewardship_leader_email_dispatch_live_v1(message.id)
        AND public.stewardship_leader_email_render_v1(proposed,recipient.cohort_id,
          runtime.active_configuration_id,recipient.address,
          jsonb_build_object('subject',recipient.subject,'html',recipient.html,'text',recipient.text));
    END IF;
    RETURN prior IS NULL AND proposed->>'action'=message.action
      AND (proposed->>'version')::bigint=message.version;
END $$;

-- The replaced functions, each its baseline body with only the leader email
-- change (the baseline carries the same final text).
CREATE OR REPLACE FUNCTION public.stewardship_outbox_state_v1() RETURNS trigger
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
       AND NEW.purpose<>'leader_email' AND NOT EXISTS (
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
    -- A Ministry leader email (#1592, migration 0117) belongs to no
    -- campaign: its Testing work is admitted only while the deployment
    -- itself is in Testing.
    IF NEW.purpose='leader_email' AND NEW.routing='testing_override' AND (TG_OP='INSERT'
       OR NEW.action IN ('prepared','submit','retry_failed','authorize_resend','retry_unaccepted','retry_idempotent'))
       AND NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration s
           WHERE s.mode='testing') THEN
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
    IF (NEW.purpose NOT IN ('operational','security_event','leader_email') AND NEW.scope_id IS DISTINCT FROM NEW.campaign_id)
       OR (NEW.purpose IN ('operational','security_event','leader_email') AND NOT EXISTS (
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
            IF NEW.routing='production' AND NEW.purpose<>'leader_email' AND NOT EXISTS (
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
            -- A Ministry leader email has no campaign: it is sent only while
            -- the deployment is in Production and no restore is under review.
            IF NEW.routing='production' AND NEW.purpose='leader_email' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_system_configuration s
                WHERE s.mode='production' AND NOT s.restore_review_required
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
       OR m.purpose NOT IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','operational','security_event','leader_email') OR e.attempt<1
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

CREATE OR REPLACE FUNCTION public.stewardship_family_mail_outbox_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
       public.stewardship_family_mail_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_family_test_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_daily_digest_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_weekly_digest_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_ops_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_security_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_leader_email_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE THEN
        RAISE EXCEPTION 'Outbox insertion requires current preparation ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

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
        WHERE id=t.domain_request_id AND task_id=t.root_id AND purpose IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','operational','security_event','leader_email');
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
    IF own.purpose='weekly_digest' THEN
        IF public.stewardship_weekly_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Weekly dispatch requires its exact current owner' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF own.purpose='daily_digest' THEN
        IF public.stewardship_daily_dispatch_write_v1(TG_TABLE_NAME,proposed,
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
            RAISE EXCEPTION 'Daily dispatch requires its exact current owner' USING ERRCODE='23514';
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
            'daily_digest_finalize',
            'daily_digest_prepare',
            'family_mail_prepare',
            'family_mail_test',
            'leader_email_prepare',
            'operational_collect',
            'operational_prepare',
            'operational_slack',
            'production_cleanup',
            'production_token_cleanup',
            'production_tokens',
            'report_exact_export',
            'report_export',
            'report_export_cleanup',
            'report_fact_verification',
            'report_facts',
            'security_prepare',
            'setup_finalize',
            'setup_source_cleanup',
            'setup_source_load',
            'source_refresh',
            'weekly_digest_finalize',
            'weekly_digest_prepare')
        THEN 'pk_stewardship_worker'
        WHEN task_type IN (
            'campaign_mail_test',
            'family_address_retry',
            'outbox_delivery',
            'setup_mail_test')
        THEN 'pk_stewardship_mail_dispatch'
    END $$;

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
        WHEN 'daily_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'daily_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
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
        WHEN 'weekly_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'weekly_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
    END $$;

DO $check$
DECLARE missing text;
BEGIN
    IF to_regclass('public.stewardship_leader_email_request') IS NULL
       OR to_regclass('public.stewardship_leader_email_cohort') IS NULL
       OR to_regclass('public.stewardship_leader_email_recipient') IS NULL THEN
        RAISE EXCEPTION 'Migration 0117: a Ministry leader email table is missing';
    END IF;
    -- The outbox admits the purpose, parish-scoped and routed by mode.
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.stewardship_outbox_message'::regclass
          AND conname IN ('outbox_purpose','outbox_routing','outbox_family_purpose')
          AND pg_get_constraintdef(oid) LIKE '%leader_email%')<>3 THEN
        RAISE EXCEPTION 'Migration 0117: the outbox checks do not admit leader_email';
    END IF;
    -- Every new function, with its declared rights; the definers and trigger
    -- functions with no EXECUTE for PUBLIC. The invoker helpers the outbox
    -- gates call grant nothing of their own, as the other owners' do not.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_leader_email_facts_v1(jsonb)', true, true),
        ('stewardship_leader_emails_v1(text,uuid)', true, true),
        ('stewardship_leader_email_request_guard_v1()', true, true),
        ('stewardship_leader_email_cohort_immutable_v1()', false, true),
        ('stewardship_leader_email_recipient_immutable_v1()', false, true),
        ('stewardship_leader_email_cohort_binding_v1()', false, true),
        ('stewardship_leader_email_render_v1(jsonb,uuid,uuid,text,jsonb)', false, false),
        ('stewardship_leader_email_recipient_binding_v1()', false, true),
        ('stewardship_leader_email_prepare_write_v1(text,jsonb)', false, false),
        ('stewardship_leader_email_prepare_receipt_v1()', false, true),
        ('stewardship_leader_email_dispatch_live_v1(uuid)', false, false),
        ('stewardship_leader_email_dispatch_write_v1(text,jsonb,jsonb)', false, false)
    ) expected(name, definer, revoked)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND p.prosecdef=expected.definer
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND (NOT expected.revoked
              OR NOT has_function_privilege('public', p.oid, 'EXECUTE')));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0117: not installed as declared: %', missing;
    END IF;
    -- Each guard trigger is in place.
    IF (SELECT count(*) FROM pg_trigger t
        WHERE NOT t.tgisinternal AND t.tgenabled='O' AND t.tgname IN (
            'stewardship_leader_email_request_guard',
            'stewardship_leader_email_cohort_immutable_guard_v1',
            'stewardship_leader_email_recipient_immutable_guard_v1',
            'stewardship_leader_email_cohort_binding',
            'stewardship_leader_email_recipient_binding',
            'stewardship_leader_email_prepare_receipt'))<>6 THEN
        RAISE EXCEPTION 'Migration 0117: a Ministry leader email trigger is missing';
    END IF;
    -- The replaced functions carry the new owner, and stay invoker's rights.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND ((p.proname='stewardship_outbox_state_v1'
                AND p.prosrc LIKE '%NEW.purpose=''leader_email'' AND NEW.routing=''testing_override''%'
                AND p.prosrc LIKE '%NEW.routing=''production'' AND NEW.purpose=''leader_email''%'
                AND p.prosrc LIKE '%(''operational'',''security_event'',''leader_email'') AND NOT EXISTS%')
            OR (p.proname='stewardship_family_smtp_result_v1'
                AND p.prosrc LIKE '%''security_event'',''leader_email'') OR e.attempt<1%')
            OR (p.proname='stewardship_family_mail_outbox_write_v1'
                AND p.prosrc LIKE '%stewardship_leader_email_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW))%')
            OR (p.proname='stewardship_family_dispatch_write_v1'
                AND p.prosrc LIKE '%stewardship_leader_email_dispatch_write_v1(TG_TABLE_NAME,proposed,%'
                AND p.prosrc LIKE '%''security_event'',''leader_email'');%')))<>4 THEN
        RAISE EXCEPTION 'Migration 0117: a replaced outbox guard lacks Ministry leader emails';
    END IF;
    IF public.stewardship_task_type_login_v1('leader_email_prepare') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('leader_email_prepare')
           IS DISTINCT FROM ARRAY['pk_stewardship_scheduler']
       OR public.stewardship_task_type_login_v1('census_publication') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('census_publication')
           IS DISTINCT FROM ARRAY['pk_stewardship_web'] THEN
        RAISE EXCEPTION 'Migration 0117: the task-type maps do not carry leader_email_prepare';
    END IF;
END
$check$;
