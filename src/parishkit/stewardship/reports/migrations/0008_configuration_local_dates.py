"""Civil-date ordering is scoped to one campaign timezone/configuration."""

from django.db import migrations

# ruff: noqa: E501 -- keep exact predecessor SQL predicates readable.

REPLACEMENTS = {
    "public.stewardship_fact_reference_guard()": [
        (
            "OR target.through_date<prior.through_date",
            """OR (target.timezone_configuration_id=prior.timezone_configuration_id
                    AND target.through_date<prior.through_date)""",
        ),
    ],
    "public.stewardship_fact_demand_guard()": [
        (
            "OR NEW.requested_through_date<OLD.requested_through_date",
            """OR (NEW.requested_timezone_configuration_id=OLD.requested_timezone_configuration_id
                AND NEW.requested_through_date<OLD.requested_through_date)""",
        ),
        (
            "IF requested_changed THEN",
            """IF requested_changed AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_campaign WHERE id=NEW.campaign_id
                AND active_configuration_id=NEW.requested_timezone_configuration_id
        ) THEN
            RAISE EXCEPTION 'Fact demand requires current campaign inputs'
                USING ERRCODE='23514';
        END IF;
    IF requested_changed THEN""",
        ),
    ],
    "public.stewardship_fact_disposable(uuid)": [
        (
            "AND newer.through_date>=old.through_date",
            """AND ((newer.timezone_configuration_id=old.timezone_configuration_id
                    AND newer.through_date>=old.through_date)
                OR (newer.timezone_configuration_id<>old.timezone_configuration_id
                    AND EXISTS (SELECT 1 FROM public.stewardship_campaign c
                        WHERE c.id=newer.campaign_id
                            AND c.active_configuration_id=newer.timezone_configuration_id)))""",
        ),
    ],
}


def rewrite(editor, *, reverse=False):
    """Preserve independent watermarks, live ownership and retained input pins."""
    for signature, pairs in REPLACEMENTS.items():
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
            definition = cursor.fetchone()[0]
        for before, after in pairs:
            if reverse:
                before, after = after, before
            if definition.count(before) != 1:
                raise RuntimeError("Fact configuration-date predecessor differs.")
            definition = definition.replace(before, after)
        editor.execute(definition, params=None)


def forward(apps, editor):
    """Current configuration may shorten date bounds without regressing data inputs."""
    rewrite(editor)


def backward(apps, editor):
    """Restore older future-write behavior without modifying exact historical facts."""
    rewrite(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("stewardship_reports", "0007_exact_fact_fence")]
    operations = [migrations.RunPython(forward, backward)]
