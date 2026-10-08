-- Frozen forward migration file 0019 (the repository-wide file sequence):
-- alert when web stops answering its liveness check, and record when it
-- recovers (#392 L1). This file is installed by its Django migration in the
-- jobs app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copies of the replaced functions still equal the
-- fresh-install baseline's. A later change gets its own numbered file. A
-- fresh install runs the baseline, 0002 to 0018 and then this; the baseline
-- already carries the widened lists and function bodies, and the new table
-- and trigger exist only here, so the install ends in the same catalog as an
-- upgraded database.
--
-- Docker restarts a container only when its process exits, so a web server
-- that still runs but no longer answers was neither restarted nor alerted
-- on. The owned scheduler now probes each web replica's /health/live once a
-- minute, off its scheduling loop (jobs.web_health), and records the result
-- in the new singleton stewardship_web_health row. That row's trigger counts
-- consecutive failed minutes and, from the third on, writes one CRITICAL
-- web_unhealthy entry per failed minute. The operational collector maps it
-- to the new web_unhealthy incident kind (banner, email, Slack) and resolves
-- the incident after five minutes of passing probes.
--
-- Objects:
--   stewardship_web_health          new table, written only by the scheduler
--   stewardship_web_health_v1       new trigger function deriving its state
--   operational_event_safe          + web_unhealthy
--   ops_incident_kind               + web_unhealthy
--   stewardship_ops_log_receipt_binding_v1   web_unhealthy -> web_unhealthy
--   stewardship_ops_content_v1      the alert's title and instruction
--   stewardship_safe_context_v1     failure words web_health_check and
--                                   web_unresponsive; timeout word web_probe
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The latest probe result. The scheduler submits only healthy; the trigger
-- sets every other column, so no caller can choose a count or a start time.
-- Django's own DDL for jobs.web_health_models.WebHealth, so the installed
-- table matches the model declaration exactly.
CREATE TABLE "stewardship_web_health" (
    "singleton" boolean NOT NULL PRIMARY KEY,
    "healthy" boolean NOT NULL,
    "observed_at" timestamp with time zone NOT NULL,
    "failures" integer NOT NULL CHECK ("failures" >= 0),
    "failing_since" timestamp with time zone NULL,
    "passing_since" timestamp with time zone NULL,
    CONSTRAINT "web_health_singleton" CHECK ("singleton"),
    CONSTRAINT "web_health_shape" CHECK ((("failing_since" IS NULL AND "failures" = 0 AND "healthy" AND "passing_since" IS NOT NULL AND "passing_since" <= ("observed_at")) OR ("failing_since" IS NOT NULL AND "failing_since" <= ("observed_at") AND "failures" >= 1 AND NOT "healthy" AND "passing_since" IS NULL)))
);

-- Derive the row from one submitted verdict (jobs.web_health.FAILED_MINUTES
-- and STALE mirror the numbers here). Only the owned scheduler's session may
-- write it. A second observation within 30 seconds (a retried pass) is
-- dropped, so each minute counts once. A result more than 150 seconds after
-- the previous one, or before it (the database clock stepped back), starts a
-- new run: a gap is not a continuous failure or recovery, and a clock step
-- never leaves results dropped until the clock catches up. The 150 seconds
-- equal jobs.web_health.STALE. The scheduler's upsert first runs this
-- trigger on its proposed INSERT row; that row can never alert, since a
-- first result counts at most one failure, below the threshold of three.
-- From the third failed minute in a row, each failed minute writes
-- one CRITICAL web_unhealthy entry whose context the probe set in a
-- transaction-local setting (how it failed, an HTTP status), plus the count;
-- a missing or refused context never blocks the entry itself.
CREATE FUNCTION public.stewardship_web_health_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE instant timestamptz; recent boolean; detail jsonb;
BEGIN
    IF TG_OP='DELETE' OR NOT EXISTS(SELECT 1 FROM pg_locks
        WHERE locktype='advisory' AND pid=pg_backend_pid()
          AND classid=736229 AND objid=1 AND objsubid=2 AND granted) THEN
        RAISE EXCEPTION 'Web health observations require the owned scheduler session' USING ERRCODE='23514';
    END IF;
    instant:=clock_timestamp();
    IF TG_OP='UPDATE' AND instant>=OLD.observed_at
       AND instant-OLD.observed_at<interval '30 seconds' THEN
        RETURN NULL;
    END IF;
    recent:=TG_OP='UPDATE' AND instant>=OLD.observed_at
        AND instant-OLD.observed_at<=interval '150 seconds';
    NEW.singleton:=true;
    NEW.observed_at:=instant;
    IF NEW.healthy THEN
        NEW.failures:=0;
        NEW.failing_since:=NULL;
        NEW.passing_since:=CASE WHEN recent AND OLD.healthy THEN OLD.passing_since ELSE instant END;
        RETURN NEW;
    END IF;
    NEW.passing_since:=NULL;
    IF recent AND NOT OLD.healthy THEN
        NEW.failures:=LEAST(OLD.failures+1,1000000);
        NEW.failing_since:=OLD.failing_since;
    ELSE
        NEW.failures:=1;
        NEW.failing_since:=instant;
    END IF;
    IF NEW.failures>=3 THEN
        BEGIN
            detail:=NULLIF(current_setting('parishkit.web_health_context',true),'')::jsonb;
        EXCEPTION WHEN invalid_text_representation THEN
            detail:=NULL;
        END;
        IF detail IS NULL OR jsonb_typeof(detail) IS DISTINCT FROM 'object'
           OR NOT public.stewardship_safe_context_v1('failure',detail) THEN
            detail:=jsonb_build_object('failure','web_unresponsive','failure_kind','unexpected_failure');
        END IF;
        detail:=detail||jsonb_build_object('count',NEW.failures);
        INSERT INTO public.stewardship_operational_log
            (id,correlation_id,level,event,schema,context)
        VALUES(gen_random_uuid(),gen_random_uuid(),'CRITICAL','web_unhealthy','failure',detail);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_web_health_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_web_health FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_web_health_v1();
REVOKE ALL ON FUNCTION public.stewardship_web_health_v1() FROM PUBLIC;

-- The event list: 0016's (unchanged by 0017 and 0018) plus web_unhealthy.
ALTER TABLE public.stewardship_operational_log DROP CONSTRAINT operational_event_safe;
ALTER TABLE public.stewardship_operational_log ADD CONSTRAINT operational_event_safe CHECK (event IN (
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy'));

-- The incident kinds: 0004's list plus web_unhealthy (as
-- operational_incidents.sql now declares it).
ALTER TABLE public.stewardship_ops_incident DROP CONSTRAINT ops_incident_kind;
ALTER TABLE "stewardship_ops_incident" ADD CONSTRAINT "ops_incident_kind" CHECK ("kind"::text = ANY(ARRAY[('database_unavailable'::varchar)::text, ('storage_integrity'::varchar)::text, ('task_failed'::varchar)::text, ('system_failure'::varchar)::text, ('source_refresh_failed'::varchar)::text, ('source_stale'::varchar)::text, ('source_tenant_mismatch'::varchar)::text, ('source_destructive_change'::varchar)::text, ('mail_provider_unavailable'::varchar)::text, ('scheduler_lag'::varchar)::text, ('worker_unavailable'::varchar)::text, ('admin_abuse'::varchar)::text, ('family_abuse'::varchar)::text, ('limiter_unavailable'::varchar)::text, ('limiter_state_lost'::varchar)::text, ('publication_ambiguous'::varchar)::text, ('production_cleanup_failed'::varchar)::text, ('backup_rpo_breach'::varchar)::text, ('backup_offsite_failed'::varchar)::text, ('backup_key_changed'::varchar)::text, ('source_retention_failing'::varchar)::text, ('purge_inconsistency'::varchar)::text, ('purge_cleanup_failed'::varchar)::text, ('automation_approved'::varchar)::text, ('automation_irreversible'::varchar)::text, ('automation_policy_change'::varchar)::text, ('automation_refused'::varchar)::text, ('web_unhealthy'::varchar)::text]));

-- A web_unhealthy entry opens the web_unhealthy incident; the rest is the
-- baseline body (operational_incidents.sql).
CREATE OR REPLACE FUNCTION public.stewardship_ops_log_receipt_binding_v1()
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
            WHEN 'due_work_lag' THEN 'scheduler_lag'
            WHEN 'production_cleanup_failed' THEN 'production_cleanup_failed'
            WHEN 'web_unhealthy' THEN 'web_unhealthy'
            ELSE 'system_failure' END
    ) THEN
        RAISE EXCEPTION 'Critical log intake requires exact current ownership' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

-- The alert's title and instruction, mirrored word for word in
-- jobs.operational_content; the rest is 0004's body.
CREATE OR REPLACE FUNCTION public.stewardship_ops_content_v1(notice uuid, deployment_mode text)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE n stewardship_ops_notice%ROWTYPE; kind text; title text; status text;
    instruction text; labels text[]; vals text[]; body text; html text; i integer;
BEGIN
    IF deployment_mode IS NULL OR deployment_mode NOT IN ('testing','production') THEN
      RAISE EXCEPTION 'Operational mode is invalid' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT n FROM stewardship_ops_notice WHERE id=notice;
    SELECT incident.kind INTO STRICT kind FROM stewardship_ops_incident incident WHERE id=n.incident_id;
    title:=CASE kind
      WHEN 'database_unavailable' THEN 'Database unavailable'
      WHEN 'storage_integrity' THEN 'Storage integrity requires attention'
      WHEN 'task_failed' THEN 'Background task failed'
      WHEN 'system_failure' THEN 'System operation requires attention'
      WHEN 'source_refresh_failed' THEN 'Parish data refresh failed'
      WHEN 'source_stale' THEN 'Parish data refresh is overdue'
      WHEN 'source_tenant_mismatch' THEN 'Parish data organization mismatch'
      WHEN 'source_destructive_change' THEN 'Unexpected parish data loss'
      WHEN 'mail_provider_unavailable' THEN 'Email provider unavailable'
      WHEN 'scheduler_lag' THEN 'Scheduled work is overdue'
      WHEN 'worker_unavailable' THEN 'Background worker unavailable'
      WHEN 'admin_abuse' THEN 'Sustained administration login abuse'
      WHEN 'family_abuse' THEN 'Sustained Family login abuse'
      WHEN 'limiter_unavailable' THEN 'Login rate limiter unavailable'
      WHEN 'limiter_state_lost' THEN 'Login rate limiter state was lost'
      WHEN 'publication_ambiguous' THEN 'Parish data publication is uncertain'
      WHEN 'production_cleanup_failed' THEN 'Campaign preparation cleanup failed'
      WHEN 'backup_rpo_breach' THEN 'Required backup is overdue'
      WHEN 'backup_offsite_failed' THEN 'Off-site backup copy failed'
      WHEN 'backup_key_changed' THEN 'Backup encryption key changed'
      WHEN 'source_retention_failing' THEN 'Parish data cleanup keeps failing'
      WHEN 'purge_inconsistency' THEN 'Campaign purge is inconsistent'
      WHEN 'purge_cleanup_failed' THEN 'Campaign purge cleanup failed'
      WHEN 'automation_approved' THEN 'An automation session was approved'
      WHEN 'automation_irreversible' THEN 'An automation session took an irreversible action'
      WHEN 'automation_policy_change' THEN 'An automation session changed user access, integration keys or notification settings'
      WHEN 'automation_refused' THEN 'An automation session was refused'
      WHEN 'web_unhealthy' THEN 'Web server is not responding'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Operational kind is invalid' USING ERRCODE='23514'; END IF;
    status:=CASE n.phase WHEN 'resolved' THEN 'RESOLVED' ELSE n.level END;
    instruction:=CASE WHEN n.phase='resolved' AND kind='backup_key_changed'
      THEN 'The backup encryption key change is no longer recent. If you have not already, ask the server operator to confirm that each kept copy of the private key opens a new backup, as the backup runbook describes.'
      WHEN n.phase='resolved' AND kind IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused')
      THEN 'No further automation events of this kind in the last hour. Review the automation notices on the Admin dashboard if you have not already.'
      WHEN n.phase='resolved'
      THEN 'This condition has recovered. Review the operational log if follow-up is needed.'
      WHEN kind='backup_key_changed' THEN 'A backup in the last two days was sealed to a different encryption key than the backup before it. Unless the server operator installed a new key on purpose, new backups may not open with the kept private key. Ask the operator to open the newest backup with each kept copy of the private key, as the backup runbook describes.'
      WHEN kind IN ('automation_approved','automation_irreversible',
        'automation_policy_change','automation_refused')
      THEN 'Review the automation notices on the Admin dashboard. They name the automation session and what it did.'
      WHEN kind='source_retention_failing' THEN 'Removing old ParishSoft copies was skipped by the last three refreshes, so the database keeps growing. Refreshes still work. Ask the server operator to check the worker log for the cause.'
      WHEN kind='web_unhealthy' THEN 'The web server has not answered three health checks in a row, a minute apart, so the Admin and Family portals may not be loading. Docker restarts web only if it stops running. Ask the server operator to restart web from the Compose directory of the deployment (docker compose ... restart web) and to check its log.'
      ELSE 'Administrator attention is required. Review the operational log for details.' END;
    labels:=ARRAY['Status','Notification','Deployment mode','First observed',
      'Latest observation','Occurrences','Incident reference'];
    vals:=ARRAY[status,n.phase,initcap(deployment_mode),
      to_char(n.first_seen AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      to_char(n.observed_at AT TIME ZONE 'UTC','MM/DD/YYYY HH24:MI:SS "UTC"'),
      to_char(n.occurrences,'FM9,999,999,999,999,999,999'),n.incident_id::text];
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||vals[i]||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] '||status||': '||title,
      'html',html||'</dl>','text',body);
