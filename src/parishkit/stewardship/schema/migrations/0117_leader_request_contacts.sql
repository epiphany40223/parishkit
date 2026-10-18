-- Frozen forward migration file 0117 (the repository-wide file sequence):
-- Ministry leaders see a requesting Member's own contact details (#1591).
-- This file is installed by its Django migration in the reports app and must
-- never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the baseline function equals the
-- fresh-install baseline's. A fresh install runs the baseline, the earlier
-- files and then this; the baseline already carries the replaced packet body,
-- so the install ends in the same catalog as an upgraded database.
--
-- A Ministry leader saw only the email addresses and phone numbers a Member
-- publishes in ParishSoft, so a Member who asked to join or leave a Ministry
-- but publishes none could not be reached by its leaders. The Administrator
-- decided (2026-10-17, "show own contacts"): asking is consent to be
-- contacted, so for a Member's request to a Ministry that Ministry's leaders
-- see the requesting Member's own email and phones even when not published.
-- Nothing else widens: a request stands only while its latest version is not
-- withdrawn (cancelled) or replaced (superseded); a Member whose latest
-- live answer says they cannot participate in any ministries (physical
-- limitations) shares nothing unpublished on any of their requests, current
-- or history, until a later submission answers it for them without the box
-- (Administrator decision, 2026-10-18, applied per Member; a submission that
-- leaves the Member out, such as one reporting them moved out or deceased,
-- does not clear it); a Member with no standing
-- request to the Ministry, another Ministry's requests and every other
-- Member of the Family keep the publish flags; Administrators and Staff keep
-- operational contact; and each selection still reads only the Ministries in
-- the reader's SQL-derived scope.
--
-- Replaced functions, each copied whole from its newest definition (0094)
-- with only the consent column, the four contact expressions and an
-- 'answered' CTE changed. Both consent columns read the cannot-participate
-- answer from each Member's latest live submission that answers it for them
-- ('answered', the same text in both), not from the request's own: a
-- cannot-participate submission adds leaves only for the Ministries the
-- Member is on the roster of, so an earlier join that was declined, a leave a
-- roster refresh confirmed, or a request to a Ministry no longer offered would
-- otherwise keep sharing, depending only on roster state:
--   stewardship_ministry_packet_v1   (the baseline): the follow-up packet,
--                                    and the leave requests' contact details
--   stewardship_ministry_report_v3   (migration-owned, 0064): the join
--                                    requests' contact details, read through
--                                    stewardship_ministry_report_v4
-- stewardship_ministry_report_v1 is left as it is: only an export capture
-- with filters from before 0064 (no 'zone') reads it, and join and leave
-- list captures were retired (#1157), so it stays the stricter rule.
-- Unchanged: every filter, order, scope and other returned key. Neither
-- function is SECURITY DEFINER, before or after, so there is no ALTER
-- FUNCTION to repeat; they keep their settings and grants. The DO block below
-- checks prosecdef, the settings, the new rule and the ACLs recorded before
-- the replacement, and refuses the 0094 bodies. Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. Settings are
-- compared in sorted order: an installed database may hold them in the
-- order of a later ALTER FUNCTION ... SET. A transaction-local setting holds
-- them, not a temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0117_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_packet_v1',
        'stewardship_ministry_report_v3')), true);

