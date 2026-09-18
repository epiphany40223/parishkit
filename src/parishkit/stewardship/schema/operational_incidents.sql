-- Fresh-install BG-10 records. No provider authority follows from an incident.
CREATE TABLE "stewardship_ops_incident" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "version" bigint NOT NULL CHECK ("version" >= 0),
    "kind" varchar(32) NOT NULL, "signal_level" varchar(8) NOT NULL,
    "action" varchar(8) DEFAULT 'observe' NOT NULL,
    "suppression_seconds" integer NOT NULL CHECK ("suppression_seconds" >= 0),
    "escalation_seconds" integer NOT NULL CHECK ("escalation_seconds" >= 0),
    "level" varchar(8) DEFAULT 'WARNING' NOT NULL,
    "first_seen" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "last_seen" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "occurrences" bigint DEFAULT 1 NOT NULL CHECK ("occurrences" >= 0),
    "last_notice_at" timestamp with time zone NULL,
    "resolved_at" timestamp with time zone NULL
);
CREATE TABLE "stewardship_ops_notice" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "incident_id" uuid NOT NULL,
    "incident_version" bigint NOT NULL CHECK ("incident_version" >= 0),
    "phase" varchar(9) NOT NULL, "level" varchar(8) NOT NULL,
    "first_seen" timestamp with time zone NOT NULL,
    "observed_at" timestamp with time zone NOT NULL,
    "occurrences" bigint NOT NULL CHECK ("occurrences" >= 0),
    CONSTRAINT "ops_notice_version" UNIQUE ("incident_id", "incident_version"),
    CONSTRAINT "ops_notice_phase" CHECK ("phase"::text = ANY(ARRAY[('opened'::varchar)::text, ('escalated'::varchar)::text, ('repeated'::varchar)::text, ('resolved'::varchar)::text])),
    CONSTRAINT "ops_notice_shape" CHECK (("level" = 'CRITICAL' AND "incident_version" >= 1 AND "occurrences" >= 1 AND "observed_at" >= ("first_seen")))
);
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "stewardship_jobs_operationalincident_positive_version" CHECK ("version" >= 1);
CREATE UNIQUE INDEX "ops_incident_active_kind" ON "stewardship_ops_incident" ("kind") WHERE "resolved_at" IS NULL;
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_kind" CHECK ("kind"::text = ANY(ARRAY[('database_unavailable'::varchar)::text, ('storage_integrity'::varchar)::text, ('task_failed'::varchar)::text, ('system_failure'::varchar)::text, ('source_refresh_failed'::varchar)::text, ('source_stale'::varchar)::text, ('source_tenant_mismatch'::varchar)::text, ('source_destructive_change'::varchar)::text, ('mail_provider_unavailable'::varchar)::text, ('scheduler_lag'::varchar)::text, ('worker_unavailable'::varchar)::text, ('admin_abuse'::varchar)::text, ('family_abuse'::varchar)::text, ('limiter_unavailable'::varchar)::text, ('limiter_state_lost'::varchar)::text, ('publication_ambiguous'::varchar)::text, ('production_cleanup_failed'::varchar)::text, ('backup_rpo_breach'::varchar)::text, ('purge_inconsistency'::varchar)::text, ('purge_cleanup_failed'::varchar)::text]));
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_levels" CHECK ("level"::text = ANY(ARRAY[('WARNING'::varchar)::text, ('CRITICAL'::varchar)::text]) AND "signal_level"::text = ANY(ARRAY[('WARNING'::varchar)::text, ('CRITICAL'::varchar)::text]));
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_action" CHECK ("action"::text = ANY(ARRAY[('observe'::varchar)::text, ('resolve'::varchar)::text]));
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_windows" CHECK ("suppression_seconds">=60 AND "suppression_seconds"<=86400 AND ("escalation_seconds">=60 AND "escalation_seconds"<=86400));
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_shape" CHECK (("occurrences" >= 1 AND "last_seen" >= ("first_seen") AND ("resolved_at" IS NULL OR "resolved_at" >= ("last_seen")) AND (("last_notice_at" IS NULL AND "level" = 'WARNING') OR ("last_notice_at" >= ("first_seen") AND "level" = 'CRITICAL' AND "last_notice_at" IS NOT NULL))));
CREATE INDEX "stewardship_ops_incident_correlation_id_baa1e1c5" ON "stewardship_ops_incident" ("correlation_id");
ALTER TABLE "stewardship_ops_notice" ADD CONSTRAINT "stewardship_ops_noti_incident_id_b186382a_fk_stewardsh" FOREIGN KEY ("incident_id") REFERENCES "stewardship_ops_incident" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ops_notice_correlation_id_e90123e8" ON "stewardship_ops_notice" ("correlation_id");
CREATE INDEX "stewardship_ops_notice_incident_id_b186382a" ON "stewardship_ops_notice" ("incident_id");

