"""Forward migration: storage for what each Family form showed (#1705).

Its SQL is the frozen file ``schema/migrations/0153_reviewed_form_shown_items.sql``
(number 0153, provisional until the coordinator assigns it in merge order),
read whole and never parsed. It adds ``reviewed_view`` (false by default)
and ``shown_items`` (NULL by default) to ``stewardship_family_form_baseline``
and ``stewardship_submission``, each with the check that pairs them, and
ends with a DO block that refuses to commit unless they are installed as
described and every existing row reads as drawn with the setting off.

It follows the users swap (through the previous frozen file's migration),
so it is in the swap's closure: `migrate --users-upgrade-base` never
applies it. It creates no table and no routine, and the web login's
table-level grants on both tables already cover the new columns, so
nothing is registered in ``runtime_grants``. The state operations record
the columns and checks in Django's model state, so ``makemigrations
--check`` stays clean. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models
from django.db.models import F, Func, Value
from django.db.models.lookups import Exact

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0153_reviewed_form_shown_items.sql"
)


def _columns(model_name):
    """The two state-only fields one table gains."""
    return [
        migrations.AddField(
            model_name=model_name,
            name="reviewed_view",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name=model_name,
            name="shown_items",
            field=models.JSONField(null=True),
        ),
    ]


def _shape(name):
    """The check pairing the two fields, as the models declare it (a copy, so
    this migration's state never follows a later model change)."""
    return models.CheckConstraint(
        condition=models.Q(reviewed_view=False, shown_items__isnull=True)
        | (
            models.Q(reviewed_view=True, shown_items__isnull=False)
            & Exact(
                Func(
                    F("shown_items"),
                    function="jsonb_typeof",
                    output_field=models.TextField(),
                ),
                Value("array"),
            )
        ),
        name=name,
    )


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf.
        ("stewardship_responses", "0012_gender_by_hand"),
        # The migration that installs the previous frozen file (0121,
        # #1658), so the files apply in prefix order.
        ("stewardship_campaigns", "0014_digest_skip_dates"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                *_columns("familyformbaseline"),
                migrations.AddConstraint(
                    model_name="familyformbaseline",
                    constraint=_shape("family_baseline_shown_shape"),
                ),
                *_columns("submission"),
                migrations.AddConstraint(
                    model_name="submission",
                    constraint=_shape("submission_shown_shape"),
                ),
            ],
        )
    ]