-- stewardship_ministry_packet_v1: copied from its newest definition (0094);
-- only the consent column and the contact expressions changed.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_packet_v1(
    campaign_uuid uuid, selection bigint[], include_history boolean,
    operational boolean, ministry_scope bigint[]
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.timezone,cc.values,cc.start_date,cc.end_date,
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
    SELECT x.*,s.generation AS source_generation,s.promoted_at AS source_as_of,
        statement_timestamp() AS observed_at
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), scoped AS MATERIALIZED (
    -- Widen before filtering, as the Ministry report does, so an unsupported
    -- configured ID cannot become an outage through predicate reordering.
    SELECT n.duid::bigint AS duid,n.in_campaign FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign. A request for a Member no longer in
    -- ParishSoft (#1217) does not keep one listed (#1219), as it is never
    -- counted or listed under it.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT r.ministry_duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
        WHERE s.campaign_id=x.id AND s.mode='live'
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(r.ministry_duid)
          AND (r.entity_kind<>'member'
              OR stewardship_member_source_status_v1(
                  x.source_id,r.entity_key,f.family_duid)='current')
    ) n(duid,in_campaign)
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
), chairs AS MATERIALIZED (
    -- One pass over the snapshot roster for every Ministry at once. This runs
    -- inside the capture trigger while it holds the shared work lock, so a
    -- per-Ministry rescan would stall Staff follow-up for a parish-wide packet.
    -- Names only, from current roster evidence: an ASCII Chairperson role held
    -- by an active Member. Listing a name needs no contact or login.
    SELECT role.ministry_key,
        jsonb_agg(role.name ORDER BY lower(role.name),role.name) AS names
    FROM (SELECT DISTINCT rp.ministry_key,
            btrim(concat_ws(' ',mp.canonical::jsonb->>'firstName',
                mp.canonical::jsonb->>'lastName')) AS name
        FROM source x
        JOIN stewardship_snapshot_roster rm ON rm.snapshot_id=x.source_id
        JOIN stewardship_source_roster rp ON rp.id=rm.payload_id
        CROSS JOIN LATERAL (SELECT rp.canonical::jsonb AS payload) r
        JOIN stewardship_snapshot_member sm ON sm.snapshot_id=x.source_id
            AND sm.source_key=rp.member_key
        JOIN stewardship_source_member mp ON mp.id=sm.payload_id
        WHERE r.payload->'current'='true'::jsonb
            AND mp.canonical::jsonb->'active'='true'::jsonb
            AND (r.payload->>'ministryRoleName') ~ '^[ -~]+$'
            AND lower(r.payload->>'ministryRoleName')='chairperson'
    ) role WHERE role.name<>'' GROUP BY role.ministry_key
), ministries AS MATERIALIZED (
    SELECT d.duid,d.in_campaign,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        coalesce(c.names,'[]'::jsonb) AS chairs
    FROM scoped d CROSS JOIN source x
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=d.duid::text
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    LEFT JOIN chairs c ON c.ministry_key=d.duid::text
    WHERE selection IS NULL OR d.duid=ANY(selection::bigint[])
), answered AS MATERIALIZED (
    -- Each Member's current answer to cannot participate in any ministries
    -- (physical limitations): the one in the Family's latest live
    -- submission that answers it for that Member (lists them under
    -- 'service'). A Member so answered shares no unpublished contact with
    -- leaders on any of their requests, current or history, until a later
    -- submission answers it for them without the box (Administrator,
    -- #1591). A submission that leaves the Member out (reported moved out or
    -- deceased) does not clear it; a Member never answered is not hidden.
    -- One sort over the campaign's live submissions of Families with a
    -- request here, joined by Member, not a lookup per request.
    SELECT DISTINCT ON (s.family_id,g.kind,e.key) s.family_id,g.kind,e.key,
        e.value->'cannot_serve'='true'::jsonb AS cannot_serve
    FROM source x JOIN stewardship_submission s
        ON s.campaign_id=x.id AND s.mode='live'
    CROSS JOIN LATERAL (VALUES ('member','members'),
        ('proposed_member','proposed_members')) g(kind,grp)
    CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(
        s.answers->'service'->g.grp)='object'
        THEN s.answers->'service'->g.grp END) e
    WHERE s.family_id IN (SELECT q.family_id FROM stewardship_submission q
        JOIN stewardship_ministry_request r ON r.submission_id=q.id
        JOIN ministries m ON m.duid=r.ministry_duid
        WHERE q.campaign_id=x.id AND q.mode='live')
    ORDER BY s.family_id,g.kind,e.key,s.family_version DESC
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,r.submission_id,s.submitted_at,f.family_duid,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), latest AS MATERIALIZED (
    SELECT r.*,p.canonical::jsonb AS person,
        -- A standing request (this is its latest version) is its Member's
        -- consent to be contacted about it by the Ministry's leaders
        -- (#1591); a withdrawn one is not. Nor is any request of a Member
        -- whose current answer ('answered') is cannot participate in any
        -- ministries (physical limitations): those follow the publish
        -- flags (Administrator, #1591), whatever the roster holds.
        r.state NOT IN ('cancelled','superseded') AND a.cannot_serve IS NOT TRUE
            AS consented,
        CASE WHEN r.entity_kind='proposed_member'
            THEN s.answers->'proposed_members'->r.entity_key END AS proposed
    FROM requests r CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN answered a ON a.family_id=s.family_id
        AND a.kind=r.entity_kind AND a.key=r.entity_key
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    WHERE r.revision=1
        AND (include_history OR r.state IN ('new','assigned','in_progress'))
        -- Not a request for a Member no longer in ParishSoft (#1217).
        AND (r.entity_kind<>'member' OR stewardship_member_source_status_v1(
            x.source_id,r.entity_key,r.family_duid)='current')
), detail AS (
    SELECT r.id,r.ministry_duid,r.entity_kind,r.action,r.state,r.outcome,
        r.submitted_at,
        coalesce(nullif(btrim(concat_ws(' ',
            coalesce(r.person->>'firstName',r.proposed->>'first_name'),
            coalesce(r.person->>'lastName',r.proposed->>'last_name'))),''),
            -- Only current Members and Members the Family added are listed
            -- (#1217), so a missing name is a gap in the data (#1216).
            'Name not recorded in ParishSoft') AS member_name,
        CASE WHEN r.entity_kind='member' THEN r.entity_key::bigint END AS member_duid,
        CASE WHEN r.entity_kind='proposed_member' THEN r.entity_key END AS proposed_id,
        -- Leaders see published values, and the requesting Member's own on
        -- a standing request (#1591); Admins and Staff see operational ones.
        CASE WHEN NOT operational AND NOT r.consented AND coalesce(
            c.value->'publish_email','false'::jsonb)<>'true'::jsonb
            THEN 'not_published' ELSE 'available' END AS email_visibility,
        CASE WHEN operational OR r.consented
            OR c.value->'publish_email'='true'::jsonb
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN NOT operational AND NOT r.consented AND coalesce(
            c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
            THEN 'not_published' ELSE 'available' END AS phone_visibility,
        CASE WHEN operational OR r.consented
            OR c.value->'publish_phone'='true'::jsonb
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'phones'
                ELSE jsonb_build_object('home',r.proposed->>'home_phone',
                    'mobile',r.proposed->>'mobile_phone',
                    'work',r.proposed->>'work_phone') END END AS phones,
        h.email_contact_at,h.phone_contact_at,
        CASE WHEN r.outcome='other' THEN h.notes END AS notes
    FROM latest r CROSS JOIN source x
    LEFT JOIN LATERAL (
        SELECT p.canonical::jsonb AS value FROM stewardship_snapshot_contact m
        JOIN stewardship_source_contact p ON p.id=m.payload_id
        WHERE r.person IS NOT NULL AND m.snapshot_id=x.source_id
            AND m.source_key='member:'||r.entity_key
    ) c ON true
    LEFT JOIN LATERAL (
        SELECT (array_agg(v.notes ORDER BY w.depth,v.expected_version DESC))[1] AS notes,
            max(v.contact_at) FILTER (WHERE v.contact_channel='email') AS email_contact_at,
            max(v.contact_at) FILTER (WHERE v.contact_channel='phone') AS phone_contact_at
        FROM stewardship_ministry_workflow_chain_v1(r.id) w
        JOIN stewardship_ministry_revision v ON v.request_id=w.request_id
    ) h ON true
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM scoped),
    'authorization_scope',jsonb_build_object('capability','ministry_report',
        'operational',operational,'ministries',coalesce((SELECT jsonb_agg(duid ORDER BY duid)
            FROM scoped),'[]'::jsonb)),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'start_date',x.start_date,'end_date',x.end_date,
        -- Captured raw: the one campaign-year rule lives in the application
        -- and reads the label, then the financial period, then the start date.
        'year_label',x.values->>'year_label','financial',x.values->'financial'),
    'total',(SELECT count(*) FROM detail),
    -- An empty selected Ministry still gets its section: a packet with a
    -- missing page would read as "nothing to do" for the wrong reason.
    'sections',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'duid',m.duid,'name',m.name,'chairs',m.chairs,'in_campaign',m.in_campaign,
            'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ministry_duid'
                ORDER BY lower(d.member_name),d.member_name,d.action,d.id)
                FROM detail d WHERE d.ministry_duid=m.duid),'[]'::jsonb))
        ORDER BY lower(m.name),m.duid) FROM ministries m),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;

