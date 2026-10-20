-- Frozen forward migration file 0139 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): correcting
-- a New Member's ParishSoft Member ID (#1773). This file is installed by its
-- Django migration in the responses app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest.
--
-- A New Member Staff mark Entered by hand may name the ParishSoft Member ID
-- of the Member they added, which the refresh confirms the change by (0138,
-- provisional number). The review pipeline
-- (docs/specs/stewardship/review-pipeline/spec.md, "Refresh checks") now
-- lets Staff and Administrators correct a mistyped ID, or give one the mark
-- lacked: a new 'entered' resolution at the change's version, as Entered by
-- hand again is over Entered incorrectly (0133, provisional number), naming
-- an ID other than the one the current mark names. The refresh check guard
-- already reads the current mark (the resolution at the version before the
-- change's current one), and the census guard's web branch already admits
-- an 'entered' resolution on a change Entered by hand, keeping it entered,
-- so neither changes.
-- Replaced, from its latest body (0138's), with this change and only this:
--   * stewardship_proposal_resolution_guard_v1: also admits, for Staff or
--     an Administrator, an 'entered' resolution of a New Member Entered by
--     hand whose latest refresh check at its current version is not
--     'confirmed', which no later response of the Family replaced, naming a
--     Member ID other than the current mark's. The ID's own rules (never a
--     Member the response listed, nor one another New Member of the Family
--     is entered as) apply as before. The later-response test is computed
--     once and shared with Entered incorrectly's.
-- The function was created without SECURITY DEFINER, so nothing else is
-- restored. The file ends with a DO block that refuses to commit unless all
-- of it is installed. Reversing needs its own forward migration.

SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_proposal_resolution_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE proposal public.stewardship_proposed_change%ROWTYPE; campaign uuid;
        by_hand boolean; open_state boolean; incorrect boolean;
        replaced boolean; again boolean;
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
    -- A change a later response of the Family replaced (the same field
    -- answered again) is history: it takes neither Entered incorrectly's
    -- fixes nor a Member ID correction.
    replaced:=EXISTS (SELECT 1 FROM public.stewardship_proposed_change later
            JOIN public.stewardship_submission answered ON answered.id=later.submission_id
            JOIN public.stewardship_submission earlier ON earlier.id=proposal.submission_id
            WHERE answered.family_id=earlier.family_id
              AND answered.campaign_id=earlier.campaign_id AND answered.mode=earlier.mode
              AND answered.family_version>earlier.family_version
              AND ROW(later.entity_kind,later.entity_key,later.field)
                  =ROW(proposal.entity_kind,proposal.entity_key,proposal.field));
    -- Entered incorrectly (#1737): a change Entered by hand whose latest
    -- refresh check, at its current version, found a third value in
    -- ParishSoft. Its two fixes are Entered by hand again ('entered') and
    -- Back to review ('reopened'), Staff's as well as an Administrator's.
    incorrect:=coalesce(proposal.execution='resolved_external',false)
        AND coalesce((SELECT k.outcome='entered_incorrectly'
            FROM public.stewardship_refresh_check k
            WHERE k.proposal_id=proposal.id AND k.item_version=proposal.version
            ORDER BY k.created_at DESC, k.id DESC LIMIT 1),false)
        AND NOT replaced;
    -- A New Member's ParishSoft Member ID corrected (#1773): a New Member
    -- Entered by hand that no refresh has confirmed at its current version,
    -- marked entered again with an ID other than the one its current mark
    -- names (the resolution that advanced it to its current version, none
    -- when the mark named no ID). Staff's as well as an Administrator's. A
    -- blank ID keeps the mark in force, so it is no correction.
    again:=coalesce(proposal.execution='resolved_external',false)
        AND NOT incorrect AND NOT replaced
        AND proposal.entity_kind='proposed_member' AND proposal.field='new_member'
        AND NEW.action='entered' AND NEW.member_duid IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM public.stewardship_refresh_check k
            WHERE k.proposal_id=proposal.id AND k.item_version=proposal.version
              AND k.outcome='confirmed')
        AND NEW.member_duid IS DISTINCT FROM (SELECT r.member_duid
            FROM public.stewardship_proposal_resolution r
            WHERE r.proposal_id=proposal.id AND r.expected_version=proposal.version-1
              AND r.action='entered');
    IF proposal.id IS NULL OR campaign IS NULL
       OR NOT public.stewardship_export_authorized_v1(NEW.actor_id,
           NEW.action='reopened' AND NOT incorrect)
       OR NOT public.stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM proposal.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       OR proposal.handling='report-only'
       OR (incorrect AND NEW.action NOT IN ('entered','reopened'))
       -- A final change takes only Entered incorrectly's fixes or a New
       -- Member's ID correction (AND binds before OR).
       OR (NOT incorrect AND NOT open_state) AND NOT again
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

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              -- This file's rule.
              AND prosrc LIKE '%again:=coalesce(proposal.execution=''resolved_external'',false)%'
              AND prosrc LIKE '%AND NEW.action=''entered'' AND NEW.member_duid IS NOT NULL%'
              AND prosrc LIKE '%AND k.outcome=''confirmed'')%'
              AND prosrc LIKE '%AND NEW.member_duid IS DISTINCT FROM (SELECT r.member_duid%'
              AND prosrc LIKE '%r.expected_version=proposal.version-1%'
              AND prosrc LIKE '%OR (NOT incorrect AND NOT open_state) AND NOT again%'
              AND prosrc LIKE '%AND NOT incorrect AND NOT replaced%'
              -- Entered incorrectly's fixes still skip a replaced change.
              AND prosrc LIKE '%LIMIT 1),false)
        AND NOT replaced;%'
              AND prosrc LIKE '%replaced:=EXISTS (SELECT 1 FROM public.stewardship_proposed_change later%'
              -- 0133's and 0138's rules, kept.
              AND prosrc LIKE '%k.outcome=''entered_incorrectly''%'
              AND prosrc LIKE '%NEW.action=''reopened'' AND NOT incorrect)%'
              AND prosrc LIKE '%OR (incorrect AND NEW.action NOT IN (''entered'',''reopened''))%'
              AND prosrc LIKE '%AND answered.family_version>earlier.family_version%'
              AND prosrc LIKE '%OR (NEW.member_duid IS NOT NULL AND (NEW.action<>''entered''%'
              AND prosrc LIKE '%WHERE other.member_duid=NEW.member_duid%'
              AND prosrc LIKE '%Census change resolution lacks current authority, version or state%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_proposal_resolution'::regclass
              AND t.tgname='stewardship_proposal_resolution_guard'
              AND t.tgfoid='public.stewardship_proposal_resolution_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_proposal_resolution_guard_v1 lacks the New Member ID correction';
    END IF;
END $check$;
