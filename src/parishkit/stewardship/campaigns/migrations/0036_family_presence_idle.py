"""Presence remains valid for the same sixty-minute idle window as Family login."""

from importlib import import_module

from django.db import migrations

_old = import_module("parishkit.stewardship.campaigns.migrations.0034_presence_guards")
REVERSE = _old.FORWARD.split("CREATE TRIGGER stewardship_family_presence_v1", 1)[
    0
].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
if REVERSE.count("interval '30 minutes'") != 1:
    raise RuntimeError("Frozen Family presence policy is unavailable.")
FORWARD = REVERSE.replace("interval '30 minutes'", "interval '60 minutes'")


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0035_schedule_window_selections")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
