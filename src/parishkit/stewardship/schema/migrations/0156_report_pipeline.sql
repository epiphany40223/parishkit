-- Frozen forward migration file 0156: the
-- report email pipeline (#1786, report-emails spec "Report send records",
-- "Report pipeline" and "Database functions", delivery plan item 3). This
-- file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- It adds the four report email tables (manual request, preparation,
-- snapshot, recipient), their guards and write admissions, the report slot
-- evaluation (stewardship_report_slot_due_v1, the database's copy of
-- campaigns/schedule_evaluation.ReportPlan, pinned to it by
-- tests/stewardship/fixtures/report_slot_cases.json), the report page proof,
-- the cohort, recheck, inputs and open-request reads, and the completion and
-- delivery-side checks, all with plain CREATE. The only existing objects it
-- replaces are the two task-type maps, which gain report_email_prepare (the
-- login map is the baseline's final definition in functions.sql; the
-- creators map belongs to migration files). The metadata finalizer's task
-- type, report_email_finalize, which the completion proof already names,
-- arrives with the scheduler's producers in the conversion file.
--
-- Nothing uses the pipeline yet. Its write admissions are called by the
-- shared guards (the occurrence, recovery replacement, outbox, source and
-- fact pin and cleanup guards) only once the conversion file rewrites them,
-- and the schedule kind check admits "report" only then; until then no
-- report definition, preparation or message can exist. Every function is
-- invoker's rights except the manual request's guard and allocation and the
-- completion settlement, which are SECURITY DEFINER, stated again with
-- ALTER as every definer is. Table grants are runtime_grants'
-- (reports/report_email_grants.py). The file ends with a DO block that
-- refuses to commit unless all of it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The four tables (reports/report_email_models.py).
CREATE TABLE "stewardship_report_email_manual_request" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "definition_id" uuid NOT NULL, "revision_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE);
CREATE TABLE "stewardship_report_email_preparation" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "campaign_id" uuid NOT NULL, "definition_id" uuid NOT NULL, "revision_id" uuid NOT NULL, "campaign_configuration_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "mode" varchar(16) NOT NULL, "rehearsal_epoch_id" uuid NULL, "cutoff" timestamp with time zone NOT NULL, "cursor" date NULL, "phase" varchar(12) NOT NULL, "occurrence_id" uuid NULL UNIQUE, "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, "covered_slots" jsonb NULL, "as_of_kind" varchar(16) NULL, "as_of" timestamp with time zone NULL, "captured_at" timestamp with time zone NULL, "report_day" date NULL, "source_id" uuid NULL, "submission_watermark" bigint NULL CHECK ("submission_watermark" >= 0), "timezone_configuration_id" uuid NULL, "through_date" date NULL, CONSTRAINT "stewardship_reports_reportemailpreparation_positive_version" CHECK ("version" >= 1), CONSTRAINT "report_email_phase" CHECK ("phase"::text = ANY(ARRAY['dates'::varchar::text,'cover'::varchar::text,'inputs'::varchar::text,'capture'::varchar::text,'fanout'::varchar::text,'complete'::varchar::text,'cancelled'::varchar::text])), CONSTRAINT "report_email_namespace" CHECK ((("mode" = 'production' AND "rehearsal_epoch_id" IS NULL) OR ("mode" = 'testing' AND "rehearsal_epoch_id" IS NOT NULL))), CONSTRAINT "report_email_inputs" CHECK ((("as_of" IS NULL AND "as_of_kind" IS NULL AND "captured_at" IS NULL AND "covered_slots" IS NULL AND "report_day" IS NULL AND "source_id" IS NULL AND "submission_watermark" IS NULL AND "through_date" IS NULL AND "timezone_configuration_id" IS NULL) OR ("as_of" IS NOT NULL AND "as_of_kind"::text = ANY(ARRAY['previous_day_end'::varchar::text,'send_time'::varchar::text]) AND "captured_at" IS NOT NULL AND "covered_slots" IS NOT NULL AND "source_id" IS NOT NULL AND "submission_watermark" IS NOT NULL AND "timezone_configuration_id" IS NOT NULL))));
CREATE TABLE "stewardship_report_email_snapshot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "values" jsonb NOT NULL, "blocks" jsonb NOT NULL, "has_content" boolean NOT NULL, "recipients" jsonb NOT NULL, "subject" varchar(254) NOT NULL, "html" text NOT NULL, "text" text NOT NULL, "images" jsonb NOT NULL, "fence" bigint NOT NULL CHECK ("fence" >= 0), "worker_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "fact_set_id" uuid NULL, "preparation_id" uuid NOT NULL UNIQUE, "run_id" uuid NOT NULL);
CREATE TABLE "stewardship_report_email_recipient" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "address" varchar(254) NOT NULL, "kind" varchar(8) NOT NULL, "ref" varchar(64) NULL, "disposition" varchar(8) NOT NULL, "outbox_id" uuid NULL UNIQUE, "snapshot_id" uuid NOT NULL, CONSTRAINT "report_email_recipient_once" UNIQUE ("snapshot_id", "address"), CONSTRAINT "report_email_recipient_kind" CHECK ("kind"::text = ANY(ARRAY['user'::varchar::text,'group'::varchar::text,'outside'::varchar::text])), CONSTRAINT "report_email_recipient_disposition" CHECK ((("disposition" = 'queued' AND "outbox_id" IS NOT NULL) OR ("disposition" = 'revoked' AND "outbox_id" IS NULL))));
ALTER TABLE "stewardship_report_email_manual_request" ADD CONSTRAINT "stewardship_report_e_campaign_id_604abb5c_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_manual_request" ADD CONSTRAINT "stewardship_report_e_configuration_id_878575fa_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_manual_request" ADD CONSTRAINT "stewardship_report_e_task_id_babe5887_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_report_email_manual_request_correlation_id_3e97581f" ON "stewardship_report_email_manual_request" ("correlation_id");
CREATE INDEX "stewardship_report_email_manual_request_campaign_id_604abb5c" ON "stewardship_report_email_manual_request" ("campaign_id");
CREATE INDEX "stewardship_report_email_m_configuration_id_878575fa" ON "stewardship_report_email_manual_request" ("configuration_id");
CREATE INDEX "stewardship_report_email_preparation_correlation_id_8f254868" ON "stewardship_report_email_preparation" ("correlation_id");
CREATE INDEX "report_email_definition" ON "stewardship_report_email_preparation" ("definition_id", "mode");
ALTER TABLE "stewardship_report_email_snapshot" ADD CONSTRAINT "stewardship_report_e_campaign_id_15f0de8f_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_snapshot" ADD CONSTRAINT "stewardship_report_e_configuration_id_e86e3ebe_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_snapshot" ADD CONSTRAINT "stewardship_report_e_fact_set_id_115c7a18_fk_stewardsh" FOREIGN KEY ("fact_set_id") REFERENCES "stewardship_daily_fact_set" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_snapshot" ADD CONSTRAINT "stewardship_report_e_preparation_id_23e4b41a_fk_stewardsh" FOREIGN KEY ("preparation_id") REFERENCES "stewardship_report_email_preparation" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_snapshot" ADD CONSTRAINT "stewardship_report_e_run_id_4afa33ab_fk_stewardsh" FOREIGN KEY ("run_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_report_email_snapshot_correlation_id_0f53351f" ON "stewardship_report_email_snapshot" ("correlation_id");
CREATE INDEX "stewardship_report_email_snapshot_campaign_id_15f0de8f" ON "stewardship_report_email_snapshot" ("campaign_id");
CREATE INDEX "stewardship_report_email_snapshot_configuration_id_e86e3ebe" ON "stewardship_report_email_snapshot" ("configuration_id");
CREATE INDEX "stewardship_report_email_snapshot_fact_set_id_115c7a18" ON "stewardship_report_email_snapshot" ("fact_set_id");
CREATE INDEX "stewardship_report_email_snapshot_run_id_4afa33ab" ON "stewardship_report_email_snapshot" ("run_id");
ALTER TABLE "stewardship_report_email_recipient" ADD CONSTRAINT "stewardship_report_e_outbox_id_ce89de5d_fk_stewardsh" FOREIGN KEY ("outbox_id") REFERENCES "stewardship_outbox_message" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_report_email_recipient" ADD CONSTRAINT "stewardship_report_e_snapshot_id_1745a2c0_fk_stewardsh" FOREIGN KEY ("snapshot_id") REFERENCES "stewardship_report_email_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_report_email_recipient_correlation_id_62a752e9" ON "stewardship_report_email_recipient" ("correlation_id");
CREATE INDEX "stewardship_report_email_recipient_snapshot_id_1745a2c0" ON "stewardship_report_email_recipient" ("snapshot_id");

-- Slot evaluation. A slot key is a campaign-local date: a send's own date
-- (slot basis send_date) or the day before it, the day it reports on
-- (report_date, the converted Daily). Bounds name send dates. These three
-- are the database's copy of schedule_evaluation.ReportPlan, case for case.

-- The first candidate send date after "after" (NULL: from the beginning),
-- before skip dates, pauses and missing days; NULL when the rule has none.
-- A weekly rule's candidates are at most seven days apart.
CREATE FUNCTION public.stewardship_report_next_send_v1(
    vals jsonb, start_date date, end_date date, after date
) RETURNS date LANGUAGE plpgsql IMMUTABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE s jsonb:=vals->'schedule'; first_send date; last_send date; base date; day date;
BEGIN
    IF s->>'type'='once' THEN
        day:=(s->>'date')::date;
        RETURN CASE WHEN after IS NULL OR after<day THEN day END;
    END IF;
    IF s->>'type' IS DISTINCT FROM 'repeat' THEN RETURN NULL; END IF;
    IF s->>'from'='campaign_start' THEN first_send:=start_date;
    ELSIF s->>'from'='day_after_campaign_start' THEN first_send:=start_date+1;
    ELSE first_send:=(s->>'from')::date;
    END IF;
    IF s->>'until'='campaign_end' THEN last_send:=end_date;
    ELSIF s->>'until'='day_after_campaign_end' THEN last_send:=end_date+1;
    ELSIF s->>'until'='first_send_after_campaign_end' THEN
        IF s->>'frequency'='daily' THEN last_send:=end_date+1;
        ELSE
            SELECT min(end_date+k) INTO last_send FROM generate_series(1,7) k
            WHERE s->'weekdays' @> to_jsonb(extract(isodow FROM end_date+k)::integer-1);
        END IF;
    ELSIF s->>'until'<>'archive' THEN last_send:=(s->>'until')::date;
    END IF;
    base:=CASE WHEN after IS NULL OR after<first_send THEN first_send-1 ELSE after END;
    FOR k IN 1..7 LOOP
        day:=base+k;
        IF last_send IS NOT NULL AND day>last_send THEN RETURN NULL; END IF;
        IF s->>'frequency'='daily'
           OR (s->>'frequency'='weekly'
               AND s->'weekdays' @> to_jsonb(extract(isodow FROM day)::integer-1)) THEN
            RETURN day;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

-- The due instant of one slot of a report's values in its campaign, or NULL
-- when the slot is not a send: not a canonical date, not a candidate, a
-- skipped slot, a send due inside a pause interval, or, for an every-day
-- rule, a slot day with no moments (as the Daily digest's rule leaves out).
CREATE FUNCTION public.stewardship_report_slot_due_v1(
    vals jsonb, start_date date, end_date date, zone text, slot text
) RETURNS timestamptz LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE day date; send date; shift integer; due timestamptz; y integer; m integer; d integer;
BEGIN
    -- Parse without an exception block: an impossible date is no slot.
    IF slot IS NULL OR slot !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN NULL; END IF;
    y:=substr(slot,1,4)::integer; m:=substr(slot,6,2)::integer; d:=substr(slot,9,2)::integer;
    IF y<1 OR m NOT BETWEEN 1 AND 12 OR d<1
       OR d>extract(day FROM make_date(y,m,1)+interval '1 month'-interval '1 day') THEN
        RETURN NULL;
    END IF;
    day:=make_date(y,m,d);
    shift:=CASE vals->>'slot_basis' WHEN 'report_date' THEN 1 WHEN 'send_date' THEN 0 END;
    IF shift IS NULL OR coalesce(vals->'skip_dates' ? slot,false) THEN RETURN NULL; END IF;
    send:=day+shift;
    IF public.stewardship_report_next_send_v1(vals,start_date,end_date,send-1)
       IS DISTINCT FROM send THEN
        RETURN NULL;
    END IF;
    IF vals->'schedule'->>'type'='repeat' AND vals->'schedule'->>'frequency'='daily'
       AND public.stewardship_resolve_local_v1(day::timestamp,zone)
           =public.stewardship_resolve_local_v1((day+1)::timestamp,zone) THEN
        RETURN NULL;
    END IF;
    due:=public.stewardship_resolve_local_v1(send+(vals->'schedule'->>'time')::time,zone);
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(vals->'paused_ranges','[]'::jsonb)) r
        WHERE (r->>'start')::timestamptz<=due
          AND (r->>'end' IS NULL OR due<(r->>'end')::timestamptz)) THEN
        RETURN NULL;
    END IF;
    RETURN due;
