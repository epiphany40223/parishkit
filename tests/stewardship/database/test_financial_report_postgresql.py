"""Financial stewardship detail under the real web role, with exact money."""

import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.financial import (
    FinancialQuery,
    financial_page,
    giving_proven,
)

from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_financial_source_postgresql import financial_source
from .test_ministry_responses_postgresql import respond, revisit
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())


def pledge(harness, form, **financial):
    """Submit one complete live response carrying this financial answer."""
    answers = answers_for(form)
    answers["financial"] = (
        dict(annual_pledge="1,234.5", frequency="monthly", shares={}) | financial
    )
    return respond(harness, form, answers)


def report(harness, *, principal=STAFF, giving=None, **values):
    """Read through actual restricted SQL grants, not the migration owner."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )
    configuration = campaign.active_configuration.values
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        if giving is None:
            from parishkit.stewardship.source.snapshot_models import SourceCurrent

            giving = giving_proven(
                campaign.pk, configuration, SourceCurrent.objects.get().snapshot_id
            )
        return financial_page(
            campaign.pk,
            FinancialQuery.parse(values),
            principal,
            giving=giving,
            parish_name="Sample Parish",
            configuration=configuration,
        )


def test_effective_response_exact_money_filters_and_privacy(response_service):
    """One row per effective live response; decoy source money never appears."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        first = pledge(
            harness, load_form(harness), shares={CHECK: "", OTHER: "Stock gift"}
        )
    page = report(harness)
    assert page["total"] == 1 and len(page["rows"]) == 1
    row = page["rows"][0]
    assert row["annual"].display == "$1,234.50"
    # 1,234.50 / 12 is 102.875: the exact helper rounds half up, no float drift.
    assert row["installment"].display == "$102.88"
    assert row["frequency_label"] == "Monthly" and row["family_version"] == 1
    assert [share["text"] for share in row["shares"]] == ["", "Stock gift"]
    assert all(share["label"] != "Unavailable share method" for share in row["shares"])
    # The harness source holds 1,200.00 pledged and 100.00 contributed for this
    # Family's mapped comparison funds, beside decoys for others.
    assert row["source_pledge"].display == "$1,200.00"
    assert row["source_contributions"].display == "$100.00"
    assert "9999" not in json.dumps(page, default=str)
    assert "8888" not in json.dumps(page, default=str)
    summary = page["summary"]
    assert summary["families"] == 1
    assert summary["annual_total"].display == "$1,234.50"
    assert dict(summary["frequencies"])["Monthly"] == 1
    assert summary["no_share"] == 0 and len(summary["shares"]) == 2
    assert page["metadata"]["giving_through"] is not None

    # Without the completeness proof, source money is unavailable, never zero.
    withheld = report(harness, giving=False)["rows"][0]
    assert not withheld["source_pledge"].available
    assert withheld["source_pledge"].display == "Unavailable"
    assert not withheld["source_contributions"].available
    assert withheld["annual"].display == "$1,234.50"

    for values, found in (
        ({"amount": "nonzero"}, 1),
        ({"amount": "zero"}, 0),
        ({"pledge_min": "1234.50", "pledge_max": "1234.50"}, 1),
        ({"pledge_max": "1234.49"}, 0),
        ({"pledge_min": "1234.51"}, 0),
        ({"frequency": "monthly"}, 1),
        ({"frequency": "weekly"}, 0),
        ({"frequency": "none"}, 0),
        ({"share": CHECK}, 1),
        ({"share": "none"}, 0),
        ({"share": "no-such-option"}, 0),
        ({"search": "no such family"}, 0),
        ({"search": str(row["family_duid"])}, 1),
        ({"active": "inactive"}, 0),
        ({"latest_start": "2000-01-01", "latest_end": "2999-12-31"}, 1),
        ({"first_end": "2000-01-01"}, 0),
    ):
        assert report(harness, **values)["total"] == found, values

    # A replacement keeps one row: the latest intent with its first-response date.
    with web_login():
        second = pledge(harness, revisit(harness), annual_pledge="0", frequency="")
    replaced = report(harness)
    assert replaced["total"] == 1
    current = replaced["rows"][0]
    assert current["id"] == str(second.pk) and current["family_version"] == 2
    assert current["first_submitted_at"] == first.submitted_at
    assert current["annual"].display == "$0.00"
    # A zero pledge has no frequency, so there is no installment to compute.
    assert not current["installment"].available
    assert current["frequency_label"] == "No frequency" and current["shares"] == []
    assert replaced["summary"]["no_share"] == 1
    assert report(harness, amount="zero", frequency="none", share="none")["total"] == 1

    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
    with pytest.raises(PermissionError):
        report(harness, principal=leader)


def test_unproven_giving_and_closed_parameters(response_service):
    """An incomplete giving read withholds money; SQL refuses unknown parameters."""
    harness = response_service
    financial_source(harness, covered=False)
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness))
    row = report(harness)["rows"][0]
    assert not row["source_pledge"].available
    assert not row["source_contributions"].available
    assert report(harness)["metadata"]["giving_through"] is None

    neutral = {"filters": FinancialQuery().form_values(), "giving": False}
    invalid = (
        neutral | {"giving": "yes"},
        neutral | {"extra": 1},
        {"filters": neutral["filters"]},
        neutral | {"filters": neutral["filters"] | {"pledge_min": "1,234"}},
        neutral | {"filters": neutral["filters"] | {"pledge_min": "12.5"}},
        neutral
        | {"filters": neutral["filters"] | {"pledge_min": "9", "pledge_max": "1"}},
        neutral | {"filters": neutral["filters"] | {"latest_start": "2026-02-30"}},
        neutral | {"filters": neutral["filters"] | {"sort": "random"}},
        neutral | {"filters": neutral["filters"] | {"amount": "all"}},
        neutral | {"filters": {"search": ""}},
        neutral | {"filters": neutral["filters"] | {"unknown": ""}},
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for parameters in invalid:
            with (
                pytest.raises(DatabaseError, match="Invalid financial report"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "SELECT stewardship_financial_report_v1(%s,%s::jsonb,1)",
                    [harness.campaign.pk, json.dumps(parameters)],
                )
    for values in (
        {"pledge_min": "1,234"},
        {"pledge_min": "5", "pledge_max": "1"},
        {"latest_start": "2026-02-30"},
        {"first_start": "2026-02-01", "first_end": "2026-01-01"},
        {"share": "bad share!"},
        {"sort": "random"},
        {"unknown": "x"},
    ):
        with pytest.raises(ValueError):
            FinancialQuery.parse(values)
