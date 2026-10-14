-- Frozen forward migration file 0091 (the repository-wide file sequence):
-- the local Ministry activity (active/inactive) leaves the database (#1267).
-- This file is installed by its Django migration in the accounts app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of each replaced baseline
-- function equals the fresh-install baseline's. A fresh install runs the
-- baseline, which already lacks the table and both dropped functions and
-- has the new bodies, the earlier files (which still replace some of these
-- functions with their older bodies) and then this, and ends in the same
-- catalog as an upgraded database.
--
-- The Administrator removed the concept (2026-10-13): an Administrator could
-- mark a ParishSoft Ministry inactive for the whole parish (the
-- configuration's `ministries` section, projected into
-- stewardship_ministry_activity), which hid it from the Family form, from
-- new Ministry selections and from its leaders' scope. Now every Ministry
-- ParishSoft lists can be chosen, and a campaign's Ministry selections alone
-- decide what Families see. This file:
--   refuses to run while the active configuration still marks a Ministry
--     inactive, so a hidden Ministry never silently reappears (Production
--     had no row when this was written);
--   drops stewardship_ministry_activity (with its keys, indexes, foreign key
--     and its two triggers) and the two trigger functions only it used,
--     stewardship_ministry_activity_immutable_v1 and
--     stewardship_ministry_projection_v1. No view, foreign key or SQL-standard
--     function body depends on the table, so no CASCADE: an unexpected
--     dependent object fails the migration instead of being dropped with it;
--   replaces, from each one's newest definition minus only its activity
--     test:
--       stewardship_campaign_pointer_v1   a live campaign may add any
--                                         Ministry in the promoted catalog
--       stewardship_chair_decisions_v1    no 'ministry_inactive' reason
--       stewardship_chair_seed_evidence_guard_v1  no inactive refusal
--       stewardship_ministry_activation_v1  only the Chair receipt check
--                                         remains (no activity change to
--                                         reconcile)
--       stewardship_ministry_complete_v1  a new configuration's `ministries`
--                                         section must be empty, whatever
--                                         its schema (it checked the
--                                         projection before)
--       stewardship_response_ministry_visible_v1  catalog and selection only
--       stewardship_ministry_report_v1 and _v3  `active` is true for a
--                                         Ministry ParishSoft lists and NULL
--                                         otherwise; the `activity` filter
--                                         key stays in the contract (the
--                                         application always sends "any")
--       stewardship_leaders_without_user_v1 and
--       stewardship_ministry_leaders_at_v1  an inactive Ministry no longer
--                                         leaves its leaders' scope
-- Stored data is untouched: applied configurations keep the section they
-- recorded (the application verifies history from the document itself).
--
-- Each replaced function keeps its owner, grants and attributes; only
-- stewardship_leaders_without_user_v1 is SECURITY DEFINER, and its ALTER is
-- repeated after the replacement. The scripted upgrade stops every
-- application service before migrating, so no running process still writes
-- the dropped table. DROP ... IF EXISTS because a fresh install's baseline
-- already lacks them. The DO block at the end refuses to commit unless
-- everything is installed as declared. Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- An inactive Ministry in the active configuration would silently reappear
-- on the Family form once the override is gone: refuse instead, so the
-- Administrator removes it from the campaign's Ministry selections first.
-- The read is nested: PL/pgSQL plans a whole condition, and a fresh
-- install's baseline has no table to read.
DO $guard$
BEGIN
    IF to_regclass('public.stewardship_ministry_activity') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_ministry_activity a
                   JOIN public.stewardship_system_configuration s
                     ON s.active_configuration_id=a.configuration_id
                   WHERE NOT a.active) THEN
            RAISE EXCEPTION 'Migration 0091: the active configuration marks a Ministry inactive; remove it from the campaign''s Ministry selections and mark it active before upgrading';
        END IF;
    END IF;
END
$guard$;

-- The replaced functions' grants before the replacement, compared in the DO
-- block. A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0091_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
        'stewardship_campaign_pointer_v1','stewardship_chair_decisions_v1',
        'stewardship_chair_seed_evidence_guard_v1','stewardship_ministry_activation_v1',
        'stewardship_ministry_complete_v1','stewardship_response_ministry_visible_v1',
        'stewardship_ministry_report_v1','stewardship_ministry_report_v3',
        'stewardship_leaders_without_user_v1','stewardship_ministry_leaders_at_v1')), true);

DROP TABLE IF EXISTS public.stewardship_ministry_activity;
DROP FUNCTION IF EXISTS public.stewardship_ministry_projection_v1();
DROP FUNCTION IF EXISTS public.stewardship_ministry_activity_immutable_v1();