END $$;

-- The canonical slot check of one report definition, as the shared
-- stewardship_catchup_due_v1 is for the other kinds: its current revision's
-- values in its campaign's active configuration.
CREATE FUNCTION public.stewardship_report_due_v1(definition uuid, slot text)
RETURNS timestamptz LANGUAGE sql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_report_slot_due_v1(v.values,p.start_date,p.end_date,p.timezone,$2)
    FROM public.stewardship_schedule_definition d
    JOIN public.stewardship_schedule_revision v ON v.id=d.current_revision_id
    JOIN public.stewardship_campaign c ON c.id=d.campaign_id
    JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    WHERE d.id=$1 AND d.kind='report'
$$;

-- Scope: the report definition is current, its campaign may send in this
-- mode, and no restore, go-live or work gate holds it. Unlike a digest a
-- report may send before the campaign starts and after it closes, until it
-- is archived (decision 9), so Production admits a scheduled, active or
-- closed campaign whatever the time. Metadata only: no report values.
CREATE FUNCTION public.stewardship_report_email_scope_v1(
    campaign uuid, revision uuid, configuration uuid, mode text, epoch uuid
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_campaign c
      JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
      JOIN public.stewardship_campaign_configuration prepared ON prepared.id=$3
        AND prepared.record_id=c.id AND prepared.timezone=p.timezone
        AND prepared.start_date=p.start_date AND prepared.end_date=p.end_date
      JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
      JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
      JOIN public.stewardship_schedule_definition d ON d.campaign_id=c.id
      WHERE c.id=$1 AND d.current_revision_id=$2 AND d.kind='report' AND r.mode=$4
        AND NOT r.restore_review_required AND NOT k.go_live_gate
        AND NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_work_gate g
            WHERE g.campaign_id=c.id AND g.state<>'released')
        AND (($4='production' AND $5 IS NULL AND c.state IN ('scheduled','active','closed')
              AND NOT EXISTS(SELECT 1 FROM public.stewardship_activation_catchup a
                  WHERE a.campaign_id=c.id AND a.completed_at IS NULL))
            OR ($4='testing' AND c.state='draft' AND k.rehearsal_epoch_id=$5
                AND EXISTS(SELECT 1 FROM public.stewardship_rehearsal_epoch e
                    WHERE e.id=$5 AND e.campaign_id=c.id AND e.state='active'))))
$$;

CREATE FUNCTION public.stewardship_report_email_live_v1(
    preparation uuid, run uuid, fence bigint, worker uuid
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
      JOIN public.stewardship_task_run t ON t.root_id=p.task_id AND t.id=$2
      WHERE p.id=$1 AND t.domain_request_id=p.id
        AND t.task_type='report_email_prepare'
        AND p.phase NOT IN ('complete','cancelled')
        AND public.stewardship_fact_live(t.id,$3,$4)
        AND public.stewardship_report_email_scope_v1(p.campaign_id,p.revision_id,
            p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id))
$$;

-- Page proof: a dates page may move the cursor only over slots that are not
-- due by the cutoff, are excluded (fulfilled, held or resolved), or have
-- their occurrence; and the dates phase ends only when the next candidate
-- send is due after the cutoff (or there is none). A page examines at most
-- 100 candidates, which for a weekly rule is at most 735 days.
CREATE FUNCTION public.stewardship_report_email_page_v1(prior jsonb, proposed jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_campaign_configuration%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE;
        shift integer; first_day date; last_day date; next_send date;
BEGIN
    IF prior->>'phase'<>'dates' OR proposed->>'phase'='cancelled' THEN RETURN true; END IF;
    SELECT * INTO p FROM public.stewardship_campaign_configuration
        WHERE id=(proposed->>'campaign_configuration_id')::uuid;
    SELECT * INTO v FROM public.stewardship_schedule_revision
        WHERE id=(proposed->>'revision_id')::uuid;
    IF p.id IS NULL OR v.id IS NULL THEN RETURN false; END IF;
    shift:=CASE v.values->>'slot_basis' WHEN 'report_date' THEN 1 ELSE 0 END;
    last_day:=(proposed->>'cursor')::date;
    IF proposed->>'cursor' IS DISTINCT FROM prior->>'cursor' THEN
        first_day:=coalesce((prior->>'cursor')::date+1,
            public.stewardship_report_next_send_v1(v.values,p.start_date,p.end_date,NULL)-shift);
        IF last_day IS NULL OR first_day IS NULL OR last_day<first_day OR last_day>first_day+735
        THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM generate_series(first_day::timestamp,last_day::timestamp,interval '1 day') day
            CROSS JOIN LATERAL (SELECT public.stewardship_report_slot_due_v1(v.values,p.start_date,
                p.end_date,p.timezone,day::date::text) AS due) expected
            WHERE expected.due IS NOT NULL AND (expected.due>(proposed->>'cutoff')::timestamptz
              OR (NOT public.stewardship_schedule_slot_excluded_v1(v.record_id,proposed->>'mode','admins',day::date::text)
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode=proposed->>'mode' AND o.target='admins'
                      AND o.slot=day::date::text AND o.due_at=expected.due)))) THEN RETURN false; END IF;
    END IF;
    IF proposed->>'phase'='cover' THEN
        next_send:=public.stewardship_report_next_send_v1(v.values,p.start_date,p.end_date,last_day+shift);
        RETURN next_send IS NULL OR public.stewardship_resolve_local_v1(
            next_send+(v.values->'schedule'->>'time')::time,p.timezone)>(proposed->>'cutoff')::timestamptz;
    END IF;
    RETURN true;
END $$;

-- The unresolved replaced-revision occurrences a new preparation must cover
-- first, as stewardship_daily_digest_predecessors_v1 lists a digest's.
CREATE FUNCTION public.stewardship_report_email_predecessors_v1(definition uuid,mode text,epoch uuid)
RETURNS SETOF uuid LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT o.id FROM public.stewardship_schedule_occurrence o
    JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
    WHERE d.id=$1 AND d.kind='report' AND o.mode=$2 AND o.target='admins'
      AND o.state='skipped' AND o.reason='schedule_replaced'
      AND o.revision_id<>d.current_revision_id
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e WHERE e.previous_id=o.id)
      AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                  WHERE f.occurrence_id=o.id AND f.disposition='coalesced')
           OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e WHERE e.replacement_id=o.id))
      AND ($2='production' AND $3 IS NULL OR $2='testing' AND EXISTS(
          SELECT 1 FROM public.stewardship_report_email_preparation p
          WHERE p.occurrence_id=o.id AND p.rehearsal_epoch_id=$3))
$$;

-- Whether a recovery replacement edge belongs to this cover page (called by
-- the recovery replacement guard once the conversion admits reports).
CREATE FUNCTION public.stewardship_report_email_replacement_v1(proposed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
        JOIN public.stewardship_task_run t ON t.root_id=p.task_id
        JOIN public.stewardship_task_event e ON e.run_id=t.id AND e.action='claim'
            AND e.fence=t.fence AND e.worker_id=t.worker_id AND e.state='running'
        JOIN public.stewardship_schedule_occurrence o ON o.id=(proposed->>'replacement_id')::uuid
        WHERE p.id=(proposed->>'preparation_id')::uuid AND p.phase='cover'
          AND (proposed->>'previous_id')::uuid IN (
              SELECT public.stewardship_report_email_predecessors_v1(p.definition_id,p.mode,p.rehearsal_epoch_id))
          AND (proposed->>'actor_id')::uuid=t.worker_id AND (proposed->>'correlation_id')::uuid=e.id
          AND public.stewardship_report_email_live_v1(p.id,t.id,t.fence,t.worker_id)
          AND o.definition_id=p.definition_id AND o.revision_id=p.revision_id AND o.mode=p.mode
          AND o.target='admins' AND o.state='pending' AND o.due_at<=p.cutoff
          AND o.task_id IS NULL AND o.outbox_id IS NULL
          AND (p.occurrence_id IS NULL OR p.occurrence_id=o.id)
          AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation other
              WHERE other.id<>p.id AND other.occurrence_id=o.id))
$$;

