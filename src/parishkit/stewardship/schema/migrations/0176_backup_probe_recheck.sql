-- Frozen forward migration file 0176 (the repository-wide file sequence):
-- the Google Workspace credential installer re-checks a queued off-site
-- backup "Test access" check against the applied settings when it runs it
-- (#269). This file is installed by its Django migration in the jobs app
-- and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- The installer impersonates a check's subject with the Workspace key.
-- stewardship_backup_probe_guard_v1 compares the subject with the applied
-- delegated mailbox user only when the web inserts the check, so a check
-- queued just before an Administrator changes that user (for example away
-- from a compromised account) would still run as the old user. The
-- installer cannot read the applied settings itself (it has no grant on
-- stewardship_applied_integration, whose other rows it must not see), so
-- stewardship_backup_destination_v1() returns just the applied backup
-- folder link and delegated mailbox user. It is a SECURITY DEFINER with a
-- fixed search_path and no arguments; only the Google Workspace installer
-- login is granted EXECUTE (runtime_grants.runtime_functions), and the body
-- refuses any other session login that is not a member of the schema owner.
-- Both values are NULL when not configured. Only the mailbox is compared
-- today; the folder link is returned for the planned backup uploader
-- (#235), which will still need its own grant and caller check.
--
-- A check closed because the settings changed records the new failure kind
-- settings_changed, so the backup_probe_failure_kind CHECK is dropped and
-- added again with it (schema/backup.sql holds the same final definition).
--
-- The DO block at the end refuses to commit unless the definer and the
-- CHECK are installed as declared. No temporary objects: the migration
-- login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE public.stewardship_backup_drive_probe
    DROP CONSTRAINT "backup_probe_failure_kind";
ALTER TABLE public.stewardship_backup_drive_probe
    ADD CONSTRAINT "backup_probe_failure_kind" CHECK (((failure_kind IS NULL) OR ((failure_kind)::text = ANY ((ARRAY['authorization'::character varying, 'api_disabled'::character varying, 'not_found'::character varying, 'permission'::character varying, 'not_folder'::character varying, 'credential'::character varying, 'verification'::character varying, 'unavailable'::character varying, 'unexpected'::character varying, 'unanswered'::character varying, 'settings_changed'::character varying])::text[]))));

CREATE FUNCTION public.stewardship_backup_destination_v1(OUT backup_target text, OUT delegated_email text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_credential_google_workspace' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the Google Workspace installer reads the backup destination'
            USING ERRCODE='42501';
    END IF;
    SELECT backup.settings->>'target', workspace.settings->>'delegated_email'
    INTO backup_target, delegated_email
    FROM public.stewardship_system_configuration runtime
    LEFT JOIN public.stewardship_applied_integration backup
        ON backup.configuration_id=runtime.active_configuration_id
        AND backup.kind='backup'
    LEFT JOIN public.stewardship_applied_integration workspace
        ON workspace.configuration_id=runtime.active_configuration_id
        AND workspace.kind='google_workspace';
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_backup_destination_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_backup_destination_v1() FROM PUBLIC;

-- Refuse to commit unless the definer is installed as declared (a stable
-- definer with the fixed search_path, the caller check, both settings and
-- no PUBLIC execute) and the CHECK admits settings_changed. It reads only
-- the catalog. The missing function is checked first, on its own, because
-- PostgreSQL does not short-circuit OR and the ::regprocedure casts below
-- would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_backup_destination_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0176 (backup probe re-check) is not installed as declared';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_backup_destination_v1()'::regprocedure
             AND p.prosecdef AND p.provolatile='s' AND NOT p.proretset
             AND p.prorettype='record'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_credential_google_workspace''%'
             AND p.prosrc LIKE '%AND NOT pg_has_role(session_user,%'
             AND p.prosrc LIKE '%backup.kind=''backup''%'
             AND p.prosrc LIKE '%workspace.kind=''google_workspace''%')
       OR has_function_privilege('public',
           'public.stewardship_backup_destination_v1()', 'EXECUTE')
       OR NOT EXISTS (SELECT 1 FROM pg_constraint c
           WHERE c.conrelid='public.stewardship_backup_drive_probe'::regclass
             AND c.conname='backup_probe_failure_kind'
             AND pg_get_constraintdef(c.oid) LIKE '%''settings_changed''%') THEN
        RAISE EXCEPTION 'Migration 0176 (backup probe re-check) is not installed as declared';
    END IF;
END
$check$;