CREATE OR REPLACE FUNCTION public.stewardship_campaign_pointer_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE target uuid; candidate stewardship_campaign_configuration%ROWTYPE;
    live_state text; live_before jsonb; live_added numeric[];
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Configuration cannot replace the current pointer' USING ERRCODE='23514';
    END IF;
    target := OLD.current_campaign_id;
    IF target IS NOT NULL AND EXISTS (
        SELECT 1 FROM stewardship_campaign_configuration c WHERE c.configuration_id=NEW.active_configuration_id
        AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)
    ) THEN RAISE EXCEPTION 'Current campaign prevents successor creation' USING ERRCODE='23514'; END IF;
    IF target IS NULL THEN
        IF (SELECT count(*) FROM stewardship_campaign_configuration c
            WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)) > 1 THEN
            RAISE EXCEPTION 'Only one successor can be created' USING ERRCODE='23514';
        END IF;
        SELECT record_id INTO target FROM stewardship_campaign_configuration c
        WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id);
        IF target IS NOT NULL AND (NEW.mode<>'testing' OR NEW.restore_review_required
            OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state NOT IN ('archived','purged'))
            OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))) THEN
            RAISE EXCEPTION 'Successor creation is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM stewardship_campaign old_campaign
        JOIN stewardship_campaign_configuration old_c ON old_c.id=old_campaign.active_configuration_id
        LEFT JOIN stewardship_campaign_configuration new_c ON new_c.record_id=old_campaign.id AND new_c.configuration_id=NEW.active_configuration_id
        WHERE new_c.id IS NULL OR (old_campaign.id IS DISTINCT FROM target AND old_c.values IS DISTINCT FROM new_c.values)
    ) THEN RAISE EXCEPTION 'Historical campaigns cannot be removed or edited' USING ERRCODE='23514'; END IF;
    IF target IS NOT NULL THEN
        SELECT * INTO candidate FROM stewardship_campaign_configuration WHERE record_id=target AND configuration_id=NEW.active_configuration_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Current campaign cannot be removed' USING ERRCODE='23514'; END IF;
        IF NEW.restore_review_required AND EXISTS (
            SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values IS DISTINCT FROM candidate.values
        ) THEN RAISE EXCEPTION 'Restore review holds campaign configuration changes' USING ERRCODE='23514'; END IF;
        IF OLD.current_campaign_id IS NULL AND candidate.timezone<>(SELECT timezone FROM stewardship_parish WHERE configuration_id=NEW.active_configuration_id) THEN
            RAISE EXCEPTION 'New draft must copy the parish timezone' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
        -- The one reviewed live structural exemption (#342): an Administrator
        -- may change a locked campaign's Ministry selections while it is still
        -- open. The value must stay canonical: a strictly ascending array of
        -- whole numbers, as the YAML schema writes it. Removing is always
        -- allowed. Every added DUID must be visible now: in the promoted
        -- catalog, in a campaign with the Ministry module. Answers are never
        -- touched here.
        -- Nested CASEs fix the evaluation order, so a malformed value is
        -- refused, not miscast.
        SELECT c.state, old_c.values->'ministry_duids' INTO live_state, live_before
        FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
        WHERE c.id=target AND c.structural_locked
          AND old_c.values->'ministry_duids' IS DISTINCT FROM candidate.values->'ministry_duids';
        IF FOUND THEN
            IF live_state NOT IN ('scheduled','active')
                OR NOT coalesce(CASE WHEN jsonb_typeof(candidate.values->'ministry_duids')='array'
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)
                        WHERE jsonb_typeof(e.duid)<>'number' OR e.duid#>>'{}'!~'^[0-9]{1,19}$') THEN
                    candidate.values->'ministry_duids'=(SELECT coalesce(jsonb_agg(to_jsonb(d.duid) ORDER BY d.duid),'[]'::jsonb)
                        FROM (SELECT DISTINCT (e.duid#>>'{}')::numeric AS duid
                              FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)) d)
                    END,false) THEN
                RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
            END IF;
            -- The value is now a canonical array of whole numbers.
            SELECT coalesce(array_agg((added.duid#>>'{}')::numeric),'{}') INTO live_added
            FROM jsonb_array_elements(candidate.values->'ministry_duids') added(duid)
            WHERE NOT coalesce(live_before @> added.duid,false);
            IF cardinality(live_added)>0 THEN
                IF NOT coalesce(candidate.values->'modules' ? 'ministry',false)
                    OR EXISTS (SELECT 1 FROM unnest(live_added) d WHERE d NOT BETWEEN 1 AND 2147483647) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
                -- A separate statement, reached only when a locked open campaign
                -- gains a Ministry: PostgreSQL checks EXECUTE on the definer
                -- stewardship_ministry_catalog_v1 whenever a statement naming it
                -- starts, whatever a CASE or AND would evaluate. Only the
                -- configuration installer, which holds no source grants, needs
                -- it; setup completion and other activations never get here.
                IF EXISTS (SELECT 1 FROM unnest(live_added) d
                    WHERE NOT EXISTS (SELECT 1 FROM stewardship_ministry_catalog_v1() present
                        WHERE present.ministry_duid=d::integer)) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_chair_decisions_v1(configuration uuid) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
    -- The current snapshot's chair relationships, read once. The planner
    -- copies the CASE below into both uses of reason, so a correlated read
    -- of the view there planned its ten-relation join twice per call, about
    -- 100-200 ms under the work-order lock (#147).
    WITH chair AS MATERIALIZED (
        SELECT DISTINCT organization_id, member_duid, ministry_duid, email
        FROM stewardship_current_chair)
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'assignment_record_id',record_id::text,
        'active',reason='current_chair','reason',reason) ORDER BY record_id),
        '[]'::jsonb)
    FROM (
        SELECT assignment.record_id, CASE
            WHEN evidence.id IS NULL THEN 'missing_binding'
            WHEN evidence.organization_id::text IS DISTINCT FROM
                integration.settings->>'organization_id' THEN 'organization_changed'
            WHEN EXISTS (SELECT 1 FROM chair
                WHERE chair.organization_id=evidence.organization_id
                  AND chair.member_duid=evidence.member_duid
                  AND chair.ministry_duid=assignment.ministry_duid
                  AND chair.email=assignment.email) THEN 'current_chair'
            ELSE 'relationship_missing' END AS reason
        FROM stewardship_ministry_assignment assignment
        LEFT JOIN stewardship_chair_seed_evidence evidence
            ON evidence.assignment_record_id=assignment.record_id
        LEFT JOIN stewardship_applied_integration integration
            ON integration.configuration_id=configuration
            AND integration.kind='parishsoft'
        WHERE assignment.configuration_id=configuration
            AND assignment.source='chair-seed'
    ) decisions;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_chair_seed_evidence_guard_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE assignment stewardship_ministry_assignment%ROWTYPE;
        evidence jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Chair seed evidence requires work ownership'
            USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM stewardship_source_current
        WHERE snapshot_id=NEW.snapshot_id AND organization_id=NEW.organization_id
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Chair seed evidence requires current source ownership'
            USING ERRCODE='23514';
    END IF;
    SELECT a.* INTO assignment FROM stewardship_ministry_assignment a
        JOIN stewardship_system_configuration runtime
            ON runtime.active_configuration_id=a.configuration_id
        JOIN stewardship_applied_integration integration
            ON integration.configuration_id=a.configuration_id
            AND integration.kind='parishsoft'
            AND integration.settings->>'organization_id'=NEW.organization_id::text
        WHERE a.id=NEW.assignment_id;
    IF assignment.id IS NULL OR assignment.source<>'chair-seed'
       OR assignment.record_id IS DISTINCT FROM NEW.assignment_record_id THEN
        RAISE EXCEPTION 'Chair seed evidence requires its applied seeded assignment'
            USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(roster_key ORDER BY roster_key) INTO evidence
        FROM stewardship_current_chair
        WHERE snapshot_id=NEW.snapshot_id AND organization_id=NEW.organization_id
          AND member_duid=NEW.member_duid AND ministry_duid=assignment.ministry_duid
          AND email=assignment.email;
    IF evidence IS NULL OR evidence IS DISTINCT FROM NEW.roster_keys THEN
        RAISE EXCEPTION 'Chair seed evidence requires the exact current relationship'
            USING ERRCODE='23514';
    END IF;
    -- A deployed role records evidence only from the Administrator's own
    -- selection, the intent bound to the confirmation request; only the
    -- schema owner may seed evidence without one. No installation or refresh
    -- can infer a Member from an address.
    IF current_user <> (SELECT tableowner FROM pg_tables
            WHERE schemaname='public' AND tablename='stewardship_chair_seed_evidence')
       AND NOT EXISTS (SELECT 1 FROM stewardship_chair_seed_intent intent
            WHERE intent.assignment_record_id=NEW.assignment_record_id
              AND intent.organization_id=NEW.organization_id
              AND intent.member_duid=NEW.member_duid) THEN
        RAISE EXCEPTION 'Chair seed evidence requires the confirmed selection'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_activation_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE snapshot uuid; seeded boolean;
BEGIN
    -- The local Ministry activity is gone (#1267); an activation still needs
    -- its Chair receipt while seeded assignments or Chair reviews exist.
    SELECT snapshot_id INTO snapshot FROM stewardship_source_current;
    seeded := EXISTS (SELECT 1 FROM stewardship_ministry_assignment
        WHERE configuration_id=NEW.configuration_id AND source='chair-seed');
    IF snapshot IS NOT NULL AND (seeded OR EXISTS (
        SELECT 1 FROM stewardship_chair_review WHERE closed_by_id IS NULL
    ) OR EXISTS (SELECT 1 FROM stewardship_chair_reconciliation
                 WHERE activation_id=NEW.id)) THEN
        PERFORM stewardship_require_chair_receipt_v1(NEW.configuration_id,snapshot);
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_complete_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- The local Ministry activity section is retired (#1267): a new
    -- configuration of any schema holds no record in it. Applied history
    -- keeps what it recorded; this runs only when a version is inserted.
    IF coalesce(NEW.canonical_document->'sections'->'ministries', '[]'::jsonb)
        <> '[]'::jsonb THEN
        RAISE EXCEPTION 'Ministry activity was removed; the ministries section must be empty'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_response_ministry_visible_v1(
    source_id uuid, configuration_id uuid, campaign_id uuid, ministry_id integer)
    RETURNS boolean LANGUAGE sql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.stewardship_snapshot_ministry membership
        JOIN public.stewardship_source_ministry ministry ON ministry.id=membership.payload_id
        JOIN public.stewardship_campaign_configuration campaign
            ON campaign.configuration_id=stewardship_response_ministry_visible_v1.configuration_id
           AND campaign.record_id=stewardship_response_ministry_visible_v1.campaign_id
        WHERE membership.snapshot_id=source_id AND membership.source_key=ministry_id::text
          AND ministry.canonical::jsonb->'catalog_present'='true'::jsonb
          AND campaign.values->'modules' ? 'ministry'
          AND campaign.values->'ministry_duids' @> to_jsonb(ministry_id)
    )
$$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_report_v1(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    ministry_id integer DEFAULT NULL, request_action text DEFAULT 'join',
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
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
    SELECT x.*,s.organization_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,statement_timestamp() AS observed_at,
        (statement_timestamp() AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date AS report_date
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Configuration permits signed-64-bit IDs; source/requests accept only
    -- positive signed-32-bit DUIDs. Widen casts before filtering so planner
    -- predicate reordering cannot turn an unsupported draft ID into an outage.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        -- Whether ParishSoft still lists the Ministry (true) or not (NULL);
        -- there is no local inactive override any more (#1267).
        CASE WHEN p.canonical::jsonb->'catalog_present'='true'::jsonb
            THEN true ELSE NULL END AS active,
        n.in_campaign
    FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT r.ministry_duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        WHERE s.campaign_id=x.id AND s.mode='live'
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(r.ministry_duid)
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
      AND (ministry_id::integer IS NULL OR n.duid::bigint=ministry_id)
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,r.assignee_id,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), summary AS MATERIALIZED (
    SELECT m.*,
        count(r.id) FILTER (WHERE r.action='join') AS joining,
        count(r.id) FILTER (WHERE r.action='leave') AS leaving,
        count(r.id) FILTER (
            WHERE r.state IN ('new','assigned','in_progress')) AS unresolved,
        count(r.id) FILTER (
            WHERE r.state IN ('resolved','closed_no_response')) AS completed,
        count(r.id) AS requests
    FROM ministries m LEFT JOIN requests r ON r.ministry_duid=m.duid
        AND r.revision=1 AND r.state NOT IN ('cancelled','superseded')
    GROUP BY m.duid,m.name,m.active,m.in_campaign
), summary_filtered AS MATERIALIZED (
    SELECT * FROM summary WHERE
        ((filters->>'activity')='any' OR ((filters->>'activity')='active' AND active IS TRUE)
            OR ((filters->>'activity')='inactive' AND active IS FALSE)
            OR ((filters->>'activity')='unavailable' AND active IS NULL))
        AND (ministry_id::integer IS NOT NULL OR (filters->>'search')=''
            OR position(lower((filters->>'search')) IN lower(name))>0
            OR position((filters->>'search') IN duid::text)>0)
), named AS MATERIALIZED (
    SELECT r.*,m.name AS ministry_name,p.canonical::jsonb AS person,
        CASE WHEN r.entity_kind='proposed_member'
            THEN s.answers->'proposed_members'->r.entity_key ELSE NULL END AS proposed,
        coalesce(nullif(btrim(concat_ws(' ',
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'middleName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'middle_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'last_name'
                END)),''),
            'Unavailable Member') AS member_name
    FROM requests r JOIN summary_filtered m ON m.duid=r.ministry_duid
    CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    WHERE ministry_id::integer IS NOT NULL AND r.action=request_action
        AND ((filters->>'history'='all') OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved' AND r.state IN ('new','assigned','in_progress')))
        AND ((filters->>'start')='' OR (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif((filters->>'start'),'')::date)
        AND ((filters->>'end')='' OR (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif((filters->>'end'),'')::date)
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR (entity_kind='member' AND position((filters->>'search') IN entity_key)>0)
), page AS MATERIALIZED (
    -- A Member DUID sort lists Members the Family added on the form (no
    -- DUID yet) last in either direction (#960).
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(member_name) END DESC,
        CASE WHEN (filters->>'sort')='newest' THEN submitted_at END DESC,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        CASE WHEN (filters->>'sort')='duid' AND entity_kind='member'
            THEN entity_key::bigint END NULLS LAST,
        CASE WHEN (filters->>'sort')='duid_desc' AND entity_kind='member'
            THEN entity_key::bigint END DESC NULLS LAST,id) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.ministry_duid,r.member_name,r.entity_kind,
        CASE WHEN r.entity_kind='member' THEN r.entity_key::bigint END AS member_duid,
        CASE WHEN r.entity_kind='proposed_member' THEN r.entity_key END AS proposed_id,
        r.action,r.state,r.outcome,r.submitted_at,
        r.revision=1 AS latest,
        -- Resolved in this statement so an export captures who held the work
        -- as of its data, not whoever holds that identity when it renders.
        (SELECT u.email FROM stewardship_portal_user u
            WHERE u.id=r.assignee_id) AS assignee,
        CASE WHEN r.action='leave' THEN (
            SELECT string_agg(DISTINCT p.canonical::jsonb->>'ministryRoleName',', '
                ORDER BY p.canonical::jsonb->>'ministryRoleName')
            FROM stewardship_snapshot_roster m
            JOIN stewardship_source_roster p ON p.id=m.payload_id
            WHERE m.snapshot_id=x.source_id AND r.person IS NOT NULL
                AND p.canonical::jsonb->>'member_key'=r.entity_key
                AND p.canonical::jsonb->>'ministry_key'=r.ministry_duid::text
                AND p.canonical::jsonb->'current'='true'::jsonb
        ) END AS current_role,
        CASE WHEN r.action='join' THEN coalesce(
            r.person->>'sex',r.proposed->>'gender') END AS gender,
        CASE WHEN r.action='join' AND
            pg_input_is_valid(coalesce(r.person->>'birthdate',r.proposed->>'birth_date'),'date')
            THEN CASE WHEN coalesce(r.person->>'birthdate',
                r.proposed->>'birth_date')::date<=x.report_date
                THEN extract(year FROM age(x.report_date,
                    coalesce(r.person->>'birthdate',r.proposed->>'birth_date')::date))::integer
                END END AS age,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_email','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS email_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_email'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS phone_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_phone'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'phones'
                ELSE jsonb_build_object('home',r.proposed->>'home_phone',
                    'mobile',r.proposed->>'mobile_phone',
                    'work',r.proposed->>'work_phone') END END AS phones,
        a.fields AS address
    FROM page r CROSS JOIN source x
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb AS value FROM stewardship_snapshot_contact m
        JOIN stewardship_source_contact p ON p.id=m.payload_id
        WHERE r.action='join' AND r.person IS NOT NULL AND m.snapshot_id=x.source_id
            AND m.source_key='member:'||r.entity_key
    ) c ON true
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb->'fields' AS fields
        FROM stewardship_snapshot_address m
        JOIN stewardship_source_address p ON p.id=m.payload_id
        WHERE r.action='join' AND (r.person IS NOT NULL OR r.proposed IS NOT NULL)
            AND m.snapshot_id=x.source_id
            AND m.source_key='family:'||r.family_duid::text||':primary'
    ) a ON true
), summary_page AS (
    SELECT * FROM summary_filtered ORDER BY
        CASE WHEN (filters->>'sort')='duid' THEN duid END,
        CASE WHEN (filters->>'sort')='duid_desc' THEN duid END DESC,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid
    LIMIT page_limit OFFSET CASE WHEN ministry_id::integer IS NULL
        THEN page_offset ELSE 0 END
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'authorization_scope',jsonb_build_object('capability','ministry_report',
        'operational',operational,'ministries',coalesce((SELECT jsonb_agg(duid ORDER BY duid)
            FROM ministries),'[]'::jsonb)),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'report_date',x.report_date),
    'total',CASE WHEN ministry_id::integer IS NULL
        THEN (SELECT count(*) FROM summary_filtered)
        ELSE (SELECT count(*) FROM filtered) END,
    'summaries',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY
        CASE WHEN (filters->>'sort')='duid' THEN duid END,
        CASE WHEN (filters->>'sort')='duid_desc' THEN duid END DESC,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid)
        FROM summary_page p),'[]'::jsonb),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_report_v3(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    ministry_id integer DEFAULT NULL, request_action text DEFAULT 'join',
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
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
    SELECT x.*,s.organization_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,statement_timestamp() AS observed_at,
        (statement_timestamp() AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date AS report_date
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Configuration permits signed-64-bit IDs; source/requests accept only
    -- positive signed-32-bit DUIDs. Widen casts before filtering so planner
    -- predicate reordering cannot turn an unsupported draft ID into an outage.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        -- Whether ParishSoft still lists the Ministry (true) or not (NULL);
        -- there is no local inactive override any more (#1267).
        CASE WHEN p.canonical::jsonb->'catalog_present'='true'::jsonb
            THEN true ELSE NULL END AS active,
        n.in_campaign
    FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT r.ministry_duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        WHERE s.campaign_id=x.id AND s.mode='live'
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(r.ministry_duid)
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
      AND (ministry_id::integer IS NULL OR n.duid::bigint=ministry_id)
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,r.assignee_id,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), summary AS MATERIALIZED (
    SELECT m.*,
        count(r.id) FILTER (WHERE r.action='join') AS joining,
        count(r.id) FILTER (WHERE r.action='leave') AS leaving,
        count(r.id) FILTER (
            WHERE r.state IN ('new','assigned','in_progress')) AS unresolved,
        count(r.id) FILTER (
            WHERE r.state IN ('resolved','closed_no_response')) AS completed,
        count(r.id) AS requests
    FROM ministries m LEFT JOIN requests r ON r.ministry_duid=m.duid
        AND r.revision=1 AND r.state NOT IN ('cancelled','superseded')
    GROUP BY m.duid,m.name,m.active,m.in_campaign
), summary_filtered AS MATERIALIZED (
    SELECT * FROM summary WHERE
        ((filters->>'activity')='any' OR ((filters->>'activity')='active' AND active IS TRUE)
            OR ((filters->>'activity')='inactive' AND active IS FALSE)
            OR ((filters->>'activity')='unavailable' AND active IS NULL))
        AND (ministry_id::integer IS NOT NULL OR (filters->>'search')=''
            OR position(lower((filters->>'search')) IN lower(name))>0
            OR position((filters->>'search') IN duid::text)>0)
), named AS MATERIALIZED (
    SELECT r.*,m.name AS ministry_name,p.canonical::jsonb AS person,
        CASE WHEN r.entity_kind='proposed_member'
            THEN s.answers->'proposed_members'->r.entity_key ELSE NULL END AS proposed,
        coalesce(nullif(btrim(concat_ws(' ',
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'middleName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'middle_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'last_name'
                END)),''),
            'Unavailable Member') AS member_name
    FROM requests r JOIN summary_filtered m ON m.duid=r.ministry_duid
    CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    WHERE ministry_id::integer IS NOT NULL AND r.action=request_action
        AND ((filters->>'history'='all') OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved' AND r.state IN ('new','assigned','in_progress')))
        -- Days in the viewer's browser zone (#558), as in the information report.
        AND ((filters->>'start')='' OR r.submitted_at>=(SELECT
            stewardship_browser_day_start_v1((filters->>'start')::date,filters->>'zone')))
        AND ((filters->>'end')='' OR r.submitted_at<(SELECT
            stewardship_browser_day_start_v1((filters->>'end')::date+1,filters->>'zone')))
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR (entity_kind='member' AND position((filters->>'search') IN entity_key)>0)
), page AS MATERIALIZED (
    -- A Member DUID sort lists Members the Family added on the form (no
    -- DUID yet) last in either direction (#960).
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(member_name) END DESC,
        CASE WHEN (filters->>'sort')='newest' THEN submitted_at END DESC,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        CASE WHEN (filters->>'sort')='duid' AND entity_kind='member'
            THEN entity_key::bigint END NULLS LAST,
        CASE WHEN (filters->>'sort')='duid_desc' AND entity_kind='member'
            THEN entity_key::bigint END DESC NULLS LAST,id) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.ministry_duid,r.member_name,r.entity_kind,
        CASE WHEN r.entity_kind='member' THEN r.entity_key::bigint END AS member_duid,
        CASE WHEN r.entity_kind='proposed_member' THEN r.entity_key END AS proposed_id,
        r.action,r.state,r.outcome,r.submitted_at,
        r.revision=1 AS latest,
        -- Resolved in this statement so an export captures who held the work
        -- as of its data, not whoever holds that identity when it renders.
        (SELECT u.email FROM stewardship_portal_user u
            WHERE u.id=r.assignee_id) AS assignee,
        CASE WHEN r.action='leave' THEN (
            SELECT string_agg(DISTINCT p.canonical::jsonb->>'ministryRoleName',', '
                ORDER BY p.canonical::jsonb->>'ministryRoleName')
            FROM stewardship_snapshot_roster m
            JOIN stewardship_source_roster p ON p.id=m.payload_id
            WHERE m.snapshot_id=x.source_id AND r.person IS NOT NULL
                AND p.canonical::jsonb->>'member_key'=r.entity_key
                AND p.canonical::jsonb->>'ministry_key'=r.ministry_duid::text
                AND p.canonical::jsonb->'current'='true'::jsonb
        ) END AS current_role,
        CASE WHEN r.action='join' THEN coalesce(
            r.person->>'sex',r.proposed->>'gender') END AS gender,
        CASE WHEN r.action='join' AND
            pg_input_is_valid(coalesce(r.person->>'birthdate',r.proposed->>'birth_date'),'date')
            THEN CASE WHEN coalesce(r.person->>'birthdate',
                r.proposed->>'birth_date')::date<=x.report_date
                THEN extract(year FROM age(x.report_date,
                    coalesce(r.person->>'birthdate',r.proposed->>'birth_date')::date))::integer
                END END AS age,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_email','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS email_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_email'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND coalesce(
                c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS phone_visibility,
        CASE WHEN r.action='join' AND (operational
            OR c.value->'publish_phone'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'phones'
                ELSE jsonb_build_object('home',r.proposed->>'home_phone',
                    'mobile',r.proposed->>'mobile_phone',
                    'work',r.proposed->>'work_phone') END END AS phones,
        a.fields AS address
    FROM page r CROSS JOIN source x
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb AS value FROM stewardship_snapshot_contact m
        JOIN stewardship_source_contact p ON p.id=m.payload_id
        WHERE r.action='join' AND r.person IS NOT NULL AND m.snapshot_id=x.source_id
            AND m.source_key='member:'||r.entity_key
    ) c ON true
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb->'fields' AS fields
        FROM stewardship_snapshot_address m
        JOIN stewardship_source_address p ON p.id=m.payload_id
        WHERE r.action='join' AND (r.person IS NOT NULL OR r.proposed IS NOT NULL)
            AND m.snapshot_id=x.source_id
            AND m.source_key='family:'||r.family_duid::text||':primary'
    ) a ON true
), summary_page AS (
    SELECT * FROM summary_filtered ORDER BY
        CASE WHEN (filters->>'sort')='duid' THEN duid END,
        CASE WHEN (filters->>'sort')='duid_desc' THEN duid END DESC,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid
    LIMIT page_limit OFFSET CASE WHEN ministry_id::integer IS NULL
        THEN page_offset ELSE 0 END
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'authorization_scope',jsonb_build_object('capability','ministry_report',
        'operational',operational,'ministries',coalesce((SELECT jsonb_agg(duid ORDER BY duid)
            FROM ministries),'[]'::jsonb)),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'report_date',x.report_date),
    'total',CASE WHEN ministry_id::integer IS NULL
        THEN (SELECT count(*) FROM summary_filtered)
        ELSE (SELECT count(*) FROM filtered) END,
    'summaries',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY
        CASE WHEN (filters->>'sort')='duid' THEN duid END,
        CASE WHEN (filters->>'sort')='duid_desc' THEN duid END DESC,
        CASE WHEN (filters->>'sort')='name_desc' THEN lower(name) END DESC,lower(name),duid)
        FROM summary_page p),'[]'::jsonb),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

CREATE OR REPLACE FUNCTION public.stewardship_ministry_leaders_at_v1(at_configuration uuid, at_campaign uuid, at_snapshot uuid)
    RETURNS TABLE(email text, ministry_duid integer, member_duid bigint)
    LANGUAGE plpgsql STABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    configuration_key uuid; campaign_values jsonb; snapshot_key uuid;
    leader_keys text[];
BEGIN
    -- As in 0042, the inputs are read on their own first, so the query below
    -- is planned with the snapshot as a known value.
    SELECT campaign.configuration_id, campaign.values, at_snapshot
      INTO configuration_key, campaign_values, snapshot_key
    FROM public.stewardship_campaign_configuration campaign
    WHERE campaign.record_id=at_campaign
      AND campaign.configuration_id=at_configuration
      AND campaign.values->'modules' ? 'ministry';
    IF snapshot_key IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
      INTO leader_keys
    FROM jsonb_array_elements_text(
        public.stewardship_ministry_leader_roles_v1(campaign_values)) role(name);
    -- Each MATERIALIZED step runs once: the current roster parsed once;
    -- its few distinct labels, each compared once (the planner would
    -- otherwise push the comparison down to every roster row); and the
    -- leaders' rows, so the keyed lookups after them run for those only.
    RETURN QUERY
    WITH roster AS MATERIALIZED (
        SELECT payload.member_key, payload.ministry_key, payload.canonical::jsonb AS doc
        FROM public.stewardship_snapshot_roster rm
        JOIN public.stewardship_source_roster payload ON payload.id=rm.payload_id
        WHERE rm.snapshot_id=snapshot_key
    ), labels AS MATERIALIZED (
        SELECT DISTINCT roster.doc->>'ministryRoleName' AS label FROM roster
    ), leader_labels AS MATERIALIZED (
        SELECT labels.label FROM labels
        WHERE public.stewardship_ministry_leader_role_key_v1(labels.label)=ANY(leader_keys)
    ), held AS MATERIALIZED (
        SELECT roster.member_key, roster.ministry_key
        FROM roster JOIN leader_labels ON leader_labels.label=roster.doc->>'ministryRoleName'
        WHERE roster.doc->'schema_version'='1'::jsonb
          AND roster.doc->'current'='true'::jsonb
    )
    SELECT DISTINCT lower(address.value->>'value'), duid.ministry::integer,
        CASE WHEN member.source_key ~ '^[0-9]{1,18}$' THEN member.source_key::bigint END
    FROM held
    JOIN public.stewardship_snapshot_member mm
        ON mm.snapshot_id=snapshot_key AND mm.source_key=held.member_key
    JOIN public.stewardship_source_member member ON member.id=mm.payload_id
    JOIN public.stewardship_snapshot_family fm
        ON fm.snapshot_id=snapshot_key AND fm.source_key=member.family_key
    JOIN public.stewardship_source_family family ON family.id=fm.payload_id
    JOIN public.stewardship_snapshot_ministry tm
        ON tm.snapshot_id=snapshot_key AND tm.source_key=held.ministry_key
    JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
    JOIN public.stewardship_snapshot_contact cm
        ON cm.snapshot_id=snapshot_key AND cm.source_key='member:' || member.source_key
    JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
    CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
        family.canonical::jsonb AS family_doc,
        ministry.canonical::jsonb AS ministry_doc,
        contact.canonical::jsonb AS contact_doc) parsed
    -- Each DUID is a positive signed 32-bit key; CASE orders the cast after
    -- the shape test.
    CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
        THEN ministry.source_key::bigint END AS ministry) duid
    CROSS JOIN LATERAL jsonb_array_elements(parsed.contact_doc->'emails') address(value)
    WHERE parsed.member_doc->'schema_version'='1'::jsonb
      AND parsed.family_doc->'schema_version'='1'::jsonb
      AND parsed.contact_doc->'schema_version'='1'::jsonb
      AND parsed.ministry_doc->'schema_version'='1'::jsonb
      AND parsed.member_doc->'active'='true'::jsonb
      AND parsed.family_doc->'active'='true'::jsonb
      AND parsed.ministry_doc->'catalog_present'='true'::jsonb
      AND duid.ministry BETWEEN 1 AND 2147483647
      AND campaign_values->'ministry_duids' @> jsonb_build_array(duid.ministry)
      AND address.value->'valid'='true'::jsonb
      AND jsonb_typeof(address.value->'value')='string';
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_leaders_without_user_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    current_configuration uuid; current_values jsonb; current_snapshot uuid;
    leader_keys text[];
    answer jsonb := jsonb_build_object('leaders', '[]'::jsonb, 'unreachable', 0);
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session whose user is an Administrator now,
    -- exactly as stewardship_member_names_for_v1 (0053) decides it.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=public.stewardship_admin_session_key_v1(admin_session) AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, true) THEN
        RAISE EXCEPTION 'Leader list requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- The current campaign, its configuration and the promoted snapshot, as
    -- 0042 reads them; no campaign asking about Ministries, or no promoted
    -- source, means no leaders.
    SELECT runtime.active_configuration_id, campaign.values, pointer.snapshot_id
      INTO current_configuration, current_values, current_snapshot
    FROM public.stewardship_system_configuration runtime
    JOIN public.stewardship_campaign_configuration campaign
        ON campaign.record_id=runtime.current_campaign_id
       AND campaign.configuration_id=runtime.active_configuration_id
    CROSS JOIN public.stewardship_source_current pointer
    WHERE pointer.singleton AND campaign.values->'modules' ? 'ministry';
    IF current_snapshot IS NOT NULL THEN
        SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
          INTO leader_keys
        FROM jsonb_array_elements_text(
            public.stewardship_ministry_leader_roles_v1(current_values)) role(name);
        WITH roster AS MATERIALIZED (
            SELECT payload.member_key, payload.ministry_key, payload.canonical::jsonb AS doc
            FROM public.stewardship_snapshot_roster rm
            JOIN public.stewardship_source_roster payload ON payload.id=rm.payload_id
            WHERE rm.snapshot_id=current_snapshot
        ), labels AS MATERIALIZED (
            SELECT DISTINCT roster.doc->>'ministryRoleName' AS label FROM roster
        ), leader_labels AS MATERIALIZED (
            SELECT labels.label FROM labels
            WHERE public.stewardship_ministry_leader_role_key_v1(labels.label)=ANY(leader_keys)
        ), held AS MATERIALIZED (
            SELECT roster.member_key, roster.ministry_key
            FROM roster JOIN leader_labels ON leader_labels.label=roster.doc->>'ministryRoleName'
            WHERE roster.doc->'schema_version'='1'::jsonb
              AND roster.doc->'current'='true'::jsonb
        ), led AS MATERIALIZED (
            -- 0042's joins and conditions, with the contact record optional.
            SELECT member.source_key AS member_key, duid.ministry AS ministry_duid,
                parsed.member_doc, parsed.family_doc,
                CASE WHEN contact.canonical::jsonb->'schema_version'='1'::jsonb
                    THEN contact.canonical::jsonb->'emails' END AS emails
            FROM held
            JOIN public.stewardship_snapshot_member mm
                ON mm.snapshot_id=current_snapshot AND mm.source_key=held.member_key
            JOIN public.stewardship_source_member member ON member.id=mm.payload_id
            JOIN public.stewardship_snapshot_family fm
                ON fm.snapshot_id=current_snapshot AND fm.source_key=member.family_key
            JOIN public.stewardship_source_family family ON family.id=fm.payload_id
            JOIN public.stewardship_snapshot_ministry tm
                ON tm.snapshot_id=current_snapshot AND tm.source_key=held.ministry_key
            JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
            LEFT JOIN public.stewardship_snapshot_contact cm
                ON cm.snapshot_id=current_snapshot AND cm.source_key='member:' || member.source_key
            LEFT JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
            CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
                family.canonical::jsonb AS family_doc,
                ministry.canonical::jsonb AS ministry_doc) parsed
            CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
                THEN ministry.source_key::bigint END AS ministry) duid
            WHERE parsed.member_doc->'schema_version'='1'::jsonb
              AND parsed.family_doc->'schema_version'='1'::jsonb
              AND parsed.ministry_doc->'schema_version'='1'::jsonb
              AND parsed.member_doc->'active'='true'::jsonb
              AND parsed.family_doc->'active'='true'::jsonb
              AND parsed.ministry_doc->'catalog_present'='true'::jsonb
              AND duid.ministry BETWEEN 1 AND 2147483647
              AND current_values->'ministry_duids' @> jsonb_build_array(duid.ministry)
        ), people AS MATERIALIZED (
            -- One row per leader Member: its "Last, first" name, whether it
            -- heads its household, its DUID and its listed addresses.
            SELECT DISTINCT ON (led.member_key) led.member_key,
                concat_ws(', ',
                    nullif(btrim(led.member_doc->>'lastName'), ''),
                    nullif(btrim(led.member_doc->>'firstName'), '')) AS name,
                EXISTS (SELECT 1 FROM jsonb_array_elements_text(
                            CASE WHEN jsonb_typeof(led.family_doc->'active_head_duids')='array'
                                THEN led.family_doc->'active_head_duids' END) head(duid)
                        WHERE head.duid=led.member_key) AS head,
                CASE WHEN led.member_key ~ '^[0-9]{1,18}$'
                    THEN led.member_key::bigint END AS duid,
                CASE WHEN jsonb_typeof(led.emails)='array' THEN led.emails
                    ELSE '[]'::jsonb END AS emails
            FROM led ORDER BY led.member_key
        ), addresses AS MATERIALIZED (
            -- Each valid address a leader Member lists, as sign-in matches it.
            SELECT DISTINCT people.member_key, lower(btrim(listed.value->>'value')) AS email
            FROM people CROSS JOIN LATERAL jsonb_array_elements(people.emails) listed(value)
            WHERE listed.value->'valid'='true'::jsonb
              AND jsonb_typeof(listed.value->'value')='string'
              AND length(btrim(listed.value->>'value')) BETWEEN 3 AND 254
        ), unused AS MATERIALIZED (
            -- Only addresses that are not a user in the active configuration.
            SELECT addresses.member_key, addresses.email FROM addresses
            WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_address_rule rule
                WHERE rule.configuration_id=current_configuration
                  AND rule.email=addresses.email)
        ), ranked AS (
            SELECT unused.email, people.name, people.duid, row_number() OVER (
                PARTITION BY unused.email
                ORDER BY people.name='', people.head DESC, people.name,
                    people.duid NULLS LAST) AS position
            FROM unused JOIN people ON people.member_key=unused.member_key
        ), answered AS (
            SELECT ranked.email, ranked.name, ranked.duid,
                (SELECT jsonb_agg(DISTINCT led.ministry_duid ORDER BY led.ministry_duid)
                 FROM unused JOIN led ON led.member_key=unused.member_key
                 WHERE unused.email=ranked.email) AS ministries
            FROM ranked WHERE ranked.position=1
        )
        SELECT jsonb_build_object(
            'leaders', coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'email', answered.email, 'name', answered.name,
                    'member_duid', answered.duid, 'ministries', answered.ministries)
                ORDER BY answered.name, answered.duid NULLS LAST, answered.email)
                FROM answered), '[]'::jsonb),
            'unreachable', (SELECT count(*) FROM people
                WHERE NOT EXISTS (SELECT 1 FROM addresses
                                  WHERE addresses.member_key=people.member_key)))
          INTO answer;
    END IF;
    -- The trace: who listed and how many rows came back, never an address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'user_leaders_listed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded',
            'count',jsonb_array_length(answer->'leaders')));
    RETURN answer;
