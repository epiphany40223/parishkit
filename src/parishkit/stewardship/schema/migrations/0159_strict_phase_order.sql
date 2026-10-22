-- Frozen forward migration file 0159 (the repository-wide file
-- sequence): strict
-- phase order in the review pipeline (#1890). This file is installed by its
-- Django migration in the responses app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that each replaced baseline function's copy here
-- equals the fresh-install baseline's (schema/guards.sql).
--
-- The Administrator (2026-10-21) requires every item to go Submitted ->
-- Reviewed -> (Updated | Entered by hand | Handled) -> Confirmed in order
-- (docs/specs/stewardship/review-pipeline/spec.md, "Baseline refresh
-- re-evaluation" rule 1 and decision 2, reversed): a refresh confirms only
-- an item already Updated in ParishSoft, Entered by hand or Entered
-- incorrectly, and Entered by hand follows an Accept. Replaced, each copied
-- whole from its latest body with these changes and only these:
--   stewardship_proposal_resolution_guard_v1  (0158's, #1889: undoing an
--       Ignore takes an optional note) an 'entered'
--       resolution needs an approved, pending change: the one-step mark of
--       a To review By hand change (decision 2, which 0128 widened the
--       review for) and a mark on a Conflict are refused.
--   stewardship_response_derived_guard_v1     (0153's, #1705's reviewed
--       view rules; guards.sql has the same body) reconciliation may
--       resolve a change upstream only from queued, so an open change
--       ParishSoft already holds stays pending (To review, Ready, To enter
--       by hand), and an approval taken over a Conflict keeps its stage
--       then instead of going back to To review (0108's reset of a stale
--       accepted base: nothing is left to write); and the publication
--       check marks an already-held change published (Updated in
--       ParishSoft, nothing written), never resolved_upstream.
--   stewardship_publication_confirmation_guard_v1 (0124's) a field row the
--       check marked is published at the next version (resolved_upstream
--       still passes, for a plan checked before this file).
--   stewardship_ministry_request_guard_v1     (0154's, #1705's reviewed
--       view rules; guards.sql has the same body) the source worker may no
--       longer resolve a request (its
--       only change there): a refresh never resolves an open request.
--   stewardship_refresh_check_guard_v1        (0138's) a Ministry request's
--       confirmed check needs its latest roster entry ticked.
--   stewardship_pledge_stages_v1              (0136's) a confirmed check
--       places only a marked pledge as Confirmed; a To review or To enter
--       by hand pledge keeps its stage (also at the submit, rules 3 and 4),
--       with the check still reported in check_outcome for the page; an
--       Accept or Edit carries the pledge's latest such check to its new
--       row until a refresh checks that row.
--   stewardship_pledge_refresh_check_guard_v1 (0135's) only a marked
--       pledge's confirmed check stops further checks, so an accepted one
--       not yet marked is checked again.
-- Nothing already stored is rewritten (no table, column, constraint, index
-- or row changes): items a refresh or a check confirmed earlier, and
-- changes marked in one step, keep their history. None of the functions is
-- SECURITY DEFINER, before or after, so there is no ALTER FUNCTION to
-- repeat; CREATE OR REPLACE keeps their owners, grants and settings, which
-- the DO block below compares with those recorded before the replacement,
-- after checking each new body. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants, settings and definer flags before the
-- replacement (with how many definitions there are), compared in the DO
-- block. A transaction-local setting holds them: the migration login has no
-- TEMP privilege.
SELECT set_config('stewardship.migration_0159_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||p.prosecdef::text
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_proposal_resolution_guard_v1',
        'stewardship_response_derived_guard_v1','stewardship_publication_confirmation_guard_v1',
        'stewardship_ministry_request_guard_v1','stewardship_refresh_check_guard_v1',
        'stewardship_pledge_stages_v1','stewardship_pledge_refresh_check_guard_v1')), true);

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
       -- Strict phase order (#1890, decision 2 reversed): Entered by hand
       -- follows an Accept, so only an approved change out of conflict is
       -- marked; a To review or Conflict change is accepted first.
       OR (open_state AND NEW.action='entered'
           AND (proposal.decision<>'approved' OR proposal.execution<>'pending'))
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

CREATE OR REPLACE FUNCTION public.stewardship_response_derived_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    response public.stewardship_submission;
    predecessor public.stewardship_proposed_change;
    source_field jsonb;
    expected_baseline jsonb;
    carries_intent boolean;
    submitted_key text;
    baseline_key text;
    current_key text;
    expected_execution text;
    expected_submitted jsonb;
    expected_handling text;
    active_base boolean;
BEGIN
    IF TG_OP='DELETE' THEN
        SELECT * INTO response FROM public.stewardship_submission WHERE id=OLD.submission_id;
        IF TG_TABLE_NAME='stewardship_submission_receipt'
           AND to_jsonb(OLD)->>'outbox_id' IS NOT NULL
           AND NOT public.stewardship_cleanup_effect_v1('submission_receipts',OLD.id)
        THEN RAISE EXCEPTION 'Receipt deliveries require journaled cleanup' USING ERRCODE='23514'; END IF;
        IF FOUND AND public.stewardship_test_response_cleanup_v1(response.mode,response.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Response detail deletion requires its retention owner' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Response detail requires ordered admission' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME <> 'stewardship_proposed_change' OR TG_OP='INSERT' THEN
        SELECT * INTO response FROM public.stewardship_submission WHERE id=NEW.submission_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Response detail has no immutable parent' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_TABLE_NAME='stewardship_submission_receipt' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Receipt intent is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='stewardship_proposed_change' THEN
        IF TG_OP='INSERT' THEN
            IF NEW.entity_kind='member' AND NEW.field IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname','maiden_name',
                'birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language',
                'moved_household','deceased_status','death_date')
               AND (response.answers->'members') ? NEW.entity_key
               AND (response.answers#>ARRAY['members',NEW.entity_key]) ? NEW.field THEN
                expected_submitted := response.answers#>ARRAY['members',NEW.entity_key,NEW.field];
                IF NEW.field='death_date' AND expected_submitted='null'::jsonb THEN
                    RAISE EXCEPTION 'Omitted terminal date cannot request source clearing' USING ERRCODE='23514';
                END IF;
                -- The live ParishSoft API test (#1596): a death date leaves
                -- the Member Active, so it is By hand with the deceased
                -- status, and no value can be cleared, so an answer that
                -- removes one (JSON null or "") is By hand too. The live
                -- gender test (#1603): a gender PUT is accepted but never
                -- changes the Member, so gender is By hand.
                expected_handling := CASE WHEN NEW.field IN ('prefix','suffix','gender','marital_status','moved_household','deceased_status','death_date')
                    OR expected_submitted IN ('null'::jsonb,'""'::jsonb)
                    THEN 'manual' ELSE 'api' END;
                source_field := public.stewardship_response_field_source_v1(
                    response.validation_source_id,response.family_id,NEW.entity_key,NEW.field);
            ELSIF NEW.entity_kind='proposed_member' AND NEW.field='new_member'
                  AND (response.answers->'proposed_members') ? NEW.entity_key THEN
                expected_submitted := response.answers#>ARRAY['proposed_members',NEW.entity_key];
                expected_handling := 'manual';
                source_field := jsonb_build_object('available',false,'value',NULL);
            ELSIF NEW.entity_kind='family' AND NEW.field IN ('home_address','mailing_address','email_opt_out') THEN
                expected_submitted := response.answers#>ARRAY['family',NEW.field];
                -- Family addresses cannot be read back from ParishSoft, so
                -- a write could not be confirmed: every Family field is By
                -- hand (#1596).
                expected_handling := 'manual';
                source_field := public.stewardship_response_household_source_v1(
                    response.validation_source_id,response.family_id,NEW.entity_key,NEW.field);
            ELSE
                RAISE EXCEPTION 'Proposal field has no admitted response owner' USING ERRCODE='23514';
            END IF;
            IF coalesce(NEW.submitted_value,'null'::jsonb) IS DISTINCT FROM expected_submitted
                OR NEW.actor_id IS DISTINCT FROM response.family_id
                OR NEW.current_source_id IS DISTINCT FROM response.validation_source_id
                OR NEW.handling IS DISTINCT FROM expected_handling THEN
                RAISE EXCEPTION 'Proposal differs from its immutable response' USING ERRCODE='23514';
            END IF;
            IF NEW.execution NOT IN ('pending','conflict') OR NEW.superseded_by_id IS NOT NULL THEN
                RAISE EXCEPTION 'New proposal cannot mint execution outcomes' USING ERRCODE='23514';
            END IF;
            IF source_field IS NULL OR source_field IS DISTINCT FROM
                jsonb_build_object('available',NEW.current_available,'value',NEW.current_value)
            THEN
                RAISE EXCEPTION 'Proposal comparison differs from its validation source' USING ERRCODE='23514';
            END IF;
            -- Terminal/date work may survive an intervening response that has
            -- no active source Member to display. Mirror the Family namespace
            -- and latest-record selection used by effective.proposal_index.
            SELECT candidate.* INTO predecessor FROM public.stewardship_proposed_change candidate
            JOIN public.stewardship_submission earlier ON earlier.id=candidate.submission_id
            JOIN public.stewardship_submission prior ON prior.id=response.prior_submission_id
            WHERE earlier.family_id=response.family_id AND earlier.campaign_id=response.campaign_id
              AND earlier.mode=response.mode
              AND earlier.rehearsal_epoch_id IS NOT DISTINCT FROM response.rehearsal_epoch_id
              AND earlier.family_version<=prior.family_version
              AND (earlier.id=public.stewardship_response_census_anchor_v1(prior.id) OR (NEW.entity_kind='member'
                   AND NEW.field IN ('moved_household','deceased_status','death_date')))
              AND ROW(candidate.entity_kind,candidate.entity_key,candidate.field)=ROW(NEW.entity_kind,NEW.entity_key,NEW.field)
            ORDER BY earlier.family_version DESC LIMIT 1;
            -- A response drawn as the reviewed form (#1705) never carries a
            -- decision: a new row there is an item the Family changed again
            -- or a new one, To review against the baseline at this submit.
            carries_intent := FOUND AND NOT response.reviewed_view
                AND predecessor.execution NOT IN ('published','resolved_upstream','resolved_external','cancelled','superseded') AND (
                public.stewardship_response_comparison_v1(NEW.field,NEW.submitted_value)
                IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(NEW.field,predecessor.submitted_value));
            expected_baseline := source_field;
            IF carries_intent THEN
                expected_baseline := jsonb_build_object('available',predecessor.baseline_available,'value',predecessor.baseline_value);
                -- An approval resting on an Administrator's accepted
                -- ParishSoft value (#528, ADM-09.03) is not carried, as the
                -- accepted value itself is not: the new row is compared with
                -- the Family's baseline again and goes back to To review.
                active_base := EXISTS (SELECT 1 FROM public.stewardship_proposal_review base
                    WHERE base.proposal_id=predecessor.id AND base.accepted_base
                      AND base.accepted_source_id=predecessor.current_source_id
                      AND NOT EXISTS (SELECT 1 FROM public.stewardship_proposal_review later
                          WHERE later.proposal_id=base.proposal_id
                            AND later.expected_version>base.expected_version
                            AND later.expected_version<predecessor.version
                            AND later.decision<>'approved'));
                IF ROW(NEW.decision,NEW.admin_value_set,NEW.admin_value)
                    IS DISTINCT FROM ROW(CASE WHEN active_base AND predecessor.decision='approved'
                        THEN 'unreviewed' ELSE predecessor.decision END,
                        predecessor.admin_value_set,predecessor.admin_value) THEN
                    RAISE EXCEPTION 'Proposal review state requires the same prior intent' USING ERRCODE='23514';
                END IF;
            ELSIF NEW.decision <> 'unreviewed' OR NEW.admin_value_set OR NEW.admin_value IS NOT NULL THEN
                RAISE EXCEPTION 'New proposal review state must be unreviewed' USING ERRCODE='23514';
            END IF;
            IF expected_baseline IS DISTINCT FROM jsonb_build_object('available',NEW.baseline_available,'value',NEW.baseline_value) THEN
                RAISE EXCEPTION 'Proposal baseline requires exact source or prior intent' USING ERRCODE='23514';
            END IF;
            submitted_key := public.stewardship_response_comparison_v1(NEW.field,NEW.submitted_value);
            baseline_key := public.stewardship_response_comparison_v1(NEW.field,NEW.baseline_value);
            current_key := public.stewardship_response_comparison_v1(NEW.field,NEW.current_value);
            IF (NEW.current_available AND submitted_key IS NOT DISTINCT FROM current_key)
               OR (NOT NEW.current_available AND submitted_key IS NULL)
               OR (NEW.baseline_available AND submitted_key IS NOT DISTINCT FROM baseline_key)
            THEN
                RAISE EXCEPTION 'Unchanged values cannot create actionable proposals' USING ERRCODE='23514';
            END IF;
            expected_execution := CASE
                WHEN NEW.current_available AND (NOT NEW.baseline_available OR current_key IS DISTINCT FROM baseline_key)
                THEN 'conflict' ELSE 'pending' END;
            IF NEW.execution <> expected_execution THEN
                RAISE EXCEPTION 'Proposal execution differs from its derived merge state' USING ERRCODE='23514';
            END IF;
        ELSE
            -- A final change never changes, except a change Entered by hand
            -- that the web login marks again over Entered incorrectly
            -- (#1737); a change Entered by hand, Updated in ParishSoft or
            -- Already in ParishSoft whose terminal step the web login undoes
            -- with Back to review (#1840): the web branch below admits each
            -- only with its paired resolution row, whose own guard checks the
            -- actor, the state and the note; and an Updated in ParishSoft
            -- change a refresh finds ParishSoft no longer holds (#1749),
            -- which moves to conflict only with its paired refresh check
            -- (below), never through the web login.
            IF (OLD.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                    AND NOT (OLD.execution='resolved_external' AND current_user='pk_stewardship_web')
                    AND NOT (OLD.execution IN ('published','resolved_upstream')
                        AND current_user='pk_stewardship_web')
                    AND NOT (OLD.execution='published' AND NEW.execution='conflict'
                        AND current_user<>'pk_stewardship_web'))
                OR (to_jsonb(NEW)-ARRAY['current_available','current_value','current_source_id','admin_value_set','admin_value','decision','execution','superseded_by_id','version','updated_at'])
                    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['current_available','current_value','current_source_id','admin_value_set','admin_value','decision','execution','superseded_by_id','version','updated_at'])
            THEN
                RAISE EXCEPTION 'Proposal history and terminal outcomes are immutable' USING ERRCODE='23514';
            END IF;
            -- An Updated in ParishSoft change becomes a Conflict (#1749) only
            -- with the refresh check the promotion recorded for it at its
            -- version, whose own guard checked ParishSoft's value against
            -- the snapshot and the change: the change takes that value and
            -- snapshot. The worker's reconciliation branch below then
            -- requires the fenced, pinned source and returns the decision
            -- to review. The web login's undo (#1840) is checked by its own
            -- branch below instead.
            IF OLD.execution='published' AND current_user<>'pk_stewardship_web' AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_refresh_check k
                WHERE k.proposal_id=OLD.id AND k.item_version=OLD.version
                  AND k.outcome='conflict' AND k.snapshot_id=NEW.current_source_id
                  AND NEW.current_available
                  AND k.observed=jsonb_build_object('available',true,'value',NEW.current_value))
            THEN
                RAISE EXCEPTION 'An updated change becomes a Conflict only with its refresh check' USING ERRCODE='23514';
            END IF;
            IF current_user='pk_stewardship_worker'
               AND NEW.current_source_id IS NOT DISTINCT FROM OLD.current_source_id THEN
                -- ParishSoft publication (#528, BG-09): with the source
                -- reference unchanged (reconciliation always changes it),
                -- the worker changes only the execution, and only as the
                -- confirmed plan and its outcomes say. It queues an approved
                -- automatic Member change at the version its plan recorded,
                -- in a confirmed Production plan whose entity is unfinished,
                -- while the system is in Production mode and the live
                -- publication lease's task is running; it then publishes it
                -- when the entity's latest outcome is published, or returns
                -- it to pending or failed when that outcome is invalidated.
                -- The publication check also marks it Updated in ParishSoft
                -- (published, nothing written; #1890) when its plan, recorded
                -- in this transaction, found the value already in
                -- ParishSoft. It queues a change only while the
                -- current data lists its Member as an active, living Member
                -- of the change's Family (#1738, #1749), as preflight and the
                -- worker check before they read or write.
                IF ROW(NEW.current_available,NEW.current_value,NEW.admin_value_set,
                       NEW.admin_value,NEW.decision,NEW.superseded_by_id)
                   IS DISTINCT FROM
                   ROW(OLD.current_available,OLD.current_value,OLD.admin_value_set,
                       OLD.admin_value,OLD.decision,OLD.superseded_by_id)
                   OR NOT (
                   (OLD.execution IN ('pending','failed') AND NEW.execution='queued'
                       AND OLD.decision='approved' AND OLD.handling='api' AND OLD.entity_kind='member'
                       AND public.stewardship_member_source_status_v1(
                           (SELECT current_source.snapshot_id
                               FROM public.stewardship_source_current current_source
                               WHERE current_source.singleton),
                           OLD.entity_key,
                           (SELECT listed_family.family_duid
                               FROM public.stewardship_submission listed_response
                               JOIN public.stewardship_family_campaign listed_family
                                   ON listed_family.id=listed_response.family_id
                               WHERE listed_response.id=OLD.submission_id))='current'
                       AND EXISTS (SELECT 1 FROM public.stewardship_publication_field f
                           JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
                           JOIN public.stewardship_publication_plan p ON p.id=e.plan_id
                           JOIN public.stewardship_publication_confirmation c ON c.plan_id=p.id
                           WHERE f.proposal_id=OLD.id AND f.proposal_version=OLD.version AND f.outcome='write'
                             AND e.planned_sha256 IS NOT NULL AND NOT p.testing
                             AND NOT EXISTS (SELECT 1 FROM public.stewardship_publication_outcome o
                                 WHERE o.entity_id=e.id AND o.result IN ('published','invalidated')))
                       AND EXISTS (SELECT 1 FROM public.stewardship_system_configuration
                           WHERE mode='production')
                       AND EXISTS (SELECT 1 FROM public.stewardship_source_lease l
                           JOIN public.stewardship_task_run t ON t.id=l.owner_id
                           WHERE l.singleton AND l.phase='publication'
                             AND l.expires_at>clock_timestamp() AND t.state='running'
                             AND t.fence=l.task_fence AND t.worker_id=l.worker_id
                             AND t.lease_expires_at>clock_timestamp()))
                   OR (OLD.execution='queued' AND NEW.execution IN ('published','pending','failed')
                       -- Only the plan that queued it settles it: the latest
                       -- confirmed plan writing it at that version, since an
                       -- earlier one (invalidated without queueing) had to
                       -- finish before a later one could be confirmed.
                       AND (SELECT latest.result FROM public.stewardship_publication_field f
                           JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
                           JOIN public.stewardship_publication_confirmation c ON c.plan_id=e.plan_id
                           LEFT JOIN LATERAL (SELECT o.result FROM public.stewardship_publication_outcome o
                               WHERE o.entity_id=f.entity_id ORDER BY o.attempt DESC LIMIT 1) latest ON true
                           WHERE f.proposal_id=OLD.id AND f.proposal_version=OLD.version-1
                             AND f.outcome='write'
                           ORDER BY c.created_at DESC, c.id DESC LIMIT 1)
                       IS NOT DISTINCT FROM CASE WHEN NEW.execution='published'
                           THEN 'published' ELSE 'invalidated' END)
                   -- The publication check (#528, ADM-09): a Production-mode
                   -- plan recorded in this same transaction found the value
                   -- already in ParishSoft, so the change is Updated in
                   -- ParishSoft (published) at the next version and nothing
                   -- is written; the refresh then confirms it after that step,
                   -- as every update (#1890, strict phase order; it was
                   -- resolved upstream, Confirmed at once, before). The plan
                   -- guard stamps created_at with transaction_timestamp(), so a
                   -- plan committed earlier never matches; published is
                   -- terminal: only the web login's Back to review (#1840,
                   -- with its resolution row) undoes the mark.
                   OR (OLD.execution IN ('pending','failed') AND NEW.execution='published'
                       AND OLD.decision='approved' AND OLD.handling='api' AND OLD.entity_kind='member'
                       AND EXISTS (SELECT 1 FROM public.stewardship_publication_field f
                           JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
                           JOIN public.stewardship_publication_plan p ON p.id=e.plan_id
                           WHERE f.proposal_id=OLD.id AND f.proposal_version=OLD.version
                             AND f.outcome='already_in_parishsoft'
                             AND NOT p.testing AND p.created_at=transaction_timestamp())))
                THEN
                    RAISE EXCEPTION 'Proposal publication requires its confirmed plan and outcome' USING ERRCODE='23514';
                END IF;
            ELSIF current_user='pk_stewardship_worker' THEN
                -- Strict phase order (#1890): a refresh confirms a change only
                -- after its step, so reconciliation resolves upstream only a
                -- change an update run has queued; an open change ParishSoft
                -- already holds keeps its stage (To review, Ready, To enter by
                -- hand) with ParishSoft's value stored on it.
                IF NEW.execution NOT IN ('pending','conflict','resolved_upstream','cancelled')
                   OR (NEW.execution='resolved_upstream' AND OLD.execution<>'queued')
                   OR NEW.current_source_id=OLD.current_source_id
                   OR NOT public.stewardship_response_source_owner_v1(NEW.current_source_id)
                   OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin
                       WHERE snapshot_id=NEW.current_source_id AND parent_kind='submission'
                         AND parent_id=NEW.submission_id AND expires_at IS NULL)
                THEN
                    RAISE EXCEPTION 'Proposal reconciliation requires fenced protected source' USING ERRCODE='23514';
                END IF;
                -- Reconciliation changes the source reference only when
                -- ParishSoft's value changed, so an Administrator's accepted
                -- value at the old reference (#528, ADM-09.03) is stale now.
                -- An approval resting on it goes back to To review while the
                -- change stays open. A queued change keeps its approval (the
                -- publish worker owns it then); its plan, merged against the
                -- Family's baseline again, can only meet a conflict. (The
                -- CASE is in parentheses: an IF condition ends at its first
                -- THEN outside them.)
                -- An Updated in ParishSoft change a refresh found
                -- ParishSoft no longer holds (#1749) goes back to To
                -- review too: its approval is decided again, never carried
                -- by a later refresh or the Family's same answer back
                -- into force over the undo in ParishSoft. Nothing else
                -- here changes the decision.
                -- Strict phase order (#1890) leaves a change ParishSoft
                -- caught up with open (pending) instead of resolving it
                -- upstream, and then nothing is left to write, so the stale
                -- base protects nothing: an approval kept over a Conflict
                -- keeps its stage (Ready, To enter by hand). The worker's
                -- merge says when ParishSoft holds the carried value, as it
                -- chose resolved_upstream before; this guard, which cannot
                -- compare field values canonically, admits the kept
                -- approval only on a pending outcome whose ParishSoft value
                -- moved off the baseline, which every caught-up value does
                -- unless the reviewer's Edit is the baseline value itself.
                IF NEW.decision IS DISTINCT FROM (CASE WHEN OLD.execution='published'
                       THEN 'unreviewed' WHEN OLD.decision='approved'
                       AND OLD.execution IN ('pending','failed')
                       AND NEW.execution IN ('pending','conflict')
                       AND EXISTS (SELECT 1 FROM public.stewardship_proposal_review base
                    WHERE base.proposal_id=OLD.id AND base.accepted_base
                      AND base.accepted_source_id=OLD.current_source_id
                      AND NOT EXISTS (SELECT 1 FROM public.stewardship_proposal_review later
                          WHERE later.proposal_id=base.proposal_id
                            AND later.expected_version>base.expected_version
                            AND later.expected_version<OLD.version
                            AND later.decision<>'approved'))
                       THEN 'unreviewed' ELSE OLD.decision END)
                   AND NOT (OLD.execution<>'published' AND NEW.decision=OLD.decision
                       AND NEW.execution='pending' AND NEW.current_available
                       AND (OLD.admin_value_set
                           OR NEW.current_value IS DISTINCT FROM OLD.baseline_value))
                THEN
                    RAISE EXCEPTION 'Proposal reconciliation must return a stale accepted approval to review' USING ERRCODE='23514';
                END IF;
            ELSIF current_user='pk_stewardship_web' THEN
                -- A Staff or Administrator resolution (#528) is admitted only
                -- with its paired history row, whose own guard checked the
                -- actor, the campaign and the starting state: it changes the
                -- execution (entered) or the decision (ignored, reopened),
                -- nothing else, and advances the version by one. Any other
                -- web change must be a replacement by a later Family response,
                -- which never changes the decision. A change Entered by hand
                -- takes only a resolution (#1737): Entered by hand again keeps
                -- it entered. Back to review (reopened) of a terminal step
                -- (#1840: Entered by hand, Updated in ParishSoft, Already in
                -- ParishSoft) makes it pending, open to reconciliation again,
                -- with the decision behind the step kept: approved, also for
                -- a change that Entered by hand accepted in one step (decision
                -- 2, unreviewed until now), and unreviewed for a change
                -- ParishSoft already held that nobody approved (To review).
                -- Reopening an Ignore keeps the execution (AND binds before
                -- OR).
                IF NOT EXISTS (
                    SELECT 1 FROM public.stewardship_proposal_resolution r
                    WHERE r.proposal_id=OLD.id AND r.expected_version=OLD.version
                      AND NEW.version=OLD.version+1
                      AND ROW(NEW.current_available,NEW.current_value,NEW.current_source_id,
                              NEW.admin_value_set,NEW.admin_value,NEW.superseded_by_id)
                          IS NOT DISTINCT FROM
                          ROW(OLD.current_available,OLD.current_value,OLD.current_source_id,
                              OLD.admin_value_set,OLD.admin_value,OLD.superseded_by_id)
                      AND CASE r.action
                          WHEN 'entered' THEN NEW.execution='resolved_external'
                              AND NEW.decision=OLD.decision
                          WHEN 'ignored' THEN NEW.decision='ignored'
                              AND NEW.execution=OLD.execution
                              AND OLD.execution NOT IN ('resolved_external','published','resolved_upstream')
                          WHEN 'reopened' THEN NEW.decision='unreviewed'
                              AND NEW.execution=OLD.execution
                              AND OLD.execution NOT IN ('resolved_external','published','resolved_upstream')
                              OR (OLD.execution IN ('resolved_external','published','resolved_upstream')
                                  AND NEW.execution='pending'
                                  AND NEW.decision=(CASE WHEN OLD.execution='resolved_upstream'
                                      AND OLD.decision<>'approved' THEN 'unreviewed' ELSE 'approved' END))
                          ELSE false END)
                   -- An Administrator's review of an Automatic change (#528,
                   -- ADM-09) is admitted the same way, only with its paired
                   -- review row, whose own guard checked the actor, the
                   -- campaign and the starting state: the decision and the
                   -- edit fields become exactly what the row records, nothing
                   -- else changes, and the version advances by one. A review
                   -- that resolves a conflict (ADM-09.03, the row accepts
                   -- ParishSoft's current value) also moves it from conflict to
                   -- pending; a decision review that takes a resolved change
                   -- (an active accepted base) away from approved undoes that,
                   -- putting it back to conflict; every other review keeps the
                   -- execution.
                   -- An entered change takes neither a review nor a Family's
                   -- replacement (#1737), nor does an updated or already
                   -- held one the web login may now undo (#1840).
                   AND (OLD.execution IN ('resolved_external','published','resolved_upstream') OR (NOT EXISTS (
                    SELECT 1 FROM public.stewardship_proposal_review v
                    WHERE v.proposal_id=OLD.id AND v.expected_version=OLD.version
                      AND NEW.version=OLD.version+1
                      AND ROW(NEW.decision,NEW.admin_value_set,NEW.admin_value)
                          IS NOT DISTINCT FROM ROW(v.decision,v.admin_value_set,v.admin_value)
                      AND ROW(NEW.current_available,NEW.current_value,NEW.current_source_id,
                              NEW.superseded_by_id)
                          IS NOT DISTINCT FROM
                          ROW(OLD.current_available,OLD.current_value,OLD.current_source_id,
                              OLD.superseded_by_id)
                      AND CASE WHEN v.accepted_base
                          THEN OLD.execution='conflict' AND NEW.execution='pending'
                          WHEN v.decision<>'approved' AND EXISTS (SELECT 1 FROM public.stewardship_proposal_review base
                    WHERE base.proposal_id=OLD.id AND base.accepted_base
                      AND base.accepted_source_id=OLD.current_source_id
                      AND NOT EXISTS (SELECT 1 FROM public.stewardship_proposal_review later
                          WHERE later.proposal_id=base.proposal_id
                            AND later.expected_version>base.expected_version
                            AND later.expected_version<OLD.version
                            AND later.decision<>'approved'))
                          THEN NEW.execution='conflict'
                          ELSE NEW.execution=OLD.execution END)
                   AND (NEW.decision<>OLD.decision
                   OR NEW.execution NOT IN ('superseded','cancelled','resolved_upstream')
                   OR NOT EXISTS (
                       SELECT 1 FROM public.stewardship_submission later
                       JOIN public.stewardship_family_form_baseline baseline ON baseline.id=later.baseline_id
                       JOIN public.stewardship_submission earlier ON earlier.id=OLD.submission_id
                       WHERE baseline.state='open'
                         AND later.family_id=earlier.family_id AND later.campaign_id=earlier.campaign_id
                         AND later.mode=earlier.mode
                         AND later.rehearsal_epoch_id IS NOT DISTINCT FROM earlier.rehearsal_epoch_id
                         AND later.family_version>earlier.family_version
                         AND (((public.stewardship_response_census_anchor_v1(later.prior_submission_id)=earlier.id OR (OLD.entity_kind='member'
                              AND OLD.field IN ('moved_household','deceased_status','death_date')))
                         AND OLD.id=(
                             SELECT candidate.id FROM public.stewardship_proposed_change candidate
                             JOIN public.stewardship_submission history ON history.id=candidate.submission_id
                             JOIN public.stewardship_submission prior ON prior.id=later.prior_submission_id
                             WHERE history.family_id=later.family_id AND history.campaign_id=later.campaign_id
                               AND history.mode=later.mode
                               AND history.rehearsal_epoch_id IS NOT DISTINCT FROM later.rehearsal_epoch_id
                               AND history.family_version<=prior.family_version
                               AND (history.id=public.stewardship_response_census_anchor_v1(prior.id) OR (OLD.entity_kind='member'
                                    AND OLD.field IN ('moved_household','deceased_status','death_date')))
                               AND ROW(candidate.entity_kind,candidate.entity_key,candidate.field)=
                                   ROW(OLD.entity_kind,OLD.entity_key,OLD.field)
                             ORDER BY history.family_version DESC LIMIT 1))
                           -- A reviewed form (#1705) names each change it
                           -- showed, which a Family that left it as shown kept
                           -- on its earlier response: such a change, on any
                           -- earlier response, is the one the new answer
                           -- replaces or withdraws.
                           OR (later.reviewed_view AND EXISTS (
                               SELECT 1 FROM jsonb_array_elements(later.shown_items) shown
                               WHERE shown->>'stream'='census' AND shown->>'item'=OLD.id::text)))
                         AND (NEW.execution <> 'superseded' OR EXISTS (
                             SELECT 1 FROM public.stewardship_proposed_change successor
                             WHERE successor.id=NEW.superseded_by_id AND successor.submission_id=later.id))
                   ))))
                THEN
                    RAISE EXCEPTION 'Proposal replacement requires a new final Family response' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='stewardship_additional_information' THEN
        IF response.mode <> 'live' OR NEW.text IS DISTINCT FROM response.answers->>'additional_information'
            OR length(NEW.text)>5000 OR btrim(NEW.text)='' THEN
            RAISE EXCEPTION 'Follow-up requires live submitted text' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (
            (to_jsonb(NEW)-ARRAY['disposition','replacement_id','follow_up_needed','followed_up_at','version','updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['disposition','replacement_id','follow_up_needed','followed_up_at','version','updated_at'])
            OR (OLD.disposition <> 'current_actionable' AND ROW(NEW.disposition,NEW.replacement_id) IS DISTINCT FROM ROW(OLD.disposition,OLD.replacement_id))
        ) THEN
            RAISE EXCEPTION 'Follow-up text and disposition history are immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.version <> OLD.version+1 THEN
            RAISE EXCEPTION 'Response detail must advance its version' USING ERRCODE='23514';
        END IF;
        NEW.updated_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_publication_confirmation_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE plan public.stewardship_publication_plan%ROWTYPE;
        writes bigint; fields bigint; blocked bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication confirmations are immutable' USING ERRCODE='23514';
    END IF;
    -- One confirmation at a time, so two cannot both see no overlap.
    PERFORM pg_advisory_xact_lock(736241, 91);
    IF NEW.actor_id IS NULL
       OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_portal_session login
            WHERE login.id=NEW.session_id AND login.principal_id=NEW.actor_id
              AND login.revoked_at IS NULL AND login.expires_at>clock_timestamp()
              AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
              AND login.authenticated_at=NEW.authenticated_at
              -- Any sign-in age (#1547); a command session still needs
              -- its live full-scope automation session (ADM-11).
              AND login.authenticated_at<=clock_timestamp()
              AND (NOT EXISTS (SELECT 1 FROM public.stewardship_automation_login command
                       WHERE command.portal_session_id=login.id)
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Publishing to ParishSoft requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO plan FROM public.stewardship_publication_plan WHERE id=NEW.plan_id;
    -- In Testing mode publication stops after preflight.
    IF plan.id IS NULL OR plan.testing
       OR plan.created_at<=statement_timestamp()-interval '60 minutes'
       OR public.stewardship_export_admitted_v1(plan.campaign_id,true) IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication plan is missing, from Testing mode or too old to confirm'
        USING ERRCODE='23514'; END IF;
    SELECT count(*) FILTER (WHERE e.planned_sha256 IS NOT NULL),
           count(*) FILTER (WHERE e.blocked)
      INTO writes, blocked
      FROM public.stewardship_publication_entity e WHERE e.plan_id=plan.id;
    SELECT count(*) INTO fields
      FROM public.stewardship_publication_field f
      JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
     WHERE e.plan_id=plan.id AND e.planned_sha256 IS NOT NULL AND f.outcome='write';
    IF NEW.write_entities IS DISTINCT FROM writes OR NEW.write_fields IS DISTINCT FROM fields
       OR NEW.blocked_entities IS DISTINCT FROM blocked
    THEN RAISE EXCEPTION 'Publication confirmation counts differ from the plan'
        USING ERRCODE='23514'; END IF;
    -- Share-locked in key order, so no decision or edit lands before commit.
    PERFORM 1 FROM public.stewardship_proposed_change p
      JOIN public.stewardship_publication_field f ON f.proposal_id=p.id
      JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
     WHERE e.plan_id=plan.id ORDER BY p.id FOR SHARE OF p;
    IF EXISTS(SELECT 1 FROM public.stewardship_publication_field f
          JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
          JOIN public.stewardship_proposed_change p ON p.id=f.proposal_id
         WHERE e.plan_id=plan.id
           AND CASE WHEN f.outcome='already_in_parishsoft'
                -- Marked at the check (#528, ADM-09), in the plan's own
                -- transaction, at the next version: Updated in ParishSoft
                -- with nothing written since #1890, resolved upstream for a
                -- plan checked before it.
                THEN p.version<>f.proposal_version+1
                    OR p.execution NOT IN ('published','resolved_upstream')
                ELSE p.version<>f.proposal_version
                OR p.execution NOT IN ('pending','failed','conflict')
                -- A planned write must still be approved and out of
                -- conflict: a writer need not bump the version (#1243).
                OR (f.outcome='write'
                    AND (p.decision<>'approved' OR p.execution='conflict')) END)
    THEN RAISE EXCEPTION 'A planned census change has changed since preflight'
        USING ERRCODE='23514'; END IF;
    -- No change this plan writes is still being written by another
    -- confirmed plan: that entity has no published or invalidated outcome.
    IF EXISTS(SELECT 1 FROM public.stewardship_publication_field f
          JOIN public.stewardship_publication_entity e ON e.id=f.entity_id
          JOIN public.stewardship_publication_field other_field
            ON other_field.proposal_id=f.proposal_id AND other_field.outcome='write'
          JOIN public.stewardship_publication_entity other
            ON other.id=other_field.entity_id AND other.plan_id<>plan.id
           AND other.planned_sha256 IS NOT NULL
          JOIN public.stewardship_publication_confirmation c ON c.plan_id=other.plan_id
         WHERE e.plan_id=plan.id AND e.planned_sha256 IS NOT NULL AND f.outcome='write'
           AND NOT EXISTS(SELECT 1 FROM public.stewardship_publication_outcome o
                WHERE o.entity_id=other.id AND o.result IN ('published','invalidated')))
    THEN RAISE EXCEPTION 'Another confirmed publication is still writing these changes'
        USING ERRCODE='23505'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

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
        -- Strict phase order (#1890): a refresh no longer resolves an open
        -- request whose roster already shows it (the worker's only change
        -- here was that resolution). The request keeps its stage and is
        -- decided, ticked and then confirmed by a refresh check. Requests a
        -- refresh resolved before keep their resolution source as history.
        RAISE EXCEPTION 'A refresh does not resolve a Ministry request' USING ERRCODE='23514';
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

CREATE OR REPLACE FUNCTION public.stewardship_refresh_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    change stewardship_proposed_change%ROWTYPE;
    request stewardship_ministry_request%ROWTYPE;
    family uuid; phase text; carried jsonb; matches boolean; roster boolean;
    marked jsonb; expected jsonb; added bigint; family_key bigint;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Refresh checks are immutable' USING ERRCODE='23514';
    END IF;
    -- Inside the promotion of the current snapshot: the owner check refresh
    -- reconciliation uses (live lease, ordered admission), and the lease's
    -- phase says whether the refresh is full. Only the source worker's
    -- login holds INSERT.
    SELECT lease.phase INTO phase FROM stewardship_source_current current_source
        JOIN stewardship_source_snapshot source ON source.id=current_source.snapshot_id
        JOIN stewardship_source_lease lease ON lease.owner_id=source.task_id
            AND lease.fence=source.source_fence
        WHERE current_source.singleton AND source.id=NEW.snapshot_id;
    IF NEW.actor_id IS NOT NULL
       OR NOT stewardship_response_source_owner_v1(NEW.snapshot_id)
       OR NEW.full_refresh IS DISTINCT FROM (phase='full')
    THEN RAISE EXCEPTION 'Refresh checks require the promoting source worker'
        USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM stewardship_refresh_check k
        WHERE k.outcome='confirmed' AND k.item_version=NEW.item_version
          AND (k.proposal_id=NEW.proposal_id OR k.request_id=NEW.request_id))
    THEN RAISE EXCEPTION 'A confirmed item is not checked again' USING ERRCODE='23514'; END IF;
    IF NEW.proposal_id IS NOT NULL THEN
        -- A live census change Entered by hand or Updated in ParishSoft, at
        -- its current version, with the snapshot's own value for it: a
        -- Member's field, the Family's email opt-out (#1738) or home
        -- address, or a New Member by the ParishSoft Member ID recorded at
        -- its mark (#1749).
        SELECT p.* INTO change FROM stewardship_proposed_change p
            WHERE p.id=NEW.proposal_id FOR SHARE;
        SELECT s.family_id INTO family FROM stewardship_submission s
            WHERE s.id=change.submission_id AND s.mode='live';
        carried := CASE WHEN change.admin_value_set THEN change.admin_value
            ELSE change.submitted_value END;
        IF change.entity_kind='proposed_member' AND change.field='new_member' THEN
            -- The Member ID of the change's current mark: the resolution
            -- that advanced it to its current version. Confirmed once the
            -- snapshot lists that Member as an active, living Member of the
            -- Family; no ID, no check.
            SELECT r.member_duid INTO added FROM stewardship_proposal_resolution r
                WHERE r.proposal_id=change.id AND r.expected_version=change.version-1
                  AND r.action='entered';
            SELECT f.family_duid INTO family_key FROM stewardship_family_campaign f
                WHERE f.id=family;
            expected := CASE WHEN added IS NOT NULL THEN jsonb_build_object('available',true,
                'value',stewardship_member_source_status_v1(NEW.snapshot_id,added::text,family_key)
                    ='current') END;
        ELSE
            expected := stewardship_refresh_census_source_v1(
                NEW.snapshot_id,family,change.entity_kind,change.entity_key,change.field);
        END IF;
        IF change.id IS NULL OR family IS NULL
           OR change.execution NOT IN ('published','resolved_external')
           OR NOT (change.entity_kind='member'
               OR (change.entity_kind='family' AND change.field IN ('email_opt_out','home_address'))
               OR (change.entity_kind='proposed_member' AND change.field='new_member'))
           OR change.version<>NEW.item_version
           OR NEW.observed IS NULL OR NOT coalesce((NEW.observed->'available')='true'::jsonb,false)
           OR NEW.observed IS DISTINCT FROM expected
        THEN RAISE EXCEPTION 'A census refresh check requires a final change and its source value'
            USING ERRCODE='23514'; END IF;
        -- A New Member is held when the Member is listed; an address is
        -- compared without its country and ZIP+4 suffix (#1749).
        matches := CASE
            WHEN change.field='new_member' THEN NEW.observed->'value'='true'::jsonb
            WHEN change.field='home_address' THEN
                stewardship_refresh_address_key_v1(NEW.observed->'value')
                IS NOT DISTINCT FROM stewardship_refresh_address_key_v1(carried)
            ELSE stewardship_response_comparison_v1(change.field,NEW.observed->'value')
                IS NOT DISTINCT FROM stewardship_response_comparison_v1(change.field,carried) END;
        -- A yes-or-no request (moved, deceased), an opt-out whose baseline
        -- was never read, a New Member and an address whose block is not
        -- known have no third value to be wrong at. An Updated in
        -- ParishSoft change ParishSoft no longer holds is a Conflict
        -- (#1749), and only such a change is; only a full refresh says so,
        -- since a quick update's change list could lag the write. A quick
        -- update records nothing for it.
        IF NEW.outcome='ignored_now_matches' OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
           OR (NEW.outcome='entered_incorrectly' AND (change.execution<>'resolved_external'
               OR change.field IN ('moved_household','deceased_status','email_opt_out',
                   'home_address','new_member')))
           OR (change.execution='published' AND NOT matches
               AND (NEW.outcome<>'conflict' OR NOT NEW.full_refresh))
           OR (NEW.outcome='conflict' AND change.execution<>'published')
        THEN RAISE EXCEPTION 'A census refresh check differs from its evidence'
            USING ERRCODE='23514'; END IF;
        -- A change a later response of the Family replaced (the same field
        -- answered again) is history and gets no check (#1737).
        IF EXISTS (SELECT 1 FROM stewardship_proposed_change later
            JOIN stewardship_submission answered ON answered.id=later.submission_id
            JOIN stewardship_submission earlier ON earlier.id=change.submission_id
            WHERE answered.family_id=earlier.family_id
              AND answered.campaign_id=earlier.campaign_id AND answered.mode=earlier.mode
              AND answered.family_version>earlier.family_version
              AND ROW(later.entity_kind,later.entity_key,later.field)
                  =ROW(change.entity_kind,change.entity_key,change.field))
        THEN RAISE EXCEPTION 'A replaced census change is not checked'
            USING ERRCODE='23514'; END IF;
        -- A change Entered by hand is judged against the value recorded at
        -- its mark (#1737): the change's stored ParishSoft value, which its
        -- first mark froze, unless the mark was made again over Entered
        -- incorrectly. A final change's version moves only by such a mark
        -- (by one), so a check at the version before the current one means
        -- the change was marked again there, and the latest such check holds
        -- the value recorded then. With no value recorded (ParishSoft could
        -- not report one at the first mark) the refresh's own value stands
        -- in, so the check may not say Entered incorrectly. A field with no
        -- third value is unchanged whenever it is not confirmed (above).
        IF change.execution='resolved_external' AND NEW.outcome<>'confirmed'
           AND change.field NOT IN ('moved_household','deceased_status','email_opt_out',
               'home_address','new_member') THEN
            SELECT k.observed INTO marked FROM stewardship_refresh_check k
                WHERE k.proposal_id=change.id AND k.item_version=change.version-1
                ORDER BY k.created_at DESC, k.id DESC LIMIT 1;
            IF marked IS NULL AND change.current_available THEN
                marked:=jsonb_build_object('available',true,'value',change.current_value);
            END IF;
            IF (marked IS NULL AND NEW.outcome='entered_incorrectly')
               OR (marked IS NOT NULL
                   AND (stewardship_response_comparison_v1(change.field,NEW.observed->'value')
                        IS NOT DISTINCT FROM
                        stewardship_response_comparison_v1(change.field,marked->'value'))
                   IS DISTINCT FROM (NEW.outcome='unchanged'))
            THEN RAISE EXCEPTION 'A census refresh check differs from the value recorded at its mark'
                USING ERRCODE='23514'; END IF;
        END IF;
    ELSE
        -- A live Ministry request a person resolved or closed, at its
        -- current version, judged by the Member's roster state for the
        -- Ministry carried forward (the Edit's, else the Family's), or the
        -- Family's for an ignored request.
        SELECT r.* INTO request FROM stewardship_ministry_request r
            WHERE r.id=NEW.request_id FOR SHARE;
        SELECT s.family_id INTO family FROM stewardship_submission s
            WHERE s.id=request.submission_id AND s.mode='live';
        IF request.id IS NULL OR family IS NULL OR request.entity_kind<>'member'
           OR request.version<>NEW.item_version OR NEW.observed IS NOT NULL
           OR request.resolution_source_id IS NOT NULL
           OR request.state NOT IN ('resolved','closed_no_response')
           OR NEW.outcome IN ('entered_incorrectly','conflict')
           OR (NEW.outcome='confirmed' AND (request.state<>'resolved'
               OR request.outcome NOT IN ('joined','leave_confirmed')))
           OR (NEW.outcome='ignored_now_matches' AND request.state='resolved'
               AND request.outcome IN ('joined','leave_confirmed'))
           -- Strict phase order (#1890): an accepted request is Confirmed
           -- only after its Entered in ParishSoft tick, its latest roster
           -- entry; before it the roster showing it changes no stage.
           OR (NEW.outcome='confirmed' AND NOT coalesce((SELECT e.entered
               FROM stewardship_ministry_roster_entry e WHERE e.request_id=request.id
               ORDER BY e.sequence DESC LIMIT 1),false))
        THEN RAISE EXCEPTION 'A Ministry refresh check requires a decided request'
            USING ERRCODE='23514'; END IF;
        roster := stewardship_response_ministry_current_v1(NEW.snapshot_id,family,
            request.entity_key,CASE WHEN request.outcome IN ('joined','leave_confirmed')
                THEN coalesce(request.reviewed_ministry_duid,request.ministry_duid)
                ELSE request.ministry_duid END);
        IF roster IS NULL
           OR (roster=(request.action='join')) IS DISTINCT FROM
              (NEW.outcome IN ('confirmed','ignored_now_matches'))
        THEN RAISE EXCEPTION 'A Ministry refresh check differs from its roster evidence'
            USING ERRCODE='23514'; END IF;
    END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_pledge_stages_v1(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL)
RETURNS TABLE(family_id uuid, family_duid bigint, submission_id uuid,
    sequence bigint, stage text, base_stage text, review_id uuid,
    in_force boolean, check_outcome text, observed numeric, baseline numeric,
    next_baseline numeric, checks bigint)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT i.family_id,i.family_duid,i.submission_id,i.sequence,
        -- Strict phase order (#1890): a confirmed check (ParishSoft holds
        -- C) places only a marked pledge; on a To review or To enter by
        -- hand one, and at the submit (rules 3 and 4), the pledge keeps
        -- its stage and the page says ParishSoft already holds it.
        CASE WHEN k.outcome='confirmed' AND i.stage='entered_by_hand'
                THEN k.outcome
            WHEN k.outcome IN ('conflict','entered_incorrectly')
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
    -- 0135's open check read, unchanged: its inner ``s`` is the checked
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
    -- Strict phase order (#1890): an Accept or Edit records a new row, which
    -- no refresh has checked yet, so the To enter by hand pledge carries
    -- the pledge's latest check made while its answer was the same (or,
    -- with none, the submit's hold, rules 3 and 4) when that found
    -- ParishSoft holding the amount to enter: its row keeps saying so until
    -- a refresh checks the new row. It only words the page: the stage, the
    -- refresh (which reads the row's own checks) and the checks' count are
    -- unchanged.
    LEFT JOIN LATERAL (
        SELECT l.observed FROM (
            SELECT c.outcome::text AS outcome,c.observed,c.created_at,c.id
            FROM stewardship_pledge_open_check c
            JOIN stewardship_submission x ON x.id=c.submission_id
            WHERE c.family_id=i.family_id
              AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                  IS NOT DISTINCT FROM stewardship_pledge_answer_v1(s.answers,s.annual_pledge)
            UNION ALL
            SELECT c.outcome::text,c.observed,c.created_at,c.id
            FROM stewardship_pledge_refresh_check c
            JOIN stewardship_pledge_review v ON v.id=c.review_id
            WHERE v.family_id=i.family_id
              AND v.answer IS NOT DISTINCT FROM stewardship_pledge_answer_v1(s.answers,s.annual_pledge)
            UNION ALL
            SELECT 'confirmed',run.answered,'-infinity'::timestamptz,NULL
            WHERE run.answered IS NOT NULL AND run.answered=s.annual_pledge
              AND NOT EXISTS(SELECT 1 FROM stewardship_pledge_review m
                  WHERE m.family_id=i.family_id AND m.entered)
            ORDER BY created_at DESC,id DESC NULLS LAST LIMIT 1) l
        WHERE i.stage='to_enter_by_hand' AND i.in_force AND d.outcome IS NULL
          AND l.outcome='confirmed'
          AND l.observed=CASE WHEN r.decision='accepted_edited' THEN r.reviewed_annual_pledge
              ELSE (r.answer->>'annual_pledge')::numeric END) h ON true
    CROSS JOIN LATERAL (SELECT CASE WHEN z.held OR h.observed IS NOT NULL THEN 'confirmed'
            ELSE coalesce(d.outcome,o.outcome) END AS outcome,
        CASE WHEN z.held THEN run.answered WHEN h.observed IS NOT NULL THEN h.observed
            WHEN d.outcome IS NOT NULL THEN d.observed ELSE o.observed END AS observed) k
$$;

CREATE OR REPLACE FUNCTION public.stewardship_pledge_refresh_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    item record; phase text; refreshed numeric; matches boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Pledge refresh checks are immutable' USING ERRCODE='23514';
    END IF;
    -- Inside the promotion of the current snapshot, as 0132's checks are:
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
    -- A decided pledge whose decision row is the item's latest and in force,
    -- not Confirmed (the definer selection above), at that row's sequence.
    -- Only a marked pledge is Confirmed (#1890): an accepted one not yet
    -- marked whose check found ParishSoft holding C stays To enter by hand,
    -- which the selection still returns, and is checked again, so a later
    -- amount moves it on. A confirmed check on a pledge the selection no
    -- longer returns means a Confirmed one, never checked again.
    SELECT * INTO item FROM stewardship_pledge_refresh_items_v2(NULL,NULL,NEW.review_id);
    IF item.review_id IS NULL AND EXISTS (SELECT 1 FROM stewardship_pledge_refresh_check k
        WHERE k.review_id=NEW.review_id AND k.outcome='confirmed')
    THEN RAISE EXCEPTION 'A confirmed pledge is not checked again' USING ERRCODE='23514'; END IF;
    IF item.review_id IS NULL OR NEW.item_version IS DISTINCT FROM item.sequence
    THEN RAISE EXCEPTION 'A pledge refresh check requires a decided pledge in force'
        USING ERRCODE='23514'; END IF;
    -- The snapshot's own amount, compared with the carried-forward one, A
    -- and the one recorded at the mark (A when it recorded none).
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
       -- Marked: Confirmed at C, unchanged at the mark, else Entered
       -- incorrectly.
       OR (item.decision<>'ignored' AND item.entered
            AND (NEW.outcome NOT IN ('confirmed','unchanged','entered_incorrectly')
                 OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
                 OR (NOT matches AND (refreshed=item.marked)
                     IS DISTINCT FROM (NEW.outcome='unchanged'))))
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

DO $check$
DECLARE before text := current_setting('stewardship.migration_0159_acl');
BEGIN
    -- Each new body, by text only the new body has (every pattern is absent
    -- from the body it replaces), and none a definer.
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%OR (open_state AND NEW.action=''entered''%'
              AND prosrc LIKE '%AND (proposal.decision<>''approved'' OR proposal.execution<>''pending''))%'
              -- 0158's optional Ignored note (#1889), kept.
              AND prosrc LIKE '%OR (undo AND NEW.note !~ ''\\S'')%'
              AND prosrc NOT LIKE '%OR (NEW.action=''reopened'' AND NEW.note%')
    THEN RAISE EXCEPTION 'The census resolution guard still admits a mark before an Accept'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%OR (NEW.execution=''resolved_upstream'' AND OLD.execution<>''queued'')%'
              AND prosrc LIKE '%OR (OLD.execution IN (''pending'',''failed'') AND NEW.execution=''published''%'
              AND prosrc NOT LIKE '%AND NEW.execution=''resolved_upstream''%'
              AND prosrc LIKE '%AND NEW.execution=''pending'' AND NEW.current_available%'
              -- 0153's reviewed view rules (#1705), kept.
              AND prosrc LIKE '%carries_intent := FOUND AND NOT response.reviewed_view%'
              AND prosrc LIKE '%WHERE shown->>''stream''=''census'' AND shown->>''item''=OLD.id::text)))%')
    THEN RAISE EXCEPTION 'The census guard still confirms a change before its step'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_publication_confirmation_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%OR p.execution NOT IN (''published'',''resolved_upstream'')%')
    THEN RAISE EXCEPTION 'The publication confirmation does not read the check''s published mark'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_ministry_request_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%RAISE EXCEPTION ''A refresh does not resolve a Ministry request''%'
              AND prosrc NOT LIKE '%Ministry resolution requires fenced scoped roster evidence%'
              -- 0154's reviewed view rules (#1705), kept.
              AND prosrc LIKE '%AND later.reviewed_view AND NOT OLD.parish_added%'
              AND prosrc LIKE '%A replaced Ministry request keeps no outcome%')
    THEN RAISE EXCEPTION 'The Ministry request guard still lets a refresh resolve a request'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%OR (NEW.outcome=''confirmed'' AND NOT coalesce((SELECT e.entered%')
    THEN RAISE EXCEPTION 'The refresh check guard still confirms an unticked request'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_stages_v1(uuid,uuid)'::regprocedure
              AND NOT prosecdef AND provolatile='s'
              AND prosrc LIKE '%CASE WHEN k.outcome=''confirmed'' AND i.stage=''entered_by_hand''%'
              AND prosrc NOT LIKE '%k.outcome IN (''confirmed'',''conflict'',''entered_incorrectly'')%'
              AND prosrc LIKE '%WHERE i.stage=''to_enter_by_hand'' AND i.in_force AND d.outcome IS NULL%')
    THEN RAISE EXCEPTION 'The pledge stage selection still confirms a pledge before its mark'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_refresh_check_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%IF item.review_id IS NULL AND EXISTS (SELECT 1 FROM stewardship_pledge_refresh_check k%')
    THEN RAISE EXCEPTION 'The pledge refresh check guard still stops checking an unmarked pledge'; END IF;
    -- Grants, settings and definer flags as they were (CREATE OR REPLACE
    -- keeps each owner, and fails for any other login).
    IF before IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||p.prosecdef::text
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_proposal_resolution_guard_v1',
            'stewardship_response_derived_guard_v1','stewardship_publication_confirmation_guard_v1',
            'stewardship_ministry_request_guard_v1','stewardship_refresh_check_guard_v1',
            'stewardship_pledge_stages_v1','stewardship_pledge_refresh_check_guard_v1'))
       OR before NOT LIKE '7:%'
    THEN RAISE EXCEPTION 'The replaced functions'' grants or settings changed'; END IF;
END $check$;
