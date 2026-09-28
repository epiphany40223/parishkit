-- One append-only row per completed, sealed backup set. The backup profile
-- inserts it after the sealed files are durable; the scheduler reads it to
-- decide whether the required backup is overdue, and offline upgrade commands
-- read it as the verified-backup evidence a configured deployment requires.
-- Nothing here names a path, a host or a credential.
CREATE TABLE "stewardship_backup_run" (
    "id" uuid NOT NULL PRIMARY KEY,
    "started_at" timestamp with time zone NOT NULL,
    "completed_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "database_bytes" bigint NOT NULL,
    "files_bytes" bigint NOT NULL,
    "manifest_digest" varchar(64) NOT NULL,
    "recipient_fingerprint" varchar(16) NOT NULL,
    "application_version" varchar(40) NOT NULL,
    CONSTRAINT "backup_run_times" CHECK (("started_at" <= "completed_at")),
    CONSTRAINT "backup_run_sizes" CHECK ((("database_bytes" >= 0) AND ("files_bytes" >= 0))),
    CONSTRAINT "backup_run_digests" CHECK ((("manifest_digest")::text ~ '^[0-9a-f]{64}$' AND ("recipient_fingerprint")::text ~ '^[0-9a-f]{16}$'))
);

CREATE FUNCTION stewardship_backup_run_immutable_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Historical records are append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER stewardship_backup_run_immutable_guard_v1
BEFORE UPDATE OR DELETE ON stewardship_backup_run
FOR EACH ROW EXECUTE FUNCTION stewardship_backup_run_immutable_v1();

-- One append-only row per off-site copy attempt outcome for a completed set.
-- The backup profile inserts it after trying to copy the set to the Google
-- Drive folder the "backup" integration names: "uploaded" when every sealed
-- file is verified there, "failed" with a fixed category otherwise, and
-- "disabled" once when the destination is removed. The scheduler alerts while
-- the newest row is "failed"; pages show the newest outcome. Rows name a
-- Drive folder ID and set name only: no paths, hosts or credentials.
CREATE TABLE "stewardship_backup_upload" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "state" varchar(16) NOT NULL,
    "set_name" varchar(16) NULL,
    "manifest_digest" varchar(64) NULL,
    "folder_id" varchar(200) NULL,
    "failure_kind" varchar(32) NULL,
    CONSTRAINT "backup_upload_state" CHECK (((state)::text = ANY ((ARRAY['uploaded'::character varying, 'failed'::character varying, 'disabled'::character varying])::text[]))),
    CONSTRAINT "backup_upload_failure_kind" CHECK (((failure_kind IS NULL) OR ((failure_kind)::text = ANY ((ARRAY['authorization'::character varying, 'api_disabled'::character varying, 'not_found'::character varying, 'permission'::character varying, 'not_folder'::character varying, 'credential'::character varying, 'verification'::character varying, 'unavailable'::character varying, 'unexpected'::character varying, 'unanswered'::character varying])::text[])))),
    CONSTRAINT "backup_upload_shape" CHECK ((((failure_kind IS NULL) AND (folder_id IS NOT NULL) AND (manifest_digest IS NOT NULL) AND (set_name IS NOT NULL) AND ((state)::text = 'uploaded'::text)) OR ((failure_kind IS NOT NULL) AND (folder_id IS NOT NULL) AND ((state)::text = 'failed'::text)) OR ((failure_kind IS NULL) AND (folder_id IS NULL) AND (manifest_digest IS NULL) AND (set_name IS NULL) AND ((state)::text = 'disabled'::text)))),
    CONSTRAINT "backup_upload_values" CHECK ((((set_name IS NULL) OR ((set_name)::text ~ '^[0-9]{8}T[0-9]{6}Z$'::text)) AND ((manifest_digest IS NULL) OR ((manifest_digest)::text ~ '^[0-9a-f]{64}$'::text)) AND ((folder_id IS NULL) OR ((folder_id)::text ~ '^[A-Za-z0-9_-]{10,200}$'::text))))
);
CREATE INDEX "backup_upload_newest" ON "stewardship_backup_upload" ("created_at" DESC);
CREATE TRIGGER stewardship_backup_upload_immutable_guard_v1
BEFORE UPDATE OR DELETE ON stewardship_backup_upload
FOR EACH ROW EXECUTE FUNCTION stewardship_backup_run_immutable_v1();

