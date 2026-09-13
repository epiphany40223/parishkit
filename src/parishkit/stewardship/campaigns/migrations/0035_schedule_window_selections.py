"""A schedule revision's resolved civil cadence belongs to its campaign window."""

from django.db import migrations

OLD = """SELECT 1 FROM stewardship_schedule_revision WHERE id=previous.current_revision_id AND values=revision.values"""  # noqa: E501
NEW = """SELECT 1 FROM stewardship_schedule_revision old_revision
            JOIN stewardship_campaign_configuration old_campaign
              ON old_campaign.configuration_id=old_revision.configuration_id
             AND old_campaign.record_id=old_revision.campaign_id
            JOIN stewardship_campaign_configuration new_campaign
              ON new_campaign.configuration_id=revision.configuration_id
             AND new_campaign.record_id=revision.campaign_id
            WHERE old_revision.id=previous.current_revision_id
              AND old_revision.values=revision.values
              AND old_campaign.timezone=new_campaign.timezone"""


def change(editor, *, forward):
    """Preserve existing replacement/cancellation guards and alter only equality."""
    old, new = (OLD, NEW) if forward else (NEW, OLD)
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_schedules_activate_v1()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
    if definition.count(old) != 1:
        raise RuntimeError("Unexpected schedule-window migration predecessor.")
    editor.execute(definition.replace(old, new), params=None)


def forward(apps, editor):
    """New activation compares cadence as well as the schedule's own YAML values."""
    change(editor, forward=True)


def backward(apps, editor):
    """Do not restore the old equality rule over retained logical-schedule history."""
    editor.execute("""
LOCK TABLE public.stewardship_schedule_definition IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_schedule_definition) THEN
        RAISE EXCEPTION 'Schedule history prevents window-guard downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
    """)
    change(editor, forward=False)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0034_presence_guards"),
        ("stewardship_accounts", "0049_content_guards"),
    ]
    operations = [migrations.RunPython(forward, backward)]
