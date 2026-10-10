-- Frozen forward migration file 0049 (the repository-wide file sequence):
-- the chosen-Family test names export is offered as CSV, XLSX and PDF
-- (#966). This file is installed by its Django migration in the reports app
-- and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- Every data export is offered as CSV, XLSX and PDF unless a recorded
-- reason says otherwise (UX conventions spec, "Downloaded files"). The
-- names export (pk-admin test families-preview --names, #817) was CSV only:
-- 0037's family_test_names arm of export_report_known requires
-- format='csv'. The worker already renders the names document in all three
-- formats (information_rendering), so only the constraint changes. This
-- file replaces export_report_known with 0037's definition (no later file
-- changed it) with that one arm widened to csv, xlsx and pdf, in the form
-- PostgreSQL deparses Django's format__in declaration, as the other
-- captured reports' arms are. ADD CONSTRAINT validates the existing rows,
-- which are all CSV. No function, grant or row changes. Reversing needs its
-- own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE public.stewardship_export_request DROP CONSTRAINT export_report_known;
ALTER TABLE public.stewardship_export_request ADD CONSTRAINT export_report_known CHECK ((((directory_snapshot_id IS NULL) AND (fact_set_id IS NOT NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'participation'::text)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NOT NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'additional_information'::text)) OR ((directory_snapshot_id IS NOT NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = ANY ((ARRAY['family_directory'::character varying, 'postal_outreach'::character varying])::text[]))) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NOT NULL) AND ((report)::text = 'ministry'::text)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NULL) AND (financial_snapshot_id IS NOT NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'financial'::text)) OR ((directory_snapshot_id IS NULL) AND (fact_set_id IS NULL) AND (family_test_names_snapshot_id IS NOT NULL) AND (financial_snapshot_id IS NULL) AND ((format)::text = ANY ((ARRAY['csv'::character varying, 'xlsx'::character varying, 'pdf'::character varying])::text[])) AND (information_snapshot_id IS NULL) AND (ministry_snapshot_id IS NULL) AND ((report)::text = 'family_test_names'::text))));

-- Refuse to commit unless the installed, validated constraint admits a
-- family_test_names request in CSV, XLSX and PDF and still refuses PNG. The
-- check evaluates the constraint's own expression against a synthetic row
-- (only the report, the format and the names capture set), so it tests that
-- one arm's result without depending on the text or number of the other
-- arms, which later migrations may change. It writes no row and makes no
-- temporary object: the migration login has no TEMP grant.
DO $check$
DECLARE
    expression text;
    fmt text;
    admitted boolean;
BEGIN
    SELECT pg_get_expr(conbin, conrelid) INTO expression FROM pg_constraint
     WHERE conrelid='public.stewardship_export_request'::regclass
       AND conname='export_report_known' AND contype='c' AND convalidated;
    IF expression IS NULL THEN
        RAISE EXCEPTION 'Migration 0049 (names export formats) is not installed as declared';
    END IF;
    FOREACH fmt IN ARRAY ARRAY['csv','xlsx','pdf','png'] LOOP
        EXECUTE format(
            'SELECT %s FROM json_populate_record(NULL::public.stewardship_export_request, $1) AS stewardship_export_request',
            expression)
          INTO admitted
          USING json_build_object(
              'report', 'family_test_names', 'format', fmt,
              'family_test_names_snapshot_id', '00000000-0000-4000-8000-000000000000');
        IF admitted IS DISTINCT FROM (fmt<>'png') THEN
            RAISE EXCEPTION 'Migration 0049 (names export formats) is not installed as declared';
        END IF;
    END LOOP;
END
$check$;
