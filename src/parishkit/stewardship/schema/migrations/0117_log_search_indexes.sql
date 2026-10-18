-- Frozen forward migration file 0117 (the repository-wide file sequence,
-- number assigned centrally): indexes for the System logs text search
-- (#765; admin portal spec, System logs, text search). This file is
-- installed by its Django migration in the audit app and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. A later change gets its own numbered file. A fresh install runs
-- the baseline, the earlier files and then this, and ends in the same
-- catalog as an upgraded database. Every statement is safe to run again
-- (the users swap test reruns closure migrations), and the closing check
-- refuses an existing object of the same name that differs.
--
-- The search (audit/log_search.py) ORs a type condition with a detail
-- condition. Without indexes it read every entry the other filters left,
-- twice per page view (#536: about 3 s per million entries). These six let
-- PostgreSQL answer every arm of that OR from an index and combine them in
-- one bitmap scan:
--   operational_value_trgm, audit_context_value_trgm
--       trigram GIN on the text of every context value, exactly the
--       UPPER(...) expression Django's icontains compares, so the query
--       needs no new lookup;
--   operational_event_trgm, audit_event_type_trgm
--       trigram GIN on the stored type, for the same icontains;
--   operational_event_pattern, audit_event_type_pattern
--       pattern B-trees on the type, for the listed types (=, IN) and the
--       trigger prefixes (LIKE 'prefix%'), which the default collation's
--       B-tree cannot serve.
--
-- pg_trgm ships with the pinned PostgreSQL image and is a trusted
-- extension, but creating it needs CREATE on the database, which the
-- migration login does not have (database_provisioning revokes it from
-- PUBLIC). The operator superuser therefore installs it first: database-roles
-- on a fresh install, and on an upgrade tools/stewardship-upgrade-host.sh or
-- the deployment runbook's one-time step. Here IF NOT EXISTS then only
-- notices it; a database without it refuses this migration with
-- "permission denied to create extension", and nothing is applied.
-- Superuser test and scratch databases (CI, restore-compare) install it
-- here. It lives in public so that a database restore, which empties and
-- reloads public, drops and re-creates it with the dump.
--
-- Plain CREATE INDEX, not CONCURRENTLY: this file runs in the migration's
-- transaction (SET LOCAL, the DO block), where CONCURRENTLY is not allowed,
-- as 0010 and 0012 do. Each build holds a lock that blocks only writes to
-- its table, during the deploy, when nothing writes. Measured on a laptop
-- with 2,000,000 operational entries, 2,000,000 audit events and 200,000
-- audit contexts, the six together took about half a minute; Production's
-- logs are a small fraction of that (#765).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

CREATE INDEX IF NOT EXISTS operational_value_trgm ON public.stewardship_operational_log USING gin (upper((jsonb_path_query_array(context, '$.*'::jsonpath))::text) public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS operational_event_trgm ON public.stewardship_operational_log USING gin (upper((event)::text) public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS operational_event_pattern ON public.stewardship_operational_log USING btree (event varchar_pattern_ops);
CREATE INDEX IF NOT EXISTS audit_context_value_trgm ON public.stewardship_audit_context USING gin (upper((jsonb_path_query_array(context, '$.*'::jsonpath))::text) public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS audit_event_type_trgm ON public.stewardship_audit_event USING gin (upper((event_type)::text) public.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS audit_event_type_pattern ON public.stewardship_audit_event USING btree (event_type varchar_pattern_ops);

-- Refuse to commit unless the extension is installed in public and each
-- index is valid, ready and exactly the declared definition, so an upgrade
-- cannot report success with one missing or different. No temporary
-- objects: the migration login has no TEMP grant.
DO $check$
DECLARE expected text[] := ARRAY[
    'CREATE INDEX operational_value_trgm ON public.stewardship_operational_log USING gin (upper((jsonb_path_query_array(context, ''$.*''::jsonpath))::text) gin_trgm_ops)',
    'CREATE INDEX operational_event_trgm ON public.stewardship_operational_log USING gin (upper((event)::text) gin_trgm_ops)',
    'CREATE INDEX operational_event_pattern ON public.stewardship_operational_log USING btree (event varchar_pattern_ops)',
    'CREATE INDEX audit_context_value_trgm ON public.stewardship_audit_context USING gin (upper((jsonb_path_query_array(context, ''$.*''::jsonpath))::text) gin_trgm_ops)',
    'CREATE INDEX audit_event_type_trgm ON public.stewardship_audit_event USING gin (upper((event_type)::text) gin_trgm_ops)',
    'CREATE INDEX audit_event_type_pattern ON public.stewardship_audit_event USING btree (event_type varchar_pattern_ops)'];
    definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_extension
        WHERE extname='pg_trgm' AND extnamespace='public'::regnamespace) THEN
        RAISE EXCEPTION 'Migration 0117 (log search indexes) needs the pg_trgm extension in public';
    END IF;
    FOREACH definition IN ARRAY expected LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
            WHERE i.relnamespace='public'::regnamespace
              AND x.indisvalid AND x.indisready
              AND pg_get_indexdef(i.oid)=definition) THEN
            RAISE EXCEPTION 'Migration 0117 (log search indexes) index is not installed as declared: %', definition;
        END IF;
    END LOOP;
END
$check$;
