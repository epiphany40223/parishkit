-- Frozen forward migration file 0059 (the repository-wide file sequence):
-- imported past-campaign archives, kept as parish records (#1018, slice 2).
-- This file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced function equals
-- the fresh-install baseline's (schema/functions.sql). A fresh install runs
-- the baseline, the earlier files and then this; the baseline already
-- carries the replaced body, so the install ends in the same catalog as an
-- upgraded database.
--
-- The campaign comparison report reads past campaigns from archive
-- workbooks (campaign-history spec, "What is stored"). This file adds:
--
--   stewardship_campaign_archive         one row per import: the file's
--                                         SHA-256 and size, its metadata,
--                                         the recomputed totals and the
--                                         pledging Families' quartiles, and
--                                         its state, pending (a Review's
--                                         preview), active or removed (a
--                                         tombstone); one active row per
--                                         checksum and per year
--   stewardship_campaign_archive_day     the daily sheet
--   stewardship_campaign_archive_band    the pledge distribution, computed
--                                         when the file was checked
--   stewardship_campaign_archive_family  each kept Family's id system, id
--                                         and first campaign day, only
--
-- No Family's pledge, status, envelope number, amount, timestamp or note
-- is stored. They are parish records: no campaign purge touches them.
--
-- stewardship_campaign_archive_guard_v1 admits an INSERT only as a pending
-- row previewed by a current Administrator, and an UPDATE only from pending
-- to active (imported by a current Administrator) or from active to removed
-- (removed by one), each advancing the version by one and changing nothing
-- else; the times are the database's. It admits a DELETE only of a pending
-- row (expired-preview housekeeping). The child rows' guard admits a DELETE
-- only while their archive is pending or removed, and no UPDATE; a child
-- row is inserted only under a pending archive, by the Administrator who
-- previewed it.
--
-- stewardship_safe_context_v1 is replaced with 0050's body plus three
-- action keys (archive_year, archive_days and archive_family_rows, counts),
-- the timeout word archive_check and the helper archive_check_worker, so
-- the import's audit events and a Review's killed helper can be recorded.
-- It is not SECURITY DEFINER (the baseline has no ALTER for it), stays
-- IMMUTABLE with its fixed search_path, and keeps its grants. The file ends
-- with a DO block that refuses to commit unless all of this is installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced function's grants before the replacement, compared in the
-- DO block. A transaction-local setting holds them, not a temporary table:
-- the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0059_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'), true);

CREATE TABLE "stewardship_campaign_archive" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "checksum" varchar(64) NOT NULL, "file_name" varchar(255) NOT NULL, "file_size" integer NOT NULL CHECK ("file_size" >= 0), "schema_version" varchar(16) NOT NULL, "campaign_name" varchar(200) NOT NULL, "campaign_year" smallint NOT NULL CHECK ("campaign_year" >= 0), "is_final" boolean NOT NULL, "as_of" timestamp with time zone NOT NULL, "timezone" varchar(64) NOT NULL, "start_date" date NOT NULL, "last_submission_date" date NOT NULL, "active_families" integer NOT NULL CHECK ("active_families" >= 0), "active_families_provenance" text NOT NULL, "source_system" text NOT NULL, "dedup_rule" text NOT NULL, "family_id_systems" jsonb NOT NULL, "family_rows_kept" boolean NOT NULL, "notes" jsonb NOT NULL, "days" integer NOT NULL CHECK ("days" >= 0), "families_responded" integer NOT NULL CHECK ("families_responded" >= 0), "submission_rows" integer NOT NULL CHECK ("submission_rows" >= 0), "unattributed_rows" integer NOT NULL CHECK ("unattributed_rows" >= 0), "ministry_rows" integer NOT NULL CHECK ("ministry_rows" >= 0), "pledging_families" integer NOT NULL CHECK ("pledging_families" >= 0), "annual_pledged" numeric(18, 2) NOT NULL, "first_quartile" numeric(18, 2) NULL, "median" numeric(18, 2) NULL, "third_quartile" numeric(18, 2) NULL, "band_set_version" smallint NOT NULL CHECK ("band_set_version" >= 0), "state" varchar(8) NOT NULL, "imported_by_id" uuid NULL, "imported_at" timestamp with time zone NULL, "removed_by_id" uuid NULL, "removed_at" timestamp with time zone NULL, CONSTRAINT "stewardship_reports_campaignarchive_positive_version" CHECK ("version" >= 1), CONSTRAINT "campaign_archive_state" CHECK (((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('active'::character varying)::text, ('removed'::character varying)::text]))), CONSTRAINT "campaign_archive_values" CHECK (("active_families" >= 1 AND "annual_pledged" >= 0 AND "band_set_version" >= 1 AND "campaign_year" >= 2000 AND "campaign_year" <= 2100 AND "checksum"::text ~ '^[0-9a-f]{64}$' AND "days" >= 1 AND "file_size" >= 1 AND "file_size" <= 20971520 AND "last_submission_date" >= ("start_date") AND "pledging_families" <= ("families_responded"))), CONSTRAINT "campaign_archive_quartiles" CHECK ((("first_quartile" IS NULL AND "median" IS NULL AND "third_quartile" IS NULL) OR ("first_quartile" IS NOT NULL AND "first_quartile" <= ("median") AND "median" IS NOT NULL AND "median" <= ("third_quartile") AND "third_quartile" IS NOT NULL))), CONSTRAINT "campaign_archive_state_attribution" CHECK ((("imported_at" IS NULL AND "imported_by_id" IS NULL AND "removed_at" IS NULL AND "removed_by_id" IS NULL AND "state" = 'pending') OR ("imported_at" IS NOT NULL AND "imported_by_id" IS NOT NULL AND "removed_at" IS NULL AND "removed_by_id" IS NULL AND "state" = 'active') OR ("imported_at" IS NOT NULL AND "imported_by_id" IS NOT NULL AND "removed_at" IS NOT NULL AND "removed_by_id" IS NOT NULL AND "state" = 'removed'))));
CREATE TABLE "stewardship_campaign_archive_band" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "band_set_version" smallint NOT NULL CHECK ("band_set_version" >= 0), "band" smallint NOT NULL CHECK ("band" >= 0), "families" integer NOT NULL CHECK ("families" >= 0), "total" numeric(18, 2) NOT NULL, "archive_id" uuid NOT NULL, CONSTRAINT "campaign_archive_band_unique" UNIQUE ("archive_id", "band_set_version", "band"), CONSTRAINT "campaign_archive_band_values" CHECK (("band" >= 1 AND "band_set_version" >= 1 AND "total" >= 0)));
CREATE TABLE "stewardship_campaign_archive_day" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "day" integer NOT NULL CHECK ("day" >= 0), "local_date" date NOT NULL, "first_submissions" integer NOT NULL CHECK ("first_submissions" >= 0), "cumulative_families" integer NOT NULL CHECK ("cumulative_families" >= 0), "cumulative_pledging_families" integer NOT NULL CHECK ("cumulative_pledging_families" >= 0), "cumulative_annual_pledged" numeric(18, 2) NOT NULL, "archive_id" uuid NOT NULL, CONSTRAINT "campaign_archive_day_unique" UNIQUE ("archive_id", "day"), CONSTRAINT "campaign_archive_day_values" CHECK (("cumulative_annual_pledged" >= 0 AND "cumulative_pledging_families" <= ("cumulative_families") AND "day" >= 1)));
CREATE TABLE "stewardship_campaign_archive_family" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "family_id_system" varchar(16) NOT NULL, "family_id" bigint NOT NULL CHECK ("family_id" >= 0), "first_campaign_day" integer NOT NULL CHECK ("first_campaign_day" >= 0), "archive_id" uuid NOT NULL, CONSTRAINT "campaign_archive_family_unique" UNIQUE ("archive_id", "family_id_system", "family_id"), CONSTRAINT "campaign_archive_family_values" CHECK (("family_id" >= 1 AND (family_id_system)::text = ANY (ARRAY[('parishsoft_duid'::character varying)::text, ('pds_fid'::character varying)::text]) AND "first_campaign_day" >= 1)));
CREATE UNIQUE INDEX "campaign_archive_active_checksum" ON "stewardship_campaign_archive" ("checksum") WHERE "state" = 'active';
CREATE UNIQUE INDEX "campaign_archive_active_year" ON "stewardship_campaign_archive" ("campaign_year") WHERE "state" = 'active';
CREATE INDEX "stewardship_campaign_archive_correlation_id_ecd174ce" ON "stewardship_campaign_archive" ("correlation_id");
ALTER TABLE "stewardship_campaign_archive_band" ADD CONSTRAINT "stewardship_campaign_archive_id_1e76e860_fk_stewardsh" FOREIGN KEY ("archive_id") REFERENCES "stewardship_campaign_archive" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_campaign_archive_band_correlation_id_06d1bb98" ON "stewardship_campaign_archive_band" ("correlation_id");
CREATE INDEX "stewardship_campaign_archive_band_archive_id_1e76e860" ON "stewardship_campaign_archive_band" ("archive_id");
ALTER TABLE "stewardship_campaign_archive_day" ADD CONSTRAINT "stewardship_campaign_archive_id_73e040bc_fk_stewardsh" FOREIGN KEY ("archive_id") REFERENCES "stewardship_campaign_archive" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_campaign_archive_day_correlation_id_792b6661" ON "stewardship_campaign_archive_day" ("correlation_id");
CREATE INDEX "stewardship_campaign_archive_day_archive_id_73e040bc" ON "stewardship_campaign_archive_day" ("archive_id");
ALTER TABLE "stewardship_campaign_archive_family" ADD CONSTRAINT "stewardship_campaign_archive_id_53490772_fk_stewardsh" FOREIGN KEY ("archive_id") REFERENCES "stewardship_campaign_archive" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_campaign_archive_family_correlation_id_0bcdb539" ON "stewardship_campaign_archive_family" ("correlation_id");
CREATE INDEX "stewardship_campaign_archive_family_archive_id_53490772" ON "stewardship_campaign_archive_family" ("archive_id");

