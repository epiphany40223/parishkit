"""Narrow session reads and expire-only transitions for the metadata scheduler."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.accounts.migrations.0047_setup_attempt_guards"
)
_function = _previous.FORWARD.split(
    "CREATE TRIGGER stewardship_setup_attempt_admission", 1
)[0]
_function = _function.replace(
    "CREATE FUNCTION stewardship_setup_attempt_guard_v1()",
    "CREATE OR REPLACE FUNCTION stewardship_setup_attempt_guard_v1()",
)
_read = "SELECT s.* INTO login FROM stewardship_portal_session s"
_bounded = """SELECT s.id,s.principal_id,s.revoked_at,s.expires_at,s.last_activity_at
        INTO login.id,login.principal_id,login.revoked_at,
             login.expires_at,login.last_activity_at
        FROM stewardship_portal_session s"""
_entry = "BEGIN\n    IF TG_OP='DELETE' THEN"
_guard = """BEGIN
    IF current_user='pk_stewardship_scheduler' AND
       (TG_OP<>'UPDATE' OR NEW.state<>'expired' OR NEW.actor_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Scheduler may only expire setup metadata'
            USING ERRCODE='23514';
    END IF;
    IF TG_OP='DELETE' THEN"""
if _function.count(_read) != 1 or _function.count(_entry) != 1:
    raise RuntimeError("Frozen setup expiry guard is unavailable.")


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0057_integration_selection_schema")]
    operations = [
        migrations.RunSQL(
            _function.replace(_read, _bounded).replace(_entry, _guard), _function
        )
    ]
