-- Fresh-install weekly metadata and private report ownership.
CREATE TABLE "stewardship_weekly_digest_preparation" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "campaign_id" uuid NOT NULL, "definition_id" uuid NOT NULL, "revision_id" uuid NOT NULL, "campaign_configuration_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "mode" varchar(16) NOT NULL, "rehearsal_epoch_id" uuid NULL, "cutoff" timestamp with time zone NOT NULL, "cursor" date NULL, "phase" varchar(12) NOT NULL, "occurrence_id" uuid NULL UNIQUE, "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, CONSTRAINT "stewardship_reports_weeklydigestpreparation_positive_version" CHECK ("version" >= 1), CONSTRAINT "weekly_digest_phase" CHECK ("phase"::text = ANY(ARRAY['dates'::varchar::text,'cover'::varchar::text,'capture'::varchar::text,'fanout'::varchar::text,'complete'::varchar::text,'cancelled'::varchar::text])), CONSTRAINT "weekly_digest_namespace" CHECK ((("mode" = 'production' AND "rehearsal_epoch_id" IS NULL) OR ("mode" = 'testing' AND "rehearsal_epoch_id" IS NOT NULL))));
CREATE TABLE "stewardship_weekly_digest_snapshot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "preparation_id" uuid NOT NULL UNIQUE, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "timezone_configuration_id" uuid NOT NULL, "source_id" uuid NOT NULL, "observed_at" timestamp with time zone NOT NULL, "submission_watermark" bigint NOT NULL CHECK ("submission_watermark" >= 0), "after_watermark" bigint NOT NULL CHECK ("after_watermark" >= 0), "observation" jsonb NOT NULL, "information" jsonb NOT NULL, "corrections" jsonb NOT NULL, "recipients" jsonb NOT NULL, "run_id" uuid NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL, CONSTRAINT "weekly_digest_watermark_order" CHECK ("after_watermark" <= ("submission_watermark")));
CREATE TABLE "stewardship_weekly_digest_recipient" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "snapshot_id" uuid NOT NULL, "address" varchar(254) NOT NULL, "information" jsonb NOT NULL, "corrections" jsonb NOT NULL, "covered_messages" jsonb NOT NULL, "subject" varchar(254) NOT NULL, "html" text NOT NULL, "text" text NOT NULL, "outbox_id" uuid NULL UNIQUE, CONSTRAINT "weekly_digest_recipient_once" UNIQUE ("snapshot_id", "address"));
CREATE INDEX "stewardship_weekly_digest_preparation_correlation_id_b800adb1" ON "stewardship_weekly_digest_preparation" ("correlation_id");
CREATE INDEX "weekly_digest_definition" ON "stewardship_weekly_digest_preparation" ("definition_id", "mode");
-- Part of the fresh empty baseline, not an upgrade of retained databases.
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD COLUMN "item_versions" jsonb NOT NULL;
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD CONSTRAINT "stewardship_weekly_d_preparation_id_f6c01e2c_fk_stewardsh" FOREIGN KEY ("preparation_id") REFERENCES "stewardship_weekly_digest_preparation" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD CONSTRAINT "stewardship_weekly_d_campaign_id_9b0800c9_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD CONSTRAINT "stewardship_weekly_d_configuration_id_70a81e50_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD CONSTRAINT "stewardship_weekly_d_timezone_configurati_bc8c98c0_fk_stewardsh" FOREIGN KEY ("timezone_configuration_id") REFERENCES "stewardship_campaign_configuration" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD CONSTRAINT "stewardship_weekly_d_source_id_843caf49_fk_stewardsh" FOREIGN KEY ("source_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_digest_snapshot" ADD CONSTRAINT "stewardship_weekly_d_run_id_a73c17f3_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_weekly_digest_snapshot_correlation_id_d3b13e1b" ON "stewardship_weekly_digest_snapshot" ("correlation_id");
CREATE INDEX "stewardship_weekly_digest_snapshot_campaign_id_9b0800c9" ON "stewardship_weekly_digest_snapshot" ("campaign_id");
CREATE INDEX "stewardship_weekly_digest_snapshot_configuration_id_70a81e50" ON "stewardship_weekly_digest_snapshot" ("configuration_id");
CREATE INDEX "stewardship_weekly_digest__timezone_configuration_id_bc8c98c0" ON "stewardship_weekly_digest_snapshot" ("timezone_configuration_id");
CREATE INDEX "stewardship_weekly_digest_snapshot_source_id_843caf49" ON "stewardship_weekly_digest_snapshot" ("source_id");
CREATE INDEX "stewardship_weekly_digest_snapshot_run_id_a73c17f3" ON "stewardship_weekly_digest_snapshot" ("run_id");
ALTER TABLE "stewardship_weekly_digest_recipient" ADD CONSTRAINT "stewardship_weekly_d_snapshot_id_b8b061c6_fk_stewardsh" FOREIGN KEY ("snapshot_id") REFERENCES "stewardship_weekly_digest_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_weekly_digest_recipient" ADD CONSTRAINT "stewardship_weekly_d_outbox_id_e6bef118_fk_stewardsh" FOREIGN KEY ("outbox_id") REFERENCES "stewardship_outbox_message" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_weekly_digest_recipient_correlation_id_6a5984af" ON "stewardship_weekly_digest_recipient" ("correlation_id");
CREATE INDEX "stewardship_weekly_digest_recipient_snapshot_id_b8b061c6" ON "stewardship_weekly_digest_recipient" ("snapshot_id");

-- The metadata owner is intentionally independent of report rows. Scheduler
-- admission can inspect it without SELECT on request text, names or Admin addresses.
CREATE FUNCTION stewardship_weekly_digest_scope_v1(
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
        AND d.kind='weekly_digest' AND r.mode=$4
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

CREATE FUNCTION stewardship_weekly_digest_live_v1(
    preparation uuid, run uuid, fence bigint, worker uuid
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation p
      JOIN stewardship_task_run t ON t.root_id=p.task_id AND t.id=$2
      WHERE p.id=$1 AND t.domain_request_id=p.id
        AND t.task_type='weekly_digest_prepare'
        AND p.phase NOT IN ('complete','cancelled')
        AND stewardship_fact_live(t.id,$3,$4)
        AND stewardship_weekly_digest_scope_v1(p.campaign_id,p.revision_id,
            p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id))
$$;

CREATE FUNCTION stewardship_weekly_digest_page_v1(prior jsonb, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_campaign_configuration%ROWTYPE;
        v stewardship_schedule_revision%ROWTYPE; first_day date; last_day date; next_day date;
BEGIN
    IF prior->>'phase'<>'dates' OR proposed->>'phase'='cancelled' THEN RETURN true; END IF;
    SELECT * INTO p FROM stewardship_campaign_configuration WHERE id=(proposed->>'campaign_configuration_id')::uuid;
    SELECT * INTO v FROM stewardship_schedule_revision WHERE id=(proposed->>'revision_id')::uuid;
    first_day:=coalesce((prior->>'cursor')::date+7,
        p.start_date+(((v.values->>'weekday')::int-extract(isodow FROM p.start_date)::int+8)%7));
    last_day:=(proposed->>'cursor')::date;
    IF proposed->>'cursor' IS DISTINCT FROM prior->>'cursor' THEN
        IF last_day IS NULL OR last_day<first_day OR last_day>first_day+99*7
          OR last_day>p.end_date OR (last_day-first_day)%7<>0 THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM generate_series(first_day::timestamp,last_day::timestamp,interval '7 days') day
            CROSS JOIN LATERAL (SELECT stewardship_catchup_due_v1(v.record_id,day::date::text) AS due) expected
            WHERE expected.due IS NULL OR expected.due>(proposed->>'cutoff')::timestamptz
              OR (NOT stewardship_schedule_slot_excluded_v1(v.record_id,proposed->>'mode','admins',day::date::text)
                AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode=proposed->>'mode' AND o.target='admins'
                      AND o.slot=day::date::text AND o.due_at=expected.due))) THEN RETURN false; END IF;
    END IF;
    IF proposed->>'phase'='cover' THEN
        next_day:=coalesce(last_day+7,first_day);
        RETURN next_day>p.end_date OR stewardship_resolve_local_v1(
            next_day+(v.values->>'time')::time,p.timezone)>(proposed->>'cutoff')::timestamptz;
    END IF;
    RETURN true;
END $$;

-- Both the scheduler and page guard consult the same unresolved lineage. These
-- are metadata-only IDs; no report values or recipient addresses are exposed.
CREATE FUNCTION stewardship_weekly_digest_predecessors_v1(definition uuid,mode text,epoch uuid)
RETURNS SETOF uuid LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT o.id FROM stewardship_schedule_occurrence o
    JOIN stewardship_schedule_definition d ON d.id=o.definition_id
    WHERE d.id=$1 AND d.kind='weekly_digest' AND o.mode=$2 AND o.target='admins'
      AND o.state='skipped' AND o.reason='schedule_replaced'
      AND o.revision_id<>d.current_revision_id
      AND NOT EXISTS(SELECT 1 FROM stewardship_recovery_replacement e WHERE e.previous_id=o.id)
      AND (EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment f
                  WHERE f.occurrence_id=o.id AND f.disposition='coalesced')
           OR EXISTS(SELECT 1 FROM stewardship_recovery_replacement e WHERE e.replacement_id=o.id))
      AND ($2='production' AND $3 IS NULL OR $2='testing' AND EXISTS(
          SELECT 1 FROM stewardship_weekly_digest_preparation p
          WHERE p.occurrence_id=o.id AND p.rehearsal_epoch_id=$3))
