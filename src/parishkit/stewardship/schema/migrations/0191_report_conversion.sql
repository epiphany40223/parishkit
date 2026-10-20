-- Frozen forward migration file 0191 (provisional: the repository-wide file
-- sequence assigns the final number when the pull request merges): report
-- emails replace the Daily and Weekly digests (#1786, report-emails spec
-- "Migration of the Daily and Weekly digests" and "Database functions",
-- delivery plan item 4). This file is installed by its Django migration in
-- the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks each replaced baseline function's copy here against the baseline.
-- Reversing needs its own forward migration.
--
-- Order against the upgrade. The configuration document lives in the YAML
-- authority directory as well as the database, so the rows cannot be
-- converted here: the frozen file never parses YAML. Instead:
--   1. this file runs (refusing, naming the counts, while any digest send
--      is pending, running or uncertain, or any digest message unfinished);
--   2. database-grants runs, as after every migration;
--   3. the offline `pk-stewardship upgrade-reports` (admin-recovery
--      profile) builds the converted document in Python and installs it
--      through the configuration installer's journaled path, as the users
--      upgrade (#952) did; its activation retypes every digest definition
--      in place (schedules_activate below), keeping each definition's UUID;
--   4. only then do the services start: web, workers, scheduler and mail
--      dispatch refuse to start while a digest definition remains.
-- The kind checks therefore admit "report" beside the digest kinds; no
-- configuration change adds a digest (campaigns/configuration.py, and
-- campaign-series-v12 admits none), and a later migration drops the digest
-- kinds once every deployment is converted. A definition the applied
-- configuration does not select (a removed digest, or one of a campaign
-- the document no longer holds) is retyped at the end of this file.
--
-- It also:
--   admits the configuration schema campaign-series-v12 (the users form
--   with report schedule records) and the conversion's request format
--   operator-upgrade-reports-v12, in the constraints and in every
--   projection, completeness and users-form function;
--   adds the outbox purposes admin_report and admin_report_test (the digest
--   purposes stay valid for the messages sent before) and the report
--   cleanup categories;
--   replaces every shared function and view that named a digest kind,
--   purpose, table or task type so it serves report emails: the worker and
--   mail write gates, occurrence, fulfillment, recovery replacement,
--   post-close, source and fact pin guards, fact binding and disposal,
--   catch-up slot checks, receipts, shapes and checkpoints, schedule
--   activation and reconciliation helpers, work rows, live pause recovery,
--   closed-campaign coverage and resolution, delivery control, retry and
--   resolution, Testing cleanup and the Production transition, withdrawal,
--   token activation, the render bound and the task-type maps (which gain
--   report_email_finalize and lose the digest task types). The digest
--   pipelines' own write functions are no longer called by any gate, and no
--   digest definition remains to admit, so they refuse every write; their
--   read functions and the digest tables stay for the sends made before.
--
-- Each replaced body is its latest definition (the baseline's, or the
-- frozen file that created it) with only the change described; the three
-- definers the baseline alters are altered again. The file ends with a DO
-- block that refuses to commit unless all of it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Never convert a send mid-flight (spec, "Database functions"). The
-- migrations before this one stay committed when it refuses, so the
-- operator restores the pre-upgrade backup with the previous release, lets
-- the send finish and upgrades again; the precheck before the window avoids
-- this (operations spec, "Report email conversion").
DO $preflight$
BEGIN
    IF EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
                WHERE d.kind IN ('daily_digest','weekly_digest')
                  AND o.state IN ('pending','running','delivery_unknown'))
           OR EXISTS(SELECT 1 FROM public.stewardship_outbox_message
                WHERE purpose IN ('daily_digest','weekly_digest')
                  AND state NOT IN ('delivered','permanent_failure','cancelled'))
           OR EXISTS(SELECT 1 FROM public.stewardship_task_run
                WHERE task_type IN ('daily_digest_prepare','daily_digest_finalize',
                    'weekly_digest_prepare','weekly_digest_finalize')
                  AND state IN ('queued','running','retry_wait','abandoned')) THEN
            RAISE EXCEPTION 'Migration 0191: a Daily or Weekly digest send is still in progress (% pending, running or uncertain sends; % unfinished messages; % open tasks); see the operations specification, Report email conversion',
                (SELECT count(*) FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
                    WHERE d.kind IN ('daily_digest','weekly_digest')
                      AND o.state IN ('pending','running','delivery_unknown')),
                (SELECT count(*) FROM public.stewardship_outbox_message
                    WHERE purpose IN ('daily_digest','weekly_digest')
                      AND state NOT IN ('delivered','permanent_failure','cancelled')),
                (SELECT count(*) FROM public.stewardship_task_run
                    WHERE task_type IN ('daily_digest_prepare','daily_digest_finalize',
                        'weekly_digest_prepare','weekly_digest_finalize')
                      AND state IN ('queued','running','retry_wait','abandoned'));
    END IF;
END
$preflight$;

-- Constraints: report admitted beside the digest kinds (the offline conversion
-- retypes the rows afterwards), the new configuration schema and conversion
-- request, the report outbox purposes and the report cleanup categories.
ALTER TABLE "stewardship_schedule_definition" DROP CONSTRAINT "schedule_definition_kind";
ALTER TABLE "stewardship_schedule_definition" ADD CONSTRAINT "schedule_definition_kind" CHECK (((kind)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'daily_digest'::character varying, 'weekly_digest'::character varying, 'report'::character varying])::text[])));
ALTER TABLE "stewardship_schedule_revision" DROP CONSTRAINT "schedule_revision_due_kind";
ALTER TABLE "stewardship_schedule_revision" ADD CONSTRAINT "schedule_revision_due_kind" CHECK ((((due_at IS NOT NULL) AND ((kind)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying])::text[]))) OR ((due_at IS NULL) AND ((kind)::text = ANY ((ARRAY['daily_digest'::character varying, 'weekly_digest'::character varying, 'report'::character varying])::text[])))));
ALTER TABLE "stewardship_configuration_version" DROP CONSTRAINT "configuration_validation_schema";
ALTER TABLE "stewardship_configuration_version" ADD CONSTRAINT "configuration_validation_schema" CHECK (((validation_schema)::text = ANY ((ARRAY['parish-integrations-v1'::character varying, 'foundation-policy-v2'::character varying, 'campaign-foundation-v3'::character varying, 'bootstrap-policy-v1'::character varying, 'ministry-activity-v4'::character varying, 'campaign-content-v5'::character varying, 'source-cadence-v8'::character varying, 'users-v10'::character varying, 'bootstrap-users-v10'::character varying, 'campaign-series-v11'::character varying, 'campaign-series-v12'::character varying])::text[])));
ALTER TABLE "stewardship_config_request" DROP CONSTRAINT "config_request_schema";
ALTER TABLE "stewardship_config_request" ADD CONSTRAINT "config_request_schema" CHECK (((request_schema)::text = ANY ((ARRAY['parish-integrations-patch-v1'::character varying, 'foundation-policy-patch-v2'::character varying, 'operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'campaign-foundation-patch-v3'::character varying, 'ministry-activity-patch-v4'::character varying, 'operator-recovery-ministry-v4'::character varying, 'campaign-content-patch-v5'::character varying, 'operator-recovery-content-v5'::character varying, 'integration-credential-patch-v6'::character varying, 'initial-setup-patch-v7'::character varying, 'source-cadence-patch-v8'::character varying, 'operator-recovery-cadence-v8'::character varying, 'integration-credential-cadence-v8'::character varying, 'chair-seed-patch-v9'::character varying, 'operator-upgrade-users-v10'::character varying, 'users-patch-v10'::character varying, 'integration-credential-users-v10'::character varying, 'operator-recovery-users-v10'::character varying, 'initial-setup-users-v10'::character varying, 'operator-upgrade-reports-v12'::character varying])::text[])));
ALTER TABLE "stewardship_config_request" DROP CONSTRAINT "config_request_actor";
ALTER TABLE "stewardship_config_request" ADD CONSTRAINT "config_request_actor" CHECK ((((actor_id IS NOT NULL) AND ((authority)::text = 'admin'::text) AND (confirmed_deployment_id IS NULL) AND (operator_name IS NULL) AND (operator_reason IS NULL) AND (recovery_target IS NULL) AND (NOT ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying, 'operator-recovery-users-v10'::character varying, 'operator-upgrade-users-v10'::character varying, 'operator-upgrade-reports-v12'::character varying])::text[])))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_recovery'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NOT NULL) AND ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying, 'operator-recovery-users-v10'::character varying])::text[]))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_upgrade'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NULL) AND ((request_schema)::text = ANY ((ARRAY['operator-upgrade-users-v10'::character varying, 'operator-upgrade-reports-v12'::character varying])::text[])))));
ALTER TABLE "stewardship_production_target" DROP CONSTRAINT "production_target_category";
ALTER TABLE "stewardship_production_target" ADD CONSTRAINT production_target_category CHECK (category::text = ANY (ARRAY[
        ('baselines'::varchar)::text,('session_data'::varchar)::text,('family_sessions'::varchar)::text,
        ('ministry_requests'::varchar)::text,('occurrences'::varchar)::text,('occurrence_events'::varchar)::text,
        ('outbox_events'::varchar)::text,('outbox_messages'::varchar)::text,('outbox_renders'::varchar)::text,
        ('proposals'::varchar)::text,('rehearsal_credentials'::varchar)::text,('rehearsal_macs'::varchar)::text,
        ('schedule_fulfillments'::varchar)::text,('source_pins'::varchar)::text,
        ('submission_receipts'::varchar)::text,('submissions'::varchar)::text,
        ('prior_inventory_targets'::varchar)::text,
        ('daily_digest_recipients'::varchar)::text,('daily_digest_ready'::varchar)::text,
        ('daily_digest_snapshots'::varchar)::text,('daily_digest_fact_pins'::varchar)::text,
        ('recovery_replacements'::varchar)::text,
        ('weekly_digest_recipients'::varchar)::text,('weekly_digest_snapshots'::varchar)::text,
        ('engagement'::varchar)::text,
        ('report_email_recipients'::varchar)::text,('report_email_snapshots'::varchar)::text,
        ('report_email_fact_pins'::varchar)::text
    ]));
ALTER TABLE "stewardship_outbox_message" DROP CONSTRAINT "outbox_purpose";
ALTER TABLE "stewardship_outbox_message" ADD CONSTRAINT "outbox_purpose" CHECK (((purpose)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'receipt'::character varying, 'family_test'::character varying, 'daily_digest'::character varying, 'weekly_digest'::character varying, 'operational'::character varying, 'security_event'::character varying, 'leader_email'::character varying, 'admin_report'::character varying, 'admin_report_test'::character varying])::text[])));
ALTER TABLE "stewardship_outbox_message" DROP CONSTRAINT "outbox_family_purpose";
ALTER TABLE "stewardship_outbox_message" ADD CONSTRAINT "outbox_family_purpose" CHECK ((((family_id IS NOT NULL) AND ((purpose)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'receipt'::character varying, 'family_test'::character varying])::text[]))) OR ((family_id IS NULL) AND ((purpose)::text = ANY ((ARRAY['daily_digest'::character varying, 'weekly_digest'::character varying, 'operational'::character varying, 'security_event'::character varying, 'leader_email'::character varying, 'admin_report'::character varying, 'admin_report_test'::character varying])::text[])))));

-- What still holds the conversion back (#1786): the digest sends in
-- progress or uncertain, the digest messages not finished, and the digest
-- tasks still open; NULL when there are none. Counts only.
CREATE FUNCTION public.stewardship_digest_work_open_v1() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE WHEN occurrences+messages+tasks>0 THEN format(
        '%s digest sends pending, running or uncertain; %s digest messages unfinished; %s digest tasks open',
        occurrences,messages,tasks) END
    FROM (SELECT
        (SELECT count(*) FROM public.stewardship_schedule_occurrence o
            JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
            JOIN public.stewardship_schedule_revision v ON v.id=o.revision_id
            WHERE (d.kind IN ('daily_digest','weekly_digest') OR v.kind IN ('daily_digest','weekly_digest'))
              AND o.state IN ('pending','running','delivery_unknown')) AS occurrences,
        (SELECT count(*) FROM public.stewardship_outbox_message
            WHERE purpose IN ('daily_digest','weekly_digest')
              AND state NOT IN ('delivered','permanent_failure','cancelled')) AS messages,
        (SELECT count(*) FROM public.stewardship_task_run
            WHERE task_type IN ('daily_digest_prepare','daily_digest_finalize',
                'weekly_digest_prepare','weekly_digest_finalize')
              AND state IN ('queued','running','retry_wait','abandoned')) AS tasks) counts
$$;
-- Only the logins that activate a configuration call it (runtime_grants).
REVOKE ALL ON FUNCTION public.stewardship_digest_work_open_v1() FROM PUBLIC;

-- Every send of one report email due by "through", with its slot key, as
-- schedule_evaluation.ReportPlan gives them (rule, slot basis, skip dates,
-- pauses and missing days): live pause recovery's inventory of a report's
-- original sends. A report sends at most once a day, from its rule's first
-- candidate until "through".
CREATE FUNCTION public.stewardship_report_slots_v1(definition uuid, through timestamptz)
RETURNS TABLE(slot text, due_at timestamptz) LANGUAGE sql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT day::date::text,due.value
    FROM public.stewardship_schedule_definition d
    JOIN public.stewardship_schedule_revision v ON v.id=d.current_revision_id
    JOIN public.stewardship_campaign c ON c.id=d.campaign_id
    JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    CROSS JOIN LATERAL (SELECT public.stewardship_report_next_send_v1(v.values,p.start_date,p.end_date,NULL)
        -CASE WHEN v.values->>'slot_basis'='report_date' THEN 1 ELSE 0 END AS first) bounds
    CROSS JOIN LATERAL generate_series(bounds.first::timestamp,
        ((through AT TIME ZONE public.stewardship_timezone_name_v1(p.timezone))::date+1)::timestamp,
        interval '1 day') day
    CROSS JOIN LATERAL (SELECT public.stewardship_report_slot_due_v1(v.values,p.start_date,
        p.end_date,p.timezone,day::date::text) AS value) due
    WHERE d.id=$1 AND d.kind='report' AND bounds.first IS NOT NULL
      AND due.value IS NOT NULL AND due.value<=through
$$;

-- The replaced functions, each its latest definition with its change.

CREATE OR REPLACE FUNCTION public.stewardship_campaign_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.validation_schema NOT IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11', 'campaign-series-v12') THEN RETURN NULL; END IF;
    IF (SELECT count(*) FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'campaigns'), 0)
       OR (SELECT count(*) FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'schedules'), 0) THEN
        RAISE EXCEPTION 'Campaign projections are incomplete' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT campaign_id FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind IN ('initial', 'reminder')
        GROUP BY campaign_id
        HAVING count(*) FILTER (WHERE kind = 'initial') <> 1
           OR count(DISTINCT due_at) <> count(*)
           OR min(due_at) FILTER (WHERE kind = 'reminder') <=
              min(due_at) FILTER (WHERE kind = 'initial')
    ) OR EXISTS (
        SELECT campaign_id, kind FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind IN ('daily_digest', 'weekly_digest')
        GROUP BY campaign_id, kind HAVING count(*) > 1
    ) OR EXISTS (
        -- At most 20 report emails per campaign (#1786, decision 10).
        SELECT campaign_id FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id AND kind = 'report'
        GROUP BY campaign_id HAVING count(*) > 20
    ) THEN
        RAISE EXCEPTION 'Invalid schedule relationships' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_campaign_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; section text; owner_zone text; expected_due timestamptz;
    owner_start timestamptz; owner_end timestamptz;
BEGIN
    section := CASE WHEN TG_TABLE_NAME = 'stewardship_campaign_configuration'
                    THEN 'campaigns' ELSE 'schedules' END;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version v,
        jsonb_array_elements(v.canonical_document->'sections'->section) item
    WHERE v.id = NEW.configuration_id AND item->>'id' = NEW.record_id::text
      AND v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11', 'campaign-series-v12');
    IF expected IS NULL OR expected IS DISTINCT FROM NEW.values THEN
        RAISE EXCEPTION 'Campaign projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    IF section = 'campaigns' THEN
        IF NEW.name IS DISTINCT FROM expected->>'name'
           OR NEW.timezone IS DISTINCT FROM expected->>'timezone'
           OR NEW.start_date IS DISTINCT FROM (expected->>'start_date')::date
           OR NEW.end_date IS DISTINCT FROM (expected->>'end_date')::date
           OR jsonb_typeof(expected->'modules') IS DISTINCT FROM 'array'
           OR coalesce(jsonb_array_length(expected->'modules'), 0) < 1
           OR expected->'modules' IS DISTINCT FROM (
               SELECT jsonb_agg(value ORDER BY value COLLATE "C")
               FROM (SELECT DISTINCT value
                     FROM jsonb_array_elements_text(expected->'modules')) canonical
           )
           OR NOT expected->'modules' <@ '["census", "ministry", "financial"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid indexed campaign projection' USING ERRCODE = '23514';
        END IF;
        IF NEW.starts_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                NEW.start_date::timestamp, NEW.timezone)
           OR NEW.ends_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                (NEW.end_date + 1)::timestamp, NEW.timezone) THEN
            RAISE EXCEPTION 'Invalid resolved campaign boundary' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb AND (
            (expected->'financial'->>'end')::date IS DISTINCT FROM
                ((expected->'financial'->>'start')::date + interval '1 year -1 day')::date
            OR (expected->'financial'->>'comparison_end')::date IS DISTINCT FROM
                ((expected->'financial'->>'comparison_start')::date + interval '1 year -1 day')::date
        ) THEN
            RAISE EXCEPTION 'Invalid financial period' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb
           AND (expected->'financial'->>'start')::date <= NEW.end_date
           AND (expected->'financial'->>'end')::date >= NEW.start_date
           AND expected->'financial'->'overlap_confirmed' IS DISTINCT FROM 'true'::jsonb THEN
            RAISE EXCEPTION 'Financial overlap requires confirmation' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.campaign_id::text IS DISTINCT FROM expected->>'campaign_id'
           OR NEW.kind IS DISTINCT FROM expected->>'kind'
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                          WHERE configuration_id = NEW.configuration_id
                            AND record_id = NEW.campaign_id) THEN
            RAISE EXCEPTION 'Invalid schedule ownership' USING ERRCODE = '23514';
        END IF;
        SELECT timezone, starts_at, ends_at INTO owner_zone, owner_start, owner_end
        FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.configuration_id AND record_id = NEW.campaign_id;
        IF NEW.kind IN ('initial', 'reminder') THEN
            expected_due := public.stewardship_resolve_local_v1(
                (expected->>'date')::date + (expected->>'time')::time, owner_zone);
        END IF;
        IF NEW.due_at IS DISTINCT FROM expected_due THEN
            RAISE EXCEPTION 'Invalid resolved schedule boundary' USING ERRCODE = '23514';
        END IF;
        IF NEW.due_at < owner_start OR NEW.due_at >= owner_end THEN
            RAISE EXCEPTION 'Schedule is outside campaign interval' USING ERRCODE = '23514';
        END IF;
        -- Match preparation's global lock even for direct competing inserts.
        -- PL/pgSQL's following READ COMMITTED query sees the previous winner.
        PERFORM pg_advisory_xact_lock(736210, 1);
        -- A schedule never moves or changes kind, except that the offline
        -- conversion (#1786) turns a Daily or Weekly digest into a report
        -- email, keeping its identity.
        IF EXISTS (SELECT 1 FROM public.stewardship_schedule_revision
                   WHERE record_id = NEW.record_id
                     AND (campaign_id <> NEW.campaign_id
                          OR (kind <> NEW.kind AND NOT (NEW.kind = 'report'
                              AND kind IN ('daily_digest', 'weekly_digest'))))) THEN
            RAISE EXCEPTION 'Logical schedule identity is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_catchup_digest_receipt_v1(proposed jsonb)
RETURNS boolean LANGUAGE plpgsql
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand public.stewardship_activation_catchup%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE;
        d public.stewardship_schedule_definition%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE;
        prefix text; position text; stage text; after_day text; candidate date;
        last_day text; due timestamptz; exhausted boolean:=false; step integer;
        pending_count integer; i integer; shift integer;
BEGIN
    SELECT * INTO demand FROM public.stewardship_activation_catchup WHERE id=(proposed->>'demand_id')::uuid;
    SELECT configuration.* INTO p FROM public.stewardship_campaign c
        JOIN public.stewardship_campaign_configuration configuration ON configuration.id=c.active_configuration_id
        WHERE c.id=demand.campaign_id;
    IF p.id IS NULL THEN RETURN false; END IF;
    prefix:=replace(p.id::text,'-','')||':';
    IF proposed->>'group_key'=prefix||'families:end' THEN
        RETURN proposed->>'cursor'=prefix||'digests:' AND (proposed->>'items')::bigint=0
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_family_campaign f
                WHERE f.campaign_id=demand.campaign_id AND f.created_at<=demand.created_at
                  AND NOT EXISTS(SELECT 1 FROM public.stewardship_catchup_checkpoint k
                      WHERE k.demand_id=demand.id AND k.group_key=prefix||'family:'||f.id::text));
    END IF;
    IF NOT starts_with(demand.cursor,prefix||'digests:') THEN RETURN false; END IF;
    SELECT * INTO d FROM public.stewardship_schedule_definition s
        WHERE s.campaign_id=demand.campaign_id AND s.current_revision_id IS NOT NULL
            AND s.kind IN ('daily_digest','weekly_digest','report')
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_catchup_checkpoint k
                WHERE k.demand_id=demand.id AND k.group_key=prefix||'digest:'||s.id::text)
        ORDER BY s.id LIMIT 1;
    IF d.id IS NULL THEN RETURN false; END IF;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=d.current_revision_id;
    position:=substring(demand.cursor FROM length(prefix||'digests:')+1);
    IF position='' THEN stage:='dates'; after_day:='';
    ELSE
        IF split_part(position,':',1)<>replace(d.id::text,'-','') THEN RETURN false; END IF;
        stage:=split_part(position,':',2); after_day:=split_part(position,':',3);
    END IF;
    IF stage='dates' AND d.kind='report' THEN
        -- A report email (#1786): its rule's candidate sends after the
        -- cursor's slot, as schedule_evaluation.ReportPlan.page examines them.
        shift:=CASE WHEN v.values->>'slot_basis'='report_date' THEN 1 ELSE 0 END;
        candidate:=public.stewardship_report_next_send_v1(v.values,p.start_date,p.end_date,
            CASE WHEN after_day<>'' THEN after_day::date+shift END);
        last_day:=after_day;
        FOR i IN 1..100 LOOP
            IF candidate IS NULL THEN exhausted:=true; EXIT; END IF;
            due:=public.stewardship_resolve_local_v1(candidate+(v.values->'schedule'->>'time')::time,p.timezone);
            IF due>least(demand.cutoff,public.stewardship_campaign_now_v1()) THEN exhausted:=true; EXIT; END IF;
            last_day:=(candidate-shift)::text;
            IF public.stewardship_catchup_due_v1(d.id,last_day) IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins'
                        AND o.slot=last_day AND o.due_at=public.stewardship_catchup_due_v1(d.id,last_day))
                AND NOT public.stewardship_schedule_slot_excluded_v1(d.id,'production','admins',last_day)
                THEN RETURN false; END IF;
            candidate:=public.stewardship_report_next_send_v1(v.values,p.start_date,p.end_date,candidate);
        END LOOP;
        exhausted:=exhausted OR candidate IS NULL;
        stage:=CASE WHEN exhausted THEN 'cover' ELSE 'dates' END;
        RETURN proposed->>'group_key'=prefix||'page:'||d.id::text||':'||coalesce(nullif(last_day,''),'empty')||':'||stage
            AND proposed->>'cursor'=prefix||'digests:'||replace(d.id::text,'-','')||':'||stage||':'||last_day;
    END IF;
    IF stage='dates' THEN
        candidate:=greatest(p.start_date,CASE WHEN after_day<>'' THEN after_day::date+1 ELSE p.start_date END);
        step:=CASE d.kind WHEN 'weekly_digest' THEN 7 ELSE 1 END;
        IF step=7 THEN candidate:=candidate+((v.values->>'weekday')::integer
            -(extract(isodow FROM candidate)::integer-1)+7)%7; END IF;
        last_day:=after_day;
        FOR i IN 1..100 LOOP
            IF candidate>p.end_date THEN exhausted:=true; EXIT; END IF;
            due:=public.stewardship_resolve_local_v1(
                (candidate+CASE d.kind WHEN 'daily_digest' THEN 1 ELSE 0 END)
                    +(v.values->>'time')::time,p.timezone);
            IF due>least(demand.cutoff,public.stewardship_campaign_now_v1()) THEN exhausted:=true; EXIT; END IF;
            last_day:=candidate::text;
            IF public.stewardship_catchup_due_v1(d.id,last_day) IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins'
                        AND o.slot=last_day AND o.due_at=due)
                AND NOT public.stewardship_schedule_slot_excluded_v1(d.id,'production','admins',last_day)
                THEN RETURN false; END IF;
            candidate:=candidate+step;
        END LOOP;
        stage:=CASE WHEN exhausted THEN 'cover' ELSE 'dates' END;
        RETURN proposed->>'group_key'=prefix||'page:'||d.id::text||':'||coalesce(nullif(last_day,''),'empty')||':'||stage
            AND proposed->>'cursor'=prefix||'digests:'||replace(d.id::text,'-','')||':'||stage||':'||last_day;
    END IF;
    IF stage<>'cover' THEN RETURN false; END IF;
    IF proposed->>'group_key'=prefix||'cover:'||d.id::text||':'||(demand.groups_completed+1)::text THEN
        RETURN proposed->>'cursor'=demand.cursor AND (proposed->>'items')::bigint>0;
    END IF;
    IF proposed->>'group_key'<>prefix||'digest:'||d.id::text
        OR proposed->>'cursor'<>prefix||'digests:' THEN RETURN false; END IF;
    -- No bound or uncertain original can be treated as prepared. Exactly one
    -- unbound selection may remain; every other date needs a terminal outcome.
    IF EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins' AND o.due_at<=demand.cutoff
            AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
            AND (o.state IN ('running','delivery_unknown')
                OR (o.state='pending' AND (o.task_id IS NOT NULL OR o.outbox_id IS NOT NULL)))) THEN RETURN false; END IF;
    SELECT count(*) INTO pending_count FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins' AND o.due_at<=demand.cutoff
            AND o.state='pending'
            AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot);
    RETURN pending_count<=1 AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.revision_id=v.id AND o.mode='production' AND o.target='admins'
            AND o.due_at<=demand.cutoff AND o.state='coalesced'
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                WHERE f.definition_id=d.id AND f.mode=o.mode AND f.target=o.target AND f.slot=o.slot
                    AND f.disposition='coalesced' AND f.occurrence_id=o.replacement_id)
    ) AND NOT EXISTS(
        SELECT 1 FROM public.stewardship_schedule_occurrence o
        WHERE o.definition_id=d.id AND o.mode='production' AND o.target='admins'
            AND o.revision_id<>v.id AND o.state='skipped' AND o.reason='schedule_replaced'
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e WHERE e.previous_id=o.id)
            AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                    WHERE f.definition_id=d.id AND f.mode='production' AND f.target='admins'
                        AND f.occurrence_id=o.id AND f.disposition='coalesced')
                OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e
                    WHERE e.demand_id=demand.id AND e.replacement_id=o.id)));
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_catchup_due_v1(definition uuid,slot text)
RETURNS timestamptz LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE d public.stewardship_schedule_definition%ROWTYPE;
        v public.stewardship_schedule_revision%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE; day date;
