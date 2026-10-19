-- Frozen forward migration file 0142 (the repository-wide file sequence;
-- provisional, the coordinator assigns the final number at merge): the
-- refresh re-evaluation of To review and conflicting pledges (#1747, review
-- pipeline). This file is installed by its Django migration in the reports
-- app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- 0139 (#1704) re-evaluates only decided pledges, and records nothing for a
-- pledge ParishSoft holds at a third amount (a Conflict the stream could not
-- show). The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Financial stream", Pledge confirmation) now re-evaluates To review pledges
-- too and shows a Conflict. Additive, as the spec's "Data integrity" requires
-- during the campaign:
--   * stewardship_pledge_open_check (new, migration-owned): one immutable row
--     per refresh and To review pledge, which has no decision row to name, so
--     it names the Family's current live response and the item's version
--     (its latest decision row's sequence, 0 with none), with the outcome
--     (confirmed, a conflict, or unchanged), ParishSoft's amount and whether
--     the refresh was full. It counts while the item is at that version and
--     no later live response changes the pledge answer.
--   * stewardship_pledge_refresh_check (0139's): its outcomes gain conflict,
--     for an accepted pledge not yet marked.
--   * stewardship_pledge_review.baseline_pledge (new column, NULL for every
--     existing row): the item's A after the row, set by the guard. A decision
--     taken on a Conflict records the Conflict's amount ("R becomes the
--     item's new A"); Back to review and the mark carry it while the Family's
--     pledge is unchanged. NULL reads as $0.00.
--   * stewardship_pledge_stages_v1 (new): 0135's selection with each item's
--     latest counting check of either kind, so the page, the counts, the
--     pledge review guard and the worker's selection share Confirmed,
--     Conflict and Entered incorrectly, with each item's A and the A a
--     decision taken now records.
--   * stewardship_pledge_refresh_items_v2 (new, SECURITY DEFINER, granted to
--     the source worker alone): every pledge a refresh re-evaluates (To
--     review, and the decided ones in force, never a Confirmed one) with C,
--     A, the amount recorded at the mark and the latest check. The worker
--     still reads no response answers or decision rows itself.
--   * 0139's pledge refresh check guard and 0135's pledge review guard,
--     replaced in place: the check guard reads v2 and admits a Conflict
--     (R differing from A and C) on an accepted pledge not yet marked, and
--     an unchanged check there only at A; the review guard places the item
--     through stages_v1, so it refuses every decision on a Confirmed pledge
--     (and, as before, on Entered incorrectly until #1737), offers Accept,
--     Edit and Ignore on a Conflict, and sets baseline_pledge.
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_pledge_open_check" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "snapshot_id" uuid NOT NULL, "full_refresh" boolean NOT NULL, "item_version" bigint NOT NULL CHECK ("item_version" >= 0), "outcome" varchar(24) NOT NULL, "observed" numeric(24, 2) NOT NULL, "family_id" uuid NOT NULL, "submission_id" uuid NOT NULL, CONSTRAINT "pledge_open_check_once" UNIQUE ("snapshot_id", "family_id"), CONSTRAINT "pledge_open_check_outcome" CHECK (((outcome)::text = ANY ((ARRAY['confirmed'::character varying, 'unchanged'::character varying, 'conflict'::character varying])::text[]))), CONSTRAINT "pledge_open_check_identity" CHECK ((actor_id IS NULL)));
ALTER TABLE "stewardship_pledge_open_check" ADD CONSTRAINT "stewardship_pledge_o_family_id_613ac592_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_pledge_open_check" ADD CONSTRAINT "stewardship_pledge_o_submission_id_517833a3_fk_stewardsh" FOREIGN KEY ("submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_pledge_open_check_correlation_id_f04fc274" ON "stewardship_pledge_open_check" ("correlation_id");
CREATE INDEX "stewardship_pledge_open_check_family_id_613ac592" ON "stewardship_pledge_open_check" ("family_id");
CREATE INDEX "stewardship_pledge_open_check_submission_id_517833a3" ON "stewardship_pledge_open_check" ("submission_id");

ALTER TABLE "stewardship_pledge_refresh_check" DROP CONSTRAINT "pledge_refresh_check_outcome";
ALTER TABLE "stewardship_pledge_refresh_check" ADD CONSTRAINT "pledge_refresh_check_outcome" CHECK (((outcome)::text = ANY ((ARRAY['confirmed'::character varying, 'unchanged'::character varying, 'entered_incorrectly'::character varying, 'ignored_now_matches'::character varying, 'conflict'::character varying])::text[])));

ALTER TABLE "stewardship_pledge_review" ADD COLUMN "baseline_pledge" numeric(24, 2) NULL;

-- Each Family's pledge item with its final stage: 0135's selection (the
-- stage its decision rows give, here base_stage) moved on by the item's
-- latest check that counts. A decided item's check counts while its decision
-- row is the latest and in force (0139); a To review item's while the item is
-- at the check's version and no live response since the checked one changes
-- the pledge answer. ``baseline`` is the item's A: the in-force row's
-- baseline_pledge, else $0.00. ``next_baseline`` is what a decision recorded
-- now carries forward: a Conflict's amount, else the in-force row's own.
CREATE FUNCTION public.stewardship_pledge_stages_v1(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL)
RETURNS TABLE(family_id uuid, family_duid bigint, submission_id uuid,
    sequence bigint, stage text, base_stage text, review_id uuid,
    in_force boolean, check_outcome text, observed numeric, baseline numeric,
    next_baseline numeric)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT i.family_id,i.family_duid,i.submission_id,i.sequence,
        CASE WHEN k.outcome IN ('confirmed','conflict','entered_incorrectly')
            THEN k.outcome ELSE i.stage END,
        i.stage,i.review_id,i.in_force,k.outcome,k.observed,
        coalesce(CASE WHEN i.in_force THEN r.baseline_pledge END,0)::numeric(24,2),
        CASE WHEN k.outcome='conflict' THEN k.observed
            WHEN i.in_force THEN r.baseline_pledge END
    FROM stewardship_pledge_review_states_v1(campaign_uuid,family_uuid) i
    LEFT JOIN stewardship_pledge_review r ON r.id=i.review_id
    LEFT JOIN LATERAL (
        SELECT c.outcome::text AS outcome,c.observed FROM stewardship_pledge_refresh_check c
        WHERE i.stage<>'to_review' AND i.in_force AND c.review_id=i.review_id
        ORDER BY c.created_at DESC,c.id DESC LIMIT 1) d ON true
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
    CROSS JOIN LATERAL (SELECT coalesce(d.outcome,o.outcome) AS outcome,
        CASE WHEN d.outcome IS NOT NULL THEN d.observed ELSE o.observed END AS observed) k
$$;

-- Every pledge a refresh re-evaluates: a To review one and a decided one in
-- force, never a Confirmed one (it is not checked again), each with C (a To
-- review pledge's current answer, else the Edit's annual pledge, else the
-- answer decided on), the amount recorded at its mark ($0.00 without one), A
-- and its latest counting check. ``family_uuid`` or ``review_uuid`` limits it
-- to that item, whose campaign it finds; ``review_uuid`` only to a decided
-- item whose row it is.
CREATE FUNCTION public.stewardship_pledge_refresh_items_v2(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL, review_uuid uuid DEFAULT NULL)
RETURNS TABLE(campaign_id uuid, family_id uuid, family_duid bigint,
    submission_id uuid, review_id uuid, sequence bigint, stage text,
    base_stage text, decision text, entered boolean, carried numeric,
    marked numeric, baseline numeric, check_outcome text, observed numeric)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH chosen AS (
        SELECT f.campaign_id AS campaign,f.id AS family
        FROM stewardship_family_campaign f
        WHERE (family_uuid IS NOT NULL OR review_uuid IS NOT NULL)
          AND f.id=coalesce(family_uuid,(SELECT r.family_id
              FROM stewardship_pledge_review r WHERE r.id=review_uuid))
        UNION ALL
        SELECT campaign_uuid,NULL::uuid WHERE family_uuid IS NULL AND review_uuid IS NULL
    )
    SELECT c.campaign,i.family_id,i.family_duid,i.submission_id,i.review_id,
        i.sequence,i.stage,i.base_stage,
        CASE WHEN i.base_stage<>'to_review' THEN r.decision::text END,
        i.base_stage='entered_by_hand',
        CASE WHEN i.base_stage='to_review'
                THEN (stewardship_pledge_answer_v1(s.answers,s.annual_pledge)
                    ->>'annual_pledge')::numeric
            WHEN r.decision='accepted_edited' THEN r.reviewed_annual_pledge
            ELSE (r.answer->>'annual_pledge')::numeric END,
        CASE WHEN i.base_stage='entered_by_hand' AND r.marked_pledge_known
                AND r.marked_pledge IS NOT NULL
            THEN r.marked_pledge ELSE 0 END,
        i.baseline,i.check_outcome,i.observed
    FROM chosen c
    CROSS JOIN LATERAL stewardship_pledge_stages_v1(c.campaign,c.family) i
    JOIN stewardship_submission s ON s.id=i.submission_id
    LEFT JOIN stewardship_pledge_review r ON r.id=i.review_id
    WHERE c.campaign IS NOT NULL
      AND (campaign_uuid IS NULL OR c.campaign=campaign_uuid)
      AND i.stage<>'confirmed'
      AND (review_uuid IS NULL OR (i.base_stage<>'to_review' AND i.review_id=review_uuid))
$$;
ALTER FUNCTION public.stewardship_pledge_refresh_items_v2(uuid, uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_pledge_refresh_items_v2(uuid, uuid, uuid) FROM PUBLIC;

-- 0139's check guard of a decided pledge, reading v2 (the same items, now
-- with A): a Conflict on an accepted pledge not yet marked, and an unchanged
-- check there only at A (#1747). Every other rule is 0139's, unchanged.
CREATE OR REPLACE FUNCTION public.stewardship_pledge_refresh_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    item record; phase text; refreshed numeric; matches boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Pledge refresh checks are immutable' USING ERRCODE='23514';
    END IF;
    -- Inside the promotion of the current snapshot, as 0136's checks are:
    -- the lease's phase says whether the refresh is full. Only the source
    -- worker's login holds INSERT.
    SELECT lease.phase INTO phase FROM stewardship_source_current current_source
        JOIN stewardship_source_snapshot source ON source.id=current_source.snapshot_id
        JOIN stewardship_source_lease lease ON lease.owner_id=source.task_id
            AND lease.fence=source.source_fence
        WHERE current_source.singleton AND source.id=NEW.snapshot_id;
    IF NEW.actor_id IS NOT NULL
       OR NOT stewardship_response_source_owner_v1(NEW.snapshot_id)
       OR NEW.full_refresh IS DISTINCT FROM (phase='full')
    THEN RAISE EXCEPTION 'Pledge refresh checks require the promoting source worker'
        USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM stewardship_pledge_refresh_check k
        WHERE k.review_id=NEW.review_id AND k.outcome='confirmed')
    THEN RAISE EXCEPTION 'A confirmed pledge is not checked again' USING ERRCODE='23514'; END IF;
    -- A decided pledge whose decision row is the item's latest and in force,
    -- not Confirmed (the definer selection above), at that row's sequence.
    SELECT * INTO item FROM stewardship_pledge_refresh_items_v2(NULL,NULL,NEW.review_id);
    IF item.review_id IS NULL OR NEW.item_version IS DISTINCT FROM item.sequence
    THEN RAISE EXCEPTION 'A pledge refresh check requires a decided pledge in force'
        USING ERRCODE='23514'; END IF;
    -- The snapshot's own amount, compared with the carried-forward one, A
    -- and the one recorded at the mark ($0.00 when it recorded none).
    refreshed:=stewardship_pledge_upcoming_v1(NEW.snapshot_id,item.campaign_id,
        item.family_duid);
    IF refreshed IS NULL OR NEW.observed IS DISTINCT FROM refreshed THEN
        RAISE EXCEPTION 'A pledge refresh check requires the snapshot''s pledge'
            USING ERRCODE='23514';
    END IF;
    matches:=refreshed=item.carried;
    IF (item.decision='ignored'
            AND (NEW.outcome NOT IN ('unchanged','ignored_now_matches')
                 OR matches IS DISTINCT FROM (NEW.outcome='ignored_now_matches')))
       OR (item.decision<>'ignored' AND item.entered
            AND (NEW.outcome NOT IN ('confirmed','unchanged','entered_incorrectly')
                 OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
                 OR (NEW.outcome='entered_incorrectly' AND refreshed=item.marked)))
       -- Not yet marked: Confirmed at C, unchanged at A, else a Conflict.
       OR (item.decision<>'ignored' AND NOT item.entered
            AND (NEW.outcome NOT IN ('confirmed','unchanged','conflict')
                 OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
                 OR (NOT matches AND (refreshed=item.baseline)
                     IS DISTINCT FROM (NEW.outcome='unchanged'))))
    THEN RAISE EXCEPTION 'A pledge refresh check differs from its evidence'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

-- A To review pledge's check: the same promotion rules as a decided one's,
-- for the item's current response at its version, with the snapshot's own
-- amount; Confirmed at C, unchanged at A, else a Conflict.
CREATE FUNCTION public.stewardship_pledge_open_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    item record; phase text; refreshed numeric; matches boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Pledge open checks are immutable' USING ERRCODE='23514';
    END IF;
    SELECT lease.phase INTO phase FROM stewardship_source_current current_source
        JOIN stewardship_source_snapshot source ON source.id=current_source.snapshot_id
        JOIN stewardship_source_lease lease ON lease.owner_id=source.task_id
            AND lease.fence=source.source_fence
        WHERE current_source.singleton AND source.id=NEW.snapshot_id;
    IF NEW.actor_id IS NOT NULL
       OR NOT stewardship_response_source_owner_v1(NEW.snapshot_id)
       OR NEW.full_refresh IS DISTINCT FROM (phase='full')
    THEN RAISE EXCEPTION 'Pledge open checks require the promoting source worker'
        USING ERRCODE='23514'; END IF;
    -- A To review pledge, not Confirmed, at the Family's current response
    -- and the item's version (the definer selection above).
    SELECT * INTO item FROM stewardship_pledge_refresh_items_v2(NULL,NEW.family_id,NULL);
    IF item.family_id IS NULL OR item.base_stage IS DISTINCT FROM 'to_review'
       OR NEW.submission_id IS DISTINCT FROM item.submission_id
       OR NEW.item_version IS DISTINCT FROM item.sequence
    THEN RAISE EXCEPTION 'A pledge open check requires a To review pledge'
        USING ERRCODE='23514'; END IF;
    refreshed:=stewardship_pledge_upcoming_v1(NEW.snapshot_id,item.campaign_id,
        item.family_duid);
    IF refreshed IS NULL OR NEW.observed IS DISTINCT FROM refreshed THEN
        RAISE EXCEPTION 'A pledge open check requires the snapshot''s pledge'
            USING ERRCODE='23514';
    END IF;
    matches:=refreshed=item.carried;
    IF matches IS NULL OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
       OR (NOT matches AND (refreshed=item.baseline)
           IS DISTINCT FROM (NEW.outcome='unchanged'))
    THEN RAISE EXCEPTION 'A pledge open check differs from its evidence'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_pledge_open_check_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_pledge_open_check
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_pledge_open_check_guard_v1();

-- 0139's pledge review guard, placing the item through stages_v1 (#1747):
-- no decision on a Confirmed pledge, Accept, Edit and Ignore on a Conflict,
-- and the row's A (baseline_pledge) set here. Entered incorrectly still
-- offers nothing until #1737. Every other rule is 0139's, unchanged.
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
        -- Reopening an ignored pledge needs a reason.
        WHEN 'back_to_review' THEN item.stage='to_enter_by_hand'
            OR (item.stage='ignored' AND btrim(NEW.note)<>'')
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
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_pledge_open_check'::regclass
              AND t.tgname='stewardship_pledge_open_check_guard'
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              -- BEFORE each row's INSERT, UPDATE and DELETE (31).
              AND t.tgtype=31
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname='stewardship_pledge_open_check_guard_v1'
              AND p.prosrc LIKE '%TG_OP<>''INSERT''%'
              AND p.prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
              AND p.prosrc LIKE '%stewardship_pledge_refresh_items_v2(NULL,NEW.family_id,NULL)%'
              AND p.prosrc LIKE '%stewardship_pledge_upcoming_v1(NEW.snapshot_id%')
    THEN
        RAISE EXCEPTION 'The pledge open checks are not guarded as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_pledge_open_check'::regclass
              AND conname IN ('pledge_open_check_once','pledge_open_check_outcome',
                              'pledge_open_check_identity'))<>3
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_pledge_refresh_check'::regclass
              AND conname='pledge_refresh_check_outcome'
              AND pg_get_constraintdef(oid) LIKE '%''conflict''%')
       OR NOT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid='public.stewardship_pledge_review'::regclass
              AND attname='baseline_pledge' AND NOT attnotnull AND NOT attisdropped)
    THEN
        RAISE EXCEPTION 'The pledge checks and reviews lack their columns or checks';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_stages_v1(uuid,uuid)'::regprocedure
              AND provolatile='s' AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_pledge_review_states_v1(%'
              AND prosrc LIKE '%stewardship_pledge_open_check%'
              AND prosrc LIKE '%c.item_version=i.sequence%')
    THEN
        RAISE EXCEPTION 'The pledge stage selection is not installed as declared';
    END IF;
    -- The worker's one read of pledges runs as its owner, and no other
    -- login may call it.
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_refresh_items_v2(uuid,uuid,uuid)'::regprocedure
              AND provolatile='s' AND prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_pledge_stages_v1(%'
              AND prosrc LIKE '%i.stage<>''confirmed''%')
       OR has_function_privilege('public',
            'public.stewardship_pledge_refresh_items_v2(uuid,uuid,uuid)','EXECUTE')
    THEN
        RAISE EXCEPTION 'The pledge refresh selection is not installed as declared';
    END IF;
    -- The replaced guards: the check guard reads v2 and knows a Conflict;
    -- the review guard places the item through stages_v1 and sets A.
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_pledge_refresh_check'::regclass
              AND t.tgname='stewardship_pledge_refresh_check_guard' AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_pledge_refresh_check_guard_v1()'::regprocedure)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_refresh_check_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_pledge_refresh_items_v2(NULL,NULL,NEW.review_id)%'
              AND prosrc LIKE '%''confirmed'',''unchanged'',''conflict''%'
              AND prosrc LIKE '%k.outcome=''confirmed''%')
    THEN
        RAISE EXCEPTION 'stewardship_pledge_refresh_check_guard_v1 does not know a Conflict';
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
              AND prosrc LIKE '%item.stage IN (''to_review'',''conflict'')%')
    THEN
        RAISE EXCEPTION 'stewardship_pledge_review_guard_v1 does not place pledges by their checks';
    END IF;
END $check$;