-- An Administrator's request to check that the Drive folder accepts backups.
-- The web inserts it pending; the Google Workspace credential installer, which
-- alone holds the key and network egress, writes a small file, trashes it and
-- records the outcome once. The subject is the delegated mailbox user the
-- copy impersonates. Completed rows never change.
CREATE TABLE "stewardship_backup_drive_probe" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "requested_by_id" uuid NOT NULL,
    "folder_id" varchar(200) NOT NULL,
    "subject" varchar(254) NOT NULL,
    "state" varchar(16) DEFAULT 'pending'::character varying NOT NULL,
    "failure_kind" varchar(32) NULL,
    "completed_at" timestamp with time zone NULL,
    CONSTRAINT "backup_probe_state" CHECK (((state)::text = ANY ((ARRAY['pending'::character varying, 'succeeded'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT "backup_probe_folder" CHECK (((folder_id)::text ~ '^[A-Za-z0-9_-]{10,200}$'::text)),
    CONSTRAINT "backup_probe_shape" CHECK ((((completed_at IS NULL) AND (failure_kind IS NULL) AND ((state)::text = 'pending'::text)) OR ((completed_at IS NOT NULL) AND (failure_kind IS NULL) AND ((state)::text = 'succeeded'::text)) OR ((completed_at IS NOT NULL) AND (failure_kind IS NOT NULL) AND ((state)::text = 'failed'::text)))),
    CONSTRAINT "backup_probe_failure_kind" CHECK (((failure_kind IS NULL) OR ((failure_kind)::text = ANY ((ARRAY['authorization'::character varying, 'api_disabled'::character varying, 'not_found'::character varying, 'permission'::character varying, 'not_folder'::character varying, 'credential'::character varying, 'verification'::character varying, 'unavailable'::character varying, 'unexpected'::character varying, 'unanswered'::character varying])::text[]))))
);
CREATE INDEX "backup_probe_pending" ON "stewardship_backup_drive_probe" ("created_at") WHERE ((state)::text = 'pending'::text);

-- SECURITY DEFINER (never callable directly) so an insert is compared with the
-- applied Workspace settings, which the inserting web role cannot choose.
CREATE FUNCTION stewardship_backup_probe_guard_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public', 'pg_temp' AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'pending' OR NEW.completed_at IS NOT NULL THEN
            RAISE EXCEPTION 'A backup access check starts pending' USING ERRCODE='23514';
        END IF;
        -- The installer impersonates the subject with the Workspace key, so a
        -- check may only name the applied delegated mailbox user: a web
        -- process cannot make the installer act as any other domain user.
        IF NEW.subject IS DISTINCT FROM (
            SELECT workspace.settings->>'delegated_email'
            FROM public.stewardship_system_configuration runtime
            JOIN public.stewardship_applied_integration workspace
                ON workspace.configuration_id=runtime.active_configuration_id
                AND workspace.kind='google_workspace'
        ) THEN
            RAISE EXCEPTION 'A backup access check uses the applied Workspace mailbox user' USING ERRCODE='23514';
        END IF;
        NEW.created_at := statement_timestamp();
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' OR OLD.state <> 'pending' OR NEW.state = 'pending'
       OR ROW(NEW.id, NEW.created_at, NEW.requested_by_id, NEW.folder_id, NEW.subject)
          IS DISTINCT FROM ROW(OLD.id, OLD.created_at, OLD.requested_by_id, OLD.folder_id, OLD.subject) THEN
        RAISE EXCEPTION 'A backup access check completes once' USING ERRCODE='23514';
    END IF;
    NEW.completed_at := statement_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION stewardship_backup_probe_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_backup_probe_guard_v1
BEFORE INSERT OR UPDATE OR DELETE ON stewardship_backup_drive_probe
FOR EACH ROW EXECUTE FUNCTION stewardship_backup_probe_guard_v1();
