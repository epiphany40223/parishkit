-- Frozen forward migration file 0090 (the repository-wide file sequence):
-- the fenced ParishSoft publish worker's proposal states (#528, BG-09). This
-- file is installed by its Django migration in the responses app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced baseline function
-- equals the fresh-install baseline's (schema/guards.sql).
--
-- stewardship_response_derived_guard_v1 is replaced with its current body
-- plus one worker branch, taken when the source reference is unchanged
-- (reconciliation always changes it): the worker may move an approved
-- automatic Member change pending|failed -> queued only for a 'write' field
-- row at the change's current version in a confirmed, non-Testing plan
-- whose entity has no published or invalidated outcome, while the system is
-- in Production mode and the live publication lease's task is running; and
-- queued -> published, or queued -> pending|failed, only when the latest
-- outcome of the entity that queued it (the latest confirmed plan writing
-- it at that version) is published, or invalidated. Nothing else in the row may
-- change. The function is not SECURITY DEFINER (the baseline has no ALTER
-- for it), so nothing else is restored.
--
-- Two of 0087's migration-owned guards are replaced too (the #1243 review
-- notes): stewardship_publication_confirmation_guard_v1 also requires every
-- planned write to be still approved and out of conflict, and
-- stewardship_publication_outcome_guard_v1 locks the lease row and requires
-- its owning task to be running at the lease's fence and worker.
-- stewardship_publication_audited_v1 is unchanged. No table, column or
-- grant changes. The file ends with a DO block that refuses to commit unless
-- all of this is installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

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
                IF ROW(NEW.decision,NEW.admin_value_set,NEW.admin_value)
                    IS DISTINCT FROM ROW(predecessor.decision,predecessor.admin_value_set,predecessor.admin_value) THEN
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
              AND (login.authenticated_at BETWEEN clock_timestamp()-interval '5 minutes'
                       AND clock_timestamp()
                   -- A live full-scope automation session stands in for a
                   -- fresh sign-in, as in the other fresh-gated guards.
                   OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id))) THEN
        RAISE EXCEPTION 'Publishing to ParishSoft requires fresh Administrator authentication'
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
           AND (p.version<>f.proposal_version
                OR p.execution NOT IN ('pending','failed','conflict')
                -- A planned write must still be approved and out of
                -- conflict: a writer need not bump the version (#1243).
                OR (f.outcome='write'
                    AND (p.decision<>'approved' OR p.execution='conflict'))))
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

CREATE OR REPLACE FUNCTION public.stewardship_publication_outcome_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE entity public.stewardship_publication_entity%ROWTYPE;
        latest public.stewardship_publication_outcome%ROWTYPE; fenced boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Publication outcomes are immutable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO entity FROM public.stewardship_publication_entity WHERE id=NEW.entity_id;
    SELECT * INTO latest FROM public.stewardship_publication_outcome
        WHERE entity_id=NEW.entity_id ORDER BY attempt DESC LIMIT 1;
    -- The lease row is locked, and its owning task must still be running at
    -- the lease's fence and worker, as 0009 requires of source writers.
    SELECT phase='publication' AND fence=NEW.fence AND owner_id IS NOT NULL
           AND expires_at>clock_timestamp()
           AND EXISTS (SELECT 1 FROM public.stewardship_task_run t
               WHERE t.id=lease.owner_id AND t.state='running'
                 AND t.fence=lease.task_fence AND t.worker_id=lease.worker_id
                 AND t.lease_expires_at>clock_timestamp())
      INTO fenced FROM public.stewardship_source_lease lease WHERE singleton FOR SHARE;
    -- A concurrent attempt at the same number fails the attempt key.
    IF entity.id IS NULL OR entity.planned_sha256 IS NULL OR NEW.actor_id IS NOT NULL
       OR NOT EXISTS(SELECT 1 FROM public.stewardship_publication_confirmation c
            WHERE c.plan_id=entity.plan_id)
       OR NEW.attempt IS DISTINCT FROM coalesce(latest.attempt,0)+1
       OR coalesce(latest.result IN ('published','invalidated'),false)
       OR fenced IS NOT TRUE
    THEN RAISE EXCEPTION 'Publication outcome lacks a confirmed entity, attempt or fence'
        USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND prosrc LIKE '%Proposal publication requires its confirmed plan and outcome%'
              AND prosrc LIKE '%NEW.current_source_id IS NOT DISTINCT FROM OLD.current_source_id%'
              AND prosrc LIKE '%f.proposal_version=OLD.version AND f.outcome=''write''%'
              AND prosrc LIKE '%f.proposal_version=OLD.version-1%'
              AND prosrc LIKE '%ORDER BY c.created_at DESC, c.id DESC LIMIT 1%'
              AND prosrc LIKE '%WHERE mode=''production''%'
              AND prosrc LIKE '%l.phase=''publication''%'
              AND prosrc LIKE '%Proposal reconciliation requires fenced protected source%'
              AND prosrc LIKE '%stewardship_proposal_resolution r%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposed_change'::regclass
              AND t.tgfoid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_response_derived_guard_v1 lacks the publication branch';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_publication_confirmation_guard_v1()'::regprocedure
              AND prosrc LIKE '%p.decision<>''approved'' OR p.execution=''conflict''%'
              AND prosrc LIKE '%p.version<>f.proposal_version%'
              AND prosrc LIKE '%pg_advisory_xact_lock(736241, 91)%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_publication_outcome_guard_v1()'::regprocedure
              AND prosrc LIKE '%WHERE singleton FOR SHARE%'
              AND prosrc LIKE '%t.id=lease.owner_id AND t.state=''running''%'
              AND prosrc LIKE '%phase=''publication'' AND fence=NEW.fence%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
    THEN
        RAISE EXCEPTION 'The publication guards lack their 0090 checks';
    END IF;
    -- Untouched here, but the replaced guards rely on it staying a definer.
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_publication_audited_v1(text,uuid,uuid,jsonb)'::regprocedure
              AND prosecdef)
    THEN
        RAISE EXCEPTION 'stewardship_publication_audited_v1 is no longer SECURITY DEFINER';
    END IF;
END $check$;
