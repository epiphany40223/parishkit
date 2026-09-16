-- The request table's export_format_known check intentionally retains the
-- PostgreSQL-deparsed ANY/ARRAY form required by model/schema comparison.
-- Replacing it with equivalent IN syntax changes the fresh-baseline contract.
CREATE TABLE "stewardship_export_request" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "requester_id" uuid NOT NULL, "request_key" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "fact_set_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "report" varchar(32) NOT NULL, "format" varchar(4) NOT NULL, "browser_timezone" varchar(254) NOT NULL, "parameters" jsonb NOT NULL, "authorization_scope" jsonb NOT NULL, CONSTRAINT "export_request_replay" UNIQUE ("requester_id", "request_key"), CONSTRAINT "export_report_known" CHECK ("report" = 'participation'), CONSTRAINT "export_format_known" CHECK ((format)::text = ANY ((ARRAY['csv'::character varying, 'png'::character varying, 'pdf'::character varying])::text[])));
CREATE TABLE "stewardship_export_attempt" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL, "run_id" uuid NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), "claim_event_id" uuid NOT NULL, CONSTRAINT "export_attempt_claim" UNIQUE ("request_id", "run_id", "fence"), CONSTRAINT "export_attempt_positive_fence" CHECK ("fence" > 0));
CREATE TABLE "stewardship_export_publication" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL UNIQUE, "attempt_id" uuid NOT NULL UNIQUE, "size" bigint NOT NULL CHECK ("size" >= 0), "sha256" varchar(64) NOT NULL, "row_count" integer NOT NULL CHECK ("row_count" >= 0), "expires_at" timestamp with time zone NOT NULL, CONSTRAINT "export_publication_size" CHECK (("size" > 0 AND "size" <= 536870912)), CONSTRAINT "export_publication_digest" CHECK ("sha256"::text ~ '^[0-9a-f]{64}$'), CONSTRAINT "export_publication_expiry" CHECK ("expires_at" > ("created_at")));
CREATE TABLE "stewardship_export_cancellation" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL UNIQUE);
CREATE TABLE "stewardship_export_download_grant" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "publication_id" uuid NOT NULL, "requester_id" uuid NOT NULL, "expires_at" timestamp with time zone NOT NULL, CONSTRAINT "export_download_expiry" CHECK ("expires_at" > ("created_at")));
CREATE TABLE "stewardship_export_download_use" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "grant_id" uuid NOT NULL UNIQUE);
ALTER TABLE "stewardship_export_request" ADD CONSTRAINT "stewardship_export_r_campaign_id_f253c145_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_request" ADD CONSTRAINT "stewardship_export_r_task_id_bd2b1408_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_request" ADD CONSTRAINT "stewardship_export_r_fact_set_id_5d0c1ba5_fk_stewardsh" FOREIGN KEY ("fact_set_id") REFERENCES "stewardship_daily_fact_set" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_request" ADD CONSTRAINT "stewardship_export_r_configuration_id_f6866b66_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_request_correlation_id_2a35f42f" ON "stewardship_export_request" ("correlation_id");
CREATE INDEX "stewardship_export_request_campaign_id_f253c145" ON "stewardship_export_request" ("campaign_id");
CREATE INDEX "stewardship_export_request_fact_set_id_5d0c1ba5" ON "stewardship_export_request" ("fact_set_id");
CREATE INDEX "stewardship_export_request_configuration_id_f6866b66" ON "stewardship_export_request" ("configuration_id");
CREATE INDEX "export_requester_history" ON "stewardship_export_request" ("requester_id", "created_at");
ALTER TABLE "stewardship_export_attempt" ADD CONSTRAINT "stewardship_export_a_request_id_21bce85e_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_export_request" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_attempt" ADD CONSTRAINT "stewardship_export_a_run_id_4efd4d0b_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_attempt" ADD CONSTRAINT "stewardship_export_a_claim_event_id_c943d1e7_fk_stewardsh" FOREIGN KEY ("claim_event_id") REFERENCES "stewardship_task_event" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_attempt_correlation_id_ec07da14" ON "stewardship_export_attempt" ("correlation_id");
CREATE INDEX "stewardship_export_attempt_request_id_21bce85e" ON "stewardship_export_attempt" ("request_id");
CREATE INDEX "stewardship_export_attempt_run_id_4efd4d0b" ON "stewardship_export_attempt" ("run_id");
CREATE INDEX "stewardship_export_attempt_claim_event_id_c943d1e7" ON "stewardship_export_attempt" ("claim_event_id");
ALTER TABLE "stewardship_export_publication" ADD CONSTRAINT "stewardship_export_p_request_id_892aa861_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_export_request" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_publication" ADD CONSTRAINT "stewardship_export_p_attempt_id_1982e248_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_export_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_publication_correlation_id_334c6a33" ON "stewardship_export_publication" ("correlation_id");
CREATE INDEX "export_expiry" ON "stewardship_export_publication" ("expires_at");
ALTER TABLE "stewardship_export_cancellation" ADD CONSTRAINT "stewardship_export_c_request_id_23f2e1a4_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_export_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_cancellation_correlation_id_abd66c87" ON "stewardship_export_cancellation" ("correlation_id");
CREATE INDEX "stewardship_export_download_grant_correlation_id_a6d860d3" ON "stewardship_export_download_grant" ("correlation_id");
ALTER TABLE "stewardship_export_download_use" ADD CONSTRAINT "stewardship_export_d_grant_id_523b50eb_fk_stewardsh" FOREIGN KEY ("grant_id") REFERENCES "stewardship_export_download_grant" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_download_use_correlation_id_cd1a7a1c" ON "stewardship_export_download_use" ("correlation_id");

