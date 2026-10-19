-- Frozen forward migration file 0136 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): the
-- baseline refresh re-evaluation's refresh checks (#1689).
-- This file is installed by its Django migration in the workflows app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Refresh checks") re-evaluates every open item at each promoted refresh.
-- A census change Entered by hand or Updated in ParishSoft is final in its
-- guard, and a Ministry request a person decided changes only by review, so
-- neither row can carry what a refresh found. Additive only, as the spec's
-- "Data integrity" requires during the campaign; no existing row or
-- function changes:
--   * stewardship_refresh_check (new, migration-owned): one immutable row
--     per refresh and item, with the outcome (confirmed, unchanged, entered
--     incorrectly, or ParishSoft now matching an ignored request), the
--     census value ParishSoft held and whether the refresh was full. Its
--     guard admits an INSERT only inside the promotion of the current
--     snapshot (the owner check refresh reconciliation uses; only the source
--     worker's login is granted INSERT). The item must be a live census
--     change Entered by hand or Updated in ParishSoft, or a live Ministry
--     request a person resolved or closed, at its current version, with no
--     confirmed check yet. The evidence must be the snapshot's own: the
--     census value is the snapshot's (stewardship_response_field_source_v1)
--     and matches the carried-forward value exactly when the outcome says
--     confirmed; the Member's roster state for the request's Ministry
--     (stewardship_response_ministry_current_v1) matches the request exactly
--     when the outcome says so. It stamps the time. Rows are retained with
--     their items: no path deletes one.
--   * stewardship_refresh_check_start (new, migration-owned): one row,
--     written here once (with a count-only refresh_checks_started audit
--     event when the database already holds live responses), recording
--     when Not seen in ParishSoft starts counting and after how many full
--     refreshes (default two). Its guard keeps the
--     start immutable and stamps it on any later insert; deleting the row
--     turns the flag off. No runtime login may write it.
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_refresh_check" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "snapshot_id" uuid NOT NULL, "full_refresh" boolean NOT NULL, "proposal_id" uuid NULL, "request_id" uuid NULL, "item_version" bigint NOT NULL CHECK ("item_version" >= 0), "outcome" varchar(24) NOT NULL, "observed" jsonb NULL, CONSTRAINT "refresh_check_proposal_once" UNIQUE ("snapshot_id", "proposal_id"), CONSTRAINT "refresh_check_request_once" UNIQUE ("snapshot_id", "request_id"), CONSTRAINT "refresh_check_outcome" CHECK (((outcome)::text = ANY ((ARRAY['confirmed'::character varying, 'unchanged'::character varying, 'entered_incorrectly'::character varying, 'ignored_now_matches'::character varying])::text[]))), CONSTRAINT "refresh_check_one_item" CHECK ((((proposal_id IS NOT NULL) AND (request_id IS NULL)) OR ((proposal_id IS NULL) AND (request_id IS NOT NULL)))), CONSTRAINT "refresh_check_identity" CHECK (((actor_id IS NULL) AND (item_version >= 1))));
ALTER TABLE "stewardship_refresh_check" ADD CONSTRAINT "stewardship_refresh__proposal_id_181a0566_fk_stewardsh" FOREIGN KEY ("proposal_id") REFERENCES "stewardship_proposed_change" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_refresh_check" ADD CONSTRAINT "stewardship_refresh__request_id_9fa6ea49_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_ministry_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_refresh_check_correlation_id_5bebb578" ON "stewardship_refresh_check" ("correlation_id");
CREATE INDEX "stewardship_refresh_check_proposal_id_181a0566" ON "stewardship_refresh_check" ("proposal_id");
CREATE INDEX "stewardship_refresh_check_request_id_9fa6ea49" ON "stewardship_refresh_check" ("request_id");
CREATE TABLE "stewardship_refresh_check_start" ("singleton" boolean NOT NULL PRIMARY KEY, "started_at" timestamp with time zone NOT NULL, "not_seen_after" smallint NOT NULL CHECK ("not_seen_after" >= 0), CONSTRAINT "refresh_check_start_one" CHECK ((((not_seen_after >= 1) AND (not_seen_after <= 10)) AND singleton)));

CREATE FUNCTION public.stewardship_refresh_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    change stewardship_proposed_change%ROWTYPE;
    request stewardship_ministry_request%ROWTYPE;
    family uuid; phase text; carried jsonb; matches boolean; roster boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Refresh checks are immutable' USING ERRCODE='23514';
    END IF;
    -- Inside the promotion of the current snapshot: the owner check refresh
    -- reconciliation uses (live lease, ordered admission), and the lease's
    -- phase says whether the refresh is full. Only the source worker's
    -- login holds INSERT.
    SELECT lease.phase INTO phase FROM stewardship_source_current current_source
        JOIN stewardship_source_snapshot source ON source.id=current_source.snapshot_id
        JOIN stewardship_source_lease lease ON lease.owner_id=source.task_id
            AND lease.fence=source.source_fence
        WHERE current_source.singleton AND source.id=NEW.snapshot_id;
    IF NEW.actor_id IS NOT NULL
       OR NOT stewardship_response_source_owner_v1(NEW.snapshot_id)
       OR NEW.full_refresh IS DISTINCT FROM (phase='full')
    THEN RAISE EXCEPTION 'Refresh checks require the promoting source worker'
        USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM stewardship_refresh_check k
        WHERE k.outcome='confirmed' AND (k.proposal_id=NEW.proposal_id OR k.request_id=NEW.request_id))
    THEN RAISE EXCEPTION 'A confirmed item is not checked again' USING ERRCODE='23514'; END IF;
    IF NEW.proposal_id IS NOT NULL THEN
        -- A live census change Entered by hand or Updated in ParishSoft, at
        -- its current version, with the snapshot's own value for it.
        SELECT p.* INTO change FROM stewardship_proposed_change p
            WHERE p.id=NEW.proposal_id FOR SHARE;
        SELECT s.family_id INTO family FROM stewardship_submission s
            WHERE s.id=change.submission_id AND s.mode='live';
        carried := CASE WHEN change.admin_value_set THEN change.admin_value
            ELSE change.submitted_value END;
        IF change.id IS NULL OR family IS NULL
           OR change.execution NOT IN ('published','resolved_external')
           OR change.entity_kind<>'member' OR change.version<>NEW.item_version
           OR NEW.observed IS NULL OR NOT coalesce((NEW.observed->'available')='true'::jsonb,false)
           OR NEW.observed IS DISTINCT FROM stewardship_response_field_source_v1(
               NEW.snapshot_id,family,change.entity_key,change.field)
        THEN RAISE EXCEPTION 'A census refresh check requires a final change and its source value'
            USING ERRCODE='23514'; END IF;
        matches := stewardship_response_comparison_v1(change.field,NEW.observed->'value')
            IS NOT DISTINCT FROM stewardship_response_comparison_v1(change.field,carried);
        IF NEW.outcome='ignored_now_matches' OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
           OR (NEW.outcome='entered_incorrectly' AND change.execution<>'resolved_external')
        THEN RAISE EXCEPTION 'A census refresh check differs from its evidence'
            USING ERRCODE='23514'; END IF;
    ELSE
        -- A live Ministry request a person resolved or closed, at its
        -- current version, judged by the Member's roster state for the
        -- Ministry carried forward (the Edit's, else the Family's), or the
        -- Family's for an ignored request.
        SELECT r.* INTO request FROM stewardship_ministry_request r
            WHERE r.id=NEW.request_id FOR SHARE;
        SELECT s.family_id INTO family FROM stewardship_submission s
            WHERE s.id=request.submission_id AND s.mode='live';
        IF request.id IS NULL OR family IS NULL OR request.entity_kind<>'member'
           OR request.version<>NEW.item_version OR NEW.observed IS NOT NULL
           OR request.resolution_source_id IS NOT NULL
           OR request.state NOT IN ('resolved','closed_no_response')
           OR NEW.outcome='entered_incorrectly'
           OR (NEW.outcome='confirmed' AND (request.state<>'resolved'
               OR request.outcome NOT IN ('joined','leave_confirmed')))
           OR (NEW.outcome='ignored_now_matches' AND request.state='resolved'
               AND request.outcome IN ('joined','leave_confirmed'))
        THEN RAISE EXCEPTION 'A Ministry refresh check requires a decided request'
            USING ERRCODE='23514'; END IF;
        roster := stewardship_response_ministry_current_v1(NEW.snapshot_id,family,
            request.entity_key,CASE WHEN request.outcome IN ('joined','leave_confirmed')
                THEN coalesce(request.reviewed_ministry_duid,request.ministry_duid)
                ELSE request.ministry_duid END);
        IF roster IS NULL
           OR (roster=(request.action='join')) IS DISTINCT FROM
              (NEW.outcome IN ('confirmed','ignored_now_matches'))
        THEN RAISE EXCEPTION 'A Ministry refresh check differs from its roster evidence'
            USING ERRCODE='23514'; END IF;
    END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_refresh_check_start_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'The refresh check start is immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.started_at:=statement_timestamp();
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_refresh_check_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_refresh_check
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_check_guard_v1();
CREATE TRIGGER stewardship_refresh_check_start_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_refresh_check_start
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_check_start_guard_v1();

