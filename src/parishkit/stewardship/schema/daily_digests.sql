-- Fresh-install daily digest ownership; no development upgrade path.
CREATE TABLE "stewardship_daily_digest_preparation" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "campaign_id" uuid NOT NULL, "definition_id" uuid NOT NULL, "revision_id" uuid NOT NULL, "campaign_configuration_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "mode" varchar(16) NOT NULL, "rehearsal_epoch_id" uuid NULL, "cutoff" timestamp with time zone NOT NULL, "cursor" date NULL, "phase" varchar(12) NOT NULL, "occurrence_id" uuid NULL UNIQUE, "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, CONSTRAINT "stewardship_reports_dailydigestpreparation_positive_version" CHECK ("version" >= 1), CONSTRAINT "daily_digest_phase" CHECK ("phase"::text = ANY(ARRAY['dates'::varchar::text,'cover'::varchar::text,'facts'::varchar::text,'fanout'::varchar::text,'complete'::varchar::text,'cancelled'::varchar::text])), CONSTRAINT "daily_digest_namespace" CHECK ((("mode" = 'production' AND "rehearsal_epoch_id" IS NULL) OR ("mode" = 'testing' AND "rehearsal_epoch_id" IS NOT NULL))));
CREATE TABLE "stewardship_daily_digest_snapshot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "preparation_id" uuid NOT NULL UNIQUE, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "source_id" uuid NOT NULL, "population_scope" varchar(12) NOT NULL, "submission_watermark" bigint NOT NULL CHECK ("submission_watermark" >= 0), "timezone_configuration_id" uuid NOT NULL, "through_date" date NOT NULL, "observed_at" timestamp with time zone NOT NULL, "statistics_inputs" text NOT NULL, "covered_dates" jsonb NOT NULL, "run_id" uuid NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL, CONSTRAINT "daily_digest_historical_scope" CHECK ("population_scope" = 'historical'));
CREATE TABLE "stewardship_daily_digest_ready" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "snapshot_id" uuid NOT NULL UNIQUE, "fact_set_id" uuid NOT NULL, "recipient_configuration_id" uuid NOT NULL, "recipients" jsonb NOT NULL, "subject" varchar(254) NOT NULL, "html" text NOT NULL, "text" text NOT NULL, "chart" bytea NOT NULL, "run_id" uuid NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL);
CREATE TABLE "stewardship_daily_digest_recipient" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "ready_id" uuid NOT NULL, "address" varchar(254) NOT NULL, "outbox_id" uuid NOT NULL UNIQUE, CONSTRAINT "daily_digest_recipient_once" UNIQUE ("ready_id", "address"));
CREATE INDEX "daily_digest_definition" ON "stewardship_daily_digest_preparation" ("definition_id", "mode");
CREATE INDEX "stewardship_daily_digest_preparation_correlation_id_ab1c44a3" ON "stewardship_daily_digest_preparation" ("correlation_id");
ALTER TABLE "stewardship_daily_digest_snapshot" ADD CONSTRAINT "stewardship_daily_di_preparation_id_8a653fdb_fk_stewardsh" FOREIGN KEY ("preparation_id") REFERENCES "stewardship_daily_digest_preparation" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_snapshot" ADD CONSTRAINT "stewardship_daily_di_campaign_id_f7568e96_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_snapshot" ADD CONSTRAINT "stewardship_daily_di_configuration_id_5ebb8da2_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_snapshot" ADD CONSTRAINT "stewardship_daily_di_source_id_f5226ad8_fk_stewardsh" FOREIGN KEY ("source_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_snapshot" ADD CONSTRAINT "stewardship_daily_di_timezone_configurati_cf3f2b80_fk_stewardsh" FOREIGN KEY ("timezone_configuration_id") REFERENCES "stewardship_campaign_configuration" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_snapshot" ADD CONSTRAINT "stewardship_daily_di_run_id_4d6b2c6c_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_daily_digest_snapshot_correlation_id_ab254671" ON "stewardship_daily_digest_snapshot" ("correlation_id");
CREATE INDEX "stewardship_daily_digest_snapshot_campaign_id_f7568e96" ON "stewardship_daily_digest_snapshot" ("campaign_id");
CREATE INDEX "stewardship_daily_digest_snapshot_configuration_id_5ebb8da2" ON "stewardship_daily_digest_snapshot" ("configuration_id");
CREATE INDEX "stewardship_daily_digest_snapshot_source_id_f5226ad8" ON "stewardship_daily_digest_snapshot" ("source_id");
CREATE INDEX "stewardship_daily_digest_s_timezone_configuration_id_cf3f2b80" ON "stewardship_daily_digest_snapshot" ("timezone_configuration_id");
CREATE INDEX "stewardship_daily_digest_snapshot_run_id_4d6b2c6c" ON "stewardship_daily_digest_snapshot" ("run_id");
ALTER TABLE "stewardship_daily_digest_ready" ADD CONSTRAINT "stewardship_daily_di_snapshot_id_5226b140_fk_stewardsh" FOREIGN KEY ("snapshot_id") REFERENCES "stewardship_daily_digest_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_ready" ADD CONSTRAINT "stewardship_daily_di_fact_set_id_dfe26277_fk_stewardsh" FOREIGN KEY ("fact_set_id") REFERENCES "stewardship_daily_fact_set" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_ready" ADD CONSTRAINT "stewardship_daily_di_recipient_configurat_d7880fe6_fk_stewardsh" FOREIGN KEY ("recipient_configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_ready" ADD CONSTRAINT "stewardship_daily_di_run_id_de7b1204_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_daily_digest_ready_correlation_id_9277d5a8" ON "stewardship_daily_digest_ready" ("correlation_id");
CREATE INDEX "stewardship_daily_digest_ready_fact_set_id_dfe26277" ON "stewardship_daily_digest_ready" ("fact_set_id");
CREATE INDEX "stewardship_daily_digest_r_recipient_configuration_id_d7880fe6" ON "stewardship_daily_digest_ready" ("recipient_configuration_id");
CREATE INDEX "stewardship_daily_digest_ready_run_id_de7b1204" ON "stewardship_daily_digest_ready" ("run_id");
ALTER TABLE "stewardship_daily_digest_recipient" ADD CONSTRAINT "stewardship_daily_di_ready_id_7fdebcb2_fk_stewardsh" FOREIGN KEY ("ready_id") REFERENCES "stewardship_daily_digest_ready" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_daily_digest_recipient" ADD CONSTRAINT "stewardship_daily_di_outbox_id_02e1d8ef_fk_stewardsh" FOREIGN KEY ("outbox_id") REFERENCES "stewardship_outbox_message" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_daily_digest_recipient_correlation_id_21fff4ce" ON "stewardship_daily_digest_recipient" ("correlation_id");
CREATE INDEX "stewardship_daily_digest_recipient_ready_id_7fdebcb2" ON "stewardship_daily_digest_recipient" ("ready_id");

