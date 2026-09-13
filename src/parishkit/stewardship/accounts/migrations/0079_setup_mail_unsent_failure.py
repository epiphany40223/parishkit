"""Retire unsent setup tests when their owning Task failed before submission."""

from importlib import import_module

from django.db import migrations

_prior = import_module(
    "parishkit.stewardship.accounts.migrations.0076_setup_mail_guards"
)
_start = "CREATE FUNCTION public.stewardship_setup_mail_guard_v1()"
BACKWARD = (
    "CREATE OR REPLACE FUNCTION public.stewardship_setup_mail_guard_v1()"
    + _prior.FORWARD.split(_start, 1)[1].split("CREATE TRIGGER", 1)[0]
)
_before = (
    "           OR public.stewardship_setup_mail_live_v1("
    "NEW.attempt_id,NEW.attempt_version,\n"
    "                NEW.credential_id,NEW.credential_version,NEW.fingerprint) THEN"
)
_after = """           OR (public.stewardship_setup_mail_live_v1(
                NEW.attempt_id,NEW.attempt_version,
                NEW.credential_id,NEW.credential_version,NEW.fingerprint)
               AND NOT EXISTS (SELECT 1 FROM public.stewardship_task_run task
                    WHERE task.id=NEW.task_id
                        AND task.state IN ('failed','cancelled'))) THEN"""
if BACKWARD.count(_before) != 1:
    raise RuntimeError("Frozen setup mail cancellation admission is unavailable.")
FORWARD = BACKWARD.replace(_before, _after)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0078_setup_mail_exchange_guards")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
