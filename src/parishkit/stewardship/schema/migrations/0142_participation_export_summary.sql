-- Frozen forward migration file 0142 (the repository-wide file sequence,
-- number assigned centrally): the Response dashboard's export carries all
-- the data on the page (#1557). This file is installed by its Django
-- migration in the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. Everything it makes is created here
-- (the baseline has none of it), so a fresh install and an upgraded
-- database end in the same catalog.
--
-- The page's Summary (the Active population statistics and the Families by
-- stage funnel counts) is read in Python, so the web captures that same
-- read when the export is requested, in the request's own transaction, and
-- stores it in an immutable stewardship_participation_export_snapshot; the
-- worker renders the file from it, so a later response or ParishSoft update
-- never changes the file and a retry writes the same figures. The document
-- holds aggregate counts and totals only, never a Family's name or
-- identifier. Unlike the other export captures, this one names its request
-- (the participation request is made first, as before), so the request
-- table does not change. It:
--   - creates the capture table, as Django renders ParticipationExportSnapshot,
--     with its capture guard: insert only (or the discard's one clearing),
--     from the web, by the request's requester (an Administrator or Staff
--     member), for an admitted campaign, for a participation request made
--     in this transaction that has not been handed off or attempted, with
--     the document's closed shape;
--   - adds a deferred check on stewardship_export_request: a participation
--     request commits only with its capture, except a latest-data export's
--     handoff (stewardship_exact_export_resolution), which has none;
--   - replaces stewardship_export_discard_v1 (0109's, migration-owned) with
--     only one change: an ended export's capture of this kind is cleared
--     too. It stays SECURITY DEFINER, as its CREATE OR REPLACE names.
-- The two new functions are not SECURITY DEFINER. Grants follow from
-- reports/export_grants.py through the upgrade's database-grants step. No
-- existing row changes: requests made before this file have no capture and
-- keep their daily rows only. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The capture table, as Django renders the model.
CREATE TABLE "stewardship_participation_export_snapshot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NOT NULL, "correlation_id" uuid NOT NULL, "document" jsonb NOT NULL, "campaign_id" uuid NOT NULL, "request_id" uuid NOT NULL UNIQUE);
ALTER TABLE "stewardship_participation_export_snapshot" ADD CONSTRAINT "stewardship_particip_campaign_id_731a5905_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_participation_export_snapshot" ADD CONSTRAINT "stewardship_particip_request_id_49608bb5_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_export_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "participation_export_correl" ON "stewardship_participation_export_snapshot" ("correlation_id");
CREATE INDEX "participation_export_campaign" ON "stewardship_participation_export_snapshot" ("campaign_id");

-- The web's capture of one new participation request's Summary. The request
-- must be this requester's participation request for the same campaign,
-- made in this transaction (its created_at, a statement time, is at or
-- after this transaction's start), still queued, never attempted and not a
-- latest-data handoff, so a capture can never be added to an export later.
-- The document's shape is closed (these keys and no other), as defense in
-- depth: a caller cannot add a field the files were not designed to hold.
-- observed_at is the statistics' instant; each population row is a label,
-- its value (a whole count, an exact amount {"money": "1234.50"} or null
-- when unknown) and its note; each stage row a label, its count and its
-- note. The trigger sets the time. Rows never change, except that an ended
-- export's discard, run as the schema owner, may clear the document.
CREATE FUNCTION public.stewardship_participation_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE request stewardship_export_request%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' AND stewardship_export_capture_cleared_v1(
           TG_RELID,to_jsonb(OLD),to_jsonb(NEW)) THEN
        RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Participation export captures are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    SELECT * INTO request FROM stewardship_export_request WHERE id=NEW.request_id;
    IF current_user='pk_stewardship_worker'
       OR request.id IS NULL OR request.report<>'participation'
       OR request.campaign_id<>NEW.campaign_id OR request.requester_id<>NEW.actor_id
       OR request.created_at<transaction_timestamp()
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR NOT EXISTS(SELECT 1 FROM stewardship_task_run t
           WHERE t.id=request.task_id AND t.state='queued')
       OR EXISTS(SELECT 1 FROM stewardship_export_attempt a WHERE a.request_id=request.id)
       OR EXISTS(SELECT 1 FROM stewardship_exact_export_resolution x
           WHERE x.export_id=request.id)
    THEN RAISE EXCEPTION 'Participation export capture is unavailable' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(NEW.document) IS DISTINCT FROM 'object'
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.document) k)
           IS DISTINCT FROM ARRAY['observed_at','population','stages']
       OR jsonb_typeof(NEW.document->'observed_at') IS DISTINCT FROM 'string'
       OR NEW.document->>'observed_at'
           !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
       OR jsonb_typeof(NEW.document->'population') IS DISTINCT FROM 'array'
       OR jsonb_typeof(NEW.document->'stages') IS DISTINCT FROM 'array'
       -- Two tiles, or four with giving; one tile per funnel stage.
       OR jsonb_array_length(NEW.document->'population') NOT IN (2,4)
       OR jsonb_array_length(NEW.document->'stages') NOT BETWEEN 1 AND 8
       OR octet_length(NEW.document::text)>16384
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'population') p
           WHERE jsonb_typeof(p) IS DISTINCT FROM 'object'
              OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k)
                  IS DISTINCT FROM ARRAY['label','note','value']
              OR jsonb_typeof(p->'label')<>'string' OR p->>'label'=''
              OR jsonb_typeof(p->'note')<>'string'
              OR NOT (p->'value'='null'::jsonb
                  OR (jsonb_typeof(p->'value')='number' AND p->>'value' ~ '^[0-9]{1,12}$')
                  OR (jsonb_typeof(p->'value')='object'
                      AND (SELECT array_agg(k) FROM jsonb_object_keys(p->'value') k)=ARRAY['money']
                      AND jsonb_typeof(p->'value'->'money')='string'
                      AND p->'value'->>'money' ~ '^-?[0-9]{1,20}\.[0-9]{2}$')))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'stages') s
           WHERE jsonb_typeof(s) IS DISTINCT FROM 'object'
              OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(s) k)
                  IS DISTINCT FROM ARRAY['count','label','note']
              OR jsonb_typeof(s->'label')<>'string' OR s->>'label'=''
              OR jsonb_typeof(s->'note')<>'string'
              OR jsonb_typeof(s->'count')<>'number' OR s->>'count' !~ '^[0-9]{1,12}$')
    THEN RAISE EXCEPTION 'Invalid participation export summary' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER participation_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_participation_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_participation_export_capture_v1();

