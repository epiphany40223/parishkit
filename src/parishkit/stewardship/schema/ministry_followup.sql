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