BEGIN
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=$1;
    SELECT * INTO v FROM public.stewardship_schedule_revision WHERE id=d.current_revision_id;
    SELECT configuration.* INTO p FROM public.stewardship_campaign c
        JOIN public.stewardship_campaign_configuration configuration ON configuration.id=c.active_configuration_id
        WHERE c.id=d.campaign_id;
    IF v.id IS NULL OR p.id IS NULL THEN RETURN NULL; END IF;
    IF d.kind IN ('initial','reminder') THEN
        RETURN CASE WHEN slot='once' THEN v.due_at END;
    END IF;
    -- A report email (#1786) evaluates its own rule, slot basis, skips and
    -- pauses (schedule_evaluation.ReportPlan).
    IF d.kind='report' THEN
        RETURN public.stewardship_report_slot_due_v1(v.values,p.start_date,p.end_date,p.timezone,slot);
    END IF;
    IF slot !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN NULL; END IF;
    day:=slot::date;
    IF day<p.start_date OR day>p.end_date OR day::text<>slot THEN RETURN NULL; END IF;
    -- A digest's skipped send (#1658) is no slot: nothing may plan it.
    IF coalesce(v.values->'skip_dates' ? slot,false) THEN RETURN NULL; END IF;
    IF d.kind='weekly_digest' THEN
        IF extract(isodow FROM day)::integer-1<>(v.values->>'weekday')::integer THEN RETURN NULL; END IF;
    ELSIF d.kind='daily_digest' THEN
        IF public.stewardship_resolve_local_v1(day::timestamp,p.timezone)
            =public.stewardship_resolve_local_v1((day+1)::timestamp,p.timezone) THEN RETURN NULL; END IF;
        day:=day+1;
    ELSE RETURN NULL;
    END IF;
    RETURN public.stewardship_resolve_local_v1(day+(v.values->>'time')::time,p.timezone);
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_catchup_occurrence_shape_v1(proposed jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE d public.stewardship_schedule_definition%ROWTYPE;
        demand public.stewardship_activation_catchup%ROWTYPE;
        c public.stewardship_campaign%ROWTYPE;
        p public.stewardship_campaign_configuration%ROWTYPE;
        family public.stewardship_family_campaign%ROWTYPE;
        selected public.stewardship_schedule_occurrence%ROWTYPE;
        selected_kind text; expected timestamptz; reason text;
BEGIN
    SELECT * INTO d FROM public.stewardship_schedule_definition WHERE id=(proposed->>'definition_id')::uuid;
    SELECT * INTO demand FROM public.stewardship_activation_catchup WHERE campaign_id=d.campaign_id AND completed_at IS NULL;
    SELECT * INTO c FROM public.stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO p FROM public.stewardship_campaign_configuration WHERE id=c.active_configuration_id;
    IF d.id IS NULL OR demand.id IS NULL OR p.id IS NULL THEN RETURN false; END IF;
    IF d.kind IN ('initial','reminder') THEN
        IF proposed->>'target' !~ '^family:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        SELECT * INTO family FROM public.stewardship_family_campaign
            WHERE id=substring(proposed->>'target' FROM 8)::uuid AND campaign_id=d.campaign_id
                AND created_at<=demand.created_at;
        IF family.id IS NULL THEN RETURN false; END IF;
    ELSIF d.kind IN ('daily_digest','weekly_digest','report') THEN
        IF proposed->>'target'<>'admins' THEN RETURN false; END IF;
    ELSE RETURN false;
    END IF;
    expected:=public.stewardship_catchup_due_v1(d.id,proposed->>'slot');
    IF proposed->>'slot'='recovery:'||demand.id::text AND d.kind IN ('daily_digest','weekly_digest','report') THEN
        -- An aggregate must be dated from a real staged or cancelled covered
        -- predecessor; neither an arbitrary timestamp nor a caller flag is proof.
        SELECT max(o.due_at) INTO expected FROM public.stewardship_schedule_occurrence o
        WHERE o.definition_id=d.id AND o.mode='production' AND o.target='admins'
            AND o.id<>(proposed->>'id')::uuid AND o.due_at<=demand.cutoff
            AND ((o.revision_id=d.current_revision_id AND o.state='pending' AND o.production_cycle=c.production_cycle
                    AND o.task_id IS NULL AND o.outbox_id IS NULL
                    AND NOT starts_with(o.slot,'recovery:')
                    AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot))
                OR (o.state='skipped' AND o.reason='schedule_replaced'
                    AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                            WHERE f.occurrence_id=o.id AND f.disposition='coalesced')
                        OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e
                            WHERE e.replacement_id=o.id AND e.demand_id=demand.id))));
    END IF;
    IF expected IS NULL OR expected IS DISTINCT FROM (proposed->>'due_at')::timestamptz
        OR expected>demand.cutoff
        OR proposed->>'occurrence_key'<>encode(sha256(convert_to(
            '["'||(proposed->>'revision_id')||'","production","'||(proposed->>'target')
            ||'","'||(proposed->>'slot')||'"'
            ||CASE WHEN coalesce((proposed->>'recovery_generation')::bigint,0)>0
                THEN ','||(proposed->>'recovery_generation') ELSE '' END
            ||CASE WHEN coalesce((proposed->>'production_cycle')::bigint,0)>0
                THEN ',["production_cycle",'||(proposed->>'production_cycle')||']' ELSE '' END
            ||']','UTF8')),'hex') THEN RETURN false; END IF;
    IF proposed->>'state'='pending' THEN
        RETURN proposed->>'reason'='' AND proposed->>'replacement_id' IS NULL;
    END IF;
    IF proposed->>'state'='skipped' THEN
        IF family.id IS NULL THEN RETURN false; END IF;
        reason:=CASE WHEN public.stewardship_campaign_now_v1()>=p.ends_at THEN 'campaign_closed'
            WHEN NOT family.active OR NOT family.email_eligible THEN 'family_ineligible'
            WHEN family.effective_submission_id IS NOT NULL THEN 'family_responded'
            WHEN NOT family.email_deliverable THEN 'no_deliverable_recipient' ELSE '' END;
        RETURN reason<>'' AND proposed->>'reason'=reason AND proposed->>'replacement_id' IS NULL;
    END IF;
    SELECT * INTO selected FROM public.stewardship_schedule_occurrence WHERE id=(proposed->>'replacement_id')::uuid;
    SELECT kind INTO selected_kind FROM public.stewardship_schedule_definition s
        WHERE s.id=selected.definition_id AND s.campaign_id=d.campaign_id
            AND s.current_revision_id=selected.revision_id;
    RETURN selected.id IS NOT NULL AND selected.id<>(proposed->>'id')::uuid
        AND selected.state='pending' AND selected.mode='production'
        AND selected.target=proposed->>'target' AND selected.task_id IS NULL AND selected.outbox_id IS NULL
        AND selected.due_at<=demand.cutoff
        AND CASE WHEN family.id IS NOT NULL THEN
            d.kind='reminder' AND selected_kind IN ('initial','reminder')
            AND proposed->>'reason'='missed_family_recovery'
            AND family.active AND family.email_eligible AND family.email_deliverable
            AND family.effective_submission_id IS NULL AND public.stewardship_campaign_now_v1()<p.ends_at
            AND (selected_kind='initial' OR (
                (selected.due_at,selected.definition_id,selected.id)>=
                    (expected,d.id,(proposed->>'id')::uuid)
                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition s
                    JOIN public.stewardship_schedule_revision v ON v.id=s.current_revision_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind='initial' AND v.due_at<=demand.cutoff
                        AND NOT public.stewardship_schedule_slot_excluded_v1(s.id,'production',proposed->>'target','once'))))
        ELSE selected.definition_id=d.id AND selected.due_at>=expected
            AND (d.kind<>'daily_digest' OR selected.slot='recovery:'||demand.id::text)
            -- A report email (#1786) coalesces into its latest send, as the
            -- weekly digest did; its recovery send reports the covered range.
            AND proposed->>'reason'=CASE d.kind
                WHEN 'daily_digest' THEN 'missed_daily_recovery'
                WHEN 'report' THEN 'missed_report_recovery' ELSE 'missed_weekly_recovery' END END;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_checkpoint_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE d stewardship_activation_catchup%ROWTYPE; t stewardship_task_run%ROWTYPE; r stewardship_system_configuration%ROWTYPE;
        projection uuid; prefix text; family uuid; pending_count integer;
BEGIN
    -- Count-only group observations are immutable with their existing fenced
    -- checkpoint. Empty metadata belongs to intermediate traversal pages.
    IF jsonb_typeof(NEW.outcome_counts)<>'object' THEN
        RAISE EXCEPTION 'Catch-up outcomes require count-only metadata' USING ERRCODE='23514';
    END IF;
    IF NEW.outcome_counts<>'{}'::jsonb AND (
        NOT NEW.outcome_counts ?& ARRAY['active_families','eligible_families','no_email_families',
            'family_messages','daily_messages','weekly_messages','coalesced_slots']
        OR NEW.outcome_counts - ARRAY['active_families','eligible_families','no_email_families',
            'family_messages','daily_messages','weekly_messages','coalesced_slots']<>'{}'::jsonb
        OR EXISTS(SELECT 1 FROM jsonb_each(NEW.outcome_counts) item
            WHERE jsonb_typeof(item.value)<>'number' OR item.value::text !~ '^(0|[1-9][0-9]{0,14})$')
    ) THEN
        RAISE EXCEPTION 'Catch-up outcomes require exact nonnegative count fields' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO r FROM stewardship_system_configuration FOR UPDATE;
    SELECT * INTO d FROM stewardship_activation_catchup WHERE id=NEW.demand_id FOR UPDATE;
    SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
    IF d.completed_at IS NOT NULL OR NEW.sequence<>d.groups_completed+1 OR NEW.group_key='' OR NEW.cursor='' OR NEW.phase=''
       OR t.id IS NULL OR d.task_root_id IS NULL OR t.root_id<>d.task_root_id OR t.state<>'running'
       OR t.fence<>NEW.fence OR t.worker_id IS DISTINCT FROM NEW.actor_id OR t.lease_expires_at<=clock_timestamp()
       OR r.restore_review_required OR r.current_campaign_id IS DISTINCT FROM d.campaign_id
       OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=d.campaign_id AND state IN ('preparing','running','tombstone')) THEN
        RAISE EXCEPTION 'Catch-up checkpoint requires current fenced input' USING ERRCODE='23514'; END IF;
    IF current_user='pk_stewardship_worker' THEN
        SELECT active_configuration_id INTO projection FROM stewardship_campaign WHERE id=d.campaign_id;
        prefix:=replace(projection::text,'-','')||':';
        IF NEW.items>100 OR NEW.phase NOT IN ('families','digests','complete')
            OR NOT starts_with(NEW.cursor,prefix) OR NOT starts_with(NEW.group_key,prefix)
            OR NEW.correlation_id<>t.id OR r.mode<>'production'
            OR NEW.complete<>(NEW.phase='complete') THEN
            RAISE EXCEPTION 'Catch-up requires a bounded compiled checkpoint' USING ERRCODE='23514';
        END IF;
        IF NEW.phase='families' THEN
            family:=split_part(NEW.group_key,':',3)::uuid;
            SELECT count(*) INTO pending_count FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_schedule_definition s ON s.current_revision_id=o.revision_id
                WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                    AND o.mode='production' AND o.target='family:'||family::text
                    AND o.due_at<=d.cutoff AND o.state='pending'
                    AND NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot);
            IF NEW.group_key<>prefix||'family:'||family::text
                OR NEW.cursor<>prefix||'families:'||replace(family::text,'-','')
                OR NOT EXISTS(SELECT 1 FROM stewardship_family_campaign f
                    WHERE f.id=family AND f.campaign_id=d.campaign_id AND f.created_at<=d.created_at)
                OR EXISTS (
                    SELECT 1 FROM stewardship_schedule_definition s
                    JOIN stewardship_schedule_revision v ON v.id=s.current_revision_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                        AND v.due_at<=d.cutoff
                        AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
                            WHERE o.revision_id=v.id AND o.mode='production'
                                AND o.target='family:'||family::text AND o.slot='once')
                        AND NOT public.stewardship_schedule_slot_excluded_v1(s.id,'production','family:'||family::text,'once')
                )
                OR pending_count>1
                OR (pending_count>0 AND EXISTS(SELECT 1 FROM public.stewardship_family_campaign f
                    JOIN public.stewardship_campaign_configuration p ON p.id=projection
                    WHERE f.id=family AND (NOT f.active OR NOT f.email_eligible OR NOT f.email_deliverable
                        OR f.effective_submission_id IS NOT NULL OR public.stewardship_campaign_now_v1()>=p.ends_at)))
                -- A current selection must carry all interrupted predecessor
                -- coverage. With no selection (removal/ineligibility), retain
                -- the old immutable history without inventing replacement mail.
                OR (pending_count>0 AND EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_schedule_definition s ON s.id=o.definition_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                        AND o.mode='production' AND o.target='family:'||family::text
                        AND o.state='skipped' AND o.reason IN ('schedule_replaced','schedule_removed')
                        AND NOT EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e WHERE e.previous_id=o.id)
                        AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                                WHERE f.mode=o.mode AND f.target=o.target AND f.disposition='coalesced' AND f.occurrence_id=o.id)
                            OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement e
                                WHERE e.demand_id=d.id AND e.replacement_id=o.id))))
                OR EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_schedule_definition s ON s.current_revision_id=o.revision_id
                    WHERE s.campaign_id=d.campaign_id AND s.kind IN ('initial','reminder')
                        AND o.mode='production' AND o.target='family:'||family::text AND o.due_at<=d.cutoff
                        AND ((NOT public.stewardship_schedule_slot_excluded_v1(o.definition_id,o.mode,o.target,o.slot)
                                AND (o.state IN ('running','delivery_unknown')
                                    OR (o.state='pending' AND (o.task_id IS NOT NULL OR o.outbox_id IS NOT NULL))))
                            -- Coverage is the evidence being checked here, not
                            -- permission to skip its integrity check. Only an
                            -- independent restore hold excludes this outcome.
                            OR (o.state='coalesced'
                                AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h
                                    WHERE h.definition_id=o.definition_id AND h.mode=o.mode AND h.target=o.target
                                        AND h.slot=o.slot AND h.state IN ('unreviewed','assumed_delivered'))
                                AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                                WHERE f.definition_id=o.definition_id AND f.mode=o.mode AND f.target=o.target
                                    AND f.slot=o.slot AND f.disposition='coalesced' AND f.occurrence_id=o.replacement_id))))
                THEN RAISE EXCEPTION 'Catch-up Family receipt lacks cutoff outcomes' USING ERRCODE='23514'; END IF;
        END IF;
        IF NEW.phase='digests' AND NOT public.stewardship_catchup_digest_receipt_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Catch-up digest receipt lacks bounded inventory proof' USING ERRCODE='23514';
        END IF;
        IF NEW.complete THEN
            IF NEW.group_key<>prefix||'complete' OR NEW.cursor<>prefix||'complete:'
                OR NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k
                    WHERE k.demand_id=d.id AND k.group_key=prefix||'families:end')
                OR EXISTS(SELECT 1 FROM stewardship_family_campaign f
                    WHERE f.campaign_id=d.campaign_id AND f.created_at<=d.created_at
                    AND NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k
                        WHERE k.demand_id=d.id AND k.group_key=prefix||'family:'||f.id::text))
                OR EXISTS(SELECT 1 FROM stewardship_schedule_definition s
                    WHERE s.campaign_id=d.campaign_id AND s.current_revision_id IS NOT NULL
                      AND s.kind IN ('daily_digest','weekly_digest','report')
                      AND NOT EXISTS(SELECT 1 FROM stewardship_catchup_checkpoint k
                          WHERE k.demand_id=d.id AND k.group_key=prefix||'digest:'||s.id::text))
            THEN RAISE EXCEPTION 'Catch-up completion requires current cohort and digest coverage'
                USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_cleanup_batch_targets_v1(request_uuid uuid, maximum_rows integer)
RETURNS TABLE(category text, target_id uuid)
LANGUAGE plpgsql STABLE SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    candidate record;
    companion uuid;
    companion_category text;
    eligible boolean;
    used integer := 0;
    unit_size integer;
BEGIN
    IF maximum_rows NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Cleanup requires a bounded batch' USING ERRCODE='23514';
    END IF;
    -- The indexed durable cursor caps inspected candidates BEFORE evaluating
    -- dependencies. Scan-only checkpoints advance over blocked prefixes; the
    -- next sweep revisits parents whose children were deleted in earlier units.
    FOR candidate IN SELECT i.category,i.target_id
        FROM public.stewardship_cleanup_scan_window_v1(request_uuid,maximum_rows) i
        WHERE CASE i.category
            WHEN 'session_data' THEN false
            WHEN 'baselines' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_submission WHERE baseline_id=i.target_id)
            WHEN 'family_sessions' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_family_form_baseline WHERE family_session_id=i.target_id)
            WHEN 'source_pins' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_source_pin WHERE id=i.target_id
                  AND (parent_kind='form_baseline' OR (parent_kind='digest'
                    AND EXISTS(SELECT 1 FROM public.stewardship_daily_digest_snapshot WHERE id=parent_id))))
            WHEN 'daily_digest_ready' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_daily_digest_recipient WHERE ready_id=i.target_id)
            WHEN 'weekly_digest_snapshots' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_weekly_digest_recipient WHERE snapshot_id=i.target_id)
            WHEN 'daily_digest_fact_pins' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_fact_pin p
                JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=p.parent_id
                WHERE p.id=i.target_id)
            WHEN 'daily_digest_snapshots' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_daily_digest_ready WHERE snapshot_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_fact_pin
                    WHERE parent_kind='digest' AND parent_id=i.target_id)
            -- A report email's snapshot (#1786) goes after its recipients.
            WHEN 'report_email_snapshots' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_report_email_recipient WHERE snapshot_id=i.target_id)
            WHEN 'proposals' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_proposed_change WHERE superseded_by_id=i.target_id)
            WHEN 'ministry_requests' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_ministry_request WHERE superseded_by_id=i.target_id)
            WHEN 'rehearsal_credentials' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_rehearsal_code_mac WHERE credential_id=i.target_id)
            WHEN 'outbox_renders' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_outbox_message WHERE render_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_event WHERE render_id=i.target_id)
            WHEN 'outbox_messages' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_outbox_event WHERE message_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission_receipt WHERE outbox_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_daily_digest_recipient WHERE outbox_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_weekly_digest_recipient WHERE outbox_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_report_email_recipient WHERE outbox_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_render r
                    JOIN public.stewardship_outbox_message m ON m.id=r.message_id
                    WHERE m.id=i.target_id AND r.id<>m.render_id)
            WHEN 'submissions' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_proposed_change WHERE submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_request WHERE submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission_receipt WHERE submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message WHERE semantic_key=i.target_id AND purpose='receipt')
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin WHERE parent_kind='submission' AND parent_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission WHERE prior_submission_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_family_form_baseline WHERE prior_submission_id=i.target_id)
            WHEN 'occurrences' THEN NOT EXISTS (
                SELECT 1 FROM public.stewardship_occurrence_transition WHERE occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_recovery_replacement
                    WHERE previous_id=i.target_id OR replacement_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_fulfillment WHERE occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence WHERE replacement_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_restore_delivery_hold WHERE recovery_occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_restore_hold_resolution WHERE recovery_occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_postclose_resolution WHERE occurrence_id=i.target_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_occurrence o
                    JOIN public.stewardship_outbox_message m ON m.id=o.outbox_id WHERE o.id=i.target_id)
            ELSE true END
        ORDER BY i.cursor_position
    LOOP
        EXIT WHEN used=maximum_rows;
        companion := NULL;
        companion_category := NULL;
        unit_size := 1;
        eligible := true;
        CASE candidate.category
        WHEN 'baselines' THEN
            SELECT id INTO companion FROM public.stewardship_source_pin
                WHERE parent_kind='form_baseline' AND parent_id=candidate.target_id;
            IF companion IS NOT NULL THEN
                companion_category := 'source_pins';
                unit_size := 2;
                eligible := EXISTS (SELECT 1 FROM public.stewardship_production_target i
                    WHERE i.request_id=request_uuid AND i.category=companion_category AND i.target_id=companion);
            END IF;
        WHEN 'family_sessions' THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_production_target i
                WHERE i.request_id=request_uuid AND i.category='session_data' AND i.target_id=candidate.target_id) THEN
                companion := candidate.target_id;
                companion_category := 'session_data';
                unit_size := 2;
            END IF;
        WHEN 'daily_digest_snapshots' THEN
            -- A snapshot and its source pin form one indivisible private unit.
            -- Removing the snapshot alone would orphan the inventory selector.
            SELECT id INTO companion FROM public.stewardship_source_pin
                WHERE parent_kind='digest' AND parent_id=candidate.target_id;
            IF companion IS NOT NULL THEN
                companion_category := 'source_pins';
                unit_size := 2;
                eligible := EXISTS (SELECT 1 FROM public.stewardship_production_target i
                    WHERE i.request_id=request_uuid AND i.category=companion_category AND i.target_id=companion);
            END IF;
        WHEN 'outbox_messages' THEN
            SELECT render_id INTO companion FROM public.stewardship_outbox_message WHERE id=candidate.target_id;
            companion_category := 'outbox_renders';
            unit_size := 2;
            eligible := EXISTS (SELECT 1 FROM public.stewardship_production_target i
                    WHERE i.request_id=request_uuid AND i.category='outbox_renders' AND i.target_id=companion);
        ELSE NULL;
        END CASE;
        IF NOT eligible OR used+unit_size>maximum_rows THEN CONTINUE; END IF;
        IF companion IS NOT NULL THEN
            category := companion_category;
            target_id := companion;
            RETURN NEXT;
        END IF;
        category := candidate.category;
        target_id := candidate.target_id;
        RETURN NEXT;
        used := used+unit_size;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_cleanup_inventory_v1(campaign_uuid uuid)
