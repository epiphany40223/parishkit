-- Frozen forward migration file 0217 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): Ministry
-- resubmission of a reviewed Family form (#1705; review-pipeline spec,
-- "Family resubmission" and "What the reviewed form shows"). This file is
-- installed by its Django migration in the workflows app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that each replaced function's copy here equals
-- the fresh-install baseline's (schema/guards.sql).
--
-- A reviewed form shows each Ministry request in force with its action on
-- the Ministry it carries forward (the Family's, an Edit's, the parish's),
-- an Ignored one as not chosen, and records them in ``shown_items`` (0143).
-- A request left as shown keeps its stage and decision with no new row
-- (rule 1); an Ignored one chosen again, or the Family's own Ministry chosen
-- while an Edit into another is in force, is replaced by a new request To
-- review (rule 2); a chosen request unticked is withdrawn (rule 3), except
-- one the parish added, a refresh resolved or Staff ticked Entered in
-- ParishSoft. So this file replaces two functions, each copied whole from
-- its latest body (0131's completeness check; 0212's request guard, with
-- #1840's terminal undo) with only these changes:
--   stewardship_ministry_submission_effects_v1
--       * a choice with no request of the response is admitted when its
--         reviewed form showed a request in force, on an earlier response of
--         the Family, with this action on this Ministry (its own, or the one
--         its Edit revision recorded);
--       * an open earlier request is admitted unreplaced when the reviewed
--         form showed it chosen and the response still chooses it.
--   stewardship_ministry_request_guard_v1
--       * a Family response drawn as the reviewed form may supersede or
--         cancel a decided request (resolved or closed with no response)
--         its shown items name, never one the parish added, a refresh
--         resolved or Staff ticked Entered in ParishSoft (no runtime login
--         but the web one may make that move); the replaced request keeps
--         no outcome, and the guard clears its resolved_at.
-- A response not drawn as the reviewed form has reviewed_view false, so
-- for it both bodies behave exactly as before. Neither function is SECURITY
-- DEFINER, before or after, so there is no ALTER FUNCTION to repeat. No
-- table, column, index, grant or credential changes (the web login already
-- updates a request's state, outcome, successor and Edit); no Family code,
-- link or session is touched, and no row is updated. The DO block at the
-- end refuses to commit unless both new bodies are installed with their
-- rules, attributes and triggers. No temporary objects. Reversing needs
-- its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

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
    -- A Family response drawn as the reviewed form (#1705) may also replace
    -- or withdraw a decided request it showed (reviewed rules 2 and 3); the
    -- Family branch below checks that response.
    IF (OLD.state NOT IN ('new','assigned','in_progress')
        AND NOT (current_user='pk_stewardship_web'
            AND OLD.state IN ('resolved','closed_no_response')
            AND (NEW.state='in_progress' OR (NEW.state='cancelled' AND OLD.parish_added)
                OR (OLD.state='resolved' AND NEW.state='resolved'
                    AND NEW.outcome IS NOT DISTINCT FROM OLD.outcome
                    AND NEW.reviewed_ministry_duid IS NOT DISTINCT FROM OLD.reviewed_ministry_duid)))
        -- Only the web login (the Family branch below checks its response)
        -- or a login that is no runtime login at all (the schema owner).
        AND NOT ((current_user='pk_stewardship_web'
                  OR current_user NOT LIKE 'pk\_stewardship\_%')
            AND OLD.state IN ('resolved','closed_no_response')
            AND NEW.state IN ('superseded','cancelled') AND NOT OLD.parish_added))
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
        -- A decided request is replaced or withdrawn only by a response
        -- drawn as the reviewed form that showed it (#1705): never one the
        -- parish added, a refresh resolved or Staff ticked Entered in
        -- ParishSoft, and its outcome is cleared as the table requires.
        IF NOT FOUND OR NEW.assignee_id IS DISTINCT FROM OLD.assignee_id
           OR (OLD.state NOT IN ('new','assigned','in_progress')
               AND NOT (OLD.state IN ('resolved','closed_no_response')
                   AND later.reviewed_view AND NOT OLD.parish_added
                   AND OLD.resolution_source_id IS NULL
                   AND EXISTS (SELECT 1 FROM jsonb_array_elements(later.shown_items) shown
                       WHERE shown->>'stream'='ministry' AND shown->>'item'=OLD.id::text)
                   AND coalesce((SELECT tick.entered FROM public.stewardship_ministry_roster_entry tick
                       WHERE tick.request_id=OLD.id ORDER BY tick.sequence DESC LIMIT 1),false)=false))
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
        -- A decided request the Family replaced or withdrew keeps no outcome
        -- (#1705); its revisions keep the decision's history.
        IF NEW.outcome IS NOT NULL THEN
            RAISE EXCEPTION 'A replaced Ministry request keeps no outcome' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.state IN ('superseded','cancelled') THEN
        NEW.resolved_at := NULL;
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

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
           -- An open request a reviewed form (#1705) showed chosen, left
           -- chosen: it stays as it is (reviewed rule 1).
           AND NOT (NEW.reviewed_view AND EXISTS (
               SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
               WHERE shown->>'stream'='ministry' AND shown->>'item'=prior.id::text
                 AND shown->>'shown'='family'
                 AND coalesce(NEW.answers#>ARRAY['ministries',
                     CASE WHEN prior.entity_kind='member' THEN 'members' ELSE 'proposed_members' END,
                     prior.entity_key,prior.action] @> to_jsonb(prior.ministry_duid),false)))
           AND public.stewardship_response_ministry_visible_v1(
               NEW.validation_source_id,NEW.configuration_id,NEW.campaign_id,prior.ministry_duid)
           AND ((prior.entity_kind='member' AND (NEW.answers->'members') ? prior.entity_key)
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
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_ministry_request_guard_v1()'::regprocedure
              AND prosrc LIKE '%AND NEW.state IN (''superseded'',''cancelled'') AND NOT OLD.parish_added))%'
              AND prosrc LIKE '%OR current_user NOT LIKE %'
              AND prosrc LIKE '%AND later.reviewed_view AND NOT OLD.parish_added%'
              AND prosrc LIKE '%WHERE shown->>''stream''=''ministry'' AND shown->>''item''=OLD.id::text)%'
              AND prosrc LIKE '%A replaced Ministry request keeps no outcome%'
              -- 0212's terminal undo (#1840), kept.
              AND prosrc LIKE '%OR (OLD.state=''resolved'' AND NEW.state=''resolved''%'
              AND prosrc LIKE '%THEN OLD.resolved_at WHEN NEW.outcome IS NOT NULL THEN staff_at END;%'
              AND prosrc LIKE '%NEW.resolution_source_id := NULL;%'
              -- 0131's rules, kept.
              AND prosrc LIKE '%A request the parish adds needs Staff, a current response and a free Ministry%'
              AND prosrc LIKE '%Ministry follow-up projection requires its paired history%'
              AND prosrc LIKE '%Selected Ministry intent cannot be cancelled%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_ministry_request'::regclass
              AND t.tgname='stewardship_ministry_request_guard'
              AND t.tgfoid='public.stewardship_ministry_request_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_ministry_request_guard_v1 lacks the reviewed form rules';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_ministry_submission_effects_v1()'::regprocedure
              AND prosrc LIKE '%WHERE shown->>''stream''=''ministry'' AND shown->>''shown'' IN (''family'',''reviewer'')%'
              AND prosrc LIKE '%WHERE shown->>''stream''=''ministry'' AND shown->>''item''=prior.id::text%'
              -- 0131's rule, kept.
              AND prosrc LIKE '%AND decided.state IN (''resolved'',''closed_no_response'')%'
              AND prosrc LIKE '%Submitted Ministry choice requires atomic derived work%'
              AND prosrc LIKE '%New informed response must reconcile earlier Ministry work%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_submission'::regclass
              AND t.tgname='stewardship_ministry_submission_effects'
              AND t.tgfoid='public.stewardship_ministry_submission_effects_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_ministry_submission_effects_v1 lacks the reviewed form rules';
    END IF;
END $check$;
