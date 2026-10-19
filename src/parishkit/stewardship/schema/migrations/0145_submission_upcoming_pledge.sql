-- Frozen forward migration file 0145 (the repository-wide file sequence;
-- provisional, the coordinator assigns the final number at merge): the
-- upcoming-year pledge a Family answered against (#1753, review pipeline).
-- This file is installed by its Django migration in the responses app and
-- must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- The financial stream (docs/specs/stewardship/review-pipeline/spec.md,
-- "Financial stream", Pledge confirmation) read A, the baseline a pledge was
-- answered against, as $0.00 for every Family answer: a response recorded no
-- ParishSoft pledge, and the form baseline's snapshot may be compacted by
-- source retention once it is no longer current. Additive, as the spec's
-- "Data integrity" requires during the campaign:
--   * stewardship_submission.upcoming_pledge (new column, NULL for every
--     existing row): ParishSoft's pledge for the campaign's upcoming
--     financial period in the response's validation snapshot (the current
--     one at the submit), recorded by the Family submit (and Open form) only
--     for a response answering the financial questions and only when that
--     snapshot's giving read is proven complete; NULL otherwise.
--   * stewardship_submission_pledge_guard_v1 (new trigger function): a
--     recorded amount must be 0139's stewardship_pledge_upcoming_v1 for the
--     response's own validation snapshot, campaign and Family, on a response
--     answering the financial questions. It pins the amount to the
--     snapshot's sum; the giving read's proof is the application's check,
--     as for the Entered by hand mark (0139). INSERT only: 0001's submission
--     guard already refuses every UPDATE and admits only the rehearsal
--     cleanup's DELETE. It runs with the submitting login's rights, which
--     already read every input (web reads the pledge snapshot rows, the
--     snapshot cursor and the campaign configuration for the Family form).
--   * stewardship_pledge_stages_v1 (0142's, replaced in place, same
--     columns): an item's A is now the amount recorded on the response at
--     which the Family's current pledge answer began (rule 1 keeps A while
--     the answer is unchanged; rule 2 takes this submit's), unless the
--     decision in force recorded its own (a Conflict's R, 0142); NULL reads
--     as $0.00, as before. next_baseline, which the pledge review guard
--     records on a decision, carries that A. A To review pledge no decision
--     is in force for whose recorded A equals the Family's annual pledge is
--     Confirmed at the submit (rules 3 and 4: nothing to enter), reported as
--     a confirmed check at that amount, unless any earlier version of the
--     Family's pledge was ever marked Entered by hand, which stays for the
--     refresh to judge. The financial stream places rules 3 and 4 so (the
--     spec's "Financial stream"), not as the general rule does. Every 0142
--     rule is otherwise unchanged.
-- No grant changes: the column is part of the response row web already
-- inserts and reads, and no new function is a definer. The file ends with a
-- DO block that refuses to commit unless all of this is installed.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_submission" ADD COLUMN "upcoming_pledge" numeric(24, 2) NULL;

-- A recorded upcoming-year pledge is the validation snapshot's own: 0139's
-- selection, the one the refresh checks and the Entered by hand mark read.
CREATE FUNCTION public.stewardship_submission_pledge_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.upcoming_pledge IS NOT NULL AND (NOT (NEW.answers ? 'financial')
       OR NEW.upcoming_pledge IS DISTINCT FROM stewardship_pledge_upcoming_v1(
           NEW.validation_source_id,NEW.campaign_id,
           (SELECT f.family_duid FROM stewardship_family_campaign f
            WHERE f.id=NEW.family_id)))
    THEN RAISE EXCEPTION 'A response''s upcoming pledge must be its snapshot''s'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- Named to fire after 0001's stewardship_submission_guard (BEFORE triggers
-- fire in name order), which has already required the validation snapshot
-- to be the current one; keep the name sorting after it.
CREATE TRIGGER stewardship_submission_pledge_guard
    BEFORE INSERT ON public.stewardship_submission
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_submission_pledge_guard_v1();

-- 0142's stage selection with A read from the responses (#1753). ``answered``
-- is the amount recorded on the earliest live financial response from which
-- every later one up to the current carries the current pledge answer (the
-- run rule 1 keeps); ``known`` is the item's A before the $0.00 reading.
CREATE OR REPLACE FUNCTION public.stewardship_pledge_stages_v1(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL)
RETURNS TABLE(family_id uuid, family_duid bigint, submission_id uuid,
    sequence bigint, stage text, base_stage text, review_id uuid,
    in_force boolean, check_outcome text, observed numeric, baseline numeric,
    next_baseline numeric, checks bigint)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT i.family_id,i.family_duid,i.submission_id,i.sequence,
        CASE WHEN k.outcome IN ('confirmed','conflict','entered_incorrectly')
            THEN k.outcome ELSE i.stage END,
        i.stage,i.review_id,i.in_force,k.outcome,k.observed,
        coalesce(a.known,0)::numeric(24,2),
        CASE WHEN k.outcome='conflict' THEN k.observed ELSE a.known END,
        (SELECT count(*) FROM stewardship_pledge_open_check c
            WHERE c.family_id=i.family_id)
        +(SELECT count(*) FROM stewardship_pledge_refresh_check c
            JOIN stewardship_pledge_review v ON v.id=c.review_id
            WHERE v.family_id=i.family_id)
    FROM stewardship_pledge_review_states_v1(campaign_uuid,family_uuid) i
    JOIN stewardship_submission s ON s.id=i.submission_id
    LEFT JOIN stewardship_pledge_review r ON r.id=i.review_id
    LEFT JOIN LATERAL (
        SELECT x.upcoming_pledge AS answered FROM stewardship_submission x
        WHERE x.family_id=i.family_id AND x.campaign_id=s.campaign_id
          AND x.mode='live' AND x.answers ? 'financial'
          AND x.campaign_sequence<=s.campaign_sequence
          AND NOT EXISTS(SELECT 1 FROM stewardship_submission y
              WHERE y.family_id=i.family_id AND y.campaign_id=s.campaign_id
                AND y.mode='live' AND y.answers ? 'financial'
                AND y.campaign_sequence>=x.campaign_sequence
                AND y.campaign_sequence<=s.campaign_sequence
                AND stewardship_pledge_answer_v1(y.answers,y.annual_pledge)
                    IS DISTINCT FROM stewardship_pledge_answer_v1(s.answers,s.annual_pledge))
        ORDER BY x.campaign_sequence LIMIT 1) run ON true
    CROSS JOIN LATERAL (SELECT CASE WHEN i.in_force AND r.baseline_pledge IS NOT NULL
        THEN r.baseline_pledge ELSE run.answered END AS known) a
    -- Rules 3 and 4: ParishSoft already held the Family's new or changed
    -- pledge at the submit, so nothing is to be entered.
    CROSS JOIN LATERAL (SELECT i.stage='to_review' AND NOT i.in_force
        AND run.answered IS NOT NULL AND run.answered=s.annual_pledge
        AND NOT EXISTS(SELECT 1 FROM stewardship_pledge_review m
            WHERE m.family_id=i.family_id AND m.entered) AS held) z
    LEFT JOIN LATERAL (
        SELECT c.outcome::text AS outcome,c.observed FROM stewardship_pledge_refresh_check c
        WHERE i.stage<>'to_review' AND i.in_force AND c.review_id=i.review_id
        ORDER BY c.created_at DESC,c.id DESC LIMIT 1) d ON true
    -- 0142's open check read, unchanged: its inner ``s`` is the checked
    -- response and shadows the outer current one inside this subquery.
    LEFT JOIN LATERAL (
        SELECT c.outcome::text AS outcome,c.observed FROM stewardship_pledge_open_check c
        JOIN stewardship_submission s ON s.id=c.submission_id
        WHERE i.stage='to_review' AND c.family_id=i.family_id
          AND c.item_version=i.sequence
          AND NOT EXISTS(SELECT 1 FROM stewardship_submission x
              WHERE x.family_id=i.family_id AND x.campaign_id=s.campaign_id
                AND x.mode='live' AND x.campaign_sequence>=s.campaign_sequence
                AND x.answers ? 'financial'
                AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                    IS DISTINCT FROM stewardship_pledge_answer_v1(s.answers,s.annual_pledge))
        ORDER BY c.created_at DESC,c.id DESC LIMIT 1) o ON true
    CROSS JOIN LATERAL (SELECT CASE WHEN z.held THEN 'confirmed'
            ELSE coalesce(d.outcome,o.outcome) END AS outcome,
        CASE WHEN z.held THEN run.answered
            WHEN d.outcome IS NOT NULL THEN d.observed ELSE o.observed END AS observed) k
$$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid='public.stewardship_submission'::regclass
              AND attname='upcoming_pledge' AND NOT attnotnull AND NOT attisdropped
              AND atttypid='numeric'::regtype)
    THEN
        RAISE EXCEPTION 'The responses lack their upcoming pledge';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_submission'::regclass
              AND t.tgname='stewardship_submission_pledge_guard'
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              -- BEFORE each row's INSERT (tgtype ROW|BEFORE|INSERT = 7).
              AND t.tgtype=7
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname='stewardship_submission_pledge_guard_v1'
              AND p.prosrc LIKE '%NEW.upcoming_pledge IS DISTINCT FROM stewardship_pledge_upcoming_v1(%'
              AND p.prosrc LIKE '%NEW.validation_source_id,NEW.campaign_id%'
              AND p.prosrc LIKE '%NOT (NEW.answers ? ''financial'')%')
    THEN
        RAISE EXCEPTION 'The responses'' upcoming pledge is not guarded as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_stages_v1(uuid,uuid)'::regprocedure
              AND provolatile='s' AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_pledge_review_states_v1(%'
              AND prosrc LIKE '%c.item_version=i.sequence%'
              AND prosrc LIKE '%count(*) FROM stewardship_pledge_open_check%'
              AND prosrc LIKE '%count(*) FROM stewardship_pledge_refresh_check%'
              AND prosrc LIKE '%IS DISTINCT FROM stewardship_pledge_answer_v1(s.answers%'
              AND prosrc LIKE '%x.upcoming_pledge AS answered%'
              AND prosrc LIKE '%run.answered=s.annual_pledge%'
              AND prosrc LIKE '%m.family_id=i.family_id AND m.entered%')
    THEN
        RAISE EXCEPTION 'The pledge stage selection does not read the answered pledge';
    END IF;
END $check$;
