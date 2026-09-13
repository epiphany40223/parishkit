"""Bind local activity to immutable authority without changing old validators."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


def change_predicates(editor, *, forward):
    """Extend only exact schema predicates; retain every earlier projection check."""
    predicates = {
        "stewardship_policy_projection_v1": (
            "version.validation_schema IN "
            "('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1')",
            "version.validation_schema IN ('foundation-policy-v2', "
            "'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4')",
        ),
        "stewardship_policy_complete_v1": (
            "NEW.validation_schema NOT IN "
            "('foundation-policy-v2', 'campaign-foundation-v3', 'bootstrap-policy-v1')",
            "NEW.validation_schema NOT IN ('foundation-policy-v2', "
            "'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4')",
        ),
        "stewardship_campaign_projection_v1": (
            "v.validation_schema = 'campaign-foundation-v3'",
            "v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4')",
        ),
        "stewardship_campaign_complete_v1": (
            "NEW.validation_schema <> 'campaign-foundation-v3'",
            "NEW.validation_schema NOT IN "
            "('campaign-foundation-v3', 'ministry-activity-v4')",
        ),
    }
    for name, (old, new) in predicates.items():
        if not forward:
            old, new = new, old
        with editor.connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_functiondef(%s::regprocedure)",
                ["public." + name + "()"],
            )
            definition = cursor.fetchone()[0]
        if definition.count(old) != 1:
            raise RuntimeError("Unexpected Ministry schema migration predecessor.")
        editor.execute(definition.replace(old, new), params=None)


def forward(apps, editor):
    """Install exact projection, ancestry and deferred completeness guards."""
    change_predicates(editor, forward=True)
    editor.execute("""
CREATE FUNCTION public.stewardship_ministry_projection_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'ministries') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema='ministry-activity-v4'
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object(
        'id', NEW.record_id, 'values', jsonb_build_object(
            'organization_id', NEW.organization_id,
            'ministry_duid', NEW.ministry_duid, 'active', NEW.active)) THEN
        RAISE EXCEPTION 'Ministry activity differs from YAML' USING ERRCODE='23514';
    END IF;
    SELECT predecessor_id INTO predecessor
    FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    IF EXISTS (
        WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM public.stewardship_configuration_version
            WHERE id=predecessor
            UNION
            SELECT p.id, p.predecessor_id
            FROM public.stewardship_configuration_version p
            JOIN chain c ON p.id=c.predecessor_id
        ) SELECT 1 FROM public.stewardship_ministry_activity m
          JOIN chain c ON m.configuration_id=c.id
          WHERE (m.record_id=NEW.record_id AND
                 (m.organization_id, m.ministry_duid) IS DISTINCT FROM
                 (NEW.organization_id, NEW.ministry_duid))
             OR (m.record_id<>NEW.record_id AND
                 m.organization_id=NEW.organization_id AND
                 m.ministry_duid=NEW.ministry_duid)
    ) THEN
        RAISE EXCEPTION 'Ministry activity identities must remain stable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_ministry_projection_v1
BEFORE INSERT ON public.stewardship_ministry_activity
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_projection_v1();

CREATE FUNCTION public.stewardship_ministry_complete_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE records jsonb; actual jsonb; vals jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'ministries', '[]'::jsonb);
    IF NEW.validation_schema <> 'ministry-activity-v4' THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Ministry activity requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid Ministry activity shape' USING ERRCODE='23514';
    END IF;
    FOR vals IN SELECT item->'values' FROM jsonb_array_elements(records) item LOOP
        IF jsonb_typeof(vals->'organization_id') IS DISTINCT FROM 'number'
           OR jsonb_typeof(vals->'ministry_duid') IS DISTINCT FROM 'number'
           OR vals->>'organization_id' !~ '^[1-9][0-9]*$'
           OR vals->>'ministry_duid' !~ '^[1-9][0-9]*$'
           OR jsonb_typeof(vals->'active') IS DISTINCT FROM 'boolean' THEN
            RAISE EXCEPTION 'Invalid Ministry activity types' USING ERRCODE='23514';
        END IF;
    END LOOP;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id,
        'values', jsonb_build_object('organization_id', organization_id,
             'ministry_duid', ministry_duid, 'active', active)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_ministry_activity WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Ministry activity projections are incomplete'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_ministry_complete_v1
AFTER INSERT ON public.stewardship_configuration_version
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_complete_v1();

-- This storage increment must not leave seeded scope stale. DAT-05's owning
-- source-effects transaction will replace this barrier with exact evidence.
-- Manual assignments and unrelated configuration edits remain available.
CREATE FUNCTION public.stewardship_ministry_activation_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE before_records jsonb; after_records jsonb;
BEGIN
    SELECT coalesce(canonical_document->'sections'->'ministries', '[]'::jsonb)
    INTO before_records FROM public.stewardship_configuration_version
    WHERE id=NEW.predecessor_id;
    SELECT coalesce(canonical_document->'sections'->'ministries', '[]'::jsonb)
    INTO after_records FROM public.stewardship_configuration_version
    WHERE id=NEW.configuration_id;
    IF coalesce(before_records, '[]'::jsonb) IS DISTINCT FROM after_records
       AND EXISTS (SELECT 1 FROM public.stewardship_ministry_assignment
                   WHERE configuration_id=NEW.configuration_id
                     AND source='chair-seed') THEN
        RAISE EXCEPTION 'Ministry activity requires seeded assignment reconciliation'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_ministry_activation_v1
BEFORE INSERT ON public.stewardship_config_activation
FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_activation_v1();
    """)


def backward(apps, editor):
    """Refuse populated downgrade before removing any retained-history guard."""
    editor.execute("""
LOCK TABLE public.stewardship_configuration_version, public.stewardship_config_request,
    public.stewardship_ministry_activity IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_ministry_activity)
       OR EXISTS (SELECT 1 FROM public.stewardship_configuration_version
                  WHERE validation_schema='ministry-activity-v4')
       OR EXISTS (SELECT 1 FROM public.stewardship_config_request
                  WHERE request_schema IN ('ministry-activity-patch-v4',
                                           'operator-recovery-ministry-v4')) THEN
        RAISE EXCEPTION 'Ministry activity history prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_ministry_complete_v1
    ON public.stewardship_configuration_version;
DROP FUNCTION public.stewardship_ministry_complete_v1();
DROP TRIGGER stewardship_ministry_projection_v1 ON public.stewardship_ministry_activity;
DROP FUNCTION public.stewardship_ministry_projection_v1();
DROP TRIGGER stewardship_ministry_activation_v1 ON public.stewardship_config_activation;
DROP FUNCTION public.stewardship_ministry_activation_v1();
    """)
    change_predicates(editor, forward=False)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0038_ministry_activity"),
        ("stewardship_campaigns", "0032_session_epoch_and_instant_guards"),
    ]

    operations = [
        immutable_guard_v1("stewardship_ministry_activity"),
        migrations.RunPython(forward, backward),
    ]
