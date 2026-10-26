-- Frozen forward migration file 0178: the review wizard's runs (#2123,
-- review pipeline spec "Wizard run storage").
-- This file is installed by its Django migration in the reports app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- It creates stewardship_review_run (reports/review_run_models.py): one row
-- per review wizard run, a reader's pass through one stream one subject at
-- a time. A reader and campaign may have many (one per browser tab), so the
-- reader index is not unique. A run is deleted with its portal user or its
-- campaign by the model's cascade (the foreign keys are Django's own, which
-- the model contract compares with the installed schema); nothing else
-- deletes a row but the reader's own Discard. The row holds record ids and
-- DUIDs only.
--
-- Its guard keeps a started run's sequence fixed, as the spec requires (an
-- item acted on stays in place, so Previous returns to what was just seen):
-- an insert needs a sequence of 1 to 5,000 subjects and a position inside
-- it; an update may change only the position (still inside the sequence),
-- the finished time (once) and updated_at, and a finished run changes no
-- more. The guard has invoker rights: it reads only the row itself.
-- It writes nothing to any existing table. The file ends with a DO block
-- that refuses to commit unless all of it is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_review_run" ("id" uuid NOT NULL PRIMARY KEY, "user_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "stream" varchar(16) NOT NULL, "filters" jsonb NOT NULL, "sort" varchar(8) NOT NULL, "subjects" jsonb NOT NULL, "position" integer NOT NULL CHECK ("position" >= 0), "finished_at" timestamp with time zone NULL, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, CONSTRAINT "review_run_stream" CHECK ("stream"::text = ANY(ARRAY['census'::varchar::text,'ministry'::varchar::text,'pledges'::varchar::text,'information'::varchar::text,'limitations'::varchar::text])), CONSTRAINT "review_run_sort" CHECK ("sort"::text = ANY(ARRAY['name'::varchar::text,'oldest'::varchar::text,'newest'::varchar::text])), CONSTRAINT "review_run_position" CHECK ("position" >= 1));
ALTER TABLE "stewardship_review_run" ADD CONSTRAINT "stewardship_review_r_user_id_ff237dbf_fk_stewardsh" FOREIGN KEY ("user_id") REFERENCES "stewardship_portal_user" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_review_run" ADD CONSTRAINT "stewardship_review_r_campaign_id_f864be24_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_review_run_user_id_ff237dbf" ON "stewardship_review_run" ("user_id");
CREATE INDEX "stewardship_review_run_campaign_id_f864be24" ON "stewardship_review_run" ("campaign_id");
CREATE INDEX "review_run_reader" ON "stewardship_review_run" ("user_id", "campaign_id");

-- The run guard (see the header). Each refusal is a check violation, which
-- the wizard's views answer as a stale or malformed request.
CREATE FUNCTION public.stewardship_review_run_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF jsonb_typeof(NEW.subjects) IS DISTINCT FROM 'array'
       OR jsonb_array_length(NEW.subjects) NOT BETWEEN 1 AND 5000
       OR jsonb_typeof(NEW.filters) IS DISTINCT FROM 'object'
       OR NEW.position > jsonb_array_length(NEW.subjects)
    THEN RAISE EXCEPTION 'Review run sequence is out of bounds'
        USING ERRCODE='23514'; END IF;
    IF TG_OP='UPDATE' AND (OLD.finished_at IS NOT NULL
       OR NEW.id<>OLD.id OR NEW.user_id<>OLD.user_id
       OR NEW.campaign_id<>OLD.campaign_id OR NEW.stream<>OLD.stream
       OR NEW.filters<>OLD.filters OR NEW.sort<>OLD.sort
       OR NEW.subjects<>OLD.subjects OR NEW.created_at<>OLD.created_at)
    THEN RAISE EXCEPTION 'A started review run keeps its sequence'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_review_run_guard_v1() FROM PUBLIC;
CREATE TRIGGER review_run_guard BEFORE INSERT OR UPDATE
    ON public.stewardship_review_run
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_review_run_guard_v1();

DO $check$
BEGIN
    IF to_regclass('public.stewardship_review_run') IS NULL
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_review_run'::regclass
             AND conname IN ('review_run_stream','review_run_sort','review_run_position')
             AND contype='c')<>3
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_review_run'::regclass
             AND contype='f')<>2
       OR to_regclass('public.review_run_reader') IS NULL THEN
        RAISE EXCEPTION 'Migration 0178: the review run table is not installed';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_review_run_guard_v1()')
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
          AND strpos(p.prosrc,'Review run sequence is out of bounds')>0
          AND strpos(p.prosrc,'A started review run keeps its sequence')>0
          AND strpos(p.prosrc,'OLD.finished_at IS NOT NULL')>0
          AND strpos(p.prosrc,'NEW.subjects<>OLD.subjects')>0)
       OR has_function_privilege('public',
          'public.stewardship_review_run_guard_v1()','EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0178: the review run guard is not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_review_run'::regclass
          AND t.tgname='review_run_guard' AND NOT t.tgisinternal
          AND t.tgenabled='O'
          -- A row trigger, before insert and update (1+2+4+16).
          AND t.tgtype=23
          AND t.tgfoid=to_regprocedure('public.stewardship_review_run_guard_v1()')) THEN
        RAISE EXCEPTION 'Migration 0178: the review run guard trigger is missing';
    END IF;
END
$check$;
