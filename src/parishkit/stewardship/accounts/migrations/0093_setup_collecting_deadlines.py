"""The two-hour watchdog owns loading, not a healthy collecting wizard."""

from django.db import migrations


def rewrite(editor, *, reverse=False):
    """Retain original-task provenance while removing only the loading deadline."""
    after = (
        "JOIN public.stewardship_task_run original "
        "ON original.id=attempt.source_task_id"
    )
    before = after + (
        "\n            AND original.created_at>clock_timestamp()-interval '2 hours'"
    )
    if reverse:
        before, after = after, before
    for target in ("mail", "slack"):
        signature = (
            f"public.stewardship_setup_{target}_live_v1(uuid,bigint,uuid,bigint,text)"
        )
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
            definition = cursor.fetchone()[0]
        if definition.count(before) != 1:
            raise RuntimeError("Collecting deadline predecessor differs.")
        editor.execute(definition.replace(before, after), params=None)


def forward(apps, editor):
    """Keep the original login's idle and absolute expiry checks authoritative."""
    rewrite(editor)


def backward(apps, editor):
    """Restore the older watchdog predicate without altering retained records."""
    rewrite(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0092_scoped_branding_requests")]
    operations = [migrations.RunPython(forward, backward)]
