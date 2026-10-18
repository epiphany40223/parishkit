-- Frozen forward migration file 0132 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): Ministry
-- requests adopt the review pipeline (#1684). This file is installed by its
-- Django migration in the workflows app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that each replaced function's copy here equals the fresh-install
-- baseline's (schema/guards.sql, schema/ministry_followup.sql).
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md) gives
-- a Ministry request three decisions it never had: Edit (Accept with another
-- Ministry carried forward), Back to review (undo a decision before Staff
-- tick it Entered in ParishSoft) and Staff Add (a request the Family did not
-- ask for), and it stops a Family that ticks a decided request again from
-- creating a second request ("Family asked again"). Additive only, as the
-- spec's "Data integrity" requires during the campaign:
--   * stewardship_ministry_request gains reviewed_ministry_duid (the Edit's
--     Ministry, only on a request resolved as joined or leave confirmed,
--     never its own), parish_added (default false: every existing row is a
--     Family's) and added_in_place_of_id (the same Member's ignored request
--     an addition replaces), with CHECKs over the new columns only;
--   * stewardship_ministry_revision gains reviewed_ministry_duid and
--     decision_note (default ''), and its outcome CHECK is widened, never
--     tightened, to also admit a cancelled revision with no outcome (an
--     added request withdrawn by Back to review).
-- No existing row is updated. Replaced, each copied whole from its latest
-- definition (the baseline; no frozen file replaced the guards, and 0094
-- replaced the selection last) with only these changes:
--   stewardship_ministry_request_guard_v1        admits a parish-added
--       insert (a current Staff member or Administrator, the Family's
--       latest live response, a Member it lists, a campaign Ministry, no
--       current request for that Member and Ministry); lets a closed
--       request's projection go back to In progress, or an added one to
--       cancelled, only through Staff follow-up's paired revision; pairs the
--       Edit's Ministry too; the Family replacement branch still needs an
--       open request, and no Family insert or worker resolution sets the
--       new columns.
--   stewardship_ministry_submission_effects_v1   a submitted choice that
--       repeats its decided predecessor's action needs no derived request.
--   stewardship_ministry_revision_guard_v1       authority on the request's
--       current Ministry (the edited one once edited); Back to review and
--       withdraw (never on a refresh-resolved or ticked request, a note to
--       reopen an ignored one); Edit (another campaign Ministry the actor
--       may also follow up); a decision note of at most 2,000 characters.
--   stewardship_ministry_revision_effect_v1      the projection check
--       includes the Edit's Ministry.
--   stewardship_ministry_followup_v1             a request is in scope by
--       its Ministry or its Edit's; the latest version is found before
--       scope; the Ministry filter and search also match the Edit's
--       Ministry; rows carry the new fields.
-- None of them is SECURITY DEFINER, before or after, so there is no ALTER
-- FUNCTION to repeat; they keep their settings and grants, which the DO
-- block below compares with those recorded before the replacement, after
-- checking each new body and the new columns and constraints. The web
-- login's UPDATE on reviewed_ministry_duid is declared in
-- responses/grants.py. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. A
-- transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0132_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_request_guard_v1',
        'stewardship_ministry_submission_effects_v1','stewardship_ministry_revision_guard_v1',
        'stewardship_ministry_revision_effect_v1','stewardship_ministry_followup_v1')), true);