END $$;

-- The context allowlist gains the failure words web_health_check (the
-- collector's check could not run) and web_unresponsive (the CRITICAL
-- entry), and the timeout word web_probe; the rest is 0018's body. It keeps
-- the baseline's attributes (IMMUTABLE, invoker rights, the search_path).
CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
        ministry jsonb; previous_ministry bigint;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome','reason']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'member_source' THEN ARRAY['family_duid','member_duid','field']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count',
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint',
            'report_mode','report_filter','talent_option_id','snapshot_id']
        WHEN 'boundary' THEN ARRAY['occurrence_id','kind','intended_unix_microseconds','actual_unix_microseconds','lag_microseconds','before_state','after_state']
        WHEN 'schedule' THEN ARRAY['definition_id','previous_revision_id','selected_revision_id','cancelled_messages','skipped_occurrences','failed_occurrences','delivered_slots']
        WHEN 'timeout' THEN ARRAY['task_id','task_type','attempt','limit_seconds','elapsed_seconds','what','helper','count','outcome']
        -- What made scheduled work late (#634): the task type, how many tasks
        -- and how late against which limit, or the Family send that stalled
        -- or overran, with its counts, how long since its last progress and
        -- how many other tasks were late in the same check.
        WHEN 'due_work' THEN ARRAY['task_type','count','lag_seconds','limit_seconds','definition_id','revision_id',
            'remaining_count','done_count','stall_seconds','elapsed_seconds','other_late_count',
            'occurrence_id','task_id']
        -- What failed and what happens next (#633): a closed word for what
        -- failed, the exception's category, the task, message and attempt,
        -- a closed provider reason or HTTP status, and whether it retries.
        WHEN 'failure' THEN ARRAY['failure','failure_kind','task_id','task_type','message_id','version',
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome']
        -- An operational incident that ended (#633): the incident, its kind,
        -- the entry that opened it, how long it lasted and how often it was seen.
        WHEN 'recovery' THEN ARRAY['incident_id','incident_kind','log_id','elapsed_seconds','count']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        IF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('no_deliverable_recipient',
                'smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic','smtp_delivery_unknown',
                'preparation_failed','slack_not_sent','slack_delivery_unknown') THEN RETURN false; END IF;
        -- What failed (#633): a closed word (audit.schemas.FAILURES).
        ELSIF key='failure' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('organization_mismatch','destructive_change',
                'shifted_scan','invalid_payload','incomplete_collection','invalid_response','lease_unavailable',
                'configuration_activating','configuration_busy','scope_changed','credential_unreadable',
                'credential_changed','provider_status','provider_timeout','provider_unreachable',
                'source_configuration','organization_changed','source_health_check','mail_health_check',
                'due_work_health_check','backup_health_check','export_cleanup','fact_verification',
                'source_retention','family_engagement','alert_mail','security_mail','slack_alert',
                'smtp_systemic','smtp_unavailable','web_health_check','web_unresponsive') THEN RETURN false; END IF;
        -- A failure's category and an incident's kind: identifier words whose
        -- closed sets Python owns (observability.FailureKind, IncidentKind).
        ELSIF key IN ('failure_kind','incident_kind') THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('start','close') THEN RETURN false; END IF;
        ELSIF key IN ('before_state','after_state') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('draft','scheduled','active','closed','archived',
                'purging','purge_cleanup_failed','purged') THEN RETURN false; END IF;
        ELSIF key='field' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname',
                'maiden_name','birth_date','gender','email','home_phone',
                'mobile_phone','work_phone','marital_status','language','death_date'
            ) THEN RETURN false; END IF;
        ELSIF key IN ('family_duid','member_duid','ministry_duid') THEN
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,10}$' THEN RETURN false; END IF;
            IF text_value::numeric NOT BETWEEN 1 AND 2147483647 THEN RETURN false; END IF;
        ELSIF key IN ('ministry_duids','previous_ministry_duids','added_ministry_duids','removed_ministry_duids') THEN
            IF jsonb_typeof(value)<>'array' THEN RETURN false; END IF;
            previous_ministry:=0;
            FOR ministry IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(ministry)<>'number' OR ministry#>>'{}'!~'^[0-9]{1,10}$'
                THEN RETURN false; END IF;
                IF (ministry#>>'{}')::bigint NOT BETWEEN previous_ministry+1 AND 2147483647
                THEN RETURN false; END IF;
                previous_ministry:=(ministry#>>'{}')::bigint;
            END LOOP;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        -- Work stopped by a time limit (#293): the task type and the limit.
        ELSIF key='task_type' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='what' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('read_guard','lease','retention_budget','drive_copy_budget',
                'drive_retry_budget','drive_request','drive_probe_wait',
                'statement_timeout','lock_timeout','transaction_timeout','mail_helper','source_helper','provider_check',
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker') THEN RETURN false; END IF;
        -- An Administrator's decision on a suspended Chairperson seed: a closed
        -- word, and the reason entered for it, bounded text refused when it
        -- carries an address-like token, since this context holds no
        -- personal data.
        ELSIF key='decision' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('keep_role','restore','remove') THEN RETURN false; END IF;
        ELSIF key='review_reason' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value) NOT BETWEEN 1 AND 500
               OR position('@' in text_value)>0 THEN RETURN false; END IF;
        -- A hosted file (#346): its placeholder name and its stored type.
        ELSIF key IN ('file_slug','previous_file_slug') THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z0-9]+(-[a-z0-9]+)*$' THEN RETURN false; END IF;
        ELSIF key='file_kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('pdf','docx','xlsx','pptx','png','jpeg') THEN RETURN false; END IF;
        -- An on-request report page or download (#556): the system mode it
        -- showed and its closed filter choice (audit.schemas.REPORT_FILTERS).
        ELSIF key='report_mode' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('production','testing') THEN RETURN false; END IF;
        ELSIF key='report_filter' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('all','invited','uninvited','progressed',
                'opened','followed','unfollowed','mailing-name','envelope','any','cannot_serve',
                'cannot_attend','option') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key IN ('directory_phone','directory_response') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

-- Refuse to commit unless every object above is installed as declared. No
-- temporary objects: the migration login has no TEMP grant.
DO $check$
DECLARE definition text; missing text;
BEGIN
    IF to_regclass('public.stewardship_web_health') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_trigger
           WHERE tgrelid=to_regclass('public.stewardship_web_health')
             AND tgname='stewardship_web_health_guard' AND NOT tgisinternal)
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
           WHERE conrelid=to_regclass('public.stewardship_web_health')
             AND conname='web_health_shape' AND convalidated) THEN
        RAISE EXCEPTION 'The web health table or its trigger was not installed';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
     WHERE conrelid='public.stewardship_operational_log'::regclass
       AND conname='operational_event_safe' AND contype='c' AND convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'operational_event_safe is not installed and validated';
    END IF;
    SELECT string_agg(name, ', ') INTO missing
      FROM unnest(ARRAY[
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','report_audit_failed','report_shaping_failed',
        'task_started','task_completed',
        'task_failed','fact_drift','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_ministry_name_repaired','source_retention_skipped',
        'source_tenant_mismatch','source_destructive_change',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'mail_provider_failed',
        'due_work_lag',
        'credential_handoff_key_mismatch','setup_credential_staged','delivery_unknown',
        'setup_credential_scrubbed','campaign_boundary_lag','production_cleanup_failed',
        'authentication_health_observation_failed',
        'task_timed_out','helper_timed_out','work_budget_reached','task_lease_lost',
        'family_engagement_failed','service_status_failed','incident_recovered',
        'startup_waiting','startup_wait_ended','debug_logging_enabled',
        'refresh_lead_window_conflict','web_unhealthy']) AS name
     WHERE position(quote_literal(name) IN definition)=0;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'operational_event_safe does not admit: %', missing;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_ops_incident'::regclass
             AND conname='ops_incident_kind' AND convalidated
             AND pg_get_constraintdef(oid) LIKE '%''web_unhealthy''%'
             AND pg_get_constraintdef(oid) LIKE '%''automation_refused''%') THEN
        RAISE EXCEPTION 'ops_incident_kind does not admit web_unhealthy';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_ops_log_receipt_binding_v1()'::regprocedure
             AND prosecdef AND prosrc LIKE '%WHEN ''web_unhealthy'' THEN ''web_unhealthy''%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_ops_content_v1(uuid,text)'::regprocedure
             AND NOT prosecdef AND prosrc LIKE '%Web server is not responding%'
             AND prosrc LIKE '%docker compose ... restart web%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_safe_context_v1(text,jsonb)'::regprocedure
             AND NOT prosecdef AND provolatile='i'
             AND prosrc LIKE '%''web_health_check'',''web_unresponsive''%'
             AND prosrc LIKE '%''configuration_activation'',''web_probe''%') THEN
        RAISE EXCEPTION 'The web_unhealthy alert functions were not installed';
    END IF;
    IF NOT public.stewardship_safe_context_v1('failure',
           '{"failure":"web_unresponsive","failure_kind":"web_probe_timeout","count":3}'::jsonb)
       OR NOT public.stewardship_safe_context_v1('timeout','{"what":"web_probe"}'::jsonb) THEN
        RAISE EXCEPTION 'The context allowlist refuses the web health words';
    END IF;
END
$check$;