CREATE FUNCTION public.stewardship_ops_incident_mutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW."id" IS DISTINCT FROM OLD."id"
       OR NEW."created_at" IS DISTINCT FROM OLD."created_at"
       OR NEW."kind" IS DISTINCT FROM OLD."kind"
       OR NEW."first_seen" IS DISTINCT FROM OLD."first_seen"
       OR NEW."suppression_seconds" IS DISTINCT FROM OLD."suppression_seconds"
       OR NEW."escalation_seconds" IS DISTINCT FROM OLD."escalation_seconds" THEN
        RAISE EXCEPTION 'Record identity and bindings are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_ops_incident_mutable_guard_v1
BEFORE UPDATE ON public.stewardship_ops_incident
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_incident_mutable_v1();

CREATE FUNCTION public.stewardship_ops_notice_immutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER stewardship_ops_notice_immutable_guard_v1
BEFORE UPDATE OR DELETE ON public.stewardship_ops_notice
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_notice_immutable_v1();

CREATE FUNCTION public.stewardship_ops_incident_state_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE instant timestamptz := statement_timestamp();
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Operational episode history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF session_user='pk_stewardship_web' AND NEW.kind NOT IN
      ('limiter_unavailable','limiter_state_lost','admin_abuse','family_abuse') THEN
        RAISE EXCEPTION 'Web operational signals are limited to authentication' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.action<>'observe' OR NEW.occurrences<>1
           OR NEW.level<>'WARNING' OR NEW.last_notice_at IS NOT NULL
           OR NEW.resolved_at IS NOT NULL OR NEW.first_seen>instant
           OR NOT isfinite(NEW.first_seen) OR NEW.first_seen<'0001-01-01T00:00:00Z'::timestamptz
           OR NEW.last_seen IS DISTINCT FROM NEW.first_seen THEN
            RAISE EXCEPTION 'Operational episode must start with one observation' USING ERRCODE='23514';
        END IF;
        NEW.level := NEW.signal_level;
        IF NEW.level='CRITICAL' THEN NEW.last_notice_at := NEW.first_seen; END IF;
        RETURN NEW;
    END IF;
    -- The writer supplies a signal, not a forged count, time or notification.
    IF OLD.resolved_at IS NOT NULL OR instant<OLD.last_seen
       OR ROW(NEW.level,NEW.last_seen,NEW.occurrences,NEW.last_notice_at,NEW.resolved_at)
          IS DISTINCT FROM ROW(OLD.level,OLD.last_seen,OLD.occurrences,OLD.last_notice_at,OLD.resolved_at) THEN
        RAISE EXCEPTION 'Operational episode derived state is owner controlled' USING ERRCODE='23514';
    END IF;
    IF NEW.action='resolve' THEN
        IF NEW.signal_level IS DISTINCT FROM OLD.signal_level THEN
            RAISE EXCEPTION 'Recovery cannot replace the observation' USING ERRCODE='23514';
        END IF;
        NEW.resolved_at := instant;
        IF OLD.last_notice_at IS NOT NULL THEN NEW.last_notice_at := instant; END IF;
    ELSIF NEW.action='observe' THEN
        NEW.last_seen := instant;
        NEW.occurrences := CASE WHEN OLD.occurrences=9223372036854775807
            THEN OLD.occurrences ELSE OLD.occurrences+1 END;
        IF OLD.level='CRITICAL' OR NEW.signal_level='CRITICAL'
           OR instant-OLD.first_seen >= make_interval(secs=>OLD.escalation_seconds) THEN
            NEW.level := 'CRITICAL';
            IF OLD.level='WARNING'
               OR instant-OLD.last_notice_at >= make_interval(secs=>OLD.suppression_seconds) THEN
                NEW.last_notice_at := instant;
            END IF;
        END IF;
    ELSE
        RAISE EXCEPTION 'Unknown operational episode action' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_ops_incident_state_guard
BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_ops_incident
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_incident_state_v1();

CREATE FUNCTION public.stewardship_ops_incident_notice_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE notice_phase text;
BEGIN
    IF TG_OP='INSERT' AND NEW.level='CRITICAL' THEN notice_phase := 'opened';
    ELSIF TG_OP='UPDATE' THEN
        IF NEW.resolved_at IS NOT NULL AND OLD.last_notice_at IS NOT NULL THEN
            notice_phase := 'resolved';
        ELSIF OLD.level='WARNING' AND NEW.level='CRITICAL' THEN
            notice_phase := 'escalated';
        ELSIF NEW.last_notice_at IS DISTINCT FROM OLD.last_notice_at THEN
            notice_phase := 'repeated';
        END IF;
    END IF;
    IF notice_phase IS NOT NULL THEN
        INSERT INTO public.stewardship_ops_notice
            (id,actor_id,correlation_id,incident_id,incident_version,phase,
             level,first_seen,observed_at,occurrences)
        VALUES (gen_random_uuid(),NEW.actor_id,NEW.correlation_id,NEW.id,NEW.version,
            notice_phase,NEW.level,NEW.first_seen,
            coalesce(NEW.resolved_at,NEW.last_seen),NEW.occurrences);
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_ops_incident_notice
AFTER INSERT OR UPDATE ON public.stewardship_ops_incident
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_incident_notice_v1();

