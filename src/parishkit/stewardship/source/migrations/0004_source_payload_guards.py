"""Frozen canonicalization, append-only payloads and snapshot membership guards."""

from django.db import migrations

KINDS = (
    "family",
    "member",
    "contact",
    "address",
    "ministry",
    "roster",
    "fund",
    "pledge",
    "contribution",
)

FUNCTIONS = """
CREATE FUNCTION stewardship_source_canonical(value jsonb, depth integer DEFAULT 0)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE result text;
BEGIN
    IF depth > 16 THEN
        RAISE EXCEPTION 'Source JSON nesting exceeds its bound' USING ERRCODE='23514';
    END IF;
    CASE jsonb_typeof(value)
    WHEN 'object' THEN
        SELECT '{' || COALESCE(string_agg(to_jsonb(key)::text || ':' ||
            stewardship_source_canonical(item, depth+1), ','
            ORDER BY key COLLATE "C"), '')
            || '}' INTO result FROM jsonb_each(value) AS pairs(key,item);
    WHEN 'array' THEN
        SELECT '[' || COALESCE(string_agg(stewardship_source_canonical(item,depth+1),
            ',' ORDER BY ordinal), '') || ']' INTO result
            FROM jsonb_array_elements(value) WITH ORDINALITY AS items(item,ordinal);
    WHEN 'number' THEN
        IF value::text !~ '^-?(0|[1-9][0-9]*)$'
           OR value::numeric < -9223372036854775808
           OR value::numeric > 9223372036854775807 THEN
            RAISE EXCEPTION 'Source JSON requires bounded integers'
                USING ERRCODE='23514';
        END IF;
        result := value::text;
    ELSE result := value::text;
    END CASE;
    RETURN result;
END;
$$;

CREATE FUNCTION stewardship_source_payload_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE field text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'Source payload versions are immutable' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
            JOIN stewardship_task_run t ON t.id=l.owner_id
            WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
              AND t.state='running' AND t.fence=l.task_fence AND t.worker_id=l.worker_id
              AND t.lease_expires_at > clock_timestamp()) THEN
            RAISE EXCEPTION 'Source deletion requires compaction ownership'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF octet_length(NEW.canonical) > 1048576
       OR jsonb_typeof(NEW.canonical::jsonb) <> 'object'
       OR NEW.canonical <> stewardship_source_canonical(NEW.canonical::jsonb)
       OR NEW.digest <> encode(sha256(convert_to(NEW.canonical,'UTF8')),'hex') THEN
        RAISE EXCEPTION 'Source payload digest or canonical form is invalid'
            USING ERRCODE='23514';
    END IF;
    FOREACH field IN ARRAY COALESCE(TG_ARGV, ARRAY[]::text[]) LOOP
        IF (to_jsonb(NEW)->>field) IS DISTINCT FROM (NEW.canonical::jsonb->>field)
           OR COALESCE(to_jsonb(NEW)->>field,'') = '' THEN
            RAISE EXCEPTION 'Source relationship differs from its payload'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE FUNCTION stewardship_source_membership_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE snapshot stewardship_source_snapshot%ROWTYPE;
        payload_key text;
        payload_org bigint;
        snapshot_id uuid;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'Snapshot membership is immutable' USING ERRCODE='23514';
    END IF;
    snapshot_id := CASE WHEN TG_OP='DELETE'
        THEN OLD.snapshot_id ELSE NEW.snapshot_id END;
    SELECT * INTO snapshot FROM stewardship_source_snapshot WHERE id=snapshot_id
        FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Snapshot membership requires its manifest'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF snapshot.compacted_at IS NULL AND snapshot.state <> 'rejected' THEN
            RAISE EXCEPTION 'Reconstructable snapshot membership is protected'
                USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF snapshot.state <> 'staging' OR NOT EXISTS (
        SELECT 1 FROM stewardship_source_lease l JOIN stewardship_task_run t
          ON t.id=l.owner_id WHERE l.owner_id=snapshot.task_id
          AND l.fence=snapshot.source_fence AND l.phase IN ('full','delta')
          AND l.expires_at > clock_timestamp() AND t.state='running'
          AND t.fence=l.task_fence AND t.worker_id=l.worker_id
          AND t.lease_expires_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Snapshot membership requires live staging ownership'
            USING ERRCODE='23514';
    END IF;
    EXECUTE format('SELECT source_key,organization_id FROM %I WHERE id=$1', TG_ARGV[0])
        INTO payload_key,payload_org USING NEW.payload_id;
    IF payload_key IS DISTINCT FROM NEW.source_key
       OR payload_org IS DISTINCT FROM snapshot.organization_id THEN
        RAISE EXCEPTION 'Snapshot membership identity differs from its payload'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
"""

RELATIONSHIPS = {
    "member": ("family_key",),
    "contact": ("owner_kind", "owner_key"),
    "address": ("owner_kind", "owner_key"),
    "roster": ("member_key", "ministry_key"),
    "pledge": ("family_key", "fund_key"),
    "contribution": ("family_key", "fund_key"),
}


def trigger_sql():
    """Only frozen internal table and field names enter these migration statements."""
    statements = []
    for kind in KINDS:
        args = ",".join("'" + name + "'" for name in RELATIONSHIPS.get(kind, ()))
        statements.extend(
            [
                f"CREATE TRIGGER source_{kind}_payload "
                "BEFORE INSERT OR UPDATE OR DELETE "
                f"ON stewardship_source_{kind} FOR EACH ROW "
                f"EXECUTE FUNCTION stewardship_source_payload_guard({args});",
                f"CREATE TRIGGER snapshot_{kind}_membership "
                "BEFORE INSERT OR UPDATE OR DELETE "
                f"ON stewardship_snapshot_{kind} FOR EACH ROW "
                "EXECUTE FUNCTION stewardship_source_membership_guard("
                f"'stewardship_source_{kind}');",
            ]
        )
    return "\n".join(statements)


def reverse_sql():
    """Remove only this migration's triggers before removing their functions."""
    statements = []
    for kind in KINDS:
        statements.extend(
            [
                f"DROP TRIGGER source_{kind}_payload ON stewardship_source_{kind};",
                f"DROP TRIGGER snapshot_{kind}_membership "
                f"ON stewardship_snapshot_{kind};",
            ]
        )
    return (
        "\n".join(statements)
        + """
        DROP FUNCTION stewardship_source_membership_guard();
        DROP FUNCTION stewardship_source_payload_guard();
        DROP FUNCTION stewardship_source_canonical(jsonb,integer);
    """
    )


class Migration(migrations.Migration):
    dependencies = [
        (
            "stewardship_source",
            "0003_sourceaddress_sourcecontact_sourcecontribution_and_more",
        )
    ]
    operations = [migrations.RunSQL(FUNCTIONS + trigger_sql(), reverse_sql())]
