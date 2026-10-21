-- Frozen forward migration 0150 (the repository-wide file sequence):
-- Back to review undoes an Additional information request's Handled
-- (#1840). It is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copy of the replaced function equals the fresh-install
-- baseline's (schema/information_followup.sql).
--
-- The Administrator decided (2026-10-20) that no review pipeline stage is
-- final: Back to review on a Handled request removes only the follow-up's
-- completion, so the request is To handle again with its Accept kept
-- (review pipeline spec, "Undoing a terminal stage"). The revision guard
-- already admitted clearing a completion with confirm_clear; this re-creates
-- stewardship_information_revision_guard_v1 from its latest body (0126's,
-- which equals the baseline's) plus one rule: a revision that leaves a
-- completed follow-up not followed up needs a new note (not blank, not the
-- earlier notes a revision carries forward), whichever writer records it;
-- and a note of nothing but white space (spaces, tabs, line breaks) is
-- blank, for that rule and for reopening an Ignored request alike.
-- The guard already admits only a current Staff member or Administrator
-- (stewardship_export_authorized_v1), at the request's current version, so
-- a Ministry leader is still refused. It is not SECURITY DEFINER (the
-- baseline has no ALTER for it), and CREATE OR REPLACE keeps its owner,
-- grants and trigger. No table, row or grant changes. The file ends with a
-- DO block that refuses to commit unless the new body is installed.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_information_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE item stewardship_additional_information%ROWTYPE;
        prior stewardship_information_revision%ROWTYPE; campaign uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Staff follow-up history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO item FROM stewardship_additional_information WHERE id=NEW.item_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=item.submission_id AND mode='live';
    -- Every revision states its review decision (#1685); a CHECK ties it to
    -- the follow-up flags.
    IF item.id IS NULL OR campaign IS NULL
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM item.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.notes)>5000
       OR (item.followed_up_at IS NOT NULL AND NOT NEW.followed_up AND NOT NEW.confirm_clear)
       OR NEW.review_decision IS NULL
    THEN RAISE EXCEPTION 'Staff follow-up change lacks current authority/version'
        USING ERRCODE='23514'; END IF;
    SELECT * INTO prior FROM stewardship_information_revision
        WHERE item_id=item.id ORDER BY expected_version DESC LIMIT 1;
    -- An Ignored request (its latest revision ignored it, or, saved before
    -- migration 0126, left follow-up not needed) leaves Ignored, whether
    -- back to review or straight to follow-up, only with a new note saying
    -- why, as the review pipeline requires: the request's earlier notes,
    -- which a revision carries forward, are not one.
    IF NEW.review_decision<>'ignored'
       AND (NEW.notes !~ '\S' OR NEW.notes=prior.notes)
       AND prior.id IS NOT NULL AND NOT item.follow_up_needed
       AND item.followed_up_at IS NULL
       AND coalesce(prior.review_decision,'ignored')='ignored'
    THEN RAISE EXCEPTION 'Reopening an ignored request needs a note'
        USING ERRCODE='23514'; END IF;
    -- Leaving Handled (a completed follow-up cleared, whether by the
    -- worklist's Back to review or the command line's clear) undoes a
    -- terminal step, which needs a new note saying why, as reopening an
    -- Ignored request does (#1840).
    IF item.followed_up_at IS NOT NULL AND NOT NEW.followed_up
       AND (NEW.notes !~ '\S' OR NEW.notes=prior.notes)
    THEN RAISE EXCEPTION 'Undoing a handled request needs a note'
        USING ERRCODE='23514'; END IF;
    -- The worklist's undo also keeps the Accept (To handle: accepted,
    -- follow-up needed, not followed up; responses.information.recorded),
    -- but this guard cannot require that: pk-admin's followup
    -- information-update records the same revision, with the same
    -- confirm_clear, and may clear a completed follow-up with Follow-up
    -- needed off, back to To review, as the admin automation spec says.
    -- Nothing in a revision tells the two apart, so the service holds the
    -- worklist's destination.
    -- Only a request To review (or already Ignored) is ignored: one whose
    -- follow-up is needed or done goes back to review first.
    IF NEW.review_decision='ignored'
       AND (item.follow_up_needed OR item.followed_up_at IS NOT NULL)
    THEN RAISE EXCEPTION 'Only a request to review can be ignored'
        USING ERRCODE='23514'; END IF;
    -- Attribution is database-owned. Notes-only edits preserve who completed the
    -- work and when; clearing removes the current projection, never old history.
    NEW.created_at:=statement_timestamp();
    NEW.followed_up_at:=NULL;
    NEW.followed_up_by_id:=NULL;
    IF NEW.followed_up THEN
        IF item.followed_up_at IS NOT NULL THEN
            IF prior.followed_up_at IS DISTINCT FROM item.followed_up_at
               OR prior.followed_up_by_id IS NULL THEN
                RAISE EXCEPTION 'Follow-up attribution history is unavailable' USING ERRCODE='23514';
            END IF;
            NEW.followed_up_at:=prior.followed_up_at;
            NEW.followed_up_by_id:=prior.followed_up_by_id;
        ELSE
            NEW.followed_up_at:=NEW.created_at;
            NEW.followed_up_by_id:=NEW.actor_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_information_revision_guard_v1()'::regprocedure
              AND prosrc LIKE '%Undoing a handled request needs a note%'
              AND prosrc NOT LIKE '%btrim(NEW.notes)%'
              AND prosrc LIKE '%AND (NEW.notes !~ ''\\S'' OR NEW.notes=prior.notes)%'
              AND prosrc LIKE '%Reopening an ignored request needs a note%'
              AND prosrc LIKE '%Only a request to review can be ignored%'
              AND prosrc LIKE '%Follow-up attribution history is unavailable%'
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id)%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_information_revision'::regclass
              AND t.tgfoid='public.stewardship_information_revision_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'Migration 0150: the information revision guard does not require an undo note';
    END IF;
END
$check$;
