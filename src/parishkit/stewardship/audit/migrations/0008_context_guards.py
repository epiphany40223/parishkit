"""Raw SQL cannot bypass approved context keys/types or immutable audit history."""

# ruff: noqa: E501
from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0007_auditcontext_operationallog")]
    operations = [
        immutable_guard_v1("stewardship_audit_context"),
        immutable_guard_v1("stewardship_operational_log"),
        migrations.RunSQL(
            sql=r"""
CREATE FUNCTION stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE allowed text[]; key text; value jsonb; text_value text;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        IF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key='retryable' THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $$;
ALTER TABLE stewardship_audit_context ADD CONSTRAINT audit_context_schema_safe
CHECK (schema='action' AND stewardship_safe_context_v1(schema,context));
ALTER TABLE stewardship_audit_context ADD CONSTRAINT audit_context_actor_kind
CHECK (actor_kind IN ('portal_user','family','system','operator'));
ALTER TABLE stewardship_operational_log ADD CONSTRAINT operational_context_safe
CHECK (stewardship_safe_context_v1(schema,context));
ALTER TABLE stewardship_operational_log ADD CONSTRAINT operational_event_safe
CHECK (event IN ('configuration_rejected','configuration_digest_mismatch','startup_rejected','startup_validated',
    'request_completed','task_started','task_completed','task_failed','unstructured_log_suppressed'));
""",
            reverse_sql="""
ALTER TABLE stewardship_operational_log DROP CONSTRAINT operational_event_safe;
ALTER TABLE stewardship_operational_log DROP CONSTRAINT operational_context_safe;
ALTER TABLE stewardship_audit_context DROP CONSTRAINT audit_context_actor_kind;
ALTER TABLE stewardship_audit_context DROP CONSTRAINT audit_context_schema_safe;
DROP FUNCTION stewardship_safe_context_v1(text,jsonb);
""",
        ),
    ]