-- A participation request commits only with its capture, by its requester
-- for its campaign, checked when the transaction commits. A latest-data
-- export's handoff (made by the worker, with its resolution) has none.
CREATE FUNCTION public.stewardship_participation_export_summary_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.report='participation'
       AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_resolution x
           WHERE x.export_id=NEW.id)
       AND NOT EXISTS(SELECT 1 FROM stewardship_participation_export_snapshot s
           WHERE s.request_id=NEW.id AND s.campaign_id=NEW.campaign_id
             AND s.actor_id=NEW.requester_id)
    THEN RAISE EXCEPTION 'Participation export requires its Summary capture' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER participation_export_summary AFTER INSERT
    ON stewardship_export_request DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_participation_export_summary_v1();

-- 0109's discard, copied whole, plus the participation capture: it names its
-- request, so it is cleared by request rather than through a request column.
CREATE OR REPLACE FUNCTION public.stewardship_export_discard_v1(request_uuid uuid, by_deploy boolean)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE r stewardship_export_request%ROWTYPE; cleared integer:=0; released integer;
        n integer; target regclass; snapshot uuid; column_name text;
BEGIN
    SELECT * INTO r FROM stewardship_export_request WHERE id=request_uuid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'No such export' USING ERRCODE='23514';
    END IF;
    -- A retry that wins these locks adds a newer run first, so the export
    -- is then not ended; one that waits sees the discard.
    PERFORM 1 FROM stewardship_task_run WHERE root_id=r.task_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM stewardship_export_discard WHERE request_id=request_uuid) THEN
        RETURN 0;
    END IF;
    IF NOT coalesce(stewardship_export_ended_v1(request_uuid),false) THEN
        RAISE EXCEPTION 'This export has not ended; its saved data stays' USING ERRCODE='23514';
    END IF;
    FOR target, snapshot, column_name IN VALUES
        ('stewardship_information_export_snapshot'::regclass, r.information_snapshot_id, 'information_snapshot_id'::text),
        ('stewardship_directory_export_snapshot'::regclass, r.directory_snapshot_id, 'directory_snapshot_id'),
        ('stewardship_ministry_export_snapshot'::regclass, r.ministry_snapshot_id, 'ministry_snapshot_id'),
        ('stewardship_financial_export_snapshot'::regclass, r.financial_snapshot_id, 'financial_snapshot_id'),
        ('stewardship_family_test_names_export_snapshot'::regclass, r.family_test_names_snapshot_id, 'family_test_names_snapshot_id'),
        ('stewardship_timeline_export_snapshot'::regclass, r.timeline_snapshot_id, 'timeline_snapshot_id'),
        ('stewardship_comparison_export_snapshot'::regclass, r.comparison_snapshot_id, 'comparison_snapshot_id')
    LOOP
        CONTINUE WHEN snapshot IS NULL;
        -- The capture trigger admits this one change, by the owner
        -- (stewardship_export_capture_cleared_v1). A capture another live
        -- request still uses stays whole.
        EXECUTE format('UPDATE %s s SET document=''{}''::jsonb WHERE s.id=$1 '
            'AND s.document<>''{}''::jsonb AND NOT EXISTS(SELECT 1 FROM '
            'stewardship_export_request o WHERE o.%I=s.id AND o.id<>$2 '
            'AND NOT coalesce(stewardship_export_ended_v1(o.id),false))',
            target, column_name) USING snapshot, request_uuid;
        GET DIAGNOSTICS n = ROW_COUNT;
        cleared:=cleared+n;
    END LOOP;
    -- The Response dashboard's Summary (migration 0142) belongs to its one
    -- request alone, so it is cleared with it.
    UPDATE stewardship_participation_export_snapshot s SET document='{}'::jsonb
        WHERE s.request_id=request_uuid AND s.document<>'{}'::jsonb;
    GET DIAGNOSTICS n = ROW_COUNT;
    cleared:=cleared+n;
    DELETE FROM stewardship_fact_pin WHERE parent_kind='export' AND parent_id=request_uuid;
    GET DIAGNOSTICS released = ROW_COUNT;
    INSERT INTO stewardship_export_discard(id,correlation_id,documents,pins,by_deploy,request_id)
        VALUES (gen_random_uuid(),r.correlation_id,cleared,released,by_deploy,request_uuid);
    -- Last: the history trigger admits this only once the discard is
    -- recorded. The generation is then free for compaction.
    UPDATE stewardship_export_request SET fact_set_id=NULL
        WHERE id=request_uuid AND fact_set_id IS NOT NULL;
    RETURN cleared+released;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_export_discard_v1(uuid,boolean) FROM PUBLIC;

