"""Preserve loaded timezone interpretation and cancellation audit outcome."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION public.stewardship_setup_timezone_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    -- Scrubbing a terminal attempt still erases the whole public profile.
    IF NEW.step='parish' AND NEW.scrubbed_at IS NULL
       AND NEW.values->>'timezone' IS DISTINCT FROM OLD.values->>'timezone'
       AND EXISTS (SELECT 1 FROM public.stewardship_setup_attempt
                   WHERE id=NEW.attempt_id AND source_task_id IS NOT NULL) THEN
        RAISE EXCEPTION 'The original source load fixes the setup timezone'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_timezone_guard
BEFORE UPDATE ON public.stewardship_setup_draft_section
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_timezone_guard_v1();
"""

BACKWARD = """
DROP TRIGGER stewardship_setup_timezone_guard ON public.stewardship_setup_draft_section;
DROP FUNCTION public.stewardship_setup_timezone_guard_v1();
"""


def rewrite(editor, *, reverse=False):
    """Keep scrub provenance while distinguishing a never-submitted cancellation."""
    before = """WHEN event='setup_mail_scrubbed' THEN 'changed'
            WHEN NEW.state='accepted' THEN 'succeeded'
            WHEN NEW.state='cancelled' THEN 'cancelled'"""
    after = """WHEN NEW.state='cancelled' THEN 'cancelled'
            WHEN event='setup_mail_scrubbed' THEN 'changed'
            WHEN NEW.state='accepted' THEN 'succeeded'"""
    if reverse:
        before, after = after, before
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_setup_mail_audit_v1()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
    if definition.count(before) != 1:
        raise RuntimeError("Setup mail audit predecessor differs.")
    editor.execute(definition.replace(before, after), params=None)


def forward(apps, editor):
    """Cancellation is the outcome even when the same write erases sample content."""
    rewrite(editor)


def backward(apps, editor):
    """Restore only future audit behavior; retained events are never rewritten."""
    rewrite(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0097_setup_scope_evidence")]
    operations = [
        migrations.RunSQL(FORWARD, BACKWARD),
        migrations.RunPython(forward, backward),
    ]
