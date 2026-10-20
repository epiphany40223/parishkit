-- Frozen forward migration file 0213 (the repository-wide file sequence;
-- provisional, the coordinator assigns the final number at merge): Back to
-- review undoes a pledge's terminal stage (#1840, review pipeline). This file
-- is installed by its Django migration in the reports app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Undoing a terminal stage") makes no stage final: Back to review on an
-- Entered by hand (with or without the Not seen in ParishSoft flag), Entered
-- incorrectly or Confirmed pledge removes only that step, with a required
-- note, for Staff and Administrators only. The decision behind the step
-- (Accepted, or Accepted (edited) with its amount and frequency) stays in
-- force, so the pledge returns to To enter by hand; a Confirmed pledge no
-- decision was behind ("Already in ParishSoft") returns to To review.
-- Additive, as the spec's "Data integrity" requires during the campaign:
--   * stewardship_pledge_review.pledge_review_action_state (0127's CHECK),
--     replaced: a back_to_review row may now keep an accepted decision
--     (accepted or accepted_edited, never ignored), still not a mark. It
--     only admits more rows, so every existing row passes it. Its shape
--     checks (pledge_review_value_shape, pledge_review_mark_shape) are
--     unchanged: a kept Accepted (edited) carries its amount, and the undo
--     records no ParishSoft amount.
--   * stewardship_pledge_review_guard_v1 (0135's, replaced in place): Back to
--     review on To enter by hand and (with a note) on Ignored still clears
--     the decision; on Entered by hand, Entered incorrectly and Confirmed it
--     needs a note and copies the decision in force and its values exactly
--     (a Confirmed pledge whose rows give To review copies none). Every other
--     rule is 0135's, unchanged: the role check (stewardship_export_
--     authorized_v1 without a leader), the version, the writers' order.
-- No stage selection changes. The undo row is the item's new latest row
-- (its version advances), so 0134's and 0135's checks, which count only
-- for the latest row (a refresh check names its review row, an open check
-- its item version), leave the undone step's Confirmed, Entered incorrectly
-- or unchanged run in history, and the next refresh judges the pledge as an
-- open To enter by hand (or To review) one; a later mark is a new row, so
-- Not seen counts from it. 0136's rule that a pledge ever marked Entered by
-- hand is never Confirmed at the Family's submit keeps counting an undone
-- mark: Staff may have typed the amount before undoing the mark, so a later
-- answer equal to ParishSoft's amount may be the portal's own entry, and
-- the refresh judges it, as for a pledge reopened by a new answer.
-- The guard is not SECURITY DEFINER; CREATE OR REPLACE keeps that and its
-- owner. The file ends with a DO block that refuses to commit unless all of
-- this is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_pledge_review" DROP CONSTRAINT "pledge_review_action_state";
ALTER TABLE "stewardship_pledge_review" ADD CONSTRAINT "pledge_review_action_state" CHECK (((((action)::text = 'accept'::text) AND ((decision)::text = 'accepted'::text) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'edit'::text) AND ((decision)::text = 'accepted_edited'::text) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'ignore'::text) AND ((decision)::text = 'ignored'::text) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'back_to_review'::text) AND (decision IS NULL) AND (NOT entered)) OR (((action)::text = 'back_to_review'::text) AND ((decision)::text = ANY ((ARRAY['accepted'::character varying, 'accepted_edited'::character varying])::text[])) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'entered_by_hand'::text) AND ((decision)::text = ANY ((ARRAY['accepted'::character varying, 'accepted_edited'::character varying])::text[])) AND (decision IS NOT NULL) AND entered)));

