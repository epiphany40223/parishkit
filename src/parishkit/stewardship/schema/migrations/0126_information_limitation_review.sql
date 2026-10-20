-- Frozen forward migration file 0126 (the repository-wide file sequence;
-- provisional until it is assigned in merge order): Additional information
-- and Limitations adopt the review pipeline (#1685). This file is installed
-- by its Django migration in the responses app and must never change once
-- released; tests/stewardship/test_schema_migration_files.py pins its digest
-- and checks that its copy of the replaced function equals the fresh-install
-- baseline's (schema/information_followup.sql).
--
-- Additional information. Each Staff revision gains review_decision
-- (accepted, ignored or none), which every revision from now on states; a
-- CHECK ties it to the follow-up flags (accepted: needed or completed;
-- ignored and none: neither). Older revisions keep NULL: the pipeline reads
-- a current request whose latest revision is older and left follow-up not
-- needed as Ignored, so no existing row is written. The revision guard
-- (stewardship_information_revision_guard_v1, information_followup.sql's
-- body) is replaced to require the decision, a note whenever an Ignored
-- request leaves Ignored, and To review for an Ignore. It is not SECURITY
-- DEFINER (the baseline has
-- no ALTER for it), so nothing else is restored.
--
-- Limitations. stewardship_limitation_review (new, migration-owned) holds
-- one immutable row per decision on a cannot-participate (Member) or
-- cannot-attend (Family) answer; nothing is stored when a Family submits.
-- stewardship_limitation_answer_v1 reads one answer from a response,
-- stewardship_limitation_stated_v1 whether a response states it at all, and
-- stewardship_limitation_in_force_v1 whether a decision still holds: no
-- later live response of the Family answers the other way. The
-- table's guard admits INSERT only, from a current Staff member or
-- Administrator, in a campaign that admits changes, on the Family's
-- current effective live response that gives the answer, at the answer's
-- current version (its number of decisions), as a step the decision in
-- force allows (To review: accepted or ignored; To handle: none or
-- handled; Ignored: none, with a note; Handled: nothing). A deferred check
-- pairs each row with its limitation_reviewed audit event.
-- stewardship_limitations_report_v2 is v1 (0094's body) plus each row's
-- answer key, Family campaign record, response and latest decision, and
-- whether that decision is in force. None of the new functions is SECURITY
-- DEFINER. The file ends with a DO block that refuses to commit unless all
-- of this is installed.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE "stewardship_information_revision" ADD COLUMN "review_decision" varchar(12) NULL;
ALTER TABLE "stewardship_information_revision" ADD CONSTRAINT "information_revision_decision" CHECK (((review_decision IS NULL) OR (follow_up_needed AND ((review_decision)::text = 'accepted'::text)) OR (followed_up AND ((review_decision)::text = 'accepted'::text)) OR ((NOT follow_up_needed) AND (NOT followed_up) AND ((review_decision)::text = ANY ((ARRAY['ignored'::character varying, 'none'::character varying])::text[])))));

CREATE OR REPLACE FUNCTION public.stewardship_information_revision_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE item stewardship_additional_information%ROWTYPE;
        prior stewardship_information_revision%ROWTYPE; campaign uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Staff follow-up history is immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO item FROM stewardship_additional_information WHERE id=NEW.item_id FOR UPDATE;
    SELECT campaign_id INTO campaign FROM stewardship_submission WHERE id=item.submission_id AND mode='live';
    -- Every revision states its review decision (#1685); a CHECK ties it to
    -- the follow-up flags.
    IF item.id IS NULL OR campaign IS NULL
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(campaign,true)
       OR NEW.expected_version IS DISTINCT FROM item.version
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.notes)>5000
       OR (item.followed_up_at IS NOT NULL AND NOT NEW.followed_up AND NOT NEW.confirm_clear)
       OR NEW.review_decision IS NULL
    THEN RAISE EXCEPTION 'Staff follow-up change lacks current authority/version'
        USING ERRCODE='23514'; END IF;
    SELECT * INTO prior FROM stewardship_information_revision
        WHERE item_id=item.id ORDER BY expected_version DESC LIMIT 1;
    -- An Ignored request (its latest revision ignored it, or, saved before
    -- migration 0126, left follow-up not needed) leaves Ignored, whether
    -- back to review or straight to follow-up, only with a new note saying
    -- why, as the review pipeline requires: the request's earlier notes,
    -- which a revision carries forward, are not one.
    IF NEW.review_decision<>'ignored'
       AND (btrim(NEW.notes)='' OR NEW.notes=prior.notes)
       AND prior.id IS NOT NULL AND NOT item.follow_up_needed
       AND item.followed_up_at IS NULL
       AND coalesce(prior.review_decision,'ignored')='ignored'
    THEN RAISE EXCEPTION 'Reopening an ignored request needs a note'
        USING ERRCODE='23514'; END IF;
    -- Only a request To review (or already Ignored) is ignored: one whose
    -- follow-up is needed or done goes back to review first.
    IF NEW.review_decision='ignored'
       AND (item.follow_up_needed OR item.followed_up_at IS NOT NULL)
    THEN RAISE EXCEPTION 'Only a request to review can be ignored'
        USING ERRCODE='23514'; END IF;
    -- Attribution is database-owned. Notes-only edits preserve who completed the
    -- work and when; clearing removes the current projection, never old history.
    NEW.created_at:=statement_timestamp();
    NEW.followed_up_at:=NULL;
    NEW.followed_up_by_id:=NULL;
    IF NEW.followed_up THEN
        IF item.followed_up_at IS NOT NULL THEN
            IF prior.followed_up_at IS DISTINCT FROM item.followed_up_at
               OR prior.followed_up_by_id IS NULL THEN
                RAISE EXCEPTION 'Follow-up attribution history is unavailable' USING ERRCODE='23514';
            END IF;
            NEW.followed_up_at:=prior.followed_up_at;
            NEW.followed_up_by_id:=prior.followed_up_by_id;
        ELSE
            NEW.followed_up_at:=NEW.created_at;
            NEW.followed_up_by_id:=NEW.actor_id;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TABLE "stewardship_limitation_review" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "kind" varchar(16) NOT NULL, "member_key" varchar(64) NOT NULL, "expected_version" bigint NOT NULL CHECK ("expected_version" >= 0), "request_key" uuid NOT NULL, "decision" varchar(12) NOT NULL, "note" text NOT NULL, "family_id" uuid NOT NULL, "submission_id" uuid NOT NULL, CONSTRAINT "limitation_review_version" UNIQUE ("family_id", "kind", "member_key", "expected_version"), CONSTRAINT "limitation_review_replay" UNIQUE ("actor_id", "request_key"), CONSTRAINT "limitation_review_identity" CHECK (("actor_id" IS NOT NULL)), CONSTRAINT "limitation_review_kind" CHECK (((kind)::text = ANY ((ARRAY['member'::character varying, 'proposed_member'::character varying, 'family'::character varying])::text[]))), CONSTRAINT "limitation_review_decision" CHECK (((decision)::text = ANY ((ARRAY['accepted'::character varying, 'ignored'::character varying, 'none'::character varying, 'handled'::character varying])::text[]))), CONSTRAINT "limitation_review_key" CHECK (((((kind)::text = 'family'::text) AND ((member_key)::text = ''::text)) OR ((NOT ((kind)::text = 'family'::text)) AND (NOT ((member_key)::text = ''::text))))));
ALTER TABLE "stewardship_limitation_review" ADD CONSTRAINT "stewardship_limitati_family_id_64958669_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_limitation_review" ADD CONSTRAINT "stewardship_limitati_submission_id_dfb3deef_fk_stewardsh" FOREIGN KEY ("submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_limitation_review_correlation_id_3f236bbe" ON "stewardship_limitation_review" ("correlation_id");
CREATE INDEX "stewardship_limitation_review_family_id_64958669" ON "stewardship_limitation_review" ("family_id");
CREATE INDEX "stewardship_limitation_review_submission_id_dfb3deef" ON "stewardship_limitation_review" ("submission_id");

-- Whether a response gives one answer: a Member's (or a proposed Member's)
-- cannot-participate answer, or the Family's cannot-attend answer, exactly
-- as the Limitations report selects them.
CREATE FUNCTION public.stewardship_limitation_answer_v1(answers jsonb, kind text, member_key text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce(CASE kind
        WHEN 'family' THEN answers->'cannot_attend'='true'::jsonb
        WHEN 'member' THEN answers#>ARRAY['service','members',member_key,'cannot_serve']='true'::jsonb
        WHEN 'proposed_member'
            THEN answers#>ARRAY['service','proposed_members',member_key,'cannot_serve']='true'::jsonb
        END,false)
$$;

-- Whether a response states one answer at all: the Family's cannot-attend
-- choice, or the Member's entry in the response's service answers. A Member
-- the form did not present (a section off, a Member out of scope) has none.
CREATE FUNCTION public.stewardship_limitation_stated_v1(answers jsonb, kind text, member_key text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce(CASE kind
        WHEN 'family' THEN answers ? 'cannot_attend'
        WHEN 'member' THEN answers#>ARRAY['service','members',member_key] IS NOT NULL
        WHEN 'proposed_member'
            THEN answers#>ARRAY['service','proposed_members',member_key] IS NOT NULL
        END,false)
$$;

-- Whether one review decision is still in force: no live response the
-- Family submitted after the one it was decided on answers the other way
-- (the review pipeline's resubmission rule with the setting off, #1685).
-- An answer given again unchanged keeps it (rule 1), one answered the other
-- way ends it (rule 3), and one a later form left out leaves it as it was
-- (rule 5); a response that gives the answer again after it ended is a new
-- To review item (rule 4).
CREATE FUNCTION public.stewardship_limitation_in_force_v1(review uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM stewardship_limitation_review r WHERE r.id=review)
       AND NOT EXISTS(SELECT 1 FROM stewardship_limitation_review r
        JOIN stewardship_submission decided ON decided.id=r.submission_id
        JOIN stewardship_submission later ON later.family_id=decided.family_id
            AND later.campaign_id=decided.campaign_id AND later.mode='live'
            AND later.family_version>decided.family_version
        WHERE r.id=review
          AND stewardship_limitation_stated_v1(later.answers,r.kind,r.member_key)
          AND NOT stewardship_limitation_answer_v1(later.answers,r.kind,r.member_key))
$$;

CREATE FUNCTION public.stewardship_limitation_review_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE family stewardship_family_campaign%ROWTYPE;
        response stewardship_submission%ROWTYPE;
        latest stewardship_limitation_review%ROWTYPE; state text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Limitation reviews are immutable' USING ERRCODE='23514';
    END IF;
    -- The work order first, as every writer takes it (the information
    -- revision guard does the same), then the Family's record, which
    -- orders this against its own submissions: they change its effective
    -- response.
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO family FROM stewardship_family_campaign WHERE id=NEW.family_id
        FOR NO KEY UPDATE;
    SELECT * INTO response FROM stewardship_submission WHERE id=NEW.submission_id;
    SELECT * INTO latest FROM stewardship_limitation_review
        WHERE family_id=NEW.family_id AND kind=NEW.kind AND member_key=NEW.member_key
        ORDER BY expected_version DESC LIMIT 1;
    -- The stage the decision in force gives; none in force is To review.
    state := CASE WHEN latest.id IS NULL OR latest.decision='none'
            OR NOT stewardship_limitation_in_force_v1(latest.id) THEN 'to_review'
        WHEN latest.decision='accepted' THEN 'to_handle'
        WHEN latest.decision='ignored' THEN 'ignored'
        ELSE 'handled' END;
    IF family.id IS NULL OR response.id IS NULL
       OR response.id IS DISTINCT FROM family.effective_submission_id
       OR response.mode<>'live' OR response.family_id<>family.id
       OR response.campaign_id<>family.campaign_id
       OR NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(family.campaign_id,true)
       OR NEW.expected_version IS DISTINCT FROM coalesce(latest.expected_version+1,0)
       OR NEW.expected_version>=9223372036854775807 OR length(NEW.note)>2000
       OR NOT stewardship_limitation_answer_v1(response.answers,NEW.kind,NEW.member_key)
       -- In parentheses, so PL/pgSQL does not read CASE's THEN as the IF's.
       OR NOT (CASE state
           WHEN 'to_review' THEN NEW.decision IN ('accepted','ignored')
           WHEN 'to_handle' THEN NEW.decision IN ('none','handled')
           WHEN 'ignored' THEN NEW.decision='none' AND btrim(NEW.note)<>''
           ELSE false END)
    THEN RAISE EXCEPTION 'Limitation review lacks current authority, version or state'
        USING ERRCODE='23514'; END IF;
    -- Attribution time is database-owned.
    NEW.created_at:=statement_timestamp();
    RETURN NEW;
END $$;

CREATE FUNCTION public.stewardship_limitation_review_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE campaign uuid;
BEGIN
    SELECT campaign_id INTO campaign FROM stewardship_family_campaign WHERE id=NEW.family_id;
    IF NOT EXISTS(SELECT 1 FROM stewardship_audit_event a
           JOIN stewardship_audit_context c ON c.event_id=a.id
           WHERE a.event_type='limitation_reviewed' AND a.subject_id=NEW.id
             AND a.actor_id=NEW.actor_id AND a.campaign_reference=campaign
             AND c.actor_kind='portal_user' AND c.schema='action'
             AND c.context=jsonb_build_object('outcome','changed',
                 'before_version',NEW.expected_version,'after_version',NEW.expected_version+1))
    THEN RAISE EXCEPTION 'Limitation review requires its audit'
        USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stewardship_limitation_review_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_limitation_review
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_limitation_review_guard_v1();
CREATE CONSTRAINT TRIGGER stewardship_limitation_review_effect
    AFTER INSERT ON public.stewardship_limitation_review DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_limitation_review_effect_v1();

-- stewardship_limitations_report_v2: v1 (0094's body), with each Member
-- and Family row's answer key, Family campaign record and response, and its
-- latest review decision: the version (decisions recorded so far), the
-- decision, who made it, when, its note, and whether it is still in force.
CREATE FUNCTION public.stewardship_limitations_report_v2(campaign_uuid uuid, parameters jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT off, as in every report function (#277).
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE answer jsonb; bad boolean;
BEGIN
    -- Statements run in order, so each check relies only on earlier ones.
    bad:=jsonb_typeof(parameters) IS DISTINCT FROM 'object';
    IF NOT bad THEN
        bad:=coalesce(NOT parameters ?& ARRAY['search','show']
            OR parameters-ARRAY['search','show']<>'{}'::jsonb
            OR jsonb_typeof(parameters->'search') IS DISTINCT FROM 'string'
            OR jsonb_typeof(parameters->'show') IS DISTINCT FROM 'string',true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(length(parameters->>'search')>200
            OR parameters->>'show' NOT IN ('any','cannot_serve','cannot_attend'),true);
    END IF;
    IF bad THEN
        RAISE EXCEPTION 'Invalid limitations report parameters' USING ERRCODE='23514';
    END IF;
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.values,sc.snapshot_id AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), responses AS MATERIALIZED (
    SELECT s.id,s.submitted_at,s.answers,i.family_duid,i.id AS family_campaign_id,
        coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
            nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            -- No name (NULL) for a Family the data no longer has: the page
            -- shows its ParishSoft status badge alone (#1216). A present
            -- Family without a usable name reads so.
            CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS family_name
    FROM selected x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), members AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.submitted_at,g.grp,e.key AS member_key,
        r.id AS submission_id,r.family_campaign_id,
        CASE g.grp WHEN 'members' THEN 'member' ELSE 'proposed_member' END AS kind,
        -- The Family's own corrected name first, then the parish record.
        coalesce(nullif(btrim(concat_ws(' ',
                nullif(btrim(r.answers->g.grp->e.key->>'first_name'),''),
                nullif(btrim(r.answers->g.grp->e.key->>'last_name'),''))),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(sm.canonical::jsonb->>'firstName'),''),
                nullif(btrim(sm.canonical::jsonb->>'lastName'),''))),''),
            'Unavailable name') AS member_name
    FROM responses r
    CROSS JOIN LATERAL (VALUES ('members'),('proposed_members')) g(grp)
    CROSS JOIN LATERAL jsonb_each(coalesce(r.answers->'service'->g.grp,'{}'::jsonb)) e
    CROSS JOIN selected x
    LEFT JOIN stewardship_snapshot_member mm
        ON g.grp='members' AND mm.snapshot_id=x.source_id AND mm.source_key=e.key
    LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    WHERE e.value->'cannot_serve'='true'::jsonb
      AND parameters->>'show' IN ('any','cannot_serve')
), matching_members AS MATERIALIZED (
    SELECT * FROM members q
    WHERE parameters->>'search'=''
       OR position(lower(parameters->>'search') IN lower(q.member_name))>0
       OR position(lower(parameters->>'search') IN lower(q.family_name))>0
       OR position(parameters->>'search' IN q.family_duid::text)>0
), families AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.submitted_at,r.id AS submission_id,
        r.family_campaign_id,'family'::text AS kind,''::text AS member_key
    FROM responses r
    WHERE r.answers->'cannot_attend'='true'::jsonb
      AND parameters->>'show' IN ('any','cannot_attend')
      AND (parameters->>'search'=''
            OR position(lower(parameters->>'search') IN lower(r.family_name))>0
            OR position(parameters->>'search' IN r.family_duid::text)>0)
), reviewed AS MATERIALIZED (
    -- Each listed answer's latest decision (the review pipeline, #1685).
    SELECT a.family_campaign_id,a.kind,a.member_key,
        v.expected_version+1 AS version,v.decision,v.created_at AS decided_at,v.note,
        coalesce(u.email,'Former portal user') AS decided_by,
        stewardship_limitation_in_force_v1(v.id) AS in_force
    FROM (SELECT family_campaign_id,kind,member_key FROM matching_members
          UNION ALL SELECT family_campaign_id,kind,member_key FROM families) a
    JOIN LATERAL (SELECT * FROM stewardship_limitation_review lr
        WHERE lr.family_id=a.family_campaign_id AND lr.kind=a.kind
          AND lr.member_key=a.member_key
        ORDER BY lr.expected_version DESC LIMIT 1) v ON true
    LEFT JOIN stewardship_portal_user u ON u.id=v.actor_id
)
SELECT CASE WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'source_id',x.source_id),
    'summary',jsonb_build_object(
        'cannot_serve',(SELECT count(*) FROM matching_members),
        'cannot_attend',(SELECT count(*) FROM families)),
    'members',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',q.family_name,'family_duid',q.family_duid,
            'member_name',q.member_name,'proposed',q.grp='proposed_members',
            'member_duid',CASE WHEN q.grp='members' AND q.member_key ~ '^[0-9]{1,18}$'
                THEN q.member_key::bigint END,
            'cannot_serve',true,'submitted_at',q.submitted_at,
            'kind',q.kind,'member_key',q.member_key,
            'family_campaign_id',q.family_campaign_id,'submission_id',q.submission_id,
            'version',coalesce(v.version,0),'decision',v.decision,
            'in_force',coalesce(v.in_force,false),'decided_by',v.decided_by,
            'decided_at',v.decided_at,'note',v.note)
        ORDER BY lower(q.family_name),q.family_duid,lower(q.member_name),q.member_key)
        FROM matching_members q LEFT JOIN reviewed v
            ON v.family_campaign_id=q.family_campaign_id AND v.kind=q.kind
           AND v.member_key=q.member_key),'[]'::jsonb),
    'families',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',f.family_name,'family_duid',f.family_duid,
            'submitted_at',f.submitted_at,'kind',f.kind,'member_key',f.member_key,
            'family_campaign_id',f.family_campaign_id,'submission_id',f.submission_id,
            'version',coalesce(v.version,0),'decision',v.decision,
            'in_force',coalesce(v.in_force,false),'decided_by',v.decided_by,
            'decided_at',v.decided_at,'note',v.note)
        ORDER BY lower(f.family_name),f.family_duid)
        FROM families f LEFT JOIN reviewed v
            ON v.family_campaign_id=f.family_campaign_id AND v.kind=f.kind
           AND v.member_key=f.member_key),'[]'::jsonb)) END