-- The slots one occurrence covers through its coalesced and replaced
-- lineage: date slots only, so a manual or recovery key adds none.
CREATE FUNCTION public.stewardship_report_email_slots_v1(occurrence uuid, mode text)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH RECURSIVE lineage(id) AS (
        SELECT id FROM public.stewardship_schedule_occurrence WHERE id=occurrence AND target='admins' AND mode=$2
        UNION
        SELECT edge.previous_id FROM (
            SELECT previous_id,replacement_id FROM public.stewardship_recovery_replacement
            UNION ALL
            SELECT id,replacement_id FROM public.stewardship_schedule_occurrence
            WHERE state='coalesced' AND target='admins' AND mode=$2
        ) edge JOIN lineage parent ON parent.id=edge.replacement_id
    ), slots(slot) AS (
        SELECT f.slot FROM public.stewardship_schedule_fulfillment f JOIN lineage parent ON parent.id=f.occurrence_id
        WHERE f.target='admins' AND f.mode=$2
        UNION
        SELECT o.slot FROM public.stewardship_schedule_occurrence o JOIN lineage parent ON parent.id=o.id
        WHERE o.target='admins' AND o.mode=$2
    ) SELECT coalesce(jsonb_agg(slot ORDER BY slot),'[]'::jsonb)
      FROM slots WHERE slot ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
$$;

-- The inputs a send records from its covered slots and capture instant
-- (spec, "As of"): report_day is the day every fact block reports (NULL
-- before the campaign's first day has ended), held when a later day is held
-- at the campaign's last day, as_of the moment every figure is counted at,
-- and through_date the fact generation's last day, only with a fact block.
-- reports/report_email_inputs.py computes the same in Python.
CREATE FUNCTION public.stewardship_report_email_inputs_v1(
    vals jsonb, covered jsonb, captured_at timestamptz,
    start_date date, end_date date, zone text
) RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE base date; report_day date; as_of timestamptz; facts boolean;
BEGIN
    facts:=EXISTS(SELECT 1 FROM jsonb_array_elements(vals->'blocks') b
        WHERE b->>'block' IN ('campaign_totals','participation_charts','day_by_day',
            'compared_with_past_campaigns'));
    IF vals->>'as_of'='send_time' THEN
        base:=(captured_at AT TIME ZONE public.stewardship_timezone_name_v1(zone))::date;
        as_of:=captured_at;
    ELSE
        -- The last covered report day, or the day before the capture.
        IF jsonb_array_length(covered)>0 THEN
            base:=(covered->>-1)::date-CASE vals->>'slot_basis' WHEN 'report_date' THEN 0 ELSE 1 END;
        ELSE
            base:=(captured_at AT TIME ZONE public.stewardship_timezone_name_v1(zone))::date-1;
        END IF;
    END IF;
    report_day:=CASE WHEN base<start_date THEN NULL ELSE least(base,end_date) END;
    IF vals->>'as_of'<>'send_time' THEN
        as_of:=least(public.stewardship_resolve_local_v1(
            (coalesce(report_day,base)+1)::timestamp,zone),captured_at);
    END IF;
    RETURN jsonb_build_object('report_day',report_day,'held',base>end_date,'as_of',as_of,
        'through_date',CASE WHEN facts THEN report_day END);
END $$;

-- The frozen cohort of a report's recipient entries under one applied
-- configuration (spec, "Resolving recipients"): the groups expand to the
-- users holding the role (All Staff is the Staff role, not Administrators,
-- who are their own group), a chosen user must be an Administrator or Staff,
-- an outside address is itself, and each address appears once, chosen by its
-- first entry. Sorted by address. reports/report_email_cohort.py resolves
-- the same in Python.
CREATE FUNCTION public.stewardship_report_email_cohort_v1(vals jsonb, configuration uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH entries AS (
        SELECT e.value AS entry, e.ordinality AS position
        FROM jsonb_array_elements(vals->'recipients') WITH ORDINALITY e
    ), chosen AS (
        SELECT lower(a.email) AS address,'user' AS kind,x.entry->>'user' AS ref,x.position
        FROM entries x JOIN public.stewardship_address_rule a
            ON a.configuration_id=$2 AND a.record_id::text=x.entry->>'user'
        WHERE x.entry ? 'user' AND (a.roles ? 'administrator' OR a.roles ? 'staff')
        UNION ALL
        SELECT lower(a.email),'group',x.entry->>'group',x.position
        FROM entries x JOIN public.stewardship_address_rule a ON a.configuration_id=$2
        WHERE (x.entry->>'group'='administrators' AND a.roles ? 'administrator')
           OR (x.entry->>'group'='staff' AND a.roles ? 'staff' AND NOT a.roles ? 'administrator')
        UNION ALL
        SELECT x.entry->>'address','outside',NULL,x.position
        FROM entries x WHERE x.entry ? 'address'
    ), distinct_addresses AS (
        SELECT DISTINCT ON (address) address,kind,ref FROM chosen ORDER BY address,position
    ) SELECT coalesce(jsonb_agg(jsonb_build_object('address',address,'kind',kind,'ref',ref)
        ORDER BY address COLLATE "C"),'[]'::jsonb) FROM distinct_addresses
$$;

-- The recheck before a recipient's message is made or submitted: a user or
-- group recipient still holds the authority it was chosen by in the active
-- configuration; an outside address is still on the report's current
-- revision. "definition" is the report's definition.
CREATE FUNCTION public.stewardship_report_email_current_v1(
    definition uuid, kind text, ref text, address text
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE $2
        WHEN 'outside' THEN EXISTS(SELECT 1 FROM public.stewardship_schedule_definition d
            JOIN public.stewardship_schedule_revision v ON v.id=d.current_revision_id
            WHERE d.id=$1 AND d.kind='report'
              AND v.values->'recipients' @> jsonb_build_array(jsonb_build_object('address',$4)))
        ELSE EXISTS(SELECT 1 FROM public.stewardship_system_configuration runtime
            JOIN public.stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
            WHERE lower(a.email)=$4 AND CASE
                WHEN $2='user' THEN a.record_id::text=$3
                    AND (a.roles ? 'administrator' OR a.roles ? 'staff')
                WHEN $2='group' AND $3='administrators' THEN a.roles ? 'administrator'
                WHEN $2='group' AND $3='staff' THEN a.roles ? 'staff'
                    AND NOT a.roles ? 'administrator'
                ELSE false END)
    END
$$;

-- How a Testing send's first line names the intended recipient (spec,
-- "Testing redirect"): an Administrator, a Staff member or an outside address.
CREATE FUNCTION public.stewardship_report_email_label_v1(kind text, ref text, address text)
RETURNS text LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE
        WHEN $1='outside' THEN 'outside address'
        WHEN $1='group' AND $2='administrators' THEN 'Administrator'
        WHEN $1='group' THEN 'Staff member'
        WHEN EXISTS(SELECT 1 FROM public.stewardship_system_configuration runtime
            JOIN public.stewardship_address_rule a ON a.configuration_id=runtime.active_configuration_id
            WHERE a.record_id::text=$2 AND a.roles ? 'administrator') THEN 'Administrator'
        ELSE 'Staff member' END
$$;

-- The open additional information requests of a campaign at an as-of
-- moment (decisions 14 and 15): live requests submitted by then that are
-- still current and not followed up (To review or To handle), oldest first,
-- each [id, Family DUID, Family name, submitted at, text], the Family named
-- from the pinned source as the Weekly digest names it.
CREATE FUNCTION public.stewardship_report_email_requests_v1(
    campaign uuid, source uuid, as_of timestamptz
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH items AS (
        SELECT i.id,f.family_duid,s.submitted_at,i.text,
            COALESCE(NULLIF(btrim(p.canonical::jsonb->>'lastName'),''),
                NULLIF(btrim(p.canonical::jsonb->>'mailingName'),''),
                NULLIF(btrim(concat_ws(' ',
                    NULLIF(btrim(p.canonical::jsonb->>'firstName'),''),
                    NULLIF(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                CASE WHEN p.id IS NULL THEN 'Not in ParishSoft'
                    ELSE 'Name not recorded in ParishSoft' END) AS family_name
        FROM public.stewardship_submission s
        JOIN public.stewardship_additional_information i ON i.submission_id=s.id
        JOIN public.stewardship_family_campaign f ON f.id=s.family_id
        LEFT JOIN public.stewardship_snapshot_family m ON m.snapshot_id=$2
            AND m.source_key=f.family_duid::text
        LEFT JOIN public.stewardship_source_family p ON p.id=m.payload_id
        WHERE s.campaign_id=$1 AND s.mode='live' AND s.submitted_at<=$3
          AND i.disposition='current_actionable' AND i.followed_up_at IS NULL
    ) SELECT coalesce(jsonb_agg(jsonb_build_array(id,family_duid,family_name,
        submitted_at,text) ORDER BY submitted_at,id),'[]'::jsonb) FROM items
$$;

-- Whether a send is skipped as empty (spec, "Sending only when there is
-- content"): no block has content and the report sends only then. Send
-- now is never skipped, so a manual send's snapshot never is.
CREATE FUNCTION public.stewardship_report_email_skipped_v1(snapshot uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_snapshot s
        JOIN public.stewardship_report_email_preparation p ON p.id=s.preparation_id
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE s.id=$1 AND NOT s.has_content
          AND (s.values->>'content_required')::boolean AND o.slot !~ '^manual:')
$$;

-- The preparation's lifecycle (spec, "Report pipeline"): a scheduled send
-- starts at dates, a manual one (allocated with its request) at inputs; the
-- phases move forward only, each fenced to its live task claim, and the
-- recorded inputs are written once, at inputs to capture, exactly as the
-- database derives them.
CREATE FUNCTION public.stewardship_report_email_preparation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c public.stewardship_campaign_configuration%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE; expected jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Report preparation history is retained' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.version<>1 OR NEW.cursor IS NOT NULL OR NEW.run_id IS NOT NULL
          OR NEW.task_fence IS NOT NULL OR NEW.worker_id IS NOT NULL
          OR NEW.covered_slots IS NOT NULL OR NEW.cutoff>public.stewardship_campaign_now_v1()
          OR NOT (
              (NEW.phase='dates' AND NEW.occurrence_id IS NULL)
              OR (NEW.phase='inputs' AND NEW.occurrence_id=NEW.id AND EXISTS(
                  SELECT 1 FROM public.stewardship_report_email_manual_request r
                  JOIN public.stewardship_schedule_occurrence o ON o.id=r.id
                  WHERE r.id=NEW.id AND r.task_id=NEW.task_id AND r.campaign_id=NEW.campaign_id
                    AND r.definition_id=NEW.definition_id AND r.revision_id=NEW.revision_id
                    AND o.definition_id=NEW.definition_id AND o.revision_id=NEW.revision_id
                    AND o.mode=NEW.mode AND o.target='admins' AND o.state='pending'
                    AND o.slot='manual:'||NEW.id::text)))
          OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
              JOIN public.stewardship_task_run t ON t.root_id=p.task_id
              WHERE p.definition_id=NEW.definition_id AND p.mode=NEW.mode
                AND p.phase NOT IN ('complete','cancelled')
                AND t.state IN ('queued','running','retry_wait','abandoned'))
          OR NOT public.stewardship_report_email_scope_v1(NEW.campaign_id,NEW.revision_id,
              NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id)
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition d
              WHERE d.id=NEW.definition_id AND d.campaign_id=NEW.campaign_id
                AND d.current_revision_id=NEW.revision_id AND d.kind='report')
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.task_id
              AND t.root_id=t.id AND t.task_type='report_email_prepare'
              AND t.domain_request_id=NEW.id AND t.idempotency_key=NEW.id::text
              AND t.state='queued' AND t.initiated_by_id IS NOT DISTINCT FROM NEW.actor_id)
        THEN RAISE EXCEPTION 'Report preparation lacks current schedule ownership' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.created_at,NEW.campaign_id,NEW.definition_id,NEW.revision_id,
          NEW.campaign_configuration_id,NEW.task_id,NEW.mode,NEW.rehearsal_epoch_id)
        IS DISTINCT FROM
       ROW(OLD.id,OLD.created_at,OLD.campaign_id,OLD.definition_id,OLD.revision_id,
          OLD.campaign_configuration_id,OLD.task_id,OLD.mode,OLD.rehearsal_epoch_id)
      OR (NEW.cutoff<>OLD.cutoff AND NOT (OLD.version=1 AND OLD.phase='dates'
          AND NEW.phase='dates' AND NEW.cursor IS NULL AND NEW.occurrence_id IS NULL
          AND NEW.cutoff>=OLD.cutoff AND NEW.cutoff<=public.stewardship_campaign_now_v1()))
      OR NEW.version<>OLD.version+1 OR OLD.phase IN ('complete','cancelled')
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.run_id
          AND t.root_id=OLD.task_id AND t.task_type='report_email_prepare'
          AND t.domain_request_id=OLD.id
          AND public.stewardship_fact_live(t.id,NEW.task_fence,NEW.worker_id))
      OR (NEW.phase<>'cancelled' AND NOT public.stewardship_report_email_scope_v1(
          NEW.campaign_id,NEW.revision_id,NEW.campaign_configuration_id,NEW.mode,NEW.rehearsal_epoch_id))
      OR NOT (NEW.phase='cancelled' OR NEW.phase=OLD.phase
          OR (OLD.phase='dates' AND NEW.phase='cover')
          OR (OLD.phase='cover' AND NEW.phase IN ('inputs','complete'))
          OR (OLD.phase='inputs' AND NEW.phase='capture')
          OR (OLD.phase='capture' AND NEW.phase IN ('fanout','complete'))
          OR (OLD.phase='fanout' AND NEW.phase='complete'))
      OR (OLD.cursor IS NOT NULL AND (NEW.cursor IS NULL OR NEW.cursor<OLD.cursor))
      OR (OLD.phase<>'dates' AND NEW.cursor IS DISTINCT FROM OLD.cursor)
      OR NOT public.stewardship_report_email_page_v1(to_jsonb(OLD),to_jsonb(NEW))
      OR (OLD.occurrence_id IS NOT NULL AND NEW.occurrence_id IS DISTINCT FROM OLD.occurrence_id)
      OR (NEW.phase IN ('inputs','capture','fanout') AND NEW.occurrence_id IS NULL)
      OR (NEW.phase<>'cancelled' AND NEW.occurrence_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM public.stewardship_schedule_occurrence o
          WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
            AND o.revision_id=NEW.revision_id AND o.mode=NEW.mode
            AND o.target='admins' AND o.due_at<=NEW.cutoff))
      -- The inputs are recorded exactly once, entering capture.
      OR (NOT (OLD.phase='inputs' AND NEW.phase='capture')
          AND ROW(NEW.covered_slots,NEW.as_of_kind,NEW.as_of,NEW.captured_at,NEW.report_day,
              NEW.source_id,NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM
              ROW(OLD.covered_slots,OLD.as_of_kind,OLD.as_of,OLD.captured_at,OLD.report_day,
              OLD.source_id,OLD.submission_watermark,OLD.timezone_configuration_id,OLD.through_date))
    THEN RAISE EXCEPTION 'Report preparation requires a fenced monotonic transition' USING ERRCODE='23514'; END IF;
    IF OLD.phase='cover' AND NEW.phase IN ('inputs','complete') AND (EXISTS(
        SELECT 1 FROM public.stewardship_report_email_predecessors_v1(
            NEW.definition_id,NEW.mode,NEW.rehearsal_epoch_id)) OR EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=NEW.revision_id AND o.mode=NEW.mode AND o.target='admins'
          AND o.state='pending' AND o.task_id IS NULL AND o.outbox_id IS NULL
          AND o.due_at<=NEW.cutoff AND o.id IS DISTINCT FROM NEW.occurrence_id
          AND o.slot !~ '^manual:'
          AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation other
              WHERE other.id<>NEW.id AND other.occurrence_id=o.id))) THEN
        RAISE EXCEPTION 'Report coverage must finish before its inputs' USING ERRCODE='23514';
    END IF;
    IF OLD.phase='inputs' AND NEW.phase='capture' THEN
        SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=NEW.revision_id;
        SELECT p.* INTO c FROM public.stewardship_campaign x
            JOIN public.stewardship_campaign_configuration p ON p.id=x.active_configuration_id
            WHERE x.id=NEW.campaign_id;
        expected:=public.stewardship_report_email_inputs_v1(v.values,NEW.covered_slots,
            NEW.captured_at,c.start_date,c.end_date,c.timezone);
        IF NEW.covered_slots IS DISTINCT FROM public.stewardship_report_email_slots_v1(NEW.occurrence_id,NEW.mode)
          OR NEW.as_of_kind IS DISTINCT FROM v.values->>'as_of'
          OR NEW.captured_at NOT BETWEEN transaction_timestamp() AND statement_timestamp()
          OR NEW.timezone_configuration_id IS DISTINCT FROM c.id
          OR NEW.as_of IS DISTINCT FROM (expected->>'as_of')::timestamptz
          OR NEW.report_day IS DISTINCT FROM (expected->>'report_day')::date
          OR NEW.through_date IS DISTINCT FROM (expected->>'through_date')::date
          OR NOT EXISTS(SELECT 1 FROM public.stewardship_source_current current
              JOIN public.stewardship_source_snapshot s ON s.id=current.snapshot_id
              WHERE s.id=NEW.source_id AND s.state='promoted' AND s.compacted_at IS NULL)
          OR NEW.submission_watermark<>coalesce((SELECT max(campaign_sequence)
              FROM public.stewardship_submission WHERE campaign_id=NEW.campaign_id AND mode='live'),0)
        THEN RAISE EXCEPTION 'Report inputs must be the exact current observation' USING ERRCODE='23514'; END IF;
    END IF;
    -- Fanout needs the snapshot, a recipient and something to send.
    IF NEW.phase='fanout' AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_snapshot s
        WHERE s.preparation_id=NEW.id AND s.recipients<>'[]'::jsonb
          AND NOT public.stewardship_report_email_skipped_v1(s.id)) THEN
        RAISE EXCEPTION 'Report fanout requires its committed snapshot with something to send' USING ERRCODE='23514';
    END IF;
    -- A capture completes without fanout only when nothing is to be sent:
    -- the send is skipped as empty, or it has no recipient.
    IF OLD.phase='capture' AND NEW.phase='complete' AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_report_email_snapshot s WHERE s.preparation_id=NEW.id
          AND (s.recipients='[]'::jsonb OR public.stewardship_report_email_skipped_v1(s.id))) THEN
        RAISE EXCEPTION 'Report capture completes only with nothing to send' USING ERRCODE='23514';
    END IF;
    IF OLD.phase='fanout' AND NEW.phase='complete' AND EXISTS(
        SELECT 1 FROM public.stewardship_report_email_snapshot s
        CROSS JOIN LATERAL jsonb_array_elements(s.recipients) member
        WHERE s.preparation_id=NEW.id AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_report_email_recipient r
            WHERE r.snapshot_id=s.id AND r.address=member->>'address')) THEN
        RAISE EXCEPTION 'Report completion requires every cohort recipient' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER report_email_preparation_write BEFORE INSERT OR UPDATE OR DELETE