DO $start$
DECLARE
    event uuid := gen_random_uuid();
    correlation uuid := gen_random_uuid();
BEGIN
    INSERT INTO public.stewardship_refresh_check_start (singleton, started_at, not_seen_after)
    VALUES (true, statement_timestamp(), 2);
    -- Audited where it matters: an upgrade of a database that already holds
    -- Family responses. A fresh install has none, and no event to explain.
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_submission WHERE mode='live') THEN
        RETURN;
    END IF;
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id)
    VALUES (event, NULL, correlation, 'refresh_checks_started', NULL);
    INSERT INTO public.stewardship_audit_context
        (id, event_id, actor_id, correlation_id, actor_kind, schema, context)
    VALUES (gen_random_uuid(), event, NULL, correlation, 'system', 'action',
        jsonb_build_object('outcome', 'changed', 'count', 1));
END
$start$;

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid IN ('public.stewardship_refresh_check'::regclass,
                                'public.stewardship_refresh_check_start'::regclass)
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              -- The guard fires BEFORE each row's INSERT, UPDATE and DELETE
              -- (tgtype ROW|BEFORE|INSERT|DELETE|UPDATE = 31).
              AND t.tgtype=31
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname IN ('stewardship_refresh_check_guard_v1',
                                'stewardship_refresh_check_start_guard_v1'))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND prosrc LIKE '%TG_OP<>''INSERT''%'
              AND prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
              AND prosrc LIKE '%stewardship_response_field_source_v1(%'
              AND prosrc LIKE '%stewardship_response_ministry_current_v1(%'
              AND prosrc LIKE '%k.outcome=''confirmed''%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_start_guard_v1()'::regprocedure
              AND prosrc LIKE '%NEW.started_at IS DISTINCT FROM OLD.started_at%')
    THEN
        RAISE EXCEPTION 'The refresh check tables are not guarded as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_refresh_check'::regclass
              AND conname IN ('refresh_check_proposal_once','refresh_check_request_once',
                              'refresh_check_outcome','refresh_check_one_item',
                              'refresh_check_identity'))<>5
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_refresh_check_start'::regclass
              AND conname='refresh_check_start_one')
    THEN
        RAISE EXCEPTION 'The refresh check tables lack their checks';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_refresh_check_start
            WHERE singleton AND not_seen_after=2)
       OR (EXISTS(SELECT 1 FROM public.stewardship_submission WHERE mode='live')
           AND NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event
            WHERE event_type='refresh_checks_started'))
    THEN
        RAISE EXCEPTION 'The refresh check start is not recorded';
    END IF;
END $check$;