-- The metadata owner is intentionally independent of report rows. Scheduler
-- admission can inspect it without SELECT on observations, chart bytes or money.
CREATE FUNCTION stewardship_daily_digest_scope_v1(
    campaign uuid, revision uuid, configuration uuid, mode text, epoch uuid
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_campaign c
      JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
      JOIN stewardship_campaign_configuration prepared ON prepared.id=$3
        AND prepared.record_id=c.id AND prepared.timezone=p.timezone
        AND prepared.start_date=p.start_date AND prepared.end_date=p.end_date
      JOIN stewardship_system_configuration r ON r.current_campaign_id=c.id
      JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
      JOIN stewardship_schedule_definition d ON d.campaign_id=c.id
      WHERE c.id=$1 AND d.current_revision_id=$2
        AND d.kind='daily_digest' AND r.mode=$4
        AND NOT r.restore_review_required AND NOT k.go_live_gate
        AND NOT EXISTS(SELECT 1 FROM stewardship_campaign_work_gate g
            WHERE g.campaign_id=c.id AND g.state<>'released')
        AND (($4='production' AND $5 IS NULL
              AND ((c.state IN ('scheduled','active')
                    AND stewardship_campaign_now_v1()>=p.starts_at
                    AND stewardship_campaign_now_v1()<p.ends_at)
                  OR (c.state='closed' AND stewardship_campaign_now_v1()>=p.ends_at))
              AND NOT EXISTS(SELECT 1 FROM stewardship_activation_catchup a
                  WHERE a.campaign_id=c.id AND a.completed_at IS NULL))
            OR ($4='testing' AND c.state='draft' AND k.rehearsal_epoch_id=$5
                AND stewardship_campaign_now_v1()>=p.starts_at
                AND stewardship_campaign_now_v1()<p.ends_at
                AND EXISTS(SELECT 1 FROM stewardship_rehearsal_epoch e
                    WHERE e.id=$5 AND e.campaign_id=c.id AND e.state='active'))))
