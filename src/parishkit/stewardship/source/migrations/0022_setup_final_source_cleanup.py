"""Extend exact expired-setup disposal to its independently fenced final load."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.source.migrations.0021_setup_source_cleanup"
)
BACKWARD = _previous.HELPERS.split("$$;", 1)[0] + "$$;"
BACKWARD = BACKWARD.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
_catalog = """AND original.root_id=attempt.source_task_id
            AND original.task_type='setup_source_load'
            AND original.domain_request_id=attempt.id"""
_both = """AND (
                (original.root_id=attempt.source_task_id
                 AND original.task_type='setup_source_load'
                 AND original.domain_request_id=attempt.id)
                OR EXISTS (
                    SELECT 1 FROM public.stewardship_setup_prepared prepared
                    JOIN public.stewardship_setup_readiness_binding readiness
                        ON readiness.id=prepared.readiness_id
                    JOIN public.stewardship_setup_config_intent intent
                        ON intent.id=readiness.intent_id
                        AND intent.attempt_id=attempt.id
                    JOIN public.stewardship_task_run root ON root.id=original.root_id
                        AND root.task_type='setup_finalize'
                        AND root.domain_request_id=prepared.id
                        AND root.idempotency_key=prepared.id::text
                        AND root.initiated_by_id=attempt.owner_id
                    WHERE original.task_type='setup_finalize'
                        AND original.domain_request_id=prepared.id
                        AND original.initiated_by_id=attempt.owner_id
                )
            )"""
if BACKWARD.count(_catalog) != 1:
    raise RuntimeError("Frozen initial source disposal guard differs.")
FORWARD = BACKWARD.replace(_catalog, _both)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0021_setup_source_cleanup"),
        ("stewardship_accounts", "0087_setup_prepared_guards"),
    ]
    operations = [
        migrations.RunSQL(
            FORWARD,
            """DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM public.stewardship_task_run
                    WHERE task_type='setup_finalize') THEN
                    RAISE EXCEPTION 'Final setup history prevents cleanup downgrade'
                        USING ERRCODE='23514';
                END IF;
            END $$;"""
            + BACKWARD,
        )
    ]
