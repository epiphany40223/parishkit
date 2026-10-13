-- Frozen forward migration file 0082 (the repository-wide file sequence):
-- remember how a logo bundle's sizes were made, so Parish logos opens in the
-- Basic or Advanced mode that saved the current logos (#1062, slice 3;
-- ui-conventions spec, "Basic and Advanced modes"). This file is installed
-- by its Django migration in the accounts app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest.
-- A fresh install runs the baseline, the earlier files and then this, and
-- ends in the same catalog as an upgraded database.
--
-- It adds one nullable column, stewardship_branding_bundle.mode: 'basic'
-- when one image was resized into all four sizes, 'advanced' when the sizes
-- were uploaded separately, or NULL for campaign artwork and for every
-- bundle staged before this file (all of which were made from one image).
-- The mode is presentation only: it chooses which mode the page opens in
-- and is never read by SQL. It is written once, when the bundle is staged;
-- the bundle's guards read named columns only, so they are unchanged. The
-- web login already has table-level SELECT, INSERT and UPDATE on bundles,
-- which cover the new column.
--
-- No function, trigger or grant changes, and no Family code or link is
-- created, replaced or cancelled. The DO block at the end refuses to commit
-- unless the column and its check are installed as described. No temporary
-- objects: the migration login has no TEMP privilege. Reversing needs its
-- own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The check is written in PostgreSQL's deparsed form of the model's, as the
-- baseline's checks are, so it matches the model's declaration.
ALTER TABLE public.stewardship_branding_bundle
    ADD COLUMN mode character varying(8) NULL,
    ADD CONSTRAINT branding_bundle_mode CHECK (((mode IS NULL) OR ((mode)::text = ANY ((ARRAY['basic'::character varying, 'advanced'::character varying])::text[]))));

DO $check$
BEGIN
    -- The column, nullable, with no default: existing bundles keep NULL.
    IF NOT EXISTS (SELECT 1 FROM pg_attribute a
        WHERE a.attrelid='public.stewardship_branding_bundle'::regclass
          AND a.attname='mode' AND NOT a.attisdropped AND NOT a.attnotnull
          AND NOT a.atthasdef
          AND format_type(a.atttypid,a.atttypmod)='character varying(8)') THEN
        RAISE EXCEPTION 'Migration 0082: stewardship_branding_bundle.mode is missing';
    END IF;
    -- Its check admits only the two modes, or none (NULL, every earlier
    -- bundle and campaign artwork).
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
        WHERE c.conrelid='public.stewardship_branding_bundle'::regclass
          AND c.conname='branding_bundle_mode' AND c.contype='c' AND c.convalidated
          AND pg_get_constraintdef(c.oid) LIKE 'CHECK (((mode IS NULL) OR %'
          AND pg_get_constraintdef(c.oid) LIKE '%ARRAY[(''basic''::character varying)::text, (''advanced''::character varying)::text]%') THEN
        RAISE EXCEPTION 'Migration 0082: the branding bundle mode check is missing';
    END IF;
END
$check$;
