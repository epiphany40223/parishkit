-- One source-coherent selection owns interactive pages and complete captures.
CREATE FUNCTION stewardship_directory_report_v1(
    campaign_uuid uuid, parameters jsonb, page_number integer DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters ?& ARRAY['filters','postal','exact','family_id']
       OR parameters-ARRAY['filters','postal','exact','family_id']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'postal') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'exact') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'family_id') NOT IN ('null','string')
       OR (parameters->>'family_id' IS NOT NULL AND
           parameters->>'family_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (parameters->'exact'='false'::jsonb AND parameters->>'family_id' IS NOT NULL)
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','reason','phone','response','sort']
       OR f-ARRAY['search','reason','phone','response','sort']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'reason' NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable')
       OR f->>'phone' NOT IN ('any','yes','no')
       OR f->>'response' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('name','name_desc','duid')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
    THEN RAISE EXCEPTION 'Invalid directory report parameters' USING ERRCODE='23514'; END IF;
    -- BEGIN DIRECTORY SELECTION
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.id,x.name,s.id AS source_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,s.organization_id
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
), options AS (
    SELECT f AS f
), family_source AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_family m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_family p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'portal_eligible'='true'::jsonb
), members AS NOT MATERIALIZED (
    SELECT p.source_key,p.family_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_member m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'active'='true'::jsonb
), contacts AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_contact m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_contact p ON p.id=m.payload_id
), addresses AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb->'fields' AS fields
    FROM source s JOIN stewardship_snapshot_address m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_address p ON p.id=m.payload_id
), base AS MATERIALIZED (
    SELECT f.source_key,f.source_key::bigint AS family_duid,i.id AS family_id,
        coalesce(nullif(btrim(f.value->>'mailingName'),''),
            nullif(btrim(concat_ws(' ',f.value->>'firstName',f.value->>'lastName')),''),
            'Family') AS family_name,
        concat_ws(' ',f.value->>'firstName',f.value->>'lastName') AS search_name,
        jsonb_array_length(f.value->'active_head_duids') AS head_count,
        (SELECT count(*)
            FROM jsonb_array_elements_text(f.value->'active_head_duids') h(head)
            JOIN contacts c ON c.source_key='member:'||h.head
            CROSS JOIN LATERAL jsonb_array_elements(c.value->'emails') e)
            AS address_count,
        recipients.eligible>0 AS email_eligible,
        recipients.deliverable>0 AS email_deliverable,
        EXISTS(SELECT 1 FROM stewardship_submission r WHERE r.family_id=i.id
            AND r.campaign_id=campaign_uuid AND r.mode='live') AS responded
    FROM family_source f CROSS JOIN source s
    LEFT JOIN stewardship_family_campaign i
        ON i.campaign_id=s.id AND i.family_duid=f.source_key::bigint
    CROSS JOIN LATERAL (
        SELECT count(*) AS eligible,count(*) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM stewardship_recipient_refusal r
            WHERE r.organization_id=s.organization_id
              AND r.family_duid=f.source_key::bigint
              AND r.address=e->>'value' AND NOT EXISTS (
                  SELECT 1 FROM stewardship_recipient_resolution z
                  WHERE z.refusal_id=r.id)
        )) AS deliverable
        FROM jsonb_array_elements_text(f.value->'active_head_duids') h(head)
        JOIN contacts c ON c.source_key='member:'||h.head
        CROSS JOIN LATERAL jsonb_array_elements(c.value->'emails') e
        WHERE e->'valid'='true'::jsonb
    ) recipients
), rows AS MATERIALIZED (
    SELECT *,CASE WHEN email_deliverable THEN 'deliverable'
        WHEN head_count=0 THEN 'no_head' WHEN address_count=0 THEN 'no_address'
        WHEN NOT email_eligible THEN 'invalid_address'
        ELSE 'provider_refused' END AS reason
    FROM base
), filtered AS MATERIALIZED (
    SELECT r.* FROM rows r CROSS JOIN options o
    WHERE (NOT (parameters->>'postal')::boolean OR NOT email_deliverable)
      AND (NOT (parameters->>'exact')::boolean OR family_id=(parameters->>'family_id')::uuid)
      AND (o.f->>'reason'='any' OR reason=o.f->>'reason')
      AND (o.f->>'phone'='any' OR (
          EXISTS(SELECT 1 FROM contacts c WHERE c.source_key='family:'||r.source_key
              AND c.value->'phones'<>'{}'::jsonb)
          OR EXISTS(SELECT 1 FROM members m JOIN contacts c
              ON c.source_key='member:'||m.source_key WHERE m.family_key=r.source_key
              AND c.value->'phones'<>'{}'::jsonb)
          )=(o.f->>'phone'='yes'))
      AND (o.f->>'response'='any' OR responded=(o.f->>'response'='yes'))
      AND (o.f->>'search'='' OR position(lower(o.f->>'search') IN lower(family_name))>0
          OR position(lower(o.f->>'search') IN lower(search_name))>0
          OR position(o.f->>'search' IN family_duid::text)>0
          OR EXISTS(SELECT 1 FROM addresses a
              CROSS JOIN LATERAL jsonb_each_text(a.fields) v
              WHERE a.source_key='family:'||r.source_key||':primary'
              AND position(lower(o.f->>'search') IN lower(v.value))>0))
), ordered AS (
    SELECT r.*,row_number() OVER (ORDER BY
        CASE WHEN o.f->>'sort'='name' THEN lower(family_name) END,
        CASE WHEN o.f->>'sort'='name_desc' THEN lower(family_name) END DESC,
        family_duid) AS ordinal
    FROM filtered r CROSS JOIN options o
), page AS MATERIALIZED (
    SELECT * FROM ordered ORDER BY ordinal LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE 50 END OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*50 END
), details AS (
    -- Only the selected page constructs display-only private contact JSON.
    SELECT p.*,f.value->>'envelopeNumber' AS envelope,
        coalesce((SELECT jsonb_agg(jsonb_build_object('duid',h.head,
            'name',btrim(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')))
            ORDER BY h.head::bigint)
            FROM jsonb_array_elements_text(f.value->'active_head_duids') h(head)
            JOIN members m ON m.source_key=h.head),'[]'::jsonb) AS heads,
        coalesce((SELECT jsonb_agg(jsonb_build_object('owner',v.owner,'kind',phone.key,
            'value',phone.value) ORDER BY v.owner,phone.key,phone.value)
            FROM (
                SELECT 'Family' AS owner,c.value FROM contacts c
                WHERE c.source_key='family:'||p.source_key
                UNION ALL
                SELECT btrim(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')),
                    c.value FROM members m
                    JOIN contacts c ON c.source_key='member:'||m.source_key
                WHERE m.family_key=p.source_key
            ) v CROSS JOIN LATERAL jsonb_each_text(v.value->'phones') phone),
            '[]'::jsonb) AS phones,
        coalesce((SELECT a.fields FROM addresses a
            WHERE a.source_key='family:'||p.source_key||':primary'),
            '{}'::jsonb) AS address
    FROM page p JOIN family_source f ON f.source_key=p.source_key
)
SELECT jsonb_build_object('metadata',to_jsonb(s),
    'total',(SELECT count(*) FROM filtered),
    'active_total',(SELECT count(*) FROM rows),
    'postal_total',(SELECT count(*) FROM rows WHERE NOT email_deliverable),
    'rows',coalesce((SELECT jsonb_agg(
        to_jsonb(p)-ARRAY['ordinal','head_count','address_count','search_name','source_key']
        ORDER BY ordinal) FROM details p),'[]'::jsonb)) INTO answer FROM source s;
    -- END DIRECTORY SELECTION
    RETURN answer;
END $$;

CREATE TABLE stewardship_directory_export_snapshot (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid NOT NULL, correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    source_id uuid NOT NULL REFERENCES stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED,
    configuration_id uuid NOT NULL REFERENCES stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED,
    parameters jsonb NOT NULL, document jsonb NOT NULL,
    row_count integer NOT NULL CHECK(row_count>=0)
);
CREATE INDEX directory_export_correlation ON stewardship_directory_export_snapshot(correlation_id);
CREATE INDEX directory_export_campaign ON stewardship_directory_export_snapshot(campaign_id);
CREATE INDEX directory_export_source ON stewardship_directory_export_snapshot(source_id);
CREATE INDEX directory_export_config ON stewardship_directory_export_snapshot(configuration_id);

CREATE FUNCTION stewardship_directory_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Directory export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Directory export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_directory_report_v1(NEW.campaign_id,NEW.parameters);
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Directory export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;

CREATE TRIGGER directory_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_directory_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_directory_export_capture_v1();

CREATE FUNCTION stewardship_directory_export_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_export_request r
        WHERE r.directory_snapshot_id=NEW.id AND r.requester_id=NEW.actor_id
          AND r.campaign_id=NEW.campaign_id AND r.configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Directory export capture requires its request' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER directory_export_binding AFTER INSERT
    ON stewardship_directory_export_snapshot DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_directory_export_binding_v1();

ALTER TABLE stewardship_export_request ADD CONSTRAINT export_directory_snapshot_fk
    FOREIGN KEY(directory_snapshot_id) REFERENCES stewardship_directory_export_snapshot(id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX export_directory_snapshot ON stewardship_export_request(directory_snapshot_id);