$$;

CREATE FUNCTION stewardship_daily_digest_live_v1(
    preparation uuid, run uuid, fence bigint, worker uuid
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation p
      JOIN stewardship_task_run t ON t.root_id=p.task_id AND t.id=$2
      WHERE p.id=$1 AND t.domain_request_id=p.id
        AND t.task_type='daily_digest_prepare'
        AND p.phase NOT IN ('complete','cancelled')
        AND stewardship_fact_live(t.id,$3,$4)
        AND stewardship_daily_digest_scope_v1(p.campaign_id,p.revision_id,
            p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id))
$$;

CREATE FUNCTION stewardship_daily_digest_page_v1(prior jsonb, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_campaign_configuration%ROWTYPE;
        v stewardship_schedule_revision%ROWTYPE; first_day date; last_day date; next_day date;
BEGIN
    IF prior->>'phase'<>'dates' OR proposed->>'phase'='cancelled' THEN RETURN true; END IF;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=(proposed->>'campaign_configuration_id')::uuid;
    SELECT * INTO v FROM stewardship_schedule_revision WHERE id=(proposed->>'revision_id')::uuid;
    first_day:=coalesce((prior->>'cursor')::date+1,p.start_date);
    last_day:=(proposed->>'cursor')::date;
    IF proposed->>'cursor' IS DISTINCT FROM prior->>'cursor' THEN
        IF last_day IS NULL OR last_day<first_day OR last_day>first_day+99 OR last_day>p.end_date
        THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM generate_series(first_day::timestamp,last_day::timestamp,interval '1 day') day
            CROSS JOIN LATERAL (SELECT stewardship_catchup_due_v1(v.record_id,day::date::text) AS due) expected
            WHERE expected.due IS NOT NULL AND (expected.due>(proposed->>'cutoff')::timestamptz
              OR (NOT stewardship_schedule_slot_excluded_v1(v.record_id,proposed->>'mode','admins',day::date::text)
                AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode=proposed->>'mode' AND o.target='admins'
                      AND o.slot=day::date::text AND o.due_at=expected.due)))) THEN RETURN false; END IF;
    END IF;
    IF proposed->>'phase'='cover' THEN
        next_day:=coalesce(last_day+1,p.start_date);
        RETURN next_day>p.end_date OR stewardship_resolve_local_v1(
            (next_day+1)+(v.values->>'time')::time,p.timezone)>(proposed->>'cutoff')::timestamptz;
    END IF;
    RETURN true;
END $$;

