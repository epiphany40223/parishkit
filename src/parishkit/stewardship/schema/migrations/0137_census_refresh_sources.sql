-- Frozen forward migration file 0137 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): refresh
-- checks of an entered move, death and email opt-out (#1738).
-- This file is installed by its Django migration in the workflows app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest.
--
-- The review pipeline (docs/specs/stewardship/review-pipeline/spec.md,
-- "Refresh checks") confirms a census change Entered by hand when the
-- promoted data shows it. 0132 (provisional number) checks the Member
-- fields the Member contact read carries. This file adds three fields the
-- contact read does not carry, each read from the snapshot its own way:
--   * moved_household: true unless the snapshot lists the Member as an
--     active Member of the change's Family (not in the data, in another
--     Family, or inactive all read true); none for a Member the snapshot
--     marks deceased, which is no evidence of a move;
--   * deceased_status: whether the snapshot marks the Member deceased,
--     only while it lists them in the change's Family;
--   * the Family's email_opt_out: its sendNoMail flag (Administrator,
--     2026-10-18, #1680), only while the snapshot lists the Family active.
-- Created: stewardship_refresh_census_source_v1, the snapshot's value for
-- a census change, which returns stewardship_response_field_source_v1's
-- for every other Member field. Replaced, from 0133's body (0132's with
-- Entered incorrectly's two fixes, #1737), with these changes and only
-- these:
--   * stewardship_refresh_check_guard_v1: a census check's change may be
--     the Family's email opt-out as well as a Member's field; its observed
--     value must be stewardship_refresh_census_source_v1's (the same as
--     before for every field 0132 admitted, since moved and deceased were
--     never checked); and entered_incorrectly is refused for the three
--     fields, which have no third value to be wrong at (a yes-or-no
--     request, and an opt-out whose baseline was never read); for the
--     same reason 0133's comparison with the value recorded at an Entered
--     by hand mark leaves them out, so such a check is unchanged whenever
--     it is not confirmed.
-- No table, column, constraint or row changes. Neither function is
-- SECURITY DEFINER (the guard was created without one), so nothing else
-- is restored. The file ends with a DO block that refuses to commit
-- unless both bodies are installed with their attributes and the guard's
-- trigger. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The snapshot's own value for one census change, as a {available, value}
-- object, or NULL when the snapshot gives no evidence (the change's
-- Member or Family is not where the field can be read).
-- responses/refresh_checks.py (census_source) reads the same.
CREATE FUNCTION public.stewardship_refresh_census_source_v1(
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
BEGIN
    SELECT f.family_duid::text INTO family_key
        FROM public.stewardship_family_campaign f WHERE f.id=input_family;
    IF family_key IS NULL THEN RETURN NULL; END IF;
    IF input_kind='family' THEN
        IF input_field<>'email_opt_out' OR input_key IS DISTINCT FROM family_key THEN
            RETURN NULL;
        END IF;
        SELECT source.canonical::jsonb INTO family_value
            FROM public.stewardship_snapshot_family membership
            JOIN public.stewardship_source_family source ON source.id=membership.payload_id
            WHERE membership.snapshot_id=input_snapshot AND membership.source_key=family_key;
        IF family_value IS NULL OR family_value->'active' IS DISTINCT FROM 'true'::jsonb
           OR jsonb_typeof(family_value->'sendNoMail') IS DISTINCT FROM 'boolean' THEN
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

CREATE OR REPLACE FUNCTION public.stewardship_refresh_check_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    change stewardship_proposed_change%ROWTYPE;
    request stewardship_ministry_request%ROWTYPE;
    family uuid; phase text; carried jsonb; matches boolean; roster boolean;
    marked jsonb;
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
        -- Member's field, or the Family's email opt-out (#1738).
        SELECT p.* INTO change FROM stewardship_proposed_change p
            WHERE p.id=NEW.proposal_id FOR SHARE;
        SELECT s.family_id INTO family FROM stewardship_submission s
            WHERE s.id=change.submission_id AND s.mode='live';
        carried := CASE WHEN change.admin_value_set THEN change.admin_value
            ELSE change.submitted_value END;
        IF change.id IS NULL OR family IS NULL
           OR change.execution NOT IN ('published','resolved_external')
           OR NOT (change.entity_kind='member'
               OR (change.entity_kind='family' AND change.field='email_opt_out'))
           OR change.version<>NEW.item_version
           OR NEW.observed IS NULL OR NOT coalesce((NEW.observed->'available')='true'::jsonb,false)
           OR NEW.observed IS DISTINCT FROM stewardship_refresh_census_source_v1(
               NEW.snapshot_id,family,change.entity_kind,change.entity_key,change.field)
        THEN RAISE EXCEPTION 'A census refresh check requires a final change and its source value'
            USING ERRCODE='23514'; END IF;
        matches := stewardship_response_comparison_v1(change.field,NEW.observed->'value')
            IS NOT DISTINCT FROM stewardship_response_comparison_v1(change.field,carried);
        -- A yes-or-no request (moved, deceased) and an opt-out whose
        -- baseline was never read have no third value to be wrong at.
        IF NEW.outcome='ignored_now_matches' OR matches IS DISTINCT FROM (NEW.outcome='confirmed')
           OR (NEW.outcome='entered_incorrectly' AND (change.execution<>'resolved_external'
               OR change.field IN ('moved_household','deceased_status','email_opt_out')))
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
           AND change.field NOT IN ('moved_household','deceased_status','email_opt_out') THEN
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
           OR NEW.outcome='entered_incorrectly'
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

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_census_source_v1(uuid,uuid,text,text,text)'::regprocedure
              AND NOT prosecdef AND provolatile='s'
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%family_value->''sendNoMail''%'
              AND prosrc LIKE '%public.stewardship_response_field_source_v1(%'
              AND prosrc LIKE '%IF member_value->''deceased''=''true''::jsonb THEN RETURN NULL; END IF;%')
    THEN
        RAISE EXCEPTION 'The census refresh source function is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_refresh_check'::regclass
              AND t.tgname='stewardship_refresh_check_guard'
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              -- BEFORE each row's INSERT, UPDATE and DELETE (31).
              AND t.tgtype=31
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname='stewardship_refresh_check_guard_v1')<>1
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_refresh_census_source_v1(%'
              AND prosrc NOT LIKE '%stewardship_response_field_source_v1(%'
              AND prosrc LIKE '%change.field IN (''moved_household'',''deceased_status'',''email_opt_out'')%'
              AND prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
              AND prosrc LIKE '%stewardship_response_ministry_current_v1(%'
              AND prosrc LIKE '%k.outcome=''confirmed''%'
              AND prosrc LIKE '%A replaced census change is not checked%'
              AND prosrc LIKE '%k.item_version=change.version-1%'
              AND prosrc LIKE '%AND change.field NOT IN (''moved_household'',''deceased_status'',''email_opt_out'') THEN%')
    THEN
        RAISE EXCEPTION 'The refresh check guard is not installed as declared';
    END IF;
END $check$;