-- 0135's pledge review guard, with Back to review undoing a terminal step
-- (#1840): on Entered by hand, Entered incorrectly or Confirmed it needs a
-- note and keeps the decision in force with its values, or keeps none when
-- the item's rows give To review. Every other rule is 0135's, unchanged.
CREATE OR REPLACE FUNCTION public.stewardship_pledge_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE family public.stewardship_family_campaign%ROWTYPE;
    response public.stewardship_submission%ROWTYPE;
    prior public.stewardship_pledge_review%ROWTYPE;
    item record; offered boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Pledge reviews are immutable' USING ERRCODE='23514';
    END IF;
    -- The writers' order, which the Family submit holds too: the item read
    -- below cannot change before this transaction commits.
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Pledge review requires ordered admission' USING ERRCODE='23514';
    END IF;
    SELECT * INTO family FROM public.stewardship_family_campaign WHERE id=NEW.family_id;
    SELECT * INTO response FROM public.stewardship_submission WHERE id=NEW.submission_id;
    SELECT * INTO item FROM public.stewardship_pledge_stages_v1(
        family.campaign_id,NEW.family_id);
    SELECT * INTO prior FROM public.stewardship_pledge_review WHERE id=item.review_id;
    -- Whether the item's stage offers the row's action. (Computed here: a
    -- CASE inside the IF below would end its condition at the first THEN.)
    offered:=CASE NEW.action
        WHEN 'accept' THEN item.stage IN ('to_review','conflict')
        WHEN 'edit' THEN item.stage IN ('to_review','conflict')
        WHEN 'ignore' THEN item.stage IN ('to_review','conflict')
        WHEN 'back_to_review' THEN CASE
            -- Undoing a decision clears it; reopening an ignored pledge
            -- needs a reason.
            WHEN item.stage='to_enter_by_hand' THEN NEW.decision IS NULL
            WHEN item.stage='ignored' THEN NEW.decision IS NULL
                AND btrim(NEW.note)<>''
            -- Undoing a terminal step (#1840) needs a reason and removes
            -- only the step: the decision its rows keep in force, with its
            -- values, stays (To enter by hand); with none behind it (a
            -- pledge ParishSoft already held) it keeps none (To review).
            WHEN item.stage IN ('entered_by_hand','entered_incorrectly','confirmed')
                THEN btrim(NEW.note)<>'' AND CASE
                    WHEN item.base_stage IN ('to_enter_by_hand','entered_by_hand')
                        THEN NEW.decision IS NOT NULL
                            AND ROW(NEW.decision,NEW.reviewed_annual_pledge,
                                NEW.reviewed_frequency) IS NOT DISTINCT FROM
                            ROW(prior.decision,prior.reviewed_annual_pledge,
                                prior.reviewed_frequency)
                    WHEN item.base_stage='to_review' THEN NEW.decision IS NULL
                    ELSE false END
            ELSE false END
        -- The mark carries the decision in force and its values.
        WHEN 'entered_by_hand' THEN item.stage='to_enter_by_hand'
            AND ROW(NEW.decision,NEW.reviewed_annual_pledge,NEW.reviewed_frequency)
                IS NOT DISTINCT FROM
                ROW(prior.decision,prior.reviewed_annual_pledge,prior.reviewed_frequency)
        ELSE false END;
    IF family.id IS NULL OR response.id IS NULL OR item.family_id IS NULL
       -- The Family's current live response, and the item's next version.
       OR item.submission_id IS DISTINCT FROM NEW.submission_id
       OR NEW.sequence IS DISTINCT FROM item.sequence+1
       -- An Administrator or Staff member; a Ministry leader never decides
       -- on a pledge.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,false)
       OR NOT public.stewardship_export_admitted_v1(family.campaign_id,true)
       OR length(NEW.note)>2000
       -- A mark records ParishSoft's upcoming-year amount from the current
       -- snapshot, or none (#1704); pledge_review_mark_shape keeps it to
       -- marks. A known amount is never NULL, even where the snapshot has
       -- none to compare with.
       OR (NEW.marked_pledge_known AND (NEW.marked_pledge IS NULL
           OR NEW.marked_pledge IS DISTINCT FROM
           public.stewardship_pledge_upcoming_v1(
               (SELECT c.snapshot_id FROM public.stewardship_source_current c
                WHERE c.singleton),family.campaign_id,family.family_duid)))
       OR NOT coalesce(offered,false)
    THEN RAISE EXCEPTION 'Pledge review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- The answer decided on, the item's A after the row and the attribution
    -- time are database-owned.
    NEW.answer:=public.stewardship_pledge_answer_v1(response.answers,response.annual_pledge);
    NEW.baseline_pledge:=item.next_baseline;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_pledge_review'::regclass
              AND conname='pledge_review_action_state' AND contype='c'
              AND convalidated
              -- A Back to review arm with a decision list (the only new
              -- arm; how PostgreSQL deparses the list may vary).
              AND pg_get_constraintdef(oid) LIKE
                  '%''back_to_review''::text) AND ((decision)::text = ANY (%accepted_edited%')
    THEN
        RAISE EXCEPTION 'The pledge reviews do not admit a kept decision on Back to review';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_pledge_review'::regclass
              AND t.tgname='stewardship_pledge_review_guard' AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_pledge_review_guard_v1()'::regprocedure)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc LIKE '%public.stewardship_pledge_stages_v1(%'
              AND prosrc LIKE '%classid=736220 AND objid=1 AND objsubid=2%'
              AND prosrc LIKE '%NEW.marked_pledge_known AND (NEW.marked_pledge IS NULL%'
              AND prosrc LIKE '%NEW.baseline_pledge:=item.next_baseline%'
              AND prosrc LIKE '%item.stage IN (''to_review'',''conflict'')%'
              AND prosrc LIKE '%WHEN item.stage=''to_enter_by_hand'' THEN NEW.decision IS NULL%'
              AND prosrc LIKE '%item.stage IN (''entered_by_hand'',''entered_incorrectly'',''confirmed'')%'
              AND prosrc LIKE '%WHEN item.base_stage IN (''to_enter_by_hand'',''entered_by_hand'')%'
              AND prosrc LIKE '%WHEN item.base_stage=''to_review'' THEN NEW.decision IS NULL%')
    THEN
        RAISE EXCEPTION 'stewardship_pledge_review_guard_v1 does not undo a terminal step';
    END IF;
END $check$;