CREATE FUNCTION stewardship_daily_digest_preparation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Daily preparation history is retained' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.phase<>'dates' OR NEW.version<>1 OR NEW.cursor IS NOT NULL
          OR NEW.occurrence_id IS NOT NULL OR NEW.run_id IS NOT NULL
          OR NEW.task_fence IS NOT NULL OR NEW.worker_id IS NOT NULL
          OR NEW.cutoff>stewardship_campaign_now_v1()
          OR EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation p
              JOIN stewardship_task_run t ON t.root_id=p.task_id
              WHERE p.definition_id=NEW.definition_id AND p.mode=NEW.mode
                AND p.phase NOT IN ('complete','cancelled')
                AND t.state IN ('queued','running','retry_wait','abandoned'))
          OR NOT stewardship_daily_digest_scope_v1(NEW.campaign_id,NEW.revision_id,
              NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id)
          OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_definition d
              WHERE d.id=NEW.definition_id AND d.campaign_id=NEW.campaign_id
                AND d.current_revision_id=NEW.revision_id AND d.kind='daily_digest')
          OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id
              AND t.root_id=t.id AND t.task_type='daily_digest_prepare'
              AND t.domain_request_id=NEW.id AND t.idempotency_key=NEW.id::text
              AND t.state='queued' AND t.initiated_by_id IS NOT DISTINCT FROM NEW.actor_id)
        THEN RAISE EXCEPTION 'Daily preparation lacks current schedule ownership' USING ERRCODE='23514'; END IF;
    ELSE
        IF ROW(NEW.id,NEW.created_at,NEW.campaign_id,NEW.definition_id,NEW.revision_id,
              NEW.campaign_configuration_id,NEW.task_id,NEW.mode,NEW.rehearsal_epoch_id)
            IS DISTINCT FROM
           ROW(OLD.id,OLD.created_at,OLD.campaign_id,OLD.definition_id,OLD.revision_id,
              OLD.campaign_configuration_id,OLD.task_id,OLD.mode,OLD.rehearsal_epoch_id)
          OR (NEW.cutoff<>OLD.cutoff AND NOT (OLD.version=1 AND OLD.phase='dates'
              AND NEW.phase='dates' AND NEW.cursor IS NULL AND NEW.occurrence_id IS NULL
              AND NEW.cutoff>=OLD.cutoff AND NEW.cutoff<=stewardship_campaign_now_v1()))
          OR NEW.version<>OLD.version+1 OR OLD.phase IN ('complete','cancelled')
          OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
          OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.run_id
              AND t.root_id=OLD.task_id AND t.task_type='daily_digest_prepare'
              AND t.domain_request_id=OLD.id
              AND stewardship_fact_live(t.id,NEW.task_fence,NEW.worker_id))
          OR (NEW.phase<>'cancelled' AND NOT stewardship_daily_digest_scope_v1(
              NEW.campaign_id,NEW.revision_id,NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id))
          OR NOT (NEW.phase='cancelled' OR NEW.phase=OLD.phase
              OR (OLD.phase='dates' AND NEW.phase='cover')
              OR (OLD.phase='cover' AND NEW.phase IN ('facts','complete'))
              OR (OLD.phase='facts' AND NEW.phase='fanout')
              OR (OLD.phase='fanout' AND NEW.phase='complete'))
          OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor<OLD.cursor))
          OR (OLD.phase<>'dates' AND NEW.cursor IS DISTINCT FROM OLD.cursor)
          OR NOT stewardship_daily_digest_page_v1(to_jsonb(OLD),to_jsonb(NEW))
          OR (OLD.occurrence_id IS NOT NULL AND NEW.occurrence_id IS DISTINCT FROM OLD.occurrence_id)
          OR (NEW.phase IN ('facts','fanout') AND NEW.occurrence_id IS NULL)
          OR (NEW.phase<>'cancelled' AND NEW.occurrence_id IS NOT NULL AND NOT EXISTS(
              SELECT 1 FROM stewardship_schedule_occurrence o
              WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
                AND o.revision_id=NEW.revision_id AND o.mode=NEW.mode
                AND o.target='admins' AND o.due_at<=NEW.cutoff))
        THEN RAISE EXCEPTION 'Daily preparation requires a fenced monotonic transition' USING ERRCODE='23514'; END IF;
        IF OLD.phase='cover' AND NEW.phase IN ('facts','complete') AND EXISTS(
            SELECT 1 FROM stewardship_schedule_occurrence o
            WHERE o.revision_id=NEW.revision_id AND o.mode=NEW.mode AND o.target='admins'
              AND o.state='pending' AND o.task_id IS NULL AND o.outbox_id IS NULL
              AND o.due_at<=NEW.cutoff AND o.id IS DISTINCT FROM NEW.occurrence_id
              AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
              AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation other
                  WHERE other.id<>NEW.id AND other.occurrence_id=o.id)) THEN
            RAISE EXCEPTION 'Daily coverage must finish before report capture' USING ERRCODE='23514';
        END IF;
        IF NEW.phase='fanout' AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_ready r
            JOIN stewardship_daily_digest_snapshot s ON s.id=r.snapshot_id
            WHERE s.preparation_id=NEW.id) THEN
            RAISE EXCEPTION 'Daily fanout requires retained compiled report content' USING ERRCODE='23514';
        END IF;
        IF OLD.phase='fanout' AND NEW.phase='complete' AND EXISTS(
            SELECT 1 FROM stewardship_daily_digest_ready r
            JOIN stewardship_daily_digest_snapshot s ON s.id=r.snapshot_id
            CROSS JOIN LATERAL jsonb_array_elements_text(r.recipients) recipient
            WHERE s.preparation_id=NEW.id AND NOT EXISTS(
                SELECT 1 FROM stewardship_daily_digest_recipient addressed
                WHERE addressed.ready_id=r.id AND addressed.address=recipient)) THEN
            RAISE EXCEPTION 'Daily completion requires every selected recipient intent' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=statement_timestamp();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER daily_digest_preparation_write BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_daily_digest_preparation FOR EACH ROW
EXECUTE FUNCTION stewardship_daily_digest_preparation_guard_v1();

