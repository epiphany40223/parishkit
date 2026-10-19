-- Frozen forward migration file 0140 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): Entered
-- incorrectly's two fixes (#1737). This file is installed by its Django
-- migration in the responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- A census change Entered by hand (execution resolved_external) is final in
-- the census guard. A refresh that finds ParishSoft holding a third value
-- places it at Entered incorrectly from its latest refresh check (0136,
-- provisional number), without changing the row. The review pipeline
-- (docs/specs/stewardship/review-pipeline/spec.md, "Entered incorrectly
-- stage" and "Refresh checks") fixes it in one of two ways, each a
-- resolution row (0060's table and actions, unchanged) paired with the
-- change at its next version:
--   * Entered by hand again: 'entered' once more. The change stays
--     resolved_external; its version moves on, so the checks made before no
--     longer place it, and the value recorded at this mark is ParishSoft's
--     value in the latest check at the version it was made at.
--   * Back to review: 'reopened'. The change becomes pending with no
--     decision (its edited value kept), open to reconciliation again.
-- Replaced, each from its latest body, with these changes and only these:
--   * stewardship_proposal_resolution_guard_v1 (0060's): both actions are
--     admitted on a change Entered by hand only while its latest refresh
--     check at its current version says entered_incorrectly, for a current
--     Staff member or Administrator, with an optional note; every other
--     rule stays, an open change's included (Reopen of an Ignore stays the
--     Administrator's, with a note).
--   * stewardship_response_derived_guard_v1 (0118's; guards.sql has the
--     same body): the web login may update a resolved_external change, and
--     only with its paired resolution row: 'entered' keeps it entered and
--     'reopened' makes it pending with decision unreviewed; 'ignored' never
--     applies to it, and neither does a review or a Family's replacement.
--     The worker and every other login still find it final.
--   * stewardship_refresh_check_guard_v1 (0136's): a census check of a
--     change Entered by hand that is not confirmed must agree with the value
--     recorded at the change's mark: unchanged exactly when ParishSoft holds
--     that value (canonically compared), and entered_incorrectly only when a
--     value was recorded. The value is the latest check's at the version
--     before the current one when there is one (a mark made again there),
--     else the change's stored ParishSoft value.
-- 0060's deferred effect check already requires 'entered' to leave the
-- change resolved_external and 'reopened' to leave it unreviewed, at the
-- next version, with the census_change_updated audit event, so it is kept.
-- No table, column, constraint or row changes. None of the three functions
-- is SECURITY DEFINER (each was created without one), so nothing else is
-- restored. The file ends with a DO block that refuses to commit unless all
-- three bodies are installed with their attributes and triggers.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_proposal_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
        by_hand boolean; open_state boolean; incorrect boolean;
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
    -- Entered incorrectly (#1737): a change Entered by hand whose latest
    -- refresh check, at its current version, found a third value in
    -- ParishSoft. Its two fixes are Entered by hand again ('entered') and
    -- Back to review ('reopened'), Staff's as well as an Administrator's.
    incorrect:=coalesce(proposal.execution='resolved_external',false)
        AND coalesce((SELECT k.outcome='entered_incorrectly'
            FROM public.stewardship_refresh_check k
            WHERE k.proposal_id=proposal.id AND k.item_version=proposal.version
            ORDER BY k.created_at DESC, k.id DESC LIMIT 1),false);
    IF proposal.id IS NULL OR campaign IS NULL
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,
           NEW.action='reopened' AND NOT incorrect)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM proposal.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       OR proposal.handling='report-only'
       OR (incorrect AND NEW.action NOT IN ('entered','reopened'))
       OR (NOT incorrect AND NOT open_state)
       OR (open_state AND NEW.action IN ('entered','ignored')
           AND (NOT by_hand OR proposal.decision='ignored'))
       OR (open_state AND NEW.action='reopened' AND proposal.decision<>'ignored')
       -- Undoing an Ignore says why.
       OR (open_state AND NEW.action='reopened' AND NEW.note !~ '\S')
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
            -- that the web login reworks over Entered incorrectly (#1737):
            -- the web branch below admits that only with its paired
            -- resolution row, whose own guard requires the change's latest
            -- refresh check to say Entered incorrectly.
            IF (OLD.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                    AND NOT (OLD.execution='resolved_external' AND current_user='pk_stewardship_web'))
                OR (to_jsonb(NEW)-ARRAY['current_available','current_value','current_source_id','admin_value_set','admin_value','decision','execution','superseded_by_id','version','updated_at'])
                    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['current_available','current_value','current_source_id','admin_value_set','admin_value','decision','execution','superseded_by_id','version','updated_at'])
            THEN
                RAISE EXCEPTION 'Proposal history and terminal outcomes are immutable' USING ERRCODE='23514';
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
                -- already in ParishSoft.
                IF ROW(NEW.current_available,NEW.current_value,NEW.admin_value_set,
                       NEW.admin_value,NEW.decision,NEW.superseded_by_id)
                   IS DISTINCT FROM
                   ROW(OLD.current_available,OLD.current_value,OLD.admin_value_set,
                       OLD.admin_value,OLD.decision,OLD.superseded_by_id)
                   OR NOT (
                   (OLD.execution IN ('pending','failed') AND NEW.execution='queued'
                       AND OLD.decision='approved' AND OLD.handling='api' AND OLD.entity_kind='member'
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
                -- Family's baseline again, can only meet a conflict. Nothing
                -- else here changes the decision. (The CASE is in
                -- parentheses: an IF condition ends at its first THEN outside
                -- them.)
                IF NEW.decision IS DISTINCT FROM (CASE WHEN OLD.decision='approved'
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
                -- it entered, and Back to review (reopened) makes it pending
                -- with no decision, open to reconciliation again.
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
                              AND OLD.execution<>'resolved_external'
                          WHEN 'reopened' THEN NEW.decision='unreviewed'
                              AND NEW.execution=(CASE WHEN OLD.execution='resolved_external'
                                  THEN 'pending' ELSE OLD.execution END)
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
                   -- replacement (#1737).
                   AND (OLD.execution='resolved_external' OR (NOT EXISTS (
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

CREATE OR REPLACE FUNCTION public.stewardship_refresh_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    change stewardship_proposed_change%ROWTYPE;
    request stewardship_ministry_request%ROWTYPE;
    family uuid; phase text; carried jsonb; matches boolean; roster boolean;
    marked jsonb;
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
        -- its current version, with the snapshot's own value for it.
        SELECT p.* INTO change FROM stewardship_proposed_change p
            WHERE p.id=NEW.proposal_id FOR SHARE;
        SELECT s.family_id INTO family FROM stewardship_submission s
            WHERE s.id=change.submission_id AND s.mode='live';
        carried := CASE WHEN change.admin_value_set THEN change.admin_value
            ELSE change.submitted_value END;
        IF change.id IS NULL OR family IS NULL
           OR change.execution NOT IN ('published','resolved_external')
           OR change.entity_kind<>'member' OR change.version<>NEW.item_version
           OR NEW.observed IS NULL OR NOT coalesce((NEW.observed->'available')='true'::jsonb,false)
           OR NEW.observed IS DISTINCT FROM stewardship_response_field_source_v1(
               NEW.snapshot_id,family,change.entity_key,change.field)
        THEN RAISE EXCEPTION 'A census refresh check requires a final change and its source value'
            USING ERRCODE='23514'; END IF;
        matches := stewardship_response_comparison_v1(change.field,NEW.observed->'value')
            IS NOT DISTINCT FROM stewardship_response_comparison_v1(change.field,carried);
        IF NEW.outcome='ignored_now_matches' OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
           OR (NEW.outcome='entered_incorrectly' AND change.execution<>'resolved_external')
        THEN RAISE EXCEPTION 'A census refresh check differs from its evidence'
            USING ERRCODE='23514'; END IF;
        -- A change Entered by hand is judged against the value recorded at
        -- its mark (#1737): the change's stored ParishSoft value, which its
        -- first mark froze, unless the mark was made again over Entered
        -- incorrectly. A final change's version moves only by such a mark
        -- (by one), so a check at the version before the current one means
        -- the change was marked again there, and the latest such check holds
        -- the value recorded then. With no value recorded (ParishSoft could
        -- not report one at the first mark) the refresh's own value stands
        -- in, so the check may not say Entered incorrectly.
        IF change.execution='resolved_external' AND NEW.outcome<>'confirmed' THEN
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
           OR NEW.outcome='entered_incorrectly'
           OR (NEW.outcome='confirmed' AND (request.state<>'resolved'
               OR request.outcome NOT IN ('joined','leave_confirmed')))
           OR (NEW.outcome='ignored_now_matches' AND request.state='resolved'
               AND request.outcome IN ('joined','leave_confirmed'))
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

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND prosrc LIKE '%k.outcome=''entered_incorrectly''%'
              AND prosrc LIKE '%NEW.action=''reopened'' AND NOT incorrect)%'
              AND prosrc LIKE '%OR (incorrect AND NEW.action NOT IN (''entered'',''reopened''))%'
              AND prosrc LIKE '%OR (NOT incorrect AND NOT open_state)%'
              AND prosrc LIKE '%Census change resolution lacks current authority, version or state%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_resolution'::regclass
              AND t.tgname='stewardship_proposal_resolution_guard'
              AND t.tgfoid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_resolution_guard_v1 lacks the Entered incorrectly fixes';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND prosrc LIKE '%AND NOT (OLD.execution=''resolved_external'' AND current_user=''pk_stewardship_web''))%'
              AND prosrc LIKE '%THEN ''pending'' ELSE OLD.execution END)%'
              AND prosrc LIKE '%AND (OLD.execution=''resolved_external'' OR (NOT EXISTS (%'
              AND prosrc LIKE '%''prefix'',''suffix'',''gender'',''marital_status'',''moved_household'',''deceased_status'',''death_date'')%'
              AND prosrc LIKE '%Proposal differs from its immutable response%'
              AND prosrc LIKE '%THEN OLD.execution=''conflict'' AND NEW.execution=''pending''%'
              AND prosrc LIKE '%Proposal publication requires its confirmed plan and outcome%'
              AND prosrc LIKE '%f.outcome=''already_in_parishsoft''%'
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
        RAISE EXCEPTION 'stewardship_response_derived_guard_v1 lacks the Entered incorrectly fixes';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND prosrc LIKE '%k.item_version=change.version-1%'
              AND prosrc LIKE '%A census refresh check differs from the value recorded at its mark%'
              AND prosrc LIKE '%TG_OP<>''INSERT''%'
              AND prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
              AND prosrc LIKE '%stewardship_response_field_source_v1(%'
              AND prosrc LIKE '%stewardship_response_ministry_current_v1(%'
              AND prosrc LIKE '%k.outcome=''confirmed''%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_refresh_check'::regclass
              AND t.tgname='stewardship_refresh_check_guard'
              AND t.tgfoid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_refresh_check_guard_v1 lacks the value recorded at the mark';
    END IF;
END $check$;