ALTER TABLE "stewardship_ministry_request" ADD COLUMN "reviewed_ministry_duid" integer NULL;
ALTER TABLE "stewardship_ministry_request" ADD COLUMN "parish_added" boolean DEFAULT false NOT NULL;
ALTER TABLE "stewardship_ministry_request" ADD COLUMN "added_in_place_of_id" uuid NULL;
ALTER TABLE "stewardship_ministry_request" ADD CONSTRAINT "ministry_request_reviewed" CHECK (((reviewed_ministry_duid IS NULL) OR (((outcome)::text = ANY (ARRAY[('joined'::character varying)::text, ('leave_confirmed'::character varying)::text])) AND (reviewed_ministry_duid > 0) AND ((state)::text = 'resolved'::text) AND (NOT ((reviewed_ministry_duid = ministry_duid) AND (reviewed_ministry_duid IS NOT NULL))))));
ALTER TABLE "stewardship_ministry_request" ADD CONSTRAINT "ministry_request_added" CHECK ((((NOT parish_added) OR ((entity_kind)::text = 'member'::text)) AND ((added_in_place_of_id IS NULL) OR (parish_added AND (NOT ((added_in_place_of_id = id) AND (added_in_place_of_id IS NOT NULL)))))));
ALTER TABLE "stewardship_ministry_request" ADD CONSTRAINT "stewardship_ministry_added_in_place_of_id_aa268172_fk_stewardsh" FOREIGN KEY ("added_in_place_of_id") REFERENCES "stewardship_ministry_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ministry_request_added_in_place_of_id_aa268172" ON "stewardship_ministry_request" ("added_in_place_of_id");
ALTER TABLE "stewardship_ministry_revision" ADD COLUMN "reviewed_ministry_duid" integer NULL;
ALTER TABLE "stewardship_ministry_revision" ADD COLUMN "decision_note" text DEFAULT ''::text NOT NULL;
ALTER TABLE "stewardship_ministry_revision" ADD CONSTRAINT "ministry_revision_reviewed" CHECK (((reviewed_ministry_duid IS NULL) OR (((outcome)::text = ANY (ARRAY[('joined'::character varying)::text, ('leave_confirmed'::character varying)::text])) AND (reviewed_ministry_duid > 0) AND ((state)::text = 'resolved'::text))));
ALTER TABLE "stewardship_ministry_revision" DROP CONSTRAINT "ministry_revision_outcome";
ALTER TABLE "stewardship_ministry_revision" ADD CONSTRAINT "ministry_revision_outcome" CHECK (((((outcome IS NULL) AND ((state)::text = ANY (ARRAY[('new'::character varying)::text, ('assigned'::character varying)::text, ('in_progress'::character varying)::text, ('cancelled'::character varying)::text]))) OR (((outcome)::text = ANY (ARRAY[('joined'::character varying)::text, ('leave_confirmed'::character varying)::text, ('declined'::character varying)::text, ('duplicate'::character varying)::text, ('other'::character varying)::text])) AND ((state)::text = 'resolved'::text)) OR (((outcome)::text = 'no_response'::text) AND ((state)::text = 'closed_no_response'::text))) AND ((NOT (((outcome)::text = 'other'::text) AND (outcome IS NOT NULL))) OR (NOT (notes = ''::text)))));

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
                 AND current_request.ministry_duid=NEW.ministry_duid
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
    IF (OLD.state NOT IN ('new','assigned','in_progress')
        AND NOT (current_user='pk_stewardship_web'
            AND OLD.state IN ('resolved','closed_no_response')
            AND (NEW.state='in_progress' OR (NEW.state='cancelled' AND OLD.parish_added))))
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
        IF NOT FOUND OR NEW.resolution_source_id IS NOT NULL OR NEW.superseded_by_id IS NOT NULL THEN
            RAISE EXCEPTION 'Ministry follow-up projection requires its paired history' USING ERRCODE='23514';
        END IF;
        NEW.resolved_at := CASE WHEN NEW.outcome IS NOT NULL THEN staff_at END;
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
                          AND decided.action=action_name) THEN
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

