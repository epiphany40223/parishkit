"""Forward migration: Ministry requests adopt the review pipeline (#1684).

Its SQL is the frozen file ``schema/migrations/0131_ministry_review_pipeline.sql``
(number 0131, provisional until it is assigned in merge order), read whole
and never parsed. It adds a request's Edit Ministry, the parish-added mark
and the request an addition replaces, and a revision's Edit Ministry and
decision note, and widens the revision outcome check to admit withdrawing
an added request; it replaces the request, revision and submission guards
and the follow-up selection so Edit, Back to review, Add and "Family asked
again" are admitted and a request is in the scope of its edited Ministry's
leaders. It ends with a DO block that refuses to commit unless all of it is
installed. The state operations record the same fields and constraints in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

import django.db.models.deletion
from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0131_ministry_review_pipeline.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_workflows", "0002_roster_entries"),
        # The migration that installs the previous frozen file (0130, the
        # census address guard), so the files apply in prefix order. The
        # follow-up selection 0094 replaced last comes before it.
        ("stewardship_responses", "0016_census_address_guard"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="ministryworkflowrevision",
                    name="ministry_revision_outcome",
                ),
                migrations.AddField(
                    model_name="ministryrequest",
                    name="added_in_place_of",
                    field=models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="stewardship_workflows.ministryrequest",
                    ),
                ),
                migrations.AddField(
                    model_name="ministryrequest",
                    name="parish_added",
                    field=models.BooleanField(db_default=False, default=False),
                ),
                migrations.AddField(
                    model_name="ministryrequest",
                    name="reviewed_ministry_duid",
                    field=models.IntegerField(null=True),
                ),
                migrations.AddField(
                    model_name="ministryworkflowrevision",
                    name="decision_note",
                    field=models.TextField(blank=True, db_default="", default=""),
                ),
                migrations.AddField(
                    model_name="ministryworkflowrevision",
                    name="reviewed_ministry_duid",
                    field=models.IntegerField(null=True),
                ),
                migrations.AddConstraint(
                    model_name="ministryrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("reviewed_ministry_duid__isnull", True),
                            models.Q(
                                ("outcome__in", ["joined", "leave_confirmed"]),
                                ("reviewed_ministry_duid__gt", 0),
                                ("state", "resolved"),
                                models.Q(
                                    (
                                        "reviewed_ministry_duid",
                                        models.F("ministry_duid"),
                                    ),
                                    _negated=True,
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="ministry_request_reviewed",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="ministryrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                models.Q(("parish_added", True), _negated=True),
                                ("entity_kind", "member"),
                                _connector="OR",
                            ),
                            models.Q(
                                ("added_in_place_of__isnull", True),
                                models.Q(
                                    ("parish_added", True),
                                    models.Q(
                                        ("added_in_place_of", models.F("id")),
                                        _negated=True,
                                    ),
                                ),
                                _connector="OR",
                            ),
                        ),
                        name="ministry_request_added",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="ministryworkflowrevision",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                models.Q(
                                    ("outcome__isnull", True),
                                    (
                                        "state__in",
                                        ["new", "assigned", "in_progress", "cancelled"],
                                    ),
                                ),
                                models.Q(
                                    (
                                        "outcome__in",
                                        [
                                            "joined",
                                            "leave_confirmed",
                                            "declined",
                                            "duplicate",
                                            "other",
                                        ],
                                    ),
                                    ("state", "resolved"),
                                ),
                                models.Q(
                                    ("outcome", "no_response"),
                                    ("state", "closed_no_response"),
                                ),
                                _connector="OR",
                            ),
                            models.Q(
                                models.Q(("outcome", "other"), _negated=True),
                                models.Q(("notes", ""), _negated=True),
                                _connector="OR",
                            ),
                        ),
                        name="ministry_revision_outcome",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="ministryworkflowrevision",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("reviewed_ministry_duid__isnull", True),
                            models.Q(
                                ("outcome__in", ["joined", "leave_confirmed"]),
                                ("reviewed_ministry_duid__gt", 0),
                                ("state", "resolved"),
                            ),
                            _connector="OR",
                        ),
                        name="ministry_revision_reviewed",
                    ),
                ),
            ],
        )
    ]
