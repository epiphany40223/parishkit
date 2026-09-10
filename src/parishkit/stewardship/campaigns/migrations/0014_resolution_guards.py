"""Restore uncertainty is not fulfillment; explicit skips cover exact versions."""

# ruff: noqa: E501
from django.db import migrations

SQL = """
CREATE FUNCTION stewardship_restore_hold_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Restore hold history cannot be deleted' USING ERRCODE='23514'; END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'unreviewed' OR NEW.version<>1 OR NEW.resolved_at IS NOT NULL OR NEW.recovery_occurrence_id IS NOT NULL
           OR NEW.evidence<>'' OR NEW.mode NOT IN ('testing','production') OR NEW.target='' OR NEW.slot='' OR NEW.discovery=''
           OR NEW.window_start<>NEW.backup_at THEN
            RAISE EXCEPTION 'Invalid restore uncertainty inventory' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(SELECT 1 FROM stewardship_restore_hold_resolution k WHERE k.hold_id=NEW.id AND k.version=NEW.version
        AND k.state=NEW.state AND k.evidence=NEW.evidence AND k.recovery_occurrence_id IS NOT DISTINCT FROM NEW.recovery_occurrence_id
        AND k.actor_id IS NOT DISTINCT FROM NEW.actor_id AND k.correlation_id=NEW.correlation_id AND NEW.resolved_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Restore hold mutation requires exact resolution' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_restore_hold_v1 BEFORE INSERT OR UPDATE OR DELETE ON stewardship_restore_delivery_hold
FOR EACH ROW EXECUTE FUNCTION stewardship_restore_hold_v1();

CREATE FUNCTION stewardship_restore_resolution_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE h stewardship_restore_delivery_hold%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO h FROM stewardship_restore_delivery_hold WHERE id=NEW.hold_id FOR UPDATE;
    IF h.id IS NULL OR NEW.version<>h.version+1 OR NEW.actor_id IS NULL OR btrim(NEW.evidence)=''
       OR NEW.state NOT IN ('assumed_delivered','resend_authorized','not_applicable')
       OR h.state IN ('resend_authorized','not_applicable')
       OR (NEW.state='resend_authorized' AND NOT EXISTS(
           SELECT 1 FROM stewardship_schedule_occurrence o WHERE o.id=NEW.recovery_occurrence_id
           AND o.definition_id=h.definition_id AND o.mode=h.mode AND o.target=h.target AND o.slot=h.slot AND o.state='pending'
       )) OR (NEW.state<>'resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Invalid restore hold review or resend binding' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_restore_resolution_v1 BEFORE INSERT ON stewardship_restore_hold_resolution
FOR EACH ROW EXECUTE FUNCTION stewardship_restore_resolution_v1();

CREATE FUNCTION stewardship_restore_resolution_effect_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    UPDATE stewardship_restore_delivery_hold SET state=NEW.state,evidence=NEW.evidence,recovery_occurrence_id=NEW.recovery_occurrence_id,
        resolved_at=stewardship_campaign_now_v1(),version=NEW.version,actor_id=NEW.actor_id,correlation_id=NEW.correlation_id WHERE id=NEW.hold_id;
    INSERT INTO stewardship_audit_event(id,actor_id,correlation_id,event_type,subject_id,campaign_reference)
    SELECT gen_random_uuid(),NEW.actor_id,NEW.correlation_id,'restore_hold_resolved',NEW.id,d.campaign_id
    FROM stewardship_restore_delivery_hold h JOIN stewardship_schedule_definition d ON d.id=h.definition_id WHERE h.id=NEW.hold_id;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_restore_resolution_effect_v1 AFTER INSERT ON stewardship_restore_hold_resolution
FOR EACH ROW EXECUTE FUNCTION stewardship_restore_resolution_effect_v1();

CREATE FUNCTION stewardship_coverage_valid_v1(manifest jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE item jsonb; days jsonb; canonical jsonb;
BEGIN
    IF jsonb_typeof(manifest)<>'object' OR NOT (manifest ?& ARRAY['items','daily_range']) OR manifest-ARRAY['items','daily_range']<>'{}'::jsonb
       OR jsonb_typeof(manifest->'items')<>'array' OR jsonb_array_length(manifest->'items')>10000 THEN RETURN false; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(manifest->'items') LOOP
        IF jsonb_typeof(item)<>'object' OR NOT (item ?& ARRAY['kind','id','version']) OR item-ARRAY['kind','id','version']<>'{}'::jsonb
           OR jsonb_typeof(item->'kind')<>'string' OR jsonb_typeof(item->'id')<>'string'
           OR item->>'kind' NOT IN ('submission','item','correction') OR (item->>'id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(item->'version')<>'number' OR (item->>'version') !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
    END LOOP;
    SELECT coalesce(jsonb_agg(value ORDER BY value->>'kind',value->>'id',(value->>'version')::numeric),'[]'::jsonb)
    INTO canonical FROM (SELECT DISTINCT value FROM jsonb_array_elements(manifest->'items')) entries;
    IF canonical<>manifest->'items' THEN RETURN false; END IF;
    days:=manifest->'daily_range';
    IF days='null'::jsonb THEN RETURN jsonb_array_length(canonical)>0; END IF;
    IF jsonb_typeof(days)<>'object' OR NOT(days ?& ARRAY['start','end']) OR days-ARRAY['start','end']<>'{}'::jsonb
       OR jsonb_typeof(days->'start')<>'string' OR jsonb_typeof(days->'end')<>'string'
       OR (days->>'start') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR (days->>'end') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RETURN false; END IF;
    RETURN (days->>'start')::date <= (days->>'end')::date;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
END $$;

CREATE FUNCTION stewardship_postclose_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF NEW.actor_id IS NULL OR NEW.mode NOT IN ('testing','production') OR NEW.obligation_key='' OR btrim(NEW.reason)=''
       OR stewardship_coverage_valid_v1(NEW.coverage) IS DISTINCT FROM true
       OR NEW.coverage_digest<>encode(sha256(convert_to(NEW.coverage::text,'UTF8')),'hex')
       OR NOT EXISTS(SELECT 1 FROM stewardship_campaign c JOIN stewardship_system_configuration r ON r.current_campaign_id=c.id
           WHERE c.id=NEW.campaign_id AND c.state='closed' AND NOT r.restore_review_required)
       OR (NEW.occurrence_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
           JOIN stewardship_schedule_definition d ON d.id=o.definition_id WHERE o.id=NEW.occurrence_id
           AND d.campaign_id=NEW.campaign_id AND o.mode=NEW.mode AND o.state='skipped'))
       OR (NEW.task_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stewardship_task_run t WHERE t.id=NEW.task_id AND t.state='cancelled')) THEN
        RAISE EXCEPTION 'Post-close skip requires exact coverage and cancellation evidence' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_postclose_guard_v1 BEFORE INSERT ON stewardship_postclose_resolution
FOR EACH ROW EXECUTE FUNCTION stewardship_postclose_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0013_control_guards")]
    operations = [
        migrations.RunSQL(
            SQL,
            reverse_sql="""
DROP TRIGGER stewardship_postclose_guard_v1 ON stewardship_postclose_resolution;
DROP FUNCTION stewardship_postclose_guard_v1();
DROP FUNCTION stewardship_coverage_valid_v1(jsonb);
DROP TRIGGER stewardship_restore_resolution_effect_v1 ON stewardship_restore_hold_resolution;
DROP FUNCTION stewardship_restore_resolution_effect_v1();
DROP TRIGGER stewardship_restore_resolution_v1 ON stewardship_restore_hold_resolution;
DROP FUNCTION stewardship_restore_resolution_v1();
DROP TRIGGER stewardship_restore_hold_v1 ON stewardship_restore_delivery_hold;
DROP FUNCTION stewardship_restore_hold_v1();
""",
        )
    ]
