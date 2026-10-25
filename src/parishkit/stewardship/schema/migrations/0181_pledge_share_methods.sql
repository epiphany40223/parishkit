-- Frozen forward migration file 0181 (the repository-wide file sequence,
-- assigned by the coordinator): a pledge Edit carries the reviewer's share
-- methods (#2177; review-pipeline spec, "Financial stream"). This file is
-- installed by its Django migration in the reports app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The Administrator chose (2026-10-25) to let the pledge editor's Edit
-- carry the reviewer's share methods beside the amount and frequency, on
-- the pledge review row, next to the Family's answer the row records; no
-- staff resubmission and no new response. So this file:
--   - adds stewardship_pledge_review.reviewed_shares (jsonb, NULL): the
--     reviewer's share methods as the Family form stores them (option id to
--     its Other text, "" for none), only on an Accepted (edited) row whose
--     annual pledge is above zero (pledge_review_shares_shape), and only
--     when they differ from the Family's own; NULL carries the Family's own,
--     as every row written before this file does;
--   - replaces stewardship_pledge_review_guard_v1 (0158's body, copied
--     whole) so the mark and the undo of a terminal step also carry the
--     share methods of the decision in force, an Edit's share methods are
--     an object of strings within the Family form's text limit, and an Edit
--     never stores the Family's own share methods as the reviewer's;
--   - replaces stewardship_pledge_review_states_v1 (0155's body, copied
--     whole) so a reviewed form that sent back the edited pledge it showed
--     is unchanged with the reviewer's share methods it showed (rule 1), as
--     responses/reviewed.pledge_shown draws them; 0159's stage selection
--     (stewardship_pledge_stages_v1) reads it, so the page, the counts and
--     the guard agree.
-- In both bodies each ?, ?|, ?&, @> and <@ is written OPERATOR(pg_catalog.x)
-- (#2166, operations spec, post-launch schema policy), so it cannot resolve
-- to a look-alike elsewhere on search_path.
-- Neither function is SECURITY DEFINER, before or after (0127 created both
-- without one and no baseline ALTER names either), so there is no ALTER
-- FUNCTION to repeat; CREATE OR REPLACE keeps their owners, grants and
-- triggers. The DO block below checks the new column, check and bodies,
-- that the guard still runs as its trigger, and that the grants and
-- settings equal those recorded before the replacement. No
-- row, grant or credential changes, and no Family code, link or session
-- is touched. No temporary objects. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0181_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_pledge_review_guard_v1',
        'stewardship_pledge_review_states_v1')), true);

ALTER TABLE "stewardship_pledge_review" ADD COLUMN "reviewed_shares" jsonb NULL;
ALTER TABLE "stewardship_pledge_review" ADD CONSTRAINT "pledge_review_shares_shape" CHECK (((reviewed_shares IS NULL) OR (((decision)::text = 'accepted_edited'::text) AND (reviewed_annual_pledge > (0)::numeric))));

