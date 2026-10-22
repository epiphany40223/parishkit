-- Frozen forward migration file 0143 (the repository-wide file sequence,
-- assigned centrally): storage for what each Family form showed, the first
-- slice of the reviewed Family form (#1705; review-pipeline spec, "Shown
-- items"). This file is installed by its Django migration in the responses
-- app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- It adds two columns to the form baseline and the same two to the
-- immutable Submission:
--   reviewed_view  boolean, false by default: the form was drawn as the
--                  reviewed Family form;
--   shown_items    jsonb, NULL by default: for a reviewed form, one entry
--                  per census, Ministry or pledge item naming the immutable
--                  value the form showed (the entries' shape is the next
--                  slice's);
-- and on each table a check that pairs them: a form not drawn as the
-- reviewed form records no shown items, a reviewed form a JSON array.
--
-- Additive only. Every existing baseline and Submission keeps every value
-- it has and reads as not drawn as the reviewed form (false, NULL), which
-- the checks admit; no row is rewritten (the false default is a stored
-- constant). Nothing issues a reviewed form yet; the submit
-- copies both from the baseline it locks, unchanged, in its one existing
-- transaction. The baseline guard already refuses an UPDATE of any
-- column other than state, ended_at and version, so both are fixed once
-- the baseline is issued, and the Submission guard already refuses every
-- UPDATE.
--
-- No grant changes. The web login (Family pages, Open form) already has
-- table-level SELECT and INSERT on both tables, which cover the new
-- columns, and its baseline UPDATE stays limited to state, ended_at and
-- version. Every other login's column grants on submissions name only the
-- columns it reads, so none of them can read these.
--
-- No function or trigger is created or replaced, so there is no SECURITY
-- DEFINER to re-assert. No Family code, link or session is created,
-- revoked or changed. The DO block at the end refuses to commit unless the
-- columns and checks are installed as described and every existing row
-- reads as not drawn as the reviewed form. No temporary objects: the migration
-- login has no TEMP privilege. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_family_form_baseline" ADD COLUMN "reviewed_view" boolean DEFAULT false NOT NULL;
ALTER TABLE "stewardship_family_form_baseline" ADD COLUMN "shown_items" jsonb NULL;
ALTER TABLE "stewardship_family_form_baseline" ADD CONSTRAINT "family_baseline_shown_shape" CHECK (((NOT "reviewed_view" AND "shown_items" IS NULL) OR ("reviewed_view" AND "shown_items" IS NOT NULL AND jsonb_typeof("shown_items") = 'array')));

ALTER TABLE "stewardship_submission" ADD COLUMN "reviewed_view" boolean DEFAULT false NOT NULL;
ALTER TABLE "stewardship_submission" ADD COLUMN "shown_items" jsonb NULL;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "submission_shown_shape" CHECK (((NOT "reviewed_view" AND "shown_items" IS NULL) OR ("reviewed_view" AND "shown_items" IS NOT NULL AND jsonb_typeof("shown_items") = 'array')));

DO $check$
DECLARE
    tables text[] := ARRAY['stewardship_family_form_baseline','stewardship_submission'];
    shapes text[] := ARRAY['family_baseline_shown_shape','submission_shown_shape'];
BEGIN
    FOR i IN 1..2 LOOP
        -- reviewed_view: boolean, NOT NULL, default false.
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relname=tables[i]
              AND a.attname='reviewed_view' AND NOT a.attisdropped
              AND a.atttypid='boolean'::regtype AND a.attnotnull
              AND pg_get_expr(d.adbin, d.adrelid)='false') THEN
            RAISE EXCEPTION '%.reviewed_view is not installed as a boolean, not null, default false', tables[i];
        END IF;
        -- shown_items: nullable jsonb without a default.
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=tables[i]
              AND a.attname='shown_items' AND NOT a.attisdropped
              AND a.atttypid='jsonb'::regtype AND NOT a.attnotnull
              AND NOT a.atthasdef) THEN
            RAISE EXCEPTION '%.shown_items is not installed as a nullable jsonb without a default', tables[i];
        END IF;
        -- The pairing check, validated, on the right table, exactly as
        -- PostgreSQL deparses it.
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint k
            JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=tables[i]
              AND k.conname=shapes[i] AND k.contype='c' AND k.convalidated
              AND pg_get_constraintdef(k.oid)='CHECK ((((NOT reviewed_view) AND (shown_items IS NULL))'
                  ||' OR (reviewed_view AND (shown_items IS NOT NULL)'
                  ||' AND (jsonb_typeof(shown_items) = ''array''::text))))') THEN
            RAISE EXCEPTION '% is not installed on %', shapes[i], tables[i];
        END IF;
    END LOOP;
    -- Additive: every existing row reads as not drawn as the reviewed form.
    IF EXISTS (SELECT 1 FROM public.stewardship_family_form_baseline
               WHERE reviewed_view OR shown_items IS NOT NULL)
       OR EXISTS (SELECT 1 FROM public.stewardship_submission
                  WHERE reviewed_view OR shown_items IS NOT NULL) THEN
        RAISE EXCEPTION 'existing forms and responses must read as not drawn as the reviewed form';
    END IF;
END
$check$;