RETURNS TABLE(category text, target_id uuid)
LANGUAGE sql STABLE AS $$
    WITH epochs AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_rehearsal_epoch WHERE campaign_id=campaign_uuid
    ), responses AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_submission
        WHERE campaign_id=campaign_uuid AND mode='test'
          AND rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), baselines AS NOT MATERIALIZED (
        SELECT b.id FROM public.stewardship_family_form_baseline b
        JOIN public.stewardship_family_campaign f ON f.id=b.family_id
        WHERE f.campaign_id=campaign_uuid AND b.mode='test'
          AND b.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), sessions AS NOT MATERIALIZED (
        SELECT s.id,s.session_id FROM public.stewardship_family_session s
        JOIN public.stewardship_family_campaign f ON f.id=s.family_id
        WHERE f.campaign_id=campaign_uuid AND s.mode='testing'
          AND s.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), messages AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_outbox_message
        WHERE campaign_id=campaign_uuid AND mode='testing' AND routing='testing_override'
    ), occurrences AS NOT MATERIALIZED (
        SELECT o.id FROM public.stewardship_schedule_occurrence o
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        WHERE d.campaign_id=campaign_uuid AND o.mode='testing' AND o.routing='testing_override'
    ), credentials AS NOT MATERIALIZED (
        SELECT c.id FROM public.stewardship_rehearsal_credential c
        JOIN public.stewardship_family_campaign f ON f.id=c.family_id
        WHERE f.campaign_id=campaign_uuid AND c.epoch_id IN (SELECT id FROM epochs)
    ), digest_snapshots AS NOT MATERIALIZED (
        SELECT s.id FROM public.stewardship_daily_digest_snapshot s
        JOIN public.stewardship_daily_digest_preparation p ON p.id=s.preparation_id
        WHERE s.campaign_id=campaign_uuid AND p.mode='testing'
          AND p.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), digest_ready AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_daily_digest_ready
        WHERE snapshot_id IN (SELECT id FROM digest_snapshots)
    ), weekly_snapshots AS NOT MATERIALIZED (
        SELECT s.id FROM public.stewardship_weekly_digest_snapshot s
        JOIN public.stewardship_weekly_digest_preparation p ON p.id=s.preparation_id
        WHERE s.campaign_id=campaign_uuid AND p.mode='testing'
          AND p.rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), report_preparations AS NOT MATERIALIZED (
        -- Report emails (#1786): the rehearsal's sends. Their preparations
        -- are retained history, as the digests' are.
        SELECT id FROM public.stewardship_report_email_preparation
        WHERE campaign_id=campaign_uuid AND mode='testing'
          AND rehearsal_epoch_id IN (SELECT id FROM epochs)
    ), report_snapshots AS NOT MATERIALIZED (
        SELECT id FROM public.stewardship_report_email_snapshot
        WHERE preparation_id IN (SELECT id FROM report_preparations)
    )
    SELECT 'baselines',id FROM baselines
    UNION ALL SELECT 'family_sessions',id FROM sessions
    UNION ALL SELECT 'session_data',id FROM sessions WHERE session_id IS NOT NULL
    UNION ALL SELECT 'submissions',id FROM responses
    UNION ALL SELECT 'proposals',id FROM public.stewardship_proposed_change
        WHERE submission_id IN (SELECT id FROM responses)
    UNION ALL SELECT 'ministry_requests',id FROM public.stewardship_ministry_request
        WHERE submission_id IN (SELECT id FROM responses)
    UNION ALL SELECT 'submission_receipts',id FROM public.stewardship_submission_receipt
        WHERE submission_id IN (SELECT id FROM responses)
    UNION ALL SELECT 'source_pins',id FROM public.stewardship_source_pin
        WHERE (parent_kind='submission' AND parent_id IN (SELECT id FROM responses))
           OR (parent_kind='form_baseline' AND parent_id IN (SELECT id FROM baselines))
           OR (parent_kind='digest' AND parent_id IN (SELECT id FROM digest_snapshots))
           OR (parent_kind='digest' AND parent_id IN (SELECT id FROM report_preparations))
    UNION ALL SELECT 'daily_digest_snapshots',id FROM digest_snapshots
    UNION ALL SELECT 'daily_digest_ready',id FROM digest_ready
    UNION ALL SELECT 'daily_digest_recipients',id FROM public.stewardship_daily_digest_recipient
        WHERE ready_id IN (SELECT id FROM digest_ready)
    UNION ALL SELECT 'daily_digest_fact_pins',id FROM public.stewardship_fact_pin
        WHERE parent_kind='digest' AND parent_id IN (SELECT id FROM digest_snapshots)
    UNION ALL SELECT 'weekly_digest_snapshots',id FROM weekly_snapshots
    UNION ALL SELECT 'weekly_digest_recipients',id FROM public.stewardship_weekly_digest_recipient
        WHERE snapshot_id IN (SELECT id FROM weekly_snapshots)
    UNION ALL SELECT 'report_email_snapshots',id FROM report_snapshots
    UNION ALL SELECT 'report_email_recipients',id FROM public.stewardship_report_email_recipient
        WHERE snapshot_id IN (SELECT id FROM report_snapshots)
    UNION ALL SELECT 'report_email_fact_pins',id FROM public.stewardship_fact_pin
        WHERE parent_kind='digest' AND parent_id IN (SELECT id FROM report_preparations)
    UNION ALL SELECT 'occurrences',id FROM occurrences
    UNION ALL SELECT 'recovery_replacements',id FROM public.stewardship_recovery_replacement
        WHERE previous_id IN (SELECT id FROM occurrences)
          AND replacement_id IN (SELECT id FROM occurrences)
    UNION ALL SELECT 'occurrence_events',id FROM public.stewardship_occurrence_transition
        WHERE occurrence_id IN (SELECT id FROM occurrences)
    UNION ALL SELECT 'schedule_fulfillments',f.id FROM public.stewardship_schedule_fulfillment f
        JOIN public.stewardship_schedule_definition d ON d.id=f.definition_id
        WHERE d.campaign_id=campaign_uuid AND f.mode='testing'
          AND f.occurrence_id IN (SELECT id FROM occurrences)
    UNION ALL SELECT 'outbox_messages',id FROM messages
    UNION ALL SELECT 'outbox_renders',id FROM public.stewardship_outbox_render
        WHERE message_id IN (SELECT id FROM messages)
    UNION ALL SELECT 'outbox_events',id FROM public.stewardship_outbox_event
        WHERE message_id IN (SELECT id FROM messages)
    UNION ALL SELECT 'rehearsal_credentials',id FROM credentials
    UNION ALL SELECT 'rehearsal_macs',id FROM public.stewardship_rehearsal_code_mac
        WHERE credential_id IN (SELECT id FROM credentials) AND epoch_id IN (SELECT id FROM epochs)
    UNION ALL SELECT 'prior_inventory_targets',i.id FROM public.stewardship_production_target i
        JOIN public.stewardship_production_request r ON r.id=i.request_id
        WHERE r.campaign_id=campaign_uuid AND r.state='cancelled'
    -- Testing Family engagement (#477; the table arrives in campaigns 0002,
    -- after this file, which function-body deferral allows at installation).
    UNION ALL SELECT 'engagement',e.id FROM public.stewardship_family_engagement e
        JOIN public.stewardship_family_campaign f ON f.id=e.family_id
        WHERE f.campaign_id=campaign_uuid AND e.mode='test'
          AND e.rehearsal_epoch_id IN (SELECT id FROM epochs)
$$;

CREATE OR REPLACE FUNCTION public.stewardship_cleanup_relation_v1(target_category text)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog, public, pg_temp AS $$
    SELECT CASE target_category
        WHEN 'baselines' THEN 'stewardship_family_form_baseline'
        WHEN 'family_sessions' THEN 'stewardship_family_session'
        WHEN 'ministry_requests' THEN 'stewardship_ministry_request'
        WHEN 'occurrences' THEN 'stewardship_schedule_occurrence'
        WHEN 'occurrence_events' THEN 'stewardship_occurrence_transition'
        WHEN 'outbox_events' THEN 'stewardship_outbox_event'
        WHEN 'outbox_messages' THEN 'stewardship_outbox_message'
        WHEN 'outbox_renders' THEN 'stewardship_outbox_render'
        WHEN 'proposals' THEN 'stewardship_proposed_change'
        WHEN 'rehearsal_credentials' THEN 'stewardship_rehearsal_credential'
        WHEN 'rehearsal_macs' THEN 'stewardship_rehearsal_code_mac'
        WHEN 'schedule_fulfillments' THEN 'stewardship_schedule_fulfillment'
        WHEN 'source_pins' THEN 'stewardship_source_pin'
        WHEN 'submission_receipts' THEN 'stewardship_submission_receipt'
        WHEN 'submissions' THEN 'stewardship_submission'
        WHEN 'prior_inventory_targets' THEN 'stewardship_production_target'
        WHEN 'daily_digest_recipients' THEN 'stewardship_daily_digest_recipient'
        WHEN 'daily_digest_ready' THEN 'stewardship_daily_digest_ready'
        WHEN 'daily_digest_snapshots' THEN 'stewardship_daily_digest_snapshot'
        WHEN 'daily_digest_fact_pins' THEN 'stewardship_fact_pin'
        WHEN 'recovery_replacements' THEN 'stewardship_recovery_replacement'
        WHEN 'weekly_digest_recipients' THEN 'stewardship_weekly_digest_recipient'
        WHEN 'weekly_digest_snapshots' THEN 'stewardship_weekly_digest_snapshot'
        WHEN 'engagement' THEN 'stewardship_family_engagement'
        WHEN 'report_email_recipients' THEN 'stewardship_report_email_recipient'
        WHEN 'report_email_snapshots' THEN 'stewardship_report_email_snapshot'
        WHEN 'report_email_fact_pins' THEN 'stewardship_fact_pin'
        ELSE NULL END
$$;

CREATE OR REPLACE FUNCTION public.stewardship_content_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE records jsonb; actual jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'content', '[]'::jsonb);
    IF NEW.validation_schema NOT IN ('campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11', 'campaign-series-v12') THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Content requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid content shape' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_content_version
        WHERE configuration_id=NEW.id AND NOT (
            (kind='page' AND slot IN ('welcome', 'login_help', 'pre_start', 'post_end',
                'census', 'member_census', 'ministry', 'financial', 'closing',
                'additional', 'review', 'thank_you', 'access_denied',
                'submission_confirmation'))
            OR (kind='email' AND slot IN ('initial', 'reminder', 'confirmation',
                'daily_digest', 'weekly_digest', 'critical_alert'))
        )) THEN
        RAISE EXCEPTION 'Invalid content slot' USING ERRCODE='23514';
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id, 'values',
        jsonb_build_object('campaign_id', campaign_id, 'kind', kind, 'slot', slot,
            'subject', subject, 'html', html, 'text', text)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_content_version WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Content projections are incomplete' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_content_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'content') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema IN ('campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11', 'campaign-series-v12')
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object('id', NEW.record_id,
        'values', jsonb_build_object('campaign_id', NEW.campaign_id,
            'kind', NEW.kind, 'slot', NEW.slot, 'subject', NEW.subject,
            'html', NEW.html, 'text', NEW.text))
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                      WHERE configuration_id=NEW.configuration_id
                        AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Content differs from YAML' USING ERRCODE='23514';
    END IF;
    SELECT predecessor_id INTO predecessor
    FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF EXISTS (
        WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM public.stewardship_configuration_version
            WHERE id=predecessor
            UNION
            SELECT p.id, p.predecessor_id
            FROM public.stewardship_configuration_version p
            JOIN chain c ON p.id=c.predecessor_id
        ) SELECT 1 FROM public.stewardship_content_version v
          JOIN chain c ON c.id=v.configuration_id
          WHERE v.record_id=NEW.record_id AND
              (v.campaign_id, v.kind, v.slot, v.subject, v.html, v.text)
              IS DISTINCT FROM
              (NEW.campaign_id, NEW.kind, NEW.slot, NEW.subject, NEW.html, NEW.text)
    ) THEN
        RAISE EXCEPTION 'Content revision identities must remain immutable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_closed_digest_v1(occurrence uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT (EXISTS(SELECT 1 FROM public.stewardship_daily_digest_preparation
                WHERE occurrence_id=$1 AND mode='production' AND phase='complete')
        OR EXISTS(SELECT 1 FROM public.stewardship_weekly_digest_preparation
                WHERE occurrence_id=$1 AND mode='production' AND phase='complete')
        OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation
                WHERE occurrence_id=$1 AND mode='production' AND phase='complete'))
      AND EXISTS(SELECT 1 FROM public.stewardship_delivery_digest_message link
        JOIN public.stewardship_delivery_message_resolution resolution ON resolution.message_id=link.outbox_id
        WHERE link.occurrence_id=$1 AND resolution.decision='cancel')
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_delivery_digest_message link
        JOIN public.stewardship_outbox_message m ON m.id=link.outbox_id
        WHERE link.occurrence_id=$1 AND NOT (m.state='delivered' OR m.state='cancelled' AND (
            m.reason='recipient_revoked' OR EXISTS(
                SELECT 1 FROM public.stewardship_delivery_message_resolution resolution
                WHERE resolution.message_id=m.id AND resolution.decision='cancel'))
            -- A failed report to a recipient who is not currently an
            -- Administrator: settled like the recipient_revoked cancellation,
            -- as the digest completion proofs count it.
            -- A report email's (#1786) is settled when its recipient is no
            -- longer one of the report's recipients.
            OR m.state='permanent_failure' AND m.purpose='admin_report' AND EXISTS(
                SELECT 1 FROM public.stewardship_report_email_recipient r
                JOIN public.stewardship_report_email_snapshot s ON s.id=r.snapshot_id
                JOIN public.stewardship_report_email_preparation p ON p.id=s.preparation_id
                WHERE r.outbox_id=m.id AND NOT public.stewardship_report_email_current_v1(
                    p.definition_id,r.kind,r.ref,r.address))
            OR m.state='permanent_failure' AND m.purpose<>'admin_report' AND NOT EXISTS(
                SELECT 1 FROM public.stewardship_system_configuration runtime
                JOIN public.stewardship_address_rule admin
                    ON admin.configuration_id=runtime.active_configuration_id
                WHERE admin.roles @> '["administrator"]'::jsonb AND admin.email IN (
                    SELECT address FROM public.stewardship_daily_digest_recipient WHERE outbox_id=m.id
                    UNION ALL
                    SELECT address FROM public.stewardship_weekly_digest_recipient WHERE outbox_id=m.id))))
$$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_control_effect_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE hold uuid; pause bigint;
BEGIN
    IF NEW.action='resolve' THEN
        PERFORM public.stewardship_delivery_resolve_closed_v1(NEW.id);
        RETURN NEW;
    END IF;
    IF NEW.action='resume' AND NEW.selection->>'plan'='family' THEN
        PERFORM public.stewardship_delivery_recover_families_v1(NEW.id);
        PERFORM public.stewardship_delivery_recover_digests_v1(NEW.id);
    END IF;
    -- Both control and all unsent holds commit together. Already-submitting or
    -- unknown outcomes keep their independent reconciliation and immutable data.
    INSERT INTO public.stewardship_campaign_control(id,campaign_id,request_id,action,
        expected_version,expected_runtime_version,reason,occurred_at,actor_id,correlation_id)
    VALUES(NEW.control_id,NEW.campaign_id,NEW.id,NEW.action,NEW.expected_campaign_version,
        NEW.expected_runtime_version,NEW.reason,public.stewardship_campaign_now_v1(),
        NEW.actor_id,NEW.correlation_id);
    SELECT pause_version INTO pause FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    IF NEW.action='resume' THEN
        UPDATE public.stewardship_outbox_message m SET
            action='release_hold',version=m.version+1,pause_hold_id=NULL,
            actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,command_id=gen_random_uuid(),
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'delivery_resume',NEW.id,m.id,m.version)::text,'UTF8')),'hex')
        WHERE m.campaign_id=NEW.campaign_id AND m.mode='production' AND m.routing='production'
            AND m.purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest','admin_report')
            AND m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL;
        RETURN NEW;
    END IF;
    INSERT INTO public.stewardship_delivery_pause_hold(id,campaign_id,pause_version,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.campaign_id,pause,NEW.actor_id,NEW.correlation_id) RETURNING id INTO hold;
    UPDATE public.stewardship_outbox_message m SET
        action='hold',version=m.version+1,pause_hold_id=hold,pause_version=pause,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id,command_id=gen_random_uuid(),
        command_digest=encode(sha256(convert_to(jsonb_build_array(
            'delivery_pause',NEW.id,m.id,m.version)::text,'UTF8')),'hex'),
        reason='pause_'||pause::text
    WHERE m.campaign_id=NEW.campaign_id AND m.mode='production' AND m.routing='production'
        AND m.purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest','admin_report')
        AND m.state IN ('pending','retry_wait');
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_control_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign public.stewardship_campaign%ROWTYPE;
    runtime public.stewardship_system_configuration%ROWTYPE;
    instant timestamptz; current_inventory jsonb; current_health jsonb; family_impact jsonb; digest_impact jsonb;
    selected_types jsonb; selected_count bigint; clears_pause boolean;
    current_coverage jsonb; preparing bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Delivery control intent is immutable' USING ERRCODE='23514';
    END IF;
    IF current_setting('transaction_isolation')<>'read committed'
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736212 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
            AND classid=736220 AND objid=1 AND objsubid=2 AND mode='ExclusiveLock' AND granted)
       OR (session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
            (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE')) THEN
        RAISE EXCEPTION 'Delivery control requires its ordered web owner' USING ERRCODE='42501';
    END IF;
    SELECT * INTO runtime FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO campaign FROM public.stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    instant:=public.stewardship_campaign_now_v1();
    IF NEW.actor_id IS NULL OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Delivery control requires a live Admin session' USING ERRCODE='42501';
    END IF;
    IF campaign.id IS NULL OR runtime.current_campaign_id IS DISTINCT FROM campaign.id
       OR runtime.mode<>'production' OR runtime.restore_review_required
       OR runtime.version IS DISTINCT FROM NEW.expected_runtime_version
       OR campaign.version IS DISTINCT FROM NEW.expected_campaign_version
       OR campaign.state NOT IN ('scheduled','active','closed')
       OR NEW.preview_at>instant OR NEW.preview_at>=NEW.expires_at OR NEW.expires_at<=instant
       OR NEW.expires_at>NEW.preview_at+interval '5 minutes' OR btrim(NEW.reason)=''
       OR EXISTS(SELECT 1 FROM public.stewardship_campaign_work_gate
            WHERE campaign_id=campaign.id AND state IN ('preparing','running','tombstone'))
       OR EXISTS(SELECT 1 FROM public.stewardship_campaign_credentials
            WHERE campaign_id=campaign.id AND go_live_gate) THEN
        RAISE EXCEPTION 'Delivery control has stale or blocked inputs' USING ERRCODE='23514';
    END IF;
    SELECT inventory INTO current_inventory FROM public.stewardship_delivery_control_inventory
        WHERE campaign_id=campaign.id;
    IF NEW.inventory IS DISTINCT FROM current_inventory THEN
        RAISE EXCEPTION 'Delivery work changed; review a new preview' USING ERRCODE='23514';
    END IF;
    IF NEW.action='resolve' THEN
        IF NOT campaign.delivery_paused OR campaign.state<>'closed'
           OR jsonb_typeof(NEW.selection->'types') IS DISTINCT FROM 'array'
           OR coalesce(NEW.selection->>'decision','') NOT IN ('release','cancel','clear') THEN
            RAISE EXCEPTION 'Held-message resolution requires a closed paused campaign' USING ERRCODE='23514';
        END IF;
        SELECT coalesce(jsonb_agg(kind ORDER BY kind),'[]') INTO selected_types
            FROM (SELECT DISTINCT value AS kind FROM jsonb_array_elements_text(NEW.selection->'types')
                WHERE value IN ('receipt','daily_digest','weekly_digest','admin_report')) types;
        SELECT coalesce(sum((current_inventory->'types'->kind->>'held')::bigint),0) INTO selected_count
            FROM jsonb_array_elements_text(selected_types) kind;
        SELECT coverage,s.preparing INTO current_coverage,preparing
            FROM public.stewardship_delivery_closed_coverage_summary s WHERE campaign_id=campaign.id;
        SELECT coalesce(jsonb_object_agg(key,value),'{}') INTO current_coverage
            FROM jsonb_each(current_coverage) WHERE selected_types ? key;
        clears_pause:=(current_inventory->>'held')::bigint
                =selected_count+(current_inventory->>'stranded')::bigint
            AND (current_inventory->>'submitting')::bigint=0 AND (current_inventory->>'unknown')::bigint=0;
        current_health:='null'::jsonb;
        IF NEW.selection->>'decision'='release' THEN
            SELECT health INTO current_health FROM public.stewardship_delivery_control_health
                WHERE campaign_id=campaign.id;
            IF (current_health->>'ready')::boolean IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Held-message release requires current sender health' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.selection IS DISTINCT FROM jsonb_build_object('plan','closed','decision',NEW.selection->>'decision',
                'types',selected_types,'health',current_health,'coverage',current_coverage)
           OR preparing IS DISTINCT FROM 0
           OR (NEW.selection->>'decision'='cancel' AND EXISTS(
                SELECT 1 FROM jsonb_array_elements_text(selected_types) kind
                WHERE (current_inventory->'types'->kind->>'submitting')::bigint>0
                    OR (current_inventory->'types'->kind->>'unknown')::bigint>0))
           OR (NEW.control_id IS NOT NULL) IS DISTINCT FROM clears_pause
           OR (NEW.selection->>'decision'='clear' AND (NOT clears_pause OR selected_types<>'[]'::jsonb))
           OR (NEW.selection->>'decision'<>'clear' AND selected_count=0) THEN
            RAISE EXCEPTION 'Held-message selection changed; review exact current counts' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.control_id IS NULL OR NEW.action NOT IN ('pause','resume') THEN
        RAISE EXCEPTION 'Delivery control action is not admitted' USING ERRCODE='23514';
    END IF;
    IF NEW.action='pause' THEN
        IF campaign.delivery_paused OR NEW.selection<>'{}'::jsonb THEN
            RAISE EXCEPTION 'Delivery pause is not admitted' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT health INTO current_health FROM public.stewardship_delivery_control_health
            WHERE campaign_id=campaign.id;
        IF NOT campaign.delivery_paused
           OR (current_health->>'ready')::boolean IS DISTINCT FROM true
           OR (current_inventory->>'submitting')::bigint<>0
           OR (current_inventory->>'unknown')::bigint<>0 THEN
            RAISE EXCEPTION 'Resume requires current health and resolved uncertainty' USING ERRCODE='23514';
        END IF;
        IF NEW.selection->>'plan'='before_start' THEN
            IF campaign.state<>'scheduled'
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_configuration p
                    WHERE p.id=campaign.active_configuration_id AND instant<p.starts_at)
               OR NEW.selection IS DISTINCT FROM jsonb_build_object('plan','before_start','health',current_health)
               OR EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
                WHERE d.campaign_id=campaign.id AND o.mode='production'
                    AND o.state IN ('pending','running','delivery_unknown') AND o.due_at<=instant) THEN
                RAISE EXCEPTION 'Pre-start resume cannot consume due work' USING ERRCODE='23514';
            END IF;
        ELSIF NEW.selection->>'plan'='family' THEN
            SELECT impact INTO family_impact FROM public.stewardship_delivery_family_recovery_summary
                WHERE campaign_id=campaign.id;
            SELECT impact INTO digest_impact FROM public.stewardship_delivery_digest_recovery_summary
                WHERE campaign_id=campaign.id;
            IF campaign.state NOT IN ('scheduled','active')
               OR NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_configuration p
                    WHERE p.id=campaign.active_configuration_id AND instant>=p.starts_at AND instant<p.ends_at)
               OR (family_impact->>'blocked')::bigint IS DISTINCT FROM 0
               OR (digest_impact->>'blocked')::bigint IS DISTINCT FROM 0
               OR NEW.selection IS DISTINCT FROM jsonb_build_object('plan','family',
                    'health',current_health,'family',family_impact,'digests',digest_impact)
               OR EXISTS(SELECT 1 FROM public.stewardship_activation_catchup
                    WHERE campaign_id=campaign.id AND completed_at IS NULL) THEN
                RAISE EXCEPTION 'Resume requires the complete current recovery plan' USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'Unknown delivery recovery plan' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_message_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Held-message decisions are immutable' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_delivery_control command
        JOIN public.stewardship_campaign c ON c.id=command.campaign_id
        JOIN public.stewardship_outbox_message m ON m.id=NEW.message_id
        WHERE command.id=NEW.command_id AND command.action='resolve'
            AND NEW.actor_id=command.actor_id AND NEW.correlation_id=command.correlation_id
            AND command.selection->>'decision'=NEW.decision
            AND command.selection->'types' ? m.purpose
            AND c.id=NEW.campaign_id AND c.state='closed' AND c.delivery_paused
            AND c.pause_version=NEW.pause_version AND m.campaign_id=c.id
            AND m.mode='production' AND m.routing='production'
            AND m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL
            AND m.version=NEW.previous_version AND m.pause_version=c.pause_version
            AND m.purpose IN ('receipt','daily_digest','weekly_digest','admin_report')) THEN
        RAISE EXCEPTION 'Held-message decision requires its exact command and version' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_new_hold_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign public.stewardship_campaign%ROWTYPE; hold uuid;