CREATE FUNCTION stewardship_daily_digest_write_admitted_v1(
    relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE;
        o stewardship_schedule_occurrence%ROWTYPE;
        selected stewardship_schedule_occurrence%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_schedule_occurrence','stewardship_occurrence_transition',
        'stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
    THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    IF relation_name='stewardship_occurrence_transition' THEN
        SELECT * INTO o FROM stewardship_schedule_occurrence WHERE id=(proposed->>'occurrence_id')::uuid;
        RETURN prior IS NULL AND o.id IS NOT NULL
          AND (proposed->>'version')::bigint=o.version AND proposed->>'after_state'=o.state
          AND (proposed->>'actor_id')::uuid=o.actor_id
          AND (proposed->>'correlation_id')::uuid=o.correlation_id
          AND stewardship_daily_digest_write_admitted_v1('stewardship_schedule_occurrence',to_jsonb(o),
              CASE WHEN o.state='pending' THEN NULL ELSE to_jsonb(o)||jsonb_build_object('state','pending') END);
    END IF;
    SELECT preparation.* INTO p FROM stewardship_daily_digest_preparation preparation
      JOIN stewardship_task_run t ON t.root_id=preparation.task_id
      JOIN stewardship_task_event e ON e.run_id=t.id
      WHERE e.id=(proposed->>'correlation_id')::uuid AND e.action='claim' AND e.state='running'
        AND e.worker_id=(proposed->>'actor_id')::uuid AND e.fence=t.fence
        AND stewardship_daily_digest_live_v1(preparation.id,t.id,e.fence,e.worker_id);
    IF p.id IS NULL THEN RETURN false; END IF;
    IF relation_name IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event') THEN
        RETURN stewardship_daily_digest_mail_write_v1(p.id,relation_name,proposed,prior);
    END IF;
    IF proposed->>'target'<>'admins'
      OR (proposed->>'definition_id')::uuid<>p.definition_id
      OR (proposed->>'revision_id')::uuid<>p.revision_id OR proposed->>'mode'<>p.mode
      OR (proposed->>'due_at')::timestamptz>p.cutoff
      OR proposed->>'task_id' IS NOT NULL OR proposed->>'outbox_id' IS NOT NULL
    THEN RETURN false; END IF;
    IF prior IS NULL THEN
        RETURN p.phase='dates' AND proposed->>'state'='pending'
          AND stewardship_catchup_due_v1(p.definition_id,proposed->>'slot') IS NOT NULL
          AND (proposed->>'due_at')::timestamptz=stewardship_catchup_due_v1(p.definition_id,proposed->>'slot');
    END IF;
    IF p.phase<>'cover' OR prior->>'state'<>'pending' OR proposed->>'state'<>'coalesced'
      OR proposed->>'reason'<>'missed_daily_recovery'
      OR prior->>'task_id' IS NOT NULL OR prior->>'outbox_id' IS NOT NULL
      OR EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=(prior->>'id')::uuid)
    THEN RETURN false; END IF;
    SELECT * INTO selected FROM stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    RETURN selected.id IS NOT NULL AND selected.state='pending'
      AND selected.definition_id=p.definition_id AND selected.revision_id=p.revision_id
      AND selected.mode=p.mode AND selected.target='admins'
      AND selected.task_id IS NULL AND selected.outbox_id IS NULL
      AND selected.due_at BETWEEN (proposed->>'due_at')::timestamptz AND p.cutoff
      AND NOT stewardship_schedule_slot_excluded_v1(selected.definition_id,selected.mode,selected.target,selected.slot)
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=selected.id);
END $$;

CREATE FUNCTION stewardship_daily_digest_dates_v1(occurrence uuid, mode text)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH RECURSIVE lineage(id) AS (
        SELECT id FROM stewardship_schedule_occurrence WHERE id=occurrence AND target='admins' AND mode=$2
        UNION
        SELECT edge.previous_id FROM (
            SELECT previous_id,replacement_id FROM stewardship_recovery_replacement
            UNION ALL
            SELECT id,replacement_id FROM stewardship_schedule_occurrence
            WHERE state='coalesced' AND target='admins' AND mode=$2
        ) edge JOIN lineage parent ON parent.id=edge.replacement_id
    ), dates(slot) AS (
        SELECT f.slot FROM stewardship_schedule_fulfillment f JOIN lineage parent ON parent.id=f.occurrence_id
        WHERE f.target='admins' AND f.mode=$2
        UNION
        SELECT o.slot FROM stewardship_schedule_occurrence o JOIN lineage parent ON parent.id=o.id
        WHERE o.target='admins' AND o.mode=$2
    ) SELECT coalesce(jsonb_agg(slot ORDER BY slot),'[]'::jsonb)
      FROM dates WHERE slot ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