ON public.stewardship_report_email_preparation FOR EACH ROW
EXECUTE FUNCTION public.stewardship_report_email_preparation_guard_v1();

-- Recorded inputs keep their source: the same transaction pins it to the
-- preparation, as a digest snapshot pins its source.
CREATE FUNCTION public.stewardship_report_email_source_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.source_id IS NOT NULL AND OLD.source_id IS NULL AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_source_pin WHERE snapshot_id=NEW.source_id
          AND parent_kind='digest' AND parent_id=NEW.id AND expires_at IS NULL) THEN
        RAISE EXCEPTION 'Report inputs require retained source protection' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER report_email_inputs_complete AFTER UPDATE
ON public.stewardship_report_email_preparation DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_source_commit_v1();

-- The snapshot: the exact report values, block data, cohort and fact pin of
-- its capture, with the compiled email within bounds.
CREATE FUNCTION public.stewardship_report_email_snapshot_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_report_email_preparation%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE; requests jsonb; captured jsonb;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=NEW.preparation_id;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=p.revision_id;
    SELECT jsonb_agg(jsonb_build_array(item->0,item->1,item->2,(item->>3)::timestamptz,item->4)
        ORDER BY position) INTO captured
        FROM jsonb_array_elements(NEW.blocks) b
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(b->'data'->'items','[]'::jsonb))
            WITH ORDINALITY entry(item,position)
        WHERE b->>'block'='additional_information';
    -- The open requests are read only for a report with their block.
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(v.values->'blocks') b
        WHERE b->>'block'='additional_information') THEN
        SELECT jsonb_agg(jsonb_build_array(item->0,item->1,item->2,(item->>3)::timestamptz,item->4)
            ORDER BY position) INTO requests
            FROM jsonb_array_elements(public.stewardship_report_email_requests_v1(
                p.campaign_id,p.source_id,p.as_of)) WITH ORDINALITY entry(item,position);
    END IF;
    IF p.phase IS DISTINCT FROM 'capture' OR NEW.campaign_id<>p.campaign_id
      OR NEW.actor_id IS DISTINCT FROM NEW.worker_id
      OR NOT public.stewardship_report_email_live_v1(p.id,NEW.run_id,NEW.fence,NEW.worker_id)
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_system_configuration
          WHERE active_configuration_id=NEW.configuration_id)
      OR NEW.values IS DISTINCT FROM v.values
      OR jsonb_typeof(NEW.blocks) IS DISTINCT FROM 'array'
      OR (SELECT jsonb_agg(jsonb_build_object('block',b->'block','version',b->'version')
              ORDER BY position) FROM jsonb_array_elements(NEW.blocks) WITH ORDINALITY x(b,position))
         IS DISTINCT FROM
         (SELECT jsonb_agg(jsonb_build_object('block',b->'block','version',b->'version')
              ORDER BY position) FROM jsonb_array_elements(v.values->'blocks') WITH ORDINALITY x(b,position))
      OR (EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.blocks) b
              WHERE b->>'block'='additional_information' AND b->'data'<>'null'::jsonb)
          AND captured IS DISTINCT FROM requests)
      OR (EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.blocks) b
              WHERE b->>'block'='additional_information' AND b->'data'='null'::jsonb)
          AND requests IS NOT NULL)
      OR NEW.has_content IS DISTINCT FROM EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.blocks) b
          WHERE (b->>'block' NOT IN ('additional_information','compared_with_past_campaigns'))
             OR (b->>'block'='additional_information'
                 AND jsonb_array_length(coalesce(b->'data'->'items','[]'::jsonb))>0)
             OR (b->>'block'='compared_with_past_campaigns' AND b->'data'<>'null'::jsonb))
      OR NEW.recipients IS DISTINCT FROM public.stewardship_report_email_cohort_v1(v.values,NEW.configuration_id)
      OR (p.through_date IS NULL AND NEW.fact_set_id IS NOT NULL)
      OR (p.through_date IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.stewardship_daily_fact_set f
          WHERE f.id=NEW.fact_set_id AND f.state='ready'
            AND ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date)=
              ROW(p.campaign_id,'historical'::varchar,p.source_id,p.submission_watermark,
                p.timezone_configuration_id,p.through_date)))
      OR length(NEW.subject)=0 OR NEW.subject ~ '[\r\n]' OR length(NEW.subject)>247
      OR octet_length(NEW.html) NOT BETWEEN 1 AND 8388608
      OR octet_length(NEW.text) NOT BETWEEN 1 AND 8388608
      OR jsonb_typeof(NEW.images) IS DISTINCT FROM 'array'
      OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.images) image
          WHERE jsonb_typeof(image)<>'array' OR jsonb_array_length(image)<>2
            OR image->>0 !~ '^[A-Za-z0-9._-]{1,64}$'
            OR substring(decode(image->>1,'base64') FROM 1 FOR 8)<>decode('89504e470d0a1a0a','hex')
            OR octet_length(decode(image->>1,'base64'))>1048576)
    THEN RAISE EXCEPTION 'Report snapshot lacks its exact capture' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER report_email_snapshot_insert BEFORE INSERT ON public.stewardship_report_email_snapshot
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_snapshot_guard_v1();

