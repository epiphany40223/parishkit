-- Frozen forward migration file 0195 (the repository-wide file sequence):
-- the schema half of the dedicated off-site backup uploader's contract
-- release (#235; operations spec, "Dedicated off-site uploader"). This file
-- is installed by its Django migration in the jobs app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. It replaces nothing, so the fresh-install baseline is
-- unchanged: a fresh install runs the baseline, the earlier files and then
-- this. Reversing needs its own forward migration.
--
-- From this release on the off-site uploader alone copies backup sets, and
-- the backup login (pk_stewardship_backup_worker) loses two write grants
-- the runtime registry no longer declares:
--
--   INSERT on stewardship_backup_upload   the copy outcomes are the
--     uploader's (its own INSERT grant is untouched), so a compromised
--     backup image can no longer record a set as copied and silence the
--     off-site alert;
--   INSERT on stewardship_backup_run (the table grant)   database-grants
--     then grants the column INSERT on every column but completed_at
--     (runtime_grants.BACKUP_RUN_INSERT_COLUMNS), so the column's
--     STATEMENT_TIMESTAMP() default, the database's own clock, is the
--     completion time a set's name is bound to. The backup's insert names
--     exactly those columns (backup_commands.record_run).
--
-- Revoking a table privilege also revokes that privilege's column grants,
-- so a column INSERT granted before this file runs (the users upgrade's
-- base grants) goes too, and the grants after the full migration give it
-- back. The backup login reads both tables through pg_read_all_data, so
-- SELECT is left alone (revoking it would narrow nothing).
--
-- The login is created by database-roles, not by the schema, so a database
-- migrated before its roles exist has nothing to revoke. PUBLIC never held
-- INSERT on either table; it is revoked again so the check below can hold
-- it to that. database-grants never revokes (it refuses a login holding
-- more than it declares, and tolerates exactly these two grants until this
-- file applies: runtime_grants.CLOSURE_TABLE_REVOKES), so the revokes are
-- here, before database-grants runs in the same upgrade.
--
-- The DO block at the end refuses to commit unless the backup login, when
-- it exists, can insert into neither table except the run table's other
-- columns (completed_at never, by a table or a column grant), and PUBLIC
-- can insert into neither. No temporary objects: the migration login has
-- no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

REVOKE INSERT ON public.stewardship_backup_upload FROM PUBLIC;
REVOKE INSERT ON public.stewardship_backup_run FROM PUBLIC;

DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname='pk_stewardship_backup_worker') THEN
        REVOKE INSERT ON public.stewardship_backup_upload
            FROM pk_stewardship_backup_worker;
        REVOKE INSERT ON public.stewardship_backup_run
            FROM pk_stewardship_backup_worker;
    END IF;
END
$revoke$;

-- Refuse to commit unless everything above holds.
DO $check$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='pk_stewardship_backup_worker')
       AND (has_table_privilege('pk_stewardship_backup_worker',
                'public.stewardship_backup_upload', 'INSERT')
            OR has_any_column_privilege('pk_stewardship_backup_worker',
                'public.stewardship_backup_upload', 'INSERT')
            OR has_table_privilege('pk_stewardship_backup_worker',
                'public.stewardship_backup_run', 'INSERT')
            OR has_column_privilege('pk_stewardship_backup_worker',
                'public.stewardship_backup_run', 'completed_at', 'INSERT')) THEN
        RAISE EXCEPTION 'Migration 0195: the backup login can still record copy outcomes or a run''s completion time';
    END IF;
    IF has_table_privilege('public', 'public.stewardship_backup_upload', 'INSERT')
       OR has_any_column_privilege('public', 'public.stewardship_backup_upload', 'INSERT')
       OR has_table_privilege('public', 'public.stewardship_backup_run', 'INSERT')
       OR has_any_column_privilege('public', 'public.stewardship_backup_run', 'INSERT') THEN
        RAISE EXCEPTION 'Migration 0195: PUBLIC can insert backup records';
    END IF;
END
$check$;