BEGIN
    IF NEW.mode<>'production' OR NEW.routing<>'production'
       OR NEW.purpose NOT IN ('initial','reminder','receipt','daily_digest','weekly_digest','admin_report') THEN RETURN NEW; END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.state NOT IN ('pending','retry_wait') OR NEW.state=OLD.state
           OR public.stewardship_delivery_message_released_v1(NEW.id) THEN RETURN NEW; END IF;
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO campaign FROM public.stewardship_campaign WHERE id=NEW.campaign_id;
    IF NOT campaign.delivery_paused THEN RETURN NEW; END IF;
    INSERT INTO public.stewardship_delivery_pause_hold(id,campaign_id,pause_version,actor_id,correlation_id)
        VALUES(gen_random_uuid(),campaign.id,campaign.pause_version,NEW.actor_id,NEW.correlation_id)
        ON CONFLICT(campaign_id,pause_version) DO NOTHING;
    SELECT id INTO hold FROM public.stewardship_delivery_pause_hold
        WHERE campaign_id=campaign.id AND pause_version=campaign.pause_version;
    IF NEW.pause_hold_id IS NOT NULL AND (NEW.pause_hold_id<>hold
        OR NEW.pause_version IS DISTINCT FROM campaign.pause_version) THEN
        RAISE EXCEPTION 'New delivery must retain the current pause hold' USING ERRCODE='23514';
    END IF;
    NEW.pause_hold_id:=hold; NEW.pause_version:=campaign.pause_version;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_recover_digests_v1(command uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_delivery_control%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
BEGIN
    SELECT * INTO STRICT intent FROM public.stewardship_delivery_control WHERE id=command;
    SELECT * INTO STRICT campaign FROM public.stewardship_campaign WHERE id=intent.campaign_id;
    IF intent.action<>'resume' OR intent.selection->>'plan'<>'family'
       OR NOT campaign.delivery_paused THEN
        RAISE EXCEPTION 'Digest recovery requires exact paused resume ownership' USING ERRCODE='23514';
    END IF;
    WITH rows AS MATERIALIZED (
        SELECT p.*,coalesce(p.occurrence_id,gen_random_uuid()) AS planned_id
        FROM public.stewardship_delivery_digest_recovery p WHERE p.campaign_id=campaign.id
    ), selected AS MATERIALIZED (
        SELECT definition_id,coalesce((array_agg(planned_id ORDER BY due_at DESC,slot DESC)
            FILTER(WHERE NOT prepared))[1],gen_random_uuid()) AS id
        FROM rows GROUP BY definition_id
    ) INSERT INTO public.stewardship_delivery_digest_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),p.definition_id,p.revision_id,p.kind,
            p.planned_id,p.occurrence_id IS NOT NULL,p.slot,p.due_at,s.id
        FROM rows p JOIN selected s USING(definition_id);
    -- If every due occurrence already has frozen report inputs, allocate a new
    -- aggregate. Its predecessor edges retain original coverage; accepted
    -- recipient evidence remains immutable and is reused by normal fanout.
    INSERT INTO public.stewardship_delivery_digest_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),p.definition_id,p.revision_id,p.kind,
            p.selected_id,false,'recovery:'||intent.id::text,max(p.due_at),p.selected_id
        FROM public.stewardship_delivery_digest_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_delivery_digest_effect chosen
                WHERE chosen.transaction_id=p.transaction_id AND chosen.backend=p.backend
                    AND chosen.occurrence_id=p.selected_id)
        GROUP BY p.definition_id,p.revision_id,p.kind,p.selected_id;
    INSERT INTO public.stewardship_schedule_occurrence
        (id,actor_id,correlation_id,version,mode,routing,target,slot,due_at,occurrence_key,
         state,fence,attempts,reason,pause_version,definition_id,revision_id,production_cycle)
        SELECT p.occurrence_id,intent.actor_id,intent.correlation_id,1,'production','production',
            'admins',p.slot,p.due_at,
            encode(sha256(convert_to('["'||p.revision_id::text||'","production","admins","'
                ||p.slot||'"'||CASE WHEN campaign.production_cycle>0 THEN ',["production_cycle",'
                    ||campaign.production_cycle::text||']' ELSE '' END||']','UTF8')),'hex'),
            'pending',0,0,'',campaign.pause_version,p.definition_id,p.revision_id,campaign.production_cycle
        FROM public.stewardship_delivery_digest_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND NOT p.existing;
    UPDATE public.stewardship_outbox_message m SET
        state='cancelled',action='cancel_unsent',version=m.version+1,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
        command_digest=encode(sha256(convert_to(jsonb_build_array(
            'delivery_digest_recovery',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
        reason=CASE p.kind WHEN 'daily_digest' THEN 'missed_daily_recovery' WHEN 'weekly_digest' THEN 'missed_weekly_recovery' ELSE 'missed_report_recovery' END,
        finished_at=statement_timestamp(),sealed_substitutions=NULL,sealed_key_id=NULL,pause_hold_id=NULL
    FROM public.stewardship_delivery_digest_effect p
    JOIN public.stewardship_delivery_digest_message messages ON messages.occurrence_id=p.occurrence_id
    WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
        AND p.occurrence_id<>p.selected_id AND m.id=messages.outbox_id
        AND m.state IN ('pending','retry_wait');
    INSERT INTO public.stewardship_schedule_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),o.id,o.version,
            intent.actor_id,intent.correlation_id,
            CASE p.kind WHEN 'daily_digest' THEN 'missed_daily_recovery' WHEN 'weekly_digest' THEN 'missed_weekly_recovery' ELSE 'missed_report_recovery' END
        FROM public.stewardship_delivery_digest_effect p
        JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.occurrence_id<>p.selected_id;
    UPDATE public.stewardship_schedule_occurrence o SET
        state='coalesced',version=o.version+1,lease_expires_at=NULL,replacement_id=p.selected_id,
        reason=CASE p.kind WHEN 'daily_digest' THEN 'missed_daily_recovery' WHEN 'weekly_digest' THEN 'missed_weekly_recovery' ELSE 'missed_report_recovery' END,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id
    FROM public.stewardship_delivery_digest_effect p
    WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
        AND p.occurrence_id<>p.selected_id AND o.id=p.occurrence_id;
    INSERT INTO public.stewardship_schedule_fulfillment
        (id,actor_id,correlation_id,definition_id,mode,target,slot,disposition,occurrence_id)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,p.definition_id,
            'production','admins',p.slot,'coalesced',p.selected_id
        FROM public.stewardship_delivery_digest_effect p
        WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
            AND p.occurrence_id<>p.selected_id;
    -- Do not fabricate terminal states for active workers. A queued obsolete
    -- delivery/finalizer can be cancelled; a running one sees coalesced scope at
    -- its next admission point and drains with its real lease/history.
    UPDATE public.stewardship_task_run t SET state='cancelled',action='safe_cancel',
        version=t.version+1,actor_id=intent.actor_id,correlation_id=intent.correlation_id,
        lease_expires_at=NULL
    WHERE t.state IN ('queued','retry_wait') AND (
        t.root_id IN (SELECT m.task_id FROM public.stewardship_outbox_message m
            JOIN public.stewardship_delivery_digest_message link ON link.outbox_id=m.id
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=link.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid()
                AND p.occurrence_id<>p.selected_id AND m.state='cancelled')
        OR t.task_type IN ('daily_digest_finalize','weekly_digest_finalize','report_email_finalize') AND t.domain_request_id IN (
            SELECT d.id FROM public.stewardship_daily_digest_preparation d
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=d.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND p.occurrence_id<>p.selected_id
            UNION ALL
            SELECT d.id FROM public.stewardship_weekly_digest_preparation d
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=d.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND p.occurrence_id<>p.selected_id
            UNION ALL
            SELECT d.id FROM public.stewardship_report_email_preparation d
            JOIN public.stewardship_delivery_digest_effect p ON p.occurrence_id=d.occurrence_id
            WHERE p.transaction_id=pg_current_xact_id() AND p.backend=pg_backend_pid() AND p.occurrence_id<>p.selected_id
        ));
    DELETE FROM public.stewardship_schedule_effect
        WHERE transaction_id=pg_current_xact_id() AND backend=pg_backend_pid();
    DELETE FROM public.stewardship_delivery_digest_effect
        WHERE transaction_id=pg_current_xact_id() AND backend=pg_backend_pid();
END $$;

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
       OR m.id IS NULL OR m.purpose NOT IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','admin_report')
       OR NEW.expected_version<>m.version OR NEW.correlation_id<>NEW.id
       OR btrim(NEW.evidence_note)=''
       OR (m.purpose NOT IN ('receipt','family_test','daily_digest','weekly_digest','admin_report') AND o.outbox_id IS DISTINCT FROM m.id)
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
       -- A chosen-Family test is settled from evidence only; it is never resent
       -- or retried, so an unknown test cannot block Testing cleanup forever.
       OR (m.purpose='family_test' AND NEW.action NOT IN ('note','accept','confirm_unsent'))
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
            IF m.purpose NOT IN ('receipt','family_test','daily_digest','weekly_digest','admin_report') THEN
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

CREATE OR REPLACE FUNCTION public.stewardship_delivery_resolve_closed_v1(command uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE intent public.stewardship_delivery_control%ROWTYPE;
    campaign public.stewardship_campaign%ROWTYPE;
BEGIN
    SELECT * INTO STRICT intent FROM public.stewardship_delivery_control WHERE id=$1;
    SELECT * INTO STRICT campaign FROM public.stewardship_campaign WHERE id=intent.campaign_id;
    IF intent.action<>'resolve' OR campaign.state<>'closed' OR NOT campaign.delivery_paused THEN
        RAISE EXCEPTION 'Held-message resolution requires a paused closed campaign' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
        VALUES(gen_random_uuid(),intent.actor_id,intent.correlation_id,
            'delivery_held_'||(intent.selection->>'decision'),intent.id,campaign.id);
    INSERT INTO public.stewardship_delivery_message_resolution
        (id,actor_id,correlation_id,command_id,message_id,campaign_id,previous_version,pause_version,decision)
        SELECT gen_random_uuid(),intent.actor_id,intent.correlation_id,
            intent.id,m.id,campaign.id,m.version,campaign.pause_version,intent.selection->>'decision'
        FROM public.stewardship_outbox_message m
        WHERE m.campaign_id=campaign.id AND m.mode='production' AND m.routing='production'
            AND m.purpose IN ('receipt','daily_digest','weekly_digest','admin_report')
            AND intent.selection->'types' ? m.purpose
            AND m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL;
    IF intent.selection->>'decision'='release' THEN
        UPDATE public.stewardship_outbox_message m SET
            action='release_hold',version=m.version+1,pause_hold_id=NULL,
            actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'postclose_release',intent.id,m.id,m.version)::text,'UTF8')),'hex')
        FROM public.stewardship_delivery_message_resolution decision
        WHERE decision.command_id=intent.id AND decision.message_id=m.id;
    ELSIF intent.selection->>'decision'='cancel' THEN
        UPDATE public.stewardship_outbox_message m SET
            action='cancel_unsent',state='cancelled',version=m.version+1,pause_hold_id=NULL,
            actor_id=intent.actor_id,correlation_id=intent.correlation_id,command_id=gen_random_uuid(),
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'postclose_cancel',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
            reason='admin_post_close_skip',finished_at=statement_timestamp(),
            sealed_substitutions=NULL,sealed_key_id=NULL
        FROM public.stewardship_delivery_message_resolution decision
        WHERE decision.command_id=intent.id AND decision.message_id=m.id;
        UPDATE public.stewardship_task_run t SET state='cancelled',action='safe_cancel',
            version=t.version+1,actor_id=intent.actor_id,correlation_id=intent.correlation_id,
            lease_expires_at=NULL
        WHERE t.state IN ('queued','retry_wait') AND t.root_id IN (
            SELECT m.task_id FROM public.stewardship_delivery_message_resolution decision
            JOIN public.stewardship_outbox_message m ON m.id=decision.message_id
            WHERE decision.command_id=intent.id);
        PERFORM public.stewardship_delivery_closed_settle_v1(intent.id);
    END IF;
    -- A stranded invitation or reminder (stewardship_delivery_stranded) has no
    -- task left to apply the close policy, so apply it here: cancel the unsent
    -- message as campaign_closed, as the mail worker would. Unlike the worker,
    -- which skips only a pending occurrence, also skip one left running by an
    -- ended attempt, since no worker will return to it; a failed occurrence
    -- keeps its truthful failure. The command ID is derived from this command
    -- and message, so the command identifies every message it cancelled. The
    -- target row is rechecked, so a row that changed since the view read it is
    -- left alone rather than failing the command. The preview counted these
    -- toward clearing the pause.
    WITH cancelled AS (
        UPDATE public.stewardship_outbox_message m SET
            state='cancelled',action='cancel_unsent',version=m.version+1,pause_hold_id=NULL,
            actor_id=intent.actor_id,correlation_id=intent.correlation_id,
            command_id=substr(encode(sha256(convert_to(
                'postclose_stranded:'||intent.id::text||':'||m.id::text,'UTF8')),'hex'),1,32)::uuid,
            command_digest=encode(sha256(convert_to(jsonb_build_array(
                'postclose_stranded',intent.id,m.id,m.version)::text,'UTF8')),'hex'),
            reason='campaign_closed',finished_at=statement_timestamp(),
            sealed_substitutions=NULL,sealed_key_id=NULL
        FROM public.stewardship_delivery_stranded stranded
        WHERE stranded.message_id=m.id AND stranded.campaign_id=campaign.id
            AND m.version=stranded.version AND m.state IN ('pending','retry_wait')
            AND m.pause_hold_id IS NOT NULL
        RETURNING m.id
    ) INSERT INTO public.stewardship_schedule_effect
        SELECT pg_current_xact_id(),pg_backend_pid(),o.id,o.version,
            intent.actor_id,intent.correlation_id,'campaign_closed'
        FROM cancelled JOIN public.stewardship_schedule_occurrence o ON o.outbox_id=cancelled.id
        WHERE o.state IN ('pending','running');
    UPDATE public.stewardship_schedule_occurrence o SET
        state='skipped',reason='campaign_closed',version=o.version+1,lease_expires_at=NULL,
        actor_id=intent.actor_id,correlation_id=intent.correlation_id
    FROM public.stewardship_schedule_effect proof
    WHERE proof.transaction_id=pg_current_xact_id() AND proof.backend=pg_backend_pid()
        AND proof.reason='campaign_closed' AND o.id=proof.occurrence_id;
    DELETE FROM public.stewardship_schedule_effect WHERE transaction_id=pg_current_xact_id()
        AND backend=pg_backend_pid() AND reason='campaign_closed';
    -- Clear only when every held/submitting/unknown row is gone. Releasing a
    -- selected type does not grant any other held type permission to dispatch.
    IF EXISTS(SELECT 1 FROM public.stewardship_delivery_control_inventory current
        WHERE current.campaign_id=campaign.id
            AND (inventory->>'held')::bigint=0
            AND (inventory->>'submitting')::bigint=0
            AND (inventory->>'unknown')::bigint=0) THEN
        INSERT INTO public.stewardship_campaign_control(id,campaign_id,request_id,action,
            expected_version,expected_runtime_version,reason,occurred_at,actor_id,correlation_id)
        VALUES(intent.control_id,campaign.id,intent.id,'resume',intent.expected_campaign_version,
            intent.expected_runtime_version,intent.reason,public.stewardship_campaign_now_v1(),
            intent.actor_id,intent.correlation_id);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_retry_admitted_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH resolving AS (SELECT EXISTS(SELECT 1 FROM public.stewardship_outbox_message
        WHERE id=message AND state='delivery_unknown') AS unknown)
    SELECT public.stewardship_receipt_dispatch_live_v1(message,resolving.unknown)
      OR public.stewardship_daily_dispatch_live_v1(message,resolving.unknown)
      OR public.stewardship_weekly_dispatch_live_v1(message,resolving.unknown)
      -- A report email (#1786) retries as its dispatch would send it.
      OR public.stewardship_report_email_dispatch_live_v1(message,resolving.unknown) OR EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_schedule_occurrence o ON o.id=m.semantic_key AND o.outbox_id=m.id
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id AND d.current_revision_id=o.revision_id
        JOIN public.stewardship_campaign c ON c.id=m.campaign_id
        JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        JOIN public.stewardship_source_current cur ON cur.snapshot_id=k.source_snapshot_id AND cur.generation=k.source_generation
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
        WHERE m.id=message AND r.mode=m.mode AND NOT k.population_dirty
          AND f.active AND f.email_eligible AND f.email_deliverable AND f.source_generation=cur.generation
          AND p.starts_at<=public.stewardship_campaign_now_v1() AND p.ends_at>public.stewardship_campaign_now_v1()
          AND public.stewardship_export_admitted_v1(c.id,true)
          AND ((m.mode='production' AND o.production_cycle=c.production_cycle AND c.state IN ('scheduled','active') AND (NOT c.delivery_paused OR resolving.unknown) AND f.effective_submission_id IS NULL)
            OR (m.mode='testing' AND c.state='draft' AND k.rehearsal_epoch_id=m.rehearsal_epoch_id
              AND EXISTS(SELECT 1 FROM public.stewardship_rehearsal_epoch e WHERE e.id=k.rehearsal_epoch_id AND e.state='active')
              AND NOT EXISTS(SELECT 1 FROM public.stewardship_submission s WHERE s.family_id=f.id AND s.mode='test' AND s.rehearsal_epoch_id=k.rehearsal_epoch_id)))
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment s WHERE s.definition_id=o.definition_id AND s.mode=o.mode AND s.target=o.target AND s.slot=o.slot)
          AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h WHERE h.definition_id=o.definition_id AND h.mode=o.mode AND h.target=o.target AND h.slot=o.slot AND h.state IN ('unreviewed','assumed_delivered')))
    FROM resolving
$$;

CREATE OR REPLACE FUNCTION public.stewardship_delivery_retry_render_v1(message uuid, preparation jsonb, actor uuid, command uuid)
RETURNS uuid LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE m public.stewardship_outbox_message%ROWTYPE; configuration uuid; revision uuid;
    content jsonb:=preparation->'render'; sealed jsonb:=preparation->'sealed'; rendering uuid:=gen_random_uuid();
BEGIN
    SELECT * INTO m FROM public.stewardship_outbox_message WHERE id=message;
    SELECT active_configuration_id INTO configuration FROM public.stewardship_system_configuration;
    SELECT revision_id INTO revision FROM public.stewardship_schedule_occurrence WHERE id=m.semantic_key;
    IF public.stewardship_delivery_retry_admitted_v1(message) IS NOT TRUE
       OR jsonb_typeof(preparation) IS DISTINCT FROM 'object'
       OR (m.purpose='receipt' AND preparation IS DISTINCT FROM '{"receipt":true}'::jsonb)
       OR (m.purpose='daily_digest' AND preparation IS DISTINCT FROM '{"daily_digest":true}'::jsonb)
       OR (m.purpose='weekly_digest' AND preparation IS DISTINCT FROM '{"weekly_digest":true}'::jsonb)
       OR (m.purpose='admin_report' AND preparation IS DISTINCT FROM '{"admin_report":true}'::jsonb)
       OR (m.purpose NOT IN ('receipt','daily_digest','weekly_digest','admin_report') AND (preparation-ARRAY['render','sealed']<>'{}'::jsonb
         OR jsonb_typeof(content) IS DISTINCT FROM 'object'
         OR jsonb_typeof(sealed) IS DISTINCT FROM 'object'
         OR public.stewardship_family_mail_render_admitted_v1(content,m.family_id,configuration,revision,m.mode) IS NOT TRUE
         OR sealed->>'sealed_substitutions' IS NULL OR sealed->>'sealed_key_id' IS NULL
         OR (m.mode='production' AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_campaign c
           JOIN public.stewardship_family_token_generation g ON g.id=c.active_token_generation_id
           JOIN public.stewardship_credential_deployment d ON d.family_link_epoch=g.credential_epoch
           WHERE c.id=m.campaign_id AND g.id=(sealed->>'token_generation_id')::uuid
             AND g.state='active' AND g.credential_epoch=(sealed->>'credential_epoch_id')::uuid))))
    THEN RAISE EXCEPTION 'Retry requires fresh scoped preparation' USING ERRCODE='23514'; END IF;
    IF m.purpose='receipt' THEN
        content:=public.stewardship_receipt_seed_v1(m.family_id,configuration,m.mode);
    ELSIF m.purpose='daily_digest' THEN
        content:=public.stewardship_daily_digest_seed_v1(m.id,configuration);
    ELSIF m.purpose='weekly_digest' THEN
        content:=public.stewardship_weekly_digest_seed_v1(m.id,configuration);
    ELSIF m.purpose='admin_report' THEN
        content:=public.stewardship_report_email_seed_v1(m.id,configuration);
    END IF;
    INSERT INTO public.stewardship_outbox_render
        (id,actor_id,correlation_id,message_id,configuration_id,template_id,sender,reply_to,
         intended_recipients,routed_recipients,subject,html,text,payload_digest)
    VALUES(rendering,actor,command,m.id,configuration,(content->>'template_id')::uuid,
        content->>'sender',content->>'reply_to',content->'intended_recipients',content->'routed_recipients',
        content->>'subject',content->>'html',content->>'text',content->>'payload_digest');
    RETURN rendering;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_exact_fact_pin_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- A report email's facts (#1786) are pinned to its preparation, with
    -- the snapshot that names them, by its live capture.
    IF current_user='pk_stewardship_worker' AND NEW.parent_kind='digest' THEN
        IF NOT EXISTS(SELECT 1 FROM stewardship_report_email_snapshot s
            WHERE s.preparation_id=NEW.parent_id AND s.fact_set_id=NEW.fact_set_id
              AND stewardship_report_email_live_v1(s.preparation_id,s.run_id,s.fence,s.worker_id)) THEN
            RAISE EXCEPTION 'Report fact protection requires its exact snapshot' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user='pk_stewardship_worker' AND (NEW.parent_kind<>'export'
        OR NOT stewardship_exact_handoff_v1(NEW.parent_id,true)
        OR NOT EXISTS(SELECT 1 FROM stewardship_export_request
            WHERE id=NEW.parent_id AND fact_set_id=NEW.fact_set_id)) THEN
        RAISE EXCEPTION 'Worker fact protection requires its exact export handoff' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_fact_disposable(identifier uuid) RETURNS boolean
    LANGUAGE sql
    AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_daily_fact_set old
        JOIN stewardship_daily_fact_set newer ON newer.campaign_id=old.campaign_id
            AND newer.population_scope=old.population_scope AND newer.state='ready'
            AND newer.id<>old.id AND newer.created_at>=old.created_at
            AND newer.source_generation>=old.source_generation
            AND newer.submission_watermark>=old.submission_watermark
            AND ((newer.timezone_configuration_id=old.timezone_configuration_id
                    AND newer.through_date>=old.through_date)
                OR (newer.timezone_configuration_id<>old.timezone_configuration_id
                    AND EXISTS (SELECT 1 FROM public.stewardship_campaign c
                        WHERE c.id=newer.campaign_id
                            AND c.active_configuration_id=newer.timezone_configuration_id)))
        WHERE old.id=identifier AND old.state='ready')
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pointer WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_verification_request r
        JOIN stewardship_task_run t ON t.root_id=r.task_id
        WHERE r.fact_set_id=identifier
            AND t.state IN ('queued','running','retry_wait','abandoned'))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_pin WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_request r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date)
            -- A latest-data export that has ended keeps nothing (#1398).
            AND NOT EXISTS(SELECT 1 FROM stewardship_export_discard d
                WHERE d.exact_request_id=r.id))
    -- An export still linked to the generation keeps it; an ended export's
    -- discard clears the link (#1398), so only those go.
    AND NOT EXISTS(SELECT 1 FROM stewardship_export_request WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_report_email_snapshot r WHERE r.fact_set_id=identifier)
    AND NOT EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot r
        JOIN stewardship_daily_fact_set f ON f.id=identifier
        WHERE ROW(r.campaign_id,r.population_scope,r.source_id,r.submission_watermark,
                r.timezone_configuration_id,r.through_date)=
            ROW(f.campaign_id,f.population_scope,f.source_id,f.submission_watermark,
                f.timezone_configuration_id,f.through_date))
    AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand
        WHERE claimed_generation_id=identifier);