$$;

CREATE FUNCTION stewardship_weekly_digest_replacement_v1(proposed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation p
        JOIN stewardship_task_run t ON t.root_id=p.task_id
        JOIN stewardship_task_event e ON e.run_id=t.id AND e.action='claim'
            AND e.fence=t.fence AND e.worker_id=t.worker_id AND e.state='running'
        JOIN stewardship_schedule_occurrence o ON o.id=(proposed->>'replacement_id')::uuid
        WHERE p.id=(proposed->>'preparation_id')::uuid AND p.phase='cover'
          AND (proposed->>'previous_id')::uuid IN (
              SELECT stewardship_weekly_digest_predecessors_v1(p.definition_id,p.mode,p.rehearsal_epoch_id))
          AND (proposed->>'actor_id')::uuid=t.worker_id AND (proposed->>'correlation_id')::uuid=e.id
          AND stewardship_weekly_digest_live_v1(p.id,t.id,t.fence,t.worker_id)
          AND o.definition_id=p.definition_id AND o.revision_id=p.revision_id AND o.mode=p.mode
          AND o.target='admins' AND o.state='pending' AND o.due_at<=p.cutoff
          AND o.task_id IS NULL AND o.outbox_id IS NULL
          AND (p.occurrence_id IS NULL OR p.occurrence_id=o.id)
          AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
          AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation other
              WHERE other.id<>p.id AND other.occurrence_id=o.id))