INTO answer FROM (SELECT 1) one LEFT JOIN selected x ON true;
    RETURN answer;
END $$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid='public.stewardship_information_revision'::regclass
              AND attname='review_decision' AND NOT attnotnull AND NOT attisdropped)
       OR NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='public.stewardship_information_revision'::regclass
              AND conname='information_revision_decision' AND contype='c')
    THEN
        RAISE EXCEPTION 'stewardship_information_revision lacks its review decision';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_information_revision_guard_v1()'::regprocedure
              AND prosrc LIKE '%OR NEW.review_decision IS NULL%'
              AND prosrc LIKE '%Reopening an ignored request needs a note%'
              AND prosrc LIKE '%Only a request to review can be ignored%'
              AND prosrc LIKE '%Follow-up attribution history is unavailable%'
              AND NOT prosecdef
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])
       OR (SELECT count(*) FROM pg_trigger t
            WHERE t.tgrelid='public.stewardship_information_revision'::regclass
              AND t.tgfoid='public.stewardship_information_revision_guard_v1()'::regprocedure
              AND t.tgenabled='O' AND NOT t.tgisinternal)<>1
    THEN
        RAISE EXCEPTION 'stewardship_information_revision_guard_v1 lacks the review decision';
    END IF;
    IF (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid='public.stewardship_limitation_review'::regclass
              AND NOT t.tgisinternal AND NOT p.prosecdef AND t.tgenabled='O'
              AND p.proname IN ('stewardship_limitation_review_guard_v1',
                                'stewardship_limitation_review_effect_v1'))<>2
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_limitation_review_effect'
              AND tgrelid='public.stewardship_limitation_review'::regclass
              AND tgdeferrable AND tginitdeferred)
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_limitation_review_guard_v1()'::regprocedure
              AND prosrc LIKE '%stewardship_export_authorized_v1(NEW.actor_id)%'
              AND prosrc LIKE '%pg_advisory_xact_lock(736220,1)%'
              AND prosrc LIKE '%WHEN ''ignored'' THEN NEW.decision=''none'' AND btrim(NEW.note)<>''''%')
       -- The guard fires before each row's INSERT, UPDATE and DELETE (row,
       -- before, insert, delete and update: tgtype 1+2+4+8+16).
       OR NOT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgname='stewardship_limitation_review_guard'
              AND tgrelid='public.stewardship_limitation_review'::regclass
              AND tgtype=31)
    THEN
        RAISE EXCEPTION 'stewardship_limitation_review is not guarded as declared';
    END IF;
    IF (SELECT count(*) FROM pg_constraint
            WHERE conrelid='public.stewardship_limitation_review'::regclass
              AND conname IN ('limitation_review_version','limitation_review_replay',
                              'limitation_review_identity','limitation_review_kind',
                              'limitation_review_decision','limitation_review_key'))<>6
    THEN
        RAISE EXCEPTION 'stewardship_limitation_review lacks its checks';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND NOT p.prosecdef
              AND p.oid IN (
                  'public.stewardship_limitation_answer_v1(jsonb,text,text)'::regprocedure,
                  'public.stewardship_limitation_stated_v1(jsonb,text,text)'::regprocedure,
                  'public.stewardship_limitation_in_force_v1(uuid)'::regprocedure,
                  'public.stewardship_limitations_report_v2(uuid,jsonb)'::regprocedure))<>4
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_limitation_answer_v1(jsonb,text,text)'::regprocedure
              AND provolatile='i'
              AND prosrc LIKE '%''cannot_serve''%' AND prosrc LIKE '%''cannot_attend''%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_limitation_stated_v1(jsonb,text,text)'::regprocedure
              AND provolatile='i' AND prosrc LIKE '%answers ? ''cannot_attend''%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_limitation_in_force_v1(uuid)'::regprocedure
              AND provolatile='s'
              AND prosrc LIKE '%later.family_version>decided.family_version%'
              AND prosrc LIKE '%stewardship_limitation_stated_v1(later.answers%'
              AND prosrc LIKE '%NOT stewardship_limitation_answer_v1(later.answers%')
       OR NOT EXISTS(SELECT 1 FROM pg_proc
            WHERE oid='public.stewardship_limitations_report_v2(uuid,jsonb)'::regprocedure
              AND provolatile='s' AND prorettype='jsonb'::regtype
              AND prosrc LIKE '%stewardship_limitation_in_force_v1(v.id) AS in_force%'
              AND prosrc LIKE '%''Name not recorded in ParishSoft''%'
              AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp','jit=off'])
    THEN
        RAISE EXCEPTION 'stewardship_limitations_report_v2 is not installed as declared';
    END IF;
END $check$;
