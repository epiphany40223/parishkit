-- Multi-Ministry follow-up packet. One statement owns the complete immutable
-- capture: every selected Ministry in the caller's current scope, its active
-- chair names, and one row per latest effective join or leave request.
--
-- Unlike the Ministry report, packets show publish-scoped contacts for both
-- actions, and unlike the follow-up queue they do carry contact payloads, so
-- this is its own projection rather than a wider version of either. Workflow
-- notes remain private: they are projected only for the outcome `other`, whose
-- packet label is defined as "Other with its notes/reference".
--
-- Latest intent is selected before state filtering, so the history option can
-- add resolved and withdrawn requests but can never resurrect a superseded one.
-- `selection` is NULL for every authorized Ministry or an explicit DUID array;
-- either way it is intersected with current scope, never trusted as authority.
CREATE FUNCTION public.stewardship_ministry_packet_v1(
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
    -- counted or listed under it. A request edited into another Ministry
    -- (#1684) holds both, as the follow-up selection lists it (#1718).
    CROSS JOIN LATERAL (
        SELECT n.duid,true FROM jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
        UNION
        SELECT DISTINCT d.duid::text,false
        FROM stewardship_submission s
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
        CROSS JOIN LATERAL (VALUES (r.ministry_duid),(r.reviewed_ministry_duid)) d(duid)
        WHERE s.campaign_id=x.id AND s.mode='live' AND d.duid IS NOT NULL
          AND r.state NOT IN ('cancelled','superseded')
          AND NOT x.values->'ministry_duids' @> to_jsonb(d.duid)
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
    -- Only an entry whose box is a JSON boolean is an answer, so a
    -- malformed one (which the guards refuse) cannot clear the box.
    -- A Member the Family added and the ParishSoft Member a refresh linked
    -- them to (stewardship_added_member_link, #1670) are one person, so a
    -- checked box as either key's current answer also withholds the other
    -- key's requests. The link only ever adds a checked box: it never lets
    -- a request share that would not share without it, so a link the
    -- evidence got wrong can only hide more.
    -- One sort over the campaign's live submissions of Families with a
    -- request here, joined by Member, not a lookup per request.
    SELECT v.family_id,o.kind,o.key,bool_or(v.cannot_serve) AS cannot_serve FROM (
    SELECT DISTINCT ON (s.family_id,g.kind,e.key) s.family_id,g.kind,e.key,
        e.value->'cannot_serve'='true'::jsonb AS cannot_serve
    FROM source x JOIN stewardship_submission s
        ON s.campaign_id=x.id AND s.mode='live'
    CROSS JOIN LATERAL (VALUES ('member','members'),
        ('proposed_member','proposed_members')) g(kind,grp)
    CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(
        s.answers->'service'->g.grp)='object'
        THEN s.answers->'service'->g.grp END) e
    WHERE jsonb_typeof(e.value->'cannot_serve')='boolean'
        AND s.family_id IN (SELECT q.family_id FROM stewardship_submission q
        JOIN stewardship_ministry_request r ON r.submission_id=q.id
        JOIN ministries m ON m.duid IN (r.ministry_duid,r.reviewed_ministry_duid)
        WHERE q.campaign_id=x.id AND q.mode='live')
    ORDER BY s.family_id,g.kind,e.key,s.family_version DESC
    ) v
    LEFT JOIN stewardship_added_member_link l ON l.family_id=v.family_id
        AND ((v.kind='member' AND l.member_key=v.key)
            OR (v.kind='proposed_member' AND l.proposed_key=v.key))
    CROSS JOIN LATERAL (VALUES (v.kind,v.key),
        (CASE v.kind WHEN 'member' THEN 'proposed_member' ELSE 'member' END,
            CASE v.kind WHEN 'member' THEN l.proposed_key::text
                ELSE l.member_key::text END)) o(kind,key)
    WHERE o.key IS NOT NULL
    GROUP BY v.family_id,o.kind,o.key
), requests AS MATERIALIZED (
    -- A request is listed, with its contact details, only under its current
    -- Ministry (#1718): the one an Edit carried forward (#1684), whose
    -- leaders now decide it, else the Family's. The versions are numbered
    -- over whole chains (every version of a Member's requests to one Family
    -- Ministry) before scope applies, so the reader's scope never changes
    -- which version is latest.
    SELECT q.* FROM (
        SELECT r.id,r.entity_kind,r.entity_key,
            coalesce(r.reviewed_ministry_duid,r.ministry_duid) AS ministry_duid,
            r.action,r.state,r.outcome,r.submission_id,s.submitted_at,f.family_duid,
            row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
        FROM source x
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
        -- Only whole chains a scoped Ministry holds, as the Family's or an
        -- Edit's, are read; the subquery names the campaign by its
        -- parameter, so it runs once rather than per row.
        WHERE r.ministry_duid IN (SELECT duid FROM ministries)
           OR (s.family_id,r.entity_kind,r.entity_key,r.ministry_duid) IN (
               SELECT es.family_id,e.entity_kind,e.entity_key,e.ministry_duid
               FROM stewardship_submission es
               JOIN stewardship_ministry_request e ON e.submission_id=es.id
               WHERE es.campaign_id=campaign_uuid AND es.mode='live'
                 AND e.reviewed_ministry_duid IN (SELECT duid FROM ministries))
    ) q JOIN ministries m ON m.duid=q.ministry_duid
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
