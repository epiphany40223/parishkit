-- Frozen forward migration file 0107 (the repository-wide file sequence):
-- an Administrator's edited value for an Automatic census change (#528,
-- ADM-09.04). This file is installed by its Django migration in the
-- responses app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
--
-- 0096's review guard admits a review only when it changes the decision and
-- leaves the edit fields as the proposal has them. This file replaces that
-- guard, so a review is exactly one of two kinds:
--   * a decision: the decision changes, the edit fields stay (as before; a
--     conflict is still not approved);
--   * an edit: the decision stays, the edit fields change. Setting an edit
--     needs admin_value_set true with a non-null value of the field's shape
--     (a phone record object for a phone, a string otherwise), which the
--     shared comparison function validates (a phone record must be its own
--     display's, a date an ISO civil date), with a non-blank value (blank by
--     the comparison's own whitespace set, not only spaces), at most
--     1,024 bytes encoded, and a key different from the Family's submitted
--     value, so an edit is never a no-op. Clearing (false with NULL) needs an
--     edit to be set. The full value rules (the Family form's validator and
--     the ParishSoft request schema's limits) stay in Python.
-- Every other rule is 0096's: a current Administrator, a live response's
-- Automatic Member change in a campaign that admits changes, at the
-- change's current version, while it is open (pending, conflict or failed;
-- never queued for publication or final). 0096's deferred effect check and
-- the derived guard's review branch already require the proposal to carry
-- exactly the row's decision and edit fields at the next version, with the
-- census_change_updated audit event, so neither is replaced.
--
-- The web login may now update the proposal's edit fields (a runtime column
-- grant), and the derived guard's other web branch, a replacement by a later
-- Family response, does not compare them. So a new trigger,
-- stewardship_proposal_edit_guard (migration-owned), refuses any change to
-- the edit fields by the web login unless a review row at the proposal's
-- current version records exactly the new values. A review row's own guard
-- and the deferred effect check then hold it to everything above.
--
-- Neither function is SECURITY DEFINER (0096 created the review guard
-- without one), so nothing else is restored. The file ends with a DO block
-- that refuses to commit unless both are installed with their attributes.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_proposal_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
    decided boolean; edited boolean; edit_type text; edit_key text; edit_text text;
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
       OR (NEW.decision='approved' AND proposal.execution='conflict')
       -- Exactly one of a decision and an edit (#528, ADM-09.04).
       OR decided=edited
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

CREATE FUNCTION public.stewardship_proposal_edit_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    -- The web login changes the edit fields only as a review row at the
    -- current version records them (Edit value or Clear edit, ADM-09.04).
    IF current_user='pk_stewardship_web'
       AND ROW(NEW.admin_value_set,NEW.admin_value)
           IS DISTINCT FROM ROW(OLD.admin_value_set,OLD.admin_value)
       AND NOT EXISTS(SELECT 1 FROM public.stewardship_proposal_review v
           WHERE v.proposal_id=OLD.id AND v.expected_version=OLD.version
             AND ROW(v.admin_value_set,v.admin_value)
                 IS NOT DISTINCT FROM ROW(NEW.admin_value_set,NEW.admin_value))
    THEN RAISE EXCEPTION 'Census change edit requires its review'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_proposal_edit_guard
    BEFORE UPDATE ON public.stewardship_proposed_change
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_proposal_edit_guard_v1();

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%OR decided=edited%'
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
        RAISE EXCEPTION 'stewardship_proposal_review_guard_v1 lacks the edit rules';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_edit_guard_v1()'::regprocedure
              AND prosrc LIKE '%Census change edit requires its review%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposed_change'::regclass
              AND t.tgname='stewardship_proposal_edit_guard'
              AND t.tgfoid='public.stewardship_proposal_edit_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal
              AND (t.tgtype & 2)=2 AND (t.tgtype & 16)=16 AND (t.tgtype & 1)=1)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposed_change lacks its edit guard';
    END IF;
END $check$;
