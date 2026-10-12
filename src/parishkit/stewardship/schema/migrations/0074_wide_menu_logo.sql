-- Frozen forward migration file 0074 (the repository-wide file sequence):
-- a menu logo may be a wide wordmark, stored up to 512 x 128 pixels instead
-- of being fitted within 128 x 128 (#1062, slice 2). This file is installed
-- by its Django migration in the accounts app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copy of stewardship_branding_asset_insert_v1 still
-- equals the fresh-install baseline's (schema/functions.sql). A later change
-- gets its own numbered file. A fresh install runs the baseline, the earlier
-- files and then this; the baseline already carries the same body, so the
-- install ends in the same catalog as an upgraded database.
--
-- Every branding file's receipt row is checked on insert by this trigger
-- function, which refused any file wider or taller than its label's bound:
-- 1,024 for large and banner, 256 for section, 32 for favicon and 128 for
-- menu and icon. A wordmark four times as wide as it is tall was therefore
-- stored at 128 x 32 and drawn small and soft beside the parish name. The
-- menu label now allows 512 pixels of width; its height bound stays 128,
-- and every other label's bounds are unchanged. The table's own
-- branding_asset_bounds check (1 to 1,024 a side) already admits 512.
--
-- Replaced function, copied whole from its latest definition (the baseline,
-- schema/functions.sql; no earlier frozen file replaced it) with only these
-- changes:
--   stewardship_branding_asset_insert_v1   a menu file may be up to 512
--                                          pixels wide (widest); it now
--                                          sets search_path, as every
--                                          function replaced since #389 L9
--                                          should, so it leaves the
--                                          reviewed list of unpinned
--                                          invokers
-- No table, column, constraint, trigger or grant changes. The function
-- stays SECURITY INVOKER, so there is no ALTER FUNCTION to repeat; the DO
-- block below checks prosecdef, the setting, the owner and grants recorded
-- before the replacement, and that the asset table's insert trigger still
-- calls it. Existing files keep their stored size: only a new upload can be
-- wider. Reversing needs its own forward migration that restores the old
-- body (and would refuse no stored row, since the check runs on insert).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's owner and grants before the replacement (with how many
-- definitions there are), compared in the DO block. A transaction-local
-- setting holds them, not a temporary table: the migration login has no
-- TEMP privilege.
SELECT set_config('stewardship.migration_0074_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||p.proowner::regrole::text
        ||'/'||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_branding_asset_insert_v1'), true);

-- stewardship_branding_asset_insert_v1: the baseline body (functions.sql).
-- CREATE OR REPLACE resets every attribute it does not state; this states the
-- baseline's own (invoker rights, VOLATILE, the search_path setting).
CREATE OR REPLACE FUNCTION public.stewardship_branding_asset_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE selected public.stewardship_branding_bundle%ROWTYPE;
        maximum integer;
        widest integer;
BEGIN
    PERFORM pg_advisory_xact_lock(736230,1);
    SELECT * INTO selected FROM public.stewardship_branding_bundle
        WHERE id=NEW.bundle_id;
    maximum := CASE NEW.label
        WHEN 'large' THEN 1024 WHEN 'banner' THEN 1024 WHEN 'section' THEN 256
        WHEN 'favicon' THEN 32 ELSE 128 END;
    -- A menu logo may be a wide wordmark (#1062): up to 4 times as wide as
    -- its 128-pixel height. Every other label is fitted within a square.
    widest := CASE NEW.label WHEN 'menu' THEN 512 ELSE maximum END;
    IF NOT FOUND OR selected.state<>'writing'
       OR selected.expires_at<=clock_timestamp()
       OR NEW.actor_id IS DISTINCT FROM selected.owner_id
       OR NEW.width>widest OR NEW.height>maximum THEN
        RAISE EXCEPTION 'Invalid branding asset receipt' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;

DO $check$
BEGIN
    -- Installed once, as declared: invoker rights, VOLATILE, a trigger
    -- function with the fixed search_path and the wide menu bound.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_branding_asset_insert_v1'
          AND pg_get_function_identity_arguments(p.oid)=''
          AND NOT p.prosecdef AND p.provolatile='v' AND p.prorettype='trigger'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%widest := CASE NEW.label WHEN ''menu'' THEN 512 ELSE maximum END;%'
          AND p.prosrc LIKE '%OR NEW.width>widest OR NEW.height>maximum THEN%')<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_branding_asset_insert_v1')<>1
    THEN
        RAISE EXCEPTION 'Migration 0074 (wide menu logo bound) is not installed as declared';
    END IF;
    -- CREATE OR REPLACE keeps the owner and grants; prove it.
    IF current_setting('stewardship.migration_0074_acl', true) IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||p.proowner::regrole::text
            ||'/'||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_branding_asset_insert_v1') THEN
        RAISE EXCEPTION 'Migration 0074 changed stewardship_branding_asset_insert_v1''s owner or grants';
    END IF;
    -- The asset table's insert trigger still runs it, before each row.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_branding_asset'::regclass
          AND t.tgname='stewardship_branding_asset_insert_v1'
          AND t.tgfoid='public.stewardship_branding_asset_insert_v1()'::regprocedure
          AND t.tgenabled='O' AND NOT t.tgisinternal) THEN
        RAISE EXCEPTION 'Migration 0074: the branding asset insert trigger is missing';
    END IF;
END
$check$;