$$;

CREATE OR REPLACE FUNCTION public.stewardship_fact_runtime_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE demand stewardship_fact_demand%ROWTYPE;
        owned_task stewardship_task_run%ROWTYPE;
        exact_request record;
BEGIN
    IF current_user<>'pk_stewardship_worker' THEN RETURN NEW; END IF;
    SELECT * INTO owned_task FROM stewardship_task_run WHERE id=NEW.task_id;
    -- A report email's capture (#1786) builds exactly its recorded inputs.
    IF owned_task.task_type='report_email_prepare' THEN
        SELECT p.* INTO exact_request FROM stewardship_report_email_preparation p
            WHERE p.id=owned_task.domain_request_id AND p.task_id=owned_task.root_id
              AND p.phase='capture';
        IF NOT FOUND OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_id,
            NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM ROW(exact_request.campaign_id,'historical'::varchar,
                exact_request.source_id,exact_request.submission_watermark,
                exact_request.timezone_configuration_id,exact_request.through_date)
            OR NOT stewardship_report_email_live_v1(exact_request.id,
                NEW.task_id,NEW.task_fence,NEW.worker_id) THEN
            RAISE EXCEPTION 'Report builder differs from its recorded inputs' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM stewardship_task_run
            WHERE id=OLD.task_id AND root_id=owned_task.root_id)
            AND NOT EXISTS(SELECT 1 FROM stewardship_task_run prior
                WHERE prior.id=OLD.task_id AND prior.task_type IN ('report_exact_export','daily_digest_prepare','report_email_prepare')
                  AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                    WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
                  AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
            RAISE EXCEPTION 'Report recovery cannot replace a recoverable root' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF owned_task.task_type='report_exact_export' THEN
        SELECT * INTO exact_request FROM stewardship_exact_export_request
            WHERE id=owned_task.domain_request_id AND task_id=owned_task.root_id;
        IF NOT FOUND OR ROW(NEW.campaign_id,NEW.population_scope,NEW.source_id,
            NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
            IS DISTINCT FROM ROW(exact_request.campaign_id,exact_request.population_scope,
                exact_request.source_id,exact_request.submission_watermark,
                exact_request.timezone_configuration_id,exact_request.through_date)
            OR NOT stewardship_export_authorized_v1(exact_request.requester_id)
            OR NOT stewardship_export_admitted_v1(exact_request.campaign_id,true)
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=exact_request.id)
            OR EXISTS(SELECT 1 FROM stewardship_exact_export_resolution WHERE request_id=exact_request.id) THEN
            RAISE EXCEPTION 'Exact builder differs from its admitted frozen request' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND NOT EXISTS(SELECT 1 FROM stewardship_task_run
            WHERE id=OLD.task_id AND root_id=owned_task.root_id)
            AND NOT EXISTS(SELECT 1 FROM stewardship_task_run prior
                WHERE prior.id=OLD.task_id AND prior.task_type IN ('report_exact_export','daily_digest_prepare','report_email_prepare')
                  AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                    WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
                  AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
            RAISE EXCEPTION 'Exact recovery cannot replace a recoverable root' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO owned_task FROM stewardship_task_run
        WHERE id=NEW.task_id AND task_type='report_facts';
    SELECT * INTO demand FROM stewardship_fact_demand WHERE id=owned_task.domain_request_id;
    IF NOT FOUND OR demand.campaign_id<>NEW.campaign_id
        OR demand.population_scope<>NEW.population_scope THEN
        RAISE EXCEPTION 'Fact builder is not bound to this demand' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF demand.claimed_generation_id IS NOT NULL
            OR ROW(NEW.source_id,NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
                IS DISTINCT FROM ROW(demand.requested_source_id,demand.requested_submission_watermark,
                    demand.requested_timezone_configuration_id,demand.requested_through_date)
            OR EXISTS(SELECT 1 FROM stewardship_daily_fact_set f
                JOIN stewardship_task_run t ON t.id=f.task_id WHERE t.root_id=owned_task.root_id) THEN
            RAISE EXCEPTION 'Fact allocation differs from its unclaimed window' USING ERRCODE='23514';
        END IF;
    ELSIF demand.claimed_generation_id IS NULL
        AND demand.pending_due_at<=clock_timestamp()
        AND ROW(NEW.source_id,NEW.submission_watermark,NEW.timezone_configuration_id,NEW.through_date)
            IS NOT DISTINCT FROM ROW(demand.requested_source_id,demand.requested_submission_watermark,
                demand.requested_timezone_configuration_id,demand.requested_through_date)
        AND EXISTS(SELECT 1 FROM stewardship_task_run prior
            WHERE prior.id=OLD.task_id AND prior.task_type IN ('report_exact_export','daily_digest_prepare','report_email_prepare')
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run active
                WHERE active.root_id=prior.root_id AND active.state IN ('queued','running','retry_wait','abandoned'))
              AND NOT EXISTS(SELECT 1 FROM stewardship_fact_demand WHERE claimed_generation_id=OLD.id)) THEN
        -- Only terminal exact work may be adopted into a new ordinary window.
        -- The normal demand guard then freezes this exact key and live fence.
        RETURN NEW;
    ELSIF demand.claimed_generation_id IS DISTINCT FROM NEW.id
        OR NOT EXISTS(SELECT 1 FROM stewardship_task_run WHERE id=OLD.task_id AND root_id=owned_task.root_id) THEN
        RAISE EXCEPTION 'Fact mutation differs from its frozen root' USING ERRCODE='23514';
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
        WHERE id=t.domain_request_id AND task_id=t.root_id AND purpose IN ('initial','reminder','receipt','family_test','admin_report','operational','security_event','leader_email');
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

CREATE OR REPLACE FUNCTION public.stewardship_family_mail_outbox_write_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
       public.stewardship_family_mail_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_family_test_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_report_email_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),NULL) IS NOT TRUE
       AND public.stewardship_ops_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_security_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE
       AND public.stewardship_leader_email_prepare_write_v1(TG_TABLE_NAME,to_jsonb(NEW)) IS NOT TRUE THEN
        RAISE EXCEPTION 'Outbox insertion requires current preparation ownership'
            USING ERRCODE='23514';
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
       OR m.purpose NOT IN ('initial','reminder','receipt','family_test','daily_digest','weekly_digest','admin_report','operational','security_event','leader_email') OR e.attempt<1
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

CREATE OR REPLACE FUNCTION public.stewardship_fulfillment_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Keep report private metadata out of query plans for unrelated mail
    -- roles: a report send's empty outcome (#1786) is proved by its own
    -- completion view, nothing to report or no current recipient.
    IF NEW.disposition='empty' AND EXISTS(SELECT 1 FROM stewardship_schedule_definition
        WHERE id=NEW.definition_id AND kind='report') THEN
        IF NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot=''
          OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
            JOIN stewardship_report_email_preparation p ON p.occurrence_id=o.id
            JOIN stewardship_report_email_completion_ready proof ON proof.preparation_id=p.id
            WHERE o.id=NEW.occurrence_id AND o.definition_id=NEW.definition_id
              AND o.mode=NEW.mode AND o.target=NEW.target AND o.slot=NEW.slot
              AND o.state='succeeded' AND proof.disposition IN ('empty','nothing_to_report')
              AND o.reason=public.stewardship_report_email_reason_v1(proof.disposition)) THEN
            RAISE EXCEPTION 'Fulfillment requires exact semantic outcome evidence' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot='' OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence o JOIN stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN stewardship_schedule_definition wanted ON wanted.id=NEW.definition_id
        WHERE o.id=NEW.occurrence_id AND o.mode=NEW.mode AND d.campaign_id=wanted.campaign_id
          AND ((NEW.disposition='delivered' AND o.state='succeeded'
                AND o.reason IS DISTINCT FROM 'report_email_no_current_recipients'
                AND o.reason IS DISTINCT FROM 'report_email_nothing_to_report'
                AND o.definition_id=NEW.definition_id AND o.target=NEW.target AND o.slot=NEW.slot)
            OR (NEW.disposition='coalesced' AND EXISTS (SELECT 1 FROM stewardship_schedule_occurrence original
                WHERE original.definition_id=NEW.definition_id AND original.mode=NEW.mode AND original.target=NEW.target AND original.slot=NEW.slot
                  AND original.state='coalesced' AND original.replacement_id=o.id)))
    ) THEN RAISE EXCEPTION 'Fulfillment requires exact semantic outcome evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_occurrence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE d stewardship_schedule_definition%ROWTYPE; c stewardship_campaign%ROWTYPE;
    r stewardship_system_configuration%ROWTYPE; t stewardship_task_run%ROWTYPE;
    instant timestamptz := stewardship_campaign_now_v1();
    digest_completed boolean := false;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='DELETE' THEN
        IF public.stewardship_cleanup_effect_v1('occurrences',OLD.id) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Occurrence history requires exceptional retention' USING ERRCODE='23514';
    END IF;
    SELECT * INTO d FROM stewardship_schedule_definition WHERE id=NEW.definition_id FOR UPDATE;
    SELECT * INTO c FROM stewardship_campaign WHERE id=d.campaign_id;
    SELECT * INTO r FROM stewardship_system_configuration;
    IF NOT EXISTS(SELECT 1 FROM stewardship_schedule_revision WHERE id=NEW.revision_id AND record_id=d.id AND campaign_id=d.campaign_id)
       OR NEW.target='' OR NEW.slot='' OR NEW.occurrence_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid occurrence identity' USING ERRCODE='23514'; END IF;
    IF (TG_OP='INSERT' OR NEW.state IN ('pending','running'))
       AND NEW.production_cycle IS DISTINCT FROM (CASE WHEN NEW.mode='production' THEN c.production_cycle ELSE 0 END) THEN
        RAISE EXCEPTION 'Occurrence belongs to a retired Production cycle' USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('succeeded','failed','skipped','coalesced') AND EXISTS (
        SELECT 1 FROM stewardship_outbox_message m WHERE m.id=NEW.outbox_id
          AND public.stewardship_occurrence_delivery_conflict_v1(NEW.state,m.state)
    ) THEN RAISE EXCEPTION 'Occurrence outcome contradicts terminal delivery'
        USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NOT public.stewardship_initial_recovery_valid_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Initial recovery requires a new deliverability transition'
                USING ERRCODE='23514';
        END IF;
        IF NEW.state<>'pending' OR NEW.version<>1 OR NEW.fence<>0 OR NEW.attempts<>0
           OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
           OR c.id IS DISTINCT FROM r.current_campaign_id OR r.restore_review_required
           OR NEW.mode<>r.mode OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
           -- A report email (#1786) may send before its campaign starts and
           -- after it closes, until it is archived (decision 9).
           OR (d.kind='report' AND NOT ((NEW.mode='testing' AND c.state='draft')
               OR (NEW.mode='production' AND c.state IN ('scheduled','active','closed'))))
           OR (d.kind<>'report' AND NOT EXISTS (SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
               AND ((instant>=p.starts_at AND instant<p.ends_at
                   AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                   OR (NEW.mode='production' AND c.state='closed'
                       AND NEW.due_at>=p.starts_at AND NEW.due_at<p.ends_at))))
           OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL)
               AND NOT public.stewardship_catchup_materializing_v1(c.id,NEW.actor_id,NEW.correlation_id))
           OR EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
           OR EXISTS (SELECT 1 FROM stewardship_postclose_resolution resolved
               JOIN public.stewardship_postclose_current covered ON covered.id=resolved.id
               WHERE resolved.campaign_id=c.id AND resolved.mode=NEW.mode
                   AND resolved.obligation_key='schedule:'||d.id::text||':'||NEW.slot)
           -- A report's skipped send (#1658) is never planned, by any writer.
           OR (d.kind='report' AND EXISTS (
               SELECT 1 FROM stewardship_schedule_revision skipping WHERE skipping.id=NEW.revision_id
                   AND coalesce(skipping.values->'skip_dates' ? NEW.slot,false))) THEN
            RAISE EXCEPTION 'Occurrence creation is not admitted' USING ERRCODE='23514'; END IF;
    ELSE
        IF session_user='pk_stewardship_scheduler' AND (
            OLD.state<>'pending' OR NEW.state NOT IN ('skipped','coalesced')
            OR OLD.task_id IS NOT NULL OR OLD.outbox_id IS NOT NULL
            OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
            OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode
            OR r.restore_review_required
            OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate
                WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone'))
            OR (NEW.state='coalesced' AND NOT EXISTS(
                SELECT 1 FROM stewardship_schedule_occurrence selected
                WHERE selected.id=NEW.replacement_id AND selected.target=NEW.target
                  AND selected.mode=NEW.mode AND selected.state='pending'
                  AND selected.task_id IS NULL AND selected.outbox_id IS NULL
            ))
        ) THEN RAISE EXCEPTION 'Scheduler may only reconcile unallocated pending work'
            USING ERRCODE='23514'; END IF;
        IF (OLD.state='pending' AND NEW.state NOT IN ('pending','running','skipped','coalesced'))
           OR (OLD.state='running' AND NEW.state NOT IN ('running','pending','delivery_unknown','succeeded','failed','skipped','coalesced'))
           OR (OLD.state='delivery_unknown' AND NEW.state NOT IN ('succeeded','pending','failed'))
           OR (OLD.state='failed' AND NEW.state NOT IN ('pending','skipped'))
           OR OLD.state IN ('succeeded','skipped','coalesced') THEN
            RAISE EXCEPTION 'Invalid occurrence transition' USING ERRCODE='23514'; END IF;
        IF NEW.state='running' THEN
            -- Branch before planning private report reads: SQL boolean order is
            -- not a privilege boundary for unrelated Family mail owners.
            IF d.kind='report' THEN
                digest_completed:=public.stewardship_report_email_completion_v1(to_jsonb(NEW));
            END IF;
        END IF;
        IF NEW.state='running' AND NOT digest_completed THEN
            SELECT * INTO t FROM stewardship_task_run WHERE id=NEW.task_id FOR UPDATE;
            IF NOT FOUND OR t.state<>'running' OR t.worker_id<>NEW.worker_id OR t.fence<>NEW.fence OR t.lease_expires_at<=clock_timestamp()
               OR NEW.lease_expires_at>t.lease_expires_at OR NEW.heartbeat_at>clock_timestamp()
               OR NEW.revision_id IS DISTINCT FROM d.current_revision_id
               OR c.id IS DISTINCT FROM r.current_campaign_id OR NEW.mode<>r.mode OR r.restore_review_required
               OR NOT ((t.task_type='schedule_occurrence' AND t.domain_request_id IS NOT DISTINCT FROM NEW.id)
                   OR (t.task_type='family_mail_prepare' AND EXISTS (
                       SELECT 1 FROM public.stewardship_family_mail_preparation q
                       WHERE q.id=t.domain_request_id AND q.task_id=t.root_id
                         AND q.occurrence_id=NEW.id AND q.mode=NEW.mode))
                   OR (t.task_type='outbox_delivery' AND EXISTS (
                       SELECT 1 FROM public.stewardship_outbox_message mail
                       WHERE mail.id=t.domain_request_id AND mail.task_id=t.root_id
                         AND mail.id=NEW.outbox_id AND mail.semantic_key=NEW.id
                         AND mail.mode=NEW.mode AND mail.family_id::text=substring(NEW.target FROM 8))))
               OR NOT EXISTS(SELECT 1 FROM stewardship_campaign_configuration p WHERE p.id=c.active_configuration_id
                   AND ((instant>=p.starts_at AND instant<p.ends_at
                       AND ((NEW.mode='testing' AND c.state='draft') OR (NEW.mode='production' AND c.state IN ('scheduled','active'))))
                       OR (NEW.mode='production' AND c.state='closed' AND d.kind='report')))
               -- Not yet due. Only Family mail preparation of a Production
               -- occurrence may claim it early, within the two-hour lead
               -- window (BG-12, #447). Every other claim, including a
               -- delivery claim, and the dispatch guard still wait for it.
               OR (NEW.due_at>instant AND NOT (t.task_type='family_mail_prepare'
                   AND NEW.mode='production' AND NEW.due_at<=instant+interval '2 hours'))
               OR (NEW.mode='production' AND c.delivery_paused)
               OR (NEW.mode='production' AND EXISTS(SELECT 1 FROM stewardship_activation_catchup WHERE campaign_id=c.id AND completed_at IS NULL))
               OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment WHERE definition_id=d.id AND mode=NEW.mode AND target=NEW.target AND slot=NEW.slot)
               OR EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h WHERE h.definition_id=d.id AND h.mode=NEW.mode
                   AND h.target=NEW.target AND h.slot=NEW.slot AND h.state IN ('unreviewed','assumed_delivered'))
               OR EXISTS(SELECT 1 FROM stewardship_campaign_work_gate WHERE campaign_id=c.id AND state IN ('preparing','running','tombstone')) THEN
                RAISE EXCEPTION 'Occurrence claim requires current fenced work' USING ERRCODE='23514'; END IF;
        END IF;
        IF OLD.state='running' AND (NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NEW.fence<>OLD.fence) THEN
            RAISE EXCEPTION 'Occurrence worker identity changed' USING ERRCODE='23514'; END IF;
        IF OLD.state='running' AND NOT (
            (NEW.state='skipped' OR (NEW.state='coalesced' AND NEW.reason IN (
                'missed_family_recovery','missed_daily_recovery','missed_weekly_recovery',
                'missed_report_recovery')))
            AND public.stewardship_schedule_effect_v1(
                OLD.id,OLD.version,NEW.actor_id,NEW.correlation_id,NEW.reason)
        ) AND NOT EXISTS(SELECT 1 FROM stewardship_task_run owner_task WHERE owner_task.id=OLD.task_id
            AND ((owner_task.state='running' AND owner_task.worker_id=NEW.actor_id AND owner_task.fence=NEW.fence AND owner_task.lease_expires_at>clock_timestamp())
                OR (owner_task.state IN ('abandoned','cancelled','succeeded','failed') AND owner_task.fence>=OLD.fence AND NEW.actor_id IS NOT NULL
                    AND NEW.reason IN ('recovery_retry','recovery_unknown','recovery_complete','recovery_fail','recovery_skip','recovery_coalesce')))) THEN
            RAISE EXCEPTION 'Occurrence write requires live worker fencing' USING ERRCODE='23514'; END IF;
        IF NEW.state<>'running' AND OLD.state<>'running' AND NEW.fence<>OLD.fence THEN
            RAISE EXCEPTION 'Only a new claim advances occurrence fencing' USING ERRCODE='23514'; END IF;
        IF OLD.state='delivery_unknown' AND (
            NEW.actor_id IS NULL
            OR NEW.reason IS DISTINCT FROM CASE NEW.state WHEN 'pending' THEN 'recovery_retry'
                WHEN 'succeeded' THEN 'recovery_complete' WHEN 'failed' THEN 'recovery_fail' END
            OR NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
            OR (NOT public.stewardship_delivery_resolution_edge_v1(to_jsonb(OLD),to_jsonb(NEW))
              AND NOT EXISTS(SELECT 1 FROM stewardship_task_run reconciled WHERE reconciled.id=OLD.task_id
                AND reconciled.task_type='schedule_occurrence' AND reconciled.domain_request_id=OLD.id
                AND reconciled.fence>=OLD.fence AND reconciled.state IN ('abandoned','cancelled','succeeded','failed')))
        ) THEN RAISE EXCEPTION 'Unknown delivery requires attributed reconciled ownership' USING ERRCODE='23514'; END IF;
        IF NEW.state='pending' AND OLD.state='failed' AND (NEW.retry_command_id IS NULL OR NEW.revision_id IS DISTINCT FROM d.current_revision_id) THEN
            RAISE EXCEPTION 'Occurrence retry requires explicit current identity' USING ERRCODE='23514'; END IF;
        IF NEW.state='skipped' AND OLD.state='failed' AND NEW.reason NOT IN ('schedule_removed','schedule_replaced') THEN
            RAISE EXCEPTION 'Failed occurrence cannot be silently discarded' USING ERRCODE='23514'; END IF;
        IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state<>'running' AND NEW.state='running' THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Occurrence attempt count is inconsistent' USING ERRCODE='23514'; END IF;
    END IF;
    IF NEW.state='coalesced' AND (NEW.replacement_id=NEW.id OR NOT EXISTS (
        SELECT 1 FROM stewardship_schedule_occurrence replacement JOIN stewardship_schedule_definition rd ON rd.id=replacement.definition_id
        WHERE replacement.id=NEW.replacement_id AND rd.campaign_id=d.campaign_id AND replacement.mode=NEW.mode
          AND replacement.state NOT IN ('skipped','coalesced','failed')
    )) THEN RAISE EXCEPTION 'Invalid occurrence coalescing target' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $_$;

CREATE OR REPLACE FUNCTION public.stewardship_outbox_render_shape_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp AS $$
DECLARE
    recipients jsonb;
    body_limit integer;
