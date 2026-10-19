-- Frozen forward migration file 0139 (the repository-wide file sequence;
-- provisional, the coordinator assigns the final number at merge): the
-- refresh re-evaluation of hand-entered pledges (#1704, review pipeline).
-- This file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Financial stream", Pledge confirmation) re-evaluates each decided pledge
-- at every promoted refresh against ParishSoft's pledge for the upcoming
-- stewardship year, which the snapshot already holds. A pledge's stage is
-- derived from its decision rows (0135), so a refresh records what it found
-- in a row of its own, as 0136 does for census changes and Ministry
-- requests. Additive, as the spec's "Data integrity" requires during the
-- campaign:
--   * stewardship_pledge_upcoming_v1 (new): one Family's ParishSoft pledge
--     for its campaign's upcoming financial period in one snapshot, the sum
--     of its pledges on that period's funds dated in it (0.00 for none),
--     or NULL when the campaign has no financial period or the snapshot no
--     giving read. The application also requires the giving read's proof
--     (responses.financial_inputs.giving_observation) before it writes a
--     check or a mark.
--   * stewardship_pledge_refresh_items_v1 (new, SECURITY DEFINER, granted to
--     the source worker alone): the decided pledges in force of a campaign
--     (or the one a decision row names), through 0135's shared selection,
--     with each one's decision, mark, carried-forward amount and the amount
--     recorded at its mark ($0.00 when it recorded none). The worker reads
--     no response answers or decision rows itself.
--   * stewardship_pledge_refresh_check (new, migration-owned): one immutable
--     row per refresh and pledge, naming the decision row in force, with the
--     outcome (confirmed, unchanged, entered incorrectly, or ParishSoft now
--     matching an ignored pledge), ParishSoft's amount and whether the
--     refresh was full. Its guard admits an INSERT only inside the promotion
--     of the current snapshot (the owner check 0136 uses; only the source
--     worker's login is granted INSERT), for a decided pledge whose decision
--     row is the latest and in force, with no confirmed check yet, at that
--     row's sequence, with the snapshot's own amount and an outcome that
--     agrees with it. It stamps the time. Rows are retained with their
--     items: no path deletes one.
--   * stewardship_pledge_review_guard_v1 (0135's, replaced in place): an
--     Entered by hand mark may now record ParishSoft's upcoming-year amount
--     (marked_pledge_known), which must be the current snapshot's own;
--     every other rule is 0135's, unchanged.
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_pledge_refresh_check" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "snapshot_id" uuid NOT NULL, "full_refresh" boolean NOT NULL, "review_id" uuid NOT NULL, "item_version" bigint NOT NULL CHECK ("item_version" >= 0), "outcome" varchar(24) NOT NULL, "observed" numeric(24, 2) NOT NULL, CONSTRAINT "pledge_refresh_check_once" UNIQUE ("snapshot_id", "review_id"), CONSTRAINT "pledge_refresh_check_outcome" CHECK (((outcome)::text = ANY ((ARRAY['confirmed'::character varying, 'unchanged'::character varying, 'entered_incorrectly'::character varying, 'ignored_now_matches'::character varying])::text[]))), CONSTRAINT "pledge_refresh_check_identity" CHECK (((actor_id IS NULL) AND (item_version >= 1))));
ALTER TABLE "stewardship_pledge_refresh_check" ADD CONSTRAINT "stewardship_pledge_r_review_id_6031e824_fk_stewardsh" FOREIGN KEY ("review_id") REFERENCES "stewardship_pledge_review" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_pledge_refresh_check_correlation_id_f181ad88" ON "stewardship_pledge_refresh_check" ("correlation_id");
CREATE INDEX "stewardship_pledge_refresh_check_review_id_6031e824" ON "stewardship_pledge_refresh_check" ("review_id");

-- One Family's ParishSoft pledge for its campaign's upcoming financial
-- period in one snapshot: money as exact numeric, never a float. The period
-- and its funds are the campaign's active configuration's, as the Family
-- form and the Financial stewardship report read them.
CREATE FUNCTION public.stewardship_pledge_upcoming_v1(
    snapshot uuid, campaign uuid, family_duid bigint)
RETURNS numeric LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT CASE WHEN jsonb_typeof(c.values->'financial')='object'
            AND c.values->'modules' ? 'financial'
            AND pg_input_is_valid(s.cursor->'load'->>'giving_as_of_date','date')
        THEN coalesce((
            SELECT sum((p.canonical::jsonb->>'amount')::numeric)
            FROM stewardship_snapshot_pledge m
            JOIN stewardship_source_pledge p ON p.id=m.payload_id
            WHERE m.snapshot_id=s.id AND p.family_key=family_duid::text
              AND c.values->'financial'->'fund_duids' @> to_jsonb(p.fund_key::bigint)
              AND (p.canonical::jsonb->>'effective_date')::date BETWEEN
                  (c.values->'financial'->>'start')::date
                  AND (c.values->'financial'->>'end')::date),0)::numeric(24,2)
        END
    FROM stewardship_campaign k
    JOIN stewardship_campaign_configuration c ON c.id=k.active_configuration_id
    JOIN stewardship_source_snapshot s ON s.id=snapshot
    WHERE k.id=campaign
$$;

-- The decided pledges a refresh re-evaluates: a decision row that is the
-- item's latest and in force, at To enter by hand, Entered by hand or
-- Ignored (0135's selection), each with C, the Edit's annual pledge else
-- the Family's answer decided on, and the amount recorded at its mark, or
-- $0.00 (A) when it recorded none. ``review_uuid`` limits it to the item
-- that row belongs to, whose campaign it finds when ``campaign_uuid`` is
-- NULL.
CREATE FUNCTION public.stewardship_pledge_refresh_items_v1(
    campaign_uuid uuid, review_uuid uuid DEFAULT NULL)
RETURNS TABLE(campaign_id uuid, family_id uuid, family_duid bigint,
    submission_id uuid, review_id uuid, sequence bigint, decision text,
    entered boolean, carried numeric, marked numeric)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH target AS (
        SELECT f.campaign_id,f.id FROM stewardship_pledge_review r
        JOIN stewardship_family_campaign f ON f.id=r.family_id
        WHERE r.id=review_uuid
    ), chosen AS (
        SELECT CASE WHEN review_uuid IS NULL THEN campaign_uuid
                ELSE (SELECT t.campaign_id FROM target t) END AS campaign,
            (SELECT t.id FROM target t) AS family
    )
    SELECT c.campaign,i.family_id,i.family_duid,i.submission_id,r.id,r.sequence,
        r.decision::text,r.entered,
        CASE WHEN r.decision='accepted_edited' THEN r.reviewed_annual_pledge
            ELSE (r.answer->>'annual_pledge')::numeric END,
        CASE WHEN r.marked_pledge_known AND r.marked_pledge IS NOT NULL
            THEN r.marked_pledge ELSE 0 END
    FROM chosen c
    CROSS JOIN LATERAL stewardship_pledge_review_states_v1(c.campaign,c.family) i
    JOIN stewardship_pledge_review r ON r.id=i.review_id
    WHERE c.campaign IS NOT NULL AND (review_uuid IS NULL OR c.family IS NOT NULL)
      AND (campaign_uuid IS NULL OR c.campaign=campaign_uuid)
      AND i.in_force AND i.stage IN ('to_enter_by_hand','entered_by_hand','ignored')
      AND (review_uuid IS NULL OR r.id=review_uuid)
$$;
ALTER FUNCTION public.stewardship_pledge_refresh_items_v1(uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_pledge_refresh_items_v1(uuid, uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_pledge_refresh_check_guard_v1() RETURNS trigger
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
    -- A decided pledge whose decision row is the item's latest and in force
    -- (the definer selection above, over 0135's), at that row's sequence.
    SELECT * INTO item FROM stewardship_pledge_refresh_items_v1(NULL,NEW.review_id);
    IF item.review_id IS NULL OR NEW.item_version IS DISTINCT FROM item.sequence
    THEN RAISE EXCEPTION 'A pledge refresh check requires a decided pledge in force'
        USING ERRCODE='23514'; END IF;
    -- The snapshot's own amount, compared with the carried-forward one and
    -- the one recorded at the mark ($0.00, A, when it recorded none).
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
       OR (item.decision<>'ignored'
            AND (NEW.outcome NOT IN ('confirmed','unchanged','entered_incorrectly')
                 OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
                 OR (NEW.outcome='entered_incorrectly'
                     AND (NOT item.entered OR refreshed=item.marked))))
    THEN RAISE EXCEPTION 'A pledge refresh check differs from its evidence'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_pledge_refresh_check_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_pledge_refresh_check
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_pledge_refresh_check_guard_v1();

-- 0135's pledge review guard, unchanged but for the mark: Entered by hand
-- may record ParishSoft's upcoming-year amount, which must be the current
-- snapshot's own (#1704); any other row still records none.
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
    SELECT * INTO item FROM public.stewardship_pledge_review_states_v1(
        family.campaign_id,NEW.family_id);
    SELECT * INTO prior FROM public.stewardship_pledge_review WHERE id=item.review_id;
    -- Whether the item's stage offers the row's action. (Computed here: a
    -- CASE inside the IF below would end its condition at the first THEN.)
    offered:=CASE NEW.action
        WHEN 'accept' THEN item.stage='to_review'
        WHEN 'edit' THEN item.stage='to_review'
        WHEN 'ignore' THEN item.stage='to_review'
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
    -- The answer decided on and the attribution time are database-owned.
    NEW.answer:=public.stewardship_pledge_answer_v1(response.answers,response.annual_pledge);
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_pledge_refresh_check'::regclass
              AND t.tgname='stewardship_pledge_refresh_check_guard'
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              -- The guard fires BEFORE each row's INSERT, UPDATE and DELETE
              -- (tgtype ROW|BEFORE|INSERT|DELETE|UPDATE = 31).
              AND t.tgtype=31
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname='stewardship_pledge_refresh_check_guard_v1'
              AND p.prosrc LIKE '%TG_OP<>''INSERT''%'
              AND p.prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
              AND p.prosrc LIKE '%stewardship_pledge_refresh_items_v1(NULL,NEW.review_id)%'
              AND p.prosrc LIKE '%stewardship_pledge_upcoming_v1(NEW.snapshot_id%'
              AND p.prosrc LIKE '%k.outcome=''confirmed''%')
    THEN
        RAISE EXCEPTION 'The pledge refresh checks are not guarded as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_pledge_refresh_check'::regclass
              AND conname IN ('pledge_refresh_check_once','pledge_refresh_check_outcome',
                              'pledge_refresh_check_identity'))<>3
    THEN
        RAISE EXCEPTION 'The pledge refresh checks lack their checks';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_upcoming_v1(uuid,uuid,bigint)'::regprocedure
              AND provolatile='s' AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%fund_duids%' AND prosrc LIKE '%giving_as_of_date%')
    THEN
        RAISE EXCEPTION 'The upcoming pledge selection is not installed as declared';
    END IF;
    -- The worker's one read of decided pledges runs as its owner, and no
    -- other login may call it.
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_refresh_items_v1(uuid,uuid)'::regprocedure
              AND provolatile='s' AND prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_pledge_review_states_v1(%'
              AND prosrc LIKE '%i.in_force%')
       OR has_function_privilege('public',
            'public.stewardship_pledge_refresh_items_v1(uuid,uuid)','EXECUTE')
    THEN
        RAISE EXCEPTION 'The decided pledge selection is not installed as declared';
    END IF;
    -- The replaced pledge review guard: 0135's rules, and the mark's amount
    -- checked against the current snapshot.
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_pledge_review'::regclass
              AND t.tgname='stewardship_pledge_review_guard' AND t.tgenabled='O'
              AND t.tgfoid='public.stewardship_pledge_review_guard_v1()'::regprocedure)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc LIKE '%stewardship_pledge_review_states_v1(%'
              AND prosrc LIKE '%classid=736220 AND objid=1 AND objsubid=2%'
              AND prosrc LIKE '%NEW.marked_pledge_known AND (NEW.marked_pledge IS NULL%'
              AND prosrc LIKE '%stewardship_pledge_upcoming_v1(%')
    THEN
        RAISE EXCEPTION 'stewardship_pledge_review_guard_v1 does not admit the mark''s pledge';
    END IF;
END $check$;