-- Refuse to commit unless every part above is installed: the table with its
-- keys, indexes and guard, the request table's deferred check, and the
-- discard with its participation clearing, still SECURITY DEFINER and not
-- executable by PUBLIC, while the two new functions are not definers. No
-- temporary objects: the migration login has no TEMP grant.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_participation_export_snapshot') IS NULL
       OR to_regclass('public.participation_export_correl') IS NULL
       OR to_regclass('public.participation_export_campaign') IS NULL
       OR (SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.stewardship_participation_export_snapshot'::regclass
             AND contype='f' AND condeferrable AND condeferred
             AND confrelid IN ('public.stewardship_campaign'::regclass,
                 'public.stewardship_export_request'::regclass))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
           WHERE conrelid='public.stewardship_participation_export_snapshot'::regclass
             AND contype='u'
             AND conkey=ARRAY[(SELECT attnum FROM pg_attribute
                 WHERE attrelid='public.stewardship_participation_export_snapshot'::regclass
                   AND attname='request_id')])
    THEN
        RAISE EXCEPTION 'Migration 0142: the participation capture table is incomplete';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_participation_export_snapshot'::regclass
          AND t.tgname='participation_export_capture' AND t.tgenabled='O'
          AND NOT t.tgisinternal AND t.tgtype=31
          AND p.proname='stewardship_participation_export_capture_v1')
       OR NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.stewardship_export_request'::regclass
          AND t.tgname='participation_export_summary' AND t.tgenabled='O'
          AND t.tgconstraint<>0 AND t.tgdeferrable AND t.tginitdeferred
          AND p.proname='stewardship_participation_export_summary_v1')
    THEN
        RAISE EXCEPTION 'Migration 0142: the participation capture guards are not installed';
    END IF;
    IF (SELECT count(*) FROM pg_proc
        WHERE pronamespace='public'::regnamespace AND NOT prosecdef
          AND proname IN ('stewardship_participation_export_capture_v1',
                          'stewardship_participation_export_summary_v1'))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_proc
        WHERE pronamespace='public'::regnamespace
          AND proname='stewardship_export_discard_v1' AND prosecdef
          AND position('UPDATE stewardship_participation_export_snapshot s' IN prosrc)>0
          AND position('stewardship_comparison_export_snapshot' IN prosrc)>0
          AND NOT has_function_privilege('public',oid,'EXECUTE'))
    THEN
        RAISE EXCEPTION 'Migration 0142: the discard does not clear the participation capture';
    END IF;
END
$check$;