-- Current projection policy is an additional SQL boundary, not a substitute for
-- the caller's verified Google session and coherent YAML policy checks.
CREATE FUNCTION public.stewardship_export_authorized_v1(user_uuid uuid, admin_only boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce((SELECT CASE WHEN admin_only THEN roles ? 'administrator'
        ELSE roles ?| ARRAY['administrator','staff'] END FROM (
        SELECT coalesce(a.roles,d.roles,'[]'::jsonb) AS roles
        FROM stewardship_portal_user u CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        LEFT JOIN stewardship_domain_rule d ON d.configuration_id=r.active_configuration_id
            AND d.domain=lower(u.hosted_domain) AND d.domain=split_part(lower(u.email),'@',2)
        WHERE u.id=user_uuid AND NOT u.disabled
    ) candidate),false)
$$;

CREATE FUNCTION public.stewardship_export_admitted_v1(campaign_uuid uuid, mutating boolean)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_campaign c
        CROSS JOIN stewardship_system_configuration r
        WHERE c.id=campaign_uuid AND r.active_configuration_id IS NOT NULL
          AND NOT r.restore_review_required
          AND c.state IN ('draft','scheduled','active','closed','archived')
          AND (NOT mutating OR (
            NOT EXISTS(SELECT 1 FROM stewardship_campaign_work_gate g
                WHERE g.campaign_id=c.id AND g.state<>'released')
            AND EXISTS(SELECT 1 FROM stewardship_campaign_credentials k
                WHERE k.campaign_id=c.id AND NOT k.go_live_gate))))
$$;

CREATE FUNCTION public.stewardship_export_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'Export history is immutable' USING ERRCODE = '23514';
END $$;