CREATE OR REPLACE FUNCTION public.stewardship_pledge_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE family public.stewardship_family_campaign%ROWTYPE;
    response public.stewardship_submission%ROWTYPE;
    prior public.stewardship_pledge_review%ROWTYPE;
    item record; offered boolean; shares_ok boolean;
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
            -- takes an optional note, as any decision does (#1889).
            WHEN item.stage='to_enter_by_hand' THEN NEW.decision IS NULL
            WHEN item.stage='ignored' THEN NEW.decision IS NULL
            -- Undoing a terminal step (#1840) needs a reason and removes
            -- only the step: the decision its rows keep in force, with its
            -- values and share methods (#2177), stays (To enter by hand);
            -- with none behind it (a pledge ParishSoft already held) it
            -- keeps none (To review).
            WHEN item.stage IN ('entered_by_hand','entered_incorrectly','confirmed')
                THEN NEW.note ~ '\S' AND CASE
                    WHEN item.base_stage IN ('to_enter_by_hand','entered_by_hand')
                        THEN NEW.decision IS NOT NULL
                            AND ROW(NEW.decision,NEW.reviewed_annual_pledge,
                                NEW.reviewed_frequency,NEW.reviewed_shares)
                            IS NOT DISTINCT FROM
                            ROW(prior.decision,prior.reviewed_annual_pledge,
                                prior.reviewed_frequency,prior.reviewed_shares)
                    WHEN item.base_stage='to_review' THEN NEW.decision IS NULL
                    ELSE false END
            ELSE false END
        -- The mark carries the decision in force, its values and its share
        -- methods (#2177).
        WHEN 'entered_by_hand' THEN item.stage='to_enter_by_hand'
            AND ROW(NEW.decision,NEW.reviewed_annual_pledge,NEW.reviewed_frequency,
                NEW.reviewed_shares)
                IS NOT DISTINCT FROM
                ROW(prior.decision,prior.reviewed_annual_pledge,prior.reviewed_frequency,
                    prior.reviewed_shares)
        ELSE false END;
    -- An Edit's share methods (#2177), as the Family form stores them: an
    -- object of option ids, each with its Other text within the form's
    -- 2,000-character limit ("" for a method without text), and never the
    -- Family's own share methods, which NULL already carries. Rows that
    -- carry a decision forward are compared with it above. (Computed here,
    -- as above; jsonb_each reads only an object.)
    shares_ok:=CASE
        WHEN NEW.reviewed_shares IS NULL THEN true
        WHEN NEW.action<>'edit' THEN true
        WHEN jsonb_typeof(NEW.reviewed_shares)<>'object' THEN false
        ELSE NOT EXISTS (SELECT 1 FROM jsonb_each(NEW.reviewed_shares) e
                WHERE jsonb_typeof(e.value)<>'string'
                   OR length(e.value #>> '{}')>2000)
            AND NEW.reviewed_shares IS DISTINCT FROM
                public.stewardship_pledge_answer_v1(response.answers,
                    response.annual_pledge)->'shares' END;
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
       OR NOT coalesce(shares_ok,false)
    THEN RAISE EXCEPTION 'Pledge review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- The answer decided on, the item's A after the row and the attribution
    -- time are database-owned.
    NEW.answer:=public.stewardship_pledge_answer_v1(response.answers,response.annual_pledge);
    NEW.baseline_pledge:=item.next_baseline;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_pledge_review_states_v1(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL)
RETURNS TABLE(family_id uuid, family_duid bigint, submission_id uuid,
    sequence bigint, stage text, review_id uuid, in_force boolean)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH current AS (
        SELECT f.id AS family_id,f.family_duid,s.id AS submission_id
        FROM stewardship_family_campaign f
        JOIN stewardship_submission s ON s.id=f.effective_submission_id
            AND s.mode='live' AND s.campaign_id=f.campaign_id
            AND s.answers OPERATOR(pg_catalog.?) 'financial'
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
              AND x.answers OPERATOR(pg_catalog.?) 'financial'
              AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                  IS DISTINCT FROM l.answer
              -- A response drawn as the reviewed form (#1705) that sent back
              -- the edited pledge the form showed (the reviewer's amount and
              -- frequency, the reviewer's share methods when the Edit
              -- changed them (#2177), else the Family's own, and the
              -- Family's own Cannot contribute answer; a zero pledge with
              -- neither) is unchanged.
              AND NOT (x.reviewed_view AND EXISTS (
                  SELECT 1 FROM jsonb_array_elements(x.shown_items) e
                  JOIN stewardship_pledge_review v ON v.id::text=e->>'decision'
                  JOIN stewardship_submission y ON y.id::text=e->>'item'
                  CROSS JOIN LATERAL (SELECT stewardship_pledge_answer_v1(
                      y.answers,y.annual_pledge) AS answer) b
                  WHERE e->>'stream'='financial' AND e->>'shown'='reviewer'
                    AND v.family_id=c.family_id AND y.family_id=c.family_id
                    AND v.reviewed_annual_pledge IS NOT NULL
                    AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                        =jsonb_build_object(
                            'annual_pledge',v.reviewed_annual_pledge::numeric(14,2)::text,
                            'frequency',CASE WHEN v.reviewed_annual_pledge>0
                                THEN coalesce(v.reviewed_frequency,'') ELSE '' END,
                            'cannot_give',CASE WHEN v.reviewed_annual_pledge>0
                                THEN 'false'::jsonb ELSE b.answer->'cannot_give' END,
                            'shares',CASE WHEN v.reviewed_annual_pledge>0
                                THEN coalesce(v.reviewed_shares,b.answer->'shares')
                                ELSE '{}'::jsonb END)))
            ) AS in_force) k ON l.id IS NOT NULL
$$;

DO $check$
BEGIN
    -- The new column and its check, as declared.
    IF NOT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid='public.stewardship_pledge_review'::regclass
              AND attname='reviewed_shares' AND NOT attisdropped AND NOT attnotnull
              AND atttypid='jsonb'::regtype)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_pledge_review'::regclass
              AND conname='pledge_review_shares_shape' AND contype='c' AND convalidated
              AND pg_get_constraintdef(oid) LIKE '%reviewed_shares IS NULL%'
              AND pg_get_constraintdef(oid) LIKE '%''accepted_edited''::text%'
              AND pg_get_constraintdef(oid) LIKE '%reviewed_annual_pledge > (0)::numeric%')
    THEN
        RAISE EXCEPTION 'Migration 0181 (pledge share methods) did not add the column';
    END IF;
    -- The new bodies, with the rules kept from 0158 and 0155.
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%NEW.reviewed_frequency,NEW.reviewed_shares)%'
              AND prosrc LIKE '%prior.reviewed_frequency,prior.reviewed_shares)%'
              AND prosrc LIKE '%prior.reviewed_shares)
        ELSE false END;%'
              AND prosrc LIKE '%OR NOT coalesce(shares_ok,false)%'
              AND prosrc LIKE '%jsonb_each(NEW.reviewed_shares)%'
              -- 0158's rules, kept.
              AND prosrc LIKE '%WHEN item.stage=''ignored'' THEN NEW.decision IS NULL%'
              AND prosrc LIKE '%THEN NEW.note ~ ''\\S'' AND CASE%'
              AND prosrc LIKE '%classid=736220 AND objid=1 AND objsubid=2%'
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc LIKE '%NEW.baseline_pledge:=item.next_baseline;%'
              AND NOT prosecdef AND provolatile='v')
    THEN
        RAISE EXCEPTION 'Migration 0181 (pledge share methods) did not replace the guard';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_states_v1(uuid,uuid)'::regprocedure
              AND prosrc LIKE '%THEN coalesce(v.reviewed_shares,b.answer->''shares'')%'
              -- 0155's and 0127's selection, kept.
              AND prosrc LIKE '%AND NOT (x.reviewed_view AND EXISTS (%'
              AND prosrc LIKE '%IS DISTINCT FROM l.answer%'
              AND prosrc LIKE '%x.campaign_sequence>=l.campaign_sequence%'
              AND prosrc LIKE '%WHEN l.entered THEN ''entered_by_hand''%'
              -- Its operators, qualified (#2166).
              AND prosrc LIKE '%s.answers OPERATOR(pg_catalog.?) ''financial''%'
              AND prosrc LIKE '%x.answers OPERATOR(pg_catalog.?) ''financial''%'
              AND prosrc NOT LIKE '%answers ? ''financial''%'
              AND NOT prosecdef AND provolatile='s'
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
    THEN
        RAISE EXCEPTION 'Migration 0181 (pledge share methods) did not replace the selection';
    END IF;
    -- The guard still runs as the trigger it was.
    IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_pledge_review'::regclass
              AND t.tgname='stewardship_pledge_review_guard' AND NOT t.tgisinternal
              AND t.tgenabled='O' AND p.proname='stewardship_pledge_review_guard_v1')
    THEN
        RAISE EXCEPTION 'Migration 0181 (pledge share methods) lost the guard trigger';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_pledge_review_guard_v1',
            'stewardship_pledge_review_states_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0181_acl') THEN
        RAISE EXCEPTION 'Migration 0181 changed the functions'' grants or settings';
    END IF;
END
$check$;
