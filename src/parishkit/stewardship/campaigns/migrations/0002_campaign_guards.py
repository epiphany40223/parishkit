"""Bind campaign projections and Testing runtime to the configuration ledger.

This migration deliberately refuses Production and historical campaign writes.
Later lifecycle/purge packages must replace these conservative guards together
with their durable evidence; a pure policy decision cannot open those paths.
"""

from django.db import migrations

# SQL function bodies retain their native layout for comparison with PostgreSQL.
# ruff: noqa: E501
from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)


def extend_policy_schema(apps, editor):
    """Extend only two schema predicates, retaining all existing policy SQL checks."""
    replacements = {
        "stewardship_policy_projection_v1": (
            "version.validation_schema = 'foundation-policy-v2'",
            "version.validation_schema IN ('foundation-policy-v2', 'campaign-foundation-v3')",
        ),
        "stewardship_policy_complete_v1": (
            "NEW.validation_schema <> 'foundation-policy-v2'",
            "NEW.validation_schema NOT IN ('foundation-policy-v2', 'campaign-foundation-v3')",
        ),
    }
    for name, (old, new) in replacements.items():
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [name + "()"])
            sql = cursor.fetchone()[0]
        if sql.count(old) != 1:
            raise RuntimeError("Unexpected policy migration predecessor.")
        editor.execute(sql.replace(old, new), params=None)


