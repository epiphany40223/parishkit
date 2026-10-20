"""Forward migration: Back to review undoes a parish-added change's mark (#1840).

Its SQL is the frozen file ``schema/migrations/0211_census_addition_undo.sql``
(number 0211, provisional until the coordinator assigns it in merge order),
read whole and never parsed. A census addition may now have more than one
later step: its resolution's UNIQUE ``addition_id`` goes, a ``sequence``
numbers each addition's steps (unique per addition), the action
``reopened`` is admitted, and the guards check which step may follow which
(an entered addition is reopened only with a note) and that an open
addition of the same field is one whose latest step is none or reopened.
It ends with a DO block that refuses to commit unless all of that is
installed.

It follows the users swap (through the previous frozen file's migration),
so it is in the swap's closure: `migrate --users-upgrade-base` never
applies it. It creates no table and no routine, and the web login's
table-level grants already cover the new column, so nothing is registered
in ``runtime_grants``; the swap test runs the file again after 0129's
tables are made again (``CLOSURE_REPLACED``). The state operations record
the field change and constraints in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

import django.db.models.deletion
from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0211_census_addition_undo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, which also installs the previous
        # frozen file (0210, #1840), so the app keeps one leaf and the
        # files apply in prefix order.
        ("stewardship_responses", "0023_census_terminal_undo"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="censusadditionresolution",
                    name="addition",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="resolutions",
                        to="stewardship_responses.censusaddition",
                    ),
                ),
                migrations.AddField(
                    model_name="censusadditionresolution",
                    name="sequence",
                    field=models.IntegerField(default=1),
                    preserve_default=False,
                ),
                migrations.RemoveConstraint(
                    model_name="censusadditionresolution",
                    name="census_addition_resolution_identity",
                ),
                migrations.AddConstraint(
                    model_name="censusadditionresolution",
                    constraint=models.UniqueConstraint(
                        fields=("addition", "sequence"),
                        name="census_addition_resolution_sequence",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="censusadditionresolution",
                    constraint=models.CheckConstraint(
                        condition=models.Q(actor_id__isnull=False)
                        & models.Q(action__in=("entered", "withdrawn", "reopened"))
                        & models.Q(sequence__gte=1),
                        name="census_addition_resolution_identity",
                    ),
                ),
            ],
        )
    ]