-- The snapshot commits with its fact pin and the phase that follows it.
CREATE FUNCTION public.stewardship_report_email_snapshot_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.fact_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.stewardship_fact_pin
        WHERE fact_set_id=NEW.fact_set_id AND parent_kind='digest' AND parent_id=NEW.preparation_id) THEN
        RAISE EXCEPTION 'Report snapshot requires retained fact protection' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation
        WHERE id=NEW.preparation_id AND phase IN ('fanout','complete')) THEN
        RAISE EXCEPTION 'Report snapshot and its next phase must commit together' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER report_email_snapshot_complete AFTER INSERT
ON public.stewardship_report_email_snapshot DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_snapshot_commit_v1();

-- One recipient: a cohort member as captured, with its own exact message
-- when it is still a recipient, or revoked when the recheck refuses it.
CREATE FUNCTION public.stewardship_report_email_recipient_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE s public.stewardship_report_email_snapshot%ROWTYPE;
        p public.stewardship_report_email_preparation%ROWTYPE; t public.stewardship_task_run%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO s FROM public.stewardship_report_email_snapshot WHERE id=NEW.snapshot_id;
    SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=s.preparation_id;
    SELECT run.* INTO t FROM public.stewardship_task_run run
        JOIN public.stewardship_task_event e ON e.run_id=run.id AND e.action='claim'
            AND e.fence=run.fence AND e.worker_id=run.worker_id AND e.state='running'
        WHERE run.root_id=p.task_id AND e.id=NEW.correlation_id AND run.worker_id=NEW.actor_id;
    IF s.id IS NULL OR p.phase IS DISTINCT FROM 'fanout' OR t.id IS NULL
      OR NOT public.stewardship_report_email_live_v1(p.id,t.id,t.fence,t.worker_id)
      OR NOT s.recipients @> jsonb_build_array(jsonb_build_object(
          'address',NEW.address,'kind',NEW.kind,'ref',NEW.ref))
      OR (NEW.disposition='revoked' AND public.stewardship_report_email_current_v1(
          p.definition_id,NEW.kind,NEW.ref,NEW.address))
      OR (NEW.disposition='queued' AND NOT (public.stewardship_report_email_current_v1(
          p.definition_id,NEW.kind,NEW.ref,NEW.address) AND EXISTS(
          SELECT 1 FROM public.stewardship_outbox_message m
          JOIN public.stewardship_outbox_render content ON content.id=m.render_id
          WHERE m.id=NEW.outbox_id AND m.correlation_id=NEW.correlation_id
            AND m.actor_id=NEW.actor_id AND m.semantic_key=NEW.id
            AND m.scope_id=p.campaign_id AND m.campaign_id=p.campaign_id AND m.family_id IS NULL
            AND m.purpose='admin_report' AND m.mode=p.mode AND m.credential_namespace='none'
            AND m.state='pending'
            AND public.stewardship_report_email_render_v1(to_jsonb(content),s.id,NEW.address,NEW.kind,NEW.ref))))
    THEN RAISE EXCEPTION 'Report recipient lacks its exact cohort message' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER report_email_recipient_insert BEFORE INSERT ON public.stewardship_report_email_recipient
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_recipient_guard_v1();

-- One recipient's render: the snapshot's email to that one address, routed
-- to the Testing recipient in Testing under the redirect's first line.
CREATE FUNCTION public.stewardship_report_email_render_v1(
    proposed jsonb, snapshot uuid, address text, kind text, ref text
) RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_snapshot s
        JOIN public.stewardship_report_email_preparation p ON p.id=s.preparation_id
        CROSS JOIN public.stewardship_system_configuration runtime
        JOIN public.stewardship_applied_integration email
            ON email.configuration_id=runtime.active_configuration_id AND email.kind='email'
        WHERE s.id=$2
          AND (proposed->>'configuration_id')::uuid=runtime.active_configuration_id
          AND proposed->>'template_id' IS NULL
          AND proposed->>'sender'=email.settings->>'sender'
          AND proposed->>'reply_to'=email.settings->>'reply_to'
          AND proposed->'intended_recipients'=jsonb_build_array($3)
          AND CASE p.mode WHEN 'testing' THEN
              proposed->'routed_recipients'=jsonb_build_array(runtime.testing_recipient)
              AND proposed->>'subject'='[TEST] '||s.subject
              AND starts_with(proposed->>'html','<h2>TEST</h2><p>')
              AND right(proposed->>'html',length(s.html))=s.html
              AND proposed->>'text'='TEST — sent to '||runtime.testing_recipient||' instead of '
                  ||public.stewardship_report_email_label_v1($4,$5,$3)||' '||$3||'.'||E'\n\n'||s.text
          ELSE proposed->'routed_recipients'=jsonb_build_array($3)
              AND proposed->>'subject'=s.subject
              AND proposed->>'html'=s.html AND proposed->>'text'=s.text END)
$$;

-- The writes a report preparation's live claim may make outside its own
-- tables, under the digest write lock: its due occurrences (dates), its
-- recovery occurrence and coalesced predecessors (cover), and one pending
-- outbox message per queued recipient (fanout). The shared occurrence,
-- transition and outbox guards call it once the conversion admits reports.
CREATE FUNCTION public.stewardship_report_email_write_admitted_v1(
    relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_report_email_preparation%ROWTYPE;
        o public.stewardship_schedule_occurrence%ROWTYPE;
        selected public.stewardship_schedule_occurrence%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_schedule_occurrence','stewardship_occurrence_transition',
        'stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
    THEN RETURN false; END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    IF relation_name='stewardship_occurrence_transition' THEN
        SELECT * INTO o FROM public.stewardship_schedule_occurrence WHERE id=(proposed->>'occurrence_id')::uuid;
        RETURN prior IS NULL AND o.id IS NOT NULL
          AND (proposed->>'version')::bigint=o.version AND proposed->>'after_state'=o.state
          AND (proposed->>'actor_id')::uuid=o.actor_id
          AND (proposed->>'correlation_id')::uuid=o.correlation_id
          AND public.stewardship_report_email_write_admitted_v1('stewardship_schedule_occurrence',to_jsonb(o),
              CASE WHEN o.state='pending' THEN NULL ELSE to_jsonb(o)||jsonb_build_object('state','pending') END);
    END IF;
    SELECT preparation.* INTO p FROM public.stewardship_report_email_preparation preparation
      JOIN public.stewardship_task_run t ON t.root_id=preparation.task_id
      JOIN public.stewardship_task_event e ON e.run_id=t.id
      WHERE e.id=(proposed->>'correlation_id')::uuid AND e.action='claim' AND e.state='running'
        AND e.worker_id=(proposed->>'actor_id')::uuid AND e.fence=t.fence
        AND public.stewardship_report_email_live_v1(preparation.id,t.id,e.fence,e.worker_id);
    IF p.id IS NULL THEN RETURN false; END IF;
    IF relation_name IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event') THEN
        RETURN public.stewardship_report_email_mail_write_v1(p.id,relation_name,proposed,prior);
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
                    FROM public.stewardship_schedule_occurrence candidate WHERE candidate.id IN (
                        SELECT public.stewardship_report_email_predecessors_v1(p.definition_id,p.mode,p.rehearsal_epoch_id)))
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence candidate
                    WHERE candidate.revision_id=p.revision_id AND candidate.mode=p.mode AND candidate.target='admins'
                      AND candidate.slot='recovery:'||p.id::text
                      AND candidate.id<>(proposed->>'id')::uuid);
        END IF;
        RETURN p.phase='dates' AND proposed->>'state'='pending'
          AND public.stewardship_report_due_v1(p.definition_id,proposed->>'slot') IS NOT NULL
          AND (proposed->>'due_at')::timestamptz=public.stewardship_report_due_v1(p.definition_id,proposed->>'slot');
    END IF;
    IF p.phase<>'cover' OR prior->>'state'<>'pending' OR proposed->>'state'<>'coalesced'
      OR proposed->>'reason'<>'missed_report_recovery'
      OR prior->>'task_id' IS NOT NULL OR prior->>'outbox_id' IS NOT NULL
      OR prior->>'slot' ~ '^manual:'
      OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=(prior->>'id')::uuid)
    THEN RETURN false; END IF;
    SELECT * INTO selected FROM public.stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    RETURN selected.id IS NOT NULL AND selected.state='pending'
      AND selected.definition_id=p.definition_id AND selected.revision_id=p.revision_id
      AND selected.mode=p.mode AND selected.target='admins'
      AND selected.task_id IS NULL AND selected.outbox_id IS NULL
      AND selected.due_at BETWEEN (proposed->>'due_at')::timestamptz AND p.cutoff
      AND NOT public.stewardship_schedule_slot_excluded_v1(selected.definition_id,selected.mode,selected.target,selected.slot)
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation other
          WHERE other.id<>p.id AND other.occurrence_id=selected.id);