CREATE FUNCTION public.stewardship_export_request_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE facts stewardship_daily_fact_set%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO facts FROM stewardship_daily_fact_set WHERE id=NEW.fact_set_id FOR SHARE;
    IF facts.id IS NULL OR facts.state<>'ready' OR facts.campaign_id<>NEW.campaign_id
       OR NEW.actor_id IS DISTINCT FROM NEW.requester_id
       OR NOT stewardship_export_authorized_v1(NEW.requester_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
       OR NEW.authorization_scope<>'{"capability":"campaign_report"}'::jsonb
       OR NEW.parameters<>jsonb_build_object('population_scope',facts.population_scope,
           'sort','date_asc','selected_ids','[]'::jsonb,'filters','{}'::jsonb)
       OR NOT EXISTS(SELECT 1 FROM pg_timezone_names
           WHERE name=public.stewardship_timezone_name_v1(NEW.browser_timezone))
       OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id
           AND t.root_id=t.id AND t.task_type='report_export' AND t.domain_request_id=NEW.id
           AND t.initiated_by_id=NEW.requester_id AND t.idempotency_key=NEW.id::text
           AND t.state='queued')
    THEN RAISE EXCEPTION 'Export request lacks exact authorized input/task binding'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_export_pin_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=NEW.fact_set_id
        AND parent_kind='export' AND parent_id=NEW.id)
    THEN RAISE EXCEPTION 'Export requires its retained fact pin' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION public.stewardship_export_attempt_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_export_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_export_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
       OR NOT stewardship_export_authorized_v1(request.requester_id)
       OR EXISTS(SELECT 1 FROM stewardship_export_publication WHERE request_id=request.id)
       OR EXISTS(SELECT 1 FROM stewardship_export_cancellation WHERE request_id=request.id)
       OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t JOIN stewardship_task_event e ON e.id=NEW.claim_event_id
           WHERE t.id=NEW.run_id AND t.root_id=request.task_id AND t.task_type='report_export'
             AND t.domain_request_id=request.id
             AND t.state='running' AND t.fence=NEW.fence AND t.worker_id=NEW.actor_id
             AND t.lease_expires_at>clock_timestamp() AND e.run_id=t.id
             AND e.action='claim' AND e.fence=t.fence AND e.worker_id=t.worker_id)
    THEN RAISE EXCEPTION 'Export attempt lacks live authorized ownership' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_export_publication_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_export_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_export_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
       OR NOT stewardship_export_authorized_v1(request.requester_id)
       OR EXISTS(SELECT 1 FROM stewardship_export_cancellation WHERE request_id=request.id)
       OR NEW.expires_at > NEW.created_at + interval '7 days 1 minute'
       OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f WHERE f.id=request.fact_set_id
           AND f.state='ready' AND f.expected_count=NEW.row_count)
       OR NOT EXISTS(SELECT 1 FROM stewardship_export_attempt a JOIN stewardship_task_run t ON t.id=a.run_id
           WHERE a.id=NEW.attempt_id AND a.request_id=request.id AND a.actor_id=NEW.actor_id
             AND t.state='running' AND t.fence=a.fence AND t.worker_id=a.actor_id
             AND t.lease_expires_at>clock_timestamp())
    THEN RAISE EXCEPTION 'Export publication lacks live authorized ownership' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_export_cancellation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_export_request%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO request FROM stewardship_export_request WHERE id=NEW.request_id;
    IF request.id IS NULL OR NOT stewardship_export_admitted_v1(request.campaign_id,true)
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR (NEW.actor_id<>request.requester_id AND NOT stewardship_export_authorized_v1(NEW.actor_id,true))
       OR EXISTS(SELECT 1 FROM stewardship_export_publication WHERE request_id=request.id)
    THEN RAISE EXCEPTION 'Export cancellation is not admitted' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_export_download_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE grant_row stewardship_export_download_grant%ROWTYPE;
        receipt stewardship_export_publication%ROWTYPE;
        request stewardship_export_request%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='stewardship_export_download_grant' THEN
        grant_row:=NEW;
        IF NEW.actor_id IS DISTINCT FROM NEW.requester_id
           OR NEW.expires_at>NEW.created_at + interval '2 minutes'
        THEN RAISE EXCEPTION 'Download grant has invalid ownership or lifetime' USING ERRCODE='23514'; END IF;
    ELSE
        SELECT * INTO grant_row FROM stewardship_export_download_grant WHERE id=NEW.grant_id;
        IF grant_row.id IS NULL OR NEW.actor_id IS DISTINCT FROM grant_row.requester_id
           OR grant_row.expires_at<=clock_timestamp()
        THEN RAISE EXCEPTION 'Download grant is unavailable' USING ERRCODE='23514'; END IF;
    END IF;
    SELECT * INTO receipt FROM stewardship_export_publication WHERE id=grant_row.publication_id;
    SELECT * INTO request FROM stewardship_export_request WHERE id=receipt.request_id;
    IF receipt.id IS NULL OR receipt.expires_at<=clock_timestamp()
       OR NOT stewardship_export_admitted_v1(request.campaign_id,false)
       OR NOT stewardship_export_authorized_v1(grant_row.requester_id)
       OR (grant_row.requester_id<>request.requester_id
           AND NOT stewardship_export_authorized_v1(grant_row.requester_id,true))
    THEN RAISE EXCEPTION 'Download is not authorized' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

