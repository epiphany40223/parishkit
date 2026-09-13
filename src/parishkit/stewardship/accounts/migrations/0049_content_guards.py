"""Exact immutable content projections and retained schema discrimination."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


def change_predicates(editor, *, forward):
    """Extend exact prior predicates without replacing their other protections."""
    predicates = {
        "stewardship_policy_projection_v1": (
            "version.validation_schema IN ('foundation-policy-v2', "
            "'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4')",
            "version.validation_schema IN ('foundation-policy-v2', "
            "'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4', "
            "'campaign-content-v5')",
        ),
        "stewardship_policy_complete_v1": (
            "NEW.validation_schema NOT IN ('foundation-policy-v2', "
            "'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4')",
            "NEW.validation_schema NOT IN ('foundation-policy-v2', "
            "'campaign-foundation-v3', 'bootstrap-policy-v1', 'ministry-activity-v4', "
            "'campaign-content-v5')",
        ),
        "stewardship_campaign_projection_v1": (
            "v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4')",
            "v.validation_schema IN ('campaign-foundation-v3', 'ministry-activity-v4', "
            "'campaign-content-v5')",
        ),
        "stewardship_campaign_complete_v1": (
            "NEW.validation_schema NOT IN "
            "('campaign-foundation-v3', 'ministry-activity-v4')",
            "NEW.validation_schema NOT IN "
            "('campaign-foundation-v3', 'ministry-activity-v4', 'campaign-content-v5')",
        ),
        "stewardship_ministry_projection_v1": (
            "v.validation_schema='ministry-activity-v4'",
            "v.validation_schema IN ('ministry-activity-v4', 'campaign-content-v5')",
        ),
        "stewardship_ministry_complete_v1": (
            "NEW.validation_schema <> 'ministry-activity-v4'",
            "NEW.validation_schema NOT IN "
            "('ministry-activity-v4', 'campaign-content-v5')",
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
            raise RuntimeError("Unexpected content schema migration predecessor.")
        editor.execute(definition.replace(old, new), params=None)


def forward(apps, editor):
    """Bind revisions to canonical YAML and refuse incomplete or rebound history."""
    change_predicates(editor, forward=True)
    editor.execute("""
CREATE FUNCTION public.stewardship_content_projection_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE expected jsonb; predecessor uuid;
BEGIN
    SELECT item INTO expected
    FROM public.stewardship_configuration_version v,
         jsonb_array_elements(v.canonical_document->'sections'->'content') item
    WHERE v.id=NEW.configuration_id AND v.validation_schema='campaign-content-v5'
      AND item->>'id'=NEW.record_id::text;
    IF expected IS DISTINCT FROM jsonb_build_object('id', NEW.record_id,
        'values', jsonb_build_object('campaign_id', NEW.campaign_id,
            'kind', NEW.kind, 'slot', NEW.slot, 'subject', NEW.subject,
            'html', NEW.html, 'text', NEW.text))
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_configuration
                      WHERE configuration_id=NEW.configuration_id
                        AND record_id=NEW.campaign_id) THEN
        RAISE EXCEPTION 'Content differs from YAML' USING ERRCODE='23514';
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
        ) SELECT 1 FROM public.stewardship_content_version v
          JOIN chain c ON c.id=v.configuration_id
          WHERE v.record_id=NEW.record_id AND
              (v.campaign_id, v.kind, v.slot, v.subject, v.html, v.text)
              IS DISTINCT FROM
              (NEW.campaign_id, NEW.kind, NEW.slot, NEW.subject, NEW.html, NEW.text)
    ) THEN
        RAISE EXCEPTION 'Content revision identities must remain immutable'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_content_projection_v1
BEFORE INSERT ON public.stewardship_content_version
FOR EACH ROW EXECUTE FUNCTION public.stewardship_content_projection_v1();

CREATE FUNCTION public.stewardship_content_complete_v1()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE records jsonb; actual jsonb;
BEGIN
    records := coalesce(NEW.canonical_document->'sections'->'content', '[]'::jsonb);
    IF NEW.validation_schema <> 'campaign-content-v5' THEN
        IF records <> '[]'::jsonb THEN
            RAISE EXCEPTION 'Content requires its versioned schema'
                USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid content shape' USING ERRCODE='23514';
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_content_version
        WHERE configuration_id=NEW.id AND NOT (
            (kind='page' AND slot IN ('welcome', 'login_help', 'pre_start', 'post_end',
                'census', 'member_census', 'ministry', 'financial', 'additional',
                'review', 'thank_you', 'access_denied', 'submission_confirmation'))
            OR (kind='email' AND slot IN ('initial', 'reminder', 'confirmation',
                'daily_digest', 'weekly_digest', 'critical_alert'))
        )) THEN
        RAISE EXCEPTION 'Invalid content slot' USING ERRCODE='23514';
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object('id', record_id, 'values',
        jsonb_build_object('campaign_id', campaign_id, 'kind', kind, 'slot', slot,
            'subject', subject, 'html', html, 'text', text)) ORDER BY record_id),
        '[]'::jsonb) INTO actual
    FROM public.stewardship_content_version WHERE configuration_id=NEW.id;
    IF actual IS DISTINCT FROM (
        SELECT coalesce(jsonb_agg(item ORDER BY item->>'id'), '[]'::jsonb)
        FROM jsonb_array_elements(records) item
    ) THEN
        RAISE EXCEPTION 'Content projections are incomplete' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER stewardship_content_complete_v1
AFTER INSERT ON public.stewardship_configuration_version
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION public.stewardship_content_complete_v1();
    """)


def backward(apps, editor):
    """Retained revision or request history prevents weakening its interpretation."""
    editor.execute("""
LOCK TABLE public.stewardship_configuration_version, public.stewardship_config_request,
    public.stewardship_content_version IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_content_version)
       OR EXISTS (SELECT 1 FROM public.stewardship_configuration_version
                  WHERE validation_schema='campaign-content-v5')
       OR EXISTS (SELECT 1 FROM public.stewardship_config_request
                  WHERE request_schema IN ('campaign-content-patch-v5',
                                           'operator-recovery-content-v5')) THEN
        RAISE EXCEPTION 'Content history prevents schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_content_complete_v1
    ON public.stewardship_configuration_version;
DROP FUNCTION public.stewardship_content_complete_v1();
DROP TRIGGER stewardship_content_projection_v1 ON public.stewardship_content_version;
DROP FUNCTION public.stewardship_content_projection_v1();
    """)
    change_predicates(editor, forward=False)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0048_content_versions"),
        ("stewardship_campaigns", "0034_presence_guards"),
    ]
    operations = [
        immutable_guard_v1("stewardship_content_version"),
        migrations.RunPython(forward, backward),
    ]