END
$$;
ALTER FUNCTION public.stewardship_leaders_without_user_v1(text, uuid) SECURITY DEFINER;


-- Refuse to commit unless the table and both trigger functions are gone, no
-- routine still names the table, and each replaced function is installed
-- with its new body, its attributes and its earlier grants. Missing
-- functions are checked first, on their own, because PostgreSQL does not
-- short-circuit OR and the ::regprocedure casts below would otherwise fail
-- with their own error.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_ministry_activity') IS NOT NULL
       OR to_regprocedure('public.stewardship_ministry_projection_v1()') IS NOT NULL
       OR to_regprocedure('public.stewardship_ministry_activity_immutable_v1()') IS NOT NULL THEN
        RAISE EXCEPTION 'Migration 0091: the Ministry activity table or its functions remain';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
               WHERE n.nspname='public' AND p.prosrc LIKE '%stewardship_ministry_activity%') THEN
        RAISE EXCEPTION 'Migration 0091: a routine still reads Ministry activity';
    END IF;
    IF to_regprocedure('public.stewardship_campaign_pointer_v1()') IS NULL
       OR to_regprocedure('public.stewardship_chair_decisions_v1(uuid)') IS NULL
       OR to_regprocedure('public.stewardship_chair_seed_evidence_guard_v1()') IS NULL
       OR to_regprocedure('public.stewardship_ministry_activation_v1()') IS NULL
       OR to_regprocedure('public.stewardship_ministry_complete_v1()') IS NULL
       OR to_regprocedure('public.stewardship_response_ministry_visible_v1(uuid,uuid,uuid,integer)') IS NULL
       OR to_regprocedure('public.stewardship_ministry_report_v1(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)') IS NULL
       OR to_regprocedure('public.stewardship_ministry_report_v3(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)') IS NULL
       OR to_regprocedure('public.stewardship_leaders_without_user_v1(text,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_ministry_leaders_at_v1(uuid,uuid,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0091: a replaced function is missing';
    END IF;
    -- Each by its exact identity: the new body's marker, SECURITY DEFINER
    -- only for the Users page's definer, and the fixed search_path (with
    -- JIT off for the two report selections).
    IF (SELECT count(*) FROM (VALUES
            ('public.stewardship_campaign_pointer_v1()', false,
             'can only add current Ministries to an open campaign', false),
            ('public.stewardship_chair_decisions_v1(uuid)', false,
             'relationship_missing', false),
            ('public.stewardship_chair_seed_evidence_guard_v1()', false,
             'evidence IS DISTINCT FROM NEW.roster_keys THEN', false),
            ('public.stewardship_ministry_activation_v1()', false,
             'IF snapshot IS NOT NULL AND (seeded OR EXISTS (', false),
            ('public.stewardship_ministry_complete_v1()', false,
             'the ministries section must be empty', false),
            ('public.stewardship_response_ministry_visible_v1(uuid,uuid,uuid,integer)', false,
             'campaign.values->''ministry_duids'' @> to_jsonb(ministry_id)', false),
            ('public.stewardship_ministry_report_v1(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)', false,
             'THEN true ELSE NULL END AS active', true),
            ('public.stewardship_ministry_report_v3(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)', false,
             'THEN true ELSE NULL END AS active', true),
            ('public.stewardship_leaders_without_user_v1(text,uuid)', true,
             '''user_leaders_listed''', false),
            ('public.stewardship_ministry_leaders_at_v1(uuid,uuid,uuid)', false,
             'AND jsonb_typeof(address.value->''value'')=''string'';', false)
        ) expected(signature, definer, marker, jit)
        JOIN pg_proc p ON p.oid=expected.signature::regprocedure
        WHERE p.prosecdef=expected.definer
          AND strpos(p.prosrc, expected.marker)>0
          AND p.proconfig=CASE WHEN expected.jit
              THEN ARRAY['search_path=pg_catalog, public, pg_temp','jit=off']
              ELSE ARRAY['search_path=pg_catalog, public, pg_temp'] END)<>10 THEN
        RAISE EXCEPTION 'Migration 0091: a replaced function is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0091_acl') IS DISTINCT FROM (
        SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN (
            'stewardship_campaign_pointer_v1','stewardship_chair_decisions_v1',
            'stewardship_chair_seed_evidence_guard_v1','stewardship_ministry_activation_v1',
            'stewardship_ministry_complete_v1','stewardship_response_ministry_visible_v1',
            'stewardship_ministry_report_v1','stewardship_ministry_report_v3',
            'stewardship_leaders_without_user_v1','stewardship_ministry_leaders_at_v1')) THEN
        RAISE EXCEPTION 'Migration 0091: a replaced function''s grants changed';
    END IF;
END
$check$;
