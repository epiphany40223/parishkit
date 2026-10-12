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

import pytest
from django.db import transaction

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


def test_the_read_needs_its_own_transaction(scenario):
    """The read-only, repeatable-read transaction must be the outermost one."""
    _, ready, *_ = scenario
    with transaction.atomic(), pytest.raises(RuntimeError):
        read_source(
            ready.pk,
            cutoff_date=first_day(ready).local_date,
            final=False,
            converted_at=CONVERTED,
        )