BEGIN
    -- Only a report email (#1786; a weekly digest before it) may retain the
    -- separately bounded multi-Family compiled report. Unknown owners and
    -- other purposes keep the original cap.
    body_limit:=CASE WHEN EXISTS(SELECT 1 FROM stewardship_outbox_message
        WHERE id=NEW.message_id AND purpose IN ('weekly_digest','admin_report','admin_report_test'))
        THEN 8388608 ELSE 1048576 END;
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

CREATE OR REPLACE FUNCTION public.stewardship_policy_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected bigint;
    actual bigint;
    rule public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF NEW.validation_schema NOT IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'bootstrap-users-v10', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11', 'campaign-series-v12') THEN RETURN NULL; END IF;
    expected := jsonb_array_length(NEW.canonical_document->'sections'->'login_rules');
    SELECT (SELECT count(*) FROM public.stewardship_domain_rule
            WHERE configuration_id = NEW.id)
         + (SELECT count(*) FROM public.stewardship_address_rule
            WHERE configuration_id = NEW.id)
         + (SELECT count(*) FROM public.stewardship_ministry_assignment
            WHERE configuration_id = NEW.id)
    INTO actual;
    IF expected IS NULL OR actual <> expected OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id AND roles ? 'administrator'
    ) THEN
        RAISE EXCEPTION 'Policy must be complete and retain an Administrator'
            USING ERRCODE = '23514';
    END IF;
    -- The users form (#952) and its bootstrap (#993) hold only users, and
    -- only they hold them.
    IF EXISTS (SELECT 1 FROM public.stewardship_address_rule
               WHERE configuration_id = NEW.id
                 AND (role IS NOT NULL)
                     <> (NEW.validation_schema IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12', 'bootstrap-users-v10')))
       OR (NEW.validation_schema IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12', 'bootstrap-users-v10') AND (
           EXISTS (SELECT 1 FROM public.stewardship_domain_rule
                   WHERE configuration_id = NEW.id)
           OR EXISTS (SELECT 1 FROM public.stewardship_ministry_assignment
                      WHERE configuration_id = NEW.id))) THEN
        RAISE EXCEPTION 'Policy form does not match the configuration schema'
            USING ERRCODE = '23514';
    END IF;
    FOR rule IN SELECT * FROM public.stewardship_address_rule
        WHERE configuration_id = NEW.id AND role IS NULL LOOP
        IF (SELECT count(*) FROM public.stewardship_address_grant
            WHERE rule_id = rule.id)
           <> jsonb_array_length(rule.roles) THEN
            RAISE EXCEPTION 'Policy grant provenance is incomplete'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_policy_projection_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    expected jsonb;
    actual jsonb;
    config uuid;
    record uuid;
    parent public.stewardship_address_rule%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'stewardship_address_grant' THEN
        SELECT * INTO parent FROM public.stewardship_address_rule
        WHERE id = NEW.rule_id;
        config := parent.configuration_id;
        record := parent.record_id;
    ELSE
        config := NEW.configuration_id;
        record := NEW.record_id;
    END IF;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version version,
        jsonb_array_elements(version.canonical_document->'sections'->'login_rules') item
    WHERE version.id = config AND version.validation_schema IN ('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1', 'bootstrap-users-v10', 'ministry-activity-v4', 'campaign-content-v5', 'source-cadence-v8', 'users-v10', 'campaign-series-v11', 'campaign-series-v12')
        AND item->>'id' = record::text;
    IF expected IS NULL THEN
        RAISE EXCEPTION 'Policy projection requires matching YAML record'
            USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'stewardship_domain_rule' THEN
        actual := jsonb_build_object('kind', 'domain', 'domain', NEW.domain,
            'roles', NEW.roles);
        IF NEW.domain <> lower(NEW.domain) OR NEW.domain = 'gmail.com'
           OR NEW.roles = '[]'::jsonb
           OR NOT NEW.roles <@ '["staff", "ministry_leader"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid hosted domain grant' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_rule'
          AND to_jsonb(NEW)->>'role' IS NOT NULL THEN
        -- A user (#952): its YAML entry is the email, Name and role. The
        -- cumulative roles follow from the role (address_rule_shape). The
        -- test reads role through to_jsonb: PL/pgSQL does not short-circuit
        -- AND, and this trigger also fires on tables without that column.
        actual := jsonb_build_object('kind', 'user', 'email', NEW.email,
            'name', NEW.name, 'role', NEW.role);
        IF NEW.email <> lower(btrim(NEW.email)) THEN
            RAISE EXCEPTION 'Invalid user' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_rule' THEN
        actual := jsonb_build_object('kind', 'address', 'email', NEW.email,
            'roles', NEW.roles, 'creation_origin', NEW.creation_origin,
            'creation_operation', NEW.creation_operation);
        expected := expected - 'grants';
        IF NEW.email <> lower(NEW.email)
           OR NEW.creation_origin NOT IN ('manual', 'chair-seed')
           OR NOT NEW.roles <@
               '["administrator", "staff", "ministry_leader"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid address grant' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'stewardship_address_grant' THEN
        actual := NEW.origins;
        expected := expected->'grants'->NEW.role;
        IF expected IS NULL OR NEW.origins = '{}'::jsonb
           OR NEW.role NOT IN ('administrator', 'staff', 'ministry_leader')
           OR (NEW.origins ? 'chair-seed' AND NEW.role <> 'ministry_leader') THEN
            RAISE EXCEPTION 'Invalid grant provenance' USING ERRCODE = '23514';
        END IF;
    ELSE
        actual := jsonb_build_object('kind', 'assignment', 'email', NEW.email,
            'ministry_duid', NEW.ministry_duid, 'source', NEW.source,
            'operation_id', NEW.operation_id);
        IF NEW.email <> lower(NEW.email) OR NEW.source NOT IN ('manual', 'chair-seed')
           OR NEW.ministry_duid < 1 THEN
            RAISE EXCEPTION 'Invalid ministry assignment' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Policy projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_postclose_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    -- The closed-message owner may resolve actual receipt/fanout work. Its
    -- private immutable decisions prove exact safe cancellation, including a
    -- worker which retains a truthful running lease but can no longer submit.
    IF NEW.mode='production' AND NEW.coverage_digest=encode(sha256(convert_to(NEW.coverage::text,'UTF8')),'hex')
       AND public.stewardship_coverage_valid_v1(NEW.coverage) AND EXISTS(
        SELECT 1 FROM public.stewardship_delivery_message_resolution decision
        JOIN public.stewardship_delivery_control command ON command.id=decision.command_id
        JOIN public.stewardship_delivery_closed_coverage covered ON covered.message_id=decision.message_id
        JOIN public.stewardship_outbox_message m ON m.id=decision.message_id
        JOIN public.stewardship_campaign c ON c.id=command.campaign_id
        WHERE decision.decision='cancel' AND command.action='resolve'
            AND command.actor_id=NEW.actor_id AND command.correlation_id=NEW.correlation_id
            AND command.reason=NEW.reason AND c.id=NEW.campaign_id AND c.state='closed'
            AND covered.obligation_key=NEW.obligation_key
            AND m.state='cancelled' AND m.reason='admin_post_close_skip'
            AND ((covered.purpose='receipt' AND covered.coverage=NEW.coverage AND NEW.occurrence_id IS NULL
                AND NEW.outbox_id=m.id AND NEW.task_id=m.task_id)
              OR (covered.occurrence_id=NEW.occurrence_id AND NEW.outbox_id IS NULL
                AND NEW.coverage=public.stewardship_delivery_closed_slot_coverage_v1(NEW.occurrence_id)
                AND public.stewardship_delivery_closed_digest_v1(NEW.occurrence_id)
                AND EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
                    WHERE o.id=NEW.occurrence_id AND o.state='skipped' AND o.reason='admin_post_close_skip'
                        AND NEW.task_id IS NOT DISTINCT FROM o.task_id)))
       ) THEN RETURN NEW; END IF;
    IF NEW.actor_id IS NULL OR NEW.mode NOT IN ('testing','production') OR NEW.obligation_key='' OR btrim(NEW.reason)=''
       OR stewardship_coverage_valid_v1(NEW.coverage) IS DISTINCT FROM true
       OR NEW.coverage_digest<>encode(sha256(convert_to(NEW.coverage::text,'UTF8')),'hex')
       OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c JOIN stewardship_system_configuration r ON r.current_campaign_id=c.id
           WHERE c.id=NEW.campaign_id AND c.state='closed' AND NEW.mode=r.mode AND NOT r.restore_review_required)
       OR NEW.occurrence_id IS NULL OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
           JOIN stewardship_schedule_definition d ON d.id=o.definition_id WHERE o.id=NEW.occurrence_id
           AND d.campaign_id=NEW.campaign_id AND d.kind='report'
           AND o.mode=NEW.mode AND o.state='skipped' AND o.reason='admin_post_close_skip'
           AND NEW.obligation_key='schedule:'||d.id::text||':'||o.slot
           AND NEW.task_id IS NOT DISTINCT FROM o.task_id AND NEW.outbox_id IS NOT DISTINCT FROM o.outbox_id)
       OR (NEW.task_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stewardship_task_run t
           WHERE t.id=NEW.task_id AND t.state='cancelled' AND t.domain_request_id=NEW.occurrence_id)) THEN
        RAISE EXCEPTION 'Post-close skip requires exact coverage and cancellation evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_recovery_replacement_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE previous public.stewardship_schedule_occurrence%ROWTYPE;
        replacement public.stewardship_schedule_occurrence%ROWTYPE;
        demand public.stewardship_activation_catchup%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO demand FROM public.stewardship_activation_catchup WHERE id=NEW.demand_id;
    SELECT * INTO previous FROM public.stewardship_schedule_occurrence WHERE id=NEW.previous_id FOR UPDATE;
    SELECT * INTO replacement FROM public.stewardship_schedule_occurrence WHERE id=NEW.replacement_id FOR UPDATE;
    IF NEW.preparation_id IS NOT NULL THEN
        -- A report email's cover page (#1786); the digests' replaced it.
        IF NEW.demand_id IS NOT NULL
           OR NOT public.stewardship_report_email_replacement_v1(to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'Recovery replacement requires current report ownership' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF demand.id IS NULL OR demand.completed_at IS NOT NULL
        OR previous.id IS NULL OR replacement.id IS NULL
        OR previous.revision_id=replacement.revision_id
        OR previous.mode<>'production' OR replacement.mode<>'production'
        OR previous.target<>replacement.target
        OR previous.state<>'skipped' OR previous.reason NOT IN ('schedule_replaced','schedule_removed')
        OR replacement.state<>'pending' OR replacement.task_id IS NOT NULL
        OR replacement.outbox_id IS NOT NULL
        OR public.stewardship_schedule_slot_excluded_v1(
            replacement.definition_id,replacement.mode,replacement.target,replacement.slot)
        OR NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_definition s
            JOIN public.stewardship_schedule_definition old_s ON old_s.id=previous.definition_id
            WHERE s.id=replacement.definition_id AND s.campaign_id=demand.campaign_id
                AND old_s.campaign_id=s.campaign_id AND s.current_revision_id=replacement.revision_id
                AND ((previous.target='admins' AND s.kind='report'
                        AND previous.definition_id=s.id AND previous.reason='schedule_replaced')
                    OR (starts_with(previous.target,'family:') AND s.kind IN ('initial','reminder')
                        AND old_s.kind IN ('initial','reminder'))))
        OR NOT (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                       WHERE f.occurrence_id=previous.id AND f.disposition='coalesced')
                OR EXISTS(SELECT 1 FROM public.stewardship_recovery_replacement link
                          WHERE link.replacement_id=previous.id AND link.demand_id=demand.id))
        OR NOT public.stewardship_catchup_write_admitted_v1(
            'stewardship_schedule_occurrence',
            to_jsonb(replacement)||jsonb_build_object('actor_id',NEW.actor_id,'correlation_id',NEW.correlation_id),NULL)
    THEN RAISE EXCEPTION 'Recovery replacement requires current owned cancelled coverage'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_report_email_manual_guard_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
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
      -- Nor while an earlier send's email is still being delivered or its
      -- delivery is uncertain (spec, "Send now"; #1786's conversion, as the
      -- Weekly digest's manual report waited for its prior work).
      OR EXISTS(SELECT 1 FROM public.stewardship_report_email_preparation p
          JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
          JOIN public.stewardship_report_email_recipient r ON r.snapshot_id=s.id
          JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
          WHERE p.definition_id=d.id AND p.mode=runtime.mode
            AND m.state NOT IN ('delivered','permanent_failure','cancelled'))
      OR NOT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=NEW.task_id AND t.root_id=t.id
          AND t.task_type='report_email_prepare' AND t.domain_request_id=NEW.id
          AND t.idempotency_key=NEW.id::text AND t.state='queued'
          AND t.initiated_by_id=NEW.actor_id AND t.correlation_id=NEW.id)
    THEN RAISE EXCEPTION 'Report manual request requires the current report and no open send'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_report_email_preparation_guard_v1() RETURNS trigger
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
          -- Captured in this transaction, on the campaign clock (#1786's
          -- conversion: the clock the capture reads, which a test may fix).
          OR NEW.captured_at NOT BETWEEN public.stewardship_campaign_now_v1()
              -(statement_timestamp()-transaction_timestamp()) AND public.stewardship_campaign_now_v1()
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

CREATE OR REPLACE FUNCTION public.stewardship_report_email_snapshot_guard_v1() RETURNS trigger
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
            -- A content ID may hold '@', as the participation chart's does
            -- (participation@parishkit), which file 0156 refused (#1786).
            OR image->>0 !~ '^[A-Za-z0-9._@-]{1,64}$'
            OR substring(decode(image->>1,'base64') FROM 1 FOR 8)<>decode('89504e470d0a1a0a','hex')
            OR octet_length(decode(image->>1,'base64'))>1048576)
    THEN RAISE EXCEPTION 'Report snapshot lacks its exact capture' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_request_checkpoint_v2() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    DECLARE
        intent stewardship_config_request%ROWTYPE;
        previous stewardship_config_checkpoint%ROWTYPE;
    BEGIN
        SELECT * INTO intent FROM stewardship_config_request
        WHERE id = NEW.request_id FOR UPDATE;
        IF NOT FOUND OR NEW.actor_id IS DISTINCT FROM intent.actor_id
           OR NEW.created_at < intent.created_at THEN
            RAISE EXCEPTION 'Invalid configuration checkpoint attribution'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO previous FROM stewardship_config_checkpoint
        WHERE request_id = NEW.request_id ORDER BY sequence DESC LIMIT 1;
        IF NOT FOUND THEN
            IF NEW.sequence <> 1 OR NEW.state <> 'staged' THEN
                RAISE EXCEPTION 'Configuration intake must start staged'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.sequence <> previous.sequence + 1
               OR NEW.created_at < previous.created_at
               OR NOT (
                   (previous.state = 'staged'
                    AND NEW.state IN ('cancelled', 'validating'))
                   OR (previous.state = 'validating'
                       AND NEW.state IN ('prepared', 'failed'))
                   OR (previous.state = 'prepared'
                       AND NEW.state IN ('yaml_activated', 'failed'))
                   OR (previous.state = 'yaml_activated'
                       AND (NEW.state = 'applied'
                        -- The activation transaction refused the confirming
                        -- Administrator, who is no longer one; the installer
                        -- restores the base selection and records the refusal.
                        -- Never from the web role: its own checkpoint grant
                        -- must not be able to strand a selected candidate.
                        OR (NEW.state='failed' AND NEW.failure_code='actor_unauthorized'
                            AND current_user<>'pk_stewardship_web')
                         -- A Chairperson confirmation whose seed the promoted
                         -- source no longer shows is refused inside the
                         -- activation the same way and restored the same way.
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                            AND intent.request_schema='chair-seed-patch-v9'
                            AND current_user<>'pk_stewardship_web')
                         -- SQL's users-form guard refused the offline users
                         -- upgrade (#952), or SQL refused the report
                         -- conversion (#1786), inside its activation: refused and
                         -- restored the same way, so the manifest never stays
                         -- ahead of the database and recovery still runs.
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                            AND intent.request_schema IN ('operator-upgrade-users-v10',
                                'operator-upgrade-reports-v12')
                            AND current_user<>'pk_stewardship_web')
                         -- A Ministry added to a live campaign that a source
                         -- promotion dropped, or that turned inactive, after
                         -- preflight (#342) is refused and restored the same
                         -- way. Only a request whose one operation changes
                         -- nothing but a structurally locked campaign's
                         -- ministry_duids qualifies.
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                            AND jsonb_typeof(intent.patch)='array'
                            AND jsonb_array_length(intent.patch)=1
                            AND intent.patch->0->>'section'='campaigns'
                            AND intent.patch->0->>'operation'='update'
                            AND intent.patch->0->'values' ? 'ministry_duids'
                            AND (intent.patch->0->'values') - 'ministry_duids'::text = '{}'::jsonb
                            AND EXISTS (SELECT 1 FROM stewardship_campaign c
                                WHERE c.id::text=intent.patch->0->>'id' AND c.structural_locked)
                            AND current_user<>'pk_stewardship_web')
                        OR (NEW.state='failed' AND NEW.failure_code='invalid_candidate'
                        AND (EXISTS(SELECT 1 FROM stewardship_campaign_config_abort b
                            JOIN stewardship_campaign_config_intent i ON i.id=b.intent_id WHERE i.request_id=NEW.request_id) OR EXISTS (
    SELECT 1 FROM public.stewardship_setup_config_abort b
    JOIN public.stewardship_setup_config_intent i ON i.id=b.intent_id
    WHERE i.request_id=NEW.request_id)))))
               ) THEN
                RAISE EXCEPTION 'Invalid configuration installer transition'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF NEW.state IN ('prepared', 'yaml_activated', 'applied')
           AND NOT EXISTS (
               SELECT 1 FROM stewardship_configuration_version
               WHERE id = intent.candidate_version_id
                 AND digest = intent.candidate_digest
                 AND predecessor_id = intent.base_id
           ) THEN
            RAISE EXCEPTION 'Checkpoint requires matching prepared candidate'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.state = 'applied' AND NOT EXISTS (
            SELECT 1 FROM stewardship_config_activation activation
            JOIN stewardship_system_configuration runtime
              ON runtime.active_configuration_id = activation.configuration_id
            WHERE activation.request_id = NEW.request_id
        ) THEN
            RAISE EXCEPTION 'Applied checkpoint requires matching activation'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$;

CREATE OR REPLACE FUNCTION public.stewardship_schedule_definition_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."campaign_id" IS DISTINCT FROM OLD."campaign_id" OR (NEW."kind" IS DISTINCT FROM OLD."kind" AND NOT (NEW."kind"='report' AND OLD."kind" IN ('daily_digest','weekly_digest'))) THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

CREATE OR REPLACE FUNCTION public.stewardship_schedule_definition_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE r stewardship_system_configuration%ROWTYPE; chosen stewardship_schedule_revision%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Schedules are removed through configuration' USING ERRCODE='23514'; END IF;
    SELECT * INTO r FROM stewardship_system_configuration;
    -- The report conversion's kind-only retyping (#1786) of a digest the
    -- applied report configuration no longer selects, in any campaign.
    IF TG_OP='UPDATE' AND OLD.kind IN ('daily_digest','weekly_digest') AND NEW.kind='report'
       AND NEW.current_revision_id IS NOT DISTINCT FROM OLD.current_revision_id
       AND NEW.actor_id IS NOT DISTINCT FROM r.actor_id AND NEW.correlation_id=r.correlation_id
       AND NOT r.restore_review_required
       AND EXISTS(SELECT 1 FROM stewardship_configuration_version
           WHERE id=r.active_configuration_id AND validation_schema='campaign-series-v12') THEN
        RETURN NEW;
    END IF;
    IF r.restore_review_required THEN
        RAISE EXCEPTION 'Restore review holds schedule selection changes' USING ERRCODE='23514'; END IF;
    SELECT * INTO chosen FROM stewardship_schedule_revision
        WHERE configuration_id=r.active_configuration_id AND record_id=NEW.id;
    IF NEW.campaign_id IS DISTINCT FROM r.current_campaign_id
       OR NEW.actor_id IS DISTINCT FROM r.actor_id OR NEW.correlation_id<>r.correlation_id
       OR (NEW.current_revision_id IS NOT NULL AND (chosen.id IS NULL OR chosen.id<>NEW.current_revision_id OR chosen.kind<>NEW.kind OR chosen.campaign_id<>NEW.campaign_id))
       OR (NEW.current_revision_id IS NULL AND chosen.id IS NOT NULL) THEN
        RAISE EXCEPTION 'Schedule selection requires exact applied configuration' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND NEW.current_revision_id IS NOT DISTINCT FROM OLD.current_revision_id THEN
        RAISE EXCEPTION 'Schedule selection must change' USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND EXISTS (
        SELECT 1 FROM stewardship_schedule_work_row o
        WHERE o.definition_id=NEW.id AND o.revision_id=OLD.current_revision_id AND o.blocking
    ) THEN RAISE EXCEPTION 'In-flight schedule work blocks replacement' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_schedule_message_ids_v1(definition uuid,revision uuid)
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
    UNION
    SELECT recipient.outbox_id FROM public.stewardship_report_email_preparation p
    JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_report_email_recipient recipient ON recipient.snapshot_id=s.id
    WHERE p.definition_id=$1 AND p.revision_id=$2 AND recipient.outbox_id IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION public.stewardship_schedule_selection_effect_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE config uuid; prior uuid; evidence jsonb; event uuid:=gen_random_uuid();
BEGIN
    -- The report conversion's kind-only retyping (#1786) selects nothing.
    IF TG_OP='UPDATE' AND NEW.current_revision_id IS NOT DISTINCT FROM OLD.current_revision_id
       AND OLD.kind IN ('daily_digest','weekly_digest') AND NEW.kind='report' THEN
        RETURN NEW;
    END IF;
    SELECT active_configuration_id INTO config FROM stewardship_system_configuration;
    IF TG_OP='UPDATE' THEN prior:=OLD.current_revision_id; END IF;
    INSERT INTO stewardship_schedule_selection(id,definition_id,configuration_id,previous_revision_id,selected_revision_id,version,actor_id,correlation_id)
    VALUES(gen_random_uuid(),NEW.id,config,prior,NEW.current_revision_id,NEW.version,NEW.actor_id,NEW.correlation_id);
    IF prior IS NOT NULL THEN
        evidence:=public.stewardship_schedule_reconcile_v1(NEW.id,prior,NEW.actor_id,NEW.correlation_id);
    END IF;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    VALUES(event,NEW.actor_id,NEW.correlation_id,'schedule_selected',NEW.id,NEW.campaign_id);
    IF evidence IS NOT NULL THEN
        INSERT INTO stewardship_audit_context(id,actor_id,correlation_id,event_id,actor_kind,schema,context)
        VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,event,'portal_user','schedule',evidence);
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_schedule_task_roots_v1(definition uuid,revision uuid)
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
    UNION SELECT p.task_id FROM public.stewardship_report_email_preparation p
    WHERE p.definition_id=$1 AND p.revision_id=$2
    UNION SELECT t.root_id FROM public.stewardship_task_run t
    JOIN public.stewardship_report_email_preparation p ON p.id=t.domain_request_id
    WHERE t.task_type='report_email_finalize' AND p.definition_id=$1 AND p.revision_id=$2
$$;