$$;

-- Serial interval progress does not confuse completed preparation with a fully
-- resolved recipient cohort. Uncertain/queued old-revision children still hold
-- replacement generation until their normal cancellation/reconciliation owner.
CREATE FUNCTION stewardship_weekly_digest_unresolved_v1(definition uuid,mode text,epoch uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation p
        JOIN stewardship_schedule_definition d ON d.id=p.definition_id
        LEFT JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE p.definition_id=$1 AND p.mode=$2 AND p.rehearsal_epoch_id IS NOT DISTINCT FROM $3
          AND ((p.revision_id=d.current_revision_id AND p.phase NOT IN ('complete','cancelled'))
            OR (p.revision_id=d.current_revision_id AND p.phase='complete'
                AND o.state NOT IN ('succeeded','skipped','coalesced'))
            OR EXISTS(SELECT 1 FROM stewardship_weekly_digest_snapshot s
                JOIN stewardship_weekly_digest_recipient r ON r.snapshot_id=s.id
                JOIN stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE s.preparation_id=p.id AND m.state IN ('pending','retry_wait','submitting','delivery_unknown'))))
$$;

CREATE FUNCTION stewardship_weekly_digest_preparation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Weekly preparation history is retained' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.cursor IS NOT NULL OR NEW.run_id IS NOT NULL
          OR NEW.task_fence IS NOT NULL OR NEW.worker_id IS NOT NULL
          OR NOT ((NEW.phase='dates' AND NEW.occurrence_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_manual_request WHERE id=NEW.id))
            OR (NEW.phase='capture' AND NEW.occurrence_id=NEW.id
                AND EXISTS(SELECT 1 FROM stewardship_weekly_manual_request request
                    JOIN stewardship_schedule_occurrence o ON o.id=request.id
                    WHERE request.id=NEW.id AND request.campaign_id=NEW.campaign_id
                      AND request.task_id=NEW.task_id AND request.actor_id=NEW.actor_id
                      AND o.slot='manual:'||request.id::text AND o.state='pending'
                      AND o.definition_id=NEW.definition_id AND o.revision_id=NEW.revision_id
                      AND o.mode=NEW.mode AND o.due_at=NEW.cutoff)))
          OR NEW.cutoff>stewardship_campaign_now_v1()
          OR stewardship_weekly_digest_unresolved_v1(NEW.definition_id,NEW.mode,NEW.rehearsal_epoch_id)
          OR NOT stewardship_weekly_digest_scope_v1(NEW.campaign_id,NEW.revision_id,
              NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id)
          OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_definition d
              WHERE d.id=NEW.definition_id AND d.campaign_id=NEW.campaign_id
                AND d.current_revision_id=NEW.revision_id AND d.kind='weekly_digest')
          OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id
              AND t.root_id=t.id AND t.task_type='weekly_digest_prepare'
              AND t.domain_request_id=NEW.id AND t.idempotency_key=NEW.id::text
              AND t.state='queued' AND t.initiated_by_id IS NOT DISTINCT FROM NEW.actor_id)
        THEN RAISE EXCEPTION 'Weekly preparation lacks current schedule ownership' USING ERRCODE='23514'; END IF;
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
              AND t.root_id=OLD.task_id AND t.task_type='weekly_digest_prepare'
              AND t.domain_request_id=OLD.id
              AND stewardship_fact_live(t.id,NEW.task_fence,NEW.worker_id))
          OR (NEW.phase<>'cancelled' AND NOT stewardship_weekly_digest_scope_v1(
              NEW.campaign_id,NEW.revision_id,NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id))
          OR NOT (NEW.phase='cancelled' OR NEW.phase=OLD.phase
              OR (OLD.phase='dates' AND NEW.phase='cover')
              OR (OLD.phase='cover' AND NEW.phase IN ('capture','complete'))
              OR (OLD.phase='capture' AND NEW.phase='fanout')
              OR (OLD.phase='fanout' AND NEW.phase='complete'))
          OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor<OLD.cursor))
          OR (OLD.phase<>'dates' AND NEW.cursor IS DISTINCT FROM OLD.cursor)
          OR NOT stewardship_weekly_digest_page_v1(to_jsonb(OLD),to_jsonb(NEW))
          OR (OLD.occurrence_id IS NOT NULL AND NEW.occurrence_id IS DISTINCT FROM OLD.occurrence_id)
          OR (NEW.phase IN ('capture','fanout') AND NEW.occurrence_id IS NULL)
          OR (NEW.phase<>'cancelled' AND NEW.occurrence_id IS NOT NULL AND NOT EXISTS(
              SELECT 1 FROM stewardship_schedule_occurrence o
              WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
                AND o.revision_id=NEW.revision_id AND o.mode=NEW.mode
                AND o.target='admins' AND o.due_at<=NEW.cutoff))
        THEN RAISE EXCEPTION 'Weekly preparation requires a fenced monotonic transition' USING ERRCODE='23514'; END IF;
        IF OLD.phase='cover' AND NEW.phase IN ('capture','complete') AND EXISTS(
            SELECT 1 FROM stewardship_weekly_digest_predecessors_v1(
                NEW.definition_id,NEW.mode,NEW.rehearsal_epoch_id)) THEN
            RAISE EXCEPTION 'Weekly replacement coverage must finish before capture' USING ERRCODE='23514';
        END IF;
        IF OLD.phase='cover' AND NEW.phase IN ('capture','complete') AND EXISTS(
            SELECT 1 FROM stewardship_schedule_occurrence o
            WHERE o.revision_id=NEW.revision_id AND o.mode=NEW.mode AND o.target='admins'
              AND o.state='pending' AND o.task_id IS NULL AND o.outbox_id IS NULL
              AND o.due_at<=NEW.cutoff AND o.id IS DISTINCT FROM NEW.occurrence_id
              AND NOT stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
              AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation other
                  WHERE other.id<>NEW.id AND other.occurrence_id=o.id)) THEN
            RAISE EXCEPTION 'Weekly coverage must finish before report capture' USING ERRCODE='23514';
        END IF;
        IF NEW.phase='fanout' AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_snapshot s
            WHERE s.preparation_id=NEW.id) THEN
            RAISE EXCEPTION 'Weekly fanout requires a retained report snapshot' USING ERRCODE='23514';
        END IF;
        IF OLD.phase='fanout' AND NEW.phase='complete' AND EXISTS(
            SELECT 1 FROM stewardship_weekly_digest_snapshot s
            CROSS JOIN LATERAL jsonb_array_elements_text(s.recipients) recipient
            WHERE s.preparation_id=NEW.id
              AND (s.information<>'[]'::jsonb OR s.corrections<>'[]'::jsonb) AND NOT EXISTS(
                SELECT 1 FROM stewardship_weekly_digest_recipient addressed
                WHERE addressed.snapshot_id=s.id AND addressed.address=recipient)) THEN
            RAISE EXCEPTION 'Weekly completion requires every selected recipient intent' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=statement_timestamp();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER weekly_digest_preparation_write BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_weekly_digest_preparation FOR EACH ROW