DO $$ DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['request','attempt','publication','cancellation','download_grant','download_use'] LOOP
        EXECUTE format('CREATE TRIGGER export_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION stewardship_export_immutable_v1()', 'stewardship_export_' || name);
    END LOOP;
END $$;
CREATE TRIGGER export_request_insert BEFORE INSERT ON stewardship_export_request
FOR EACH ROW EXECUTE FUNCTION stewardship_export_request_guard_v1();
CREATE CONSTRAINT TRIGGER export_pin_complete AFTER INSERT ON stewardship_export_request
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_export_pin_guard_v1();
CREATE TRIGGER export_attempt_insert BEFORE INSERT ON stewardship_export_attempt
FOR EACH ROW EXECUTE FUNCTION stewardship_export_attempt_guard_v1();
CREATE TRIGGER export_publication_insert BEFORE INSERT ON stewardship_export_publication
FOR EACH ROW EXECUTE FUNCTION stewardship_export_publication_guard_v1();
CREATE TRIGGER export_cancellation_insert BEFORE INSERT ON stewardship_export_cancellation
FOR EACH ROW EXECUTE FUNCTION stewardship_export_cancellation_guard_v1();
CREATE TRIGGER export_download_grant_insert BEFORE INSERT ON stewardship_export_download_grant
FOR EACH ROW EXECUTE FUNCTION stewardship_export_download_guard_v1();
CREATE TRIGGER export_download_use_insert BEFORE INSERT ON stewardship_export_download_use
FOR EACH ROW EXECUTE FUNCTION stewardship_export_download_guard_v1();
CREATE TABLE "stewardship_export_cleanup" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "attempt_id" uuid NOT NULL UNIQUE, "task_id" uuid NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), CONSTRAINT "export_cleanup_positive_fence" CHECK ("fence" > 0));
ALTER TABLE "stewardship_export_cleanup" ADD CONSTRAINT "stewardship_export_c_attempt_id_5b0beece_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_export_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_export_cleanup" ADD CONSTRAINT "stewardship_export_c_task_id_fb5a6196_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_export_cleanup_correlation_id_7accdc24" ON "stewardship_export_cleanup" ("correlation_id");
CREATE INDEX "stewardship_export_cleanup_task_id_fb5a6196" ON "stewardship_export_cleanup" ("task_id");

CREATE FUNCTION public.stewardship_export_disposable_v1(attempt_uuid uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_export_attempt a
        WHERE a.id=attempt_uuid
          AND NOT EXISTS(SELECT 1 FROM stewardship_export_cleanup c WHERE c.attempt_id=a.id)
          AND NOT EXISTS(SELECT 1 FROM stewardship_export_publication p
              WHERE p.attempt_id=a.id AND p.expires_at>clock_timestamp())
          AND NOT EXISTS(SELECT 1 FROM stewardship_task_run t
              WHERE t.id=a.run_id AND t.fence=a.fence AND t.worker_id=a.actor_id
                AND t.state='running' AND t.lease_expires_at>clock_timestamp()))
$$;

CREATE FUNCTION public.stewardship_export_cleanup_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign_uuid uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT r.campaign_id INTO campaign_uuid FROM stewardship_export_attempt a
        JOIN stewardship_export_request r ON r.id=a.request_id WHERE a.id=NEW.attempt_id;
    IF campaign_uuid IS NULL OR NOT stewardship_export_admitted_v1(campaign_uuid,true)
       OR NOT stewardship_export_disposable_v1(NEW.attempt_id)
       OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id
           AND t.task_type='report_export_cleanup' AND t.domain_request_id=NEW.attempt_id
           AND t.state='running' AND t.fence=NEW.fence AND t.worker_id=NEW.actor_id
           AND t.lease_expires_at>clock_timestamp())
    THEN RAISE EXCEPTION 'Export cleanup lacks live disposable ownership'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER export_immutable BEFORE UPDATE OR DELETE ON stewardship_export_cleanup
FOR EACH ROW EXECUTE FUNCTION stewardship_export_immutable_v1();
CREATE TRIGGER export_cleanup_insert BEFORE INSERT ON stewardship_export_cleanup
FOR EACH ROW EXECUTE FUNCTION stewardship_export_cleanup_guard_v1();
