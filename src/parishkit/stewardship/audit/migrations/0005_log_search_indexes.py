"""Forward migration: indexes for the System logs text search (#765).

Its SQL is the frozen file ``schema/migrations/0116_log_search_indexes.sql``
(number 0116, assigned centrally), read whole and never parsed. It notices
the ``pg_trgm`` extension the operator installed (or, on a superuser test
database, installs it) and creates six indexes: a trigram GIN on the text of
every context value of the operational log and of audit contexts, a trigram
GIN on each log's stored type, and a pattern B-tree on each type, so every
arm of the search's OR can use an index (``audit.log_search``). It ends
with a DO block that refuses to commit unless the extension and every index
are installed as declared. The state operations record the same indexes on
the models, so ``makemigrations --check`` stays clean. Reversing needs its
own forward migration, so, like the baseline, it has no reverse operation.

It lives in the audit app, which owns both logs, and depends on the
migration that installs the previous frozen file, so the files apply in
prefix order. Every statement is safe to repeat, so the users swap test,
which may run it again, needs no registration for it.
"""

from pathlib import Path

import django.contrib.postgres.indexes
import django.db.models.functions.comparison
import django.db.models.functions.text
from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0116_log_search_indexes.sql"
)


def value_text_index(name):
    """The trigram GIN on the text of every context value, as frozen here."""
    return django.contrib.postgres.indexes.GinIndex(
        django.contrib.postgres.indexes.OpClass(
            django.db.models.functions.text.Upper(
                django.db.models.functions.comparison.Cast(
                    models.Func(
                        "context",
                        models.Value("$.*"),
                        function="jsonb_path_query_array",
                        output_field=models.JSONField(),
                    ),
                    models.TextField(),
                )
            ),
            name="gin_trgm_ops",
        ),
        name=name,
    )


def type_indexes(model_name, column, prefix):
    """The trigram GIN and pattern B-tree on one log's type, as frozen here."""
    return [
        migrations.AddIndex(
            model_name=model_name,
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.indexes.OpClass(
                    django.db.models.functions.text.Upper(column), name="gin_trgm_ops"
                ),
                name=f"{prefix}_trgm",
            ),
        ),
        migrations.AddIndex(
            model_name=model_name,
            index=models.Index(
                fields=[column],
                name=f"{prefix}_pattern",
                opclasses=["varchar_pattern_ops"],
            ),
        ),
    ]


class Migration(migrations.Migration):
    dependencies = [
        # The audit app's previous migration (0106, #1403).
        ("stewardship_audit", "0004_admin_input_refusal_log"),
        # The migration that installs the previous frozen file (0110, #558),
        # so the files apply in prefix order.
        ("stewardship_reports", "0030_financial_local_days"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="auditcontext",
                    index=value_text_index("audit_context_value_trgm"),
                ),
                *type_indexes("auditevent", "event_type", "audit_event_type"),
                migrations.AddIndex(
                    model_name="operationallog",
                    index=value_text_index("operational_value_trgm"),
                ),
                *type_indexes("operationallog", "event", "operational_event"),
            ],
        )
    ]
