-- Frozen forward migration file 0148 (the repository-wide file
-- sequence): Back to review undoes a Ministry request's Entered by hand
-- and Confirmed (#1840).
-- This file is installed by its Django migration in the workflows app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that each replaced baseline function's copy
-- here equals the fresh-install baseline's (schema/guards.sql,
-- schema/ministry_followup.sql).
--
-- No review pipeline stage is final (review pipeline spec, "Undoing a
-- terminal stage"). A Ministry request's terminal steps are the Entered in
-- ParishSoft tick (stewardship_ministry_roster_entry), a refresh check that
-- Confirmed it at its version (stewardship_refresh_check), and a refresh's
-- own resolution (resolution_source_id). Back to review undoes each with a
-- revision that carries its required note (decision_note):
--   * after a person's decision (ticked, or Confirmed by a check), the
--     revision keeps the request resolved with its outcome and Edit and
--     only advances its version, so a check made at the old version no
--     longer places it, and the tick, if set, is cleared in the same
--     transaction by a roster entry paired with that revision (the same
--     actor and request key): To enter by hand again;
--   * after a refresh's resolution (no decision behind it), the revision
--     sends it back to In progress and the request's resolution source is
--     cleared: To review (the next refresh may resolve it again).
-- Both only by a current Staff member or Administrator, never a Ministry
-- leader. No table, column or row changes. Replaced, each copied whole from
-- its latest body with only these changes:
--   stewardship_ministry_request_guard_v1       (0131's) a resolved request
--       may stay resolved with its outcome and Edit through Staff
--       follow-up's paired revision, keeping its resolved time; Staff
--       follow-up clears a refresh's resolution source (the web login has
--       no privilege on the column);
--   stewardship_ministry_revision_guard_v1      (0131's) admits the undo of
--       a tick or a Confirmed check (kept decision, a note, Staff only),
--       and Back to review of a refresh's resolution (a note, Staff only);
--       a ticked request, or one a check Confirmed after a person's
--       decision, is never reopened to In progress directly; a blank note
--       is one with no character other than white space;
--   stewardship_ministry_roster_entry_guard_v1  (0061's) clearing the tick
--       needs the undo's paired revision with its note.
-- None of them is SECURITY DEFINER, before or after, so there is no ALTER
-- FUNCTION to repeat; they keep their settings and grants, which the DO
-- block below compares with those recorded before the replacement, after
-- checking each new body. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0148_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_request_guard_v1','stewardship_ministry_revision_guard_v1',
        'stewardship_ministry_roster_entry_guard_v1')), true);

CREATE OR REPLACE FUNCTION public.stewardship_ministry_request_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    response public.stewardship_submission;
    later public.stewardship_submission;
    prior public.stewardship_ministry_request;
    successor public.stewardship_ministry_request;
    replaced public.stewardship_ministry_request;
    group_name text; expected_state text; same_intent boolean;
    staff_at timestamp with time zone;
