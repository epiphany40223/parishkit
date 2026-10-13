"""Forward migration: the debug-off switch, server side (ADM-13 PR 6a, #530).

Its SQL is the frozen file ``schema/migrations/0078_debug_off_switch.sql``
(prefix 0078 in the repository-wide file sequence), read whole and never
parsed. It creates ``stewardship_debug_off_switch`` with its check, index and
guard trigger (a switch is set only by the web login for a freshly signed-in
Administrator, one at a time, cleared once and never deleted), the
``stewardship_debug_logging_off_v1()`` read every runtime login uses, the
admin-recovery-only ``stewardship_debug_off_clear_v1()``, and adds
``debug_logging_switched_off`` to the operational event check. It ends with a
DO block that refuses to commit unless all of it is installed as declared.
The state operation records the model in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration.

It follows the users swap (#952), so its two functions are granted only once
it is applied (runtime_grants.CLOSURE_FUNCTIONS). No login has a grant on the
table yet.
"""

import uuid
from pathlib import Path

import django.db.models.functions.datetime
from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0078_debug_off_switch.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration. Frozen files 0078 to 0080 are
        # still in review; once they land, this also depends on the migration
        # that installs 0080, so the files apply in prefix order. It follows
        # the users swap, so `migrate --users-upgrade-base` never applies this
        # file and its functions are registered in
        # runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_jobs", "0027_address_retry"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="DebugOffSwitch",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        (
                            "set_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now()
                            ),
                        ),
                        ("actor_id", models.UUIDField()),
                        ("session_id", models.UUIDField()),
                        (
                            "authenticated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "cleared_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        ("cleared_by", models.CharField(max_length=16, null=True)),
                        ("cleared_actor_id", models.UUIDField(null=True)),
                    ],
                    options={
                        "db_table": "stewardship_debug_off_switch",
                        "indexes": [
                            models.Index(
                                models.OrderBy(models.F("set_at"), descending=True),
                                name="debug_off_switch_newest",
                            )
                        ],
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(
                                    cleared_actor_id__isnull=True,
                                    cleared_at__isnull=True,
                                    cleared_by__isnull=True,
                                )
                                | models.Q(
                                    cleared_actor_id__isnull=True,
                                    cleared_at__isnull=False,
                                    cleared_by="operator",
                                )
                                | models.Q(
                                    cleared_actor_id__isnull=False,
                                    cleared_at__isnull=False,
                                    cleared_by="administrator",
                                ),
                                name="debug_off_switch_cleared",
                            ),
                        ],
                    },
                ),
            ],
        ),
    ]
