-- Frozen forward migration file 0189 (the repository-wide file sequence;
-- the number is reserved for this change): a decided Ministry request's
-- refresh check reads ParishSoft's roster as of the later of the
-- refresh's own date and the campaign's commitment period start (#2293;
-- review-pipeline spec, "Ministry roster date"). This file is installed by
-- its Django migration in the responses app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its
-- digest.
--
-- Staff enter a campaign's Ministry joins and leaves in ParishSoft dated for
-- its commitment period (the stored financial period start, values ->
-- 'financial' -> 'start'): for Stewardship 2027 a leave ends 2026-12-31 and
-- a join starts 2027-01-01. Each roster row's stored "current" flag is
-- judged as of the refresh's date, so such a join was not current until the
-- period began, and a refresh check of it was refused unless it said Not
-- seen. Once the period is under way the refresh's own date is the later
-- one, so a join or leave entered late, dated after the start, is still
-- confirmed. Created, each a plain SQL read with no definer:
--   stewardship_ministry_roster_date_v1(campaign, snapshot)   the later of
--       the campaign's commitment period start (its active configuration's)
--       and the snapshot's refresh date (its load's as_of_date, the day
--       each roster row's stored flag was judged on), or NULL when the
--       campaign has no commitment period (Financial stewardship off).
--   stewardship_response_ministry_held_v1(source, family, member,
--       ministry, as_of)   the baseline roster read
--       stewardship_response_ministry_current_v1 (NULL when the Member or
--       Ministry gives no evidence), with the roster rows judged as of
--       as_of by their start and end dates (held when a row has either
--       date, starts on or before as_of and ends after it, the rule of
--       parishsoft.ministry_membership_is_current), or that read unchanged
--       when as_of is NULL. Each gets the baseline read's grants.
-- Replaced, copied whole from its latest body with this change and only
-- this one:
--   stewardship_refresh_check_guard_v1   (0159's) a Ministry request's check
--       is judged by stewardship_response_ministry_held_v1 as of
--       stewardship_ministry_roster_date_v1 for the request's campaign and
--       the snapshot.
-- The Family form and the response guard keep today's roster (the stored
-- flag, through stewardship_response_ministry_current_v1, unchanged), as
-- do the Ministry reports. Nothing already stored is rewritten (no table,
-- column, constraint, index or row changes): checks already written stay,
-- and later refreshes classify requests by the new rule. The guard is not
-- SECURITY DEFINER, before or after, so there is no ALTER FUNCTION to
-- repeat; it runs as the source worker, which already reads the campaign,
-- its configuration and the snapshot cursor. CREATE OR REPLACE keeps its
-- owner, grants and settings, which the DO block below compares with those
-- recorded before the replacement, after checking every new body.
-- Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced guard's grants, settings and definer flag before the
-- replacement (with how many definitions there are), compared in the DO
-- block. A transaction-local setting holds them: the migration login has
-- no TEMP privilege.
SELECT set_config('stewardship.migration_0189_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||p.prosecdef::text
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_refresh_check_guard_v1'), true);

-- The day a campaign's Ministry checks read the roster on in a snapshot
-- (#2293): the later of the commitment period start of the campaign's
-- active configuration and the snapshot's refresh date, NULL when the
-- campaign has no commitment period (so the stored flag decides). The
-- refresh date is the load's as_of_date only when written as YYYY-MM-DD
-- and a real date; otherwise the start alone decides (greatest ignores a
-- NULL). refresh_checks.roster_date reads both the same way.
-- Configuration validation (campaigns.configuration) keeps the start an
-- ISO date whenever the period is present.
CREATE FUNCTION public.stewardship_ministry_roster_date_v1(
    campaign_uuid uuid, snapshot_uuid uuid)
    RETURNS date LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT greatest(commitment.starts, (
        SELECT CASE WHEN refreshed.day ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            THEN CASE WHEN pg_input_is_valid(refreshed.day,'date')
                THEN refreshed.day::date END END
        FROM (SELECT snapshot.cursor->'load'->>'as_of_date' AS day
            FROM public.stewardship_source_snapshot snapshot
            WHERE snapshot.id=snapshot_uuid) refreshed))
    FROM (SELECT CASE WHEN jsonb_typeof(configuration.values->'financial')='object'
            THEN (configuration.values->'financial'->>'start')::date END AS starts
        FROM public.stewardship_campaign campaign
        JOIN public.stewardship_campaign_configuration configuration
            ON configuration.id=campaign.active_configuration_id
        WHERE campaign.id=campaign_uuid) commitment
    WHERE commitment.starts IS NOT NULL
$$;

-- Whether the roster holds a Member in a Ministry on as_of (#2293). The
-- baseline read's NULL (no evidence) stays NULL, and a NULL as_of keeps
-- that read's answer, the roster as of the refresh's date. Otherwise a row
-- is held as parishsoft.ministry_membership_is_current judges it on as_of:
-- it has a start or an end date, starts on or before as_of, and ends after
-- it (an end date is exclusive). Roster dates are stored as ISO dates.
CREATE FUNCTION public.stewardship_response_ministry_held_v1(
    source_id uuid, family_id uuid, member_key text, ministry_id integer, as_of date)
    RETURNS boolean LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT CASE WHEN evidence.held IS NULL OR as_of IS NULL THEN evidence.held
        ELSE EXISTS (
            SELECT 1 FROM public.stewardship_snapshot_roster membership
            JOIN public.stewardship_source_roster roster ON roster.id=membership.payload_id
            CROSS JOIN LATERAL (SELECT (roster.canonical::jsonb->>'startDate')::date AS starts,
                (roster.canonical::jsonb->>'endDate')::date AS ends) dates
            WHERE membership.snapshot_id=source_id
              AND roster.member_key=stewardship_response_ministry_held_v1.member_key
              AND roster.ministry_key=ministry_id::text
              AND (dates.starts IS NOT NULL OR dates.ends IS NOT NULL)
              AND (dates.starts IS NULL OR dates.starts<=as_of)
              AND (dates.ends IS NULL OR dates.ends>as_of)) END
    FROM (SELECT public.stewardship_response_ministry_current_v1(
        source_id,family_id,member_key,ministry_id) AS held) evidence
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
           -- Strict phase order (#1890): an accepted request is Confirmed
           -- only after its Entered in ParishSoft tick, its latest roster
           -- entry; before it the roster showing it changes no stage.
           OR (NEW.outcome='confirmed' AND NOT coalesce((SELECT e.entered
               FROM stewardship_ministry_roster_entry e WHERE e.request_id=request.id
               ORDER BY e.sequence DESC LIMIT 1),false))
        THEN RAISE EXCEPTION 'A Ministry refresh check requires a decided request'
            USING ERRCODE='23514'; END IF;
        -- The roster as of the later of the refresh date and the campaign's
        -- commitment period start (#2293), the day staff date the request's
        -- join or leave for, until the period is under way.
        roster := stewardship_response_ministry_held_v1(NEW.snapshot_id,family,
            request.entity_key,CASE WHEN request.outcome IN ('joined','leave_confirmed')
                THEN coalesce(request.reviewed_ministry_duid,request.ministry_duid)
                ELSE request.ministry_duid END,
            stewardship_ministry_roster_date_v1((SELECT s.campaign_id
                FROM stewardship_submission s WHERE s.id=request.submission_id),
                NEW.snapshot_id));
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
DECLARE before text := current_setting('stewardship.migration_0189_acl');
BEGIN
    -- The two reads, by text only they have, neither a definer, each with
    -- the baseline roster read's grants.
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_ministry_roster_date_v1(uuid,uuid)'::regprocedure
              AND NOT prosecdef AND provolatile='s' AND prorettype='date'::regtype
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%SELECT greatest(commitment.starts, (%'
              AND prosrc LIKE '%snapshot.cursor->''load''->>''as_of_date'' AS day%'
              AND prosrc LIKE '%THEN (configuration.values->''financial''->>''start'')::date END AS starts%'
              AND prosrc LIKE '%WHERE commitment.starts IS NOT NULL%'
              AND proacl IS NOT DISTINCT FROM (SELECT proacl FROM pg_proc
                  WHERE oid='public.stewardship_response_ministry_current_v1(uuid,uuid,text,integer)'::regprocedure))
    THEN RAISE EXCEPTION 'The Ministry roster date read is not installed'; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_response_ministry_held_v1(uuid,uuid,text,integer,date)'::regprocedure
              AND NOT prosecdef AND provolatile='s' AND prorettype='boolean'::regtype
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND prosrc LIKE '%WHEN evidence.held IS NULL OR as_of IS NULL THEN evidence.held%'
              AND prosrc LIKE '%AND (dates.starts IS NULL OR dates.starts<=as_of)%'
              AND prosrc LIKE '%AND (dates.ends IS NULL OR dates.ends>as_of)) END%'
              AND prosrc LIKE '%public.stewardship_response_ministry_current_v1(%'
              AND proacl IS NOT DISTINCT FROM (SELECT proacl FROM pg_proc
                  WHERE oid='public.stewardship_response_ministry_current_v1(uuid,uuid,text,integer)'::regprocedure))
    THEN RAISE EXCEPTION 'The roster read as of a date is not installed'; END IF;
    -- The guard judges a Ministry check as of the Ministry roster date,
    -- keeps 0159's rule, and its trigger still calls it.
    IF NOT EXISTS (SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND NOT prosecdef
              AND prosrc LIKE '%roster := stewardship_response_ministry_held_v1(NEW.snapshot_id,family,%stewardship_ministry_roster_date_v1((SELECT s.campaign_id%NEW.snapshot_id));%'
              AND prosrc NOT LIKE '%stewardship_response_ministry_current_v1(%'
              -- 0159's strict phase order (#1890), kept.
              AND prosrc LIKE '%OR (NEW.outcome=''confirmed'' AND NOT coalesce((SELECT e.entered%'
              AND prosrc LIKE '%A Ministry refresh check differs from its roster evidence%')
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_refresh_check'::regclass
              AND t.tgname='stewardship_refresh_check_guard'
              AND t.tgfoid='public.stewardship_refresh_check_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN RAISE EXCEPTION 'The refresh check guard does not read the roster as of the Ministry roster date'; END IF;
    -- Grants, settings and definer flag as they were (CREATE OR REPLACE
    -- keeps the owner, and fails for any other login).
    IF before IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||p.prosecdef::text
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_refresh_check_guard_v1')
       OR before NOT LIKE '1:%'
    THEN RAISE EXCEPTION 'The replaced guard''s grants or settings changed'; END IF;
END $check$;
