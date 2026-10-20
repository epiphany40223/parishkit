-- Frozen forward migration file 0210 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): Back to
-- review undoes a census change's terminal step (#1840). This file is
-- installed by its Django migration in the responses app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Undoing a terminal stage") makes no stage final: Back to review removes
-- only the terminal step, so the change returns to the stage before it with
-- its decision kept. For census changes the undo is a 'reopened'
-- resolution row (0060's table and actions, unchanged) paired with the
-- change at its next version, which becomes pending:
--   * Entered by hand (resolved_external; also Entered incorrectly and
--     Confirmed, which a refresh check places it at): approved, so To enter
--     by hand. A To review change Entered by hand accepted and marked in one
--     step (decision 2, its decision still unreviewed) becomes approved,
--     as it was shown: Accepted.
--   * Updated in ParishSoft (published, also when Confirmed): approved, so
--     Ready to update. Nothing is written to or read from ParishSoft; a
--     later update run plans it again from a fresh read.
--   * Already in ParishSoft (resolved_upstream): approved stays approved;
--     a change nobody approved goes back to unreviewed (To review).
-- The undo advances the version, so the refresh checks made for the
-- undone step no longer place the change; it is open again, so the refresh
-- checks nothing until it is marked or updated again, and a later mark
-- starts Not seen afresh. Entered incorrectly's Back to review (#1737),
-- which went to pending with no decision, is now this undo.
-- Replaced, each from its latest body, with these changes and only these:
--   * stewardship_proposal_resolution_guard_v1 (0139's): also admits a
--     'reopened' resolution of a resolved_external, published or
--     resolved_upstream change that no later response of the Family
--     replaced, for a current Staff member or Administrator, with a note.
--     Every 'reopened' resolution now needs a note (Entered incorrectly's
--     did not). Reopening an Ignore stays the Administrator's.
--   * stewardship_proposal_resolution_effect_v1 (0060's): a 'reopened'
--     resolution may leave the change pending and approved, the decision
--     an undone step kept.
--   * stewardship_response_derived_guard_v1 (0138's; guards.sql has the
--     same body): the web login may update a published or resolved_upstream
--     change too, and, like a resolved_external one, only with its paired
--     resolution row; 'reopened' on any of the three makes it pending with
--     the decision above, never a review, an 'ignored' resolution or a
--     Family's replacement. The worker and every other login still find
--     them final.
-- stewardship_refresh_check_guard_v1 is not replaced: it checks only a
-- published or resolved_external change, and its value recorded at a mark
-- reads a check at the version before the change's current one. An undo
-- leaves the change open, where no check is made, so a mark after an undo
-- never finds a check there and its value recorded at the mark is the
-- change's stored ParishSoft value, as for a first mark.
-- None of the functions is SECURITY DEFINER (each was created without
-- one), so nothing else is restored. The file ends with a DO block that
-- refuses to commit unless all of it is installed. Reversing needs its own
-- forward migration.

SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

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
       -- Undoing an Ignore, or a terminal step (#1840), says why.
       OR (NEW.action='reopened' AND NEW.note !~ '\S')
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

CREATE OR REPLACE FUNCTION public.stewardship_proposal_resolution_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO proposal FROM public.stewardship_proposed_change WHERE id=NEW.proposal_id;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=proposal.submission_id;
    IF proposal.version IS DISTINCT FROM NEW.expected_version+1
       OR (NEW.action='entered' AND proposal.execution<>'resolved_external')
       OR (NEW.action='ignored' AND proposal.decision<>'ignored')
       -- Reopening an Ignore leaves the decision unreviewed; undoing a
       -- terminal step (#1840) leaves the change pending with the decision
       -- behind it, unreviewed or approved, which the census guard checks.
       OR (NEW.action='reopened' AND proposal.decision<>'unreviewed'
           AND NOT (proposal.decision='approved' AND proposal.execution='pending'))
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='census_change_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.expected_version,'after_version',NEW.expected_version+1))
    THEN RAISE EXCEPTION 'Census change resolution requires its result and audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
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
            carries_intent := FOUND
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
                -- The publication check also marks it resolved upstream when
                -- its plan, recorded in this transaction, found the value
                -- already in ParishSoft. It queues a change only while the
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
                   -- already in ParishSoft, so the change is resolved
                   -- upstream at the next version and nothing is written. The
                   -- plan guard stamps created_at with transaction_timestamp(),
                   -- so a plan committed earlier never matches; resolved_upstream
                   -- is terminal, so the mark is final.
                   OR (OLD.execution IN ('pending','failed') AND NEW.execution='resolved_upstream'
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
                IF NEW.execution NOT IN ('pending','conflict','resolved_upstream','cancelled')
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
                         AND (public.stewardship_response_census_anchor_v1(later.prior_submission_id)=earlier.id OR (OLD.entity_kind='member'
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
                             ORDER BY history.family_version DESC LIMIT 1)
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

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              -- This file's rule.
              AND prosrc LIKE '%undo:=NEW.action=''reopened'' AND NOT replaced%'
              AND prosrc LIKE '%(NOT undo AND NEW.action=''reopened'' AND NOT incorrect))%'
              AND prosrc LIKE '%OR (NOT incorrect AND NOT open_state) AND NOT again AND NOT undo%'
              AND prosrc LIKE '%OR (NEW.action=''reopened'' AND NEW.note !~ %'
              -- 0133's, 0138's and 0139's rules, kept.
              AND prosrc LIKE '%k.outcome=''entered_incorrectly''%'
              AND prosrc LIKE '%OR (incorrect AND NEW.action NOT IN (''entered'',''reopened''))%'
              AND prosrc LIKE '%again:=coalesce(proposal.execution=''resolved_external'',false)%'
              AND prosrc LIKE '%WHERE other.member_duid=NEW.member_duid%'
              AND prosrc LIKE '%Census change resolution lacks current authority, version or state%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_resolution'::regclass
              AND t.tgname='stewardship_proposal_resolution_guard'
              AND t.tgfoid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_resolution_guard_v1 lacks the terminal undo';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_resolution_effect_v1()'::regprocedure
              AND prosrc LIKE '%AND NOT (proposal.decision=''approved'' AND proposal.execution=''pending''))%'
              AND prosrc LIKE '%Census change resolution requires its result and audit%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_resolution'::regclass
              AND t.tgname='stewardship_proposal_resolution_effect'
              AND t.tgfoid='public.stewardship_proposal_resolution_effect_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_resolution_effect_v1 lacks the terminal undo';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              -- This file's rules.
              AND prosrc LIKE '%AND NOT (OLD.execution IN (''published'',''resolved_upstream'')%'
              AND prosrc LIKE '%IF OLD.execution=''published'' AND current_user<>''pk_stewardship_web'' AND NOT EXISTS (%'
              AND prosrc LIKE '%AND OLD.decision<>''approved'' THEN ''unreviewed'' ELSE ''approved'' END)%'
              AND prosrc LIKE '%AND (OLD.execution IN (''resolved_external'',''published'',''resolved_upstream'') OR (NOT EXISTS (%'
              AND prosrc NOT LIKE '%THEN ''pending'' ELSE OLD.execution END)%'
              -- 0138's rules, kept.
              AND prosrc LIKE '%An updated change becomes a Conflict only with its refresh check%'
              AND prosrc LIKE '%AND public.stewardship_member_source_status_v1(%'
              AND prosrc LIKE '%Proposal publication requires its confirmed plan and outcome%'
              AND prosrc LIKE '%Proposal reconciliation requires fenced protected source%'
              AND prosrc LIKE '%Proposal replacement requires a new final Family response%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposed_change'::regclass
              AND t.tgname='stewardship_proposed_change_guard'
              AND t.tgfoid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_response_derived_guard_v1 lacks the terminal undo';
    END IF;
END $check$;
