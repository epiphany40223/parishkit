-- Frozen forward migration 0215 (provisional number, assigned in merge
-- order): Back to review undoes a Limitations answer's Handled (#1840). It
-- is installed by the Django migration that names it in FROZEN_SQL and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The Administrator decided (2026-10-20) that no review pipeline stage is
-- final: Back to review on a Handled cannot-participate or cannot-attend
-- answer removes only the Handled mark, so the answer is To handle again
-- with its Accept kept (review pipeline spec, "Undoing a terminal stage").
-- It is stored as one more immutable stewardship_limitation_review row,
-- decision accepted, with a note saying why. This re-creates
-- stewardship_limitation_review_guard_v1 from its latest body (0126's, which
-- created it; no baseline copy exists) plus one allowed step: from Handled,
-- accepted with a non-blank note (before, Handled allowed nothing). The
-- guard still admits only a current Staff member or Administrator
-- (stewardship_export_authorized_v1), on the Family's effective live
-- response, at the answer's current version, so a Ministry leader and a
-- stale version are still refused. It is not SECURITY DEFINER, and CREATE
-- OR REPLACE keeps its owner, grants and trigger. No table, row or grant
-- changes. The file ends with a DO block that refuses to commit unless the
-- new body is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_limitation_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE family stewardship_family_campaign%ROWTYPE;
        response stewardship_submission%ROWTYPE;
        latest stewardship_limitation_review%ROWTYPE; state text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Limitation reviews are immutable' USING ERRCODE='23514';
    END IF;
    -- The work order first, as every writer takes it (the information
    -- revision guard does the same), then the Family's record, which
    -- orders this against its own submissions: they change its effective
    -- response.
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO family FROM stewardship_family_campaign WHERE id=NEW.family_id
        FOR NO KEY UPDATE;
    SELECT * INTO response FROM stewardship_submission WHERE id=NEW.submission_id;
    SELECT * INTO latest FROM stewardship_limitation_review
        WHERE family_id=NEW.family_id AND kind=NEW.kind AND member_key=NEW.member_key
        ORDER BY expected_version DESC LIMIT 1;
    -- The stage the decision in force gives; none in force is To review.
    state := CASE WHEN latest.id IS NULL OR latest.decision='none'
            OR NOT stewardship_limitation_in_force_v1(latest.id) THEN 'to_review'
        WHEN latest.decision='accepted' THEN 'to_handle'
        WHEN latest.decision='ignored' THEN 'ignored'
        ELSE 'handled' END;
    IF family.id IS NULL OR response.id IS NULL
       OR response.id IS DISTINCT FROM family.effective_submission_id
       OR response.mode<>'live' OR response.family_id<>family.id
       OR response.campaign_id<>family.campaign_id
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(family.campaign_id,true)
       OR NEW.expected_version IS DISTINCT FROM coalesce(latest.expected_version+1,0)
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       OR NOT stewardship_limitation_answer_v1(response.answers,NEW.kind,NEW.member_key)
       -- In parentheses, so PL/pgSQL does not read CASE's THEN as the IF's.
       OR NOT (CASE state
           WHEN 'to_review' THEN NEW.decision IN ('accepted','ignored')
           WHEN 'to_handle' THEN NEW.decision IN ('none','handled')
           WHEN 'ignored' THEN NEW.decision='none' AND btrim(NEW.note)<>''
           -- Handled's undo (#1840) keeps the Accept: To handle again.
           WHEN 'handled' THEN NEW.decision='accepted' AND btrim(NEW.note)<>''
           ELSE false END)
    THEN RAISE EXCEPTION 'Limitation review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_limitation_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%WHEN ''handled'' THEN NEW.decision=''accepted'' AND btrim(NEW.note)<>''''%'
              AND prosrc LIKE '%WHEN ''ignored'' THEN NEW.decision=''none'' AND btrim(NEW.note)<>''''%'
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id)%'
              AND prosrc LIKE '%pg_advisory_xact_lock(736220,1)%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       -- The guard still fires before each row's INSERT, UPDATE and DELETE
       -- (row, before, insert, delete and update: tgtype 1+2+4+8+16).
       OR NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgname='stewardship_limitation_review_guard'
              AND t.tgrelid='public.stewardship_limitation_review'::regclass
              AND t.tgfoid='public.stewardship_limitation_review_guard_v1()'::regprocedure
              AND t.tgtype=31 AND t.tgenabled='O')
    THEN
        RAISE EXCEPTION 'Migration 0215: the limitation review guard does not admit the undo of Handled';
    END IF;
END
$check$;
