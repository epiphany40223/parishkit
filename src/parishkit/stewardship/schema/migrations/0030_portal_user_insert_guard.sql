-- Frozen forward migration file 0030 (the repository-wide file sequence): a
-- BEFORE INSERT guard on stewardship_portal_user (#389, the #352 review
-- residual). This file is installed by its Django migration and must never
-- change once released; tests/stewardship/test_schema_migration_files.py pins
-- its digest. A later change gets its own numbered file. A fresh install runs
-- the baseline, the earlier files and then this, and ends in the same catalog
-- as an upgraded database.
--
-- The SQL Admin checks trust PortalUser rows (#306 M2): the session
-- admission guard admits a session for any live user whose email matches an
-- Admin rule. Updates were already guarded; inserts were not. SQL cannot see
-- whether a Google sign-in happened, so this guard enforces only what it can:
-- the inserting login is web; verified_at lies inside the inserting
-- transaction (between its start and this statement), as the sign-in's
-- get_or_create stamps it with the database clock; and the row is not
-- disabled, is at version 1, names no actor, and has a non-empty subject and
-- email. It sets created_at and updated_at itself. A web bug that inserts a
-- user with a fresh database timestamp still passes; what it no longer can
-- is insert from another login, backdate or postdate the verification, or
-- insert a disabled, attributed or already-versioned row. Email and hosted
-- domain normalization are not checked here. The schema owner (migrations,
-- tests, repair) is exempt, as in the other guards.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_portal_user_insert_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RETURN NEW;
    END IF;
    IF current_user<>'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Only the web login records portal users' USING ERRCODE='42501';
    END IF;
    IF NEW.verified_at < transaction_timestamp()
       OR NEW.verified_at > statement_timestamp()
       OR NEW.disabled
       OR NEW.version<>1
       OR NEW.actor_id IS NOT NULL
       OR btrim(NEW.google_subject)=''
       OR btrim(NEW.email)='' THEN
        RAISE EXCEPTION 'A portal user insert needs an enabled new row with a verification inside this transaction'
            USING ERRCODE='23514';
    END IF;
    NEW.created_at := statement_timestamp();
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_portal_user_insert_guard_v1 BEFORE INSERT ON public.stewardship_portal_user
FOR EACH ROW EXECUTE FUNCTION public.stewardship_portal_user_insert_v1();
REVOKE ALL ON FUNCTION public.stewardship_portal_user_insert_v1() FROM PUBLIC;

-- Refuse to commit unless the guard is installed as declared: an enabled
-- row-level BEFORE INSERT trigger on the table calling this function, which
-- is an invoker with a fixed search_path.
DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgname='stewardship_portal_user_insert_guard_v1'
          AND t.tgrelid='public.stewardship_portal_user'::regclass
          AND t.tgenabled='O' AND NOT t.tgisinternal
          AND t.tgtype=7
          AND p.oid='public.stewardship_portal_user_insert_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION 'Migration 0030 (portal user insert guard) is not installed as declared';
    END IF;
END
$check$;
