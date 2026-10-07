-- Frozen forward migration file 0010 (the repository-wide file sequence;
-- Django's stewardship_jobs.0009): an index on stewardship_task_run for
-- lookups by task type (#641). This file is installed by
-- jobs/migrations/0009_task_type_index.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. A later change gets its own numbered file. A fresh install runs the
-- baseline, 0002 to 0009 and then this, and ends in the same catalog as an
-- upgraded database.
--
-- The operational producers look for notices no Task owns yet with an
-- anti-join on (task_type, domain_request_id) over Tasks in every state, on
-- every scheduler loop. No index led with task_type (task_execution_key is
-- partial on idempotency_key), so each loop read the whole table (#629).
-- With this index that read is an index-only scan of one task type's
-- entries. It is not partial: a notice stays owned after its Task settles,
-- so the lookup must see settled Tasks too.
--
-- Plain CREATE INDEX, not CONCURRENTLY: this file runs in the migration's
-- transaction (SET LOCAL, the DO block), where CONCURRENTLY is not allowed.
-- The table holds tens of thousands of rows, so the build takes well under a
-- second (about 0.4 s for 100,000 rows) under a lock that blocks only writes
-- to this table, during the deploy.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE INDEX "task_type_request" ON "stewardship_task_run" ("task_type", "domain_request_id");

-- Refuse to commit unless the index is installed as declared: valid, on this
-- table, a plain B-tree over exactly these two columns in this order, both
-- ascending with the default NULLS LAST, with no predicate or expression.
DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_am am ON am.oid=c.relam
        WHERE c.relname='task_type_request'
          AND c.relnamespace='public'::regnamespace
          AND i.indrelid='public.stewardship_task_run'::regclass
          AND am.amname='btree' AND i.indisvalid AND NOT i.indisunique
          AND i.indpred IS NULL AND i.indexprs IS NULL
          AND i.indnatts=2
          AND i.indoption[0]=0 AND i.indoption[1]=0
          AND i.indkey[0]=(SELECT attnum FROM pg_attribute
              WHERE attrelid='public.stewardship_task_run'::regclass
                AND attname='task_type')
          AND i.indkey[1]=(SELECT attnum FROM pg_attribute
              WHERE attrelid='public.stewardship_task_run'::regclass
                AND attname='domain_request_id')) THEN
        RAISE EXCEPTION 'The task_type_request index was not created';
    END IF;
END
$check$;
