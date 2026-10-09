-- Frozen forward migration file 0021 (the repository-wide file sequence): a
-- directory export's snapshot keeps only the private contact columns its
-- file renders (#388 L6). This file is installed by its Django migration in
-- the reports app and must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of
-- stewardship_directory_export_capture_v1 still equals the fresh-install
-- baseline's (schema/directory_reports.sql). A later change gets its own
-- numbered file. A fresh install runs the baseline, 0002 to 0020 and then
-- this; the baseline already carries the same body, so the install ends in
-- the same catalog as an upgraded database.
--
-- The capture trigger stores the whole directory selection for every
-- matching Family in the immutable snapshot: phones, primary address,
-- envelope number and heads. The files use less (reports.directory_documents):
--   postal mail merge   address and heads; no phones, no envelope
--   Family-code list    heads; phones only when reach is 'neither'; no
--                       address, no envelope
-- The trigger now empties the unused keys before the row is stored
-- (envelope null, address {}, phones []), keeping the document's shape for
-- the worker's code and head-email steps. Existing snapshots are not
-- rewritten: they are immutable and expire with their exports. No table,
-- column, grant or other function changes. Reversing needs its own forward
-- migration that restores the old body.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The capture, exactly as the fresh-install directory_reports.sql defines
-- it. CREATE OR REPLACE resets every attribute it does not state; this
-- states the baseline's own (invoker rights, the fixed search_path). The
-- trigger and its grants are unchanged.
CREATE OR REPLACE FUNCTION public.stewardship_directory_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE postal boolean; phones boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Directory export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Directory export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_directory_report_v1(NEW.campaign_id,NEW.parameters);
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Directory export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    -- Keep only the private contact columns the requested file renders
    -- (#388 L6; reports.directory_documents): the postal mail merge uses the
    -- address, and the code list uses phones only for reach 'neither'. The
    -- envelope number is in neither file. The keys stay, emptied, so the
    -- document keeps its shape; heads (names and head emails) are in both.
    postal:=coalesce((NEW.parameters->>'postal')::boolean,false);
    phones:=NOT postal AND NEW.parameters->'filters'->>'reach' IS NOT DISTINCT FROM 'neither';
    NEW.document:=jsonb_set(NEW.document,'{rows}',coalesce((SELECT jsonb_agg(
        e.r||jsonb_build_object('envelope',NULL)
           ||CASE WHEN postal THEN '{}'::jsonb ELSE jsonb_build_object('address','{}'::jsonb) END
           ||CASE WHEN phones THEN '{}'::jsonb ELSE jsonb_build_object('phones','[]'::jsonb) END
        ORDER BY e.ordinal)
        FROM jsonb_array_elements(NEW.document->'rows') WITH ORDINALITY e(r,ordinal)),
        '[]'::jsonb));
    RETURN NEW;
END $$;

-- Refuse to commit unless the new body is installed with its attributes
-- (invoker rights, the fixed search_path) and the trigger still calls it. No temporary objects: the migration login has
-- no TEMP grant.
DO $check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_directory_export_capture_v1()'::regprocedure
             AND NOT prosecdef
             AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%jsonb_build_object(''envelope'',NULL)%'
             AND prosrc LIKE '%jsonb_build_object(''address'',''{}''::jsonb)%'
             AND prosrc LIKE '%jsonb_build_object(''phones'',''[]''::jsonb)%'
             AND prosrc LIKE '%''reach'' IS NOT DISTINCT FROM ''neither''%')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger
           WHERE tgrelid='public.stewardship_directory_export_snapshot'::regclass
             AND tgname='directory_export_capture' AND NOT tgisinternal
             AND tgfoid='public.stewardship_directory_export_capture_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'The slimmer directory export capture was not installed';
    END IF;
END
$check$;
