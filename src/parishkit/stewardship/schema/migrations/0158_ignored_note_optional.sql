-- Frozen forward migration file 0158 (the repository-wide file sequence;
-- reserved by the coordinator, final in merge order): reopening an Ignored
-- item no longer needs a note (#1889). This file is installed by its Django
-- migration in the responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that each replaced baseline function's copy here equals the
-- fresh-install baseline's (schema/ministry_followup.sql,
-- schema/information_followup.sql).
--
-- The Administrator decided (2026-10-21) that Back to review from Ignored
-- takes an optional note, as every other decision does: a note is required
-- only to undo a terminal step (review pipeline spec, "Reviewed stage",
-- "Undoing a terminal stage"). Replaced, each copied whole from its latest
-- body with only its Ignored-note check dropped (and the comments saying
-- so); every terminal step's required note, every role, version and state
-- rule stays (0210-0215 are #1856's provisional file numbers; a renumber
-- of those files renames them here too):
--   stewardship_proposal_review_guard_v1      (0130's) an ignored census
--       change sent back to review (decision unreviewed) with no note;
--   stewardship_proposal_resolution_guard_v1  (0210's) a 'reopened'
--       resolution of an open ignored change (still the Administrator's)
--       with no note; the undo of a terminal step still needs one;
--   stewardship_ministry_revision_guard_v1    (0212's) an ignored Ministry
--       request reopened with no decision note;
--   stewardship_pledge_review_guard_v1        (0213's) an ignored pledge's
--       Back to review with no note;
--   stewardship_information_revision_guard_v1 (0214's) an Ignored request
--       leaving Ignored (back to review, or straight to follow-up from
--       pk-admin) with no new notes;
--   stewardship_limitation_review_guard_v1    (0215's) an Ignored
--       cannot-participate or cannot-attend answer's Back to review with no
--       note.
-- None of them is SECURITY DEFINER, before or after (each was created
-- without one and no baseline ALTER names it), so there is no ALTER
-- FUNCTION to repeat; CREATE OR REPLACE keeps their owners, grants and
-- triggers, which the DO block below compares with those recorded before
-- the replacement, after checking each new body. No table, column, row or
-- grant changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0158_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_proposal_review_guard_v1',
        'stewardship_proposal_resolution_guard_v1','stewardship_ministry_revision_guard_v1',
        'stewardship_pledge_review_guard_v1','stewardship_information_revision_guard_v1',
        'stewardship_limitation_review_guard_v1')), true);

CREATE OR REPLACE FUNCTION public.stewardship_proposal_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
    decided boolean; edited boolean; edit_type text; edit_key text; edit_text text;
    resolved boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Census change reviews are immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO proposal FROM public.stewardship_proposed_change
        WHERE id=NEW.proposal_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=proposal.submission_id AND mode='live';
    decided := NEW.decision IS DISTINCT FROM proposal.decision;
    edited := ROW(NEW.admin_value_set,NEW.admin_value)
        IS DISTINCT FROM ROW(proposal.admin_value_set,proposal.admin_value);
    resolved := NEW.accepted_base;
    IF proposal.id IS NULL OR campaign IS NULL
       -- A current Staff member or Administrator (review pipeline,
       -- decision 1); only an Administrator starts an update run.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,false)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM proposal.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       -- Every census change the worklist lists, Automatic or By hand
       -- (decision 2); report-only rows belong to other streams.
       OR proposal.handling NOT IN ('api','manual')
       -- Open only: never while queued for publication, never once final.
       OR proposal.execution NOT IN ('pending','conflict','failed')
       -- A conflict is approved only by resolving it (ADM-09.03, below).
       OR (NEW.decision='approved' AND proposal.execution='conflict' AND NOT resolved)
       -- A review records something: a decision, an edit or a resolution.
       OR NOT (decided OR edited OR resolved)
       -- A decision and an edit together only when the decision accepts:
       -- Edit is Accept with a value (review pipeline, "Reviewed stage").
       OR (decided AND edited AND NEW.decision<>'approved')
       -- A resolution ("Use the Family's value anyway", ADM-09.03; the
       -- worklist's Accept or Edit on a Conflict) accepts ParishSoft's
       -- current value, exactly as the proposal holds it and from the
       -- snapshot it came from, as the base publication merges against, and
       -- approves the change. Only on a conflict whose ParishSoft value is
       -- known.
       OR (resolved AND (proposal.execution<>'conflict'
           OR NOT proposal.current_available OR NEW.decision<>'approved'
           OR NEW.accepted_value IS DISTINCT FROM proposal.current_value
           OR NEW.accepted_source_id IS DISTINCT FROM proposal.current_source_id))
       -- Every other review accepts nothing.
       OR (NOT resolved AND ROW(NEW.accepted_value,NEW.accepted_source_id)
           IS DISTINCT FROM ROW(NULL::jsonb,NULL::uuid))
       -- Sending an ignored change back to review takes an optional
       -- note, as every decision does (#1889).
    THEN RAISE EXCEPTION 'Census change review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    IF edited AND NEW.admin_value_set THEN
        -- No value can be typed for a yes-or-no answer or a whole New Member:
        -- those are accepted or ignored as the Family gave them.
        IF proposal.field IN ('moved_household','deceased_status','email_opt_out','new_member')
        THEN RAISE EXCEPTION 'Census change edit is not offered for this field'
            USING ERRCODE='23514'; END IF;
        -- A phone edit is a phone record, an address edit an address of its
        -- parts, any other edit text. (The type is worked out first: an IF
        -- condition ends at its first THEN.)
        edit_type := CASE WHEN proposal.field IN ('home_phone','mobile_phone','work_phone',
                'home_address','mailing_address')
            THEN 'object' ELSE 'string' END;
        IF NEW.admin_value IS NULL OR NEW.admin_value='null'::jsonb
           OR jsonb_typeof(NEW.admin_value) IS DISTINCT FROM edit_type
           OR octet_length(NEW.admin_value::text)>1024
        THEN RAISE EXCEPTION 'Census change edit has no value of its field''s shape'
            USING ERRCODE='23514'; END IF;
        -- The comparison validates the shape (a phone record is its own
        -- display's, an address has exactly its six text parts, a date an
        -- ISO civil date) and raises on a bad one. Its key is trimmed of the
        -- full whitespace set, so a blank edit (a tab, a no-break or
        -- ideographic space) has an empty key; a phone's key is not text, so
        -- its display is read the same way, and an address is blank when its
        -- street line is.
        edit_key := public.stewardship_response_comparison_v1(proposal.field,NEW.admin_value);
        edit_text := CASE
            WHEN proposal.field IN ('home_phone','mobile_phone','work_phone')
            THEN public.stewardship_response_comparison_v1('nickname',NEW.admin_value->'display')
            WHEN proposal.field IN ('home_address','mailing_address')
            THEN edit_key::jsonb->>'line1'
            ELSE edit_key END;
        IF edit_key IS NULL OR coalesce(edit_text,'')='' OR edit_key IS NOT DISTINCT FROM
            public.stewardship_response_comparison_v1(proposal.field,proposal.submitted_value)
        THEN RAISE EXCEPTION 'Census change edit must differ from the Family''s answer'
            USING ERRCODE='23514'; END IF;
        -- An address edit meets the Family's own address rules (#1719): the
        -- same function that holds the Family's answer, so the vocabulary
        -- (countries, US regions, ZIP) has one SQL copy. It runs after the
        -- checks above so their messages stay for the shape and a blank
        -- street line; it raises 23514 for anything the Family form refuses.
        IF proposal.field IN ('home_address','mailing_address') THEN
            PERFORM public.stewardship_response_address_guard_v1(NEW.admin_value);
        END IF;
    END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_proposal_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
        by_hand boolean; open_state boolean; incorrect boolean;
        replaced boolean; again boolean; undo boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Census change resolutions are immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO proposal FROM public.stewardship_proposed_change
        WHERE id=NEW.proposal_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=proposal.submission_id AND mode='live';
    by_hand:=proposal.handling<>'api' OR proposal.entity_kind='family';
    open_state:=proposal.execution IN ('pending','conflict');
    -- A change a later response of the Family replaced (the same field
    -- answered again) is history: it takes neither Entered incorrectly's
    -- fixes nor a Member ID correction.
    replaced:=EXISTS (SELECT 1 FROM public.stewardship_proposed_change later
            JOIN public.stewardship_submission answered ON answered.id=later.submission_id
            JOIN public.stewardship_submission earlier ON earlier.id=proposal.submission_id
            WHERE answered.family_id=earlier.family_id
              AND answered.campaign_id=earlier.campaign_id AND answered.mode=earlier.mode
              AND answered.family_version>earlier.family_version
              AND ROW(later.entity_kind,later.entity_key,later.field)
                  =ROW(proposal.entity_kind,proposal.entity_key,proposal.field));
    -- Entered incorrectly (#1737): a change Entered by hand whose latest
    -- refresh check, at its current version, found a third value in
    -- ParishSoft. Its two fixes are Entered by hand again ('entered') and
    -- Back to review ('reopened'), Staff's as well as an Administrator's.
    incorrect:=coalesce(proposal.execution='resolved_external',false)
        AND coalesce((SELECT k.outcome='entered_incorrectly'
            FROM public.stewardship_refresh_check k
            WHERE k.proposal_id=proposal.id AND k.item_version=proposal.version
            ORDER BY k.created_at DESC, k.id DESC LIMIT 1),false)
        AND NOT replaced;
    -- A New Member's ParishSoft Member ID corrected (#1773): a New Member
    -- Entered by hand that no refresh has confirmed at its current version,
    -- marked entered again with an ID other than the one its current mark
    -- names (the resolution that advanced it to its current version, none
    -- when the mark named no ID). Staff's as well as an Administrator's. A
    -- blank ID keeps the mark in force, so it is no correction.
    again:=coalesce(proposal.execution='resolved_external',false)
        AND NOT incorrect AND NOT replaced
        AND proposal.entity_kind='proposed_member' AND proposal.field='new_member'
        AND NEW.action='entered' AND NEW.member_duid IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM public.stewardship_refresh_check k
            WHERE k.proposal_id=proposal.id AND k.item_version=proposal.version
              AND k.outcome='confirmed')
        AND NEW.member_duid IS DISTINCT FROM (SELECT r.member_duid
            FROM public.stewardship_proposal_resolution r
            WHERE r.proposal_id=proposal.id AND r.expected_version=proposal.version-1
              AND r.action='entered');
    -- Back to review undoes a terminal step (#1840): a change Entered by
    -- hand (Entered incorrectly and Confirmed too), Updated in ParishSoft
    -- or Already in ParishSoft goes back to pending with the decision
    -- behind the step kept, which the census guard checks. Staff's as well
    -- as an Administrator's, with a note; never on a change a later
    -- response of the Family replaced, which is history. Entered
    -- incorrectly's Back to review (#1737) is now this undo.
    undo:=NEW.action='reopened' AND NOT replaced
        AND coalesce(proposal.execution IN ('resolved_external','published',
            'resolved_upstream'),false);
    IF proposal.id IS NULL OR campaign IS NULL
       -- Reopening an Ignore is the Administrator's; Entered incorrectly's
       -- Back to review is an undo, so NOT undo already holds NOT incorrect.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,
           (NOT undo AND NEW.action='reopened' AND NOT incorrect))
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM proposal.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       OR proposal.handling='report-only'
       OR (incorrect AND NEW.action NOT IN ('entered','reopened'))
       -- A final change takes only Entered incorrectly's mark made again, a
       -- New Member's ID correction or the undo of its terminal step (AND
       -- binds before OR).
       OR (NOT incorrect AND NOT open_state) AND NOT again AND NOT undo
       OR (open_state AND NEW.action IN ('entered','ignored')
           AND (NOT by_hand OR proposal.decision='ignored'))
       OR (open_state AND NEW.action='reopened' AND proposal.decision<>'ignored')
       -- Undoing a terminal step (#1840) says why; undoing an Ignore
       -- takes an optional note (#1889).
       OR (undo AND NEW.note !~ '\S')
       -- A ParishSoft Member ID (#1749) names the Member Staff added for a
       -- New Member they mark entered, which the refresh then confirms by;
       -- never a Member the Family's response already listed, nor one
       -- another New Member of the Family is entered as now (the same
       -- proposed Member answered again keeps its own).
       OR (NEW.member_duid IS NOT NULL AND (NEW.action<>'entered'
           OR proposal.entity_kind<>'proposed_member' OR proposal.field<>'new_member'
           OR coalesce((SELECT (response.answers->'members') ? NEW.member_duid::text
               FROM public.stewardship_submission response
               WHERE response.id=proposal.submission_id),true)
           OR EXISTS (SELECT 1 FROM public.stewardship_proposal_resolution other
               JOIN public.stewardship_proposed_change added ON added.id=other.proposal_id
               JOIN public.stewardship_submission answered ON answered.id=added.submission_id
               JOIN public.stewardship_submission this_response
                   ON this_response.id=proposal.submission_id
               WHERE other.member_duid=NEW.member_duid AND other.action='entered'
                 AND other.expected_version=added.version-1
                 AND added.execution='resolved_external'
                 AND added.entity_key<>proposal.entity_key
                 AND answered.family_id=this_response.family_id
                 AND answered.campaign_id=this_response.campaign_id
                 AND answered.mode=this_response.mode)))
    THEN RAISE EXCEPTION 'Census change resolution lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid; asked jsonb;
    reopen boolean; ticked boolean; undo boolean; confirmed boolean; operational boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry follow-up history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO target FROM stewardship_ministry_request WHERE id=NEW.request_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM stewardship_submission
        WHERE id=target.submission_id AND mode='live';
    -- Cancelled and superseded requests are immutable, and a successor
    -- created by the Family invalidates every form bound to this request. A
    -- resolved or closed request changes only by going back to review, or,
    -- one the parish added, by being withdrawn (#1684), below.
    reopen := target.state IN ('resolved','closed_no_response');
    -- Authority follows the request's current Ministry: the one an Edit
    -- carried forward, once edited (#1684).
    IF target.id IS NULL OR campaign IS NULL
       OR (target.state NOT IN ('new','assigned','in_progress') AND NOT reopen)
       OR NOT stewardship_ministry_followup_authorized_v1(NEW.actor_id,
           coalesce(target.reviewed_ministry_duid,target.ministry_duid))
       OR NOT stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM target.version
       OR NEW.expected_version>=9223372036854775807
       OR length(NEW.notes)>5000 OR length(NEW.contact_notes)>2000
       OR length(NEW.decision_note)>2000
    THEN RAISE EXCEPTION 'Ministry follow-up change lacks current authority/version'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned, so a contact cannot postdate its record.
    NEW.created_at:=statement_timestamp();
    SELECT cc.values->'ministry_duids' INTO asked FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        WHERE c.id=campaign;
    SELECT e.entered INTO ticked FROM stewardship_ministry_roster_entry e
        WHERE e.request_id=target.id ORDER BY e.sequence DESC LIMIT 1;
    -- Back to review of a terminal step (#1840): a resolved request kept
    -- resolved, with its outcome and Edit, undoes Entered in ParishSoft or
    -- a Confirmed refresh check at its version; a Staff member or
    -- Administrator only (never a Ministry leader), with a note.
    undo := reopen AND NEW.state='resolved';
    confirmed := EXISTS (SELECT 1 FROM stewardship_refresh_check k
        WHERE k.request_id=target.id AND k.item_version=target.version
          AND k.outcome='confirmed');
    operational := coalesce((stewardship_ministry_scope_v1(NEW.actor_id)->'operational')
        ='true'::jsonb,false);
    IF (NEW.assignee_id IS NOT NULL AND NOT
            stewardship_ministry_followup_authorized_v1(NEW.assignee_id,target.ministry_duid))
       OR (NEW.outcome='joined' AND target.action<>'join')
       OR (NEW.outcome='leave_confirmed' AND target.action<>'leave')
       OR NEW.contact_at>NEW.created_at
       -- Back to review: In progress with nothing carried forward, never
       -- while Staff ticked it Entered in ParishSoft or a refresh check
       -- Confirmed a person's decision (that step is undone first, by the
       -- undo below, keeping the decision), with an optional note when
       -- it reopens an ignored request (#1889), and by Staff with a note
       -- when it undoes a refresh's resolution (#1840). A request the
       -- parish added is withdrawn (cancelled) instead.
       OR (reopen AND NOT undo
           AND (NEW.state<>CASE WHEN target.parish_added THEN 'cancelled' ELSE 'in_progress' END
           OR NEW.outcome IS NOT NULL OR NEW.reviewed_ministry_duid IS NOT NULL
           OR NEW.contact_channel IS NOT NULL OR coalesce(ticked,false)
           OR (confirmed AND target.resolution_source_id IS NULL)
           OR (target.resolution_source_id IS NOT NULL
               AND (NEW.decision_note !~ '\S' OR NOT operational))))
       -- The undo of Entered in ParishSoft or Confirmed (above): the
       -- decision as it stands, a person's (no refresh resolution), and a
       -- tick or a Confirmed check to undo.
       OR (undo AND (target.state<>'resolved'
           OR target.outcome NOT IN ('joined','leave_confirmed')
           OR NEW.outcome IS DISTINCT FROM target.outcome
           OR NEW.reviewed_ministry_duid IS DISTINCT FROM target.reviewed_ministry_duid
           OR NEW.contact_channel IS NOT NULL
           OR target.resolution_source_id IS NOT NULL
           OR NOT (coalesce(ticked,false) OR confirmed)
           OR NEW.decision_note !~ '\S' OR NOT operational))
       OR (NOT reopen AND NEW.state='cancelled')
       -- Edit: Accept with another of the campaign's Ministries carried
       -- forward, which the actor may also follow up (a leader edits only
       -- into a Ministry they lead).
       -- (The undo keeps the Edit it had, checked above.)
       OR (NEW.reviewed_ministry_duid IS NOT NULL AND NOT undo AND (NEW.state<>'resolved'
           OR NEW.outcome IS DISTINCT FROM
               CASE WHEN target.action='join' THEN 'joined' ELSE 'leave_confirmed' END
           OR NEW.reviewed_ministry_duid=target.ministry_duid
           OR NOT coalesce(asked @> to_jsonb(NEW.reviewed_ministry_duid),false)
           OR NOT stewardship_ministry_followup_authorized_v1(NEW.actor_id,NEW.reviewed_ministry_duid)
           -- Never a second current request about one Member and Ministry.
           OR EXISTS (SELECT 1 FROM stewardship_ministry_request other
               JOIN stewardship_submission history ON history.id=other.submission_id
               JOIN stewardship_submission own ON own.id=target.submission_id
               WHERE other.id<>target.id AND history.family_id=own.family_id
                 AND history.campaign_id=own.campaign_id AND history.mode='live'
                 AND other.entity_kind=target.entity_kind
                 AND other.entity_key=target.entity_key
                 AND NEW.reviewed_ministry_duid IN (other.ministry_duid,
                     other.reviewed_ministry_duid)
                 AND other.state NOT IN ('cancelled','superseded'))))
    THEN RAISE EXCEPTION 'Ministry follow-up change is not valid for this request'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

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
            -- takes an optional note, as any decision does (#1889).
            WHEN item.stage='to_enter_by_hand' THEN NEW.decision IS NULL
            WHEN item.stage='ignored' THEN NEW.decision IS NULL
            -- Undoing a terminal step (#1840) needs a reason and removes
            -- only the step: the decision its rows keep in force, with its
            -- values, stays (To enter by hand); with none behind it (a
            -- pledge ParishSoft already held) it keeps none (To review).
            WHEN item.stage IN ('entered_by_hand','entered_incorrectly','confirmed')
                THEN NEW.note ~ '\S' AND CASE
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
    -- An Ignored request leaves Ignored, whether back to review or
    -- straight to follow-up, with an optional note, as every decision
    -- takes one (#1889).
    -- Leaving Handled (a completed follow-up cleared, whether by the
    -- worklist's Back to review or the command line's clear) undoes a
    -- terminal step, which needs a new note saying why (#1840).
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
           -- Reopening an Ignored answer takes an optional note (#1889).
           WHEN 'ignored' THEN NEW.decision='none'
           -- Handled's undo (#1840) keeps the Accept: To handle again.
           WHEN 'handled' THEN NEW.decision='accepted' AND NEW.note ~ '\S'
           ELSE false END)
    THEN RAISE EXCEPTION 'Limitation review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    -- Each replaced function, once, invoker, with its new text and without
    -- its Ignored-note check; the terminal steps' note checks remain.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef AND p.prosrc LIKE '%(#1889)%'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND ((p.proname='stewardship_proposal_review_guard_v1'
                AND p.prosrc NOT LIKE '%NEW.decision=''unreviewed''%'
                AND p.prosrc LIKE '%PERFORM public.stewardship_response_address_guard_v1(NEW.admin_value);%'
                AND p.prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%')
            OR (p.proname='stewardship_proposal_resolution_guard_v1'
                AND p.prosrc LIKE '%OR (undo AND NEW.note !~ ''\\S'')%'
                AND p.prosrc NOT LIKE '%OR (NEW.action=''reopened'' AND NEW.note%'
                AND p.prosrc LIKE '%undo:=NEW.action=''reopened'' AND NOT replaced%')
            OR (p.proname='stewardship_ministry_revision_guard_v1'
                AND p.prosrc NOT LIKE '%target.outcome NOT IN (''joined'',''leave_confirmed'') AND NEW.decision_note%'
                AND p.prosrc LIKE '%AND (NEW.decision_note !~ ''\\S'' OR NOT operational))))%'
                AND p.prosrc LIKE '%OR NEW.decision_note !~ ''\\S'' OR NOT operational))%'
                AND p.prosrc LIKE '%undo := reopen AND NEW.state=''resolved'';%')
            OR (p.proname='stewardship_pledge_review_guard_v1'
                AND p.prosrc LIKE '%WHEN item.stage=''ignored'' THEN NEW.decision IS NULL
            -- Undoing a terminal step%'
                AND p.prosrc LIKE '%THEN NEW.note ~ ''\\S'' AND CASE%'
                AND p.prosrc LIKE '%classid=736220 AND objid=1 AND objsubid=2%')
            OR (p.proname='stewardship_information_revision_guard_v1'
                AND p.prosrc NOT LIKE '%Reopening an ignored request needs a note%'
                AND p.prosrc LIKE '%Undoing a handled request needs a note%'
                AND p.prosrc LIKE '%Only a request to review can be ignored%'
                AND p.prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id)%')
            OR (p.proname='stewardship_limitation_review_guard_v1'
                AND p.prosrc LIKE '%WHEN ''ignored'' THEN NEW.decision=''none''
%'
                AND p.prosrc NOT LIKE '%WHEN ''ignored'' THEN NEW.decision=''none'' AND%'
                AND p.prosrc LIKE '%WHEN ''handled'' THEN NEW.decision=''accepted'' AND NEW.note ~ ''\\S''%'
                AND p.prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id)%')))<>6 THEN
        RAISE EXCEPTION 'Migration 0158 (Ignored reopen note) did not replace the guards';
    END IF;
    -- The guards still run as the triggers they were.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE NOT t.tgisinternal AND t.tgenabled='O'
          AND (t.tgrelid,t.tgname,p.proname) IN (
            ('public.stewardship_proposal_review'::regclass,'stewardship_proposal_review_guard',
                'stewardship_proposal_review_guard_v1'),
            ('public.stewardship_proposal_resolution'::regclass,'stewardship_proposal_resolution_guard',
                'stewardship_proposal_resolution_guard_v1'),
            ('public.stewardship_ministry_revision'::regclass,'stewardship_ministry_revision_guard',
                'stewardship_ministry_revision_guard_v1'),
            ('public.stewardship_pledge_review'::regclass,'stewardship_pledge_review_guard',
                'stewardship_pledge_review_guard_v1'),
            ('public.stewardship_information_revision'::regclass,'stewardship_information_revision_guard',
                'stewardship_information_revision_guard_v1'),
            ('public.stewardship_limitation_review'::regclass,'stewardship_limitation_review_guard',
                'stewardship_limitation_review_guard_v1')))<>6 THEN
        RAISE EXCEPTION 'Migration 0158 (Ignored reopen note) lost a guard trigger';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_proposal_review_guard_v1',
            'stewardship_proposal_resolution_guard_v1','stewardship_ministry_revision_guard_v1',
            'stewardship_pledge_review_guard_v1','stewardship_information_revision_guard_v1',
            'stewardship_limitation_review_guard_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0158_acl') THEN
        RAISE EXCEPTION 'Migration 0158 changed the guards'' grants or settings';
    END IF;
END
$check$;