END $$;

-- Fanout's outbox allocation only, never a later provider state change: a
-- pending admin_report message, its creation event and its render, whose
-- content is the snapshot's to one cohort address.
CREATE FUNCTION public.stewardship_report_email_mail_write_v1(
    preparation uuid,relation_name text,proposed jsonb,prior jsonb
) RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_report_email_preparation%ROWTYPE;
        s public.stewardship_report_email_snapshot%ROWTYPE;
        m public.stewardship_outbox_message%ROWTYPE; member jsonb;
BEGIN
    SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=preparation;
    SELECT * INTO s FROM public.stewardship_report_email_snapshot WHERE preparation_id=p.id;
    IF prior IS NOT NULL OR p.phase<>'fanout' OR s.id IS NULL THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_message' THEN
        RETURN proposed->>'purpose'='admin_report' AND proposed->>'mode'=p.mode
            AND (proposed->>'scope_id')::uuid=p.campaign_id
            AND (proposed->>'campaign_id')::uuid=p.campaign_id
            AND proposed->>'family_id' IS NULL AND proposed->>'credential_namespace'='none'
            AND proposed->>'state'='pending' AND proposed->>'action'='created'
            AND (proposed->>'version')::bigint=1;
    END IF;
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=(proposed->>'message_id')::uuid;
    IF m.id IS NULL OR m.purpose<>'admin_report' OR m.campaign_id<>p.campaign_id
        OR m.mode<>p.mode OR m.state<>'pending' OR m.version<>1
        OR m.correlation_id IS DISTINCT FROM (proposed->>'correlation_id')::uuid
        OR m.actor_id IS DISTINCT FROM (proposed->>'actor_id')::uuid THEN RETURN false; END IF;
    IF relation_name='stewardship_outbox_event' THEN
        RETURN proposed->>'action'='created' AND proposed->>'state'='pending'
            AND (proposed->>'version')::bigint=1;
    END IF;
    IF relation_name<>'stewardship_outbox_render' OR (proposed->>'id')::uuid<>m.render_id
       OR jsonb_array_length(proposed->'intended_recipients')<>1 THEN RETURN false; END IF;
    SELECT x INTO member FROM jsonb_array_elements(s.recipients) x
        WHERE x->>'address'=proposed->'intended_recipients'->>0;
    RETURN member IS NOT NULL AND public.stewardship_report_email_render_v1(
        proposed,s.id,member->>'address',member->>'kind',member->>'ref');
END $$;

-- A report message is created only with its recipient row, in one commit.
CREATE FUNCTION public.stewardship_report_email_mail_commit_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.purpose='admin_report' AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_report_email_recipient
        WHERE id=NEW.semantic_key AND outbox_id=NEW.id
          AND actor_id=NEW.actor_id AND correlation_id=NEW.correlation_id) THEN
        RAISE EXCEPTION 'Report message requires atomic recipient ownership' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_report_email_mail_commit_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER report_email_mail_complete AFTER INSERT ON public.stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.purpose='admin_report')
EXECUTE FUNCTION public.stewardship_report_email_mail_commit_v1();

-- Completion is derived from the whole frozen cohort (as a digest's is): a
-- send with nothing to report, and one whose recipients were all revoked or
-- settled without delivery, is "empty"; any delivered message makes it
-- "delivered". A failed message to an address that is no longer a
-- recipient can never be retried, so it settles like a revocation.
CREATE VIEW public.stewardship_report_email_completion_ready AS
    SELECT p.id AS preparation_id,
        CASE WHEN public.stewardship_report_email_skipped_v1(s.id)
                THEN 'nothing_to_report'
            WHEN EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
                JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE r.snapshot_id=s.id AND m.state='delivered')
                THEN 'delivered'
            ELSE 'empty' END AS disposition
    FROM public.stewardship_report_email_preparation p
    JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
    WHERE p.phase='complete' AND (
        public.stewardship_report_email_skipped_v1(s.id)
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.recipients) member
            WHERE NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
                LEFT JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE r.snapshot_id=s.id AND r.address=member->>'address'
                  AND (r.disposition='revoked' OR m.state='delivered'
                      OR (m.state='cancelled' AND m.reason='recipient_revoked')
                      OR (m.state='permanent_failure' AND NOT public.stewardship_report_email_current_v1(
                          p.definition_id,r.kind,r.ref,r.address))))));
REVOKE ALL ON public.stewardship_report_email_completion_ready FROM PUBLIC;

CREATE FUNCTION public.stewardship_report_email_reason_v1(disposition text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE $1 WHEN 'delivered' THEN 'report_email_complete'
        WHEN 'nothing_to_report' THEN 'report_email_nothing_to_report'
        ELSE 'report_email_no_current_recipients' END
$$;

-- The completion proof of an occurrence's running/succeeded transition, by
-- the preparation's own task, the metadata finalizer or the delivery that
-- settled the last recipient (the occurrence guard calls it once the
-- conversion admits reports).
CREATE FUNCTION public.stewardship_report_email_completion_v1(proposed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN public.stewardship_report_email_completion_ready proof ON proof.preparation_id=p.id
        JOIN public.stewardship_task_run t ON t.id=(proposed->>'task_id')::uuid
        WHERE o.id=(proposed->>'id')::uuid AND o.state IN ('pending','running')
          AND proposed->>'state' IN ('running','succeeded')
          AND proposed->>'reason'=public.stewardship_report_email_reason_v1(proof.disposition)
          AND (proposed->>'definition_id')::uuid=p.definition_id
          AND (proposed->>'revision_id')::uuid=p.revision_id AND proposed->>'mode'=p.mode
          AND proposed->>'target'='admins' AND proposed->>'outbox_id' IS NULL
          AND (proposed->>'actor_id')::uuid=t.worker_id AND (proposed->>'worker_id')::uuid=t.worker_id
          AND (proposed->>'correlation_id')::uuid=t.id AND (proposed->>'fence')::bigint=t.fence
          AND public.stewardship_fact_live(t.id,t.fence,t.worker_id)
          AND ((t.task_type='report_email_prepare' AND t.root_id=p.task_id AND t.domain_request_id=p.id)
            OR (t.task_type='report_email_finalize' AND t.domain_request_id=p.id AND EXISTS(
                SELECT 1 FROM public.stewardship_task_run root WHERE root.id=t.root_id
                  AND root.task_type=t.task_type AND root.domain_request_id=p.id
                  AND root.idempotency_key=p.id::text))
            OR (t.task_type='outbox_delivery' AND EXISTS(
                SELECT 1 FROM public.stewardship_outbox_message m
                JOIN public.stewardship_report_email_recipient r ON r.outbox_id=m.id
                JOIN public.stewardship_report_email_snapshot s ON s.id=r.snapshot_id
                WHERE m.id=t.domain_request_id AND m.task_id=t.root_id AND s.preparation_id=p.id
                  AND ((m.state='delivered' AND m.run_id=t.id AND m.task_fence=t.fence AND m.worker_id=t.worker_id)
                    OR (m.state='cancelled' AND m.reason='recipient_revoked'
                      AND m.correlation_id=t.id AND m.actor_id=t.worker_id))))))
$$;

-- Records the occurrence's running and succeeded history and its semantic
-- fulfillment in the commit that settles the cohort, as the digests do.
CREATE FUNCTION public.stewardship_report_email_settle_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE p public.stewardship_report_email_preparation%ROWTYPE;
    occurrence public.stewardship_schedule_occurrence%ROWTYPE;
    task public.stewardship_task_run%ROWTYPE;
    proposed jsonb; outcome text; v_reason text;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_TABLE_NAME='stewardship_report_email_preparation' THEN
        SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=NEW.id;
        SELECT * INTO task FROM public.stewardship_task_run WHERE id=p.run_id;
    ELSIF TG_TABLE_NAME='stewardship_task_run' THEN
        SELECT * INTO p FROM public.stewardship_report_email_preparation WHERE id=NEW.domain_request_id;
        SELECT * INTO task FROM public.stewardship_task_run WHERE id=NEW.id;
    ELSE
        SELECT preparation.* INTO p FROM public.stewardship_report_email_recipient r
            JOIN public.stewardship_report_email_snapshot s ON s.id=r.snapshot_id
            JOIN public.stewardship_report_email_preparation preparation ON preparation.id=s.preparation_id
            WHERE r.outbox_id=NEW.id;
        SELECT * INTO task FROM public.stewardship_task_run WHERE id=CASE
            WHEN NEW.state='cancelled' THEN NEW.correlation_id ELSE NEW.run_id END;
    END IF;
    SELECT disposition INTO outcome FROM public.stewardship_report_email_completion_ready
        WHERE preparation_id=p.id;
    IF p.id IS NULL OR outcome IS NULL THEN RETURN NULL; END IF;
    v_reason:=public.stewardship_report_email_reason_v1(outcome);
    SELECT * INTO occurrence FROM public.stewardship_schedule_occurrence WHERE id=p.occurrence_id FOR UPDATE;
    IF occurrence.state<>'pending' THEN RETURN NULL; END IF;
    proposed:=to_jsonb(occurrence)||jsonb_build_object('state','running','reason',v_reason,
        'task_id',task.id,'worker_id',task.worker_id,'actor_id',task.worker_id,
        'correlation_id',task.id,'fence',task.fence);
    IF NOT public.stewardship_report_email_completion_v1(proposed) THEN
        -- A later reconciliation acquires a fresh finalizer claim instead.
        RETURN NULL;
    END IF;
    UPDATE public.stewardship_schedule_occurrence SET state='running',reason=v_reason,
        task_id=task.id,worker_id=task.worker_id,actor_id=task.worker_id,
        correlation_id=task.id,fence=task.fence,heartbeat_at=clock_timestamp(),
        lease_expires_at=task.lease_expires_at,attempts=attempts+1,version=version+1
        WHERE id=occurrence.id;
    UPDATE public.stewardship_schedule_occurrence SET state='succeeded',lease_expires_at=NULL,version=version+1
        WHERE id=occurrence.id;
    -- A manual send fulfils no scheduled slot (spec, "Send now").
    IF occurrence.slot !~ '^manual:' THEN
        INSERT INTO public.stewardship_schedule_fulfillment(id,definition_id,mode,target,slot,
            disposition,occurrence_id,actor_id,correlation_id)
        VALUES(gen_random_uuid(),occurrence.definition_id,occurrence.mode,occurrence.target,occurrence.slot,
            CASE outcome WHEN 'delivered' THEN 'delivered' ELSE 'empty' END,
            occurrence.id,task.worker_id,task.id);
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION public.stewardship_report_email_settle_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_settle_v1() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER report_email_cohort_complete AFTER UPDATE ON public.stewardship_report_email_preparation
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.phase='complete')
EXECUTE FUNCTION public.stewardship_report_email_settle_v1();
CREATE CONSTRAINT TRIGGER report_email_late_completion AFTER UPDATE ON public.stewardship_task_run
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.task_type='report_email_finalize' AND NEW.state='running' AND NEW.action='claim')
EXECUTE FUNCTION public.stewardship_report_email_settle_v1();
CREATE CONSTRAINT TRIGGER report_email_delivery_complete AFTER UPDATE ON public.stewardship_outbox_message
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.purpose='admin_report'
    AND (NEW.state='delivered' OR (NEW.state='cancelled' AND NEW.reason='recipient_revoked')))
