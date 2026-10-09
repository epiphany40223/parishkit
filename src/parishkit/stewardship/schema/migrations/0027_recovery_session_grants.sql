-- Frozen forward migration file 0027 (the repository-wide file sequence):
-- narrow the offline admin-recovery login's grants on
-- stewardship_portal_session to the columns it uses (#389 L6). This file is
-- installed by its Django migration and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. A fresh install runs the baseline, the
-- earlier files and then this, and ends in the same catalog as an upgraded
-- database.
--
-- Until this release admin-recovery held whole-table SELECT and UPDATE on the
-- portal session table, although its only use is the recovery activation
-- trigger's revocation of every live session. Its declared grants are now
-- column grants (runtime_database.offline_columns), which database-grants
-- adds. database-grants never revokes: it refuses a login that already holds
-- more than it declares. So the whole-table grants are revoked here, before
-- database-grants runs in the same upgrade.
--
-- The role is created by database-roles, not by the schema, so a fresh
-- database migrated before its roles exist has nothing to revoke. Revoking
-- table-level SELECT and UPDATE leaves any column grants in place. No other
-- grant, table or function changes, so nothing in the schema baseline does.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname='pk_stewardship_admin_recovery') THEN
        REVOKE SELECT, UPDATE ON public.stewardship_portal_session
            FROM pk_stewardship_admin_recovery;
    END IF;
END
$revoke$;

-- Refuse to commit unless admin-recovery, when it exists, holds no
-- table-level SELECT or UPDATE on the session table (column grants are not
-- table-level privileges, so they do not count here).
DO $check$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname='pk_stewardship_admin_recovery')
       AND EXISTS (
           SELECT 1 FROM pg_class c,
               aclexplode(c.relacl) acl
           WHERE c.oid='public.stewardship_portal_session'::regclass
             AND acl.grantee=(SELECT oid FROM pg_roles
                              WHERE rolname='pk_stewardship_admin_recovery')
             AND acl.privilege_type IN ('SELECT','UPDATE')) THEN
        RAISE EXCEPTION 'Migration 0027 (recovery session grants) is not installed as declared';
    END IF;
END
$check$;
