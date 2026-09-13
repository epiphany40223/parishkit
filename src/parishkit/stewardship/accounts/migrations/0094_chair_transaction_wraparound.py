"""Compare xmin's 32-bit transaction component without overflowing xid text."""

from django.db import migrations


def rewrite(editor, *, reverse=False):
    """Keep same-transaction activation proof valid across a transaction epoch."""
    before = "xmin=(pg_current_xact_id()::text)::xid"
    after = "xmin::text::numeric=mod(pg_current_xact_id()::text::numeric,4294967296)"
    if reverse:
        before, after = after, before
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_chair_reconciliation_guard_v1()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
    if definition.count(before) != 1:
        raise RuntimeError("Chair transaction predecessor differs.")
    editor.execute(definition.replace(before, after), params=None)


def forward(apps, editor):
    """Install the already-established epoch-safe xmin comparison."""
    rewrite(editor)


def backward(apps, editor):
    """Restore only the comparison when the migration is explicitly reversed."""
    rewrite(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0093_setup_collecting_deadlines")]
    operations = [migrations.RunPython(forward, backward)]