EXECUTE FUNCTION public.stewardship_report_email_settle_v1();

-- Delivery-side checks (mail dispatch's write gate calls them once the
-- conversion admits reports): a queued report message may be prepared or
-- submitted only while its send is complete, its occurrence open and due,
-- live delivery not paused (or this message released), its scope current,
-- its slot not excluded, and its recipient still a recipient (the recheck
-- before each submission, spec "Resolving recipients").
CREATE FUNCTION public.stewardship_report_email_dispatch_live_v1(message uuid,paused_ok boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_report_email_recipient r ON r.outbox_id=m.id AND r.id=m.semantic_key
        JOIN public.stewardship_report_email_snapshot s ON s.id=r.snapshot_id
        JOIN public.stewardship_report_email_preparation p ON p.id=s.preparation_id
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        JOIN public.stewardship_campaign c ON c.id=p.campaign_id
        WHERE m.id=$1 AND m.purpose='admin_report' AND m.credential_namespace='none' AND m.family_id IS NULL
          AND m.campaign_id=p.campaign_id AND m.mode=p.mode AND p.phase='complete'
          AND o.state IN ('pending','running') AND o.due_at<=public.stewardship_campaign_now_v1()
          AND (paused_ok OR NOT (m.mode='production' AND c.delivery_paused)
              OR public.stewardship_delivery_message_released_v1(m.id))
          AND public.stewardship_report_email_scope_v1(p.campaign_id,p.revision_id,
              p.campaign_configuration_id,p.mode,p.rehearsal_epoch_id)
          AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
          AND public.stewardship_report_email_current_v1(p.definition_id,r.kind,r.ref,r.address))
$$;

-- A retry's fixed, non-sendable seed, which mail dispatch must replace with
-- the snapshot's content under its live claim, as for the digests.
CREATE FUNCTION public.stewardship_report_email_seed_v1(message uuid,configuration uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE email jsonb; intended jsonb; routed jsonb; mode text; testing text;
    content jsonb; heading text:='Report awaiting preparation';
    body text:='PARISHKIT_PENDING_REPORT_EMAIL: Report awaiting preparation.';
BEGIN
    SELECT settings INTO email FROM public.stewardship_applied_integration
        WHERE configuration_id=configuration AND kind='email';
    SELECT testing_recipient INTO testing FROM public.stewardship_system_configuration;
    SELECT m.mode,jsonb_build_array(r.address) INTO mode,intended
        FROM public.stewardship_outbox_message m
        JOIN public.stewardship_report_email_recipient r ON r.outbox_id=m.id
        WHERE m.id=message AND m.purpose='admin_report';
    IF email IS NULL OR mode IS NULL OR jsonb_array_length(intended)<>1 THEN
        RAISE EXCEPTION 'Report retry requires exact configured routing' USING ERRCODE='23514'; END IF;
    routed:=CASE mode WHEN 'testing' THEN jsonb_build_array(testing) ELSE intended END;
    IF mode='testing' THEN
        heading:='[TEST] '||heading;
        body:='TEST — pending report is routed only to the Testing recipient. '||body;
    END IF;
    content:=jsonb_build_object('sender',email->>'sender','reply_to',email->>'reply_to',
        'intended_recipients',intended,'routed_recipients',routed,'subject',heading,
        'html',CASE mode WHEN 'testing' THEN '<h2>TEST</h2>' ELSE '' END||'<p>'||body||'</p>',
        'text',body);
    RETURN content||jsonb_build_object('configuration_id',configuration,'template_id',NULL,
        'payload_digest',encode(sha256(convert_to(content::text,'UTF8')),'hex'));
END $$;
REVOKE ALL ON FUNCTION public.stewardship_report_email_seed_v1(uuid,uuid) FROM PUBLIC;

-- Mail dispatch's writes to one report message, under its live delivery
-- claim: its render (the snapshot's exact content), its provider outcome,
-- and its cancellation when the recipient is no longer one.
CREATE FUNCTION public.stewardship_report_email_dispatch_write_v1(relation_name text,proposed jsonb,prior jsonb)
RETURNS boolean LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE task public.stewardship_task_run%ROWTYPE; message public.stewardship_outbox_message%ROWTYPE;
        r public.stewardship_report_email_recipient%ROWTYPE;
        p public.stewardship_report_email_preparation%ROWTYPE;
BEGIN
    IF relation_name NOT IN ('stewardship_outbox_message','stewardship_outbox_render','stewardship_outbox_event')
      OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN RETURN false; END IF;
    SELECT * INTO task FROM public.stewardship_task_run WHERE id=(proposed->>'correlation_id')::uuid;
    SELECT * INTO message FROM public.stewardship_outbox_message
        WHERE id=task.domain_request_id AND task_id=task.root_id AND purpose='admin_report';
    SELECT recipient.* INTO r FROM public.stewardship_report_email_recipient recipient
        WHERE recipient.outbox_id=message.id AND recipient.id=message.semantic_key;
    SELECT preparation.* INTO p FROM public.stewardship_report_email_snapshot s
        JOIN public.stewardship_report_email_preparation preparation ON preparation.id=s.preparation_id
        WHERE s.id=r.snapshot_id;
    IF task.id IS NULL OR task.task_type<>'outbox_delivery' OR message.id IS NULL OR r.id IS NULL
      OR message.campaign_id IS DISTINCT FROM p.campaign_id OR message.mode IS DISTINCT FROM p.mode
      OR (CASE WHEN relation_name='stewardship_outbox_message' THEN proposed->>'id'
            ELSE proposed->>'message_id' END)::uuid IS DISTINCT FROM message.id
    THEN RETURN false; END IF;
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
            RETURN public.stewardship_report_email_dispatch_live_v1(message.id) AND EXISTS(
                SELECT 1 FROM public.stewardship_outbox_render rendering
                WHERE rendering.id=(proposed->>'render_id')::uuid AND rendering.message_id=message.id
                  AND public.stewardship_report_email_render_v1(to_jsonb(rendering),r.snapshot_id,
                      r.address,r.kind,r.ref));
        ELSIF proposed->>'action' IN ('accept','retry_unaccepted','fail_unaccepted','mark_unknown') THEN
            RETURN prior->>'state'='submitting' AND (prior->>'run_id')::uuid=task.id
              AND (prior->>'task_fence')::bigint=task.fence AND (prior->>'worker_id')::uuid=task.worker_id
              AND proposed->>'reason' LIKE 'smtp_%';
        END IF;
        IF proposed->>'action'='cancel_unsent' AND proposed->>'reason'='recipient_revoked' THEN
            RETURN NOT public.stewardship_report_email_current_v1(p.definition_id,r.kind,r.ref,r.address);
        END IF;
        RETURN proposed->>'action' IN ('cancel_unsent','hold','release_hold');
    ELSIF relation_name='stewardship_outbox_render' THEN
        RETURN prior IS NULL AND message.state IN ('pending','retry_wait')
          AND (proposed->>'message_id')::uuid=message.id
          AND public.stewardship_report_email_dispatch_live_v1(message.id)
          AND public.stewardship_report_email_render_v1(proposed,r.snapshot_id,r.address,r.kind,r.ref);
    END IF;
    RETURN relation_name='stewardship_outbox_event' AND prior IS NULL
        AND proposed->>'action'=message.action AND (proposed->>'version')::bigint=message.version;
END $$;

-- An Administrator's Send now: web inserts only the request; its guard
-- checks the current report, scope, revision and that no other send of the
-- report is open, and its allocation adds the manual occurrence, the
-- preparation (starting at inputs) and the audit event.
CREATE FUNCTION public.stewardship_report_email_manual_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c public.stewardship_campaign%ROWTYPE; d public.stewardship_schedule_definition%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE; epoch uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Report manual request history is immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO runtime FROM public.stewardship_system_configuration;
    SELECT * INTO d FROM public.stewardship_schedule_definition
        WHERE id=NEW.definition_id AND campaign_id=c.id AND kind='report';
    IF runtime.mode='testing' THEN
        SELECT rehearsal_epoch_id INTO epoch FROM public.stewardship_campaign_credentials WHERE campaign_id=c.id;
    END IF;
    IF session_user<>'pk_stewardship_web' OR NEW.actor_id IS NULL
      OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
      OR NEW.correlation_id<>NEW.id OR c.id IS NULL OR d.id IS NULL
      OR d.current_revision_id IS DISTINCT FROM NEW.revision_id
      OR NEW.configuration_id IS DISTINCT FROM runtime.active_configuration_id
      OR NOT public.stewardship_report_email_scope_v1(c.id,d.current_revision_id,
          c.active_configuration_id,runtime.mode,epoch)
      OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
          JOIN public.stewardship_task_run t ON t.root_id=p.task_id
          WHERE p.definition_id=d.id AND p.mode=runtime.mode
            AND p.phase NOT IN ('complete','cancelled')
            AND t.state IN ('queued','running','retry_wait','abandoned'))
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.task_id AND t.root_id=t.id
          AND t.task_type='report_email_prepare' AND t.domain_request_id=NEW.id
          AND t.idempotency_key=NEW.id::text AND t.state='queued'
          AND t.initiated_by_id=NEW.actor_id AND t.correlation_id=NEW.id)
    THEN RAISE EXCEPTION 'Report manual request requires the current report and no open send'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION public.stewardship_report_email_manual_guard_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_manual_guard_v1() FROM PUBLIC;
CREATE TRIGGER report_email_manual_guard BEFORE INSERT OR UPDATE OR DELETE
ON public.stewardship_report_email_manual_request
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_manual_guard_v1();

CREATE FUNCTION public.stewardship_report_email_manual_allocate_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE c public.stewardship_campaign%ROWTYPE; runtime public.stewardship_system_configuration%ROWTYPE;
    epoch uuid; cycle bigint; instant timestamptz:=public.stewardship_campaign_now_v1();
    slot text:='manual:'||NEW.id::text;
