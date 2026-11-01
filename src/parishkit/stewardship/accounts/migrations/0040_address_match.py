"""Forward migration: verified-address sign-in's schema (#2343, slice 1).

Its SQL is the frozen file ``schema/migrations/0200_address_match.sql``
(prefix 0200 in the repository-wide file sequence; renumbered at train time
if the sequence moved), read whole and never parsed. It adds
``stewardship_portal_user.sign_in_email`` (set to ``email`` on every row),
creates ``stewardship_address_match`` with its guards and the "address in
use" definer, lets a security event come from a match instead of an
activation, and replaces the portal user insert and update guards and the
security alert content compiler. It ends with a DO block that refuses to
commit unless all of it is installed as declared. The state operations
record the same model changes, so ``makemigrations --check`` stays clean.
Reversing needs its own forward migration.

It follows the users swap (#952), so its granted table and definer are
granted only once it is applied (runtime_grants.CLOSURE_TABLES and
CLOSURE_FUNCTIONS).
"""

import uuid
from pathlib import Path

import django.db.models.deletion
import django.db.models.functions.datetime
import django.db.models.functions.text
import django.db.models.lookups
from django.db import migrations, models

import parishkit.stewardship.observability
import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0200_address_match.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration, so the app keeps one leaf.
        ("stewardship_accounts", "0039_setup_backup_step"),
        # The migration that installs the previous frozen file (0193 on
        # main; 0194 to 0199 are other open pull requests, and the train
        # renumbers this file and repoints this dependency).
        ("stewardship_jobs", "0041_backup_uploader_preparation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="policysecurityevent",
                    name="account_email",
                    field=models.EmailField(max_length=254, null=True),
                ),
                migrations.AddField(
                    model_name="policysecurityevent",
                    name="replaced_account_email",
                    field=models.EmailField(max_length=254, null=True),
                ),
                migrations.AddField(
                    model_name="policysecurityevent",
                    name="replaced_login_at",
                    field=parishkit.stewardship.storage.UTCDateTimeField(null=True),
                ),
                migrations.AddField(
                    model_name="portaluser",
                    name="sign_in_email",
                    field=models.EmailField(max_length=254),
                ),
                migrations.AlterField(
                    model_name="policysecurityevent",
                    name="activation",
                    field=models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="stewardship_accounts.configurationactivation",
                    ),
                ),
                migrations.CreateModel(
                    name="AddressMatch",
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
                            "created_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "actor_id",
                            models.UUIDField(blank=True, editable=False, null=True),
                        ),
                        (
                            "correlation_id",
                            models.UUIDField(
                                db_index=True,
                                default=parishkit.stewardship.observability.current_correlation,
                                editable=False,
                            ),
                        ),
                        (
                            "updated_at",
                            parishkit.stewardship.storage.UTCDateTimeField(
                                db_default=django.db.models.functions.datetime.Now(),
                                editable=False,
                            ),
                        ),
                        (
                            "version",
                            models.PositiveBigIntegerField(default=1, editable=False),
                        ),
                        ("email", models.EmailField(max_length=254)),
                        ("user_record_id", models.UUIDField()),
                        ("sign_in_email", models.EmailField(max_length=254)),
                        (
                            "matched_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "confirmed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        (
                            "ended_at",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        ("end_reason", models.CharField(max_length=32, null=True)),
                        ("ended_by_id", models.UUIDField(null=True)),
                        ("ended_session_id", models.UUIDField(null=True)),
                        (
                            "portal_user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="stewardship_accounts.portaluser",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_address_match",
                        "abstract": False,
                    },
                ),
                migrations.AddField(
                    model_name="policysecurityevent",
                    name="address_match",
                    field=models.OneToOneField(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="stewardship_accounts.addressmatch",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="policysecurityevent",
                    constraint=models.CheckConstraint(
                        condition=django.db.models.lookups.Exact(
                            models.Func(
                                models.F("activation"),
                                models.F("address_match"),
                                function="num_nonnulls",
                                output_field=models.IntegerField(),
                            ),
                            1,
                        ),
                        name="policy_security_event_source",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="policysecurityevent",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("account_email__isnull", False),
                                ("address_match__isnull", False),
                                ("kind", "address_matched"),
                                (
                                    "account_email",
                                    django.db.models.functions.text.Lower(
                                        django.db.models.functions.text.Trim(
                                            "account_email"
                                        )
                                    ),
                                ),
                            ),
                            models.Q(
                                models.Q(("kind", "address_matched"), _negated=True),
                                ("account_email__isnull", True),
                                ("address_match__isnull", True),
                                ("replaced_account_email__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="policy_security_event_account",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="policysecurityevent",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("replaced_account_email__isnull", True),
                                ("replaced_login_at__isnull", True),
                            ),
                            models.Q(
                                ("replaced_account_email__isnull", False),
                                (
                                    "replaced_account_email",
                                    django.db.models.functions.text.Lower(
                                        django.db.models.functions.text.Trim(
                                            "replaced_account_email"
                                        )
                                    ),
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="policy_security_event_replaced",
                    ),
                ),
                migrations.AddIndex(
                    model_name="addressmatch",
                    index=models.Index(
                        fields=["email", "matched_at"], name="address_match_email_time"
                    ),
                ),
                migrations.AddConstraint(
                    model_name="addressmatch",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="stewardship_accounts_addressmatch_positive_version",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="addressmatch",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("ended_at__isnull", True)),
                        fields=("portal_user",),
                        name="address_match_one_per_account",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="addressmatch",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("ended_at__isnull", True)),
                        fields=("email",),
                        name="address_match_one_per_address",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="addressmatch",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "email",
                                django.db.models.functions.text.Lower(
                                    django.db.models.functions.text.Trim("email")
                                ),
                            ),
                            (
                                "sign_in_email",
                                django.db.models.functions.text.Lower(
                                    django.db.models.functions.text.Trim(
                                        "sign_in_email"
                                    )
                                ),
                            ),
                        ),
                        name="address_match_normalized",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="addressmatch",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("email", models.F("sign_in_email")), _negated=True
                        ),
                        name="address_match_addresses_differ",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="addressmatch",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("end_reason__isnull", True),
                                ("ended_at__isnull", True),
                                ("ended_by_id__isnull", True),
                                ("ended_session_id__isnull", True),
                            ),
                            models.Q(
                                ("end_reason", "ended_by_administrator"),
                                ("end_reason__isnull", False),
                                ("ended_at__isnull", False),
                                ("ended_by_id__isnull", False),
                                ("ended_session_id__isnull", False),
                            ),
                            models.Q(
                                (
                                    "end_reason__in",
                                    (
                                        "sign_in_address_matches",
                                        "not_confirmed",
                                        "user_gone",
                                        "replaced",
                                    ),
                                ),
                                ("end_reason__isnull", False),
                                ("ended_at__isnull", False),
                                ("ended_by_id__isnull", True),
                                ("ended_session_id__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="address_match_end",
                    ),
                ),
            ],
        ),
    ]
