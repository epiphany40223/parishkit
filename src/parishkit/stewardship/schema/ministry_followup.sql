-- Fresh-install Ministry follow-up history. This is not an upgrade migration.
CREATE TABLE "stewardship_ministry_revision" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL, "expected_version" bigint NOT NULL CHECK ("expected_version" >= 0), "request_key" uuid NOT NULL, "assignee_id" uuid NULL, "state" varchar(20) NOT NULL, "outcome" varchar(20) NULL, "notes" text NOT NULL, "contact_channel" varchar(10) NULL, "contact_at" timestamp with time zone NULL, "contact_notes" text NOT NULL, CONSTRAINT "ministry_revision_version" UNIQUE ("request_id", "expected_version"), CONSTRAINT "ministry_revision_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "ministry_revision_identity" CHECK (("actor_id" IS NOT NULL AND "expected_version" >= 1)), CONSTRAINT "ministry_revision_outcome" CHECK (((((outcome IS NULL) AND ((state)::text = ANY ((ARRAY['new'::character varying, 'assigned'::character varying, 'in_progress'::character varying])::text[]))) OR (((outcome)::text = ANY ((ARRAY['joined'::character varying, 'leave_confirmed'::character varying, 'declined'::character varying, 'duplicate'::character varying, 'other'::character varying])::text[])) AND ((state)::text = 'resolved'::text)) OR (((outcome)::text = 'no_response'::text) AND ((state)::text = 'closed_no_response'::text))) AND ((NOT (((outcome)::text = 'other'::text) AND (outcome IS NOT NULL))) OR (NOT (notes = ''::text))))), CONSTRAINT "ministry_revision_assignment" CHECK (((NOT ("state" = 'new') OR "assignee_id" IS NULL) AND (NOT ("state" = 'assigned') OR "assignee_id" IS NOT NULL))), CONSTRAINT "ministry_revision_contact" CHECK ((((contact_at IS NULL) AND (contact_channel IS NULL) AND (contact_notes = ''::text)) OR ((contact_at IS NOT NULL) AND ((contact_channel)::text = ANY ((ARRAY['email'::character varying, 'phone'::character varying, 'in_person'::character varying, 'other'::character varying])::text[]))))));
ALTER TABLE "stewardship_ministry_revision" ADD CONSTRAINT "stewardship_ministry_request_id_01b2bcc8_fk_stewardsh" FOREIGN KEY ("request_id") REFERENCES "stewardship_ministry_request" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_ministry_revision_correlation_id_0911383b" ON "stewardship_ministry_revision" ("correlation_id");
CREATE INDEX "stewardship_ministry_revision_request_id_01b2bcc8" ON "stewardship_ministry_revision" ("request_id");

-- Current authority only: Admin/Staff hold every Ministry, a leader holds the
-- Ministries assigned now. The same rule admits an actor and an assignee.
CREATE FUNCTION stewardship_ministry_followup_authorized_v1(user_uuid uuid, ministry integer)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce((SELECT scope->'operational'='true'::jsonb
            OR (scope->'ministries') @> to_jsonb(ministry)
        FROM (SELECT stewardship_ministry_scope_v1(user_uuid) AS scope) p),false)
$$;

-- A Family resubmission replaces the request row but not the Staff work. The
-- same-intent predecessors are exactly those superseded by this chain with the
-- same action; a changed action starts a fresh workflow with no inherited
-- history. Depth 0 is the request itself. Length is bounded by resubmissions.
CREATE FUNCTION stewardship_ministry_workflow_chain_v1(request_uuid uuid)
RETURNS TABLE(request_id uuid, depth integer)
LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH RECURSIVE chain AS (
        SELECT r.id,r.action,0 AS depth FROM stewardship_ministry_request r
            WHERE r.id=request_uuid
        UNION ALL
        SELECT p.id,p.action,c.depth+1 FROM chain c
            JOIN stewardship_ministry_request p
              ON p.superseded_by_id=c.id AND p.action=c.action
    )
    SELECT id,depth FROM chain
$$;

