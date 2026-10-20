-- Frozen forward migration file 0218 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): pledge
-- resubmission of a reviewed Family form (#1705; review-pipeline spec,
-- "Shown items"). This file is installed by its Django migration in the
-- reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- A reviewed form shows an edited pledge with the reviewer's amount and
-- frequency, with the Family's own share methods and Cannot contribute
-- answer (a zero pledge with neither), and records which pledge review it
-- showed in ``shown_items`` (0143). A Family that sends that pledge back as
-- shown leaves it unchanged (rule 1), so its decision stays in force, in
-- every stage, Confirmed included. So this file replaces 0127's
-- stewardship_pledge_review_states_v1, copied whole, with one change: a
-- later live financial response is also unchanged when it was drawn as the
-- reviewed form, its shown items name a pledge review of the Family with a
-- reviewer's value and the response it showed, and its pledge answer equals
-- that shown pledge. 0136's stage selection (stewardship_pledge_stages_v1)
-- reads this selection, so the Pledges page, its counts and the pledge
-- review guard all see the same stage; #1753's submit-time placement
-- already applies only to a pledge that is not in force. A response not
-- drawn as the reviewed form has reviewed_view false, so the selection is
-- as before for it.
--
-- The function is not SECURITY DEFINER, before or after (0127 created it
-- without one), so there is no ALTER FUNCTION to repeat; CREATE OR REPLACE
-- keeps its grants. No table, column, index, grant or credential changes;
-- no Family code, link or session is touched, and no row is updated. The DO
-- block at the end refuses to commit unless the new body is installed with
-- its rule and attributes. No temporary objects. Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_pledge_review_states_v1(
    campaign_uuid uuid, family_uuid uuid DEFAULT NULL)
RETURNS TABLE(family_id uuid, family_duid bigint, submission_id uuid,
    sequence bigint, stage text, review_id uuid, in_force boolean)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH current AS (
        SELECT f.id AS family_id,f.family_duid,s.id AS submission_id
        FROM stewardship_family_campaign f
        JOIN stewardship_submission s ON s.id=f.effective_submission_id
            AND s.mode='live' AND s.campaign_id=f.campaign_id AND s.answers ? 'financial'
        WHERE f.campaign_id=campaign_uuid AND (family_uuid IS NULL OR f.id=family_uuid)
    ), latest AS (
        SELECT DISTINCT ON (r.family_id) r.id,r.family_id,r.sequence,r.decision,
            r.entered,r.answer,d.campaign_sequence
        FROM current c
        JOIN stewardship_pledge_review r ON r.family_id=c.family_id
        JOIN stewardship_submission d ON d.id=r.submission_id
        ORDER BY r.family_id,r.sequence DESC
    )
    SELECT c.family_id,c.family_duid::bigint,c.submission_id,coalesce(l.sequence,0),
        CASE WHEN NOT coalesce(k.in_force,false) OR l.decision IS NULL THEN 'to_review'
            WHEN l.decision='ignored' THEN 'ignored'
            WHEN l.entered THEN 'entered_by_hand'
            ELSE 'to_enter_by_hand' END,
        l.id,coalesce(k.in_force,false)
    FROM current c
    LEFT JOIN latest l ON l.family_id=c.family_id
    LEFT JOIN LATERAL (SELECT NOT EXISTS(
            SELECT 1 FROM stewardship_submission x
            WHERE x.family_id=c.family_id AND x.campaign_id=campaign_uuid
              AND x.mode='live' AND x.campaign_sequence>=l.campaign_sequence
              AND x.answers ? 'financial'
              AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                  IS DISTINCT FROM l.answer
              -- A response drawn as the reviewed form (#1705) that sent back
              -- the edited pledge the form showed (the reviewer's amount and
              -- frequency with the Family's own share methods and Cannot
              -- contribute answer; a zero pledge with neither) is unchanged.
              AND NOT (x.reviewed_view AND EXISTS (
                  SELECT 1 FROM jsonb_array_elements(x.shown_items) e
                  JOIN stewardship_pledge_review v ON v.id::text=e->>'decision'
                  JOIN stewardship_submission y ON y.id::text=e->>'item'
                  CROSS JOIN LATERAL (SELECT stewardship_pledge_answer_v1(
                      y.answers,y.annual_pledge) AS answer) b
                  WHERE e->>'stream'='financial' AND e->>'shown'='reviewer'
                    AND v.family_id=c.family_id AND y.family_id=c.family_id
                    AND v.reviewed_annual_pledge IS NOT NULL
                    AND stewardship_pledge_answer_v1(x.answers,x.annual_pledge)
                        =jsonb_build_object(
                            'annual_pledge',v.reviewed_annual_pledge::numeric(14,2)::text,
                            'frequency',CASE WHEN v.reviewed_annual_pledge>0
                                THEN coalesce(v.reviewed_frequency,'') ELSE '' END,
                            'cannot_give',CASE WHEN v.reviewed_annual_pledge>0
                                THEN 'false'::jsonb ELSE b.answer->'cannot_give' END,
                            'shares',CASE WHEN v.reviewed_annual_pledge>0
                                THEN b.answer->'shares' ELSE '{}'::jsonb END)))
            ) AS in_force) k ON l.id IS NOT NULL
$$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_pledge_review_states_v1(uuid,uuid)'::regprocedure
              AND prosrc LIKE '%AND NOT (x.reviewed_view AND EXISTS (%'
              AND prosrc LIKE '%WHERE e->>''stream''=''financial'' AND e->>''shown''=''reviewer''%'
              AND prosrc LIKE '%''annual_pledge'',v.reviewed_annual_pledge::numeric(14,2)::text%'
              -- 0127's selection, kept.
              AND prosrc LIKE '%IS DISTINCT FROM l.answer%'
              AND prosrc LIKE '%WHEN l.entered THEN ''entered_by_hand''%'
              AND NOT prosecdef
              AND provolatile='s'
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
    THEN
        RAISE EXCEPTION 'stewardship_pledge_review_states_v1 lacks the reviewed form rule';
    END IF;
END $check$;
