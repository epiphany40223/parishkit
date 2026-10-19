-- Frozen forward migration file 0135 (the repository-wide file sequence;
-- provisional, the coordinator assigns the final number at merge): the
-- financial stream's review history (#1686, review pipeline). This file is
-- installed by its Django migration in the reports app and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest.
--
-- Pledges to enter reviews each Family's current pledge answer: Accept, Edit
-- (the annual pledge and frequency), Ignore, Back to review and Entered by
-- hand. stewardship_pledge_review (new, migration-owned) is one immutable row
-- per decision: who, when, the note, the live response it was made on, that
-- response's pledge answer (stewardship_pledge_answer_v1, set here, never by
-- the caller), the decision in force afterwards with an Edit's values, and
-- the Entered by hand mark. No stage is stored: the item's stage is derived
-- from its latest row by stewardship_pledge_review_states_v1, which the page,
-- the counts and this guard share. A Family answering again with a different
-- pledge answer reopens the item (To review); an unchanged answer keeps its
-- decision at any stage (review pipeline spec, "Resubmission while the
-- setting is off").
--
-- The guard admits INSERT only, in the writers' order (the work-order lock
-- the Family submit holds too, so a decision and a resubmission never
-- interleave), from a current Administrator or Staff member, in a campaign
-- that admits changes, for the Family's current live response, at the
-- item's next sequence, for a decision its stage offers: Accept, Edit and
-- Ignore on To review; Back to review on Ignored (with a note) or To enter
-- by hand; Entered by hand on To enter by hand, keeping the decision and
-- its values. A mark never records a ParishSoft pledge yet: nothing reads
-- the upcoming year before #1704. A deferred constraint trigger then
-- requires the row's pledge_review_updated audit event. The file ends with
-- a DO block that refuses to commit unless all of this is installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_pledge_review" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "family_id" uuid NOT NULL, "submission_id" uuid NOT NULL, "sequence" bigint NOT NULL CHECK ("sequence" >= 0), "request_key" uuid NOT NULL, "action" varchar(16) NOT NULL, "decision" varchar(16) NULL, "reviewed_annual_pledge" numeric(14, 2) NULL, "reviewed_frequency" varchar(12) NULL, "entered" boolean NOT NULL, "answer" jsonb NOT NULL, "marked_pledge" numeric(14, 2) NULL, "marked_pledge_known" boolean NOT NULL, "note" text NOT NULL, CONSTRAINT "pledge_review_sequence" UNIQUE ("family_id", "sequence"), CONSTRAINT "pledge_review_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "pledge_review_identity" CHECK (((actor_id IS NOT NULL) AND (sequence >= 1))), CONSTRAINT "pledge_review_action" CHECK (((action)::text = ANY ((ARRAY['accept'::character varying, 'edit'::character varying, 'ignore'::character varying, 'back_to_review'::character varying, 'entered_by_hand'::character varying])::text[]))), CONSTRAINT "pledge_review_decision" CHECK (((decision IS NULL) OR ((decision)::text = ANY ((ARRAY['accepted'::character varying, 'accepted_edited'::character varying, 'ignored'::character varying])::text[])))), CONSTRAINT "pledge_review_value_shape" CHECK (((((decision)::text = 'accepted_edited'::text) AND (reviewed_annual_pledge >= (0)::numeric) AND (reviewed_annual_pledge IS NOT NULL) AND (reviewed_annual_pledge <= 999999999.99) AND ((((reviewed_frequency)::text = ANY ((ARRAY['weekly'::character varying, 'monthly'::character varying, 'quarterly'::character varying, 'annual'::character varying])::text[])) AND (reviewed_frequency IS NOT NULL)) OR ((reviewed_annual_pledge = (0)::numeric) AND (reviewed_frequency IS NULL)))) OR ((decision IS NULL) AND (reviewed_annual_pledge IS NULL) AND (reviewed_frequency IS NULL)) OR (((decision)::text = ANY ((ARRAY['accepted'::character varying, 'ignored'::character varying])::text[])) AND (reviewed_annual_pledge IS NULL) AND (reviewed_frequency IS NULL)))), CONSTRAINT "pledge_review_action_state" CHECK (((((action)::text = 'accept'::text) AND ((decision)::text = 'accepted'::text) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'edit'::text) AND ((decision)::text = 'accepted_edited'::text) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'ignore'::text) AND ((decision)::text = 'ignored'::text) AND (decision IS NOT NULL) AND (NOT entered)) OR (((action)::text = 'back_to_review'::text) AND (decision IS NULL) AND (NOT entered)) OR (((action)::text = 'entered_by_hand'::text) AND ((decision)::text = ANY ((ARRAY['accepted'::character varying, 'accepted_edited'::character varying])::text[])) AND (decision IS NOT NULL) AND entered))), CONSTRAINT "pledge_review_mark_shape" CHECK (((entered AND marked_pledge_known) OR ((marked_pledge IS NULL) AND (NOT marked_pledge_known)))));
ALTER TABLE "stewardship_pledge_review" ADD CONSTRAINT "stewardship_pledge_r_family_id_89c74c5b_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_pledge_review" ADD CONSTRAINT "stewardship_pledge_r_submission_id_c16fe99f_fk_stewardsh" FOREIGN KEY ("submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_pledge_review_correlation_id_4cb3b433" ON "stewardship_pledge_review" ("correlation_id");
CREATE INDEX "stewardship_pledge_review_family_id_89c74c5b" ON "stewardship_pledge_review" ("family_id");
CREATE INDEX "stewardship_pledge_review_submission_id_c16fe99f" ON "stewardship_pledge_review" ("submission_id");

-- One response's pledge answer as the stream compares it: the annual pledge
-- as exact two-decimal text (never a float), the frequency ("" for none),
-- whether the Family cannot contribute, and the share methods chosen with
-- their Other text (a JSON object, so key order never matters). Responses
-- recorded before cannot_give existed read it as false, as the Financial
-- stewardship report does.
CREATE FUNCTION public.stewardship_pledge_answer_v1(answers jsonb, annual_pledge numeric)
RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT jsonb_build_object(
        'annual_pledge',annual_pledge::numeric(14,2)::text,
        'frequency',coalesce(answers->'financial'->>'frequency',''),
        'cannot_give',coalesce(answers->'financial'->'cannot_give'='true'::jsonb,false),
        'shares',CASE WHEN jsonb_typeof(answers->'financial'->'shares')='object'
            THEN answers->'financial'->'shares' ELSE '{}'::jsonb END)
$$;

-- Each Family's pledge item in one campaign (or the one Family asked for):
-- its current live response that answers the financial questions (the rows
-- of the Financial stewardship report), its latest decision row and the
-- stage derived from them. The latest row is in force while every live
-- financial response since the one it was made on carries the same pledge
-- answer; a different one reopens the item as To review, even if a later
-- response returns to the decided answer (rule 2 compares each response
-- with the previous one). A response without the financial questions is
-- not presented, so it changes nothing (rule 5).
CREATE FUNCTION public.stewardship_pledge_review_states_v1(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL)
RETURNS TABLE(family_id uuid, family_duid bigint, submission_id uuid,
    sequence bigint, stage text, review_id uuid, in_force boolean)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH current AS (
        SELECT f.id AS family_id,f.family_duid,s.id AS submission_id
        FROM stewardship_family_campaign f
        JOIN stewardship_submission s ON s.id=f.effective_submission_id
            AND s.mode='live' AND s.campaign_id=f.campaign_id AND s.answers ? 'financial'
        WHERE f.campaign_id=campaign_uuid AND (family_uuid IS NULL OR f.id=family_uuid)
    ), latest AS (
        SELECT DISTINCT ON (r.family_id) r.id,r.family_id,r.sequence,r.decision,
            r.entered,r.answer,d.campaign_sequence
        FROM current c
        JOIN stewardship_pledge_review r ON r.family_id=c.family_id
        JOIN stewardship_submission d ON d.id=r.submission_id
        ORDER BY r.family_id,r.sequence DESC
    )
    SELECT c.family_id,c.family_duid::bigint,c.submission_id,coalesce(l.sequence,0),
        CASE WHEN NOT coalesce(k.in_force,false) OR l.decision IS NULL THEN 'to_review'
            WHEN l.decision='ignored' THEN 'ignored'
            WHEN l.entered THEN 'entered_by_hand'
            ELSE 'to_enter_by_hand' END,
        l.id,coalesce(k.in_force,false)
    FROM current c
    LEFT JOIN latest l ON l.family_id=c.family_id
    LEFT JOIN LATERAL (SELECT NOT EXISTS(
            SELECT 1 FROM stewardship_submission x
            WHERE x.family_id=c.family_id AND x.campaign_id=campaign_uuid
              AND x.mode='live' AND x.campaign_sequence>=l.campaign_sequence
              AND x.answers ? 'financial'
              AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                  IS DISTINCT FROM l.answer) AS in_force) k ON l.id IS NOT NULL
$$;

CREATE FUNCTION public.stewardship_pledge_review_guard_v1() RETURNS trigger
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
       -- Nothing reads ParishSoft's upcoming-year pledge before #1704.
       OR NEW.marked_pledge_known
       OR NOT coalesce(offered,false)
    THEN RAISE EXCEPTION 'Pledge review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- The answer decided on and the attribution time are database-owned.
    NEW.answer:=public.stewardship_pledge_answer_v1(response.answers,response.annual_pledge);
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_pledge_review_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid;
BEGIN
    SELECT campaign_id INTO campaign FROM public.stewardship_family_campaign
        WHERE id=NEW.family_id;
    IF NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='pledge_review_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.sequence-1,'after_version',NEW.sequence))
    THEN RAISE EXCEPTION 'Pledge review requires its audit' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_pledge_review_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_pledge_review
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_pledge_review_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_pledge_review_effect
    AFTER INSERT ON public.stewardship_pledge_review DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_pledge_review_effect_v1();

DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_pledge_review'::regclass
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              AND p.proname IN ('stewardship_pledge_review_guard_v1',
                                'stewardship_pledge_review_effect_v1'))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_pledge_review_effect'
              AND tgrelid='public.stewardship_pledge_review'::regclass
              AND tgdeferrable AND tginitdeferred)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc LIKE '%stewardship_pledge_review_states_v1(%'
              AND prosrc LIKE '%classid=736220 AND objid=1 AND objsubid=2%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_effect_v1()'::regprocedure
              AND prosrc LIKE '%pledge_review_updated%')
    THEN
        RAISE EXCEPTION 'stewardship_pledge_review is not guarded as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_answer_v1(jsonb,numeric)'::regprocedure
              AND provolatile='i' AND NOT prosecdef)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_states_v1(uuid,uuid)'::regprocedure
              AND provolatile='s' AND NOT prosecdef
              AND prosrc LIKE '%x.campaign_sequence>=l.campaign_sequence%')
    THEN
        RAISE EXCEPTION 'The pledge review selection is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_pledge_review'::regclass
              AND conname IN ('pledge_review_sequence','pledge_review_replay',
                  'pledge_review_identity','pledge_review_action',
                  'pledge_review_decision','pledge_review_value_shape',
                  'pledge_review_action_state','pledge_review_mark_shape'))<>8
    THEN
        RAISE EXCEPTION 'stewardship_pledge_review lacks its checks';
    END IF;
END $check$;