CREATE FUNCTION public.stewardship_campaign_archive_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        -- Expired-preview housekeeping; the child rows go first.
        IF OLD.state='pending' THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'Imported campaign archives are kept until removed' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'pending' OR NEW.version<>1
           OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,true)
           OR jsonb_typeof(NEW.notes)<>'object' OR octet_length(NEW.notes::text)>262144
           OR jsonb_typeof(NEW.family_id_systems)<>'array'
           OR octet_length(NEW.family_id_systems::text)>256
           OR length(NEW.active_families_provenance)>10000 OR length(NEW.source_system)>10000
           OR length(NEW.dedup_rule)>10000
        THEN RAISE EXCEPTION 'A campaign archive starts as a preview by a current Administrator'
            USING ERRCODE='23514'; END IF;
        NEW.created_at:=statement_timestamp();
        NEW.updated_at:=NEW.created_at;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW)-ARRAY['state','version','updated_at','imported_by_id','imported_at','removed_by_id','removed_at'])
           IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','version','updated_at','imported_by_id','imported_at','removed_by_id','removed_at'])
       OR NEW.version<>OLD.version+1
       OR NOT ((OLD.state='pending' AND NEW.state='active'
                AND public.stewardship_export_authorized_v1(NEW.imported_by_id,true))
            OR (OLD.state='active' AND NEW.state='removed'
                AND NEW.imported_by_id IS NOT DISTINCT FROM OLD.imported_by_id
                AND NEW.imported_at IS NOT DISTINCT FROM OLD.imported_at
                AND public.stewardship_export_authorized_v1(NEW.removed_by_id,true)))
    THEN RAISE EXCEPTION 'A campaign archive only becomes active, then removed' USING ERRCODE='23514'; END IF;
    -- Attribution times are database-owned.
    NEW.updated_at:=statement_timestamp();
    IF NEW.state='active' THEN NEW.imported_at:=NEW.updated_at; ELSE NEW.removed_at:=NEW.updated_at; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_campaign_archive_child_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' AND EXISTS(SELECT 1 FROM public.stewardship_campaign_archive a
           WHERE a.id=OLD.archive_id AND a.state IN ('pending','removed')) THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'Imported campaign archive rows are kept until their archive goes' USING ERRCODE='23514';
