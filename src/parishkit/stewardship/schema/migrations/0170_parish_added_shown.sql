-- Frozen forward migration file 0170 (the repository-wide file sequence;
-- the number is reserved for this change): a Family that submits a change
-- the parish added unchanged, as its reviewed form drew it in the field,
-- makes no census change of its own, and one that edits it always makes a
-- Family change To review (#2008; review-pipeline spec, "What the reviewed
-- form shows"). This file is installed by its Django migration in the
-- responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that each replaced function's copy here equals the fresh-install
-- baseline's (schema/guards.sql).
--
-- The reviewed form now records a shown item for a field with a standing
-- census change the parish added (census Add, #1710): {"stream":"census",
-- entity, key, field, "item": <the addition's id>, "version": null,
-- "decision": null, "shown": "parish"}, and draws the added value in the
-- field. A response that leaves that value as shown makes no row for the
-- field and leaves an earlier change of the field as it is (an Ignored
-- change the addition was added in place of stays Ignored), where the
-- submission check refused both ('Submitted census change requires atomic
-- derived work', 'New census response must reconcile earlier census
-- work'). Any other answer is an ordinary Family change To review (the
-- Administrator's decision 2), even ParishSoft's value, which the derived
-- guard refused ('Unchanged values cannot create actionable proposals') and
-- the submission check let go with no row. Replaced, copied whole from
-- their latest bodies with these changes and only these:
--   stewardship_submission_effects_v1      (0160's, #1895; guards.sql has
--       the same body)
--       * an answer the reviewed form's 'parish' entry shows, naming this
--         Family's addition for that field, not withdrawn, with that value,
--         is admitted with no row;
--       * so is an earlier open change of that field, unreplaced;
--       * under such an entry any other answer requires its row.
--   stewardship_response_derived_guard_v1  (0163's, #1909; guards.sql has
--       the same body)
--       * a new change whose value is ParishSoft's is admitted when its
--         response's 'parish' entry for the field names this Family's
--         addition, not withdrawn, with another value.
-- Every other rule is unchanged, and an entry names an addition only with
-- 'parish', so no earlier entry's meaning changes. Nothing already stored
-- is rewritten (no table, column, constraint, index or row changes).
-- Neither function is SECURITY DEFINER, before or after, so there is no
-- ALTER FUNCTION to repeat; both run as the web login, which already reads
-- both addition tables (0129). CREATE OR REPLACE keeps their owner, grants
-- and settings, which the DO block below compares with those recorded
-- before the replacement, after checking the new bodies. Reversing needs
-- its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants, settings and definer flags before the
-- replacement (with how many definitions there are), compared in the DO
-- block. A transaction-local setting holds them: the migration login has no
-- TEMP privilege.
SELECT set_config('stewardship.migration_0170_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||p.prosecdef::text
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public'
      AND p.proname IN ('stewardship_submission_effects_v1','stewardship_response_derived_guard_v1')), true);

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
    -- What the form showed (#1705) is its baseline's, copied unchanged: the
    -- reviewed rules below and the derived and Ministry guards trust it.
    IF ROW(NEW.reviewed_view,NEW.shown_items) IS DISTINCT FROM (
        SELECT ROW(baseline.reviewed_view,baseline.shown_items)
        FROM public.stewardship_family_form_baseline baseline WHERE baseline.id=NEW.baseline_id) THEN
        RAISE EXCEPTION 'Submission must keep what its form showed' USING ERRCODE='23514';
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
               -- A response drawn as the reviewed form (#1705) left this
               -- answer as the form showed it (the Family's answer or the
               -- reviewer's value of a change in force on an earlier
               -- response of this Family): that change stays as it is and
               -- no row is made (reviewed rule 1; responses/proposals.py
               -- derives the same).
               AND NOT (NEW.reviewed_view AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
                   JOIN public.stewardship_proposed_change item ON item.id::text=shown->>'item'
                   JOIN public.stewardship_submission history ON history.id=item.submission_id
                   WHERE shown->>'stream'='census'
                     AND ROW(shown->>'entity',shown->>'key',shown->>'field')
                         =ROW(answer.entity,answer.identity,answer.field)
                     AND ROW(item.entity_kind,item.entity_key,item.field)
                         =ROW(answer.entity,answer.identity,answer.field)
                     AND history.family_id=NEW.family_id AND history.campaign_id=NEW.campaign_id
                     AND history.mode=NEW.mode
                     AND history.family_version<NEW.family_version
                     AND item.execution NOT IN ('cancelled','superseded')
                     AND ((shown->>'shown'='family'
                           AND public.stewardship_response_comparison_v1(answer.field,item.submitted_value)
                               IS NOT DISTINCT FROM submitted_key)
                          OR (shown->>'shown'='reviewer' AND EXISTS (
                              SELECT 1 FROM public.stewardship_proposal_review edit
                              JOIN public.stewardship_proposed_change edited ON edited.id=edit.proposal_id
                              JOIN public.stewardship_submission edited_response
                                  ON edited_response.id=edited.submission_id
                              WHERE edit.id::text=shown->>'decision' AND edit.admin_value_set
                                AND ROW(edited.entity_kind,edited.entity_key,edited.field)
                                    =ROW(item.entity_kind,item.entity_key,item.field)
                                AND edited_response.family_id=NEW.family_id
                                AND public.stewardship_response_comparison_v1(answer.field,edit.admin_value)
                                    IS NOT DISTINCT FROM submitted_key)))))
               -- A change the parish added (#2008) that a response drawn as
               -- the reviewed form left as shown, the added value in its
               -- field: no Family change is made (reviewed rule 1;
               -- responses/proposals.py derives the same). The entry names
               -- this Family's addition for this very field, not withdrawn
               -- (withdrawn is final), whose value is the answer.
               AND NOT (NEW.reviewed_view AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
                   JOIN public.stewardship_census_addition addition ON addition.id::text=shown->>'item'
                   WHERE shown->>'stream'='census' AND shown->>'shown'='parish'
                     AND ROW(shown->>'entity',shown->>'key',shown->>'field')
                         =ROW(answer.entity,answer.identity,answer.field)
                     AND ROW(addition.entity_kind,addition.entity_key,addition.field)
                         =ROW(answer.entity,answer.identity,answer.field)
                     AND addition.family_id=NEW.family_id
                     AND NOT EXISTS (SELECT 1 FROM public.stewardship_census_addition_resolution step
                         WHERE step.addition_id=addition.id AND step.action='withdrawn')
                     AND public.stewardship_response_comparison_v1(answer.field,addition.value)
                         IS NOT DISTINCT FROM submitted_key))
            THEN
                RAISE EXCEPTION 'Submitted census change requires atomic derived work' USING ERRCODE='23514';
            END IF;
            -- Under that same 'parish' entry any other answer is an
            -- ordinary Family change To review (#2008, the Administrator's
            -- decision 2), so it needs its row even where it is
            -- ParishSoft's value or an Ignored change's (the rules above
            -- would admit none); responses/proposals.py makes the same.
            IF NEW.reviewed_view
               AND NOT EXISTS (SELECT 1 FROM public.stewardship_proposed_change proposal
                   WHERE proposal.submission_id=NEW.id AND proposal.entity_kind=answer.entity
                     AND proposal.entity_key=answer.identity AND proposal.field=answer.field)
               AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
                   JOIN public.stewardship_census_addition addition ON addition.id::text=shown->>'item'
                   WHERE shown->>'stream'='census' AND shown->>'shown'='parish'
                     AND ROW(shown->>'entity',shown->>'key',shown->>'field')
                         =ROW(answer.entity,answer.identity,answer.field)
                     AND ROW(addition.entity_kind,addition.entity_key,addition.field)
                         =ROW(answer.entity,answer.identity,answer.field)
                     AND addition.family_id=NEW.family_id
                     AND NOT EXISTS (SELECT 1 FROM public.stewardship_census_addition_resolution step
                         WHERE step.addition_id=addition.id AND step.action='withdrawn')
                     AND public.stewardship_response_comparison_v1(answer.field,addition.value)
                         IS DISTINCT FROM submitted_key)
            THEN
                RAISE EXCEPTION 'An edited census change the parish added requires a Family change' USING ERRCODE='23514';
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
                       AND proposal.field IN ('moved_household','deceased_status','death_date'))
                   -- An open change kept on an earlier response (#1895).
                   OR proposal.id=(SELECT newest.id FROM (
                           SELECT kept.id, kept.execution, kept.submitted_value
                           FROM public.stewardship_proposed_change kept
                           JOIN public.stewardship_submission kept_response ON kept_response.id=kept.submission_id
                           JOIN public.stewardship_submission kept_prior ON kept_prior.id=previous.id
                           WHERE kept_response.family_id=kept_prior.family_id
                             AND kept_response.campaign_id=kept_prior.campaign_id
                             AND kept_response.mode=kept_prior.mode
                             AND kept_response.rehearsal_epoch_id IS NOT DISTINCT FROM kept_prior.rehearsal_epoch_id
                             AND kept_response.family_version<=kept_prior.family_version
                             AND ROW(kept.entity_kind,kept.entity_key,kept.field)=ROW(proposal.entity_kind,proposal.entity_key,proposal.field)
                           ORDER BY kept_response.family_version DESC LIMIT 1) newest
                       JOIN public.stewardship_submission kept_census
                           ON kept_census.id=public.stewardship_response_census_anchor_v1(previous.id)
                       CROSS JOIN LATERAL (SELECT kept_census.answers #> CASE proposal.entity_kind
                             WHEN 'family' THEN ARRAY['family',proposal.field]
                             ELSE ARRAY['members',proposal.entity_key,proposal.field] END AS value) given
                       WHERE proposal.entity_kind IN ('family','member')
                         AND newest.execution NOT IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                         AND given.value IS NOT NULL
                         AND public.stewardship_response_comparison_v1(proposal.field,given.value)
                             IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(proposal.field,newest.submitted_value)))
            ORDER BY proposal.entity_kind,proposal.entity_key,proposal.field,history.family_version DESC
        LOOP
            IF prior.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded') THEN
                CONTINUE;
            END IF;
            -- Strict phase order (#1895): the same answer again for an open
            -- change ParishSoft already holds keeps it as it is, its stage
            -- and decision, until its review and step (the resubmission
            -- rule 1; rule 3 withdraws only an item whose previous answer
            -- ParishSoft does not hold). responses/proposals.py derives the
            -- same; a reviewed form's own rules are above and below.
            IF NOT NEW.reviewed_view AND prior.entity_kind IN ('family','member') AND EXISTS (
                SELECT 1 FROM (SELECT NEW.answers #> CASE prior.entity_kind
                      WHEN 'family' THEN ARRAY['family',prior.field]
                      ELSE ARRAY['members',prior.entity_key,prior.field] END AS value,
                    CASE prior.entity_kind
                      WHEN 'family' THEN public.stewardship_response_household_source_v1(
                          NEW.validation_source_id,NEW.family_id,prior.entity_key,prior.field)
                      ELSE public.stewardship_response_field_source_v1(
                          NEW.validation_source_id,NEW.family_id,prior.entity_key,prior.field) END AS source) held
                WHERE held.value IS NOT NULL AND held.source->'available'='true'::jsonb
                  AND public.stewardship_response_comparison_v1(prior.field,held.value)
                      IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(prior.field,prior.submitted_value)
                  AND public.stewardship_response_comparison_v1(prior.field,held.value)
                      IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(prior.field,held.source->'value')) THEN
                CONTINUE;
            END IF;
            -- A change a reviewed form (#1705) showed, which this response
            -- left as shown, stays as it is (reviewed rule 1): its answer
            -- equals the Family's answer or the reviewer's value the form
            -- showed, or, for an Ignored change the form showed as the
            -- baseline, the response gives no answer for it (a hidden New
            -- Member, a request shown as ParishSoft has the Member) or the
            -- baseline's own value in the form's snapshot.
            IF NEW.reviewed_view AND EXISTS (
                SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
                CROSS JOIN LATERAL (SELECT NEW.answers #> CASE prior.entity_kind
                      WHEN 'family' THEN ARRAY['family',prior.field]
                      WHEN 'member' THEN ARRAY['members',prior.entity_key,prior.field]
                      ELSE ARRAY['proposed_members',prior.entity_key] END AS value) given
                WHERE shown->>'stream'='census' AND shown->>'item'=prior.id::text
                  AND CASE shown->>'shown'
                      WHEN 'family' THEN given.value IS NOT NULL
                          AND public.stewardship_response_comparison_v1(prior.field,given.value)
                              IS NOT DISTINCT FROM
                              public.stewardship_response_comparison_v1(prior.field,prior.submitted_value)
                      WHEN 'reviewer' THEN given.value IS NOT NULL AND EXISTS (
                          SELECT 1 FROM public.stewardship_proposal_review edit
                          JOIN public.stewardship_proposed_change edited ON edited.id=edit.proposal_id
                          JOIN public.stewardship_submission edited_response
                              ON edited_response.id=edited.submission_id
                          WHERE edit.id::text=shown->>'decision' AND edit.admin_value_set
                            AND ROW(edited.entity_kind,edited.entity_key,edited.field)
                                =ROW(prior.entity_kind,prior.entity_key,prior.field)
                            AND edited_response.family_id=NEW.family_id
                            AND public.stewardship_response_comparison_v1(prior.field,edit.admin_value)
                                IS NOT DISTINCT FROM
                                public.stewardship_response_comparison_v1(prior.field,given.value))
                      WHEN 'baseline' THEN given.value IS NULL
                          OR (prior.entity_kind='member' AND EXISTS (
                              SELECT 1 FROM (SELECT public.stewardship_response_field_source_v1(
                                  NEW.reviewed_source_id,NEW.family_id,prior.entity_key,prior.field) AS source) b
                              WHERE b.source->'available'='true'::jsonb
                                AND public.stewardship_response_comparison_v1(prior.field,given.value)
                                    IS NOT DISTINCT FROM
                                    public.stewardship_response_comparison_v1(prior.field,b.source->'value')))
                      ELSE false END) THEN
                CONTINUE;
            END IF;
            -- An earlier change of a field whose change the parish added
            -- (#2008) this response left as shown stays as it is, its stage
            -- and decision (an Ignored change it was added in place of stays
            -- Ignored): the answer is the added value of a shown 'parish'
            -- entry naming this Family's addition for the field, not
            -- withdrawn. responses/proposals.py derives the same.
            IF NEW.reviewed_view AND prior.entity_kind IN ('family','member') AND EXISTS (
                SELECT 1 FROM jsonb_array_elements(NEW.shown_items) shown
                JOIN public.stewardship_census_addition addition ON addition.id::text=shown->>'item'
                CROSS JOIN LATERAL (SELECT NEW.answers #> CASE prior.entity_kind
                      WHEN 'family' THEN ARRAY['family',prior.field]
                      ELSE ARRAY['members',prior.entity_key,prior.field] END AS value) given
                WHERE shown->>'stream'='census' AND shown->>'shown'='parish'
                  AND ROW(shown->>'entity',shown->>'key',shown->>'field')
                      =ROW(prior.entity_kind,prior.entity_key,prior.field)
                  AND ROW(addition.entity_kind,addition.entity_key,addition.field)
                      =ROW(prior.entity_kind,prior.entity_key,prior.field)
                  AND addition.family_id=NEW.family_id
                  AND NOT EXISTS (SELECT 1 FROM public.stewardship_census_addition_resolution step
                      WHERE step.addition_id=addition.id AND step.action='withdrawn')
                  AND given.value IS NOT NULL
                  AND public.stewardship_response_comparison_v1(prior.field,given.value)
                      IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(prior.field,addition.value)) THEN
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
            -- So may an open change ParishSoft already holds that the Family
            -- answered again unchanged (strict phase order, #1895): it stays
            -- on its earlier response while each later census response gives
            -- its answer, the newest change for the field and still open.
            SELECT candidate.* INTO predecessor FROM public.stewardship_proposed_change candidate
            JOIN public.stewardship_submission earlier ON earlier.id=candidate.submission_id
            JOIN public.stewardship_submission prior ON prior.id=response.prior_submission_id
            WHERE earlier.family_id=response.family_id AND earlier.campaign_id=response.campaign_id
              AND earlier.mode=response.mode
              AND earlier.rehearsal_epoch_id IS NOT DISTINCT FROM response.rehearsal_epoch_id
              AND earlier.family_version<=prior.family_version
              AND (earlier.id=public.stewardship_response_census_anchor_v1(prior.id) OR (NEW.entity_kind='member'
                   AND NEW.field IN ('moved_household','deceased_status','death_date'))
                   -- A change the prior census response, drawn as the reviewed
                   -- form (#1705), showed and left as shown, the Family's answer or
                   -- a reviewer's Edit (#1909): it stays on its earlier response,
                   -- and effective.proposal_index finds it for the next response
                   -- (_shown_still_answered), so a response not drawn as the
                   -- reviewed form carries it or replaces it.
                   OR EXISTS (SELECT 1 FROM public.stewardship_submission shown_census
                       CROSS JOIN LATERAL jsonb_array_elements(shown_census.shown_items) shown
                       CROSS JOIN LATERAL (SELECT shown_census.answers #> CASE NEW.entity_kind
                             WHEN 'family' THEN ARRAY['family',NEW.field]
                             WHEN 'member' THEN ARRAY['members',NEW.entity_key,NEW.field]
                             ELSE ARRAY['proposed_members',NEW.entity_key] END AS value) given
                       WHERE shown_census.id=public.stewardship_response_census_anchor_v1(prior.id)
                         AND shown_census.reviewed_view
                         AND shown->>'stream'='census' AND shown->>'item'=candidate.id::text
                         AND candidate.execution NOT IN ('cancelled','superseded')
                         AND CASE shown->>'shown'
                             WHEN 'baseline' THEN true
                             WHEN 'family' THEN given.value IS NOT NULL
                                 AND public.stewardship_response_comparison_v1(NEW.field,given.value)
                                     IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(NEW.field,candidate.submitted_value)
                             WHEN 'reviewer' THEN given.value IS NOT NULL AND EXISTS (
                                 SELECT 1 FROM public.stewardship_proposal_review edit
                                 JOIN public.stewardship_proposed_change edited ON edited.id=edit.proposal_id
                                 JOIN public.stewardship_submission edited_response
                                     ON edited_response.id=edited.submission_id
                                 WHERE edit.id::text=shown->>'decision' AND edit.admin_value_set
                                   AND ROW(edited.entity_kind,edited.entity_key,edited.field)
                                       =ROW(candidate.entity_kind,candidate.entity_key,candidate.field)
                                   AND edited_response.family_id=shown_census.family_id
                                   AND public.stewardship_response_comparison_v1(NEW.field,edit.admin_value)
                                       IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(NEW.field,given.value))
                             ELSE false END)
                   OR candidate.id=(SELECT newest.id FROM (
                           SELECT kept.id, kept.execution, kept.submitted_value
                           FROM public.stewardship_proposed_change kept
                           JOIN public.stewardship_submission kept_response ON kept_response.id=kept.submission_id
                           JOIN public.stewardship_submission kept_prior ON kept_prior.id=prior.id
                           WHERE kept_response.family_id=kept_prior.family_id
                             AND kept_response.campaign_id=kept_prior.campaign_id
                             AND kept_response.mode=kept_prior.mode
                             AND kept_response.rehearsal_epoch_id IS NOT DISTINCT FROM kept_prior.rehearsal_epoch_id
                             AND kept_response.family_version<=kept_prior.family_version
                             AND ROW(kept.entity_kind,kept.entity_key,kept.field)=ROW(NEW.entity_kind,NEW.entity_key,NEW.field)
                           ORDER BY kept_response.family_version DESC LIMIT 1) newest
                       JOIN public.stewardship_submission kept_census
                           ON kept_census.id=public.stewardship_response_census_anchor_v1(prior.id)
                       CROSS JOIN LATERAL (SELECT kept_census.answers #> CASE NEW.entity_kind
                             WHEN 'family' THEN ARRAY['family',NEW.field]
                             ELSE ARRAY['members',NEW.entity_key,NEW.field] END AS value) given
                       WHERE NEW.entity_kind IN ('family','member')
                         AND newest.execution NOT IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                         AND given.value IS NOT NULL
                         AND public.stewardship_response_comparison_v1(NEW.field,given.value)
                             IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(NEW.field,newest.submitted_value)))
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
            IF ((NEW.current_available AND submitted_key IS NOT DISTINCT FROM current_key)
                OR (NOT NEW.current_available AND submitted_key IS NULL)
                OR (NEW.baseline_available AND submitted_key IS NOT DISTINCT FROM baseline_key))
               -- Except a change the parish added (#2008) that a response
               -- drawn as the reviewed form edited back to ParishSoft's
               -- value: its 'parish' entry names this Family's addition for
               -- this field, not withdrawn, with another value. The Family
               -- turned the added value down, which goes to review (the
               -- Administrator's decision 2); responses/proposals.py makes
               -- the same row.
               AND NOT (response.reviewed_view AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(response.shown_items) shown
                   JOIN public.stewardship_census_addition addition ON addition.id::text=shown->>'item'
                   WHERE shown->>'stream'='census' AND shown->>'shown'='parish'
                     AND ROW(shown->>'entity',shown->>'key',shown->>'field')
                         =ROW(NEW.entity_kind,NEW.entity_key,NEW.field)
                     AND ROW(addition.entity_kind,addition.entity_key,addition.field)
                         =ROW(NEW.entity_kind,NEW.entity_key,NEW.field)
                     AND addition.family_id=response.family_id
                     AND NOT EXISTS (SELECT 1 FROM public.stewardship_census_addition_resolution step
                         WHERE step.addition_id=addition.id AND step.action='withdrawn')
                     AND public.stewardship_response_comparison_v1(NEW.field,addition.value)
                         IS DISTINCT FROM submitted_key))
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
                -- which never changes the decision and never resolves a change
                -- upstream (strict phase order, #1895: an unchanged answer
                -- keeps a change ParishSoft already holds, and an answer set
                -- back withdraws one). A change Entered by hand
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
                   OR NEW.execution NOT IN ('superseded','cancelled')
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
                           -- An open change ParishSoft already holds, which
                           -- the Family answered again unchanged, stays on its
                           -- earlier response (#1895): the predecessor lookup
                           -- above finds it the same way.
                           OR OLD.id=(SELECT newest.id FROM (
                                   SELECT kept.id, kept.execution, kept.submitted_value
                                   FROM public.stewardship_proposed_change kept
                                   JOIN public.stewardship_submission kept_response ON kept_response.id=kept.submission_id
                                   JOIN public.stewardship_submission kept_prior ON kept_prior.id=later.prior_submission_id
                                   WHERE kept_response.family_id=kept_prior.family_id
                                     AND kept_response.campaign_id=kept_prior.campaign_id
                                     AND kept_response.mode=kept_prior.mode
                                     AND kept_response.rehearsal_epoch_id IS NOT DISTINCT FROM kept_prior.rehearsal_epoch_id
                                     AND kept_response.family_version<=kept_prior.family_version
                                     AND ROW(kept.entity_kind,kept.entity_key,kept.field)=ROW(OLD.entity_kind,OLD.entity_key,OLD.field)
                                   ORDER BY kept_response.family_version DESC LIMIT 1) newest
                               JOIN public.stewardship_submission kept_census
                                   ON kept_census.id=public.stewardship_response_census_anchor_v1(later.prior_submission_id)
                               CROSS JOIN LATERAL (SELECT kept_census.answers #> CASE OLD.entity_kind
                                     WHEN 'family' THEN ARRAY['family',OLD.field]
                                     ELSE ARRAY['members',OLD.entity_key,OLD.field] END AS value) given
                               WHERE OLD.entity_kind IN ('family','member')
                                 AND newest.execution NOT IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                                 AND given.value IS NOT NULL
                                 AND public.stewardship_response_comparison_v1(OLD.field,given.value)
                                     IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(OLD.field,newest.submitted_value))
                           -- A change the prior census response, drawn as the reviewed
                           -- form (#1705), showed and left as shown, the Family's answer or
                           -- a reviewer's Edit (#1909): it stays on its earlier response,
                           -- and effective.proposal_index finds it for the next response
                           -- (_shown_still_answered), so a response not drawn as the
                           -- reviewed form carries it or replaces it.
                           OR EXISTS (SELECT 1 FROM public.stewardship_submission shown_census
                               CROSS JOIN LATERAL jsonb_array_elements(shown_census.shown_items) shown
                               CROSS JOIN LATERAL (SELECT shown_census.answers #> CASE OLD.entity_kind
                                     WHEN 'family' THEN ARRAY['family',OLD.field]
                                     WHEN 'member' THEN ARRAY['members',OLD.entity_key,OLD.field]
                                     ELSE ARRAY['proposed_members',OLD.entity_key] END AS value) given
                               WHERE shown_census.id=public.stewardship_response_census_anchor_v1(later.prior_submission_id)
                                 AND shown_census.reviewed_view
                                 AND shown->>'stream'='census' AND shown->>'item'=OLD.id::text
                                 AND OLD.execution NOT IN ('cancelled','superseded')
                                 AND CASE shown->>'shown'
                                     WHEN 'baseline' THEN true
                                     WHEN 'family' THEN given.value IS NOT NULL
                                         AND public.stewardship_response_comparison_v1(OLD.field,given.value)
                                             IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(OLD.field,OLD.submitted_value)
                                     WHEN 'reviewer' THEN given.value IS NOT NULL AND EXISTS (
                                         SELECT 1 FROM public.stewardship_proposal_review edit
                                         JOIN public.stewardship_proposed_change edited ON edited.id=edit.proposal_id
                                         JOIN public.stewardship_submission edited_response
                                             ON edited_response.id=edited.submission_id
                                         WHERE edit.id::text=shown->>'decision' AND edit.admin_value_set
                                           AND ROW(edited.entity_kind,edited.entity_key,edited.field)
                                               =ROW(OLD.entity_kind,OLD.entity_key,OLD.field)
                                           AND edited_response.family_id=shown_census.family_id
                                           AND public.stewardship_response_comparison_v1(OLD.field,edit.admin_value)
                                               IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(OLD.field,given.value))
                                     ELSE false END)
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

DO $check$
DECLARE before text := current_setting('stewardship.migration_0170_acl');
BEGIN
    -- The new bodies, by text only they have (each pattern is absent from
    -- the body it replaces), neither a definer; the rules of the bodies
    -- they were copied from survive, and the triggers still call them.
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_submission_effects_v1()'::regprocedure
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%AND NOT (NEW.reviewed_view AND EXISTS (%JOIN public.stewardship_census_addition addition ON addition.id::text=shown->>''item''%=ROW(answer.entity,answer.identity,answer.field)%IS NOT DISTINCT FROM submitted_key))%'
              AND prosrc LIKE '%IF NEW.reviewed_view AND prior.entity_kind IN (''family'',''member'') AND EXISTS (%JOIN public.stewardship_census_addition addition%=ROW(prior.entity_kind,prior.entity_key,prior.field)%public.stewardship_response_comparison_v1(prior.field,addition.value)) THEN%'
              AND prosrc LIKE '%IS DISTINCT FROM submitted_key)%An edited census change the parish added requires a Family change%'
              AND (length(prosrc)-length(replace(prosrc,'step.action=''withdrawn''','')))
                  /length('step.action=''withdrawn''')=3
              -- 0160's strict phase order (#1895), kept.
              AND prosrc LIKE '%OR proposal.id=(SELECT newest.id FROM (%'
              AND prosrc LIKE '%IF NOT NEW.reviewed_view AND prior.entity_kind IN (''family'',''member'') AND EXISTS (%'
              -- 0153's reviewed view rules (#1705), kept.
              AND prosrc LIKE '%Submission must keep what its form showed%'
              AND prosrc LIKE '%IF NEW.reviewed_view AND EXISTS (%'
              AND prosrc LIKE '%OR (shown->>''shown''=''reviewer'' AND EXISTS (%'
              -- 0128's resubmission rule (#1683), kept.
              AND prosrc LIKE '%WHERE latest.execution IN (''published'',''resolved_external'')%'
              AND prosrc LIKE '%Submitted census change requires atomic derived work%'
              AND prosrc LIKE '%New census response must reconcile earlier census work%'
              AND prosrc LIKE '%Follow-up replacement requires exact linked history%')
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_submission'::regclass
              AND t.tgname='stewardship_submission_effects'
              AND t.tgfoid='public.stewardship_submission_effects_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN RAISE EXCEPTION 'The submission check does not admit a change the parish added left as shown'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%AND NOT (response.reviewed_view AND EXISTS (%JOIN public.stewardship_census_addition addition ON addition.id::text=shown->>''item''%AND addition.family_id=response.family_id%IS DISTINCT FROM submitted_key))%Unchanged values cannot create actionable proposals%'
              -- 0163's shown changes (#1909), kept.
              AND prosrc LIKE '%AND shown->>''stream''=''census'' AND shown->>''item''=candidate.id::text%'
              AND prosrc LIKE '%AND shown->>''stream''=''census'' AND shown->>''item''=OLD.id::text%'
              -- 0160's kept changes (#1895), kept.
              AND prosrc LIKE '%OR candidate.id=(SELECT newest.id FROM (%'
              AND prosrc LIKE '%OR OLD.id=(SELECT newest.id FROM (%'
              -- 0159's strict phase order (#1890), kept.
              AND prosrc LIKE '%OR (NEW.execution=''resolved_upstream'' AND OLD.execution<>''queued'')%'
              -- 0153's reviewed view rules (#1705), kept.
              AND prosrc LIKE '%carries_intent := FOUND AND NOT response.reviewed_view%'
              AND prosrc LIKE '%Proposal replacement requires a new final Family response%')
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgfoid='public.stewardship_response_derived_guard_v1()'::regprocedure
              AND (t.tgrelid, t.tgname) IN (
                  ('public.stewardship_proposed_change'::regclass, 'stewardship_proposed_change_guard'),
                  ('public.stewardship_additional_information'::regclass, 'stewardship_additional_information_guard'),
                  ('public.stewardship_submission_receipt'::regclass, 'stewardship_submission_receipt_guard'))
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>3
    THEN RAISE EXCEPTION 'The census guard does not admit an edited change the parish added'; END IF;
    -- Grants, settings and definer flags as they were (CREATE OR REPLACE
    -- keeps the owner, and fails for any other login).
    IF before IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||p.prosecdef::text
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_submission_effects_v1','stewardship_response_derived_guard_v1'))
       OR before NOT LIKE '2:%'
    THEN RAISE EXCEPTION 'The replaced functions'' grants or settings changed'; END IF;
END $check$;
