-- Frozen forward migration file 0149 (the repository-wide file sequence;
-- provisional, the number is assigned centrally in merge order): a Member
-- the Family added on the form is linked to the ParishSoft Member Staff
-- later create for them, for the cannot-participate contact rule (#1670).
-- This file is installed by its Django migration in the responses app and
-- must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the baseline function equals
-- the fresh-install baseline's. A fresh install runs the baseline, the
-- earlier files and then this; the baseline already carries the replaced
-- packet body, so the install ends in the same catalog as an upgraded
-- database.
--
-- A Member added on the form has only the form's local UUID (the
-- 'proposed_member' key). Once Staff create the person in ParishSoft, the
-- Family answers for them under the new Member DUID, so a later
-- cannot-participate answer for that Member did not cover the earlier
-- added-Member requests, whose leaders kept seeing the contacts the Family
-- typed (data spec, "Proposed changes"; reports spec, Ministry change
-- summary). Additive except for the two selections:
--   * stewardship_added_member_link (new, migration-owned): one immutable
--     row per Family campaign and added Member, with the ParishSoft Member
--     DUID, the Entered by hand New Member change it rests on and the
--     promoted snapshot that showed the Member. Its guard admits an INSERT
--     only inside the promotion of the current snapshot (the owner check
--     refresh reconciliation uses; only the source worker's login is
--     granted INSERT), and only for a row the candidates function below
--     gives for that snapshot. UPDATE and DELETE are refused.
--   * stewardship_added_member_candidates_v1 (new, SECURITY DEFINER, for
--     the general worker and the schema owner only): the links the evidence
--     supports, never by matching names, email or birth dates. A Family's
--     only Entered by hand New Member without a link, and the snapshot's
--     only active, living Member of the Family who has no link and was not
--     in the Family's household on any live response up to the one that
--     first added the person (its 'members' answers, which the submission
--     guard makes the household exactly). Anything ambiguous gives no row.
--     It returns identifiers only; the worker has no grant on the answers.
--   * stewardship_ministry_packet_v1 (the baseline) and
--     stewardship_ministry_report_v3 (migration-owned, 0064), each copied
--     whole from its newest definition (0144) with only the 'answered' CTE
--     changed: an added Member and their linked ParishSoft Member are one
--     person, so a checked box as either key's current answer withholds
--     the requests under both. The link only adds a checked box, never
--     removes one, so a wrong link can only hide more, never share more.
--     Neither function is SECURITY DEFINER, before or after, so
--     there is no ALTER FUNCTION to repeat for them; they keep their
--     settings and grants.
-- The file ends with a DO block that refuses to commit unless all of this
-- is installed as declared, and refuses the 0144 bodies. Reversing needs
-- its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. Settings are
-- compared in sorted order: an installed database may hold them in the
-- order of a later ALTER FUNCTION ... SET. A transaction-local setting holds
-- them, not a temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0149_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_packet_v1',
        'stewardship_ministry_report_v3')), true);

CREATE TABLE "stewardship_added_member_link" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "family_id" uuid NOT NULL, "proposed_key" varchar(36) NOT NULL, "member_key" varchar(20) NOT NULL, "proposal_id" uuid NOT NULL, "snapshot_id" uuid NOT NULL, CONSTRAINT "added_member_link_proposed_once" UNIQUE ("family_id", "proposed_key"), CONSTRAINT "added_member_link_member_once" UNIQUE ("family_id", "member_key"), CONSTRAINT "added_member_link_identity" CHECK ((actor_id IS NULL)));
ALTER TABLE "stewardship_added_member_link" ADD CONSTRAINT "stewardship_added_me_family_id_a560e625_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_added_member_link" ADD CONSTRAINT "stewardship_added_me_proposal_id_c1f358fb_fk_stewardsh" FOREIGN KEY ("proposal_id") REFERENCES "stewardship_proposed_change" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_added_member_link_correlation_id_0d7f6d96" ON "stewardship_added_member_link" ("correlation_id");
CREATE INDEX "stewardship_added_member_link_family_id_a560e625" ON "stewardship_added_member_link" ("family_id");
CREATE INDEX "stewardship_added_member_link_proposal_id_c1f358fb" ON "stewardship_added_member_link" ("proposal_id");