CREATE OR REPLACE FUNCTION public.stewardship_schedules_activate_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE revision stewardship_schedule_revision%ROWTYPE; previous stewardship_schedule_definition%ROWTYPE;
BEGIN
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    -- The report conversion (#1786) is refused, before anything is replaced,
    -- while a digest send is in progress or uncertain. Only the conversion
    -- itself (upgrade-reports, under the admin-recovery login) asks: no other
    -- activation finds a digest definition beside a campaign-series-v12
    -- configuration.
    IF EXISTS(SELECT 1 FROM stewardship_configuration_version
              WHERE id=NEW.active_configuration_id AND validation_schema='campaign-series-v12')
       AND EXISTS(SELECT 1 FROM stewardship_schedule_definition WHERE kind IN ('daily_digest','weekly_digest')) THEN
        IF public.stewardship_digest_work_open_v1() IS NOT NULL THEN
            RAISE EXCEPTION 'The report conversion waits for every digest send to finish: %',
                public.stewardship_digest_work_open_v1() USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.current_campaign_id IS NOT NULL THEN
    FOR revision IN SELECT * FROM stewardship_schedule_revision
        WHERE configuration_id=NEW.active_configuration_id AND campaign_id=NEW.current_campaign_id ORDER BY record_id LOOP
        SELECT * INTO previous FROM stewardship_schedule_definition WHERE id=revision.record_id FOR UPDATE;
        IF NOT FOUND THEN
            INSERT INTO stewardship_schedule_definition(id,campaign_id,kind,current_revision_id,version,actor_id,correlation_id)
            VALUES(revision.record_id,revision.campaign_id,revision.kind,revision.id,1,NEW.actor_id,NEW.correlation_id);
        ELSIF previous.current_revision_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_schedule_revision old_revision
            JOIN stewardship_campaign_configuration old_campaign
              ON old_campaign.configuration_id=old_revision.configuration_id
             AND old_campaign.record_id=old_revision.campaign_id
            JOIN stewardship_campaign_configuration new_campaign
              ON new_campaign.configuration_id=revision.configuration_id
             AND new_campaign.record_id=revision.campaign_id
            WHERE old_revision.id=previous.current_revision_id
              AND old_revision.values=revision.values
              AND old_campaign.timezone=new_campaign.timezone
              AND (revision.kind NOT IN ('daily_digest','weekly_digest','report')
                  OR (old_campaign.start_date=new_campaign.start_date
                      AND old_campaign.end_date=new_campaign.end_date))
        ) THEN
            -- The report conversion (#1786) retypes a digest in place.
            UPDATE stewardship_schedule_definition SET current_revision_id=revision.id,removed_at=NULL,
                kind=revision.kind,
                version=version+1,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=previous.id;
        END IF;
    END LOOP;
    UPDATE stewardship_schedule_definition d SET current_revision_id=NULL,removed_at=statement_timestamp(),version=version+1,
        actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
    WHERE d.campaign_id=NEW.current_campaign_id AND d.current_revision_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_revision WHERE configuration_id=NEW.active_configuration_id AND record_id=d.id);
    END IF;
    -- The report conversion (#1786): a configuration holding report emails
    -- holds no digest, so every definition still of a digest kind, in any
    -- campaign (one not current, or a removed one, keeps its selection), becomes a
    -- report, kind only, once no digest send is in progress.
    IF EXISTS(SELECT 1 FROM stewardship_configuration_version
              WHERE id=NEW.active_configuration_id AND validation_schema='campaign-series-v12')
       AND EXISTS(SELECT 1 FROM stewardship_schedule_definition WHERE kind IN ('daily_digest','weekly_digest')) THEN
        UPDATE stewardship_schedule_definition SET kind='report',version=version+1,
            actor_id=NEW.actor_id,correlation_id=NEW.correlation_id
        WHERE kind IN ('daily_digest','weekly_digest');
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_setup_completion_write_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    IF current_user='pk_stewardship_worker' AND
        public.stewardship_setup_completion_context_v1() IS NULL AND
        public.stewardship_boundary_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        public.stewardship_catchup_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        public.stewardship_family_mail_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE AND
        -- Report emails (#1786) replaced the Daily and Weekly digests, whose
        -- own write admissions no longer admit anything.
        public.stewardship_report_email_write_admitted_v1(TG_TABLE_NAME,to_jsonb(NEW),
            CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END) IS NOT TRUE THEN
        RAISE EXCEPTION 'Worker configuration effects require atomic setup ownership'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_source_pin_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE snapshot record;
BEGIN
    -- Separate role branches before planning their private table queries.
    -- A boolean AND does not prevent PostgreSQL privilege checks on subqueries.
    IF current_user='pk_stewardship_worker' THEN
      IF TG_OP='DELETE' THEN
        -- Source retention deletes a superseded, disposable fact generation
        -- and then releases its now-orphaned source input pin, only while it
        -- owns the live compaction lease. The fact rows' own guards admit
        -- only stewardship_fact_disposable generations.
        IF OLD.parent_kind='facts'
           AND NOT EXISTS (SELECT 1 FROM public.stewardship_daily_fact_set
               WHERE id=OLD.parent_id)
           AND EXISTS (SELECT 1 FROM public.stewardship_source_lease l
               JOIN public.stewardship_task_run t ON t.id=l.owner_id
               WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
                 AND t.state='running' AND t.fence=l.task_fence
                 AND t.worker_id=l.worker_id
                 AND t.lease_expires_at > clock_timestamp())
        THEN
            -- Same snapshot row lock as every other pin release.
            PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id
                FOR UPDATE;
            RETURN OLD;
        END IF;
        IF OLD.parent_kind <> 'submission'
           OR NOT public.stewardship_response_source_owner_v1(
               (SELECT snapshot_id FROM public.stewardship_source_current))
           OR public.stewardship_response_pin_required_v1(OLD.parent_id,OLD.snapshot_id)
        THEN
            RAISE EXCEPTION 'Worker may release only unused response comparison inputs'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='digest' THEN
        -- A report email's inputs (#1786) pin the current promoted source to
        -- its preparation, by its live claim, as it records them.
        IF NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_report_email_preparation p
            JOIN stewardship_task_run t ON t.root_id=p.task_id AND t.state='running'
            JOIN stewardship_source_current current ON current.snapshot_id=NEW.snapshot_id
            WHERE p.id=NEW.parent_id AND p.phase='inputs' AND p.source_id IS NULL
              AND stewardship_report_email_live_v1(p.id,t.id,t.fence,t.worker_id)
        ) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Report source protection requires its live preparation'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='facts' THEN
        IF NEW.expires_at IS NOT NULL OR NOT (EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_fact_demand d
                ON d.id=t.domain_request_id
            WHERE t.task_type='report_facts' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp()
                AND d.population_scope='current'
                AND d.requested_source_id=NEW.snapshot_id
                AND d.claimed_generation_id IS NULL
        ) OR EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_exact_export_request r
                ON r.id=t.domain_request_id AND r.task_id=t.root_id
            WHERE t.task_type='report_exact_export' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp() AND r.population_scope='current'
                AND r.source_id=NEW.snapshot_id
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
                AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=r.id)
        )) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Worker fact input protection requires its live demand'
                USING ERRCODE='23514';
        END IF;
      ELSE
      IF (
        TG_OP <> 'INSERT' OR NEW.parent_kind <> 'submission' OR NEW.expires_at IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT (EXISTS (
            SELECT 1 FROM public.stewardship_proposed_change change
            JOIN public.stewardship_submission response ON response.id=change.submission_id
            WHERE response.id=NEW.parent_id
              AND change.execution IN ('pending','conflict','queued','failed')
        ) OR EXISTS (
            SELECT 1 FROM public.stewardship_ministry_request request
            WHERE request.submission_id=NEW.parent_id AND request.entity_kind='member'
              AND request.state IN ('new','assigned','in_progress')
        ))
        OR NOT public.stewardship_response_source_owner_v1(NEW.snapshot_id)
    ) THEN
        RAISE EXCEPTION 'Worker source protection requires current response reconciliation'
            USING ERRCODE='23514';
      END IF;
      END IF;
    END IF;
    IF current_user='pk_stewardship_web' THEN
      IF (
        (TG_OP <> 'INSERT' AND OLD.parent_kind <> 'form_baseline') OR
        (TG_OP <> 'DELETE' AND NEW.parent_kind NOT IN ('form_baseline','submission','report'))
    ) THEN
        RAISE EXCEPTION 'Web source protection is limited to Family forms'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='report' AND (
        NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_exact_export_request r
            WHERE r.id=NEW.parent_id AND r.source_id=NEW.snapshot_id
                AND r.population_scope='current'
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
        )) THEN
        RAISE EXCEPTION 'Web report protection requires its exact retained request'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='form_baseline' AND NOT EXISTS (
        SELECT 1 FROM stewardship_family_form_baseline
        WHERE id=NEW.parent_id AND source_id=NEW.snapshot_id AND state='open'
          AND expires_at=NEW.expires_at
    ) THEN
        RAISE EXCEPTION 'Web form protection requires its owned baseline'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='submission' AND NOT EXISTS (
        SELECT 1 FROM stewardship_submission response
        JOIN stewardship_family_form_baseline baseline ON baseline.id=response.baseline_id
        WHERE response.id=NEW.parent_id AND baseline.state='open'
          AND NEW.snapshot_id IN (response.reviewed_source_id,response.validation_source_id)
          AND NEW.expires_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Web response protection requires its final submission'
            USING ERRCODE='23514';
    END IF;
    END IF;
    IF TG_OP='DELETE' THEN
        PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id FOR UPDATE;
        RETURN OLD;
    END IF;
    SELECT state, compacted_at INTO snapshot FROM stewardship_source_snapshot WHERE id=NEW.snapshot_id
        FOR UPDATE;
    IF NOT FOUND OR snapshot.state <> 'promoted'
       OR snapshot.compacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Only reconstructable promoted snapshots can be protected'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

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

CREATE OR REPLACE FUNCTION public.stewardship_token_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE generation stewardship_family_token_generation%ROWTYPE;
    population stewardship_campaign_credentials%ROWTYPE;
    deployment stewardship_credential_deployment%ROWTYPE;
    current_key_digest text;
BEGIN
    IF NEW.active_token_generation_id IS NULL OR NEW.active_token_generation_id IS NOT DISTINCT FROM OLD.active_token_generation_id THEN RETURN NEW; END IF;
    IF NOT pg_try_advisory_xact_lock_shared(736226,1) THEN
        RAISE EXCEPTION 'Token key rotation is busy; retry' USING ERRCODE='23514'; END IF;
    SELECT * INTO deployment FROM stewardship_credential_deployment FOR SHARE;
    SELECT * INTO generation FROM stewardship_family_token_generation WHERE id=NEW.active_token_generation_id FOR UPDATE;
    SELECT * INTO population FROM stewardship_campaign_credentials WHERE campaign_id=NEW.id FOR UPDATE;
    SELECT inventory_digest INTO current_key_digest FROM stewardship_credential_key_state WHERE kind='token_public';
    IF generation.id IS NULL OR population.id IS NULL OR deployment.id IS NULL
       OR generation.campaign_id<>NEW.id OR generation.state<>'ready'
       OR generation.credential_epoch IS DISTINCT FROM deployment.family_link_epoch
       OR generation.key_inventory_digest IS DISTINCT FROM current_key_digest
       OR population.population_dirty OR population.rehearsal_epoch_id IS NOT NULL
       OR generation.coverage_digest IS DISTINCT FROM population.eligibility_digest
       OR generation.coverage_count IS DISTINCT FROM population.eligible_count
       OR generation.source_snapshot_id IS DISTINCT FROM population.source_snapshot_id
       OR generation.source_generation IS DISTINCT FROM population.source_generation
       OR EXISTS(SELECT 1 FROM stewardship_rehearsal_credential c
                 JOIN stewardship_rehearsal_epoch e ON e.id=c.epoch_id WHERE e.campaign_id=NEW.id)
       OR EXISTS(SELECT 1 FROM stewardship_submission WHERE campaign_id=NEW.id AND mode='test')
       -- A scalar PK lookup keeps this a baseline-driven existence probe. A
       -- join may instead scan every Family when cleanup left stale statistics.
       OR EXISTS(SELECT 1 FROM stewardship_family_form_baseline baseline
                 WHERE baseline.mode='test' AND (SELECT family.campaign_id
                     FROM stewardship_family_campaign family WHERE family.id=baseline.family_id)=NEW.id)
       OR EXISTS(SELECT 1 FROM stewardship_daily_digest_snapshot snapshot
                 JOIN stewardship_daily_digest_preparation p ON p.id=snapshot.preparation_id
                 WHERE snapshot.campaign_id=NEW.id AND p.mode='testing')
       OR EXISTS(SELECT 1 FROM stewardship_weekly_digest_snapshot snapshot
                 JOIN stewardship_weekly_digest_preparation p ON p.id=snapshot.preparation_id
                 WHERE snapshot.campaign_id=NEW.id AND p.mode='testing')
       OR EXISTS(SELECT 1 FROM stewardship_report_email_snapshot snapshot
                 JOIN stewardship_report_email_preparation p ON p.id=snapshot.preparation_id
                 WHERE snapshot.campaign_id=NEW.id AND p.mode='testing') THEN
        RAISE EXCEPTION 'Campaign requires a complete current token generation and rehearsal cleanup' USING ERRCODE='23514'; END IF;
    IF generation.configuration_request_id IS NULL THEN
        IF generation.configuration_id IS DISTINCT FROM NEW.active_configuration_id THEN
            RAISE EXCEPTION 'Prepared token configuration changed' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(
        SELECT 1 FROM stewardship_campaign_config_intent i
        JOIN stewardship_config_request r ON r.id=i.request_id
        JOIN stewardship_campaign_configuration p ON p.configuration_id=r.candidate_version_id AND p.record_id=NEW.id
        WHERE i.request_id=generation.configuration_request_id AND i.campaign_id=NEW.id
          AND i.action='reopen' AND i.prior_projection_id=generation.configuration_id
          AND i.token_generation_id=generation.id AND p.id=NEW.active_configuration_id
    ) THEN
        RAISE EXCEPTION 'Prepared tokens do not match the reviewed reopen candidate' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_users_form_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE
    candidate_schema text;
    predecessor_schema text;
    upgrade boolean;
    reports boolean;
BEGIN
    SELECT validation_schema INTO candidate_schema
    FROM public.stewardship_configuration_version WHERE id = NEW.configuration_id;
    SELECT validation_schema INTO predecessor_schema
    FROM public.stewardship_configuration_version WHERE id = NEW.predecessor_id;
    upgrade := EXISTS (SELECT 1 FROM public.stewardship_config_request
                       WHERE id = NEW.request_id AND authority = 'operator_upgrade'
                         AND request_schema = 'operator-upgrade-users-v10');
    -- The offline conversion of the digests into report emails (#1786).
    reports := EXISTS (SELECT 1 FROM public.stewardship_config_request
                       WHERE id = NEW.request_id AND authority = 'operator_upgrade'
                         AND request_schema = 'operator-upgrade-reports-v12');
    -- A users bootstrap (#993) is in the users form too: its successor is a
    -- users bootstrap (recovery) or, through setup, users-v10. So is
    -- campaign-series-v11 (#469): users-v10 with remembered reminder series.
    IF predecessor_schema IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12', 'bootstrap-users-v10')
       AND (candidate_schema IS NULL OR candidate_schema NOT IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12', 'bootstrap-users-v10')) THEN
        RAISE EXCEPTION 'Users cannot return to sign-in rules'
            USING ERRCODE = '23514';
    END IF;
    -- Remembered reminder series keep their schema once present, even with
    -- no series left, as the configuration schema selection does.
    -- Report emails (#1786, campaign-series-v12) build on the series schema.
    IF predecessor_schema = 'campaign-series-v11'
       AND candidate_schema NOT IN ('campaign-series-v11', 'campaign-series-v12') THEN
        RAISE EXCEPTION 'Remembered reminder series keep their schema'
            USING ERRCODE = '23514';
    END IF;
    IF upgrade AND (candidate_schema IS DISTINCT FROM 'users-v10'
                    OR predecessor_schema IS NULL
                    OR predecessor_schema IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12', 'bootstrap-users-v10')) THEN
        RAISE EXCEPTION 'The users upgrade converts sign-in rules once'
            USING ERRCODE = '23514';
    END IF;
    IF NOT upgrade AND candidate_schema IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12')
       AND predecessor_schema IS NOT NULL
       AND predecessor_schema NOT IN ('users-v10', 'campaign-series-v11', 'campaign-series-v12', 'bootstrap-users-v10') THEN
        RAISE EXCEPTION 'Only the offline users upgrade converts sign-in rules'
            USING ERRCODE = '23514';
    END IF;
    IF upgrade AND EXISTS (
        SELECT 1 FROM public.stewardship_address_rule prior_rule
        WHERE prior_rule.configuration_id = NEW.predecessor_id
          AND prior_rule.roles ? 'administrator'
          AND NOT EXISTS (
              SELECT 1 FROM public.stewardship_address_rule next_rule
              WHERE next_rule.configuration_id = NEW.configuration_id
                AND next_rule.email = prior_rule.email
                AND next_rule.role = 'administrator')
    ) THEN
        RAISE EXCEPTION 'The users upgrade must keep every Administrator'
            USING ERRCODE = '23514';
    END IF;
    -- The report conversion starts from the users form and ends in v12.
    IF reports AND (candidate_schema IS DISTINCT FROM 'campaign-series-v12'
                    OR predecessor_schema NOT IN ('users-v10', 'campaign-series-v11')) THEN
        RAISE EXCEPTION 'The report conversion converts the digests once'
            USING ERRCODE = '23514';
    END IF;
    IF reports THEN
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NULL, NEW.correlation_id,
            'operator_reports_converted', NEW.request_id);
    END IF;
    -- One audit event for the conversion, in its activation transaction. It
    -- names the request, whose immutable patch is the exact conversion.
    IF upgrade THEN
        INSERT INTO public.stewardship_audit_event
            (id, actor_id, correlation_id, event_type, subject_id)
        VALUES (gen_random_uuid(), NULL, NEW.correlation_id,
            'operator_users_upgraded', NEW.request_id);
    END IF;
    RETURN NEW;
END;
$$;

-- The replaced views (and the report work row they read).

-- A report send's work (#1786), as the digests' work rows count theirs.
CREATE VIEW public.stewardship_report_email_work_row AS
SELECT p.occurrence_id,p.task_id,
    coalesce(messages.outbox_versions,0) AS outbox_versions,
    coalesce(messages.outboxes,0) AS outboxes,
    coalesce(tasks.versions,0)+coalesce(finalizers.versions,0)+p.version AS task_versions,
    coalesce(messages.blocking,false) AS blocking
FROM public.stewardship_report_email_preparation p
LEFT JOIN LATERAL (
    SELECT sum(m.version) AS outbox_versions,count(m.id) AS outboxes,
        bool_or(m.state IN ('submitting','delivery_unknown')
            OR (m.state IN ('pending','retry_wait') AND resolution.action='retry_idempotent')
            OR m.purpose<>'admin_report' OR m.mode<>p.mode
            OR m.campaign_id IS DISTINCT FROM p.campaign_id
            OR m.semantic_key IS DISTINCT FROM recipient.id
            OR m.family_id IS NOT NULL OR m.credential_namespace<>'none') AS blocking
    FROM public.stewardship_report_email_snapshot s
    JOIN public.stewardship_report_email_recipient recipient ON recipient.snapshot_id=s.id
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
        SELECT m.task_id FROM public.stewardship_report_email_snapshot s
        JOIN public.stewardship_report_email_recipient recipient ON recipient.snapshot_id=s.id
        JOIN public.stewardship_outbox_message m ON m.id=recipient.outbox_id
        WHERE s.preparation_id=p.id
    ) roots JOIN public.stewardship_task_run t ON t.root_id=roots.task_id
) tasks ON true
LEFT JOIN (
    SELECT domain_request_id,sum(version) AS versions
    FROM public.stewardship_task_run WHERE task_type='report_email_finalize'
    GROUP BY domain_request_id
) finalizers ON finalizers.domain_request_id=p.id
WHERE p.occurrence_id IS NOT NULL;

CREATE OR REPLACE VIEW public.stewardship_admin_digest_work_row AS
SELECT * FROM public.stewardship_daily_digest_work_row
UNION ALL
SELECT * FROM public.stewardship_weekly_digest_work_row
UNION ALL
SELECT * FROM public.stewardship_report_email_work_row;

CREATE OR REPLACE VIEW public.stewardship_schedule_work_row AS
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
                             AND preparation.occurrence_id=o.id))))
                    OR (d.kind='report' AND digest.occurrence_id=o.id AND (
                        (t.task_type='report_email_prepare' AND t.root_id=digest.task_id)
                        OR (t.task_type='report_email_finalize' AND EXISTS(
                            SELECT 1 FROM public.stewardship_report_email_preparation preparation
                            WHERE preparation.id=t.domain_request_id AND preparation.occurrence_id=o.id))
                        OR (t.task_type='outbox_delivery' AND EXISTS (
                            SELECT 1 FROM public.stewardship_outbox_message child
                            JOIN public.stewardship_report_email_recipient recipient ON recipient.outbox_id=child.id
                            JOIN public.stewardship_report_email_snapshot snapshot ON snapshot.id=recipient.snapshot_id
                            JOIN public.stewardship_report_email_preparation preparation ON preparation.id=snapshot.preparation_id
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

CREATE OR REPLACE VIEW public.stewardship_delivery_digest_message AS
    SELECT p.occurrence_id,recipient.outbox_id FROM public.stewardship_daily_digest_preparation p
    JOIN public.stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_daily_digest_ready ready ON ready.snapshot_id=s.id
    JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=ready.id
    UNION ALL
    SELECT p.occurrence_id,recipient.outbox_id FROM public.stewardship_weekly_digest_preparation p
    JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id
    UNION ALL
    SELECT p.occurrence_id,recipient.outbox_id FROM public.stewardship_report_email_preparation p
    JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_report_email_recipient recipient ON recipient.snapshot_id=s.id
    WHERE recipient.outbox_id IS NOT NULL;

CREATE OR REPLACE VIEW public.stewardship_delivery_digest_recovery AS
WITH definitions AS (
    SELECT c.id AS campaign_id,c.production_cycle,d.id AS definition_id,
        d.version AS definition_version,d.current_revision_id AS revision_id,d.kind,
        p.start_date,p.end_date
    FROM public.stewardship_system_configuration r
    JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
    JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    JOIN public.stewardship_schedule_definition d ON d.campaign_id=c.id
    WHERE r.mode='production' AND c.delivery_paused
        AND d.kind='report' AND d.current_revision_id IS NOT NULL
), originals AS (
    -- Every send of a report email (#1786) due by now, wherever its rule
    -- puts it (before the campaign starts or after it ends too).
    SELECT d.*,due.slot,due.due_at
    FROM definitions d CROSS JOIN LATERAL public.stewardship_report_slots_v1(
        d.definition_id,public.stewardship_campaign_now_v1()) due
), inputs AS (
    SELECT d.campaign_id,d.definition_id,d.definition_version,d.revision_id,d.kind,
        o.id AS occurrence_id,o.version AS occurrence_version,o.slot,o.due_at,
        o.state,w.task_versions,w.outbox_version,
        coalesce(w.blocking,false) OR o.state='delivery_unknown' AS uncertain,
        report.occurrence_id IS NOT NULL AS prepared
    FROM definitions d
    JOIN public.stewardship_schedule_occurrence o ON o.revision_id=d.revision_id
        AND o.production_cycle=d.production_cycle AND o.mode='production' AND o.target='admins'
        AND o.due_at<=public.stewardship_campaign_now_v1()
        AND o.state IN ('pending','running','delivery_unknown')
    LEFT JOIN public.stewardship_schedule_work_row w ON w.id=o.id
    LEFT JOIN public.stewardship_report_email_work_row report ON report.occurrence_id=o.id
    WHERE NOT public.stewardship_schedule_slot_excluded_v1(d.definition_id,'production','admins',o.slot)
      AND o.slot NOT LIKE 'manual:%'
    UNION ALL
    SELECT d.campaign_id,d.definition_id,d.definition_version,d.revision_id,d.kind,
        NULL::uuid,NULL::bigint,d.slot,d.due_at,'pending',NULL::numeric,NULL::numeric,false,false
    FROM originals d
    WHERE d.due_at<=public.stewardship_campaign_now_v1()
        AND NOT public.stewardship_schedule_slot_excluded_v1(d.definition_id,'production','admins',d.slot)
        AND NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
            WHERE o.revision_id=d.revision_id AND o.production_cycle=d.production_cycle
                AND o.mode='production' AND o.target='admins' AND o.slot=d.slot)
), blocked AS (
    -- A report still discovering/capturing/fanning out must reach its ordinary
    -- safe point. The Admin never steals a worker lease or freezes a partial
    -- recipient set. Include its version in the exact confirmation fingerprint.
    SELECT p.definition_id,sum(p.version) AS versions,bool_or(p.phase NOT IN ('complete','cancelled')) AS preparing
    FROM (
        SELECT definition_id,revision_id,version,phase FROM public.stewardship_report_email_preparation p
        WHERE mode='production' AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_report_email_manual_request m WHERE m.id=p.id)
    ) p JOIN definitions d ON d.definition_id=p.definition_id AND d.revision_id=p.revision_id
    GROUP BY p.definition_id
)
SELECT i.*,coalesce(b.versions,0) AS preparation_versions,
    coalesce(b.preparing,false) OR bool_or(i.uncertain) OVER (PARTITION BY i.definition_id) AS blocked
FROM inputs i LEFT JOIN blocked b ON b.definition_id=i.definition_id;

CREATE OR REPLACE VIEW public.stewardship_delivery_closed_coverage AS
    SELECT receipt.outbox_id AS message_id,s.campaign_id,'receipt'::text AS purpose,
        'receipt:'||s.id::text AS obligation_key,NULL::uuid AS occurrence_id,
        jsonb_build_object('items',jsonb_build_array(jsonb_build_object(
            'kind','submission','id',s.id,'version',1)),'daily_range',NULL) AS coverage
    FROM public.stewardship_submission_receipt receipt
    JOIN public.stewardship_submission s ON s.id=receipt.submission_id
    WHERE s.mode='live' AND receipt.outbox_id IS NOT NULL
    UNION ALL
    SELECT recipient.outbox_id,p.campaign_id,'daily_digest','schedule:'||p.definition_id::text||':'||o.slot,
        o.id,jsonb_build_object('items','[]'::jsonb,'daily_range',jsonb_build_object(
            'start',s.covered_dates->>0,'end',s.covered_dates->>-1))
    FROM public.stewardship_daily_digest_preparation p
    JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
    JOIN public.stewardship_daily_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_daily_digest_ready r ON r.snapshot_id=s.id
    JOIN public.stewardship_daily_digest_recipient recipient ON recipient.ready_id=r.id
    WHERE p.mode='production' AND recipient.outbox_id IS NOT NULL
    UNION ALL
    SELECT recipient.outbox_id,p.campaign_id,'weekly_digest','schedule:'||p.definition_id::text||':'||o.slot,
        o.id,jsonb_build_object('items',(
            SELECT jsonb_agg(jsonb_build_object('kind',selected.kind,'id',selected.item_id,
                    'version',s.item_versions->selected.item_id)
                ORDER BY selected.kind,selected.item_id)
            FROM (SELECT 'item' AS kind,value AS item_id FROM jsonb_array_elements_text(recipient.information)
                UNION ALL SELECT 'correction',value->>0 FROM jsonb_array_elements(recipient.corrections)) selected
        ),'daily_range',NULL)
    FROM public.stewardship_weekly_digest_preparation p
    JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
    JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_weekly_digest_recipient recipient ON recipient.snapshot_id=s.id
    WHERE p.mode='production' AND recipient.outbox_id IS NOT NULL
    UNION ALL
    -- A report email (#1786) keeps no item coverage (decision 14): its
    -- obligation is its slot, and it reports its covered days.
    SELECT recipient.outbox_id,p.campaign_id,'admin_report','schedule:'||p.definition_id::text||':'||o.slot,
        o.id,jsonb_build_object('items','[]'::jsonb,'daily_range',CASE
            WHEN jsonb_array_length(p.covered_slots)>0 THEN jsonb_build_object(
                'start',p.covered_slots->>0,'end',p.covered_slots->>-1) END)
    FROM public.stewardship_report_email_preparation p
    JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
    JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
    JOIN public.stewardship_report_email_recipient recipient ON recipient.snapshot_id=s.id
    WHERE p.mode='production' AND recipient.outbox_id IS NOT NULL;

CREATE OR REPLACE VIEW public.stewardship_postclose_current AS
    SELECT resolved.id FROM public.stewardship_postclose_resolution resolved
    JOIN public.stewardship_schedule_occurrence o ON o.id=resolved.occurrence_id
    JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
    WHERE (d.kind IN ('daily_digest','report') AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(resolved.coverage->'items') item
        LEFT JOIN public.stewardship_additional_information i ON i.id=(item->>'id')::uuid
        WHERE item->>'kind' IN ('item','correction')
            AND to_jsonb(stewardship_information_digest_version_v1(i.disposition))
                IS DISTINCT FROM item->'version'))
    OR (d.kind='weekly_digest' AND EXISTS(
        SELECT 1 FROM public.stewardship_weekly_digest_preparation p
        JOIN public.stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
        JOIN public.stewardship_weekly_digest_preparation original ON original.occurrence_id=o.id
        JOIN public.stewardship_weekly_digest_snapshot old ON old.preparation_id=original.id
        WHERE p.campaign_id=resolved.campaign_id AND p.mode=resolved.mode
            AND p.rehearsal_epoch_id IS NOT DISTINCT FROM original.rehearsal_epoch_id
            AND s.submission_watermark>=old.submission_watermark AND s.observed_at>=old.observed_at
            AND NOT EXISTS(SELECT 1 FROM public.stewardship_weekly_manual_request WHERE id=p.id)
            AND (EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment f
                    WHERE f.occurrence_id=p.occurrence_id AND f.mode=p.mode AND f.target='admins'
                        AND f.disposition IN ('delivered','empty'))
                OR EXISTS(SELECT 1 FROM public.stewardship_postclose_resolution later
                    WHERE later.occurrence_id=p.occurrence_id AND later.mode=p.mode))
            AND NOT EXISTS(
                SELECT 1 FROM jsonb_each(s.item_versions) version
                LEFT JOIN public.stewardship_additional_information i ON i.id=version.key::uuid
                WHERE to_jsonb(stewardship_information_digest_version_v1(i.disposition))
                    IS DISTINCT FROM version.value)
            AND NOT EXISTS(
                SELECT 1 FROM public.stewardship_additional_information i
                JOIN public.stewardship_submission submission ON submission.id=i.submission_id
                WHERE submission.campaign_id=resolved.campaign_id AND submission.mode='live'
                    AND submission.campaign_sequence>s.submission_watermark)
    ));

CREATE OR REPLACE VIEW public.stewardship_delivery_closed_coverage_summary AS
WITH per_type AS (
    SELECT c.campaign_id,c.purpose,count(*) AS messages,
        sum(jsonb_array_length(c.coverage->'items')) AS items,
        count(*) FILTER(WHERE c.coverage->'daily_range'<>'null'::jsonb) AS daily_ranges,
        encode(sha256(convert_to(string_agg(encode(sha256(convert_to(
            jsonb_build_array(c.message_id,c.obligation_key,c.coverage)::text,'UTF8')),'hex'),''
            ORDER BY c.message_id),'UTF8')),'hex') AS fingerprint
    FROM public.stewardship_delivery_closed_coverage c
    JOIN public.stewardship_outbox_message m ON m.id=c.message_id
    JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.campaign_id
    WHERE m.state IN ('pending','retry_wait') AND m.pause_hold_id IS NOT NULL
    GROUP BY c.campaign_id,c.purpose
) SELECT r.current_campaign_id AS campaign_id,coalesce(jsonb_object_agg(p.purpose,
    to_jsonb(p)-'campaign_id'-'purpose') FILTER(WHERE p.purpose IS NOT NULL),'{}') AS coverage
    ,(SELECT count(*) FROM (
        SELECT campaign_id,revision_id,phase FROM public.stewardship_daily_digest_preparation
            WHERE mode='production'
        UNION ALL SELECT campaign_id,revision_id,phase FROM public.stewardship_weekly_digest_preparation
            WHERE mode='production'
        UNION ALL SELECT campaign_id,revision_id,phase FROM public.stewardship_report_email_preparation
            WHERE mode='production'
      ) preparing JOIN public.stewardship_schedule_definition d ON d.current_revision_id=preparing.revision_id
      WHERE preparing.campaign_id=r.current_campaign_id AND preparing.phase NOT IN ('complete','cancelled')) AS preparing
FROM public.stewardship_system_configuration r
LEFT JOIN per_type p ON p.campaign_id=r.current_campaign_id GROUP BY r.current_campaign_id;

CREATE OR REPLACE VIEW public.stewardship_delivery_control_inventory AS
    WITH scope AS (
        SELECT current_campaign_id AS campaign_id FROM public.stewardship_system_configuration
    ), messages AS (
        SELECT id,version,purpose,state,pause_hold_id,
            EXISTS(SELECT 1 FROM public.stewardship_delivery_uncertain u WHERE u.message_id=m.id) AS uncertain,
            EXISTS(SELECT 1 FROM public.stewardship_delivery_stranded s WHERE s.message_id=m.id) AS stranded
        FROM public.stewardship_outbox_message m
        WHERE campaign_id=(SELECT campaign_id FROM scope)
          AND mode='production' AND routing='production'
          AND purpose IN ('initial','reminder','receipt','daily_digest','weekly_digest','admin_report')
          AND state IN ('pending','retry_wait','submitting','delivery_unknown')
    ), types AS (
        SELECT purpose,count(*) FILTER(WHERE state IN ('pending','retry_wait')) AS queued,
            count(*) FILTER(WHERE pause_hold_id IS NOT NULL) AS held,
            count(*) FILTER(WHERE state='submitting') AS submitting,
            count(*) FILTER(WHERE uncertain) AS unknown
        FROM messages GROUP BY purpose
    ) SELECT scope.campaign_id,jsonb_build_object(
        'queued',(SELECT count(*) FROM messages WHERE state IN ('pending','retry_wait')),
        'held',(SELECT count(*) FROM messages WHERE pause_hold_id IS NOT NULL),
        'submitting',(SELECT count(*) FROM messages WHERE state='submitting'),
        'unknown',(SELECT count(*) FROM messages WHERE uncertain),
        'stranded',(SELECT count(*) FROM messages WHERE stranded),
        'types',(SELECT coalesce(jsonb_object_agg(purpose,to_jsonb(types)-'purpose'),'{}') FROM types),
        'fingerprint',encode(sha256(convert_to((SELECT coalesce(
            jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM messages)::text,'UTF8')),'hex')
    ) AS inventory FROM scope;

CREATE OR REPLACE VIEW public.stewardship_withdrawal_inventory AS
    WITH scope AS (
        SELECT current_campaign_id AS campaign_id FROM public.stewardship_system_configuration
    ), definitions AS (
        SELECT id,version FROM public.stewardship_schedule_definition
        WHERE campaign_id=(SELECT campaign_id FROM scope)
    ), work AS (
        SELECT w.* FROM public.stewardship_schedule_work_row w
        JOIN definitions d ON d.id=w.definition_id
        JOIN public.stewardship_schedule_occurrence o ON o.id=w.id WHERE o.mode='production'
    ), messages AS (
        SELECT m.id,m.version,m.state FROM public.stewardship_outbox_message m
        WHERE m.campaign_id=(SELECT campaign_id FROM scope) AND m.mode='production'
          AND m.purpose<>'operational'
    ), owned AS (
        SELECT outbox_id AS id FROM work WHERE outbox_id IS NOT NULL
        UNION SELECT recipient.outbox_id FROM public.stewardship_daily_digest_recipient recipient
        JOIN public.stewardship_daily_digest_ready ready ON ready.id=recipient.ready_id
        JOIN public.stewardship_daily_digest_snapshot snapshot ON snapshot.id=ready.snapshot_id
        JOIN public.stewardship_daily_digest_preparation preparation ON preparation.id=snapshot.preparation_id
        JOIN work ON work.id=preparation.occurrence_id WHERE recipient.outbox_id IS NOT NULL
        UNION SELECT recipient.outbox_id FROM public.stewardship_weekly_digest_recipient recipient
        JOIN public.stewardship_weekly_digest_snapshot snapshot ON snapshot.id=recipient.snapshot_id
        JOIN public.stewardship_weekly_digest_preparation preparation ON preparation.id=snapshot.preparation_id
        JOIN work ON work.id=preparation.occurrence_id WHERE recipient.outbox_id IS NOT NULL
        UNION SELECT recipient.outbox_id FROM public.stewardship_report_email_recipient recipient
        JOIN public.stewardship_report_email_snapshot snapshot ON snapshot.id=recipient.snapshot_id
        JOIN public.stewardship_report_email_preparation preparation ON preparation.id=snapshot.preparation_id
        JOIN work ON work.id=preparation.occurrence_id WHERE recipient.outbox_id IS NOT NULL
    ), totals AS (
        SELECT jsonb_build_object(
            'occurrences',(SELECT count(*) FROM work),
            'cancellable',(SELECT count(*) FROM work WHERE state IN ('pending','running') AND NOT blocking),
            'messages',(SELECT count(*) FROM messages WHERE state IN ('pending','retry_wait')),
            'failed',(SELECT count(*) FROM work WHERE state='failed'),
            'delivered',(SELECT count(*) FROM messages WHERE state='delivered'),
            'blocking',(SELECT count(*) FROM work WHERE blocking)
                +(SELECT count(*) FROM messages WHERE state IN ('submitting','delivery_unknown')
                    OR (state IN ('pending','retry_wait') AND id NOT IN (SELECT id FROM owned)))
                -- Cleanup must have removed Testing work. Never let a shared
                -- definition cancellation touch an unexpected Testing row.
                +(SELECT count(*) FROM public.stewardship_schedule_occurrence o
                    JOIN definitions d ON d.id=o.definition_id WHERE o.mode<>'production')
                +(SELECT count(*) FROM public.stewardship_task_run t
                    WHERE t.task_type='campaign_boundary'
                        AND t.domain_request_id=(SELECT campaign_id FROM scope)
                        AND t.state IN ('queued','retry_wait','running','abandoned')),
            'fingerprint',encode(sha256(convert_to(jsonb_build_array(
                (SELECT coalesce(jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM definitions),
                (SELECT coalesce(jsonb_agg(jsonb_build_array(id,version,task_versions,outbox_version) ORDER BY id),'[]') FROM work),
                (SELECT coalesce(jsonb_agg(jsonb_build_array(id,version) ORDER BY id),'[]') FROM messages)
            )::text,'UTF8')),'hex')
        ) AS value
    ) SELECT scope.campaign_id,totals.value AS inventory FROM scope CROSS JOIN totals;

-- The completion proof, which the scheduler's finalizer reads, asked
-- stewardship_report_email_skipped_v1 whether a send was skipped as empty;
-- that function reads the snapshot's values, which the scheduler may not,
-- and a function in a view runs with the reader's rights. The view now
-- answers it itself, with the view owner's rights, as the function does
-- (#1786); the function stays as file 0156 made it for the guards, which
-- the worker and mail dispatch run. Columns and grants are unchanged.
CREATE OR REPLACE VIEW public.stewardship_report_email_completion_ready AS
    SELECT p.id AS preparation_id,
        CASE WHEN skipped.value THEN 'nothing_to_report'
            WHEN EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
                JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE r.snapshot_id=s.id AND m.state='delivered')
                THEN 'delivered'
            ELSE 'empty' END AS disposition
    FROM public.stewardship_report_email_preparation p
    JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
    CROSS JOIN LATERAL (SELECT NOT s.has_content
        AND coalesce((s.values->>'content_required')::boolean,false)
        AND EXISTS(SELECT 1 FROM public.stewardship_schedule_occurrence o
            WHERE o.id=p.occurrence_id AND o.slot !~ '^manual:') AS value) skipped
    WHERE p.phase='complete' AND (
        skipped.value
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.recipients) member
            WHERE NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
                LEFT JOIN public.stewardship_outbox_message m ON m.id=r.outbox_id
                WHERE r.snapshot_id=s.id AND r.address=member->>'address'
                  AND (r.disposition='revoked' OR m.state='delivered'
                      OR (m.state='cancelled' AND m.reason='recipient_revoked')
                      OR (m.state='permanent_failure' AND NOT public.stewardship_report_email_current_v1(
                          p.definition_id,r.kind,r.ref,r.address))))));

-- A report send's work invalidates the activation impact preview, as the
-- weekly digest's did.
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_report_email_preparation FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_report_email_snapshot FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_report_email_recipient FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_report_email_manual_request FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();

-- The definitions the applied configuration does not select (a removed
-- digest, or one whose record the document no longer holds) are retyped
-- here, kind only; the offline conversion's activation retypes the others
-- with their new revisions. Owner-only and offline: the definition guards
-- would refuse a change outside a configuration activation.
ALTER TABLE public.stewardship_schedule_definition DISABLE TRIGGER USER;
UPDATE public.stewardship_schedule_definition d SET kind='report',version=version+1,
    updated_at=statement_timestamp()
WHERE d.kind IN ('daily_digest','weekly_digest') AND NOT EXISTS(
    SELECT 1 FROM public.stewardship_system_configuration r
    JOIN public.stewardship_schedule_revision v ON v.configuration_id=r.active_configuration_id
    WHERE v.record_id=d.id);
ALTER TABLE public.stewardship_schedule_definition ENABLE TRIGGER USER;

-- The definers the baseline alters, altered again after their replacement.
ALTER FUNCTION public.stewardship_occurrence_guard_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_schedule_definition_v1() SECURITY DEFINER;
ALTER FUNCTION public.stewardship_schedule_selection_effect_v1() SECURITY DEFINER;

DO $check$
DECLARE missing text;
BEGIN
    -- The new functions, with their declared rights and fixed search path.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_digest_work_open_v1()', true),
        ('stewardship_report_slots_v1(uuid,timestamp with time zone)', false)
    ) expected(name, definer)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND p.prosecdef=expected.definer
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0191: not installed as declared: %', missing;
    END IF;
    -- Each replaced function carries its report change, and the definers
    -- the baseline alters are definers again.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_campaign_complete_v1()', 'campaign-series-v12', false),
        ('stewardship_campaign_projection_v1()', 'campaign-series-v12', false),
        ('stewardship_content_complete_v1()', 'campaign-series-v12', false),
        ('stewardship_content_projection_v1()', 'campaign-series-v12', false),
        ('stewardship_policy_complete_v1()', 'campaign-series-v12', false),
        ('stewardship_policy_projection_v1()', 'campaign-series-v12', false),
        ('stewardship_users_form_v1()', 'operator-upgrade-reports-v12', false),
        ('stewardship_request_checkpoint_v2()', 'operator-upgrade-reports-v12', false),
        ('stewardship_schedule_definition_mutable_v1()', 'report', false),
        ('stewardship_schedule_definition_v1()', 'campaign-series-v12', true),
        ('stewardship_schedule_selection_effect_v1()', 'report', true),
        ('stewardship_schedules_activate_v1()', 'stewardship_digest_work_open_v1', false),
        ('stewardship_setup_completion_write_v1()', 'stewardship_report_email_write_admitted_v1', false),
        ('stewardship_family_mail_outbox_write_v1()', 'stewardship_report_email_write_admitted_v1', false),
        ('stewardship_family_dispatch_write_v1()', 'stewardship_report_email_dispatch_write_v1', false),
        ('stewardship_family_smtp_result_v1(uuid)', 'admin_report', false),
        ('stewardship_outbox_render_shape_v1()', 'admin_report', false),
        ('stewardship_occurrence_guard_v1()', 'stewardship_report_email_completion_v1', true),
        ('stewardship_fulfillment_guard_v1()', 'stewardship_report_email_completion_ready', false),
        ('stewardship_recovery_replacement_v1()', 'stewardship_report_email_replacement_v1', false),
        ('stewardship_postclose_guard_v1()', 'report', false),
        ('stewardship_source_pin_guard()', 'stewardship_report_email_preparation', false),
        ('stewardship_exact_fact_pin_guard_v1()', 'stewardship_report_email_snapshot', false),
        ('stewardship_fact_runtime_binding_v1()', 'report_email_prepare', false),
        ('stewardship_fact_disposable(uuid)', 'stewardship_report_email_snapshot', false),
        ('stewardship_task_type_login_v1(text)', 'report_email_finalize', false),
        ('stewardship_task_type_creators_v1(text)', 'report_email_finalize', false),
        ('stewardship_schedule_message_ids_v1(uuid,uuid)', 'stewardship_report_email_recipient', false),
        ('stewardship_schedule_task_roots_v1(uuid,uuid)', 'report_email_finalize', false),
        ('stewardship_catchup_due_v1(uuid,text)', 'stewardship_report_slot_due_v1', false),
        ('stewardship_catchup_digest_receipt_v1(jsonb)', 'stewardship_report_next_send_v1', false),
        ('stewardship_catchup_occurrence_shape_v1(jsonb)', 'missed_report_recovery', false),
        ('stewardship_checkpoint_guard_v1()', 'report', false),
        ('stewardship_cleanup_batch_targets_v1(uuid,integer)', 'report_email_snapshots', false),
        ('stewardship_cleanup_inventory_v1(uuid)', 'report_email_fact_pins', false),
        ('stewardship_cleanup_relation_v1(text)', 'report_email_recipients', false),
        ('stewardship_token_activation_v1()', 'stewardship_report_email_snapshot', false),
        ('stewardship_delivery_closed_digest_v1(uuid)', 'stewardship_report_email_preparation', false),
        ('stewardship_delivery_control_effect_v1()', 'admin_report', true),
        ('stewardship_delivery_control_guard_v1()', 'admin_report', true),
        ('stewardship_delivery_message_resolution_guard_v1()', 'admin_report', false),
        ('stewardship_delivery_new_hold_v1()', 'admin_report', true),
        ('stewardship_delivery_recover_digests_v1(uuid)', 'missed_report_recovery', true),
        ('stewardship_delivery_resolution_guard_v1()', 'stewardship_report_email_recipient', true),
        ('stewardship_delivery_resolve_closed_v1(uuid)', 'admin_report', true),
        ('stewardship_delivery_retry_render_v1(uuid,jsonb,uuid,uuid)', 'stewardship_report_email_seed_v1', false),
        ('stewardship_delivery_retry_admitted_v1(uuid)', 'stewardship_report_email_dispatch_live_v1', false),
        ('stewardship_report_email_preparation_guard_v1()', 'statement_timestamp()-transaction_timestamp()', false),
        ('stewardship_report_email_snapshot_guard_v1()', 'A-Za-z0-9._@-', false),
        ('stewardship_report_email_manual_guard_v1()', 'still being delivered', true)
    ) expected(name, marker, definer)
    WHERE NOT EXISTS (SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.'||expected.name)
          AND strpos(p.prosrc, expected.marker)>0 AND p.prosecdef=expected.definer);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0191: a replaced function lacks its report change: %', missing;
    END IF;
    -- No gate admits a digest write any more, and no task map names a
    -- digest task type.
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname !~ '^stewardship_(daily|weekly)_'
          AND p.prosrc ~ 'stewardship_(daily|weekly)_digest_write_admitted_v1')
       OR public.stewardship_task_type_login_v1('daily_digest_prepare') IS NOT NULL
       OR public.stewardship_task_type_login_v1('weekly_digest_finalize') IS NOT NULL
       OR public.stewardship_task_type_login_v1('report_email_finalize') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_creators_v1('report_email_finalize')
           IS DISTINCT FROM ARRAY['pk_stewardship_scheduler','pk_stewardship_web'] THEN
        RAISE EXCEPTION 'Migration 0191: a digest write or task type is still admitted';
    END IF;
    -- The constraints admit what the conversion and report sends write.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_schedule_definition', 'schedule_definition_kind', 'report'),
        ('stewardship_schedule_revision', 'schedule_revision_due_kind', 'report'),
        ('stewardship_configuration_version', 'configuration_validation_schema', 'campaign-series-v12'),
        ('stewardship_config_request', 'config_request_schema', 'operator-upgrade-reports-v12'),
        ('stewardship_config_request', 'config_request_actor', 'operator-upgrade-reports-v12'),
        ('stewardship_outbox_message', 'outbox_purpose', 'admin_report_test'),
        ('stewardship_outbox_message', 'outbox_family_purpose', 'admin_report_test'),
        ('stewardship_production_target', 'production_target_category', 'report_email_fact_pins')
    ) expected(relation, name, marker)
    WHERE NOT EXISTS (SELECT 1 FROM pg_constraint c
        WHERE c.conrelid=to_regclass('public.'||expected.relation) AND c.conname=expected.name
          AND strpos(pg_get_constraintdef(c.oid), expected.marker)>0);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0191: a constraint does not admit report emails: %', missing;
    END IF;
    -- The views read the report records.
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_report_email_work_row', 'stewardship_report_email_preparation'),
        ('stewardship_admin_digest_work_row', 'stewardship_report_email_work_row'),
        ('stewardship_schedule_work_row', 'report_email_prepare'),
        ('stewardship_delivery_digest_message', 'stewardship_report_email_recipient'),
        ('stewardship_delivery_digest_recovery', 'stewardship_report_slots_v1'),
        ('stewardship_delivery_closed_coverage', 'admin_report'),
        ('stewardship_postclose_current', 'report'),
        ('stewardship_delivery_closed_coverage_summary', 'stewardship_report_email_preparation'),
        ('stewardship_delivery_control_inventory', 'admin_report'),
        ('stewardship_withdrawal_inventory', 'stewardship_report_email_recipient')
    ) expected(name, marker)
    WHERE to_regclass('public.'||expected.name) IS NULL
       OR strpos(pg_get_viewdef(to_regclass('public.'||expected.name)), expected.marker)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0191: a view does not read report emails: %', missing;
    END IF;
    IF has_table_privilege('public','public.stewardship_report_email_work_row','SELECT') THEN
        RAISE EXCEPTION 'Migration 0191: the report work row is readable by PUBLIC';
    END IF;
    SELECT string_agg(name, ', ') INTO missing FROM (VALUES
        ('stewardship_report_email_preparation'),('stewardship_report_email_snapshot'),
        ('stewardship_report_email_recipient'),('stewardship_report_email_manual_request')
    ) expected(name)
    WHERE NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid=to_regclass('public.'||expected.name)
          AND t.tgname='activation_impact' AND NOT t.tgisinternal AND t.tgenabled='O');
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0191: an activation impact trigger is missing: %', missing;
    END IF;
    -- The definition guards are enabled again, and only definitions the
    -- applied configuration selects still have a digest kind.
    IF EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_schedule_definition'::regclass
          AND NOT t.tgisinternal AND t.tgenabled<>'O')
       OR EXISTS (SELECT 1 FROM public.stewardship_schedule_definition d
        WHERE d.kind IN ('daily_digest','weekly_digest') AND NOT EXISTS(
            SELECT 1 FROM public.stewardship_system_configuration r
            JOIN public.stewardship_schedule_revision v ON v.configuration_id=r.active_configuration_id
            WHERE v.record_id=d.id)) THEN
        RAISE EXCEPTION 'Migration 0191: an unselected digest definition was not retyped';
    END IF;
    -- The slot inventory answers nothing for a definition that is no report.
    IF (SELECT count(*) FROM public.stewardship_report_slots_v1(NULL, now())) <> 0 THEN
        RAISE EXCEPTION 'Migration 0191: the report slot inventory is not installed as declared';
    END IF;
    -- The new definer is no one's but the activating logins' to call, and
    -- the completion proof answers a skipped send without a function the
    -- scheduler cannot run (file 0156's skipped check stays an invoker).
    IF has_function_privilege('public','public.stewardship_digest_work_open_v1()','EXECUTE')
       OR (SELECT prosecdef FROM pg_proc
           WHERE oid='public.stewardship_report_email_skipped_v1(uuid)'::regprocedure)
       OR strpos(pg_get_viewdef('public.stewardship_report_email_completion_ready'::regclass),
           'stewardship_report_email_skipped_v1')>0
       OR has_table_privilege('public','public.stewardship_report_email_completion_ready','SELECT') THEN
        RAISE EXCEPTION 'Migration 0191: the conversion check or completion proof is not installed as declared';
    END IF;
END
$check$;
