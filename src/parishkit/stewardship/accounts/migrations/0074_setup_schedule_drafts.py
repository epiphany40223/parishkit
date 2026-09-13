"""Admit bounded first-campaign schedules with original source and owner proof."""

from importlib import import_module

from django.db import migrations, models

_prior = import_module(
    "parishkit.stewardship.accounts.migrations.0073_setup_content_drafts"
)
BACKWARD = _prior.FORWARD
STEPS = [*_prior.STEPS[:7], "schedules", *_prior.STEPS[7:]]
_markers = (
    "(CASE WHEN NEW.step='campaign' ",
    "        ELSE ARRAY['id','values'] END;",
    "    IF left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' THEN",
    '        IF NEW.values<>\'{"id":null,"values":null}\'::jsonb AND (',
)
for marker in _markers:
    if BACKWARD.count(marker) != 1:
        raise RuntimeError("Frozen setup schedule admission is unavailable.")
FORWARD = BACKWARD.replace(
    _markers[0], "(CASE WHEN NEW.step IN ('campaign','schedules') "
)
FORWARD = FORWARD.replace(
    _markers[1], "        WHEN 'schedules' THEN ARRAY['records']\n" + _markers[1]
)
FORWARD = FORWARD.replace(
    _markers[2],
    "    IF NEW.step='schedules' OR left(NEW.step,5)='page_' "
    "OR left(NEW.step,6)='email_' THEN",
)
FORWARD = FORWARD.replace(
    _markers[3],
    "        IF NEW.step<>'schedules' "
    'AND NEW.values<>\'{"id":null,"values":null}\'::jsonb AND (',
)
_shape = "    RETURN NEW;\nEND $$;"
_proof = """
    IF NEW.step='schedules' THEN
        IF jsonb_typeof(NEW.values->'records') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Setup schedules require a bounded record inventory'
                USING ERRCODE='23514';
        END IF;
        IF jsonb_array_length(NEW.values->'records')>100 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.values->'records') record
            WHERE record->'values'->>'campaign_id' IS DISTINCT FROM attempt.id::text
        ) THEN
            RAISE EXCEPTION 'Setup schedules belong to their original campaign'
                USING ERRCODE='23514';
        END IF;
    END IF;
"""
if FORWARD.count(_shape) != 1:
    raise RuntimeError("Frozen setup schedule final check is unavailable.")
FORWARD = FORWARD.replace(_shape, _proof + _shape)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0073_setup_content_drafts")]
    operations = [
        migrations.RemoveConstraint(
            model_name="setupdraftsection", name="setup_public_step"
        ),
        migrations.AddConstraint(
            model_name="setupdraftsection",
            constraint=models.CheckConstraint(
                condition=models.Q(step__in=STEPS), name="setup_public_step"
            ),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