-- The links one promoted snapshot's evidence supports for one campaign's
-- live responses (data spec, "Proposed changes"). Every condition must
-- hold, and each Family gives at most one row:
--   * entered: the newest Entered by hand (resolved_external) New Member
--     change for each local id with no link yet, and the Family version of
--     the first live response that added that person;
--   * single: the Family has exactly one such added Member;
--   * appeared: the snapshot's active, living Members of the Family with no
--     link, who were not in the household on any live response up to the
--     one that first added the person;
--   * exactly one Member appeared.
-- Called by promotion (responses/added_members.py), by the quick update's
-- skip check and by the link table's guard, which runs as the worker.
CREATE FUNCTION public.stewardship_added_member_candidates_v1(
    input_snapshot uuid, input_campaign uuid)
RETURNS TABLE(family_id uuid, proposal_id uuid, proposed_key text, member_key text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
#variable_conflict use_column
BEGIN
    IF session_user<>'pk_stewardship_worker' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only the general worker reads added Member links' USING ERRCODE='42501';
    END IF;
    RETURN QUERY
    WITH entered AS MATERIALIZED (
        SELECT DISTINCT ON (s.family_id,p.entity_key) s.family_id,
            p.id AS proposal_id,p.entity_key::text AS proposed_key,
            (SELECT min(f.family_version) FROM public.stewardship_submission f
                WHERE f.family_id=s.family_id AND f.mode='live'
                  AND (f.answers->'proposed_members') ? p.entity_key) AS first_version
        FROM public.stewardship_proposed_change p
        JOIN public.stewardship_submission s ON s.id=p.submission_id
        WHERE s.campaign_id=input_campaign AND s.mode='live'
          AND p.entity_kind='proposed_member' AND p.field='new_member'
          AND p.execution='resolved_external'
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_added_member_link l
              WHERE l.family_id=s.family_id AND l.proposed_key=p.entity_key)
        ORDER BY s.family_id,p.entity_key,s.family_version DESC
    ), single AS MATERIALIZED (
        SELECT e.* FROM entered e
        WHERE e.first_version IS NOT NULL
          AND (SELECT count(*) FROM entered o WHERE o.family_id=e.family_id)=1
    ), appeared AS MATERIALIZED (
        SELECT g.family_id,g.proposal_id,g.proposed_key,
            membership.source_key::text AS member_key
        FROM single g
        JOIN public.stewardship_family_campaign family ON family.id=g.family_id
        JOIN public.stewardship_source_member member
            ON member.family_key=family.family_duid::text
        JOIN public.stewardship_snapshot_member membership
            ON membership.payload_id=member.id AND membership.snapshot_id=input_snapshot
        WHERE member.canonical::jsonb->'active'='true'::jsonb
          AND member.canonical::jsonb->'deceased'='false'::jsonb
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_added_member_link l
              WHERE l.family_id=g.family_id AND l.member_key=membership.source_key::text)
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission h
              WHERE h.family_id=g.family_id AND h.mode='live'
                AND h.family_version<=g.first_version
                AND (h.answers->'members') ? membership.source_key::text)
    )
    SELECT a.family_id,a.proposal_id,a.proposed_key,a.member_key FROM appeared a
    WHERE (SELECT count(*) FROM appeared o WHERE o.family_id=a.family_id)=1
    ORDER BY a.family_id;