$$;

CREATE FUNCTION stewardship_daily_digest_snapshot_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE; observation jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO p FROM stewardship_daily_digest_preparation WHERE id=NEW.preparation_id;
    observation:=NEW.statistics_inputs::jsonb;
    IF p.phase IS DISTINCT FROM 'facts' OR NEW.campaign_id<>p.campaign_id
      OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c
          WHERE c.id=p.campaign_id AND c.active_configuration_id=NEW.timezone_configuration_id)
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT stewardship_daily_digest_live_v1(p.id,NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
          WHERE active_configuration_id=NEW.configuration_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_source_current c
          JOIN stewardship_source_snapshot s ON s.id=c.snapshot_id
          WHERE s.id=NEW.source_id AND s.state='promoted' AND s.compacted_at IS NULL)
      OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
          FROM stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
      OR observation->>'schema' IS DISTINCT FROM 'campaign-statistics-v1'
      OR observation->>'campaign_id' IS DISTINCT FROM NEW.campaign_id::text
      OR observation->>'configuration_id' IS DISTINCT FROM NEW.timezone_configuration_id::text
      OR observation->'source'->>'id' IS DISTINCT FROM NEW.source_id::text
      OR (observation->>'submission_watermark')::bigint IS DISTINCT FROM NEW.submission_watermark
      OR (observation->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at
      OR NEW.observed_at>stewardship_campaign_now_v1()
      OR jsonb_typeof(NEW.covered_dates) IS DISTINCT FROM 'array'
      OR jsonb_array_length(NEW.covered_dates)=0
      OR NEW.covered_dates->>-1 IS DISTINCT FROM NEW.through_date::text
      OR NEW.covered_dates IS DISTINCT FROM stewardship_daily_digest_dates_v1(p.occurrence_id,p.mode)
    THEN RAISE EXCEPTION 'Daily snapshot lacks its exact current observation' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER daily_digest_snapshot_insert BEFORE INSERT ON stewardship_daily_digest_snapshot
FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_snapshot_guard_v1();

CREATE FUNCTION stewardship_daily_digest_source_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=NEW.source_id
        AND parent_kind='digest' AND parent_id=NEW.id AND expires_at IS NULL) THEN
        RAISE EXCEPTION 'Daily snapshot requires retained source protection' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER daily_digest_snapshot_complete AFTER INSERT ON stewardship_daily_digest_snapshot
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_source_commit_v1();

CREATE FUNCTION stewardship_daily_digest_ready_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE snapshot stewardship_daily_digest_snapshot%ROWTYPE; expected jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO snapshot FROM stewardship_daily_digest_snapshot WHERE id=NEW.snapshot_id;
    SELECT coalesce(jsonb_agg(email ORDER BY email),'[]'::jsonb) INTO expected
        FROM stewardship_address_rule WHERE configuration_id=NEW.recipient_configuration_id
          AND roles ? 'administrator';
    IF snapshot.id IS NULL OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT stewardship_daily_digest_live_v1(snapshot.preparation_id,NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_preparation
          WHERE id=snapshot.preparation_id AND phase='facts')
      OR NOT EXISTS(SELECT 1 FROM stewardship_daily_fact_set f WHERE f.id=NEW.fact_set_id
          AND f.state='ready' AND ROW(f.campaign_id,f.population_scope,f.source_id,
              f.submission_watermark,f.timezone_configuration_id,f.through_date)=
            ROW(snapshot.campaign_id,snapshot.population_scope,snapshot.source_id,
              snapshot.submission_watermark,snapshot.timezone_configuration_id,snapshot.through_date))
      OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
          WHERE active_configuration_id=NEW.recipient_configuration_id)
      OR NEW.recipients IS DISTINCT FROM expected OR jsonb_array_length(expected)=0
      OR length(NEW.subject)=0 OR NEW.subject ~ '[\r\n]'
      OR octet_length(NEW.html) NOT BETWEEN 1 AND 1048576
      OR octet_length(NEW.text) NOT BETWEEN 1 AND 1048576
      OR octet_length(NEW.chart) NOT BETWEEN 1 AND 1048576
      OR substring(NEW.chart FROM 1 FOR 8)<>decode('89504e470d0a1a0a','hex')
    THEN RAISE EXCEPTION 'Daily compilation requires exact ready facts and Admin selection' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER daily_digest_ready_insert BEFORE INSERT ON stewardship_daily_digest_ready
FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_ready_guard_v1();

CREATE FUNCTION stewardship_daily_digest_fact_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=NEW.fact_set_id
        AND parent_kind='digest' AND parent_id=NEW.snapshot_id) THEN
        RAISE EXCEPTION 'Daily compilation requires retained fact protection' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot s
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        WHERE s.id=NEW.snapshot_id AND p.phase IN ('fanout','complete')) THEN
        RAISE EXCEPTION 'Daily report protection must release its fanout atomically' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER daily_digest_ready_complete AFTER INSERT ON stewardship_daily_digest_ready
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_fact_commit_v1();

CREATE FUNCTION stewardship_daily_digest_recipient_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_ready r
        JOIN stewardship_daily_digest_snapshot s ON s.id=r.snapshot_id
        JOIN stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        JOIN stewardship_task_run t ON t.root_id=p.task_id
        JOIN stewardship_task_event e ON e.run_id=t.id AND e.action='claim'
            AND e.fence=t.fence AND e.worker_id=t.worker_id AND e.state='running'
        JOIN stewardship_outbox_message m ON m.id=NEW.outbox_id
        JOIN stewardship_outbox_render content ON content.id=m.render_id
        WHERE r.id=NEW.ready_id AND p.phase='fanout' AND r.recipients ? NEW.address
          AND t.worker_id=NEW.actor_id
          AND e.id=NEW.correlation_id AND e.id=m.correlation_id AND m.actor_id=NEW.actor_id
          AND stewardship_daily_digest_live_v1(p.id,t.id,t.fence,t.worker_id)
          AND m.semantic_key=NEW.id AND m.scope_id=s.campaign_id
          AND m.campaign_id=s.campaign_id AND m.family_id IS NULL
          AND m.purpose='daily_digest' AND m.mode=p.mode AND m.credential_namespace='none'
          AND m.state='pending' AND content.intended_recipients=jsonb_build_array(NEW.address)
          AND right(content.html,length(r.html))=r.html
          AND right(content.text,length(r.text))=r.text)
    THEN RAISE EXCEPTION 'Daily recipient requires its own exact compiled message' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER daily_digest_recipient_insert BEFORE INSERT ON stewardship_daily_digest_recipient
FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_recipient_guard_v1();