CREATE FUNCTION public.stewardship_ops_notice_binding_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF pg_trigger_depth()<>2 OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_ops_incident i
        WHERE i.id=NEW.incident_id AND i.version=NEW.incident_version
        AND i.level=NEW.level AND i.first_seen=NEW.first_seen
        AND coalesce(i.resolved_at,i.last_seen)=NEW.observed_at
        AND i.occurrences=NEW.occurrences AND i.correlation_id=NEW.correlation_id
        AND i.last_notice_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Operational notice requires its atomic episode transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_ops_notice_binding
BEFORE INSERT ON public.stewardship_ops_notice
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_notice_binding_v1();

REVOKE ALL ON FUNCTION public.stewardship_ops_incident_notice_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_ops_incident_state_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stewardship_ops_notice_binding_v1() FROM PUBLIC;

-- Critical SQL producers are consumed exactly once, not missed by a Python hook.
CREATE TABLE "stewardship_ops_log_receipt" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL, "correlation_id" uuid NOT NULL,
    "log_id" uuid NOT NULL UNIQUE, "incident_id" uuid NOT NULL,
    "incident_version" bigint NOT NULL CHECK ("incident_version" >= 0),
    "run_id" uuid NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0),
    "worker_id" uuid NOT NULL,
    CONSTRAINT "ops_log_receipt_version" CHECK (("incident_version" >= 1 AND "fence" >= 1))
);
ALTER TABLE "stewardship_ops_log_receipt" ADD CONSTRAINT "stewardship_ops_log__log_id_70e6fcd8_fk_stewardsh" FOREIGN KEY ("log_id") REFERENCES "stewardship_operational_log" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_ops_log_receipt" ADD CONSTRAINT "stewardship_ops_log__incident_id_74efd4e9_fk_stewardsh" FOREIGN KEY ("incident_id") REFERENCES "stewardship_ops_incident" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_ops_log_receipt" ADD CONSTRAINT "stewardship_ops_log__run_id_1fa0689a_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ops_log_receipt_correlation_id_96edbfa2" ON "stewardship_ops_log_receipt" ("correlation_id");
CREATE INDEX "stewardship_ops_log_receipt_incident_id_74efd4e9" ON "stewardship_ops_log_receipt" ("incident_id");
CREATE INDEX "stewardship_ops_log_receipt_run_id_1fa0689a" ON "stewardship_ops_log_receipt" ("run_id");

CREATE FUNCTION public.stewardship_ops_log_receipt_immutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER stewardship_ops_log_receipt_immutable_guard_v1
BEFORE UPDATE OR DELETE ON public.stewardship_ops_log_receipt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_log_receipt_immutable_v1();

CREATE FUNCTION public.stewardship_ops_log_receipt_binding_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_task_run r
        JOIN public.stewardship_operational_log l ON l.id=NEW.log_id
        JOIN public.stewardship_ops_incident i ON i.id=NEW.incident_id
        WHERE r.id=NEW.run_id AND r.task_type='operational_collect'
        AND r.state='running' AND r.fence=NEW.fence AND r.worker_id=NEW.worker_id
        AND r.lease_expires_at>clock_timestamp()
        AND l.level='CRITICAL' AND l.correlation_id=NEW.correlation_id
        AND i.version=NEW.incident_version AND i.level='CRITICAL' AND i.resolved_at IS NULL
        AND i.correlation_id=NEW.correlation_id AND i.kind=CASE l.event
            WHEN 'task_failed' THEN 'task_failed'
            WHEN 'fact_drift' THEN 'storage_integrity'
            WHEN 'source_refresh_invalid' THEN 'source_refresh_failed'
            WHEN 'source_tenant_mismatch' THEN 'source_tenant_mismatch'
            WHEN 'source_destructive_change' THEN 'source_destructive_change'
            WHEN 'source_refresh_held' THEN 'source_refresh_failed'
            WHEN 'source_credential_failed' THEN 'source_refresh_failed'
            WHEN 'source_provider_failed' THEN 'source_refresh_failed'
            WHEN 'mail_provider_failed' THEN 'mail_provider_unavailable'
            WHEN 'campaign_boundary_lag' THEN 'scheduler_lag'
            WHEN 'production_cleanup_failed' THEN 'production_cleanup_failed'
            ELSE 'system_failure' END
    ) THEN
        RAISE EXCEPTION 'Critical log intake requires exact current ownership' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_ops_log_receipt_binding
BEFORE INSERT ON public.stewardship_ops_log_receipt
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ops_log_receipt_binding_v1();
REVOKE ALL ON FUNCTION public.stewardship_ops_log_receipt_binding_v1() FROM PUBLIC;
