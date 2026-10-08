-- Frozen forward migration file 0021 (the repository-wide file sequence;
-- Django's stewardship_responses.0002): mark a response Staff entered for a
-- Family through Open form (#529). This file is installed by
-- responses/migrations/0002_staff_entered.py and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest. A fresh install runs the baseline, 0002 to 0020 and then this, and
-- ends in the same catalog as an upgraded database.
--
-- It adds one nullable column, stewardship_submission.entered_by_id: the
-- portal user (Staff or Administrator) who entered the response for the
-- Family through an Open form handoff, or NULL when the Family entered it
-- (every existing row). A soft reference like the table's actor_id, with no
-- foreign key, so the history outlives the portal user. The web login
-- already has table-level SELECT and INSERT on submissions, which cover the
-- new column; no other grant, table, function or trigger changes, and no
-- function here is SECURITY DEFINER. Submissions stay immutable.
--
-- No Family code or link is created, replaced or cancelled. The DO block at
-- the end refuses to commit unless the column is installed as described.
-- No temporary objects: the migration login has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_submission" ADD COLUMN "entered_by_id" uuid NULL;

DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname='stewardship_submission'
          AND a.attname='entered_by_id' AND NOT a.attisdropped
          AND a.atttypid='uuid'::regtype AND NOT a.attnotnull
          AND NOT a.atthasdef) THEN
        RAISE EXCEPTION 'stewardship_submission.entered_by_id is not installed as a nullable uuid without a default';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_submission WHERE entered_by_id IS NOT NULL) THEN
        RAISE EXCEPTION 'existing submissions must stay entered by their Families';
    END IF;
END
$check$;
