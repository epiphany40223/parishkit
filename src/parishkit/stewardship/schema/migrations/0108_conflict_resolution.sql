-- Frozen forward migration file 0108 (the repository-wide file sequence):
-- an Administrator resolves an Automatic census change's conflict (#528,
-- ADM-09.03). This file is installed by its Django migration in the
-- responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copy of the replaced derived guard equals the
-- fresh-install baseline's (schema/guards.sql).
--
-- A conflict means ParishSoft changed after the Family answered.
-- "Use the Family's value anyway" is a review of a third kind, besides a
-- decision and an edit: it accepts ParishSoft's current value as the base
-- publication merges against (instead of the Family's baseline, which stays
-- the immutable record of what the Family saw), approves the change and
-- moves it from conflict to pending. stewardship_proposal_review gains the
-- accepted base: accepted_base, accepted_value and accepted_source_id, the
-- snapshot the value came from (no foreign key, so source retention is
-- unaffected). A base is active while that snapshot is still the
-- proposal's current_source_id (and no later decision review undid it);
-- reconciliation changes that reference whenever ParishSoft's value
-- changes, so a base goes stale by itself, and nothing on the proposal
-- records it.
--
-- Replaced, each from its latest body:
--   * stewardship_proposal_review_guard_v1 (0107's): a resolution only on a
--     conflict whose ParishSoft value is known, approving it, with no edit,
--     and a base equal to the proposal's current value and source; every
--     other review accepts nothing. Every 0096 and 0107 rule stays.
--   * stewardship_proposal_review_effect_v1 (0096's): a resolution leaves
--     the proposal pending at the next version.
--   * stewardship_response_derived_guard_v1 (0096's, the latest; guards.sql
--     has the same body): the web review branch moves conflict to pending
--     only with a paired resolution, and back to conflict with a decision
--     review that takes a resolved change away from approved (an undo);
--     reconciliation must send an approval resting on a now stale base back
--     to To review while the change stays open (not while queued), and
--     changes the decision in no other way; a Family resubmission does not
--     carry such an approval either (nor the base).
-- A base is active while its snapshot is the proposal's current source
-- reference and no later decision review (one recording a decision other
-- than approved) follows it; a later Edit value keeps it.
-- None is SECURITY DEFINER (the baseline has no ALTER for the derived
-- guard, and 0096 created the other two without one), so nothing else is
-- restored. The file ends with a DO block that refuses to commit unless all
-- of this is installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_proposal_review" ADD COLUMN "accepted_base" boolean DEFAULT false NOT NULL;
ALTER TABLE "stewardship_proposal_review" ADD COLUMN "accepted_source_id" uuid NULL;
ALTER TABLE "stewardship_proposal_review" ADD COLUMN "accepted_value" jsonb NULL;
ALTER TABLE "stewardship_proposal_review" ADD CONSTRAINT "proposal_review_accepted_shape" CHECK (((NOT "accepted_base" AND "accepted_source_id" IS NULL AND "accepted_value" IS NULL) OR ("accepted_base" AND "accepted_source_id" IS NOT NULL AND "decision" = 'approved')));

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
       -- An Administrator only; Staff keep Automatic changes view-only.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,true)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM proposal.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       -- Automatic only: an API-writable Member field. Family addresses are
       -- worked by hand until a verified ParishSoft address read exists.
       OR proposal.handling<>'api' OR proposal.entity_kind<>'member'
       -- Open only: never while queued for publication, never once final.
       OR proposal.execution NOT IN ('pending','conflict','failed')
       -- A conflict is approved only by resolving it (ADM-09.03, below).
       OR (NEW.decision='approved' AND proposal.execution='conflict' AND NOT resolved)
       -- Exactly one of a decision and an edit (#528, ADM-09.04), unless the
       -- review resolves a conflict (AND binds first: this reads
       -- "OR (decided=edited AND NOT resolved)").
       OR decided=edited AND NOT resolved
       -- A resolution ("Use the Family's value anyway", ADM-09.03) accepts
       -- ParishSoft's current value, exactly as the proposal holds it and
       -- from the snapshot it came from, as the base publication merges
       -- against, and approves the change. Only on a conflict whose
       -- ParishSoft value is known, and never with an edit.
       OR (resolved AND (edited OR proposal.execution<>'conflict'
           OR NOT proposal.current_available OR NEW.decision<>'approved'
           OR NEW.accepted_value IS DISTINCT FROM proposal.current_value
           OR NEW.accepted_source_id IS DISTINCT FROM proposal.current_source_id))
       -- Every other review accepts nothing.
       OR (NOT resolved AND ROW(NEW.accepted_value,NEW.accepted_source_id)
           IS DISTINCT FROM ROW(NULL::jsonb,NULL::uuid))
    THEN RAISE EXCEPTION 'Census change review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    IF edited AND NEW.admin_value_set THEN
        -- A phone edit is a phone record, any other edit text. (The type is
        -- worked out first: an IF condition ends at its first THEN.)
        edit_type := CASE WHEN proposal.field IN ('home_phone','mobile_phone','work_phone')
            THEN 'object' ELSE 'string' END;
        IF NEW.admin_value IS NULL OR NEW.admin_value='null'::jsonb
           OR jsonb_typeof(NEW.admin_value) IS DISTINCT FROM edit_type
           OR octet_length(NEW.admin_value::text)>1024
        THEN RAISE EXCEPTION 'Census change edit has no value of its field''s shape'
            USING ERRCODE='23514'; END IF;
        -- The comparison validates the shape (a phone record is its own
        -- display's, a date an ISO civil date) and raises on a bad one. Its
        -- key is trimmed of the full whitespace set, so a blank edit (a tab,
        -- a no-break or ideographic space) has an empty key; a phone's key
        -- is not text, so its display is read the same way.
        edit_key := public.stewardship_response_comparison_v1(proposal.field,NEW.admin_value);
        edit_text := CASE WHEN edit_type='object'
            THEN public.stewardship_response_comparison_v1('nickname',NEW.admin_value->'display')
            ELSE edit_key END;
        IF edit_key IS NULL OR coalesce(edit_text,'')='' OR edit_key IS NOT DISTINCT FROM
            public.stewardship_response_comparison_v1(proposal.field,proposal.submitted_value)
        THEN RAISE EXCEPTION 'Census change edit must differ from the Family''s answer'
            USING ERRCODE='23514'; END IF;
    END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_proposal_review_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO proposal FROM public.stewardship_proposed_change WHERE id=NEW.proposal_id;
    SELECT campaign_id INTO campaign FROM public.stewardship_submission
        WHERE id=proposal.submission_id;
    IF proposal.version IS DISTINCT FROM NEW.expected_version+1
       OR ROW(proposal.decision,proposal.admin_value_set,proposal.admin_value)
           IS DISTINCT FROM ROW(NEW.decision,NEW.admin_value_set,NEW.admin_value)
       -- A conflict resolution (ADM-09.03) leaves the change pending.
       OR (NEW.accepted_base AND proposal.execution<>'pending')
       -- One history row per version: never a review and a resolution both.
       OR EXISTS(SELECT 1 FROM public.stewardship_proposal_resolution r
           WHERE r.proposal_id=NEW.proposal_id AND r.expected_version=NEW.expected_version)
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_audit_event a
           JOIN public.stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='census_change_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.expected_version,'after_version',NEW.expected_version+1))
    THEN RAISE EXCEPTION 'Census change review requires its result and audit'
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
                expected_handling := CASE WHEN NEW.field IN ('prefix','suffix','marital_status','moved_household','deceased_status')
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
                expected_handling := CASE WHEN NEW.field='email_opt_out' THEN 'manual' ELSE 'api' END;
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
            IF OLD.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
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
                           THEN 'published' ELSE 'invalidated' END))
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
                -- which never changes the decision.
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
                          WHEN 'reopened' THEN NEW.decision='unreviewed'
                              AND NEW.execution=OLD.execution
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
                   AND NOT EXISTS (
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
                   ))
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
            WHERE oid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%resolved := NEW.accepted_base;%'
              AND prosrc LIKE '%NEW.accepted_source_id IS DISTINCT FROM proposal.current_source_id%'
              AND prosrc LIKE '%OR decided=edited AND NOT resolved%'
              AND prosrc LIKE '%Census change edit must differ from the Family%'
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,true)%'
              AND prosrc LIKE '%proposal.execution NOT IN (''pending'',''conflict'',''failed'')%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_review'::regclass
              AND t.tgfoid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_review_guard_v1 lacks the conflict resolution';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_review_effect_v1()'::regprocedure
              AND prosrc LIKE '%NEW.accepted_base AND proposal.execution<>''pending''%'
              AND prosrc LIKE '%Census change review requires its result and audit%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_proposal_review_effect'
              AND tgrelid='public.stewardship_proposal_review'::regclass
              AND tgfoid='public.stewardship_proposal_review_effect_v1()'::regprocedure
              AND tgenabled='O' AND tgdeferrable AND tginitdeferred)
    THEN
        RAISE EXCEPTION 'stewardship_proposal_review_effect_v1 lacks the conflict resolution';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND prosrc LIKE '%THEN OLD.execution=''conflict'' AND NEW.execution=''pending''%'
              AND prosrc LIKE '%THEN NEW.execution=''conflict''%'
              AND prosrc LIKE '%later.decision<>''approved''%'
              AND prosrc LIKE '%Proposal reconciliation must return a stale accepted approval to review%'
              AND prosrc LIKE '%THEN ''unreviewed'' ELSE predecessor.decision END%'
              AND prosrc LIKE '%stewardship_proposal_review v%'
              AND prosrc LIKE '%stewardship_proposal_resolution r%'
              AND prosrc LIKE '%Proposal publication requires its confirmed plan and outcome%'
              AND prosrc LIKE '%Proposal reconciliation requires fenced protected source%'
              AND prosrc LIKE '%Proposal replacement requires a new final Family response%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposed_change'::regclass
              AND t.tgfoid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_response_derived_guard_v1 lacks the conflict resolution';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_proposal_review'::regclass
              AND conname='proposal_review_accepted_shape' AND contype='c'
              AND pg_get_constraintdef(oid) LIKE '%accepted_base%decision%approved%')
       OR (SELECT count(*) FROM pg_attribute
            WHERE attrelid='public.stewardship_proposal_review'::regclass
              AND NOT attisdropped
              AND (attname, format_type(atttypid, atttypmod), attnotnull) IN (
                  ('accepted_base', 'boolean', true),
                  ('accepted_value', 'jsonb', false),
                  ('accepted_source_id', 'uuid', false)))<>3
    THEN
        RAISE EXCEPTION 'stewardship_proposal_review lacks its accepted base';
    END IF;
END $check$;