END $$;
ALTER FUNCTION public.stewardship_added_member_candidates_v1(uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_added_member_candidates_v1(uuid, uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_added_member_link_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Added Member links are immutable' USING ERRCODE='23514';
    END IF;
    -- Inside the promotion of the current snapshot: the owner check refresh
    -- reconciliation uses (live lease, ordered admission). Only the source
    -- worker's login holds INSERT.
    IF NEW.actor_id IS NOT NULL
       OR NOT stewardship_response_source_owner_v1(NEW.snapshot_id)
    THEN RAISE EXCEPTION 'Added Member links require the promoting source worker'
        USING ERRCODE='23514'; END IF;
    -- Exactly a link the snapshot's evidence supports, for the Family's own
    -- campaign. This evaluates the campaign's candidates once per inserted
    -- link; promotion inserts at most one per Family, and a parish has few,
    -- so the repeated read is accepted for one decision in one place.
    IF NOT EXISTS (SELECT 1 FROM stewardship_family_campaign f
        CROSS JOIN LATERAL stewardship_added_member_candidates_v1(NEW.snapshot_id,f.campaign_id) c
        WHERE f.id=NEW.family_id AND c.family_id=NEW.family_id
          AND c.proposal_id=NEW.proposal_id AND c.proposed_key=NEW.proposed_key
          AND c.member_key=NEW.member_key)
    THEN RAISE EXCEPTION 'An added Member link requires its evidence'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_added_member_link_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_added_member_link
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_added_member_link_guard_v1();

-- stewardship_ministry_packet_v1: copied from its newest definition (0144);
-- only the 'answered' CTE changed.
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

-- stewardship_ministry_report_v3: copied from its newest definition (0144);
-- only the 'answered' CTE changed.
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
    -- ministry_duid is the request's current Ministry (#1718): the one an
    -- Edit carried forward (#1684), whose leaders now decide it, else the
    -- Family's (family_ministry_duid). Versions and consent are found over
    -- whole chains (every version of a Member's
    -- requests to one Family Ministry) before scope applies, so the
    -- reader's scope never changes either.
    SELECT q.* FROM (
        SELECT r.id,r.entity_kind,r.entity_key,
            coalesce(r.reviewed_ministry_duid,r.ministry_duid) AS ministry_duid,
            r.ministry_duid AS family_ministry_duid,r.action,r.state,
            r.outcome,r.assignee_id,s.submitted_at,s.family_version,f.family_duid,s.id AS submission_id,
            row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision,
            -- Whether the Member's latest request to this Ministry stands (is
            -- not withdrawn): their consent to be contacted about it by the
            -- leaders of its current Ministry (#1591, #1718). Every version
            -- of it listed under that same Ministry follows the latest, as a
            -- leave request's details do; an earlier version listed under
            -- the Family's Ministry after an Edit carried the latest
            -- elsewhere is not consent to that Ministry's leaders, who no
            -- longer decide it. No request of a Member whose current answer
            -- ('answered') is cannot participate in any ministries (physical
            -- limitations) is consent (Administrator, #1591), whatever the
            -- roster holds; the packet reads the same.
            first_value(r.state NOT IN ('cancelled','superseded'))
                OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                    r.ministry_duid ORDER BY s.family_version DESC,r.id)
                AND coalesce(r.reviewed_ministry_duid,r.ministry_duid)
                    =first_value(coalesce(r.reviewed_ministry_duid,r.ministry_duid))
                    OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                        r.ministry_duid ORDER BY s.family_version DESC,r.id)
                AND a.cannot_serve IS NOT TRUE
                AS consented
        FROM source x
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
        LEFT JOIN answered a ON a.family_id=s.family_id
            AND a.kind=r.entity_kind AND a.key=r.entity_key
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
    ) q WHERE EXISTS (SELECT 1 FROM ministries m
        WHERE m.duid IN (q.ministry_duid,q.family_ministry_duid))
), summary AS MATERIALIZED (
    -- An edited request counts under both its Ministries, as the follow-up
    -- selection lists it under either (#1684), so each count link lists
    -- exactly what it counts; its rows and contact details are listed under
    -- its current Ministry only (named, below), so a one-Ministry read can
    -- count a request it does not list.
    SELECT m.*,
        count(r.id) FILTER (WHERE r.action='join') AS joining,
        count(r.id) FILTER (WHERE r.action='leave') AS leaving,
        count(r.id) FILTER (
            WHERE r.state IN ('new','assigned','in_progress')) AS unresolved,
        count(r.id) FILTER (
            WHERE r.state IN ('resolved','closed_no_response')) AS completed,
        count(r.id) AS requests
    FROM ministries m LEFT JOIN requests r
        ON m.duid IN (r.ministry_duid,r.family_ministry_duid)
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
    -- The link table's guard, BEFORE each row's INSERT, UPDATE and DELETE
    -- (tgtype ROW|BEFORE|INSERT|DELETE|UPDATE = 31), invoker, with the
    -- owner check and the candidates check.
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_added_member_link'::regclass
              AND NOT t.tgisinternal AND t.tgenabled='O' AND NOT p.prosecdef
              AND t.tgtype=31
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND p.proname='stewardship_added_member_link_guard_v1'
              AND p.prosrc LIKE '%TG_OP<>''INSERT''%'
              AND p.prosrc LIKE '%stewardship_response_source_owner_v1(NEW.snapshot_id)%'
              AND p.prosrc LIKE '%stewardship_added_member_candidates_v1(NEW.snapshot_id,f.campaign_id)%'
              AND p.prosrc LIKE '%NEW.created_at:=statement_timestamp();%')<>1
    THEN
        RAISE EXCEPTION 'The added Member link table is not guarded as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_added_member_link'::regclass
              AND conname IN ('added_member_link_proposed_once',
                              'added_member_link_member_once',
                              'added_member_link_identity'))<>3
    THEN
        RAISE EXCEPTION 'The added Member link table lacks its checks';
    END IF;
    -- No privilege for PUBLIC on the link table; the runtime grants name
    -- each login's own.
    IF EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.oid='public.stewardship_added_member_link'::regclass
              AND a.grantee=0)
    THEN
        RAISE EXCEPTION 'The added Member link table is granted to PUBLIC';
    END IF;
    -- The candidates function: one definition, SECURITY DEFINER (ALTER
    -- repeated above), stable, set-returning, its search path pinned, no
    -- PUBLIC execute, the caller check and every evidence condition.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_added_member_candidates_v1')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
            WHERE p.oid='public.stewardship_added_member_candidates_v1(uuid,uuid)'::regprocedure
              AND p.prosecdef AND p.provolatile='s' AND p.proretset
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT coalesce(aclcontains(p.proacl,makeaclitem(0,p.proowner,'EXECUTE',false)),true)
              AND p.prosrc LIKE '%session_user<>''pk_stewardship_worker''%'
              AND p.prosrc LIKE '%p.execution=''resolved_external''%'
              AND p.prosrc LIKE '%(SELECT count(*) FROM entered o WHERE o.family_id=e.family_id)=1%'
              AND p.prosrc LIKE '%h.family_version<=g.first_version%'
              AND p.prosrc LIKE '%(SELECT count(*) FROM appeared o WHERE o.family_id=a.family_id)=1%')
    THEN
        RAISE EXCEPTION 'The added Member candidates function is not installed as declared';
    END IF;
    -- Each replaced selection, once, invoker, stable, reading the links in
    -- its 'answered' CTE, with no 0144 'answered' left.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_ministry_packet_v1',
              'stewardship_ministry_report_v3')
          AND NOT p.prosecdef AND p.provolatile='s'
          AND p.prorettype='jsonb'::regtype
          AND p.prosrc LIKE '%    SELECT v.family_id,o.kind,o.key,bool_or(v.cannot_serve) AS cannot_serve FROM (
    SELECT DISTINCT ON (s.family_id,g.kind,e.key) s.family_id,g.kind,e.key,
%    ORDER BY s.family_id,g.kind,e.key,s.family_version DESC
    ) v
    LEFT JOIN stewardship_added_member_link l ON l.family_id=v.family_id
        AND ((v.kind=''member'' AND l.member_key=v.key)
            OR (v.kind=''proposed_member'' AND l.proposed_key=v.key))
%    WHERE o.key IS NOT NULL
    GROUP BY v.family_id,o.kind,o.key
), requests AS MATERIALIZED (%'
          AND p.prosrc NOT LIKE '%    ORDER BY s.family_id,g.kind,e.key,s.family_version DESC
), requests AS MATERIALIZED (%'
          AND p.prosrc LIKE '%AND a.cannot_serve IS NOT TRUE%')<>2 THEN
        RAISE EXCEPTION 'Migration 0149 (added Member links) did not replace the selections';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_ministry_packet_v1',
            'stewardship_ministry_report_v3'))
        IS DISTINCT FROM current_setting('stewardship.migration_0149_acl') THEN
        RAISE EXCEPTION 'Migration 0149 changed the selections'' grants or settings';
    END IF;
END
$check$;