CREATE FUNCTION stewardship_daily_digest_mail_write_v1(
    preparation uuid,relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_daily_digest_preparation%ROWTYPE;
        r stewardship_daily_digest_ready%ROWTYPE;
        m stewardship_outbox_message%ROWTYPE;
        runtime stewardship_system_configuration%ROWTYPE;
        template uuid; email jsonb;
BEGIN
    -- The caller has already verified the exact live claim event and work lock.
    -- This helper admits allocation only, never later provider state changes.
    SELECT * INTO p FROM stewardship_daily_digest_preparation WHERE id=preparation;
    SELECT ready.* INTO r FROM stewardship_daily_digest_ready ready
        JOIN stewardship_daily_digest_snapshot s ON s.id=ready.snapshot_id
        WHERE s.preparation_id=p.id;
    IF prior IS NOT NULL OR p.phase<>'fanout' OR r.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
        RETURN proposed->>'purpose'='daily_digest' AND proposed->>'mode'=p.mode
            AND (proposed->>'scope_id')::uuid=p.campaign_id
            AND (proposed->>'campaign_id')::uuid=p.campaign_id
            AND proposed->>'family_id' IS NULL AND proposed->>'credential_namespace'='none'
            AND proposed->>'state'='pending' AND proposed->>'action'='created'
            AND (proposed->>'version')::bigint=1;
    END IF;
    SELECT * INTO m FROM stewardship_outbox_message WHERE id=(proposed->>'message_id')::uuid;
    IF m.id IS NULL OR m.purpose<>'daily_digest' OR m.campaign_id<>p.campaign_id
        OR m.mode<>p.mode OR m.state<>'pending' OR m.version<>1
        OR m.correlation_id IS DISTINCT FROM (proposed->>'correlation_id')::uuid
        OR m.actor_id IS DISTINCT FROM (proposed->>'actor_id')::uuid THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_event' THEN
        RETURN proposed->>'action'='created' AND proposed->>'state'='pending'
            AND (proposed->>'version')::bigint=1;
    END IF;
    IF relation_name<>'stewardship_outbox_render' THEN RETURN false; END IF;
    SELECT * INTO runtime FROM stewardship_system_configuration;
    SELECT content.id INTO template FROM stewardship_content_version content
        JOIN stewardship_schedule_revision revision ON revision.id=p.revision_id
        WHERE content.configuration_id=runtime.active_configuration_id
          AND content.campaign_id=p.campaign_id AND content.kind='email'
          AND content.record_id=(revision.values->>'template_version')::uuid;
    SELECT settings INTO email FROM stewardship_applied_integration
        WHERE configuration_id=runtime.active_configuration_id AND kind='email';
    RETURN (proposed->>'id')::uuid=m.render_id
        AND (proposed->>'configuration_id')::uuid=runtime.active_configuration_id
        AND (proposed->>'template_id')::uuid=template
        AND proposed->>'sender'=email->>'sender' AND proposed->>'reply_to'=email->>'reply_to'
        AND jsonb_array_length(proposed->'intended_recipients')=1
        AND r.recipients ? (proposed->'intended_recipients'->>0)
        AND proposed->'routed_recipients'=CASE p.mode WHEN 'testing'
            THEN jsonb_build_array(runtime.testing_recipient) ELSE proposed->'intended_recipients' END
        AND right(proposed->>'html',length(r.html))=r.html
        AND right(proposed->>'text',length(r.text))=r.text;
END $$;
-- Invoker-only predicate: execution cannot confer the private SELECT privileges
-- required by its lookups, and it grants no mutation or provider authority.

CREATE FUNCTION stewardship_daily_digest_mail_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='daily_digest' AND EXISTS(
        SELECT 1 FROM stewardship_task_event e JOIN stewardship_task_run t ON t.id=e.run_id
        WHERE e.id=NEW.correlation_id AND t.task_type='daily_digest_prepare')
      AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_recipient
          WHERE id=NEW.semantic_key AND outbox_id=NEW.id
            AND actor_id=NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
        RAISE EXCEPTION 'Daily message requires atomic recipient ownership' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION stewardship_daily_digest_mail_commit_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER daily_digest_mail_complete AFTER INSERT ON stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_mail_commit_v1();

CREATE FUNCTION stewardship_daily_digest_pin_retained_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF OLD.parent_kind='digest' THEN
        IF EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot WHERE id=OLD.parent_id) THEN
            IF TG_OP='DELETE' THEN
                IF TG_TABLE_NAME='stewardship_source_pin'
                    AND stewardship_cleanup_effect_v1('source_pins',OLD.id)
                    AND stewardship_cleanup_effect_v1('daily_digest_snapshots',OLD.parent_id) THEN
                    RETURN OLD;
                ELSIF TG_TABLE_NAME='stewardship_fact_pin'
                    AND stewardship_cleanup_effect_v1('daily_digest_fact_pins',OLD.id) THEN
                    RETURN OLD;
                END IF;
            END IF;
            RAISE EXCEPTION 'Retained daily report still requires its exact inputs' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER daily_digest_source_retained BEFORE UPDATE OR DELETE ON stewardship_source_pin
FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_pin_retained_v1();
CREATE TRIGGER daily_digest_facts_retained BEFORE UPDATE OR DELETE ON stewardship_fact_pin
FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_pin_retained_v1();

-- Retention release is deliberately not a generic DELETE privilege. Only the
-- private checkpoint effect may delete one exact inventoried Testing record.
CREATE FUNCTION stewardship_daily_digest_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' AND stewardship_cleanup_effect_v1(TG_ARGV[0],OLD.id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Daily report records are immutable outside owned cleanup' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION stewardship_daily_digest_immutable_v1() FROM PUBLIC;

DO $$ DECLARE item text[]; BEGIN
    FOREACH item SLICE 1 IN ARRAY ARRAY[
        ['snapshot','daily_digest_snapshots'],['ready','daily_digest_ready'],
        ['recipient','daily_digest_recipients']] LOOP
        EXECUTE format('CREATE TRIGGER daily_digest_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION stewardship_daily_digest_immutable_v1(%L)',
            'stewardship_daily_digest_' || item[1],item[2]);
        EXECUTE format('CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION stewardship_cleanup_protect_v1(%L)',
            'stewardship_daily_digest_' || item[1],item[2]);
    END LOOP;
END $$;
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON stewardship_fact_pin
FOR EACH ROW EXECUTE FUNCTION stewardship_cleanup_protect_v1('daily_digest_fact_pins');
