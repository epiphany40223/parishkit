"""Report Family status is the dashboard's eligibility (#2335, frozen 0197).

The ``active`` column of the Financial selection (which carries "In this
campaign today", the ParishSoft status badge and the "No longer in this
campaign" refinement) and of both Additional information selections (their
status badge) is each row's ``portal_eligible`` under every Families rule,
so they agree with the Response dashboard under the legacy rule too (reports
spec, "Financial stewardship detail" and "Additional information"). The
agreement itself is driven through the pages in
test_report_agreement_postgresql.py; these tests run the frozen file's own
closing check against the bodies it replaces (0194's, the OLD definitions)
and the bodies it installs.
"""

import json
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.responses.models import AdditionalInformationItem
from parishkit.stewardship.source.models import SourceCurrent

from ..sql_operators import bare_operators
from .test_financial_in_force_postgresql import Rollback, normalized
from .test_financial_report_postgresql import _rewrite_payload
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0197_financial_eligibility.sql"
# The file whose bodies 0197 copied (0194's, #2320), and the functions it
# replaces, each with the refusal its check raises for 0194's body.
PREVIOUS = MIGRATIONS / "0194_mixed_surname_names.sql"
NAMES = {
    "stewardship_financial_report_v1": "did not replace the Financial selection",
    "stewardship_information_report_v1": (
        "did not replace the Additional information selections"
    ),
    "stewardship_information_report_v2": (
        "did not replace the Additional information selections"
    ),
}
# The active column before and after, as normalized() reads them.
OLD_ACTIVE = (
    "CASE WHEN p.canonical::jsonb ? 'campaign_families' AND "
    "jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean' THEN "
    "(p.canonical::jsonb->'portal_eligible')::boolean WHEN "
    "jsonb_typeof(p.canonical::jsonb->'active')='boolean' THEN "
    "(p.canonical::jsonb->'active')::boolean END AS active,"
)
NEW_ACTIVE = (
    "CASE WHEN jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean' THEN "
    "(p.canonical::jsonb->'portal_eligible')::boolean END AS active,"
)


def statement(path, name):
    """``name``'s ``CREATE OR REPLACE FUNCTION`` statement in the file ``path``."""
    text = path.read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return text[start : text.index("END $$;", start) + len("END $$;")]


def check():
    """0197's closing check: the settings it records first, and its DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index("SELECT set_config(")
    recorded = text[start : text.index("\nCREATE OR REPLACE FUNCTION", start)]
    return recorded + text[text.index("DO $check$") :]


def test_the_check_passes_as_installed():
    """A fresh install has 0197's bodies, and each whole selection runs."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
        raise Rollback


@pytest.mark.parametrize("name", sorted(NAMES))
def test_the_check_refuses_each_previous_body(name):
    """Each selection put back to 0194's body (the OLD definition, with the
    legacy branch) fails the check, naming that selection."""
    with (
        pytest.raises(DatabaseError, match=rf"Migration 0197 .* {NAMES[name]}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement(PREVIOUS, name))
        cursor.execute(check())


@pytest.mark.parametrize("name", sorted(NAMES))
def test_the_check_refuses_changed_grants(name):
    """A grant changed between the recorded state and the check fails it."""
    text = check()
    recorded, block = text[: text.index("DO $check$")], text[text.index("DO $check$") :]
    with (
        pytest.raises(DatabaseError, match="changed the selections' grants"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(recorded)
        cursor.execute(
            "SELECT format('REVOKE EXECUTE ON FUNCTION %%s FROM PUBLIC',"
            " p.oid::regprocedure) FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'public' AND p.proname = %s",
            [name],
        )
        cursor.execute(cursor.fetchone()[0])
        cursor.execute(block)


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("stewardship_pledge_review", "reviewed_shares"),
        ("stewardship_information_revision", "followed_up_by_id"),
    ),
)
def test_the_check_runs_the_selections_so_a_missing_column_fails(table, column):
    """The check runs each whole selection, so a column the Financial or the
    Additional information selections read that is missing fails the file,
    not a later page."""
    with (
        pytest.raises(
            DatabaseError, match=rf"column \W?(\w+\.)?{column}\W? does not exist"
        ),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        cursor.execute(check())


@pytest.mark.parametrize("name", sorted(NAMES))
def test_each_new_body_is_the_previous_one_but_its_active_column(name):
    """Only the active column (and comments) changed, every operator still
    qualified (#2166), so a copy of a stale predecessor fails here."""
    new_text = statement(FROZEN, name)
    assert not bare_operators(new_text)
    old = normalized(statement(PREVIOUS, name))
    assert old.count(OLD_ACTIVE) == 1
    assert old.replace(OLD_ACTIVE, NEW_ACTIVE) == normalized(new_text)


def test_the_file_installs_its_bodies_over_the_previous_ones():
    """On a database as it was before 0197, the file installs the bodies and
    its check passes, keeping each function an invoker; rolled back."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        for name in NAMES:
            cursor.execute(statement(PREVIOUS, name))
        cursor.execute(FROZEN.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef AND proname = ANY(%s)"
            " AND prosrc NOT LIKE '%%campaign_families%%'"
            " AND prosrc LIKE '%%portal_eligible'')::boolean END AS active,%%'",
            [list(NAMES)],
        )
        assert cursor.fetchone() == (3,)
        raise Rollback


def test_additional_information_reads_eligibility_under_the_legacy_rule(
    live_response_service,
):
    """A legacy-rule Family active in ParishSoft but not eligible (registered
    at another parish) is no longer in this campaign on both Additional
    information selections, as on Financial stewardship and the dashboard;
    0194's bodies (the OLD definitions) still read it as in the campaign."""
    harness = live_response_service
    respond(harness, "Please call about the new choir")
    duid = AdditionalInformationItem.objects.get().submission.family.family_duid
    snapshot = SourceCurrent.objects.get().snapshot_id
    _rewrite_payload(
        "family",
        snapshot,
        duid,
        {
            "active": True,
            "parishioner": False,
            "email_eligible": False,
            "portal_eligible": False,
        },
    )
    legacy = InformationQuery(disposition="all", zone=None).form_values()
    zoned = InformationQuery(disposition="all", zone="Etc/UTC").form_values()

    def statuses(cursor):
        """Each selection's one row's ``active``, v1 then v2."""
        cursor.execute(
            "SELECT stewardship_information_report_v1(%s,%s::jsonb),"
            "stewardship_information_report_v2(%s,%s::jsonb)",
            [
                harness.campaign.pk,
                json.dumps({"filters": legacy, "history": True}),
                harness.campaign.pk,
                json.dumps({"filters": zoned, "history": True}),
            ],
        )
        return [
            [row["active"] for row in json.loads(value)["rows"]]
            for value in cursor.fetchone()
        ]

    with connection.cursor() as cursor:
        assert statuses(cursor) == [[False], [False]]
        with pytest.raises(Rollback), transaction.atomic():
            for name in NAMES:
                cursor.execute(statement(PREVIOUS, name))
            assert statuses(cursor) == [[True], [True]]
            raise Rollback
