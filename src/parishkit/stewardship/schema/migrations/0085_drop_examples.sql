-- Frozen forward migration file 0085 (the repository-wide file sequence):
-- the example Families of a refused large ParishSoft change (ADM-13 PR 5c,
-- #530). This file is installed by its Django migration in the source app
-- and must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. A later change gets its own numbered file. A fresh install
-- runs the baseline, the earlier forward files and then this. Reversing needs
-- its own forward migration.
--
-- When a full refresh is refused because a count fell too far, the worker
-- records, with the refusal, up to five example Families for each failing
-- count that concerns Families, Members or contacts: Families the current
-- promoted data counts but the refused load no longer does
-- (parishkit.stewardship.source.drop_examples). The Administrator looks them
-- up in ParishSoft before accepting the change once. Each example keeps only
-- the Family's DUID and its name as the active parishioner family directory
-- shows it; nothing else from the refused load is kept.
--
-- stewardship_source_drop_example: the refused attempt, the count, the
-- position (1 to 5, so never more than five per count), the Family DUID and
-- name, and when it was recorded.
--
-- The guard is the boundary:
--   INSERT: only the worker login (the schema owner is not checked), only
--     with the refusal (the attempt's failing drop count for the same count
--     was recorded no earlier than this transaction began, which in practice
--     is the refusal's own transaction), and never for a load with no
--     Families or no Members, which can never be accepted. The time comes
--     from the database clock. The rule cannot tell another transaction that
--     began before the counts were committed from the refusal's own; only
--     the worker login can insert at all, so that narrow window is accepted.
--   UPDATE: never, by anyone.
--   DELETE: only the worker login (its hourly housekeeping) and the schema
--     owner (the restore procedure).
-- Invoker's rights with a fixed search_path.
--
-- stewardship_source_drop_example_attempt_v1() names the one refusal whose
-- examples may be shown, or NULL: the newest refusal, if no full refresh has
-- been promoted since, it has not been accepted, and it is less than seven
-- days old. The page and the command read only that refusal's examples, and
-- the worker's housekeeping deletes every other example, so the two cannot
-- disagree, and an example whose deletion is due is never shown even before
-- it is deleted (or after a restore brings it back). Invoker's rights, STABLE,
-- fixed search_path: it reads only what its caller may read, so it keeps
-- PostgreSQL's default PUBLIC EXECUTE, as
-- stewardship_source_drop_acceptance_active_v1 does, and needs no grant of
-- its own. A login without SELECT on the tables it reads gets an error,
-- never an answer. No SECURITY DEFINER routine is created.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_source_drop_example" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "attempt_id" uuid NOT NULL, "measure" varchar(32) NOT NULL, "position" smallint NOT NULL CHECK ("position" >= 0), "family_duid" bigint NOT NULL CHECK ("family_duid" >= 0), "family_name" varchar(300) NOT NULL, CONSTRAINT "source_drop_example_position" UNIQUE ("attempt_id", "measure", "position"), CONSTRAINT "source_drop_example_family" UNIQUE ("attempt_id", "measure", "family_duid"), CONSTRAINT "source_drop_example_place" CHECK (("position" >= 1 AND "position" <= 5)), CONSTRAINT "source_drop_example_family_shape" CHECK (("family_duid" > 0 AND NOT ("family_name" = ''))));
ALTER TABLE "stewardship_source_drop_example" ADD CONSTRAINT "source_drop_example_measure" CHECK (((measure)::text = ANY (ARRAY[('family'::character varying)::text, ('member'::character varying)::text, ('portal_eligible_families'::character varying)::text, ('email_eligible_families'::character varying)::text, ('active_head_families'::character varying)::text, ('valid_email_contacts'::character varying)::text])));
ALTER TABLE "stewardship_source_drop_example" ADD CONSTRAINT "stewardship_source_d_attempt_id_c1ad1892_fk_stewardsh" FOREIGN KEY ("attempt_id") REFERENCES "stewardship_source_refresh_attempt" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_source_drop_example_attempt_id_c1ad1892" ON "stewardship_source_drop_example" ("attempt_id");

CREATE FUNCTION public.stewardship_source_drop_example_attempt_v1() RETURNS uuid
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT r.attempt_id
      FROM (SELECT c.attempt_id, c.created_at AS refused_at
              FROM public.stewardship_source_drop_count c
             ORDER BY c.created_at DESC, c.id DESC
             LIMIT 1) r
     WHERE r.refused_at>statement_timestamp()-interval '7 days'
       AND NOT EXISTS(SELECT 1 FROM public.stewardship_source_snapshot s
                       WHERE s.kind='full' AND s.state='promoted'
                         AND s.promoted_at>=r.refused_at)
       AND NOT EXISTS(SELECT 1 FROM public.stewardship_source_drop_acceptance a
                       WHERE a.attempt_id=r.attempt_id)
$$;

CREATE FUNCTION public.stewardship_source_drop_example_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean := pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
BEGIN
    IF TG_OP='UPDATE' THEN
        RAISE EXCEPTION 'An example Family of a refused change is never changed' USING ERRCODE='42501';
    END IF;
    IF NOT owner AND current_user IS DISTINCT FROM 'pk_stewardship_worker' THEN
        RAISE EXCEPTION 'Only the worker records or removes example Families' USING ERRCODE='42501';
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    IF owner THEN
        RETURN NEW;
    END IF;
    -- Recorded with the refusal: the same count failed, and its drop count
    -- was recorded no earlier than this transaction began.
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_source_drop_count c
                   WHERE c.attempt_id=NEW.attempt_id AND c.measure=NEW.measure
                     AND c.failed AND c.created_at>=transaction_timestamp()) THEN
        RAISE EXCEPTION 'An example Family is recorded with its refusal, for a count that failed' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.stewardship_source_drop_count c
               WHERE c.attempt_id=NEW.attempt_id
                 AND c.measure IN ('family','member') AND c.after=0) THEN
        RAISE EXCEPTION 'A load with no Families or no Members has no example Families' USING ERRCODE='23514';
    END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_source_drop_example_guard_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_source_drop_example_guard BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_source_drop_example
FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_drop_example_guard_v1();

-- Refuse to commit unless the table (with no PUBLIC privilege), its keys,
-- checks and index, the guard (invoker's rights, fixed search_path, no
-- PUBLIC EXECUTE) and its trigger, and the shown-refusal function
-- (invoker's rights, STABLE, fixed search_path) are all installed as
-- declared. Missing objects are checked first, on their own, because
-- PostgreSQL does not short-circuit OR and the ::regprocedure casts below
-- would otherwise fail with their own error.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_source_drop_example') IS NULL
       OR to_regprocedure('public.stewardship_source_drop_example_guard_v1()') IS NULL
       OR to_regprocedure('public.stewardship_source_drop_example_attempt_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0085 (example Families) is not installed as declared';
    END IF;
    -- Each key and check with its definition, so neither the five-per-count
    -- limit nor a key can be loosened under the same name.
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.stewardship_source_drop_example'::regclass
          AND convalidated
          AND ((contype='u' AND conname='source_drop_example_position'
                AND pg_get_constraintdef(oid)='UNIQUE (attempt_id, measure, "position")')
               OR (contype='u' AND conname='source_drop_example_family'
                AND pg_get_constraintdef(oid)='UNIQUE (attempt_id, measure, family_duid)')
               OR (contype='c' AND conname='source_drop_example_place'
                AND pg_get_constraintdef(oid)='CHECK ((("position" >= 1) AND ("position" <= 5)))')
               OR (contype='c' AND conname='source_drop_example_family_shape'
                AND pg_get_constraintdef(oid)='CHECK (((family_duid > 0) AND (NOT ((family_name)::text = ''''::text))))')
               OR (contype='c' AND conname='source_drop_example_measure'
                AND pg_get_constraintdef(oid) LIKE '%''family''%''valid_email_contacts''%'
                AND pg_get_constraintdef(oid) NOT LIKE '%''ministry''%')
               OR (contype='f' AND confrelid='public.stewardship_source_refresh_attempt'::regclass)))<>6 THEN
        RAISE EXCEPTION 'The example Family keys and checks were not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        WHERE c.relname='stewardship_source_drop_example_attempt_id_c1ad1892'
          AND i.indrelid='public.stewardship_source_drop_example'::regclass
          AND i.indisvalid) THEN
        RAISE EXCEPTION 'The example Family index was not created';
    END IF;
    -- PUBLIC holds no privilege on the table (grantee 0 in its ACL).
    IF EXISTS(SELECT 1 FROM pg_class c, aclexplode(coalesce(c.relacl,
                  acldefault('r',c.relowner))) a
              WHERE c.oid='public.stewardship_source_drop_example'::regclass
                AND a.grantee=0) THEN
        RAISE EXCEPTION 'PUBLIC holds a privilege on the example Families';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_source_drop_example_guard_v1()'::regprocedure
          AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%never changed%'
          AND p.prosrc LIKE '%current_user IS DISTINCT FROM ''pk_stewardship_worker''%'
          AND p.prosrc LIKE '%c.failed AND c.created_at>=transaction_timestamp()%'
          AND p.prosrc LIKE '%c.measure IN (''family'',''member'') AND c.after=0%'
          AND p.prosrc LIKE '%NEW.created_at:=statement_timestamp()%')
       OR has_function_privilege('public',
            'public.stewardship_source_drop_example_guard_v1()', 'EXECUTE') THEN
        RAISE EXCEPTION 'The example Family guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_source_drop_example'::regclass
          AND t.tgname='stewardship_source_drop_example_guard' AND t.tgenabled='O'
          AND t.tgfoid='public.stewardship_source_drop_example_guard_v1()'::regprocedure
          AND t.tgtype=31) THEN
        RAISE EXCEPTION 'The example Family guard trigger was not created';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid='public.stewardship_source_drop_example_attempt_v1()'::regprocedure
          AND NOT p.prosecdef AND p.provolatile='s' AND NOT p.proretset
          AND p.prorettype='uuid'::regtype
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%r.refused_at>statement_timestamp()-interval ''7 days''%'
          AND p.prosrc LIKE '%s.promoted_at>=r.refused_at%'
          AND p.prosrc LIKE '%a.attempt_id=r.attempt_id%') THEN
        RAISE EXCEPTION 'The example Family visibility is not installed as declared';
    END IF;
END
$check$;
