-- Fresh-install Ministry follow-up history. This is not an upgrade migration.
CREATE TABLE "stewardship_ministry_revision" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL, "expected_version" bigint NOT NULL CHECK ("expected_version" >= 0), "request_key" uuid NOT NULL, "assignee_id" uuid NULL, "state" varchar(20) NOT NULL, "outcome" varchar(20) NULL, "notes" text NOT NULL, "contact_channel" varchar(10) NULL, "contact_at" timestamp with time zone NULL, "contact_notes" text NOT NULL, CONSTRAINT "ministry_revision_version" UNIQUE ("request_id", "expected_version"), CONSTRAINT "ministry_revision_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "ministry_revision_identity" CHECK (("actor_id" IS NOT NULL AND "expected_version" >= 1)), CONSTRAINT "ministry_revision_outcome" CHECK (((("outcome" IS NULL AND "state" IN ('new', 'assigned', 'in_progress')) OR ("outcome" IN ('joined', 'leave_confirmed', 'declined', 'duplicate', 'other') AND "state" = 'resolved') OR ("outcome" = 'no_response' AND "state" = 'closed_no_response')) AND (NOT ("outcome" = 'other' AND "outcome" IS NOT NULL) OR NOT ("notes" = '')))), CONSTRAINT "ministry_revision_assignment" CHECK (((NOT ("state" = 'new') OR "assignee_id" IS NULL) AND (NOT ("state" = 'assigned') OR "assignee_id" IS NOT NULL))), CONSTRAINT "ministry_revision_contact" CHECK ((("contact_at" IS NULL AND "contact_channel" IS NULL AND "contact_notes" = '') OR ("contact_at" IS NOT NULL AND "contact_channel" IN ('email', 'phone', 'in_person', 'other')))));
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

CREATE FUNCTION stewardship_ministry_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry follow-up history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO target FROM stewardship_ministry_request WHERE id=NEW.request_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM stewardship_submission
        WHERE id=target.submission_id AND mode='live';
    -- Closed, cancelled and superseded requests are immutable. A successor
    -- created by the Family invalidates every form bound to this request.
    IF target.id IS NULL OR campaign IS NULL
       OR target.state NOT IN ('new','assigned','in_progress')
       OR NOT stewardship_ministry_followup_authorized_v1(NEW.actor_id,target.ministry_duid)
       OR NOT stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM target.version
       OR NEW.expected_version>=9223372036854775807
       OR length(NEW.notes)>5000 OR length(NEW.contact_notes)>2000
    THEN RAISE EXCEPTION 'Ministry follow-up change lacks current authority/version'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned, so a contact cannot postdate its record.
    NEW.created_at:=statement_timestamp();
    IF (NEW.assignee_id IS NOT NULL AND NOT
            stewardship_ministry_followup_authorized_v1(NEW.assignee_id,target.ministry_duid))
       OR (NEW.outcome='joined' AND target.action<>'join')
       OR (NEW.outcome='leave_confirmed' AND target.action<>'leave')
       OR NEW.contact_at>NEW.created_at
    THEN RAISE EXCEPTION 'Ministry follow-up change is not valid for this request'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION stewardship_ministry_revision_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE target stewardship_ministry_request%ROWTYPE; campaign uuid;