def restore_policy_schema(apps, editor):
    """Refuse populated downgrade before any campaign data or schema is removed."""
    editor.execute("""
        LOCK TABLE stewardship_configuration_version, stewardship_config_request,
            stewardship_campaign IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM stewardship_configuration_version
                       WHERE validation_schema = 'campaign-foundation-v3')
               OR EXISTS (SELECT 1 FROM stewardship_config_request
                          WHERE request_schema IN ('campaign-foundation-patch-v3',
                                                   'operator-recovery-patch-v2'))
               OR EXISTS (SELECT 1 FROM stewardship_campaign) THEN
                RAISE EXCEPTION 'Campaign history prevents schema downgrade'
                    USING ERRCODE = '23514';
            END IF;
        END $$;
    """)
    for name, prefix, operator in (
        ("stewardship_policy_projection_v1", "version", "="),
        ("stewardship_policy_complete_v1", "NEW", "<>"),
    ):
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [name + "()"])
            sql = cursor.fetchone()[0]
        expanded = f"{prefix}.validation_schema {'NOT ' if operator == '<>' else ''}IN ('foundation-policy-v2', 'campaign-foundation-v3')"
        if sql.count(expanded) != 1:
            raise RuntimeError("Unexpected policy migration successor.")
        editor.execute(
            sql.replace(
                expanded,
                f"{prefix}.validation_schema {operator} 'foundation-policy-v2'",
            ),
            params=None,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0001_initial"),
        ("stewardship_audit", "0006_parish_ownership"),
        (
            "stewardship_accounts",
            "0022_remove_appliedconfigurationversion_configuration_validation_schema_and_more",
        ),
    ]
    operations = [
        immutable_guard_v1("stewardship_campaign_configuration"),
        immutable_guard_v1("stewardship_schedule_revision"),
        mutable_guard_v1("stewardship_campaign", frozen_fields=("state",)),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION stewardship_resolve_local_v1(wall timestamp, zone text)
RETURNS timestamptz LANGUAGE plpgsql STABLE STRICT
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE nominal timestamptz; earliest timestamptz; low timestamptz;
    high timestamptz; middle timestamptz; width bigint;
BEGIN
    nominal := wall AT TIME ZONE zone;
    -- Collect adjacent offset regimes, including non-hour shifts and a skipped
    -- whole day. Round trips distinguish folds from hypothetical gap offsets.
    WITH offsets AS (
        SELECT DISTINCT (probe AT TIME ZONE zone) - (probe AT TIME ZONE 'UTC') AS delta
        FROM generate_series(nominal - interval '48 hours',
                             nominal + interval '48 hours', interval '1 hour') probe
    ), candidates AS (
        SELECT (wall - delta) AT TIME ZONE 'UTC' AS instant FROM offsets
    )
    SELECT min(instant) FILTER (WHERE instant AT TIME ZONE zone = wall),
           min(instant), max(instant)
    INTO earliest, low, high FROM candidates;
    IF earliest IS NOT NULL THEN RETURN earliest; END IF;
    IF low IS NULL OR high IS NULL OR low >= high
       OR low AT TIME ZONE zone >= wall OR high AT TIME ZONE zone <= wall THEN
        RAISE EXCEPTION 'Local boundary cannot be resolved' USING ERRCODE = '23514';
    END IF;
    -- The candidate offsets bracket the transition. Select its first valid
    -- microsecond, not the PostgreSQL default that preserves minutes in a gap.
    LOOP
        width := extract(epoch FROM high - low) * 1000000;
        EXIT WHEN width <= 1;
        middle := low + (width / 2) * interval '1 microsecond';
        IF middle AT TIME ZONE zone < wall THEN low := middle;
        ELSE high := middle; END IF;
    END LOOP;
    RETURN high;
END $$;

CREATE FUNCTION stewardship_campaign_projection_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE expected jsonb; section text; owner_zone text; expected_due timestamptz;
BEGIN
    section := CASE WHEN TG_TABLE_NAME = 'stewardship_campaign_configuration'
                    THEN 'campaigns' ELSE 'schedules' END;
    SELECT item->'values' INTO expected
    FROM public.stewardship_configuration_version v,
        jsonb_array_elements(v.canonical_document->'sections'->section) item
    WHERE v.id = NEW.configuration_id AND item->>'id' = NEW.record_id::text
      AND v.validation_schema = 'campaign-foundation-v3';
    IF expected IS NULL OR expected IS DISTINCT FROM NEW.values THEN
        RAISE EXCEPTION 'Campaign projection differs from YAML' USING ERRCODE = '23514';
    END IF;
    IF section = 'campaigns' THEN
        IF NEW.name IS DISTINCT FROM expected->>'name'
           OR NEW.timezone IS DISTINCT FROM expected->>'timezone'
           OR NEW.start_date IS DISTINCT FROM (expected->>'start_date')::date
           OR NEW.end_date IS DISTINCT FROM (expected->>'end_date')::date
           OR jsonb_typeof(expected->'modules') IS DISTINCT FROM 'array'
           OR coalesce(jsonb_array_length(expected->'modules'), 0) < 1
           OR NOT expected->'modules' <@ '["census", "ministry", "financial"]'::jsonb THEN
            RAISE EXCEPTION 'Invalid indexed campaign projection' USING ERRCODE = '23514';
        END IF;
        IF NEW.starts_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                NEW.start_date::timestamp, NEW.timezone)
           OR NEW.ends_at IS DISTINCT FROM public.stewardship_resolve_local_v1(
                (NEW.end_date + 1)::timestamp, NEW.timezone) THEN
            RAISE EXCEPTION 'Invalid resolved campaign boundary' USING ERRCODE = '23514';
        END IF;
        IF expected->'financial' <> 'null'::jsonb AND (
            (expected->'financial'->>'end')::date IS DISTINCT FROM
                ((expected->'financial'->>'start')::date + interval '1 year -1 day')::date
            OR (expected->'financial'->>'comparison_end')::date IS DISTINCT FROM
                ((expected->'financial'->>'comparison_start')::date + interval '1 year -1 day')::date
        ) THEN
            RAISE EXCEPTION 'Invalid financial period' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.campaign_id::text IS DISTINCT FROM expected->>'campaign_id'
           OR NEW.kind IS DISTINCT FROM expected->>'kind'
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                          WHERE configuration_id = NEW.configuration_id
                            AND record_id = NEW.campaign_id) THEN
            RAISE EXCEPTION 'Invalid schedule ownership' USING ERRCODE = '23514';
        END IF;
        SELECT timezone INTO owner_zone FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.configuration_id AND record_id = NEW.campaign_id;
        IF NEW.kind IN ('initial', 'reminder') THEN
            expected_due := public.stewardship_resolve_local_v1(
                (expected->>'date')::date + (expected->>'time')::time, owner_zone);
        END IF;
        IF NEW.due_at IS DISTINCT FROM expected_due THEN
            RAISE EXCEPTION 'Invalid resolved schedule boundary' USING ERRCODE = '23514';
        END IF;
        -- Match preparation's global lock even for direct competing inserts.
        -- PL/pgSQL's following READ COMMITTED query sees the previous winner.
        PERFORM pg_advisory_xact_lock(736210, 1);
        IF EXISTS (SELECT 1 FROM public.stewardship_schedule_revision
                   WHERE record_id = NEW.record_id
                     AND (campaign_id <> NEW.campaign_id OR kind <> NEW.kind)) THEN
            RAISE EXCEPTION 'Logical schedule identity is immutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_projection_v1 BEFORE INSERT