END $$;

CREATE FUNCTION public.stewardship_campaign_archive_child_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_campaign_archive a
           WHERE a.id=NEW.archive_id AND a.state='pending'
             AND a.actor_id IS NOT DISTINCT FROM NEW.actor_id)
    THEN RAISE EXCEPTION 'Campaign archive rows are added only to its preview' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_campaign_archive_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_campaign_archive
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_guard_v1();
CREATE TRIGGER stewardship_campaign_archive_day_guard
    BEFORE UPDATE OR DELETE ON public.stewardship_campaign_archive_day
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_child_guard_v1();
CREATE TRIGGER stewardship_campaign_archive_band_guard
    BEFORE UPDATE OR DELETE ON public.stewardship_campaign_archive_band
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_child_guard_v1();
CREATE TRIGGER stewardship_campaign_archive_family_guard
    BEFORE UPDATE OR DELETE ON public.stewardship_campaign_archive_family
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_child_guard_v1();
CREATE TRIGGER stewardship_campaign_archive_day_insert
    BEFORE INSERT ON public.stewardship_campaign_archive_day
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_child_insert_v1();
CREATE TRIGGER stewardship_campaign_archive_band_insert
    BEFORE INSERT ON public.stewardship_campaign_archive_band
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_child_insert_v1();
CREATE TRIGGER stewardship_campaign_archive_family_insert
    BEFORE INSERT ON public.stewardship_campaign_archive_family
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_archive_child_insert_v1();

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
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','directory_reach','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint',
            'report_mode','report_filter','talent_option_id','snapshot_id',
            'report_sort','directory_data_check','directory_response_columns',
            'archive_year','archive_days','archive_family_rows']
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
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome','command']
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
                'smtp_systemic','smtp_unavailable','web_health_check','web_unresponsive',
                'admin_command','admin_command_outcome_unknown') THEN RETURN false; END IF;
        -- An Admin command-line command (#617): its catalog name, lower-case
        -- words such as 'export create' (Python checks the catalog itself).
        ELSIF key='command' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
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
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe','source_load_budget',
                'archive_check') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker',
                'archive_check_worker') THEN RETURN false; END IF;
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
        -- A response list's sort order (#851): its closed sort token, a column
        -- key ascending or '-key' descending (audit.schemas.REPORT_SORTS).
        ELSIF key='report_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('family','-family',
                'duid','-duid','envelope','-envelope','submitted','-submitted',
                'submissions','-submissions','opened','-opened','progressed','-progressed',
                'invited','-invited','link','-link','last','-last','mailing','-mailing',
                'problem','-problem') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational','directory_response_columns') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key='directory_phone' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        -- The directory's Response filter (#933): its closed choices, plus the
        -- older yes and no that earlier entries recorded.
        ELSIF key='directory_response' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no','submitted',
                'more-than-once','started','progressed','opened-only','never-opened',
                'link-followed','link-not-followed','not-invited','not-submitted') THEN RETURN false; END IF;
        -- The directory's ParishSoft data to check filter (#933).
        ELSIF key='directory_data_check' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','anything','mailing-name','envelope') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid','duid_desc',
                'invited','invited_desc','link','link_desc','opened','opened_desc',
                'progressed','progressed_desc','submitted','submitted_desc','last','last_desc',
                'submissions','submissions_desc') THEN RETURN false; END IF;
        -- How campaign mail can reach the listed Families (#388 L1).
        ELSIF key='directory_reach' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','email','mail','neither') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

-- Refuse to commit unless everything is installed as declared. No
-- temporary objects: the migration login has no TEMP grant.
DO $check$
DECLARE child text;
BEGIN
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relname IN
            ('stewardship_campaign_archive','stewardship_campaign_archive_day',
             'stewardship_campaign_archive_band','stewardship_campaign_archive_family'))<>4 THEN
        RAISE EXCEPTION 'The campaign archive tables are not installed';
    END IF;
    IF (SELECT count(*) FROM pg_indexes WHERE schemaname='public'
        AND indexname IN ('campaign_archive_active_checksum','campaign_archive_active_year')
        AND indexdef LIKE 'CREATE UNIQUE INDEX%' AND indexdef LIKE '%WHERE ((state)::text = ''active''::text)%')<>2 THEN
        RAISE EXCEPTION 'The one-active-archive indexes are not installed';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.stewardship_campaign_archive'::regclass
           AND tgname='stewardship_campaign_archive_guard' AND tgenabled='O' AND NOT tgisinternal
           AND tgtype=31 AND tgfoid='public.stewardship_campaign_archive_guard_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'stewardship_campaign_archive is not guarded as declared';
    END IF;
    FOREACH child IN ARRAY ARRAY['day','band','family'] LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_trigger
               WHERE tgrelid=('public.stewardship_campaign_archive_'||child)::regclass
                 AND tgname='stewardship_campaign_archive_'||child||'_guard' AND tgenabled='O'
                 AND tgtype=27 AND tgfoid='public.stewardship_campaign_archive_child_guard_v1()'::regprocedure)
           OR NOT EXISTS(SELECT 1 FROM pg_trigger
               WHERE tgrelid=('public.stewardship_campaign_archive_'||child)::regclass
                 AND tgname='stewardship_campaign_archive_'||child||'_insert' AND tgenabled='O'
                 AND tgtype=7 AND tgfoid='public.stewardship_campaign_archive_child_insert_v1()'::regprocedure) THEN
            RAISE EXCEPTION 'stewardship_campaign_archive_% is not guarded as declared', child;
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_proc WHERE proname IN ('stewardship_campaign_archive_guard_v1',
               'stewardship_campaign_archive_child_guard_v1','stewardship_campaign_archive_child_insert_v1')
               AND (prosecdef OR prosrc NOT LIKE '%RAISE EXCEPTION%'))
       OR NOT EXISTS(SELECT 1 FROM pg_proc WHERE proname='stewardship_campaign_archive_guard_v1'
               AND prosrc LIKE '%OLD.state=''pending'' AND NEW.state=''active''%'
               AND prosrc LIKE '%OLD.state=''active'' AND NEW.state=''removed''%'
               AND prosrc LIKE '%NEW.version<>OLD.version+1%') THEN
        RAISE EXCEPTION 'The campaign archive guards lack their checks';
    END IF;
    -- The context allowlist admits the import's audit and timeout values,
    -- still refuses unknown ones and wrong types, and still admits the
    -- earlier contexts (the body is whole, not just this change).
    IF NOT public.stewardship_safe_context_v1(
           'action', '{"outcome": "succeeded", "archive_year": 2026, "archive_days": 40,
                       "archive_family_rows": 0, "count": 12, "version": 2,
                       "file_fingerprint": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'timeout', '{"what": "archive_check", "helper": "archive_check_worker",
                        "limit_seconds": 60, "elapsed_seconds": 61}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"directory_response": "never-opened", "report_sort": "-submitted"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'timeout', '{"what": "source_load_budget", "helper": "parishsoft_http_worker"}'::jsonb)
       OR public.stewardship_safe_context_v1('action', '{"archive_year": "2026"}'::jsonb)
       OR public.stewardship_safe_context_v1('timeout', '{"what": "archive"}'::jsonb)
       OR public.stewardship_safe_context_v1('timeout', '{"helper": "archive_check"}'::jsonb)
       OR public.stewardship_safe_context_v1('request', '{"archive_year": 2026}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit the archive values as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>1 THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0059_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1') THEN
        RAISE EXCEPTION 'The replaced function lost or changed its grants';
    END IF;
END
$check$;
