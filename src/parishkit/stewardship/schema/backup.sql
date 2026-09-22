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
