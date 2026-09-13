"""Stage the first campaign against an exact ready setup source result."""

from importlib import import_module

from django.db import migrations, models

_prior = import_module(
    "parishkit.stewardship.accounts.migrations.0060_setup_draft_guards"
)
BACKWARD = (
    "CREATE OR REPLACE FUNCTION stewardship_setup_draft_guard_v1()"
    + (
        _prior.FORWARD.split("CREATE FUNCTION stewardship_setup_draft_guard_v1()", 1)[
            1
        ].split("CREATE TRIGGER stewardship_setup_draft_admission", 1)[0]
    )
)
_marker = "        WHEN 'testing' THEN ARRAY['testing_recipient'] END;"
if BACKWARD.count(_marker) != 1:
    raise RuntimeError("Frozen public setup vocabulary is unavailable.")
FORWARD = BACKWARD.replace(
    _marker,
    "        WHEN 'campaign' THEN ARRAY['campaign','source_result']\n" + _marker,
)
_shape = "    RETURN NEW;\nEND $$;"
_proof = """
    IF NEW.step='campaign' AND (
        jsonb_typeof(NEW.values->'campaign') IS DISTINCT FROM 'object'
        OR jsonb_typeof(NEW.values->'source_result') IS DISTINCT FROM 'string'
        OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_source_result result
            JOIN public.stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
                AND exchange.scrubbed_at IS NULL
            JOIN public.stewardship_task_run task ON task.id=exchange.task_id
                AND task.state='succeeded' AND task.root_id=attempt.source_task_id
                AND task.fence=exchange.task_fence
            JOIN public.stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN public.stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
            WHERE result.id::text=NEW.values->>'source_result'
        )
    ) THEN
        RAISE EXCEPTION 'Setup campaign requires its validated original source result'
            USING ERRCODE='23514';
    END IF;
"""
if FORWARD.count(_shape) != 1:
    raise RuntimeError("Frozen public setup final check is unavailable.")
FORWARD = FORWARD.replace(_shape, _proof + _shape)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0071_setup_worker_completion"),
        ("stewardship_source", "0021_setup_source_cleanup"),
    ]
    operations = [
        migrations.RemoveConstraint(
            model_name="setupdraftsection", name="setup_public_step"
        ),
        migrations.AddConstraint(
            model_name="setupdraftsection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    step__in=[
                        "parish",
                        "branding",
                        "access",
                        "mail",
                        "slack",
                        "testing",
                        "campaign",
                    ]
                ),
                name="setup_public_step",
            ),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
