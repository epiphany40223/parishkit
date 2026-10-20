-- Frozen forward migration file 0240 (the repository-wide file sequence;
-- provisional number, assigned centrally at merge): the audit index the user
-- timeline reads by (#1866, part of #1570; admin portal spec, User
-- timeline). This file is installed by its Django migration in the audit
-- app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. Every statement is safe to run again
-- (the users swap test reruns closure migrations), and the closing check
-- refuses an existing index of the same name that differs.
--
-- A user's timeline lists every audit record whose actor is one of the
-- user's Google accounts (stewardship_portal_user ids). The audit table had
-- no index on actor_id, so each view would read the whole table, which only
-- grows. audit_event_actor_time is a B-tree on (actor_id, created_at): the
-- read is actor_id = ANY(...) with a created_at order and optional time
-- bounds, which this index answers directly. Family session reads
-- (actor_id = a Family's campaign record id) can use it too.
--
-- Plain CREATE INDEX, not CONCURRENTLY: this file runs in the migration's
-- transaction (SET LOCAL, the DO block), where CONCURRENTLY is not allowed,
-- as 0117 does. The build holds a lock that blocks only writes to the audit
-- table, during the deploy in the maintenance window, when nothing writes.
-- Measured on a laptop (PostgreSQL 18.6 in Docker) with 2,000,000 audit-shaped
-- rows over 500 actors, the build took about 4 s; Production's audit table
-- is a small fraction of that (every Admin page view and Family session
-- adds rows, so it grows over a campaign) (#1866).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE INDEX IF NOT EXISTS audit_event_actor_time ON public.stewardship_audit_event USING btree (actor_id, created_at);

-- Refuse to commit unless the index is valid, ready and exactly the declared
-- definition, so an upgrade cannot report success with it missing or
-- different. It reads only the catalog and writes nothing.
DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
        WHERE i.relnamespace='public'::regnamespace
          AND i.relname='audit_event_actor_time'
          AND x.indisvalid AND x.indisready
          AND pg_get_indexdef(i.oid)='CREATE INDEX audit_event_actor_time ON public.stewardship_audit_event USING btree (actor_id, created_at)') THEN
        RAISE EXCEPTION 'Migration 0240 (audit actor index) is not installed as declared';
    END IF;
END
$check$;