BEGIN
    SELECT * INTO target FROM stewardship_ministry_request WHERE id=NEW.request_id;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=target.submission_id;
    -- The request guard proved the exact projection when it advanced this
    -- version. A later version in this transaction has its own revision.
    IF target.version<=NEW.expected_version
       OR (target.version=NEW.expected_version+1
           AND ROW(target.state,target.outcome,target.assignee_id)
               IS DISTINCT FROM ROW(NEW.state,NEW.outcome,NEW.assignee_id))
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
CREATE FUNCTION stewardship_ministry_followup_v1(
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
            'Unavailable Ministry') AS name
    FROM source x
    CROSS JOIN LATERAL jsonb_array_elements_text(x.values->'ministry_duids') n(duid)
    LEFT JOIN stewardship_snapshot_ministry m
        ON m.snapshot_id=x.source_id AND m.source_key=n.duid
    LEFT JOIN stewardship_source_ministry p ON p.id=m.payload_id
    WHERE n.duid::bigint BETWEEN 1 AND 2147483647
      AND (operational OR n.duid::bigint=ANY(ministry_scope::bigint[]))
), requests AS MATERIALIZED (
    SELECT r.*,s.submitted_at,f.family_duid,m.name AS ministry_name,
        row_number() OVER (PARTITION BY s.family_id,r.entity_kind,r.entity_key,
            r.ministry_duid ORDER BY s.family_version DESC,r.id) AS revision
    FROM source x
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_ministry_request r ON r.submission_id=s.id
    JOIN ministries m ON m.duid=r.ministry_duid
    JOIN stewardship_family_campaign f ON f.id=s.family_id
), named AS MATERIALIZED (
    SELECT r.*,coalesce(nullif(btrim(concat_ws(' ',
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'firstName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'first_name' END,
            CASE WHEN r.entity_kind='member' THEN p.canonical::jsonb->>'lastName'
                ELSE s.answers->'proposed_members'->r.entity_key->>'last_name' END)),''),
            'Unavailable Member') AS member_name
    FROM requests r CROSS JOIN source x
    JOIN stewardship_submission s ON s.id=r.submission_id
    LEFT JOIN stewardship_snapshot_member sm
        ON r.entity_kind='member' AND sm.snapshot_id=x.source_id
        AND sm.source_key=r.entity_key
    LEFT JOIN stewardship_source_member p ON p.id=sm.payload_id
        AND p.family_key=r.family_duid::text
    WHERE CASE WHEN request_uuid IS NOT NULL THEN r.id=request_uuid ELSE
        ((filters->>'history')='all' OR (r.revision=1
            AND r.state NOT IN ('cancelled','superseded')))
        AND ((filters->>'ministry')='' OR r.ministry_duid::text=(filters->>'ministry'))
        AND ((filters->>'action')='any' OR r.action=(filters->>'action'))
        AND ((filters->>'state')='any' OR r.state=(filters->>'state')
            OR ((filters->>'state')='unresolved'
                AND r.state IN ('new','assigned','in_progress')))
        AND ((filters->>'outcome')='any' OR r.outcome=(filters->>'outcome'))
        AND ((filters->>'assignee')='any'
            OR ((filters->>'assignee')='mine' AND r.assignee_id=viewer)
            OR ((filters->>'assignee')='unassigned' AND r.assignee_id IS NULL)
            OR r.assignee_id::text=(filters->>'assignee')) END
), filtered AS MATERIALIZED (
    SELECT * FROM named WHERE request_uuid IS NOT NULL OR (filters->>'search')=''
        OR position(lower((filters->>'search')) IN lower(member_name))>0
        OR position(lower((filters->>'search')) IN lower(ministry_name))>0
), page AS MATERIALIZED (
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN (filters->>'sort')='name' THEN lower(member_name) END,
        CASE WHEN (filters->>'sort')='ministry' THEN lower(ministry_name) END,
        CASE WHEN (filters->>'sort')='oldest' THEN submitted_at END,
        submitted_at DESC,id) AS ordinal
    FROM filtered ORDER BY ordinal LIMIT page_limit OFFSET page_offset
), detail AS (
    SELECT r.ordinal,r.id,r.version,r.ministry_duid,r.ministry_name,r.member_name,
        r.entity_kind,r.action,r.state,r.outcome,r.assignee_id,r.submitted_at,
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
    'ministries',coalesce((SELECT jsonb_agg(jsonb_build_object('duid',duid,'name',name)
        ORDER BY lower(name),duid) FROM ministries),'[]'::jsonb),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
FROM selected z LEFT JOIN source x ON true;
$$;