ON public.stewardship_campaign_configuration FOR EACH ROW
EXECUTE FUNCTION stewardship_campaign_projection_v1();
CREATE TRIGGER stewardship_campaign_projection_v1 BEFORE INSERT
ON public.stewardship_schedule_revision FOR EACH ROW
EXECUTE FUNCTION stewardship_campaign_projection_v1();

CREATE FUNCTION stewardship_campaign_complete_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF NEW.validation_schema <> 'campaign-foundation-v3' THEN RETURN NULL; END IF;
    IF (SELECT count(*) FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'campaigns'), 0)
       OR (SELECT count(*) FROM public.stewardship_schedule_revision
        WHERE configuration_id = NEW.id) <> coalesce(jsonb_array_length(
            NEW.canonical_document->'sections'->'schedules'), 0) THEN
        RAISE EXCEPTION 'Campaign projections are incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_campaign_complete_v1 AFTER INSERT
ON public.stewardship_configuration_version DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_complete_v1();

CREATE FUNCTION stewardship_campaign_pointer_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE target uuid; candidate public.stewardship_campaign_configuration%ROWTYPE;
BEGIN
    -- Shared namespace reserved for creation/Return-to-Testing/purge admission.
    PERFORM pg_advisory_xact_lock(736220, 1);
    IF TG_OP = 'INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN
            RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Only campaign activation may select the pointer' USING ERRCODE = '23514';
    END IF;
    IF (SELECT count(*) FROM public.stewardship_campaign_configuration
        WHERE configuration_id = NEW.active_configuration_id) > 1 THEN
        RAISE EXCEPTION 'Only one draft is admitted' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO candidate FROM public.stewardship_campaign_configuration
    WHERE configuration_id = NEW.active_configuration_id;
    target := candidate.record_id;
    IF OLD.current_campaign_id IS NOT NULL AND target IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Current campaign cannot be removed or replaced' USING ERRCODE = '23514';
    END IF;
    IF target IS NOT NULL AND (
        NEW.mode <> 'testing' OR NEW.restore_review_required
        OR (OLD.current_campaign_id IS NULL AND EXISTS (SELECT 1 FROM public.stewardship_campaign))
        ) THEN
        RAISE EXCEPTION 'Draft creation or edit is not admitted' USING ERRCODE = '23514';
    END IF;
    IF target IS NOT NULL AND OLD.current_campaign_id IS NULL AND candidate.timezone <>
       (SELECT timezone FROM public.stewardship_parish
        WHERE configuration_id = NEW.active_configuration_id) THEN
        RAISE EXCEPTION 'A new draft must copy the parish timezone' USING ERRCODE = '23514';
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_pointer_v1 BEFORE INSERT OR UPDATE
ON public.stewardship_system_configuration FOR EACH ROW
EXECUTE FUNCTION stewardship_campaign_pointer_v1();

