"""The campaign archive export's database read (#1018 part (a), slice A1).

A financial campaign with four households' invented live pledges on day 1
(the comparison report's scenario) and its ready historical fact set. The
read-back runs the import's reader in this process, as the comparison
tests do: the isolated helper runs the installed package, which a local
worktree may not be (the archive tests in CI cover the helper itself).
"""

# ruff: noqa: F811 -- the imported scenario fixture is injected by pytest name.

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.archive import check
from parishkit.stewardship.archive.export import export_archive, read_source
from parishkit.stewardship.archive.workbook import read_archive
from parishkit.stewardship.reports.facts import FactUnavailable

from ..test_financial_answers import CHECK
from .campaign_builders import campaign_clock
from .test_campaign_comparison_postgresql import (  # noqa: F401
    PLEDGES,
    first_day,
    in_process,
    scenario,
)
from .test_financial_report_postgresql import family_session, pledge
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)
CONVERTED = datetime(2054, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reader_here(monkeypatch):
    """Read written files back in this process (see the module docstring)."""
    monkeypatch.setattr(check, "check_archive", in_process)


def export(ready, **changes):
    """The day-1 partial archive of the scenario's ready fact set."""
    return export_archive(
        ready.pk,
        cutoff_date=changes.pop("cutoff_date", first_day(ready).local_date),
        final=changes.pop("final", False),
        converted_at=CONVERTED,
    )


def revise(harness, when, amount="999"):
    """Household 2 revises its pledge at ``when``, after the facts were built."""
    with campaign_clock(when), web_login():
        family = family_session(harness, 2)
        pledge(
            family,
            load_form(family),
            annual_pledge=amount,
            frequency="annual",
            shares={CHECK: ""},
        )


def test_the_export_reads_the_graphs_respondents_and_matches_its_facts(scenario):
    """The reader accepts the file; its figures are the fact row's."""
    harness, ready, *_ = scenario
    written = export(ready)
    archive = read_archive(written.data)
    fact = first_day(ready)
    assert {f["family_id"] for f in archive.families} == set(PLEDGES)
    assert {f["family_id"]: f["annual_pledge"] for f in archive.families} == {
        duid: Decimal(amount) for duid, amount in PLEDGES.items()
    }
    statuses = {f["family_id"]: f["pledge_status"] for f in archive.families}
    assert statuses[6] == "zero" and statuses[1] == "pledged"
    [day] = archive.daily
    assert (day["first_submissions"], day["cumulative_families"]) == (
        fact.first_responses,
        fact.cumulative_responses,
    )
    assert day["cumulative_annual_pledged"] == fact.pledge_total
    assert archive.metadata["active_families"] == fact.cohort_denominator
    assert archive.metadata["campaign_name"] == (
        harness.campaign.active_configuration.name
    )
    assert written.unchecked == {"population": 0, "pledge": 0}
    assert written.name.endswith("-partial.xlsx")


def test_a_version_the_facts_have_not_absorbed_makes_the_export_wait(scenario):
    """A version above the watermark, before the cutoff: not ready yet."""
    harness, ready, *_ = scenario
    opened = harness.campaign.active_configuration.starts_at
    revise(harness, opened + timedelta(hours=6))
    with pytest.raises(FactUnavailable, match="not yet absorbed"):
        export(ready)


def test_a_version_after_the_cutoff_is_left_out(scenario):
    """A later day's revision changes nothing in a file cut off before it."""
    harness, ready, *_ = scenario
    before = export(ready)
    opened = harness.campaign.active_configuration.starts_at
    revise(harness, opened + timedelta(days=2))
    assert export(ready).data == before.data


def test_the_read_runs_in_its_callers_transaction(scenario):
    """``read_source`` needs a transaction; ``export_archive`` owns its own."""
    _, ready, *_ = scenario
    with pytest.raises(RuntimeError):
        read_source(
            ready.pk,
            cutoff_date=first_day(ready).local_date,
            final=False,
            converted_at=CONVERTED,
        )
    with transaction.atomic(), pytest.raises(RuntimeError):
        export(ready)


def test_ministry_choices_carry_their_names_from_the_reviewed_snapshot(
    response_service, google
):
    """A Member's leave and a proposed Member's join, with the Ministry's name.

    The names come from the definer read's lookup in the version's reviewed
    source snapshot (the invented corpus's Choir, Ministry 4); slots number
    the existing Member first, then the proposed one.
    """
    from dataclasses import asdict

    from parishkit.stewardship.archive.export import archive_sheets
    from parishkit.stewardship.reports.materialization import materialize_fact_set
    from parishkit.stewardship.source.snapshot_models import SourceSnapshot

    from ..census_factory import member
    from ..test_financial_answers import OPTIONS
    from .response_builders import activate_response_service
    from .test_fact_materialization_postgresql import allocation
    from .test_financial_source_postgresql import financial_source
    from .test_ministry_responses_postgresql import respond
    from .test_response_http_postgresql import answers_for
    from .test_source_snapshots_postgresql import permit

    harness = response_service
    financial_source(
        harness,
        modules=["census", "ministry", "financial"],
        options=map(asdict, OPTIONS),
        selected=(4,),
    )
    harness = activate_response_service(harness)
    opened = harness.campaign.active_configuration.starts_at
    proposed = str(uuid4())
    with campaign_clock(opened + timedelta(hours=1)), web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
        answers["proposed_members"][proposed] = member()
        answers["ministries"]["proposed_members"][proposed] = {"join": [4]}
        answers["financial"] = {
            "annual_pledge": "100",
            "frequency": "annual",
            "shares": {CHECK: ""},
        }
        respond(harness, form, answers)
    newest = SourceSnapshot.objects.filter(state="promoted").latest("generation")
    record, claim = allocation(harness, watermark=1, source=newest)
    ready = materialize_fact_set(record.pk, claim, admit=permit, interactive=True)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        source = read_source(
            ready.pk,
            cutoff_date=first_day(ready).local_date,
            final=False,
            converted_at=CONVERTED,
        )
    [version] = source.versions
    assert version.ministry_names == {"4": "Choir"}
    rows = archive_sheets(source)[3]
    assert [
        (
            r["member_slot"],
            r["member_id"],
            r["ministry"],
            r["ministry_code"],
            r["response"],
        )
        for r in rows
    ] == [(1, 3, "4", None, "leave"), (2, None, "4", None, "join")]
