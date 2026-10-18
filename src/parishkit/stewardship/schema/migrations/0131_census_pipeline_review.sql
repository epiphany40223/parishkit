-- Frozen forward migration file 0131 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): census
-- changes adopt the review pipeline (#1683). This file is installed by its
-- Django migration in the responses app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- Before this file only an Administrator reviewed, and only an Automatic
-- Member change (0096, widened for Edit value by 0107 and for "Use the
-- Family's value anyway" by 0108). The review pipeline separates review
-- from update: Staff and Administrators review every census change, By
-- hand ones too, and only an Administrator starts an update run
-- (docs/specs/stewardship/review-pipeline/spec.md, decisions 1 and 2).
-- So this file replaces stewardship_proposal_review_guard_v1 (0108's, its
-- latest body) with these changes, and only these:
--   * the actor is a current Staff member or Administrator;
--   * the change is any census change the worklist lists: Automatic or By
--     hand, a Member's, a New Member's or the Family's (never report-only);
--   * a review records a decision, an edit or a resolution as before, and
--     now also a decision and an edit together when the decision is
--     approved (Edit is Accept with a value), with or without an accepted
--     base (an Edit on a Conflict accepts against ParishSoft's value);
--   * an edit's value has its field's shape: a phone record for a phone,
--     an address of its six parts with a non-blank street line for a home
--     or mailing address, text otherwise; the boolean fields (moved,
--     deceased, email opt-out) and a New Member take no edit;
--   * sending an ignored change back to review (decision unreviewed) needs
--     a note saying why, as 0060's Reopen always did.
-- Every other rule stays: a live response in a campaign that admits
-- changes, the change's current version, open only (pending, conflict or
-- failed; never queued for publication nor final), a conflict approved
-- only by resolving it, a resolution only on a conflict whose ParishSoft
-- value is known, at exactly that value and snapshot, a non-blank edit
-- different from the Family's answer, at most 1,024 bytes.
--
-- It also replaces stewardship_submission_effects_v1 (the baseline's body,
-- schema/guards.sql, which has the same new body) for the review
-- pipeline's resubmission rule: a Family's same answer again for a change
-- already Entered by hand or written to ParishSoft, which the download may
-- not show yet, keeps that change and needs no new row. Its completeness
-- check now admits a changed answer with no row of this response only when
-- the newest earlier change for that key is entered or published with this
-- very answer and the prior census response gave that answer too
-- (responses/proposals.py derives the same). Every other rule of
-- that body stays. It is not SECURITY DEFINER (the baseline has no ALTER for
-- it).
--
-- Nothing else is replaced. 0108's deferred effect check already requires
-- the proposal to carry the row's decision and edit fields at the next
-- version, pending after a resolution, with the census_change_updated audit
-- event; 0107's edit guard already ties any edit-field change to a review
-- row at the current version; and the derived guard's web review branch
-- (0113's body) already admits a paired review on any handling. No table,
-- column or index changes, and no existing row is touched. The function is
-- not SECURITY DEFINER (0096 created it without one), so nothing else is
-- restored. The file ends with a DO block that refuses to commit unless both
-- new bodies are installed with their attributes, the review guard with its
-- trigger.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

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
       -- A current Staff member or Administrator (review pipeline,
       -- decision 1); only an Administrator starts an update run.
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,false)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM proposal.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       -- Every census change the worklist lists, Automatic or By hand
       -- (decision 2); report-only rows belong to other streams.
       OR proposal.handling NOT IN ('api','manual')
       -- Open only: never while queued for publication, never once final.
       OR proposal.execution NOT IN ('pending','conflict','failed')
       -- A conflict is approved only by resolving it (ADM-09.03, below).
       OR (NEW.decision='approved' AND proposal.execution='conflict' AND NOT resolved)
       -- A review records something: a decision, an edit or a resolution.
       OR NOT (decided OR edited OR resolved)
       -- A decision and an edit together only when the decision accepts:
       -- Edit is Accept with a value (review pipeline, "Reviewed stage").
       OR (decided AND edited AND NEW.decision<>'approved')
       -- A resolution ("Use the Family's value anyway", ADM-09.03; the
       -- worklist's Accept or Edit on a Conflict) accepts ParishSoft's
       -- current value, exactly as the proposal holds it and from the
       -- snapshot it came from, as the base publication merges against, and
       -- approves the change. Only on a conflict whose ParishSoft value is
       -- known.
       OR (resolved AND (proposal.execution<>'conflict'
           OR NOT proposal.current_available OR NEW.decision<>'approved'
           OR NEW.accepted_value IS DISTINCT FROM proposal.current_value
           OR NEW.accepted_source_id IS DISTINCT FROM proposal.current_source_id))
       -- Every other review accepts nothing.
       OR (NOT resolved AND ROW(NEW.accepted_value,NEW.accepted_source_id)
           IS DISTINCT FROM ROW(NULL::jsonb,NULL::uuid))
       -- Sending an ignored change back to review says why.
       OR (proposal.decision='ignored' AND NEW.decision='unreviewed'
           AND NEW.note !~ '\S')
    THEN RAISE EXCEPTION 'Census change review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    IF edited AND NEW.admin_value_set THEN
        -- No value can be typed for a yes-or-no answer or a whole New Member:
        -- those are accepted or ignored as the Family gave them.
        IF proposal.field IN ('moved_household','deceased_status','email_opt_out','new_member')
        THEN RAISE EXCEPTION 'Census change edit is not offered for this field'
            USING ERRCODE='23514'; END IF;
        -- A phone edit is a phone record, an address edit an address of its
        -- parts, any other edit text. (The type is worked out first: an IF
        -- condition ends at its first THEN.)
        edit_type := CASE WHEN proposal.field IN ('home_phone','mobile_phone','work_phone',
                'home_address','mailing_address')
            THEN 'object' ELSE 'string' END;
        IF NEW.admin_value IS NULL OR NEW.admin_value='null'::jsonb
           OR jsonb_typeof(NEW.admin_value) IS DISTINCT FROM edit_type
           OR octet_length(NEW.admin_value::text)>1024
        THEN RAISE EXCEPTION 'Census change edit has no value of its field''s shape'
            USING ERRCODE='23514'; END IF;
        -- The comparison validates the shape (a phone record is its own
        -- display's, an address has exactly its six text parts, a date an
        -- ISO civil date) and raises on a bad one. Its key is trimmed of the
        -- full whitespace set, so a blank edit (a tab, a no-break or
        -- ideographic space) has an empty key; a phone's key is not text, so
        -- its display is read the same way, and an address is blank when its
        -- street line is.
        edit_key := public.stewardship_response_comparison_v1(proposal.field,NEW.admin_value);
        edit_text := CASE
            WHEN proposal.field IN ('home_phone','mobile_phone','work_phone')
            THEN public.stewardship_response_comparison_v1('nickname',NEW.admin_value->'display')
            WHEN proposal.field IN ('home_address','mailing_address')
            THEN edit_key::jsonb->>'line1'
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

CREATE OR REPLACE FUNCTION public.stewardship_submission_effects_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    answer record; source_field jsonb; submitted_key text;
    prior public.stewardship_proposed_change;
    latest_text text; prior_text text; replacement_id uuid;
    prior_followup public.stewardship_additional_information;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_submission WHERE id=NEW.id) THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_family_form_baseline
                  WHERE id=NEW.baseline_id AND state='submitted')
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_submission_receipt WHERE submission_id=NEW.id)
       OR EXISTS (
           SELECT source_id FROM (VALUES (NEW.reviewed_source_id),(NEW.validation_source_id)) inputs(source_id)
           WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin
               WHERE parent_kind='submission' AND parent_id=NEW.id AND snapshot_id=inputs.source_id AND expires_at IS NULL)
       ) THEN
        RAISE EXCEPTION 'Submission requires atomic baseline, receipt and source protection' USING ERRCODE='23514';
    END IF;
    IF NEW.mode='live' AND (
        NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign family
            WHERE family.id=NEW.family_id
              AND family.first_live_submission_id=(SELECT id FROM public.stewardship_submission
                WHERE family_id=NEW.family_id AND mode='live' ORDER BY family_version LIMIT 1)
              AND family.effective_submission_id=(SELECT id FROM public.stewardship_submission
                WHERE family_id=NEW.family_id AND mode='live' ORDER BY family_version DESC LIMIT 1))
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign
                       WHERE id=NEW.campaign_id AND first_live_submission_at IS NOT NULL)
        OR EXISTS (SELECT scope FROM (VALUES ('historical'),('current')) populations(scope)
            WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_fact_demand
                WHERE campaign_id=NEW.campaign_id AND population_scope=populations.scope
                  AND requested_submission_watermark >= NEW.campaign_sequence))
    ) THEN
        RAISE EXCEPTION 'Live response requires atomic selectors and fact demand' USING ERRCODE='23514';
    END IF;
    -- Child guards validate rows that exist. This parent guard also detects a
    -- missing derivation owner, including replacement/withdrawal with no new row.
    IF NEW.answers->'family' ? 'home_address' THEN
        FOR answer IN
            SELECT 'family' AS entity, family.family_duid::text AS identity,
                   field.key AS field, field.value AS value
            FROM public.stewardship_family_campaign family,
                 jsonb_each(NEW.answers->'family') field
            WHERE family.id=NEW.family_id
              AND field.key IN ('home_address','mailing_address','email_opt_out')
            UNION ALL
            SELECT 'member', member.key, field.key, field.value
            FROM jsonb_each(NEW.answers->'members') member,
                 jsonb_each(member.value) field
            WHERE field.key <> 'confirmed'
              AND NOT (field.key='death_date' AND field.value='null'::jsonb)
            UNION ALL
            SELECT 'proposed_member', member.key, 'new_member', member.value
            FROM jsonb_each(NEW.answers->'proposed_members') member
        LOOP
            source_field := CASE answer.entity
                WHEN 'family' THEN public.stewardship_response_household_source_v1(
                    NEW.validation_source_id,NEW.family_id,answer.identity,answer.field)
                WHEN 'member' THEN public.stewardship_response_field_source_v1(
                    NEW.validation_source_id,NEW.family_id,answer.identity,answer.field)
                ELSE jsonb_build_object('available',false,'value',NULL) END;
            submitted_key := public.stewardship_response_comparison_v1(answer.field,answer.value);
            IF NOT ((source_field->'available'='true'::jsonb AND submitted_key IS NOT DISTINCT FROM
                     public.stewardship_response_comparison_v1(answer.field,source_field->'value'))
                    OR (source_field->'available'='false'::jsonb AND answer.value='null'::jsonb))
               AND NOT EXISTS (SELECT 1 FROM public.stewardship_proposed_change proposal
                   WHERE proposal.submission_id=NEW.id AND proposal.entity_kind=answer.entity
                     AND proposal.entity_key=answer.identity AND proposal.field=answer.field)
               -- The same answer again for a change already entered by hand
               -- or published, which the download may not show yet, keeps
               -- that change and makes no row (the review pipeline's
               -- resubmission rule, #1683): the newest earlier change for
               -- the key is entered or published with this very answer, and
               -- the prior census response gave it too. Without that, an
               -- answer set back to ParishSoft's value in between (which
               -- makes no row) would let the old answer again pass with no
               -- row, where responses/proposals.py makes a new one. Not for
               -- a terminal request, which is no longer presented once
               -- entered, so asking for it again is a new request.
               AND ((answer.entity='member'
                     AND answer.field IN ('moved_household','deceased_status','death_date'))
                    OR NOT EXISTS (SELECT 1 FROM (
                   SELECT proposal.execution, proposal.submitted_value
                   FROM public.stewardship_proposed_change proposal
                   JOIN public.stewardship_submission history ON history.id=proposal.submission_id
                   WHERE history.family_id=NEW.family_id AND history.campaign_id=NEW.campaign_id
                     AND history.mode=NEW.mode
                     AND history.rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
                     AND history.family_version<NEW.family_version
                     AND proposal.entity_kind=answer.entity
                     AND proposal.entity_key=answer.identity AND proposal.field=answer.field
                   ORDER BY history.family_version DESC LIMIT 1) latest
                   JOIN public.stewardship_submission census_prior
                     ON census_prior.id=public.stewardship_response_census_anchor_v1(NEW.prior_submission_id)
                   CROSS JOIN LATERAL (SELECT census_prior.answers #> CASE answer.entity
                         WHEN 'family' THEN ARRAY['family',answer.field]
                         WHEN 'member' THEN ARRAY['members',answer.identity,answer.field]
                         ELSE ARRAY['proposed_members',answer.identity] END AS value) given
                   WHERE latest.execution IN ('published','resolved_external')
                     AND public.stewardship_response_comparison_v1(answer.field,latest.submitted_value)
                         IS NOT DISTINCT FROM submitted_key
                     AND given.value IS NOT NULL
                     AND public.stewardship_response_comparison_v1(answer.field,given.value)
                         IS NOT DISTINCT FROM submitted_key))
            THEN
                RAISE EXCEPTION 'Submitted census change requires atomic derived work' USING ERRCODE='23514';
            END IF;
        END LOOP;
        FOR prior IN
            SELECT DISTINCT ON (proposal.entity_kind,proposal.entity_key,proposal.field) proposal.*
            FROM public.stewardship_proposed_change proposal
            JOIN public.stewardship_submission history ON history.id=proposal.submission_id
            JOIN public.stewardship_submission previous ON previous.id=NEW.prior_submission_id
            WHERE history.family_id=NEW.family_id AND history.campaign_id=NEW.campaign_id
              AND history.mode=NEW.mode
              AND history.rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
              AND history.family_version<=previous.family_version
              AND (history.id=public.stewardship_response_census_anchor_v1(previous.id)
                   OR (proposal.entity_kind='member'
                       AND proposal.field IN ('moved_household','deceased_status','death_date')))
            ORDER BY proposal.entity_kind,proposal.entity_key,proposal.field,history.family_version DESC
        LOOP
            IF prior.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded') THEN
                CONTINUE;
            END IF;
            -- Source removal is not withdrawal of independent terminal work.
            IF prior.entity_kind='member' AND prior.field IN ('moved_household','deceased_status','death_date') THEN
                IF NOT (NEW.answers->'members' ? prior.entity_key) THEN CONTINUE; END IF;
                IF prior.field='death_date'
                   AND coalesce(NEW.answers#>ARRAY['members',prior.entity_key,'death_date'],'null'::jsonb)='null'::jsonb
                   AND (SELECT proposal.execution FROM public.stewardship_proposed_change proposal
                       JOIN public.stewardship_submission history ON history.id=proposal.submission_id
                       JOIN public.stewardship_submission previous ON previous.id=NEW.prior_submission_id
                       WHERE history.family_id=NEW.family_id AND history.campaign_id=NEW.campaign_id
                         AND history.mode=NEW.mode
                         AND history.rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
                         AND history.family_version<=previous.family_version
                         AND proposal.entity_kind='member' AND proposal.entity_key=prior.entity_key
                         AND proposal.field='deceased_status'
                       ORDER BY history.family_version DESC LIMIT 1)
                       IN ('published','resolved_upstream','resolved_external') THEN CONTINUE; END IF;
            END IF;
            RAISE EXCEPTION 'New census response must reconcile earlier census work' USING ERRCODE='23514';
        END LOOP;
    END IF;
    IF NEW.mode='live' THEN
        SELECT answers->>'additional_information' INTO latest_text
        FROM public.stewardship_submission WHERE family_id=NEW.family_id AND mode='live'
        ORDER BY family_version DESC LIMIT 1;
        IF (SELECT count(*) FROM public.stewardship_additional_information item
            JOIN public.stewardship_submission response ON response.id=item.submission_id
            WHERE response.family_id=NEW.family_id AND item.disposition='current_actionable')
            <> (CASE WHEN latest_text='' THEN 0 ELSE 1 END)
           OR EXISTS (SELECT 1 FROM public.stewardship_additional_information item
               JOIN public.stewardship_submission response ON response.id=item.submission_id
               WHERE response.family_id=NEW.family_id AND item.disposition='current_actionable'
                 AND item.text IS DISTINCT FROM latest_text)
           OR (NEW.answers->>'additional_information'<>''
               AND NEW.answers->>'additional_information' IS DISTINCT FROM (
                   SELECT answers->>'additional_information' FROM public.stewardship_submission
                   WHERE id=NEW.prior_submission_id)
               AND NOT EXISTS (SELECT 1 FROM public.stewardship_additional_information
                   WHERE submission_id=NEW.id)) THEN
            RAISE EXCEPTION 'Live response requires atomic follow-up derivation' USING ERRCODE='23514';
        END IF;
        SELECT answers->>'additional_information' INTO prior_text
        FROM public.stewardship_submission WHERE id=NEW.prior_submission_id;
        IF NEW.answers->>'additional_information'=coalesce(prior_text,'') THEN
            IF EXISTS (SELECT 1 FROM public.stewardship_additional_information WHERE submission_id=NEW.id) THEN
                RAISE EXCEPTION 'Repeated text must preserve existing follow-up history' USING ERRCODE='23514';
            END IF;
        ELSIF coalesce(prior_text,'')<>'' THEN
            -- Check the actual historical transition, not only the latest queue
            -- selection: a replacement is supersession, never a withdrawal.
            SELECT item.* INTO prior_followup
            FROM public.stewardship_additional_information item
            JOIN public.stewardship_submission history ON history.id=item.submission_id
            JOIN public.stewardship_submission previous ON previous.id=NEW.prior_submission_id
            WHERE history.family_id=NEW.family_id AND history.mode='live'
              AND history.family_version<=previous.family_version
            ORDER BY history.family_version DESC LIMIT 1;
            SELECT id INTO replacement_id FROM public.stewardship_additional_information WHERE submission_id=NEW.id;
            IF prior_followup.id IS NULL OR prior_followup.text IS DISTINCT FROM prior_text
               OR prior_followup.disposition IS DISTINCT FROM
                    (CASE WHEN NEW.answers->>'additional_information'='' THEN 'withdrawn' ELSE 'superseded' END)
               OR prior_followup.replacement_id IS DISTINCT FROM replacement_id THEN
                RAISE EXCEPTION 'Follow-up replacement requires exact linked history' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc NOT LIKE '%stewardship_export_authorized_v1(NEW.actor_id,true)%'
              AND prosrc LIKE '%proposal.handling NOT IN (''api'',''manual'')%'
              AND prosrc NOT LIKE '%proposal.entity_kind<>''member''%'
              AND prosrc LIKE '%OR (decided AND edited AND NEW.decision<>''approved'')%'
              AND prosrc LIKE '%Census change edit is not offered for this field%'
              AND prosrc LIKE '%THEN edit_key::jsonb->>''line1''%'
              AND prosrc LIKE '%AND NEW.decision=''unreviewed''%'
              AND prosrc LIKE '%Census change edit must differ from the Family%'
              AND prosrc LIKE '%proposal.execution NOT IN (''pending'',''conflict'',''failed'')%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_review'::regclass
              AND t.tgname='stewardship_proposal_review_guard'
              AND t.tgfoid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_review_guard_v1 lacks the review pipeline rules';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_submission_effects_v1()'::regprocedure
              AND prosrc LIKE '%WHERE latest.execution IN (''published'',''resolved_external'')%'
              AND prosrc LIKE '%ON census_prior.id=public.stewardship_response_census_anchor_v1(NEW.prior_submission_id)%'
              AND prosrc LIKE '%AND public.stewardship_response_comparison_v1(answer.field,given.value)%'
              AND prosrc LIKE '%Submitted census change requires atomic derived work%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
    THEN
        RAISE EXCEPTION 'stewardship_submission_effects_v1 lacks the resubmission rule';
    END IF;
END $check$;
