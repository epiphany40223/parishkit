-- Frozen forward migration file 0180 (the repository-wide file sequence,
-- assigned by the coordinator): undoing a decision on a Ministry request
-- the parish added returns it to To review instead of withdrawing it
-- (#2173; review-pipeline spec, "Reviewed stage" and "What the reviewed
-- form shows"). This file is installed by the Django migration that names
-- it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that each replaced function's copy here equals the fresh-install
-- baseline's (schema/ministry_followup.sql, schema/guards.sql).
--
-- The Administrator decided (2026-10-25) that an Added item behaves
-- exactly like a submitted one: Back to review sends it to In progress (To
-- review), where it stays listed, and Ignore is how Staff drop it. Until
-- now Back to review on an added request recorded it cancelled, and it
-- left every list. Requests withdrawn that way stay withdrawn: no row is
-- updated. Two functions are replaced, each copied whole from its latest
-- body, with only these changes:
--   stewardship_ministry_revision_guard_v1 (0158's)
--       * Back to review of a resolved or closed request the parish added
--         records In progress, as for any other request; no revision
--         records one cancelled any more.
--   stewardship_ministry_submission_effects_v1 (0154's)
--       * a Family response may leave an open request the parish added in
--         force: an untick never withdraws parish work (reviewed rule 3,
--         which already held for a decided one), so the completeness check
--         no longer asks the response to replace or cancel it.
-- In both, each ?, ?|, ?&, @> and <@ is also written OPERATOR(pg_catalog.x)
-- (#2166, operations spec, post-launch schema policy), so it cannot resolve
-- to a look-alike elsewhere on search_path; the fresh-install copies match.
-- The request guard is left as it is: its branch for a withdrawn added
-- request needs a paired cancelled revision, which the revision guard now
-- refuses. Neither replaced function is SECURITY DEFINER, before or after
-- (each was created without one and no baseline ALTER names it), so there
-- is no ALTER FUNCTION to repeat; CREATE OR REPLACE keeps their owners,
-- grants and triggers, which the DO block below compares with those
-- recorded before the replacement, after checking each new body. No
-- table, column, row, grant or credential changes, and no Family code,
-- link or session is touched. No temporary objects. Reversing needs its
-- own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0180_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_revision_guard_v1',
        'stewardship_ministry_submission_effects_v1')), true);

-- stewardship_ministry_revision_guard_v1, as 0158_ignored_note_optional.sql
-- left it, with Back to review of an added request sent to In progress.
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
    -- resolved or closed request changes only by going back to review,
    -- below; one the parish added too, never withdrawn (#2173).
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
       -- parish added goes back to In progress too: Ignore, not Back to
       -- review, is how Staff drop one (#2173).
       OR (reopen AND NOT undo
           AND (NEW.state<>'in_progress'
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
           OR NOT coalesce(asked OPERATOR(pg_catalog.@>) to_jsonb(NEW.reviewed_ministry_duid),false)
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

-- stewardship_ministry_submission_effects_v1, as
-- 0154_reviewed_ministry_resubmission.sql left it, with an open request the
-- parish added admitted unreplaced.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_submission_effects_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    group_name text; entity text; member_key text; choices jsonb;
    action_name text; identifiers jsonb; identifier jsonb;
    prior public.stewardship_ministry_request;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_submission WHERE id=NEW.id)
       OR NEW.answers->'ministries'='{}'::jsonb THEN RETURN NULL; END IF;
    FOREACH group_name IN ARRAY ARRAY['members','proposed_members'] LOOP
        entity := CASE WHEN group_name='members' THEN 'member' ELSE 'proposed_member' END;
        FOR member_key,choices IN SELECT * FROM jsonb_each(NEW.answers#>ARRAY['ministries',group_name]) LOOP
            FOR action_name,identifiers IN SELECT * FROM jsonb_each(choices) LOOP
                FOR identifier IN SELECT * FROM jsonb_array_elements(identifiers) LOOP
                    -- A choice repeating a decided request's action for the
                    -- same Member and Ministry derives nothing: the decided
                    -- request keeps its decision ("Family asked again", #1684).
                    IF NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_request request
                        WHERE request.submission_id=NEW.id AND request.entity_kind=entity
                          AND request.entity_key=member_key AND request.ministry_duid=identifier::text::integer
                          AND request.action=action_name)
                       AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_request decided
                        WHERE decided.id=public.stewardship_ministry_predecessor_v1(
                              NEW,entity,member_key,identifier::text::integer)
                          AND decided.state IN ('resolved','closed_no_response')
                          AND decided.action=action_name)
                       -- A response drawn as the reviewed form (#1705) left
                       -- a request it showed chosen as shown: the request,
                       -- on an earlier response of this Family and still in
                       -- force, keeps its stage and decision with no new row
                       -- (reviewed rule 1). The Ministry it showed is its own,
                       -- or the one its Edit carried forward.
                       AND NOT (NEW.reviewed_view AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
                        JOIN public.stewardship_ministry_request item ON item.id::text=shown->>'item'
                        JOIN public.stewardship_submission history ON history.id=item.submission_id
                        WHERE shown->>'stream'='ministry' AND shown->>'shown' IN ('family','reviewer')
                          AND shown->>'entity'=entity AND shown->>'key'=member_key
                          AND shown->>'action'=action_name
                          AND item.entity_kind=entity AND item.entity_key=member_key
                          AND item.action=action_name
                          AND item.state NOT IN ('cancelled','superseded')
                          AND history.family_id=NEW.family_id AND history.campaign_id=NEW.campaign_id
                          AND history.mode=NEW.mode AND history.family_version<NEW.family_version
                          AND identifier::text::integer=CASE
                              WHEN shown->>'shown'='reviewer' AND shown->>'decision' IS NOT NULL
                              THEN (SELECT edit.reviewed_ministry_duid FROM public.stewardship_ministry_revision edit
                                  WHERE edit.id::text=shown->>'decision' AND edit.request_id=item.id)
                              ELSE item.ministry_duid END)) THEN
                        RAISE EXCEPTION 'Submitted Ministry choice requires atomic derived work' USING ERRCODE='23514';
                    END IF;
                END LOOP;
            END LOOP;
        END LOOP;
    END LOOP;
    FOR prior IN SELECT DISTINCT ON (request.entity_kind,request.entity_key,request.ministry_duid) request.*
        FROM public.stewardship_ministry_request request
        JOIN public.stewardship_submission history ON history.id=request.submission_id
        JOIN public.stewardship_submission previous ON previous.id=NEW.prior_submission_id
        WHERE history.family_id=NEW.family_id AND history.campaign_id=NEW.campaign_id
          AND history.mode=NEW.mode AND history.rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
          AND history.family_version<=previous.family_version
        ORDER BY request.entity_kind,request.entity_key,request.ministry_duid,history.family_version DESC
    LOOP
        IF prior.state IN ('new','assigned','in_progress')
           -- An open request the parish added is never withdrawn by the
           -- Family (reviewed rule 3): Staff sent it back to review, and
           -- it stays parish work until they decide (#2173).
           AND NOT prior.parish_added
           -- An open request a reviewed form (#1705) showed chosen, left
           -- chosen: it stays as it is (reviewed rule 1).
           AND NOT (NEW.reviewed_view AND EXISTS (
               SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
               WHERE shown->>'stream'='ministry' AND shown->>'item'=prior.id::text
                 AND shown->>'shown'='family'
                 AND coalesce(NEW.answers#>ARRAY['ministries',
                     CASE WHEN prior.entity_kind='member' THEN 'members' ELSE 'proposed_members' END,
                     prior.entity_key,prior.action] OPERATOR(pg_catalog.@>) to_jsonb(prior.ministry_duid),false)))
           AND public.stewardship_response_ministry_visible_v1(
               NEW.validation_source_id,NEW.configuration_id,NEW.campaign_id,prior.ministry_duid)
           AND ((prior.entity_kind='member' AND (NEW.answers->'members') OPERATOR(pg_catalog.?) prior.entity_key)
                OR (prior.entity_kind='proposed_member' AND
                    public.stewardship_ministry_proposed_scope_v1(NEW,prior.entity_key))) THEN
            RAISE EXCEPTION 'New informed response must reconcile earlier Ministry work' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

DO $check$
BEGIN
    -- Each replaced function, once, invoker, with its new rule and without
    -- the old one; the rules kept from its earlier body remain.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef AND p.prosrc LIKE '%(#2173)%'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.proname='stewardship_ministry_revision_guard_v1'
          AND p.prosrc NOT LIKE '%''cancelled'' ELSE ''in_progress''%'
          AND p.prosrc LIKE '%AND (NEW.state<>''in_progress''
           OR NEW.outcome IS NOT NULL%'
          -- 0148's terminal undo and 0158's optional Ignored note, kept.
          AND p.prosrc LIKE '%undo := reopen AND NEW.state=''resolved'';%'
          AND p.prosrc LIKE '%AND (NEW.decision_note !~ ''\\S'' OR NOT operational))))%'
          AND p.prosrc LIKE '%OR (NOT reopen AND NEW.state=''cancelled'')%'
          -- Its operator, qualified (#2166).
          AND p.prosrc LIKE '%coalesce(asked OPERATOR(pg_catalog.@>) to\_jsonb(%')<>1
    THEN
        RAISE EXCEPTION 'Migration 0180 did not replace stewardship_ministry_revision_guard_v1';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef AND p.prosrc LIKE '%(#2173)%'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.proname='stewardship_ministry_submission_effects_v1'
          AND p.prosrc LIKE '%IF prior.state IN (''new'',''assigned'',''in_progress'')%AND NOT prior.parish_added%New informed response must reconcile earlier Ministry work%'
          -- 0154's reviewed form rules and 0131's, kept.
          AND p.prosrc LIKE '%WHERE shown->>''stream''=''ministry'' AND shown->>''item''=prior.id::text%'
          AND p.prosrc LIKE '%AND decided.state IN (''resolved'',''closed_no_response'')%'
          AND p.prosrc LIKE '%Submitted Ministry choice requires atomic derived work%'
          -- Its operators, qualified (#2166).
          AND p.prosrc LIKE '%prior.action] OPERATOR(pg_catalog.@>) to\_jsonb(prior.ministry\_duid)%'
          AND p.prosrc LIKE '%(NEW.answers->''members'') OPERATOR(pg_catalog.?) prior.entity\_key)%')<>1
    THEN
        RAISE EXCEPTION 'Migration 0180 did not replace stewardship_ministry_submission_effects_v1';
    END IF;
    -- The guards still run as the triggers they were.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE NOT t.tgisinternal AND t.tgenabled='O'
          AND (t.tgrelid,t.tgname,p.proname) IN (
            ('public.stewardship_ministry_revision'::regclass,'stewardship_ministry_revision_guard',
                'stewardship_ministry_revision_guard_v1'),
            ('public.stewardship_submission'::regclass,'stewardship_ministry_submission_effects',
                'stewardship_ministry_submission_effects_v1')))<>2 THEN
        RAISE EXCEPTION 'Migration 0180 lost a guard trigger';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_revision_guard_v1',
            'stewardship_ministry_submission_effects_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0180_acl') THEN
        RAISE EXCEPTION 'Migration 0180 changed the guards'' grants or settings';
    END IF;
END
$check$;