CREATE FUNCTION public.stewardship_ministry_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid; asked jsonb;
    reopen boolean; ticked boolean; undo boolean; confirmed boolean; operational boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry follow-up history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO target FROM stewardship_ministry_request WHERE id=NEW.request_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM stewardship_submission
        WHERE id=target.submission_id AND mode='live';
    -- Cancelled and superseded requests are immutable, and a successor
    -- created by the Family invalidates every form bound to this request. A
    -- resolved or closed request changes only by going back to review, or,
    -- one the parish added, by being withdrawn (#1684), below.
    reopen := target.state IN ('resolved','closed_no_response');
    -- Authority follows the request's current Ministry: the one an Edit
    -- carried forward, once edited (#1684).
    IF target.id IS NULL OR campaign IS NULL
       OR (target.state NOT IN ('new','assigned','in_progress') AND NOT reopen)
       OR NOT stewardship_ministry_followup_authorized_v1(NEW.actor_id,
           coalesce(target.reviewed_ministry_duid,target.ministry_duid))
       OR NOT stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM target.version
       OR NEW.expected_version>=9223372036854775807
       OR length(NEW.notes)>5000 OR length(NEW.contact_notes)>2000
       OR length(NEW.decision_note)>2000
    THEN RAISE EXCEPTION 'Ministry follow-up change lacks current authority/version'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned, so a contact cannot postdate its record.
    NEW.created_at:=statement_timestamp();
    SELECT cc.values->'ministry_duids' INTO asked FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        WHERE c.id=campaign;
    SELECT e.entered INTO ticked FROM stewardship_ministry_roster_entry e
        WHERE e.request_id=target.id ORDER BY e.sequence DESC LIMIT 1;
    -- Back to review of a terminal step (#1840): a resolved request kept
    -- resolved, with its outcome and Edit, undoes Entered in ParishSoft or
    -- a Confirmed refresh check at its version; a Staff member or
    -- Administrator only (never a Ministry leader), with a note.
    undo := reopen AND NEW.state='resolved';
    confirmed := EXISTS (SELECT 1 FROM stewardship_refresh_check k
        WHERE k.request_id=target.id AND k.item_version=target.version
          AND k.outcome='confirmed');
    operational := coalesce((stewardship_ministry_scope_v1(NEW.actor_id)->'operational')
        ='true'::jsonb,false);
    IF (NEW.assignee_id IS NOT NULL AND NOT
            stewardship_ministry_followup_authorized_v1(NEW.assignee_id,target.ministry_duid))
       OR (NEW.outcome='joined' AND target.action<>'join')
       OR (NEW.outcome='leave_confirmed' AND target.action<>'leave')
       OR NEW.contact_at>NEW.created_at
       -- Back to review: In progress with nothing carried forward, never
       -- while Staff ticked it Entered in ParishSoft (that tick is undone
       -- first), with a note when it reopens an ignored request, and by
       -- Staff with a note when it undoes a refresh's resolution (#1840). A
       -- request the parish added is withdrawn (cancelled) instead.
       OR (reopen AND NOT undo
           AND (NEW.state<>CASE WHEN target.parish_added THEN 'cancelled' ELSE 'in_progress' END
           OR NEW.outcome IS NOT NULL OR NEW.reviewed_ministry_duid IS NOT NULL
           OR NEW.contact_channel IS NOT NULL OR coalesce(ticked,false)
           OR (target.resolution_source_id IS NOT NULL
               AND (btrim(NEW.decision_note)='' OR NOT operational))
           OR (target.outcome NOT IN ('joined','leave_confirmed') AND btrim(NEW.decision_note)='')))
       -- The undo of Entered in ParishSoft or Confirmed (above): the
       -- decision as it stands, a person's (no refresh resolution), and a
       -- tick or a Confirmed check to undo.
       OR (undo AND (target.state<>'resolved'
           OR target.outcome NOT IN ('joined','leave_confirmed')
           OR NEW.outcome IS DISTINCT FROM target.outcome
           OR NEW.reviewed_ministry_duid IS DISTINCT FROM target.reviewed_ministry_duid
           OR NEW.contact_channel IS NOT NULL
           OR target.resolution_source_id IS NOT NULL
           OR NOT (coalesce(ticked,false) OR confirmed)
           OR btrim(NEW.decision_note)='' OR NOT operational))
       OR (NOT reopen AND NEW.state='cancelled')
       -- Edit: Accept with another of the campaign's Ministries carried
       -- forward, which the actor may also follow up (a leader edits only
       -- into a Ministry they lead).
       -- (The undo keeps the Edit it had, checked above.)
       OR (NEW.reviewed_ministry_duid IS NOT NULL AND NOT undo AND (NEW.state<>'resolved'
           OR NEW.outcome IS DISTINCT FROM
               CASE WHEN target.action='join' THEN 'joined' ELSE 'leave_confirmed' END
           OR NEW.reviewed_ministry_duid=target.ministry_duid
           OR NOT coalesce(asked @> to_jsonb(NEW.reviewed_ministry_duid),false)
           OR NOT stewardship_ministry_followup_authorized_v1(NEW.actor_id,NEW.reviewed_ministry_duid)
           -- Never a second current request about one Member and Ministry.
           OR EXISTS (SELECT 1 FROM stewardship_ministry_request other
               JOIN stewardship_submission history ON history.id=other.submission_id
               JOIN stewardship_submission own ON own.id=target.submission_id
               WHERE other.id<>target.id AND history.family_id=own.family_id
                 AND history.campaign_id=own.campaign_id AND history.mode='live'
                 AND other.entity_kind=target.entity_kind
                 AND other.entity_key=target.entity_key
                 AND NEW.reviewed_ministry_duid IN (other.ministry_duid,
                     other.reviewed_ministry_duid)
                 AND other.state NOT IN ('cancelled','superseded'))))
    THEN RAISE EXCEPTION 'Ministry follow-up change is not valid for this request'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_ministry_revision_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO target FROM stewardship_ministry_request WHERE id=NEW.request_id;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=target.submission_id;
    -- The request guard proved the exact projection when it advanced this
    -- version, the Ministry an Edit carries forward included (#1684). A
    -- later version in this transaction has its own revision.
    IF target.version<=NEW.expected_version
       OR (target.version=NEW.expected_version+1
           AND ROW(target.state,target.outcome,target.assignee_id,target.reviewed_ministry_duid)
               IS DISTINCT FROM ROW(NEW.state,NEW.outcome,NEW.assignee_id,NEW.reviewed_ministry_duid))
       OR NOT EXISTS(SELECT 1 FROM stewardship_audit_event a
           JOIN stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='ministry_request_updated' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.expected_version,'after_version',NEW.expected_version+1,
                 'ministry_duid',target.ministry_duid))
    THEN RAISE EXCEPTION 'Ministry follow-up requires its projection and audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_ministry_revision_guard
    BEFORE INSERT OR UPDATE OR DELETE ON stewardship_ministry_revision
    FOR EACH ROW EXECUTE FUNCTION stewardship_ministry_revision_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_ministry_revision_effect
    AFTER INSERT ON stewardship_ministry_revision DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_ministry_revision_effect_v1();

-- One MVCC statement owns a queue page or one request's detail, so names,
-- workflow versions, counts and rows cannot mix during concurrent edits. It
-- projects names and workflow only. Email, phone and address stay behind the
-- Ministry report's publish-flag rules; the interface links there instead of
-- duplicating that privacy decision. Latest intent is selected before state
-- filtering, including hidden Ministries, so history cannot resurrect a
-- replaced request. Notes and contact dates are read across the same-intent
-- chain and only for the returned page.
CREATE FUNCTION public.stewardship_ministry_followup_v1(
    campaign_uuid uuid, filters jsonb, operational boolean, ministry_scope bigint[],
    viewer uuid, request_uuid uuid DEFAULT NULL,
    page_limit integer DEFAULT NULL, page_offset integer DEFAULT 0
) RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
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
    SELECT x.*,s.promoted_at AS source_as_of,statement_timestamp() AS observed_at
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' ? 'ministry'
), ministries AS MATERIALIZED (
    -- Widen before filtering, as the Ministry report does, so an unsupported
    -- configured ID cannot become an outage through predicate reordering.
    SELECT n.duid::bigint AS duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
            'Unavailable Ministry') AS name,
        n.in_campaign
    FROM source x
    -- The campaign's selections plus any Ministry it no longer selects that
    -- still has a current request (not cancelled or replaced) from this
    -- campaign (#342): removing a Ministry never hides answers already given;
    -- they are marked not in_campaign. A request for a Member no longer in
    -- ParishSoft (#1217) does not keep one listed (#1219), as it is never
    -- counted or listed under it.
    -- Staff and Administrators still get it where they list such requests:
    -- by id, and with the presence filters 'gone' and 'any'. The Ministry an
    -- Edit carried forward (#1684) counts as the request's too.
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
              OR (operational AND (request_uuid IS NOT NULL
                  OR coalesce(filters->>'presence','current') IN ('gone','any')))
              OR stewardship_member_source_status_v1(
                  x.source_id,r.entity_key,f.family_duid)='current')
    ) n(duid,in_campaign)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
), requests AS MATERIALIZED (
    -- A request is in the reader's scope by the Family's Ministry or by the
    -- one an Edit carried forward (#1684), so the leaders of both see it.
    -- Its latest version is found across every request first, so scope
    -- never changes which version is latest. A Ministry outside the
    -- reader's scope is still named, from the same snapshot.
    SELECT q.*,
        coalesce(m.name,(SELECT coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
                'Unavailable Ministry')
            FROM stewardship_snapshot_ministry sm
            JOIN stewardship_source_ministry p ON p.id=sm.payload_id
            WHERE sm.snapshot_id=x.source_id AND sm.source_key=q.ministry_duid::text),
            'Unavailable Ministry') AS ministry_name,
        coalesce(m.in_campaign,
            x.values->'ministry_duids' @> to_jsonb(q.ministry_duid)) AS in_campaign,
        CASE WHEN q.reviewed_ministry_duid IS NOT NULL THEN coalesce(e.name,
            (SELECT coalesce(nullif(btrim(p.canonical::jsonb->>'name'),''),
                'Unavailable Ministry')
            FROM stewardship_snapshot_ministry sm
            JOIN stewardship_source_ministry p ON p.id=sm.payload_id
            WHERE sm.snapshot_id=x.source_id
              AND sm.source_key=q.reviewed_ministry_duid::text),
            'Unavailable Ministry') END AS reviewed_ministry_name
    FROM (
        SELECT r.*,s.submitted_at,f.family_duid,
            row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
                r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
        FROM source x
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_ministry_request r ON r.submission_id=s.id
        JOIN stewardship_family_campaign f ON f.id=s.family_id
    ) q CROSS JOIN source x
    LEFT JOIN ministries m ON m.duid=q.ministry_duid
    LEFT JOIN ministries e ON e.duid=q.reviewed_ministry_duid
    WHERE m.duid IS NOT NULL OR e.duid IS NOT NULL
), named AS MATERIALIZED (
    -- A Member is named "Last, first" (UX conventions, "Family and Member
    -- names", #932), so the name sort orders by surname; the search also
    -- matches the given-name-first form shown before.
    SELECT r.*,coalesce(nullif(concat_ws(', ',
            nullif(stewardship_name_strip_v1(n.last_name),''),
            nullif(stewardship_name_strip_v1(n.first_name),'')),''),
            -- No name (NULL) for a Member the data no longer has in this
            -- Family: the page shows its ParishSoft status badge alone, and
            -- it sorts after the names (#1216). A present Member, or one the
            -- Family added, without a usable name reads as such.
            CASE WHEN r.entity_kind<>'member' OR p.id IS NOT NULL
                THEN 'Name not recorded in ParishSoft' END) AS member_name,
        coalesce(nullif(btrim(concat_ws(' ',n.first_name,n.last_name)),''),
            CASE WHEN r.entity_kind<>'member' OR p.id IS NOT NULL
                THEN 'Name not recorded in ParishSoft' END) AS member_search_name,
        -- "Cannot participate in any ministries" (#247) explains a leave.
        coalesce(s.answers#>ARRAY['service',
            CASE WHEN r.entity_kind='member' THEN 'members' ELSE 'proposed_members' END,
            r.entity_key,'cannot_serve']='true'::jsonb,false) AS cannot_serve,
        -- Whether ParishSoft still has the Member in this Family (#1217),
        -- worked out only for the rows the filters below keep; NULL for a
        -- Member the Family added on the form.
        CASE WHEN r.entity_kind='member' THEN stewardship_member_source_status_v1(
            x.source_id,r.entity_key,r.family_duid) END AS member_status
    FROM requests r CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    CROSS JOIN LATERAL (SELECT
        CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
            ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
        CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
            ELSE s.answers->'proposed_members'->r.entity_key->>'last_name' END
    ) n(first_name,last_name)
    WHERE CASE WHEN request_uuid IS NOT NULL THEN r.id=request_uuid ELSE
        ((filters->>'history')='all' OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'ministry')='' OR r.ministry_duid::text=(filters->>'ministry')
            OR r.reviewed_ministry_duid::text=(filters->>'ministry'))
        AND ((filters->>'action')='any' OR r.action=(filters->>'action'))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved'
                AND r.state IN ('new','assigned','in_progress')))
        AND ((filters->>'outcome')='any' OR r.outcome=(filters->>'outcome'))
        AND ((filters->>'assignee')='any'
            OR ((filters->>'assignee')='mine' AND r.assignee_id=viewer)
            OR ((filters->>'assignee')='unassigned' AND r.assignee_id IS NULL)
            OR r.assignee_id::text=(filters->>'assignee')) END
        -- A request for a Member no longer in ParishSoft (#1217) never
        -- reaches a Ministry leader, even by its own id. Staff and
        -- Administrators open one by id, list only those with the
        -- presence filter 'gone' (none by default), and every request
        -- with 'any' (Roster changes to enter). The standing is worked
        -- out last, only for rows the cheaper tests keep.
        AND CASE WHEN operational AND (request_uuid IS NOT NULL
                OR coalesce(filters->>'presence','current')='any') THEN true
            WHEN r.entity_kind<>'member'
                THEN NOT (operational AND request_uuid IS NULL
                    AND coalesce(filters->>'presence','current')='gone')
            ELSE (stewardship_member_source_status_v1(
                x.source_id,r.entity_key,r.family_duid)<>'current')
                =(operational AND request_uuid IS NULL
                    AND coalesce(filters->>'presence','current')='gone') END
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE request_uuid IS NOT NULL OR (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR position(lower((filters->>'search')) IN lower(member_search_name))>0
        OR position(lower((filters->>'search')) IN lower(ministry_name))>0
        OR position(lower((filters->>'search')) IN lower(coalesce(reviewed_ministry_name,'')))>0
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='ministry' THEN lower(ministry_name) END,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        submitted_at DESC,id) AS ordinal
    FROM filtered ORDER BY ordinal LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.version,r.ministry_duid,r.ministry_name,r.in_campaign,r.member_name,
        r.entity_kind,r.action,r.state,r.outcome,r.assignee_id,r.submitted_at,
        r.cannot_serve,r.member_status,
        r.reviewed_ministry_duid,r.reviewed_ministry_name,r.parish_added,r.added_in_place_of_id,
        r.resolved_at,r.resolution_source_id IS NOT NULL AS source_resolved,
        r.revision=1 AS latest,
        r.state IN ('new','assigned','in_progress') AS open,
        h.notes,h.email_contact_at,h.phone_contact_at,h.last_contact_at
    FROM page r LEFT JOIN LATERAL (
        SELECT (array_agg(v.notes ORDER BY c.depth,v.expected_version DESC))[1] AS notes,
            max(v.contact_at) FILTER (WHERE v.contact_channel='email') AS email_contact_at,
            max(v.contact_at) FILTER (WHERE v.contact_channel='phone') AS phone_contact_at,
            max(v.contact_at) AS last_contact_at
        FROM stewardship_ministry_workflow_chain_v1(r.id) c
        JOIN stewardship_ministry_revision v ON v.request_id=c.request_id
    ) h ON true
)
SELECT CASE WHEN NOT z.values->'modules' ? 'ministry'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'authorized',EXISTS(SELECT 1 FROM ministries),
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at),
    'ministries',coalesce((SELECT jsonb_agg(jsonb_build_object('duid',duid,'name',name,'in_campaign',in_campaign)
        ORDER BY lower(name),duid) FROM ministries),'[]'::jsonb),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;