BEGIN
    IF TG_OP='DELETE' THEN
        SELECT * INTO response FROM public.stewardship_submission WHERE id=OLD.submission_id;
        IF FOUND AND public.stewardship_test_response_cleanup_v1(response.mode,response.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Ministry history deletion requires its retention owner' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Ministry requests require ordered admission' USING ERRCODE='23514';
    END IF;
    IF current_user='pk_stewardship_worker' THEN
        SELECT id,family_id,campaign_id INTO response.id,response.family_id,response.campaign_id
            FROM public.stewardship_submission WHERE id=NEW.submission_id;
    ELSE
        SELECT * INTO response FROM public.stewardship_submission WHERE id=NEW.submission_id;
    END IF;
    IF response.id IS NULL THEN
        RAISE EXCEPTION 'Ministry request requires its immutable response' USING ERRCODE='23514';
    END IF;
    group_name := CASE WHEN NEW.entity_kind='member' THEN 'members' ELSE 'proposed_members' END;
    IF TG_OP='INSERT' AND NEW.parish_added THEN
        -- A request the parish added (#1684): a current Staff member or
        -- Administrator, on the Family's latest live response in a campaign
        -- that admits edits, for a Member that response lists and a Ministry
        -- the campaign asks about, open and unworked (its Accept follows as
        -- a revision), with no current request for that Member and
        -- Ministry. It may name the same Member's ignored request it was
        -- added in place of.
        IF current_user<>'pk_stewardship_web' OR response.mode<>'live'
           OR NOT coalesce((public.stewardship_ministry_scope_v1(NEW.actor_id)->'operational')
               ='true'::jsonb,false)
           OR NOT public.stewardship_export_admitted_v1(response.campaign_id,true)
           OR EXISTS (SELECT 1 FROM public.stewardship_submission newer
               WHERE newer.family_id=response.family_id AND newer.campaign_id=response.campaign_id
                 AND newer.mode='live' AND newer.family_version>response.family_version)
           OR NEW.entity_kind<>'member' OR NOT ((response.answers->'members') ? NEW.entity_key)
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign c
               JOIN public.stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
               WHERE c.id=response.campaign_id
                 AND cc.values->'ministry_duids' @> to_jsonb(NEW.ministry_duid))
           OR EXISTS (SELECT 1 FROM public.stewardship_ministry_request current_request
               JOIN public.stewardship_submission history ON history.id=current_request.submission_id
               WHERE history.family_id=response.family_id AND history.campaign_id=response.campaign_id
                 AND history.mode='live' AND current_request.entity_kind=NEW.entity_kind
                 AND current_request.entity_key=NEW.entity_key
                 -- A request holds its Family's Ministry and its Edit's:
                 -- Back to review can return it to the first.
                 AND NEW.ministry_duid IN (current_request.ministry_duid,
                     current_request.reviewed_ministry_duid)
                 AND current_request.state NOT IN ('cancelled','superseded'))
           OR NEW.version<>1 OR NEW.state<>'new' OR NEW.assignee_id IS NOT NULL
           OR NEW.superseded_by_id IS NOT NULL OR NEW.outcome IS NOT NULL
           OR NEW.resolved_at IS NOT NULL OR NEW.resolution_source_id IS NOT NULL
           OR NEW.reviewed_ministry_duid IS NOT NULL THEN
            RAISE EXCEPTION 'A request the parish adds needs Staff, a current response and a free Ministry'
                USING ERRCODE='23514';
        END IF;
        IF NEW.added_in_place_of_id IS NOT NULL THEN
            SELECT r.* INTO replaced FROM public.stewardship_ministry_request r
                JOIN public.stewardship_submission history ON history.id=r.submission_id
                WHERE r.id=NEW.added_in_place_of_id AND history.family_id=response.family_id
                  AND history.campaign_id=response.campaign_id AND history.mode='live';
            IF replaced.id IS NULL OR replaced.entity_kind<>NEW.entity_kind
               OR replaced.entity_key<>NEW.entity_key
               OR NOT (replaced.state='closed_no_response'
                   OR (replaced.state='resolved' AND replaced.outcome NOT IN ('joined','leave_confirmed'))) THEN
                RAISE EXCEPTION 'An added request replaces only the same Member''s ignored request'
                    USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM public.stewardship_family_form_baseline
                       WHERE id=response.baseline_id AND state='open')
           OR NEW.actor_id IS DISTINCT FROM response.family_id OR NEW.version<>1
           OR NOT coalesce(response.answers#>ARRAY['ministries',group_name,NEW.entity_key,NEW.action]
                           @> to_jsonb(NEW.ministry_duid),false)
           OR NEW.superseded_by_id IS NOT NULL OR NEW.outcome IS NOT NULL
           OR NEW.resolved_at IS NOT NULL OR NEW.resolution_source_id IS NOT NULL
           OR NEW.reviewed_ministry_duid IS NOT NULL OR NEW.added_in_place_of_id IS NOT NULL THEN
            RAISE EXCEPTION 'Ministry request must derive from an in-flight final answer' USING ERRCODE='23514';
        END IF;
        SELECT * INTO prior FROM public.stewardship_ministry_request WHERE id=
            public.stewardship_ministry_predecessor_v1(response,NEW.entity_kind,NEW.entity_key,NEW.ministry_duid);
        -- A Family cannot assign Staff work, and resubmitting the same intent
        -- must not discard it: the successor inherits state and assignee only.
        same_intent := coalesce(prior.state IN ('new','assigned','in_progress')
            AND prior.action=NEW.action,false);
        expected_state := CASE WHEN same_intent THEN prior.state ELSE 'new' END;
        IF NEW.state IS DISTINCT FROM expected_state OR NEW.assignee_id IS DISTINCT FROM
            (CASE WHEN same_intent THEN prior.assignee_id END) THEN
            RAISE EXCEPTION 'Ministry request must preserve same-intent workflow state' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    -- A closed request changes only by Staff follow-up sending it back to
    -- review (or withdrawing one the parish added); its revision guard
    -- proves which (#1684). The Edit's Ministry is part of the projection.
    -- Back to review of a terminal step (Entered in ParishSoft or a
    -- Confirmed refresh check, #1840) keeps the decision: the request stays
    -- resolved with its outcome and Edit, and only its version moves.
    IF (OLD.state NOT IN ('new','assigned','in_progress')
        AND NOT (current_user='pk_stewardship_web'
            AND OLD.state IN ('resolved','closed_no_response')
            AND (NEW.state='in_progress' OR (NEW.state='cancelled' AND OLD.parish_added)
                OR (OLD.state='resolved' AND NEW.state='resolved'
                    AND NEW.outcome IS NOT DISTINCT FROM OLD.outcome
                    AND NEW.reviewed_ministry_duid IS NOT DISTINCT FROM OLD.reviewed_ministry_duid))))
       OR NEW.version<>OLD.version+1
       OR (to_jsonb(NEW)-ARRAY['state','outcome','resolved_at','resolution_source_id','superseded_by_id','assignee_id','reviewed_ministry_duid','version','updated_at'])
          IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['state','outcome','resolved_at','resolution_source_id','superseded_by_id','assignee_id','reviewed_ministry_duid','version','updated_at']) THEN
        RAISE EXCEPTION 'Ministry provenance and closed outcomes are immutable' USING ERRCODE='23514';
    END IF;
    IF current_user='pk_stewardship_worker' THEN
        IF NEW.state<>'resolved' OR NEW.outcome IS DISTINCT FROM
            (CASE WHEN NEW.action='join' THEN 'joined' ELSE 'leave_confirmed' END)
           OR NEW.assignee_id IS DISTINCT FROM OLD.assignee_id
           OR NEW.reviewed_ministry_duid IS NOT NULL
           OR NEW.resolution_source_id IS NULL
           OR NOT public.stewardship_response_source_owner_v1(NEW.resolution_source_id)
           OR NEW.entity_kind<>'member'
           OR public.stewardship_response_ministry_current_v1(
                NEW.resolution_source_id,response.family_id,NEW.entity_key,NEW.ministry_duid)
              IS DISTINCT FROM (NEW.action='join')
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin
               WHERE snapshot_id=NEW.resolution_source_id AND parent_kind='submission'
                 AND parent_id=NEW.submission_id AND expires_at IS NULL) THEN
            RAISE EXCEPTION 'Ministry resolution requires fenced scoped roster evidence' USING ERRCODE='23514';
        END IF;
        NEW.resolved_at := clock_timestamp();
    ELSIF current_user='pk_stewardship_web' AND (NEW.state NOT IN ('superseded','cancelled')
        OR (NEW.state='cancelled' AND OLD.parish_added AND OLD.state='resolved')) THEN
        -- Staff follow-up. Its revision guard already proved current authority,
        -- Ministry scope, the work gate and this exact version; accept only the
        -- projection that history records. Roster evidence stays worker-owned.
        SELECT r.created_at INTO staff_at FROM public.stewardship_ministry_revision r
            WHERE r.request_id=NEW.id AND r.expected_version=OLD.version AND r.state=NEW.state
              AND r.outcome IS NOT DISTINCT FROM NEW.outcome
              AND r.assignee_id IS NOT DISTINCT FROM NEW.assignee_id
              AND r.reviewed_ministry_duid IS NOT DISTINCT FROM NEW.reviewed_ministry_duid;
        -- Staff follow-up never carries a refresh's resolution: Back to
        -- review of a request the refresh resolved, which its revision
        -- guard admits only with a note, clears it (#1840); the web login
        -- has no privilege on the column.
        NEW.resolution_source_id := NULL;
        IF NOT FOUND OR NEW.superseded_by_id IS NOT NULL THEN
            RAISE EXCEPTION 'Ministry follow-up projection requires its paired history' USING ERRCODE='23514';
        END IF;
        -- An undone terminal step keeps the decision's time (#1840).
        NEW.resolved_at := CASE WHEN OLD.state='resolved' AND NEW.state='resolved'
            THEN OLD.resolved_at WHEN NEW.outcome IS NOT NULL THEN staff_at END;
    ELSIF current_user='pk_stewardship_web' THEN
        SELECT candidate.* INTO later FROM public.stewardship_submission candidate
        JOIN public.stewardship_family_form_baseline baseline ON baseline.id=candidate.baseline_id
        WHERE baseline.state='open' AND candidate.family_id=response.family_id
          AND candidate.campaign_id=response.campaign_id AND candidate.mode=response.mode
          AND candidate.rehearsal_epoch_id IS NOT DISTINCT FROM response.rehearsal_epoch_id
          AND candidate.family_version>response.family_version
          AND OLD.id=public.stewardship_ministry_predecessor_v1(
              candidate,OLD.entity_kind,OLD.entity_key,OLD.ministry_duid);
        IF NOT FOUND OR NEW.assignee_id IS DISTINCT FROM OLD.assignee_id
           OR OLD.state NOT IN ('new','assigned','in_progress')
           OR NEW.reviewed_ministry_duid IS NOT NULL
           OR NOT public.stewardship_response_ministry_visible_v1(
               later.validation_source_id,later.configuration_id,later.campaign_id,NEW.ministry_duid)
           OR (NEW.entity_kind='member' AND NOT ((later.answers->'members') ? NEW.entity_key))
           OR (NEW.entity_kind='proposed_member' AND NOT
               public.stewardship_ministry_proposed_scope_v1(later,NEW.entity_key)) THEN
            RAISE EXCEPTION 'Ministry replacement requires an informed new final response' USING ERRCODE='23514';
        END IF;
        IF NEW.state='superseded' THEN
            SELECT * INTO successor FROM public.stewardship_ministry_request WHERE id=NEW.superseded_by_id;
            IF NOT FOUND OR successor.submission_id<>later.id
               OR ROW(successor.entity_kind,successor.entity_key,successor.ministry_duid)
                  IS DISTINCT FROM ROW(NEW.entity_kind,NEW.entity_key,NEW.ministry_duid) THEN
                RAISE EXCEPTION 'Ministry successor must have the exact new response identity' USING ERRCODE='23514';
            END IF;
        ELSIF coalesce(later.answers#>ARRAY['ministries',group_name,NEW.entity_key,'join'] @> to_jsonb(NEW.ministry_duid),false)
           OR coalesce(later.answers#>ARRAY['ministries',group_name,NEW.entity_key,'leave'] @> to_jsonb(NEW.ministry_duid),false) THEN
            RAISE EXCEPTION 'Selected Ministry intent cannot be cancelled' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

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
       -- undo below, keeping the decision), with a note when it reopens an
       -- ignored request, and by Staff with a note when it undoes a
       -- refresh's resolution (#1840). A request the parish added is
       -- withdrawn (cancelled) instead.
       OR (reopen AND NOT undo
           AND (NEW.state<>CASE WHEN target.parish_added THEN 'cancelled' ELSE 'in_progress' END
           OR NEW.outcome IS NOT NULL OR NEW.reviewed_ministry_duid IS NOT NULL
           OR NEW.contact_channel IS NOT NULL OR coalesce(ticked,false)
           OR (confirmed AND target.resolution_source_id IS NULL)
           OR (target.resolution_source_id IS NOT NULL
               AND (NEW.decision_note !~ '\S' OR NOT operational))
           OR (target.outcome NOT IN ('joined','leave_confirmed') AND NEW.decision_note !~ '\S')))
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

CREATE OR REPLACE FUNCTION public.stewardship_ministry_roster_entry_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target public.stewardship_ministry_request%ROWTYPE; campaign uuid;
        latest public.stewardship_ministry_roster_entry%ROWTYPE;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Roster entry history is immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO target FROM public.stewardship_ministry_request
        WHERE id=NEW.request_id FOR NO KEY UPDATE;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=target.submission_id AND mode='live';
    SELECT * INTO latest FROM public.stewardship_ministry_roster_entry
        WHERE request_id=NEW.request_id ORDER BY sequence DESC LIMIT 1;
    IF target.id IS NULL OR campaign IS NULL
       OR target.state<>'resolved'
       OR target.outcome NOT IN ('joined','leave_confirmed')
       OR target.outcome IS NULL
       OR target.resolution_source_id IS NOT NULL
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.sequence IS DISTINCT FROM coalesce(latest.sequence,0)+1
       OR NEW.sequence>=9223372036854775807
       OR NEW.entered IS NOT DISTINCT FROM coalesce(latest.entered,false)
    THEN RAISE EXCEPTION 'Roster entry lacks current authority, request or sequence'
        USING ERRCODE='23514'; END IF;
    -- Clearing the tick is Back to review of Entered in ParishSoft (#1840):
    -- only with that undo's revision, just recorded on this request by the
    -- same actor and request key, which kept the decision and has its note.
    -- (The other way round, an undo revision of a ticked request without
    -- this clearing, is not refused here: the revision guard cannot see a
    -- row written after it, and a deferred check would be another trigger
    -- for every closure to track. workflows/review.py records both in one
    -- transaction; a revision alone would only advance the version.)
    IF NOT NEW.entered AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_revision r
           WHERE r.request_id=target.id AND r.actor_id=NEW.actor_id
             AND r.request_key=NEW.request_key AND r.expected_version=target.version-1
             AND r.state='resolved' AND r.outcome=target.outcome
             AND r.decision_note ~ '\S')
    THEN RAISE EXCEPTION 'Clearing Entered in ParishSoft needs its Back to review with a note'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    -- Each replaced function, once, invoker, with its new text.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND ((p.proname='stewardship_ministry_request_guard_v1'
                AND p.prosrc LIKE '%OR (OLD.state=''resolved'' AND NEW.state=''resolved''%'
                AND p.prosrc LIKE '%THEN OLD.resolved_at WHEN NEW.outcome IS NOT NULL THEN staff_at END;%'
                AND p.prosrc LIKE '%NEW.resolution_source_id := NULL;%')
            OR (p.proname='stewardship_ministry_revision_guard_v1'
                AND p.prosrc LIKE '%undo := reopen AND NEW.state=''resolved'';%'
                AND p.prosrc LIKE '%OR NOT (coalesce(ticked,false) OR confirmed)%'
                AND p.prosrc LIKE '%OR (confirmed AND target.resolution_source_id IS NULL)%'
                AND p.prosrc LIKE '%AND (NEW.decision_note !~ ''\\S'' OR NOT operational))%')
            OR (p.proname='stewardship_ministry_roster_entry_guard_v1'
                AND p.prosrc LIKE '%Clearing Entered in ParishSoft needs its Back to review with a note%'
                AND p.prosrc LIKE '%AND r.decision_note ~ ''\\S'')%'
                AND p.prosrc LIKE '%FOR NO KEY UPDATE%')))<>3 THEN
        RAISE EXCEPTION 'Migration 0148 (Ministry terminal undo) did not replace the guards';
    END IF;
    -- The guards still run as the triggers they were.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE NOT t.tgisinternal AND t.tgenabled<>'D'
          AND (t.tgrelid,t.tgname,p.proname) IN (
            ('public.stewardship_ministry_request'::regclass,'stewardship_ministry_request_guard',
                'stewardship_ministry_request_guard_v1'),
            ('public.stewardship_ministry_revision'::regclass,'stewardship_ministry_revision_guard',
                'stewardship_ministry_revision_guard_v1'),
            ('public.stewardship_ministry_roster_entry'::regclass,'stewardship_ministry_roster_entry_guard',
                'stewardship_ministry_roster_entry_guard_v1')))<>3 THEN
        RAISE EXCEPTION 'Migration 0148 (Ministry terminal undo) lost a Ministry trigger';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_request_guard_v1','stewardship_ministry_revision_guard_v1',
        'stewardship_ministry_roster_entry_guard_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0148_acl') THEN
        RAISE EXCEPTION 'Migration 0148 changed the Ministry functions'' grants or settings';
    END IF;
END
$check$;
