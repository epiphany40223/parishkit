-- Frozen forward migration file 0150 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): the
-- refresh's last census cases (#1749). This file is installed by its Django
-- migration in the responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Refresh checks") re-evaluates a final census change against each
-- promoted refresh. 0136, 0140 and 0141 (provisional numbers) check a change
-- Entered by hand and confirm an Updated in ParishSoft one. This file adds:
--   * An Updated in ParishSoft change ParishSoft no longer holds (back at
--     the baseline, or at a third value) is a Conflict: a full refresh's
--     source worker records a check saying so and moves the change to
--     conflict, at that value and the promoting snapshot, keeping its
--     decision.
--   * A New Member Staff mark entered may name the ParishSoft Member ID
--     of the Member they added; the refresh confirms the change once the
--     snapshot lists that Member, active and living, in the Family.
--   * The Family's home address is confirmed against ParishSoft's primary
--     address (the #1688 smoke test: the primary fields follow the home
--     block when home is primary), never Entered incorrectly.
--   * The publish worker queues a change only while the current snapshot
--     lists its Member, active and living, in the change's Family (#1738's
--     check, in the database too).
-- Changed tables, additively (no row is updated):
--   * stewardship_refresh_check's outcome CHECK is widened to 'conflict';
--   * stewardship_proposal_resolution gains member_duid (NULL, or a
--     positive ParishSoft DUID on an 'entered' row), with a CHECK.
-- Created: stewardship_refresh_address_key_v1, an address's confirmation
-- key. Replaced, each from its latest body, with these changes and only
-- these:
--   * stewardship_refresh_census_source_v1 (0141's): also the Family's
--     home address, from the snapshot's primary address of an active
--     Family.
--   * stewardship_proposal_resolution_guard_v1 (0140's): a member_duid
--     only on an 'entered' resolution of a New Member, never a Member the
--     Family's response listed nor one another New Member of the Family is
--     entered as now.
--   * stewardship_response_derived_guard_v1 (0140's; guards.sql has the
--     same body): the worker may move a published change to conflict, only
--     through its reconciliation branch (fenced, pinned, changed source)
--     with this promotion's 'conflict' check at the change's version whose
--     observed value is the change's new one, its decision kept; and the
--     queue step requires the Member's current standing.
--   * stewardship_refresh_check_guard_v1 (0140's and 0141's bodies,
--     joined: 0140 and 0141 each replace 0136's and neither depends on the
--     other, so this one carries both): a census check may be of a Family's
--     home address or a New Member; 'conflict' is the outcome of exactly a
--     published change ParishSoft no longer holds, on a full refresh only
--     (a quick update records none for it); a New Member, a home
--     address (and 0141's three fields) are never entered_incorrectly and
--     take no value recorded at a mark.
--   * stewardship_source_pin_guard (the baseline's; functions.sql has the
--     same body): the source worker may also pin the promoting snapshot for
--     a response whose Updated in ParishSoft change has this promotion's
--     conflict check at its version, so the change can move to it.
-- None of the functions is SECURITY DEFINER (each was created without one),
-- so nothing else is restored. The file ends with a DO block that refuses
-- to commit unless all of it is installed. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_refresh_check" DROP CONSTRAINT "refresh_check_outcome";
ALTER TABLE "stewardship_refresh_check" ADD CONSTRAINT "refresh_check_outcome" CHECK (((outcome)::text = ANY ((ARRAY['confirmed'::character varying, 'unchanged'::character varying, 'entered_incorrectly'::character varying, 'ignored_now_matches'::character varying, 'conflict'::character varying])::text[])));
ALTER TABLE "stewardship_proposal_resolution" ADD COLUMN "member_duid" bigint NULL;
ALTER TABLE "stewardship_proposal_resolution" ADD CONSTRAINT "proposal_resolution_member" CHECK (((member_duid IS NULL) OR (((action)::text = 'entered'::text) AND (member_duid > 0))));

-- The snapshot's own value for one census change, as a {available, value}
-- object, or NULL when the snapshot gives no evidence: 0141's body, plus
-- the Family's home address (#1749), read as ParishSoft's primary address
-- in the census address shape, with no country (no read returns one) and
-- ParishSoft's second and third lines joined. responses/refresh_checks.py
-- (census_source) reads the same. A New Member's evidence depends on its
-- mark, so the refresh check guard reads it itself.
CREATE OR REPLACE FUNCTION public.stewardship_refresh_census_source_v1(
    input_snapshot uuid, input_family uuid, input_kind text, input_key text,
    input_field text)
    RETURNS jsonb LANGUAGE plpgsql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    family_key text;
    family_value jsonb;
    member_value jsonb;
    member_family text;
    address jsonb;
BEGIN
    SELECT f.family_duid::text INTO family_key
        FROM public.stewardship_family_campaign f WHERE f.id=input_family;
    IF family_key IS NULL THEN RETURN NULL; END IF;
    IF input_kind='family' THEN
        IF input_field NOT IN ('email_opt_out','home_address')
           OR input_key IS DISTINCT FROM family_key THEN
            RETURN NULL;
        END IF;
        SELECT source.canonical::jsonb INTO family_value
            FROM public.stewardship_snapshot_family membership
            JOIN public.stewardship_source_family source ON source.id=membership.payload_id
            WHERE membership.snapshot_id=input_snapshot AND membership.source_key=family_key;
        IF family_value IS NULL OR family_value->'active' IS DISTINCT FROM 'true'::jsonb THEN
            RETURN NULL;
        END IF;
        IF input_field='home_address' THEN
            SELECT source.canonical::jsonb->'fields' INTO address
                FROM public.stewardship_snapshot_address membership
                JOIN public.stewardship_source_address source ON source.id=membership.payload_id
                WHERE membership.snapshot_id=input_snapshot
                  AND membership.source_key='family:'||family_key||':primary';
            IF jsonb_typeof(address) IS DISTINCT FROM 'object' THEN RETURN NULL; END IF;
            RETURN jsonb_build_object('available',true,'value',jsonb_build_object(
                'line1',coalesce(address->>'primaryAddress1',''),
                'line2',concat_ws(' ',nullif(address->>'primaryAddress2',''),
                    nullif(address->>'primaryAddress3','')),
                'city',coalesce(address->>'primaryCity',''),
                'region',coalesce(address->>'primaryState',''),
                'postal_code',coalesce(address->>'primaryPostalCode',''),
                'country',''));
        END IF;
        IF jsonb_typeof(family_value->'sendNoMail') IS DISTINCT FROM 'boolean' THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('available',true,'value',family_value->'sendNoMail');
    END IF;
    IF input_kind<>'member' THEN RETURN NULL; END IF;
    IF input_field NOT IN ('moved_household','deceased_status') THEN
        RETURN public.stewardship_response_field_source_v1(
            input_snapshot,input_family,input_key,input_field);
    END IF;
    SELECT member.canonical::jsonb, member.family_key INTO member_value, member_family
        FROM public.stewardship_snapshot_member membership
        JOIN public.stewardship_source_member member ON member.id=membership.payload_id
        WHERE membership.snapshot_id=input_snapshot AND membership.source_key=input_key;
    IF input_field='moved_household' THEN
        -- A death is not a move. (A Member not in the data has no row, so
        -- both tests below read NULL and the Member has left.)
        IF member_value->'deceased'='true'::jsonb THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('available',true,'value',NOT coalesce(
            member_family=family_key AND member_value->'active'='true'::jsonb,false));
    END IF;
    IF member_value IS NULL OR member_family IS DISTINCT FROM family_key THEN
        RETURN NULL;
    END IF;
    RETURN jsonb_build_object('available',true,'value',
        coalesce(member_value->'deceased'='true'::jsonb,false));
END;
$$;

-- A census address's confirmation key (#1749): the census comparison of
-- the address with no country (no read returns ParishSoft's) and a ZIP+4
-- suffix dropped (ParishSoft keeps the four digits apart). NULL for no
-- address. responses/refresh_checks.py (_address_key) builds the same.
CREATE FUNCTION public.stewardship_refresh_address_key_v1(input_value jsonb)
    RETURNS text LANGUAGE sql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT CASE WHEN input_value IS NULL OR input_value='null'::jsonb THEN NULL
        ELSE public.stewardship_response_comparison_v1('home_address',
            input_value || jsonb_build_object('country','',
                'postal_code',regexp_replace(coalesce(input_value->>'postal_code',''),
                    '^([0-9]{5})-[0-9]{4}$','\1'))) END
$$;

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
    -- Not for a change a later response of the Family replaced (the same
    -- field answered again): it is history.
    incorrect:=coalesce(proposal.execution='resolved_external',false)
        AND coalesce((SELECT k.outcome='entered_incorrectly'
            FROM public.stewardship_refresh_check k
            WHERE k.proposal_id=proposal.id AND k.item_version=proposal.version
            ORDER BY k.created_at DESC, k.id DESC LIMIT 1),false)
        AND NOT EXISTS (SELECT 1 FROM public.stewardship_proposed_change later
            JOIN public.stewardship_submission answered ON answered.id=later.submission_id
            JOIN public.stewardship_submission earlier ON earlier.id=proposal.submission_id
            WHERE answered.family_id=earlier.family_id
              AND answered.campaign_id=earlier.campaign_id AND answered.mode=earlier.mode
              AND answered.family_version>earlier.family_version
              AND ROW(later.entity_kind,later.entity_key,later.field)
                  =ROW(proposal.entity_kind,proposal.entity_key,proposal.field));
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
            -- refresh check to say Entered incorrectly; and an Updated in
            -- ParishSoft change a refresh finds ParishSoft no longer holds
            -- (#1749), which moves to conflict only with its paired refresh
            -- check (below), never through the web login.
            IF (OLD.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                    AND NOT (OLD.execution='resolved_external' AND current_user='pk_stewardship_web')
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
            -- requires the fenced, pinned source and keeps the decision.
            IF OLD.execution='published' AND NOT EXISTS (
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

CREATE OR REPLACE FUNCTION public.stewardship_source_pin_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE snapshot record;
BEGIN
    -- Separate role branches before planning their private table queries.
    -- A boolean AND does not prevent PostgreSQL privilege checks on subqueries.
    IF current_user='pk_stewardship_worker' THEN
      IF TG_OP='DELETE' THEN
        -- Source retention deletes a superseded, disposable fact generation
        -- and then releases its now-orphaned source input pin, only while it
        -- owns the live compaction lease. The fact rows' own guards admit
        -- only stewardship_fact_disposable generations.
        IF OLD.parent_kind='facts'
           AND NOT EXISTS (SELECT 1 FROM public.stewardship_daily_fact_set
               WHERE id=OLD.parent_id)
           AND EXISTS (SELECT 1 FROM public.stewardship_source_lease l
               JOIN public.stewardship_task_run t ON t.id=l.owner_id
               WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
                 AND t.state='running' AND t.fence=l.task_fence
                 AND t.worker_id=l.worker_id
                 AND t.lease_expires_at > clock_timestamp())
        THEN
            -- Same snapshot row lock as every other pin release.
            PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id
                FOR UPDATE;
            RETURN OLD;
        END IF;
        IF OLD.parent_kind <> 'submission'
           OR NOT public.stewardship_response_source_owner_v1(
               (SELECT snapshot_id FROM public.stewardship_source_current))
           OR public.stewardship_response_pin_required_v1(OLD.parent_id,OLD.snapshot_id)
        THEN
            RAISE EXCEPTION 'Worker may release only unused response comparison inputs'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='digest' THEN
        IF NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_daily_digest_snapshot s
            WHERE s.id=NEW.parent_id AND s.source_id=NEW.snapshot_id
              AND stewardship_daily_digest_live_v1(s.preparation_id,s.run_id,s.fence,s.worker_id)
        ) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Daily source protection requires its live exact snapshot'
                USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='INSERT' AND NEW.parent_kind='facts' THEN
        IF NEW.expires_at IS NOT NULL OR NOT (EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_fact_demand d
                ON d.id=t.domain_request_id
            WHERE t.task_type='report_facts' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp()
                AND d.population_scope='current'
                AND d.requested_source_id=NEW.snapshot_id
                AND d.claimed_generation_id IS NULL
        ) OR EXISTS (
            SELECT 1 FROM stewardship_task_run t JOIN stewardship_exact_export_request r
                ON r.id=t.domain_request_id AND r.task_id=t.root_id
            WHERE t.task_type='report_exact_export' AND t.state='running'
                AND t.lease_expires_at>clock_timestamp() AND r.population_scope='current'
                AND r.source_id=NEW.snapshot_id
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
                AND NOT EXISTS(SELECT 1 FROM stewardship_exact_export_cancel WHERE request_id=r.id)
        )) OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
            RAISE EXCEPTION 'Worker fact input protection requires its live demand'
                USING ERRCODE='23514';
        END IF;
      ELSE
      IF (
        TG_OP <> 'INSERT' OR NEW.parent_kind <> 'submission' OR NEW.expires_at IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted)
        OR NOT (EXISTS (
            SELECT 1 FROM public.stewardship_proposed_change change
            JOIN public.stewardship_submission response ON response.id=change.submission_id
            WHERE response.id=NEW.parent_id
              AND change.execution IN ('pending','conflict','queued','failed')
        ) OR EXISTS (
            SELECT 1 FROM public.stewardship_ministry_request request
            WHERE request.submission_id=NEW.parent_id AND request.entity_kind='member'
              AND request.state IN ('new','assigned','in_progress')
        ) OR EXISTS (
            -- An Updated in ParishSoft change this promotion found
            -- ParishSoft no longer holds (#1749): its conflict check, at
            -- its version and in this snapshot, comes first; the census
            -- guard then moves it to conflict at this pinned snapshot.
            SELECT 1 FROM public.stewardship_proposed_change change
            JOIN public.stewardship_refresh_check k ON k.proposal_id=change.id
                AND k.item_version=change.version AND k.outcome='conflict'
                AND k.snapshot_id=NEW.snapshot_id
            WHERE change.submission_id=NEW.parent_id AND change.execution='published'
        ))
        OR NOT public.stewardship_response_source_owner_v1(NEW.snapshot_id)
    ) THEN
        RAISE EXCEPTION 'Worker source protection requires current response reconciliation'
            USING ERRCODE='23514';
      END IF;
      END IF;
    END IF;
    IF current_user='pk_stewardship_web' THEN
      IF (
        (TG_OP <> 'INSERT' AND OLD.parent_kind <> 'form_baseline') OR
        (TG_OP <> 'DELETE' AND NEW.parent_kind NOT IN ('form_baseline','submission','report'))
    ) THEN
        RAISE EXCEPTION 'Web source protection is limited to Family forms'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='report' AND (
        NEW.expires_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM stewardship_exact_export_request r
            WHERE r.id=NEW.parent_id AND r.source_id=NEW.snapshot_id
                AND r.population_scope='current'
                AND stewardship_export_authorized_v1(r.requester_id)
                AND stewardship_export_admitted_v1(r.campaign_id,true)
        )) THEN
        RAISE EXCEPTION 'Web report protection requires its exact retained request'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='form_baseline' AND NOT EXISTS (
        SELECT 1 FROM stewardship_family_form_baseline
        WHERE id=NEW.parent_id AND source_id=NEW.snapshot_id AND state='open'
          AND expires_at=NEW.expires_at
    ) THEN
        RAISE EXCEPTION 'Web form protection requires its owned baseline'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND NEW.parent_kind='submission' AND NOT EXISTS (
        SELECT 1 FROM stewardship_submission response
        JOIN stewardship_family_form_baseline baseline ON baseline.id=response.baseline_id
        WHERE response.id=NEW.parent_id AND baseline.state='open'
          AND NEW.snapshot_id IN (response.reviewed_source_id,response.validation_source_id)
          AND NEW.expires_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Web response protection requires its final submission'
            USING ERRCODE='23514';
    END IF;
    END IF;
    IF TG_OP='DELETE' THEN
        PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id FOR UPDATE;
        RETURN OLD;
    END IF;
    SELECT state, compacted_at INTO snapshot FROM stewardship_source_snapshot WHERE id=NEW.snapshot_id
        FOR UPDATE;
    IF NOT FOUND OR snapshot.state <> 'promoted'
       OR snapshot.compacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Only reconstructable promoted snapshots can be protected'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_refresh_check'::regclass
              AND conname='refresh_check_outcome'
              AND pg_get_constraintdef(oid) LIKE '%''conflict''%')
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_proposal_resolution'::regclass
              AND conname='proposal_resolution_member' AND contype='c'
              AND pg_get_constraintdef(oid) LIKE '%member_duid IS NULL%'
              AND pg_get_constraintdef(oid) LIKE '%''entered''::text%'
              AND pg_get_constraintdef(oid) LIKE '%member_duid > 0%')
       OR NOT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid='public.stewardship_proposal_resolution'::regclass
              AND attname='member_duid' AND atttypid='bigint'::regtype
              AND NOT attnotnull AND NOT attisdropped)
    THEN
        RAISE EXCEPTION 'The refresh check outcome or the resolution Member ID is not installed';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_census_source_v1(uuid,uuid,text,text,text)'::regprocedure
              AND NOT prosecdef AND provolatile='s'
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%''family:''||family_key||'':primary''%'
              AND prosrc LIKE '%family_value->''sendNoMail''%'
              AND prosrc LIKE '%public.stewardship_response_field_source_v1(%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_address_key_v1(jsonb)'::regprocedure
              AND NOT prosecdef AND provolatile='i'
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
    THEN
        RAISE EXCEPTION 'The census refresh source functions are not installed as declared';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND prosrc LIKE '%OR (NEW.member_duid IS NOT NULL AND (NEW.action<>''entered''%'
              AND prosrc LIKE '%WHERE other.member_duid=NEW.member_duid%'
              AND prosrc LIKE '%OR (NOT incorrect AND NOT open_state)%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_resolution'::regclass
              AND t.tgname='stewardship_proposal_resolution_guard'
              AND t.tgfoid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_resolution_guard_v1 lacks the New Member ID rule';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND prosrc LIKE '%AND NOT (OLD.execution=''published'' AND NEW.execution=''conflict''%'
              AND prosrc LIKE '%An updated change becomes a Conflict only with its refresh check%'
              AND prosrc LIKE '%AND public.stewardship_member_source_status_v1(%'
              AND prosrc LIKE '%AND NOT (OLD.execution=''resolved_external'' AND current_user=''pk_stewardship_web'')%'
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
        RAISE EXCEPTION 'stewardship_response_derived_guard_v1 lacks the published Conflict rules';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND prosrc LIKE '%AND (NEW.outcome<>''conflict'' OR NOT NEW.full_refresh))%'
              AND prosrc LIKE '%OR (NEW.outcome=''conflict'' AND change.execution<>''published'')%'
              AND prosrc LIKE '%stewardship_refresh_address_key_v1(NEW.observed->''value'')%'
              AND prosrc LIKE '%r.action=''entered''%'
              -- 0140's rules and 0141's, both kept.
              AND prosrc LIKE '%k.item_version=change.version-1%'
              AND prosrc LIKE '%A replaced census change is not checked%'
              AND prosrc LIKE '%stewardship_refresh_census_source_v1(%'
              AND prosrc NOT LIKE '%stewardship_response_field_source_v1(%'
              AND prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
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
        RAISE EXCEPTION 'stewardship_refresh_check_guard_v1 lacks the published Conflict and the new census checks';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_source_pin_guard()'::regprocedure
              AND prosrc LIKE '%AND k.item_version=change.version AND k.outcome=''conflict''%'
              AND prosrc LIKE '%Worker source protection requires current response reconciliation%'
              AND NOT prosecdef)
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_source_pin'::regclass
              AND t.tgname='source_pin_guard'
              AND t.tgfoid='public.stewardship_source_pin_guard()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_source_pin_guard lacks the published Conflict pin';
    END IF;
END $check$;
