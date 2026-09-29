-- One closed query owns the HTML queue and complete immutable export inputs.
CREATE FUNCTION stewardship_information_report_v1(
    campaign uuid, parameters jsonb, page_number integer DEFAULT NULL,
    item_uuid uuid DEFAULT NULL, page_size integer DEFAULT 50
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters ?& ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','disposition','needed','completed','start','end','sort']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'disposition' NOT IN ('current_actionable','superseded','withdrawn','all')
       OR f->>'needed' NOT IN ('any','yes','no')
       OR f->>'completed' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('newest','oldest','name','name_desc')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'Invalid information report parameters' USING ERRCODE='23514'; END IF;
    -- Python validates the same canonical dates before reaching SQL. The SQL
    -- owner also rejects alternate spellings on direct restricted-role writes.
    IF (f->>'start'<>'' AND (f->>'start')::date::text<>f->>'start')
       OR (f->>'end'<>'' AND (f->>'end')::date::text<>f->>'end')
       OR (f->>'start'<>'' AND f->>'end'<>'' AND f->>'start'>f->>'end')
    THEN RAISE EXCEPTION 'Invalid information report interval' USING ERRCODE='23514'; END IF;

    WITH selected AS MATERIALIZED (
        SELECT c.id,cc.name,cc.timezone,cc.id AS timezone_configuration_id,
            sc.snapshot_id AS source_id,ss.generation AS source_generation,
            ss.promoted_at AS source_as_of,statement_timestamp() AS observed_at
        FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        JOIN stewardship_source_current sc ON sc.singleton
        JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
            AND ss.state='promoted' AND ss.compacted_at IS NULL
        WHERE c.id=campaign
    ), history AS MATERIALIZED (
        SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value FROM selected
    ), rows AS MATERIALIZED (
        SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
            i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
            coalesce(u.email,'Former portal user') AS completed_by,
            coalesce(r.notes,'') AS notes,s.submitted_at,fam.family_duid,
            coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
                nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
                nullif(btrim(concat_ws(' ',
                    nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                    nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                'Family') AS family_name,
            h.value->'reported' ? i.id::text AS previously_reported,
            h.value->'corrected' @>
                jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
                AS correction_resolved
        FROM selected x CROSS JOIN history h
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_additional_information i ON i.submission_id=s.id
        JOIN stewardship_family_campaign fam ON fam.id=s.family_id
        LEFT JOIN stewardship_snapshot_family m
            ON m.snapshot_id=x.source_id AND m.source_key=fam.family_duid::text
        LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
        LEFT JOIN LATERAL (
            SELECT notes,followed_up_by_id FROM stewardship_information_revision
            WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
        LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
        WHERE (item_uuid IS NULL OR i.id=item_uuid)
          AND (f->>'start'='' OR (s.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date>=(f->>'start')::date)
          AND (f->>'end'='' OR (s.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date<=(f->>'end')::date)
    ), filtered AS MATERIALIZED (
        SELECT * FROM rows WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(family_name))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(family_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(family_name) END DESC,id) AS ordinal
        FROM filtered
    ), page AS (
        SELECT * FROM ordered ORDER BY ordinal
        LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
        OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
    ), detached AS (
        SELECT ordinal,to_jsonb(page)-'ordinal' || jsonb_build_object('history',
            CASE WHEN (parameters->>'history')::boolean THEN coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version',r.expected_version+1,'created_at',r.created_at,
                    'actor_id',r.actor_id,'actor_label',coalesce(a.email,'Former portal user'),
                    'follow_up_needed',r.follow_up_needed,'followed_up',r.followed_up,
                    'followed_up_at',r.followed_up_at,'followed_up_by_id',r.followed_up_by_id,
                    'completed_by',coalesce(u.email,'Former portal user'),'notes',r.notes)
                    ORDER BY r.expected_version)
                FROM stewardship_information_revision r
                LEFT JOIN stewardship_portal_user a ON a.id=r.actor_id
                LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
                WHERE r.item_id=page.id AND r.expected_version<page.version
            ),'[]'::jsonb) ELSE '[]'::jsonb END) AS value FROM page
    )
    SELECT jsonb_build_object('metadata',to_jsonb(x),
        'total',(SELECT count(*) FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

-- A self-contained capture preserves exactly the query's source names, text
-- and Staff history without pinning the complete source corpus indefinitely.
CREATE TABLE stewardship_information_export_snapshot (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid NOT NULL, correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    source_id uuid NOT NULL REFERENCES stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED,
    configuration_id uuid NOT NULL REFERENCES stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED,
    parameters jsonb NOT NULL, document jsonb NOT NULL,
    row_count integer NOT NULL CHECK(row_count>=0)
);
CREATE INDEX information_export_correlation ON stewardship_information_export_snapshot(correlation_id);
CREATE INDEX information_export_campaign ON stewardship_information_export_snapshot(campaign_id);
CREATE INDEX information_export_source ON stewardship_information_export_snapshot(source_id);
CREATE INDEX information_export_config ON stewardship_information_export_snapshot(configuration_id);

CREATE FUNCTION stewardship_information_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Information export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Information export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_information_report_v1(NEW.campaign_id,NEW.parameters);
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Information export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

CREATE TRIGGER information_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_information_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_information_export_capture_v1();

CREATE FUNCTION stewardship_information_export_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_export_request r
        WHERE r.information_snapshot_id=NEW.id AND r.requester_id=NEW.actor_id
          AND r.campaign_id=NEW.campaign_id AND r.configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Information export capture requires its request' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER information_export_binding AFTER INSERT
    ON stewardship_information_export_snapshot DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_information_export_binding_v1();

ALTER TABLE stewardship_export_request ADD CONSTRAINT export_information_snapshot_fk
    FOREIGN KEY(information_snapshot_id) REFERENCES stewardship_information_export_snapshot(id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX export_information_snapshot ON stewardship_export_request(information_snapshot_id);

-- Talents and limitations (#247): the new Family and Member answers, from each
-- Family's currently effective live response. Members appear only when they
-- chose a talent or cannot participate in ministries; Families only when they
-- cannot attend Mass or prayer services. Filters are a closed vocabulary:
-- `search` (name or Family DUID text) and `talent` ('any', 'cannot_serve',
-- 'cannot_attend' or one talent option identity). Talent wording is resolved
-- by the application from the campaign configuration.
CREATE FUNCTION stewardship_talent_report_v1(campaign_uuid uuid, parameters jsonb)
RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT off, as in the information report above.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE answer jsonb; bad boolean;
    uuid_text constant text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    -- Statements run in order, so each check relies only on earlier ones.
    bad:=jsonb_typeof(parameters) IS DISTINCT FROM 'object';
    IF NOT bad THEN
        bad:=coalesce(NOT parameters ?& ARRAY['search','talent']
            OR parameters-ARRAY['search','talent']<>'{}'::jsonb
            OR jsonb_typeof(parameters->'search') IS DISTINCT FROM 'string'
            OR jsonb_typeof(parameters->'talent') IS DISTINCT FROM 'string',true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(length(parameters->>'search')>200
            OR (parameters->>'talent' NOT IN ('any','cannot_serve','cannot_attend')
                AND NOT parameters->>'talent' ~ uuid_text),true);
    END IF;
    IF bad THEN
        RAISE EXCEPTION 'Invalid talent report parameters' USING ERRCODE='23514';
    END IF;
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.values,sc.snapshot_id AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), responses AS MATERIALIZED (
    SELECT s.id,s.submitted_at,s.answers,i.family_duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
            nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            'Unavailable Family') AS family_name
    FROM selected x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
), people AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.submitted_at,g.grp,e.key AS member_key,
        coalesce(e.value->'cannot_serve'='true'::jsonb,false) AS cannot_serve,
        coalesce(e.value->'talents','{}'::jsonb) AS talents,
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
    WHERE e.value->'cannot_serve'='true'::jsonb OR e.value->'talents'<>'{}'::jsonb
), members AS MATERIALIZED (
    SELECT * FROM people q
    WHERE (parameters->>'search'=''
            OR position(lower(parameters->>'search') IN lower(q.member_name))>0
            OR position(lower(parameters->>'search') IN lower(q.family_name))>0
            OR position(parameters->>'search' IN q.family_duid::text)>0)
      AND (parameters->>'talent'='any'
            OR (parameters->>'talent'='cannot_serve' AND q.cannot_serve)
            OR q.talents ? (parameters->>'talent'))
), families AS MATERIALIZED (
    SELECT r.family_duid,r.family_name,r.submitted_at FROM responses r
    WHERE r.answers->'cannot_attend'='true'::jsonb
      AND parameters->>'talent' IN ('any','cannot_attend')
      AND (parameters->>'search'=''
            OR position(lower(parameters->>'search') IN lower(r.family_name))>0
            OR position(parameters->>'search' IN r.family_duid::text)>0)
)
SELECT CASE WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'source_id',x.source_id),
    'summary',jsonb_build_object(
        'members',(SELECT count(*) FROM members),
        'cannot_serve',(SELECT count(*) FROM members WHERE cannot_serve),
        'cannot_attend',(SELECT count(*) FROM families),
        'talents',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT t.key AS k,count(*) AS n FROM members q
            CROSS JOIN LATERAL jsonb_object_keys(q.talents) t(key) GROUP BY 1) d),
            '{}'::jsonb)),
    'members',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',family_name,'family_duid',family_duid,
            'member_name',member_name,'proposed',grp='proposed_members',
            'cannot_serve',cannot_serve,'talents',talents,'submitted_at',submitted_at)
        ORDER BY lower(family_name),family_duid,lower(member_name),member_key)
        FROM members),'[]'::jsonb),
    'families',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'family_name',family_name,'family_duid',family_duid,
            'submitted_at',submitted_at)
        ORDER BY lower(family_name),family_duid) FROM families),'[]'::jsonb)) END
INTO answer FROM (SELECT 1) one LEFT JOIN selected x ON true;
    RETURN answer;
END $$;
