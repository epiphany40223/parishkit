-- Frozen forward migration file 0261 (provisional: the repository-wide file
-- sequence assigns the final number when the pull request merges): the
-- Administrator-only read of the report sends waiting on an earlier message
-- (#1872, report-emails spec "Catch-up, recovery and holds"). This file is
-- installed by its Django migration in the reports app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- It holds only the one definer read, created without checking its body,
-- so it installs on its own: the users swap's base must lack it (it is
-- granted to web, runtime_grants.CLOSURE_FUNCTIONS), and the swap installs
-- it again whatever else is pending. It reads frozen files 0156's tables
-- and 0260's comparison when it runs. The file ends with a DO block that
-- refuses to commit unless the read is installed as declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Every report send waiting in fanout on an earlier message, for Sent mail,
-- the Mail message page and pk-admin delivery (#1872): one row per send and
-- per earlier message it waits on, with the send's due time (NULL for a
-- send with no occurrence). Only cohort addresses that have no recipient row
-- yet and are still recipients count, as the fanout handles them: one that
-- failed the recheck is revoked, never compared. It returns ids and times,
-- never an address. As 0116's recipient reads do, it refuses any caller but
-- the web login (or the schema owner, for maintenance and tests) and any
-- actor but an enabled Administrator (SQLSTATE 42501); PUBLIC has no
-- EXECUTE and database-grants grants it only to web.
CREATE FUNCTION public.stewardship_report_email_waiting_v1(actor_uuid uuid)
RETURNS TABLE(preparation_id uuid, due_at timestamptz, message_id uuid)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Waiting report sends are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Waiting report sends require an Administrator' USING ERRCODE='42501';
    END IF;
    RETURN QUERY
        SELECT DISTINCT p.id, o.due_at, waiting.value::uuid
          FROM public.stewardship_report_email_preparation p
          JOIN public.stewardship_report_email_snapshot s ON s.preparation_id=p.id
          LEFT JOIN public.stewardship_schedule_occurrence o ON o.id=p.occurrence_id
         CROSS JOIN LATERAL jsonb_array_elements(s.recipients) member
         CROSS JOIN LATERAL (SELECT public.stewardship_report_email_prior_v1(
               s.id,member->>'address') AS answer) comparison
         CROSS JOIN LATERAL jsonb_array_elements_text(
               CASE WHEN comparison.answer->>'state'='uncertain'
                    THEN comparison.answer->'waiting' ELSE '[]'::jsonb END) waiting(value)
         WHERE p.phase='fanout'
           AND NOT EXISTS(SELECT 1 FROM public.stewardship_report_email_recipient r
               WHERE r.snapshot_id=s.id AND r.address=member->>'address')
           AND public.stewardship_report_email_current_v1(p.definition_id,
               member->>'kind',member->>'ref',member->>'address')
         ORDER BY o.due_at, p.id, waiting.value::uuid;
END $$;
ALTER FUNCTION public.stewardship_report_email_waiting_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_email_waiting_v1(uuid) FROM PUBLIC;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc p
        WHERE p.oid=to_regprocedure('public.stewardship_report_email_waiting_v1(uuid)')
          AND p.prosecdef AND p.provolatile='s'
          AND strpos(p.prosrc,'Waiting report sends require an Administrator')>0
          AND strpos(p.prosrc,'stewardship_report_email_prior_v1(')>0
          AND strpos(p.prosrc,'stewardship_report_email_current_v1(')>0
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR has_function_privilege('public','public.stewardship_report_email_waiting_v1(uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0261: the waiting read is not installed as declared';
    END IF;
END
$check$;
