-- Frozen forward migration file 0130 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): an edited
-- census address meets the Family's own address rules (#1719). This file is
-- installed by its Django migration in the responses app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- 0128 (#1683) let a reviewer's Edit (Accept with a value) change a Family's
-- home or mailing address, but its review guard checks only the edit's
-- shape for one: an object of its six parts with a non-blank street line,
-- at most 1,024 bytes, different from the Family's answer. The Family's own
-- answer is also held to stewardship_response_address_guard_v1 (the
-- baseline's schema/functions.sql: a known ISO country, a US state or
-- territory and a ZIP or ZIP+4 for a US address, a city, the per-part
-- lengths and normalized single-line text), which the service mirrors in
-- census.validate_address for both. So this file replaces
-- stewardship_proposal_review_guard_v1 (0128's, its latest body) with that
-- body plus one change: an address edit, once the shape and difference
-- checks pass (their messages stay), is also passed to
-- stewardship_response_address_guard_v1, so the database refuses the same
-- addresses for an edit that it refuses for the Family's answer. Calling
-- the one function keeps one SQL copy of the vocabulary.
--
-- Nothing else is replaced. No table, column or index changes, and no
-- existing row is touched (the guard runs only when a review row is
-- inserted). The function is not SECURITY DEFINER (0096 created it without
-- one), so nothing else is restored. The file ends with a DO block that
-- refuses to commit unless the new body is installed with its attributes
-- and its trigger.
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
        -- An address edit meets the Family's own address rules (#1719): the
        -- same function that holds the Family's answer, so the vocabulary
        -- (countries, US regions, ZIP) has one SQL copy. It runs after the
        -- checks above so their messages stay for the shape and a blank
        -- street line; it raises 23514 for anything the Family form refuses.
        IF proposal.field IN ('home_address','mailing_address') THEN
            PERFORM public.stewardship_response_address_guard_v1(NEW.admin_value);
        END IF;
    END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%PERFORM public.stewardship_response_address_guard_v1(NEW.admin_value);%'
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id,false)%'
              AND prosrc LIKE '%OR (decided AND edited AND NEW.decision<>''approved'')%'
              AND prosrc LIKE '%Census change edit must differ from the Family%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_review'::regclass
              AND t.tgname='stewardship_proposal_review_guard'
              AND t.tgfoid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_review_guard_v1 lacks the Family address rules';
    END IF;
END $check$;