BEGIN
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    SELECT * INTO runtime FROM public.stewardship_system_configuration;
    IF runtime.mode='testing' THEN
        SELECT rehearsal_epoch_id INTO epoch FROM public.stewardship_campaign_credentials WHERE campaign_id=c.id;
    END IF;
    cycle:=CASE WHEN runtime.mode='production' THEN c.production_cycle ELSE 0 END;
    INSERT INTO public.stewardship_schedule_occurrence(
        id,actor_id,correlation_id,version,definition_id,revision_id,mode,routing,
        target,slot,due_at,occurrence_key,state,fence,attempts,reason,pause_version,production_cycle)
    VALUES(NEW.id,NEW.actor_id,NEW.id,1,NEW.definition_id,NEW.revision_id,runtime.mode,
        CASE runtime.mode WHEN 'testing' THEN 'testing_override' ELSE 'production' END,
        'admins',slot,instant,encode(sha256(convert_to(
            '["'||NEW.revision_id::text||'","'||runtime.mode||'","admins","'||slot||'"'
            ||CASE WHEN cycle>0 THEN ',["production_cycle",'||cycle::text||']' ELSE '' END||']','UTF8')),'hex'),
        'pending',0,0,'',CASE WHEN runtime.mode='production' AND c.delivery_paused THEN c.pause_version ELSE NULL END,cycle);
    INSERT INTO public.stewardship_report_email_preparation(
        id,actor_id,correlation_id,version,campaign_id,definition_id,revision_id,
        campaign_configuration_id,task_id,mode,rehearsal_epoch_id,cutoff,phase,occurrence_id)
    VALUES(NEW.id,NEW.actor_id,NEW.id,1,c.id,NEW.definition_id,NEW.revision_id,c.active_configuration_id,
        NEW.task_id,runtime.mode,epoch,instant,'inputs',NEW.id);
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(gen_random_uuid(),NEW.actor_id,NEW.id,'report_email_manual_requested',NEW.definition_id,c.id);
    RETURN NULL;
END $$;
ALTER FUNCTION public.stewardship_report_email_manual_allocate_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_manual_allocate_v1() FROM PUBLIC;
CREATE TRIGGER report_email_manual_allocate AFTER INSERT ON public.stewardship_report_email_manual_request
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_manual_allocate_v1();

-- Report records are immutable outside owned cleanup, as the digests' are;
-- the conversion adds their cleanup categories to Testing cleanup and the
-- Production transition, so until then nothing may delete them.
CREATE FUNCTION public.stewardship_report_email_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' AND stewardship_cleanup_effect_v1(TG_ARGV[0],OLD.id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Report email records are immutable outside owned cleanup' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION public.stewardship_report_email_immutable_v1() FROM PUBLIC;
CREATE TRIGGER report_email_immutable BEFORE UPDATE OR DELETE ON public.stewardship_report_email_snapshot
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_immutable_v1('report_email_snapshots');
CREATE TRIGGER report_email_immutable BEFORE UPDATE OR DELETE ON public.stewardship_report_email_recipient
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_immutable_v1('report_email_recipients');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_report_email_snapshot
FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('report_email_snapshots');
CREATE TRIGGER production_cleanup_protect BEFORE DELETE ON public.stewardship_report_email_recipient
FOR EACH ROW EXECUTE FUNCTION public.stewardship_cleanup_protect_v1('report_email_recipients');

-- A report's source and fact pins (parent kind "digest", parent its
-- preparation) are retained while the preparation exists; only owned
-- cleanup releases them.
CREATE FUNCTION public.stewardship_report_email_pin_retained_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF OLD.parent_kind='digest' AND EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation
        WHERE id=OLD.parent_id) THEN
        IF TG_OP='DELETE' AND (
            (TG_TABLE_NAME='stewardship_source_pin'
                AND public.stewardship_cleanup_effect_v1('source_pins',OLD.id))
            OR (TG_TABLE_NAME='stewardship_fact_pin'
                AND public.stewardship_cleanup_effect_v1('report_email_fact_pins',OLD.id))) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Retained report email still requires its exact inputs' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER report_email_source_retained BEFORE UPDATE OR DELETE ON public.stewardship_source_pin
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_pin_retained_v1();
CREATE TRIGGER report_email_facts_retained BEFORE UPDATE OR DELETE ON public.stewardship_fact_pin
FOR EACH ROW EXECUTE FUNCTION public.stewardship_report_email_pin_retained_v1();

-- The two task-type maps, each its latest definition (frozen file 0120)
-- with report_email_prepare added: run by the general worker, created by
-- the scheduler's producer and by web's Send now.
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
            'report_email_prepare',
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
        WHEN 'report_email_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
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
    IF to_regclass('public.stewardship_report_email_manual_request') IS NULL
       OR to_regclass('public.stewardship_report_email_preparation') IS NULL
       OR to_regclass('public.stewardship_report_email_snapshot') IS NULL
       OR to_regclass('public.stewardship_report_email_recipient') IS NULL
       OR to_regclass('public.stewardship_report_email_completion_ready') IS NULL THEN
        RAISE EXCEPTION 'Migration 0156: a report email table or view is missing';
    END IF;
    -- Every new function, with its declared rights and fixed search path;
    -- the definers and the private trigger functions with no EXECUTE for
    -- PUBLIC.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_report_next_send_v1(jsonb,date,date,date)', false, false),
        ('stewardship_report_slot_due_v1(jsonb,date,date,text,text)', false, false),
        ('stewardship_report_due_v1(uuid,text)', false, false),
        ('stewardship_report_email_scope_v1(uuid,uuid,uuid,text,uuid)', false, false),
        ('stewardship_report_email_live_v1(uuid,uuid,bigint,uuid)', false, false),
        ('stewardship_report_email_page_v1(jsonb,jsonb)', false, false),
        ('stewardship_report_email_predecessors_v1(uuid,text,uuid)', false, false),
        ('stewardship_report_email_replacement_v1(jsonb)', false, false),
        ('stewardship_report_email_slots_v1(uuid,text)', false, false),
        ('stewardship_report_email_inputs_v1(jsonb,jsonb,timestamp with time zone,date,date,text)', false, false),
        ('stewardship_report_email_cohort_v1(jsonb,uuid)', false, false),
        ('stewardship_report_email_current_v1(uuid,text,text,text)', false, false),
        ('stewardship_report_email_label_v1(text,text,text)', false, false),
        ('stewardship_report_email_requests_v1(uuid,uuid,timestamp with time zone)', false, false),
        ('stewardship_report_email_skipped_v1(uuid)', false, false),
        ('stewardship_report_email_preparation_guard_v1()', false, false),
        ('stewardship_report_email_source_commit_v1()', false, false),
        ('stewardship_report_email_snapshot_guard_v1()', false, false),
        ('stewardship_report_email_snapshot_commit_v1()', false, false),
        ('stewardship_report_email_recipient_guard_v1()', false, false),
        ('stewardship_report_email_render_v1(jsonb,uuid,text,text,text)', false, false),
        ('stewardship_report_email_write_admitted_v1(text,jsonb,jsonb)', false, false),
        ('stewardship_report_email_mail_write_v1(uuid,text,jsonb,jsonb)', false, false),
        ('stewardship_report_email_mail_commit_v1()', false, true),
        ('stewardship_report_email_reason_v1(text)', false, false),
        ('stewardship_report_email_completion_v1(jsonb)', false, false),
        ('stewardship_report_email_settle_v1()', true, true),
        ('stewardship_report_email_dispatch_live_v1(uuid,boolean)', false, false),
        ('stewardship_report_email_seed_v1(uuid,uuid)', false, true),
        ('stewardship_report_email_dispatch_write_v1(text,jsonb,jsonb)', false, false),
        ('stewardship_report_email_manual_guard_v1()', true, true),
        ('stewardship_report_email_manual_allocate_v1()', true, true),
        ('stewardship_report_email_immutable_v1()', false, true),
        ('stewardship_report_email_pin_retained_v1()', false, false)
    ) expected(name, definer, revoked)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND p.prosecdef=expected.definer
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND (NOT expected.revoked
              OR NOT has_function_privilege('public', p.oid, 'EXECUTE')));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0156: not installed as declared: %', missing;
    END IF;
    IF has_table_privilege('public','public.stewardship_report_email_completion_ready','SELECT') THEN
        RAISE EXCEPTION 'Migration 0156: the completion proof is readable by PUBLIC';
    END IF;
    -- Each trigger is in place on its table.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_report_email_preparation', 'report_email_preparation_write'),
        ('stewardship_report_email_preparation', 'report_email_inputs_complete'),
        ('stewardship_report_email_preparation', 'report_email_cohort_complete'),
        ('stewardship_report_email_snapshot', 'report_email_snapshot_insert'),
        ('stewardship_report_email_snapshot', 'report_email_snapshot_complete'),
        ('stewardship_report_email_snapshot', 'report_email_immutable'),
        ('stewardship_report_email_snapshot', 'production_cleanup_protect'),
        ('stewardship_report_email_recipient', 'report_email_recipient_insert'),
        ('stewardship_report_email_recipient', 'report_email_immutable'),
        ('stewardship_report_email_recipient', 'production_cleanup_protect'),
        ('stewardship_report_email_manual_request', 'report_email_manual_guard'),
        ('stewardship_report_email_manual_request', 'report_email_manual_allocate'),
        ('stewardship_outbox_message', 'report_email_mail_complete'),
        ('stewardship_outbox_message', 'report_email_delivery_complete'),
        ('stewardship_task_run', 'report_email_late_completion'),
        ('stewardship_source_pin', 'report_email_source_retained'),
        ('stewardship_fact_pin', 'report_email_facts_retained')
    ) expected(relation, name)
    WHERE NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid=to_regclass('public.'||expected.relation)
          AND t.tgname=expected.name AND NOT t.tgisinternal AND t.tgenabled='O');
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0156: a report email trigger is missing: %', missing;
    END IF;
    -- The slot evaluation answers one known case: the converted Daily's
    -- first slot, the day the campaign starts, sends the next morning.
    IF public.stewardship_report_slot_due_v1(
        '{"schedule":{"type":"repeat","frequency":"daily","from":"day_after_campaign_start","until":"day_after_campaign_end","time":"07:00:00"},"slot_basis":"report_date","skip_dates":[],"paused_ranges":[]}'::jsonb,
        DATE '2026-10-05',DATE '2026-11-06','America/New_York','2026-10-05')
       IS DISTINCT FROM TIMESTAMPTZ '2026-10-06 11:00:00+00' THEN
        RAISE EXCEPTION 'Migration 0156: the report slot evaluation is not installed as declared';
    END IF;
    IF public.stewardship_task_type_login_v1('report_email_prepare') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('report_email_prepare')
           IS DISTINCT FROM ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
       OR public.stewardship_task_type_login_v1('leader_email_prepare') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('leader_email_prepare')
           IS DISTINCT FROM ARRAY['pk_stewardship_scheduler'] THEN
        RAISE EXCEPTION 'Migration 0156: the task-type maps do not carry report_email_prepare';
    END IF;
END
$check$;
