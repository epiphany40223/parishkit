"""Forward migration: an Administrator grant names a standing match (#2363).

Its SQL is the frozen file ``schema/migrations/0204_grant_match.sql``
(prefix 0204 in the repository-wide file sequence; renumbered at train time
if the sequence moved), read whole and never parsed. It replaces the
``policy_security_event_account`` check so an ``administrator_granted``
event may carry ``account_email``, creates the definer trigger
``stewardship_grant_match_v1`` that sets it from the standing
verified-address match of the grant's address, and replaces the security
alert content compiler with the new row. It ends with a DO block that
refuses to commit unless all of it is installed as declared. The state
operations record the check's new condition, so ``makemigrations --check``
stays clean. Reversing needs its own forward migration.
"""

from pathlib import Path

import django.db.models.functions.text
from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0204_grant_match.sql"
)


def _trimmed_lower(field):
    """The model's check that an address column is trimmed lower case."""
    return models.Q(
        (
            field,
            django.db.models.functions.text.Lower(
                django.db.models.functions.text.Trim(field)
            ),
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration, which installs 0203 (the
        # frozen file just before this one's 0204).
        ("stewardship_accounts", "0042_address_check_context"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="policysecurityevent",
                    name="policy_security_event_account",
                ),
                migrations.AddConstraint(
                    model_name="policysecurityevent",
                    constraint=models.CheckConstraint(
                        condition=(
                            models.Q(
                                kind="address_matched",
                                address_match__isnull=False,
                                account_email__isnull=False,
                            )
                            & _trimmed_lower("account_email")
                        )
                        | (
                            models.Q(
                                kind="administrator_granted",
                                address_match__isnull=True,
                                replaced_account_email__isnull=True,
                            )
                            & (
                                models.Q(account_email__isnull=True)
                                | _trimmed_lower("account_email")
                            )
                        )
                        | (
                            ~models.Q(kind="address_matched")
                            & ~models.Q(kind="administrator_granted")
                            & models.Q(
                                address_match__isnull=True,
                                account_email__isnull=True,
                                replaced_account_email__isnull=True,
                            )
                        ),
                        name="policy_security_event_account",
                    ),
                ),
            ],
        ),
    ]
