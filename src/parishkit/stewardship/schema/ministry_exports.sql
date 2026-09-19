-- Current authority is independent of captured data-as-of and caller input.
CREATE FUNCTION stewardship_ministry_scope_v1(user_uuid uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH policy AS (
        SELECT u.email,r.active_configuration_id,
            coalesce(a.roles,d.roles,'[]'::jsonb) AS roles
        FROM stewardship_portal_user u CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        LEFT JOIN stewardship_domain_rule d ON d.configuration_id=r.active_configuration_id
            AND d.domain=lower(u.hosted_domain) AND d.domain=split_part(lower(u.email),'@',2)
        WHERE u.id=user_uuid AND NOT u.disabled
    )
    SELECT jsonb_build_object('capability','ministry_report',
        'operational',p.roles ?| ARRAY['administrator','staff'],
        'ministries',coalesce((SELECT jsonb_agg(DISTINCT m.ministry_duid ORDER BY m.ministry_duid)
            FROM stewardship_ministry_assignment m
            WHERE m.configuration_id=p.active_configuration_id AND m.email=lower(p.email)
                AND m.ministry_duid BETWEEN 1 AND 2147483647
                AND (m.source='manual' OR EXISTS(SELECT 1 FROM stewardship_assignment_overlay o
                    WHERE o.assignment_record_id=m.record_id AND o.active))), '[]'::jsonb))
    FROM policy p WHERE p.roles ?| ARRAY['administrator','staff','ministry_leader']
$$;

CREATE FUNCTION stewardship_ministry_scope_authorized_v1(user_uuid uuid, recorded jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT coalesce((SELECT current_scope->'operational'='true'::jsonb OR (
        recorded->'operational'='false'::jsonb
        AND jsonb_array_length(recorded->'ministries')>0
        AND (current_scope->'ministries') @> (recorded->'ministries'))
        FROM (SELECT stewardship_ministry_scope_v1(user_uuid) AS current_scope) p),false)
$$;

CREATE TABLE stewardship_ministry_export_snapshot (
    id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    actor_id uuid NOT NULL, correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL REFERENCES stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED,
    source_id uuid NOT NULL REFERENCES stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED,
    configuration_id uuid NOT NULL REFERENCES stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED,
    parameters jsonb NOT NULL, authorization_scope jsonb NOT NULL, document jsonb NOT NULL,
    row_count integer NOT NULL CHECK(row_count>=0)
);
CREATE INDEX ministry_export_correlation ON stewardship_ministry_export_snapshot(correlation_id);
CREATE INDEX ministry_export_campaign ON stewardship_ministry_export_snapshot(campaign_id);
CREATE INDEX ministry_export_source ON stewardship_ministry_export_snapshot(source_id);
CREATE INDEX ministry_export_config ON stewardship_ministry_export_snapshot(configuration_id);

CREATE FUNCTION stewardship_ministry_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE current_scope jsonb; filters jsonb; ministry_id integer; request_action text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ministry export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM pg_advisory_xact_lock(736220,1);
    current_scope:=stewardship_ministry_scope_v1(NEW.actor_id);
    IF current_scope IS NULL
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Ministry export capture is unavailable' USING ERRCODE='23514'; END IF;
    -- Closed parameters have identical meanings in HTML and immutable capture.
    filters:=NEW.parameters->'filters';
    request_action:=NEW.parameters->>'action';
    IF jsonb_typeof(NEW.parameters) IS DISTINCT FROM 'object'
       OR NOT NEW.parameters ?& ARRAY['filters','ministry','action']
       OR NEW.parameters-ARRAY['filters','ministry','action']<>'{}'::jsonb
       OR jsonb_typeof(filters) IS DISTINCT FROM 'object'
       OR NOT filters ?& ARRAY['search','activity','history','state','start','end','sort']
       OR filters-ARRAY['search','activity','history','state','start','end','sort']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(filters) f WHERE jsonb_typeof(f.value)<>'string')
       OR octet_length(filters->>'search')>131072
       OR filters->>'activity' NOT IN ('any','active','inactive','unavailable')
       OR filters->>'history' NOT IN ('current','all')
       OR filters->>'state' NOT IN ('any','unresolved','new','assigned','in_progress',
           'resolved','closed_no_response','cancelled','superseded')
       OR filters->>'sort' NOT IN ('name','name_desc','newest','oldest')
       OR request_action IS NULL OR request_action NOT IN ('summary','join','leave')
    THEN RAISE EXCEPTION 'Invalid Ministry export parameters' USING ERRCODE='23514'; END IF;
    IF NEW.parameters->'ministry'<>'null'::jsonb THEN
        IF jsonb_typeof(NEW.parameters->'ministry')<>'number'
           OR NOT (NEW.parameters->>'ministry') ~ '^[1-9][0-9]{0,9}$'
           OR (NEW.parameters->>'ministry')::bigint>2147483647
        THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
        ministry_id:=(NEW.parameters->>'ministry')::integer;
    END IF;
    IF (ministry_id IS NULL) IS DISTINCT FROM (request_action='summary')
       OR (request_action='summary' AND (filters->>'history'<>'current'
           OR filters->>'state'<>'any' OR filters->>'start'<>'' OR filters->>'end'<>''
           OR filters->>'sort' NOT IN ('name','name_desc')))
       OR EXISTS(SELECT 1 FROM jsonb_each_text(filters) f
           WHERE f.key IN ('start','end') AND f.value<>'' AND (
               NOT f.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
               OR NOT pg_input_is_valid(f.value,'date')))
       OR (filters->>'start'<>'' AND filters->>'end'<>''
           AND filters->>'start'>filters->>'end')
    THEN RAISE EXCEPTION 'Invalid Ministry selection' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_ministry_report_v1(NEW.campaign_id,filters,
        (current_scope->>'operational')::boolean,
        ARRAY(SELECT value::bigint FROM jsonb_array_elements_text(current_scope->'ministries')),
        ministry_id,request_action);
    IF NEW.document IS NULL OR NEW.document ?| ARRAY['disabled','unavailable']
       OR (NEW.document->'authorized'<>'true'::jsonb AND
           (ministry_id IS NOT NULL OR current_scope->'operational'<>'true'::jsonb))
    THEN RAISE EXCEPTION 'Ministry export inputs are unavailable' USING ERRCODE='23514'; END IF;
    NEW.authorization_scope:=NEW.document->'authorization_scope';
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    RETURN NEW;
END $$;
CREATE TRIGGER ministry_export_capture BEFORE INSERT OR UPDATE OR DELETE
    ON stewardship_ministry_export_snapshot FOR EACH ROW
    EXECUTE FUNCTION stewardship_ministry_export_capture_v1();

CREATE FUNCTION stewardship_ministry_export_binding_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM stewardship_export_request r
        WHERE r.ministry_snapshot_id=NEW.id AND r.requester_id=NEW.actor_id
          AND r.campaign_id=NEW.campaign_id AND r.configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Ministry export capture requires its request' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ministry_export_binding AFTER INSERT
    ON stewardship_ministry_export_snapshot DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION stewardship_ministry_export_binding_v1();
ALTER TABLE stewardship_export_request ADD CONSTRAINT export_ministry_snapshot_fk
    FOREIGN KEY(ministry_snapshot_id) REFERENCES stewardship_ministry_export_snapshot(id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX export_ministry_snapshot ON stewardship_export_request(ministry_snapshot_id);

-- Only the Ministry kind uses assignment authority; all other reports keep
-- their original global capability. Ownership remains independent of scope.
CREATE FUNCTION stewardship_export_request_authorized_v1(user_uuid uuid, request stewardship_export_request)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT (user_uuid=request.requester_id OR stewardship_export_authorized_v1(user_uuid,true))
        AND CASE WHEN request.report='ministry' THEN
            stewardship_ministry_scope_authorized_v1(user_uuid,request.authorization_scope)
        ELSE stewardship_export_authorized_v1(user_uuid) END
$$;