-- stewardship_ministry_report_v3: copied from its newest definition (0094);
-- only the consent column and the contact expressions changed.
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
    -- they are marked not in_campaign. A request for a Member no longer in
    -- ParishSoft (#1217) does not keep one listed (#1219), as it is never
    -- counted or listed under it.
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT r.ministry_duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
        WHERE s.campaign_id=x.id AND s.mode='live'
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(r.ministry_duid)
          AND (r.entity_kind<>'member'
              OR stewardship_member_source_status_v1(
                  x.source_id,r.entity_key,f.family_duid)='current')
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
      AND (ministry_id::integer IS NULL OR n.duid::bigint=ministry_id)
), answered AS MATERIALIZED (
    -- Each Member's current answer to cannot participate in any ministries
    -- (physical limitations): the one in the Family's latest live
    -- submission that answers it for that Member (lists them under
    -- 'service'). A Member so answered shares no unpublished contact with
    -- leaders on any of their requests, current or history, until a later
    -- submission answers it for them without the box (Administrator,
    -- #1591). A submission that leaves the Member out (reported moved out or
    -- deceased) does not clear it; a Member never answered is not hidden.
    -- One sort over the campaign's live submissions of Families with a
    -- request here, joined by Member, not a lookup per request.
    SELECT DISTINCT ON (s.family_id,g.kind,e.key) s.family_id,g.kind,e.key,
        e.value->'cannot_serve'='true'::jsonb AS cannot_serve
    FROM source x JOIN stewardship_submission s
        ON s.campaign_id=x.id AND s.mode='live'
    CROSS JOIN LATERAL (VALUES ('member','members'),
        ('proposed_member','proposed_members')) g(kind,grp)
    CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(
        s.answers->'service'->g.grp)='object'
        THEN s.answers->'service'->g.grp END) e
    WHERE s.family_id IN (SELECT q.family_id FROM stewardship_submission q
        JOIN stewardship_ministry_request r ON r.submission_id=q.id
        JOIN ministries m ON m.duid=r.ministry_duid
        WHERE q.campaign_id=x.id AND q.mode='live')
    ORDER BY s.family_id,g.kind,e.key,s.family_version DESC
), requests AS MATERIALIZED (
    SELECT r.id,r.entity_kind,r.entity_key,r.ministry_duid,r.action,r.state,
        r.outcome,r.assignee_id,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision,
        -- Whether the Member's latest request to this Ministry stands (is
        -- not withdrawn): their consent to be contacted about it by the
        -- Ministry's leaders (#1591). Every version of it follows the
        -- latest, as a leave request's details do. No request of a Member
        -- whose current answer ('answered') is cannot participate in any
        -- ministries (physical limitations) is consent (Administrator,
        -- #1591), whatever the roster holds; the packet reads the same.
        first_value(r.state NOT IN ('cancelled','superseded'))
            OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                r.ministry_duid ORDER BY s.family_version DESC,r.id)
            AND a.cannot_serve IS NOT TRUE
            AS consented
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
    LEFT JOIN answered a ON a.family_id=s.family_id
        AND a.kind=r.entity_kind AND a.key=r.entity_key
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
        -- Not a request for a Member no longer in ParishSoft (#1217).
        AND (r.entity_kind<>'member' OR stewardship_member_source_status_v1(
            (SELECT source_id FROM source),r.entity_key,r.family_duid)='current')
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
            -- Only current Members and Members the Family added are listed
            -- (#1217), so a missing name is a gap in the data (#1216).
            'Name not recorded in ParishSoft') AS member_name
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
        -- Not a request for a Member no longer in ParishSoft (#1217).
        AND (r.entity_kind<>'member' OR stewardship_member_source_status_v1(
            x.source_id,r.entity_key,r.family_duid)='current')
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
        -- Leaders see published values, and the requesting Member's own on
        -- a standing request (#1591); Admins and Staff see operational ones.
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND NOT r.consented AND coalesce(
                c.value->'publish_email','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS email_visibility,
        CASE WHEN r.action='join' AND (operational OR r.consented
            OR c.value->'publish_email'='true'::jsonb)
            THEN CASE WHEN r.entity_kind='member' THEN c.value->'emails'
                ELSE jsonb_build_array(jsonb_build_object(
                    'value',r.proposed->>'email')) END END AS emails,
        CASE WHEN r.action='join' THEN
            CASE WHEN NOT operational AND NOT r.consented AND coalesce(
                c.value->'publish_phone','false'::jsonb)<>'true'::jsonb
                THEN 'not_published' ELSE 'available' END END AS phone_visibility,
        CASE WHEN r.action='join' AND (operational OR r.consented
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

DO $check$
BEGIN
    -- Each replaced selection, once, invoker, stable, with its consent rule
    -- in both contact expressions of both kinds and no 0094 expression left.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_ministry_packet_v1',
              'stewardship_ministry_report_v3')
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          -- Both consents exclude every request of a Member whose current
          -- answer is cannot participate: each Member's latest live answer
          -- ('answered', one row per Family, kind and key, only from
          -- submissions that list them under service), never the request's
          -- own submission or a Family-wide latest one; v3 takes the
          -- chain's latest standing.
          AND p.prosrc LIKE '%), answered AS MATERIALIZED (
%SELECT DISTINCT ON (s.family_id,g.kind,e.key) s.family_id,g.kind,e.key,
        e.value->''cannot_serve''=''true''::jsonb AS cannot_serve
    FROM source x JOIN stewardship_submission s
        ON s.campaign_id=x.id AND s.mode=''live''
    CROSS JOIN LATERAL (VALUES (''member'',''members''),
        (''proposed_member'',''proposed_members'')) g(kind,grp)
    CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(
        s.answers->''service''->g.grp)=''object''
        THEN s.answers->''service''->g.grp END) e
    WHERE s.family_id IN (SELECT q.family_id FROM stewardship_submission q
        JOIN stewardship_ministry_request r ON r.submission_id=q.id
        JOIN ministries m ON m.duid=r.ministry_duid
        WHERE q.campaign_id=x.id AND q.mode=''live'')
    ORDER BY s.family_id,g.kind,e.key,s.family_version DESC
), requests AS MATERIALIZED (%'
          -- Anchored to the next line, so no extra join condition (which
          -- would read every Member as never answered) can follow.
          AND p.prosrc LIKE ('%LEFT JOIN answered a ON a.family_id=s.family_id
        AND a.kind=r.entity_kind AND a.key=r.entity_key
'
              || CASE WHEN p.proname='stewardship_ministry_report_v3'
                  THEN '), summary AS MATERIALIZED (
%'
                  ELSE '    LEFT JOIN stewardship_snapshot_member sm
%' END)
          AND p.prosrc NOT LIKE '%answers#>ARRAY[''service''%'
          AND p.prosrc NOT LIKE '%DISTINCT ON (s.family_id) s.family_id,s.id%'
          AND CASE WHEN p.proname='stewardship_ministry_report_v3'
              THEN p.prosrc LIKE '%first_value(r.state NOT IN (''cancelled'',''superseded''))
            OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                r.ministry_duid ORDER BY s.family_version DESC,r.id)
            AND a.cannot_serve IS NOT TRUE
            AS consented
%'
              ELSE p.prosrc LIKE '%r.state NOT IN (''cancelled'',''superseded'') AND a.cannot_serve IS NOT TRUE
            AS consented,%' END
          AND p.prosrc LIKE '%NOT operational AND NOT r.consented AND coalesce(
%c.value->''publish_email''%'
          AND p.prosrc LIKE '%NOT operational AND NOT r.consented AND coalesce(
%c.value->''publish_phone''%'
          AND p.prosrc LIKE '%operational OR r.consented
%OR c.value->''publish_email''=''true''::jsonb%'
          AND p.prosrc LIKE '%operational OR r.consented
%OR c.value->''publish_phone''=''true''::jsonb%'
          AND p.prosrc NOT LIKE '%NOT operational AND coalesce(%'
          AND p.prosrc NOT LIKE '%operational OR c.value->''publish_%'
          AND p.prosrc NOT LIKE '%(operational
%OR c.value->''publish_%')<>2 THEN
        RAISE EXCEPTION 'Migration 0117 (leaders see a requesting Member''s own contact) did not replace the selections';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_packet_v1',
            'stewardship_ministry_report_v3'))
        IS DISTINCT FROM current_setting('stewardship.migration_0117_acl') THEN
        RAISE EXCEPTION 'Migration 0117 changed the selections'' grants or settings';
    END IF;
END
$check$;
