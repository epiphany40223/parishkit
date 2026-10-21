"""Forward migration: Sent mail's recipient filter and names (#1562).

Its SQL is the frozen file ``schema/migrations/0116_sent_mail_recipients.sql``
(prefix 0116, assigned centrally in the repository-wide file sequence), read
whole and never parsed. It adds the GIN index ``outbox_render_intended_lower``
(operator class ``jsonb_path_ops``) over the renderings' lower-cased
intended recipients and two
Administrator-only definer reads for Sent mail:
``stewardship_delivery_ids_for_address_v1`` (the emails meant for one
address) and ``stewardship_delivery_page_recipients_v1`` (the addresses of a
page's Administrator reports and notices). It ends with a DO block that
refuses to commit unless all three are installed as declared. The state
operation records the index on the model, so ``makemigrations --check``
stays clean. Reversing needs its own forward migration.

It follows the users swap (#952), so its definers are granted only once it
is applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

import django.db.models.functions.comparison
import django.db.models.functions.text
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0116_sent_mail_recipients.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration, which also installs the
        # previous frozen file (0115, Clear the halt, #1555, which merges
        # just before this one), so the app keeps one leaf and the files
        # apply in prefix order.
        ("stewardship_jobs", "0033_mail_halt_clear"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="outboxrender",
                    index=GinIndex(
                        OpClass(
                            django.db.models.functions.comparison.Cast(
                                django.db.models.functions.text.Lower(
                                    django.db.models.functions.comparison.Cast(
                                        "intended_recipients",
                                        output_field=models.TextField(),
                                    )
                                ),
                                output_field=models.JSONField(),
                            ),
                            name="jsonb_path_ops",
                        ),
                        name="outbox_render_intended_lower",
                    ),
                ),
            ],
        )
    ]
