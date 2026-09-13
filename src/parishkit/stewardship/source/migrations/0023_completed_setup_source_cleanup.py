"""Completed setup may discard its unused catalog, never its promoted corpus."""

from importlib import import_module

from django.db import migrations

BACKWARD = import_module(
    "parishkit.stewardship.source.migrations.0022_setup_final_source_cleanup"
).FORWARD
_expired = "attempt.state='expired'"
_terminal = """(attempt.state='expired' OR (attempt.state='completed' AND EXISTS (
                SELECT 1 FROM public.stewardship_setup_completion completed
                JOIN public.stewardship_setup_prepared prepared
                    ON prepared.id=completed.preparation_id
                JOIN public.stewardship_setup_readiness_binding ready
                    ON ready.id=prepared.readiness_id
                JOIN public.stewardship_setup_config_intent intent
                    ON intent.id=ready.intent_id AND intent.attempt_id=attempt.id
            )))"""
if BACKWARD.count(_expired) != 1:
    raise RuntimeError("Terminal setup source cleanup predecessor differs.")
FORWARD = BACKWARD.replace(_expired, _terminal)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0022_setup_final_source_cleanup"),
        ("stewardship_accounts", "0089_setup_completion_guards"),
    ]
    operations = [
        migrations.RunSQL(
            FORWARD,
            """DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM public.stewardship_setup_completion) THEN
                    RAISE EXCEPTION 'Completed setup prevents cleanup downgrade'
                        USING ERRCODE='23514';
                END IF;
            END $$;"""
            + BACKWARD,
        )
    ]