CREATE FUNCTION stewardship_campaign_runtime_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Campaign deletion requires exceptional purge' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stewardship_system_configuration runtime
        JOIN public.stewardship_campaign_configuration c ON c.configuration_id = runtime.active_configuration_id
        JOIN public.stewardship_config_activation a ON a.configuration_id = runtime.active_configuration_id
        WHERE runtime.current_campaign_id = NEW.id AND runtime.mode = 'testing'
          AND c.id = NEW.active_configuration_id AND c.record_id = NEW.id
          AND a.actor_id IS NOT DISTINCT FROM NEW.actor_id
          AND a.correlation_id = NEW.correlation_id
    ) OR (TG_OP = 'UPDATE' AND OLD.active_configuration_id = NEW.active_configuration_id) THEN
        RAISE EXCEPTION 'Campaign writes require matching activation' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_runtime_v1 BEFORE INSERT OR UPDATE OR DELETE
ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION stewardship_campaign_runtime_v1();

CREATE FUNCTION stewardship_campaign_activate_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE projection uuid; event_name text;
BEGIN
    IF NEW.current_campaign_id IS NULL THEN RETURN NEW; END IF;
    SELECT id INTO projection FROM public.stewardship_campaign_configuration
    WHERE configuration_id = NEW.active_configuration_id AND record_id = NEW.current_campaign_id;
    event_name := 'campaign_configured';
    IF EXISTS (
        SELECT 1 FROM public.stewardship_configuration_version old_version,
                      public.stewardship_configuration_version new_version
        WHERE old_version.id = OLD.active_configuration_id
          AND new_version.id = NEW.active_configuration_id
          AND old_version.canonical_document->'sections'->'campaigns'
              IS NOT DISTINCT FROM new_version.canonical_document->'sections'->'campaigns'
          AND old_version.canonical_document->'sections'->'schedules'
              IS NOT DISTINCT FROM new_version.canonical_document->'sections'->'schedules'
    ) THEN event_name := 'campaign_reprojected'; END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_campaign WHERE id = NEW.current_campaign_id) THEN
        UPDATE public.stewardship_campaign SET active_configuration_id = projection,
            version = version + 1, actor_id = NEW.actor_id, correlation_id = NEW.correlation_id
        WHERE id = NEW.current_campaign_id;
    ELSE
        INSERT INTO public.stewardship_campaign
            (id, state, version, active_configuration_id, actor_id, correlation_id)
        VALUES (NEW.current_campaign_id, 'draft', 1, projection, NEW.actor_id, NEW.correlation_id);
    END IF;
    INSERT INTO public.stewardship_audit_event
        (id, actor_id, correlation_id, event_type, subject_id, campaign_reference)
    VALUES (gen_random_uuid(), NEW.actor_id, NEW.correlation_id, event_name,
            NEW.current_campaign_id, NEW.current_campaign_id);
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_campaign_activate_v1 AFTER UPDATE
ON public.stewardship_system_configuration FOR EACH ROW
EXECUTE FUNCTION stewardship_campaign_activate_v1();
""",
            reverse_sql="""
DROP TRIGGER stewardship_campaign_activate_v1 ON public.stewardship_system_configuration;
DROP FUNCTION stewardship_campaign_activate_v1();
DROP TRIGGER stewardship_campaign_runtime_v1 ON public.stewardship_campaign;
DROP FUNCTION stewardship_campaign_runtime_v1();
DROP TRIGGER stewardship_campaign_pointer_v1 ON public.stewardship_system_configuration;
DROP FUNCTION stewardship_campaign_pointer_v1();
DROP TRIGGER stewardship_campaign_complete_v1 ON public.stewardship_configuration_version;
DROP FUNCTION stewardship_campaign_complete_v1();
DROP TRIGGER stewardship_campaign_projection_v1 ON public.stewardship_campaign_configuration;
DROP TRIGGER stewardship_campaign_projection_v1 ON public.stewardship_schedule_revision;
DROP FUNCTION stewardship_campaign_projection_v1();
DROP FUNCTION stewardship_resolve_local_v1(timestamp, text);
""",
        ),
        migrations.RunPython(extend_policy_schema, restore_policy_schema),
    ]