EXECUTE FUNCTION stewardship_weekly_digest_preparation_guard_v1();

CREATE FUNCTION stewardship_weekly_digest_write_admitted_v1(
    relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p stewardship_weekly_digest_preparation%ROWTYPE;
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
          AND stewardship_weekly_digest_write_admitted_v1('stewardship_schedule_occurrence',to_jsonb(o),
              CASE WHEN o.state='pending' THEN NULL ELSE to_jsonb(o)||jsonb_build_object('state','pending') END);
    END IF;
    SELECT preparation.* INTO p FROM stewardship_weekly_digest_preparation preparation
      JOIN stewardship_task_run t ON t.root_id=preparation.task_id
      JOIN stewardship_task_event e ON e.run_id=t.id
      WHERE e.id=(proposed->>'correlation_id')::uuid AND e.action='claim' AND e.state='running'
        AND e.worker_id=(proposed->>'actor_id')::uuid AND e.fence=t.fence
        AND stewardship_weekly_digest_live_v1(preparation.id,t.id,e.fence,e.worker_id);
    IF p.id IS NULL THEN RETURN false; END IF;
    IF relation_name IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event') THEN
        RETURN stewardship_weekly_digest_mail_write_v1(p.id,relation_name,proposed,prior);
    END IF;
    IF proposed->>'target'<>'admins'
      OR (proposed->>'definition_id')::uuid<>p.definition_id
      OR (proposed->>'revision_id')::uuid<>p.revision_id OR proposed->>'mode'<>p.mode
      OR (proposed->>'due_at')::timestamptz>p.cutoff
      OR proposed->>'task_id' IS NOT NULL OR proposed->>'outbox_id' IS NOT NULL
    THEN RETURN false; END IF;
    IF prior IS NULL THEN
        IF p.phase='cover' AND proposed->>'slot'='recovery:'||p.id::text THEN
            RETURN proposed->>'state'='pending' AND p.occurrence_id IS NULL
                AND (proposed->>'due_at')::timestamptz=(SELECT max(candidate.due_at)
                    FROM stewardship_schedule_occurrence candidate WHERE candidate.id IN (
                        SELECT stewardship_weekly_digest_predecessors_v1(p.definition_id,p.mode,p.rehearsal_epoch_id)))
                AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence candidate
                    WHERE candidate.revision_id=p.revision_id AND candidate.mode=p.mode AND candidate.target='admins'
                      AND candidate.slot='recovery:'||p.id::text
                      AND candidate.id<>(proposed->>'id')::uuid);
        END IF;
        RETURN p.phase='dates' AND proposed->>'state'='pending'
          AND stewardship_catchup_due_v1(p.definition_id,proposed->>'slot') IS NOT NULL
          AND (proposed->>'due_at')::timestamptz=stewardship_catchup_due_v1(p.definition_id,proposed->>'slot');
    END IF;
    IF p.phase<>'cover' OR prior->>'state'<>'pending' OR proposed->>'state'<>'coalesced'
      OR proposed->>'reason'<>'missed_weekly_recovery'
      OR prior->>'task_id' IS NOT NULL OR prior->>'outbox_id' IS NOT NULL
      OR EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=(prior->>'id')::uuid)
    THEN RETURN false; END IF;
    SELECT * INTO selected FROM stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    RETURN selected.id IS NOT NULL AND selected.state='pending'
      AND selected.definition_id=p.definition_id AND selected.revision_id=p.revision_id
      AND selected.mode=p.mode AND selected.target='admins'
      AND selected.task_id IS NULL AND selected.outbox_id IS NULL
      AND selected.due_at BETWEEN (proposed->>'due_at')::timestamptz AND p.cutoff
      AND NOT stewardship_schedule_slot_excluded_v1(selected.definition_id,selected.mode,selected.target,selected.slot)
      AND NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=selected.id);
END $$;