CREATE OR REPLACE FUNCTION public.stewardship_ministry_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid; asked jsonb;
    reopen boolean; ticked boolean;
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
    IF (NEW.assignee_id IS NOT NULL AND NOT
            stewardship_ministry_followup_authorized_v1(NEW.assignee_id,target.ministry_duid))
       OR (NEW.outcome='joined' AND target.action<>'join')
       OR (NEW.outcome='leave_confirmed' AND target.action<>'leave')
       OR NEW.contact_at>NEW.created_at
       -- Back to review: In progress with nothing carried forward, never once
       -- a refresh resolved it or Staff ticked it Entered in ParishSoft, and
       -- with a note when it reopens an ignored request. A request the parish
       -- added is withdrawn (cancelled) instead.
       OR (reopen AND (NEW.state<>CASE WHEN target.parish_added THEN 'cancelled' ELSE 'in_progress' END
           OR NEW.outcome IS NOT NULL OR NEW.reviewed_ministry_duid IS NOT NULL
           OR NEW.contact_channel IS NOT NULL
           OR target.resolution_source_id IS NOT NULL OR coalesce(ticked,false)
           OR (target.outcome NOT IN ('joined','leave_confirmed') AND btrim(NEW.decision_note)='')))
       OR (NOT reopen AND NEW.state='cancelled')
       -- Edit: Accept with another of the campaign's Ministries carried
       -- forward, which the actor may also follow up (a leader edits only
       -- into a Ministry they lead).
       OR (NEW.reviewed_ministry_duid IS NOT NULL AND (NEW.state<>'resolved'
           OR NEW.outcome IS DISTINCT FROM
               CASE WHEN target.action='join' THEN 'joined' ELSE 'leave_confirmed' END
           OR NEW.reviewed_ministry_duid=target.ministry_duid
           OR NOT coalesce(asked @> to_jsonb(NEW.reviewed_ministry_duid),false)
           OR NOT stewardship_ministry_followup_authorized_v1(NEW.actor_id,NEW.reviewed_ministry_duid)))
    THEN RAISE EXCEPTION 'Ministry follow-up change is not valid for this request'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_revision_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO target FROM stewardship_ministry_request WHERE id=NEW.request_id;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=target.submission_id;
    -- The request guard proved the exact projection when it advanced this
    -- version, the Ministry an Edit carries forward included (#1684). A
    -- later version in this transaction has its own revision.
    IF target.version<=NEW.expected_version
       OR (target.version=NEW.expected_version+1
           AND ROW(target.state,target.outcome,target.assignee_id,target.reviewed_ministry_duid)
               IS DISTINCT FROM ROW(NEW.state,NEW.outcome,NEW.assignee_id,NEW.reviewed_ministry_duid))
       OR NOT EXISTS(SELECT 1 FROM stewardship_audit_event a
           JOIN stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='ministry_request_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.expected_version,'after_version',NEW.expected_version+1,
                 'ministry_duid',target.ministry_duid))
    THEN RAISE EXCEPTION 'Ministry follow-up requires its projection and audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_followup_v1(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    viewer uuid, request_uuid uuid DEFAULT NULL,
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.timezone,cc.values,
        CASE WHEN c.state='archived' THEN cc.configuration_id
             ELSE sys.active_configuration_id END AS configuration_id,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    CROSS JOIN stewardship_system_configuration sys
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.*,s.promoted_at AS source_as_of,statement_timestamp() AS observed_at
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Widen before filtering, as the Ministry report does, so an unsupported
    -- configured ID cannot become an outage through predicate reordering.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        n.in_campaign
    FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign. A request for a Member no longer in
    -- ParishSoft (#1217) does not keep one listed (#1219), as it is never
    -- counted or listed under it.
    -- Staff and Administrators still get it where they list such requests:
    -- by id, and with the presence filters 'gone' and 'any'. The Ministry an
    -- Edit carried forward (#1684) counts as the request's too.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT d.duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
        CROSS JOIN LATERAL (VALUES (r.ministry_duid),(r.reviewed_ministry_duid)) d(duid)
        WHERE s.campaign_id=x.id AND s.mode='live' AND d.duid IS NOT NULL
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(d.duid)
          AND (r.entity_kind<>'member'
              OR (operational AND (request_uuid IS NOT NULL
                  OR coalesce(filters->>'presence','current') IN ('gone','any')))
              OR stewardship_member_source_status_v1(
                  x.source_id,r.entity_key,f.family_duid)='current')
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
), requests AS MATERIALIZED (
    -- A request is in the reader's scope by the Family's Ministry or by the
    -- one an Edit carried forward (#1684), so the leaders of both see it.
    -- Its latest version is found across every request first, so scope
    -- never changes which version is latest. A Ministry outside the
    -- reader's scope is still named, from the same snapshot.
    SELECT q.*,
        coalesce(m.name,(SELECT coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
                'Unavailable Ministry')
            FROM stewardship_snapshot_ministry sm
            JOIN stewardship_source_ministry p ON p.id=sm.payload_id
            WHERE sm.snapshot_id=x.source_id AND sm.source_key=q.ministry_duid::text),
            'Unavailable Ministry') AS ministry_name,
        coalesce(m.in_campaign,
            x.values->'ministry_duids' @> to_jsonb(q.ministry_duid)) AS in_campaign,
        CASE WHEN q.reviewed_ministry_duid IS NOT NULL THEN coalesce(e.name,
            (SELECT coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
                'Unavailable Ministry')
            FROM stewardship_snapshot_ministry sm
            JOIN stewardship_source_ministry p ON p.id=sm.payload_id
            WHERE sm.snapshot_id=x.source_id
              AND sm.source_key=q.reviewed_ministry_duid::text),
            'Unavailable Ministry') END AS reviewed_ministry_name
    FROM (
        SELECT r.*,s.submitted_at,f.family_duid,
            row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
        FROM source x
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
    ) q CROSS JOIN source x
    LEFT JOIN ministries m ON m.duid=q.ministry_duid
    LEFT JOIN ministries e ON e.duid=q.reviewed_ministry_duid
    WHERE m.duid IS NOT NULL OR e.duid IS NOT NULL
), named AS MATERIALIZED (
    -- A Member is named "Last, first" (UX conventions, "Family and Member
    -- names", #932), so the name sort orders by surname; the search also
    -- matches the given-name-first form shown before.
    SELECT r.*,coalesce(nullif(concat_ws(', ',
            nullif(stewardship_name_strip_v1(n.last_name),''),
            nullif(stewardship_name_strip_v1(n.first_name),'')),''),
            -- No name (NULL) for a Member the data no longer has in this
            -- Family: the page shows its ParishSoft status badge alone, and
            -- it sorts after the names (#1216). A present Member, or one the
            -- Family added, without a usable name reads as such.
            CASE WHEN r.entity_kind<>'member' OR p.id IS NOT NULL
                THEN 'Name not recorded in ParishSoft' END) AS member_name,
        coalesce(nullif(btrim(concat_ws(' ',n.first_name,n.last_name)),''),
            CASE WHEN r.entity_kind<>'member' OR p.id IS NOT NULL
                THEN 'Name not recorded in ParishSoft' END) AS member_search_name,
        -- "Cannot participate in any ministries" (#247) explains a leave.
        coalesce(s.answers#>ARRAY['service',
            CASE WHEN r.entity_kind='member' THEN 'members' ELSE 'proposed_members' END,
            r.entity_key,'cannot_serve']='true'::jsonb,false) AS cannot_serve,
        -- Whether ParishSoft still has the Member in this Family (#1217),
        -- worked out only for the rows the filters below keep; NULL for a
        -- Member the Family added on the form.
        CASE WHEN r.entity_kind='member' THEN stewardship_member_source_status_v1(
            x.source_id,r.entity_key,r.family_duid) END AS member_status
    FROM requests r CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    CROSS JOIN LATERAL (SELECT
        CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
            ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
        CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
            ELSE s.answers->'proposed_members'->r.entity_key->>'last_name' END
    ) n(first_name,last_name)
    WHERE CASE WHEN request_uuid IS NOT NULL THEN r.id=request_uuid ELSE
        ((filters->>'history')='all' OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'ministry')='' OR r.ministry_duid::text=(filters->>'ministry')
            OR r.reviewed_ministry_duid::text=(filters->>'ministry'))
        AND ((filters->>'action')='any' OR r.action=(filters->>'action'))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved'
                AND r.state IN ('new','assigned','in_progress')))
        AND ((filters->>'outcome')='any' OR r.outcome=(filters->>'outcome'))
        AND ((filters->>'assignee')='any'
            OR ((filters->>'assignee')='mine' AND r.assignee_id=viewer)
            OR ((filters->>'assignee')='unassigned' AND r.assignee_id IS NULL)
            OR r.assignee_id::text=(filters->>'assignee')) END
        -- A request for a Member no longer in ParishSoft (#1217) never
        -- reaches a Ministry leader, even by its own id. Staff and
        -- Administrators open one by id, list only those with the
        -- presence filter 'gone' (none by default), and every request
        -- with 'any' (Roster changes to enter). The standing is worked
        -- out last, only for rows the cheaper tests keep.
        AND CASE WHEN operational AND (request_uuid IS NOT NULL
                OR coalesce(filters->>'presence','current')='any') THEN true
            WHEN r.entity_kind<>'member'
                THEN NOT (operational AND request_uuid IS NULL
                    AND coalesce(filters->>'presence','current')='gone')
            ELSE (stewardship_member_source_status_v1(
                x.source_id,r.entity_key,r.family_duid)<>'current')
                =(operational AND request_uuid IS NULL
                    AND coalesce(filters->>'presence','current')='gone') END
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE request_uuid IS NOT NULL OR (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR position(lower((filters->>'search')) IN lower(member_search_name))>0
        OR position(lower((filters->>'search')) IN lower(ministry_name))>0
        OR position(lower((filters->>'search')) IN lower(coalesce(reviewed_ministry_name,'')))>0
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='ministry' THEN lower(ministry_name) END,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        submitted_at DESC,id) AS ordinal
    FROM filtered ORDER BY ordinal LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.version,r.ministry_duid,r.ministry_name,r.in_campaign,r.member_name,
        r.entity_kind,r.action,r.state,r.outcome,r.assignee_id,r.submitted_at,
        r.cannot_serve,r.member_status,
        r.reviewed_ministry_duid,r.reviewed_ministry_name,r.parish_added,r.added_in_place_of_id,
        r.resolved_at,r.resolution_source_id IS NOT NULL AS source_resolved,
        r.revision=1 AS latest,
        r.state IN ('new','assigned','in_progress') AS open,
        h.notes,h.email_contact_at,h.phone_contact_at,h.last_contact_at
    FROM page r LEFT JOIN LATERAL (
        SELECT (array_agg(v.notes ORDER BY c.depth,v.expected_version DESC))[1] AS notes,
            max(v.contact_at) FILTER (WHERE v.contact_channel='email') AS email_contact_at,
            max(v.contact_at) FILTER (WHERE v.contact_channel='phone') AS phone_contact_at,
            max(v.contact_at) AS last_contact_at
        FROM stewardship_ministry_workflow_chain_v1(r.id) c
        JOIN stewardship_ministry_revision v ON v.request_id=c.request_id
    ) h ON true
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at),
    'ministries',coalesce((SELECT jsonb_agg(jsonb_build_object('duid',duid,'name',name,'in_campaign',in_campaign)
        ORDER BY lower(name),duid) FROM ministries),'[]'::jsonb),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

DO $check$
BEGIN
    -- The new columns, with their types and defaults, and their checks.
    IF (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND (table_name,column_name,data_type,is_nullable,
            coalesce(column_default,'')) IN (
            ('stewardship_ministry_request','reviewed_ministry_duid','integer','YES',''),
            ('stewardship_ministry_request','parish_added','boolean','NO','false'),
            ('stewardship_ministry_request','added_in_place_of_id','uuid','YES',''),
            ('stewardship_ministry_revision','reviewed_ministry_duid','integer','YES',''),
            ('stewardship_ministry_revision','decision_note','text','NO','''''::text')))<>5
       OR (SELECT count(*) FROM pg_constraint
        WHERE conrelid IN ('public.stewardship_ministry_request'::regclass,
                'public.stewardship_ministry_revision'::regclass)
          AND conname IN ('ministry_request_reviewed','ministry_request_added',
              'ministry_revision_reviewed',
              'stewardship_ministry_added_in_place_of_id_aa268172_fk_stewardsh'))<>4
       OR NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_ministry_revision'::regclass
          AND conname='ministry_revision_outcome'
          AND pg_get_constraintdef(oid) LIKE '%''cancelled''%') THEN
        RAISE EXCEPTION 'Migration 0132 (Ministry review pipeline) did not add the request and revision fields';
    END IF;
    -- Each replaced function, once, invoker, with its new text.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND ((p.proname='stewardship_ministry_request_guard_v1'
                AND p.prosrc LIKE '%A request the parish adds needs Staff%'
                AND p.prosrc LIKE '%r.reviewed_ministry_duid IS NOT DISTINCT FROM NEW.reviewed_ministry_duid%')
            OR (p.proname='stewardship_ministry_submission_effects_v1'
                AND p.prosrc LIKE '%decided.state IN (''resolved'',''closed_no_response'')%')
            OR (p.proname='stewardship_ministry_revision_guard_v1'
                AND p.prosrc LIKE '%length(NEW.decision_note)>2000%'
                AND p.prosrc LIKE '%coalesce(target.reviewed_ministry_duid,target.ministry_duid)%')
            OR (p.proname='stewardship_ministry_revision_effect_v1'
                AND p.prosrc LIKE '%target.assignee_id,target.reviewed_ministry_duid)%')
            OR (p.proname='stewardship_ministry_followup_v1' AND p.provolatile='s'
                AND p.prosrc LIKE '%AS reviewed_ministry_name%'
                AND p.prosrc LIKE '%r.parish_added,r.added_in_place_of_id%')))<>5 THEN
        RAISE EXCEPTION 'Migration 0132 (Ministry review pipeline) did not replace the guards and selection';
    END IF;
    -- The guards still run as the triggers they were.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE NOT t.tgisinternal AND t.tgenabled<>'D'
          AND (t.tgrelid,t.tgname,p.proname) IN (
            ('public.stewardship_ministry_request'::regclass,'stewardship_ministry_request_guard',
                'stewardship_ministry_request_guard_v1'),
            ('public.stewardship_submission'::regclass,'stewardship_ministry_submission_effects',
                'stewardship_ministry_submission_effects_v1'),
            ('public.stewardship_ministry_revision'::regclass,'stewardship_ministry_revision_guard',
                'stewardship_ministry_revision_guard_v1'),
            ('public.stewardship_ministry_revision'::regclass,'stewardship_ministry_revision_effect',
                'stewardship_ministry_revision_effect_v1')))<>4 THEN
        RAISE EXCEPTION 'Migration 0132 (Ministry review pipeline) lost a Ministry trigger';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_request_guard_v1',
            'stewardship_ministry_submission_effects_v1','stewardship_ministry_revision_guard_v1',
            'stewardship_ministry_revision_effect_v1','stewardship_ministry_followup_v1'))
        IS DISTINCT FROM current_setting('stewardship.migration_0132_acl') THEN
        RAISE EXCEPTION 'Migration 0132 changed the Ministry functions'' grants or settings';
    END IF;
END
$check$;
