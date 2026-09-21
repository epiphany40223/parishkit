"""Actual promoted financial source reads under the restricted operational web role."""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.financial_inputs import financial_definition
from parishkit.stewardship.responses.source_inputs import load_financial_inputs
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.leases import acquire_source
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    stage_entities,
)

from ..financial_factory import configuration, record
from ..test_source_corpus import TODAY
from .campaign_builders import change
from .response_builders import response_source
from .source_builders import running_source_task
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import promote
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def financial_source(
    harness,
    *,
    covered=True,
    empty=False,
    modules=None,
    options=(),
    selected=(),
    extra_pledges=None,
    extra_funds=None,
    extra_contributions=None,
    extra_families=None,
    extra_members=None,
    giving_as_of=None,
):
    """Use actual configuration/staging/promotion, with only synthetic money rows.

    `extra_families` and `extra_members` can make more households eligible, and
    `giving_as_of` backdates the recorded giving cutoff so a test does not
    depend on today.
    """
    financial = configuration()["financial"] | {
        "fund_duids": [9],
        "overlap_confirmed": True,
    }
    receipt = change(
        harness.service.store,
        harness.service.store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": {
                    "modules": sorted(modules or ["financial"]),
                    "ministry_duids": sorted(selected),
                    "financial": financial,
                    "share_options": list(options),
                },
            }
        ],
    )
    assert receipt.state == "applied"
    harness.campaign.refresh_from_db()
    values = harness.campaign.active_configuration.values
    definition = financial_definition(values, campaign_id=harness.campaign.pk)
    data = replace(response_source(), organization_id=12345)
    data.funds.update(extra_funds or {})
    data.families.update(extra_families or {})
    data.members.update(extra_members or {})
    # A household is eligible only in an active Family group; 7 is the fixture's
    # one "Active" group, which the sample's second Family otherwise lacks.
    headed = {member["familyDUID"] for member in (extra_members or {}).values()}
    for family in data.families.values():
        if family["familyDUID"] in headed:
            family.setdefault("famGroupID", 7)
        if family.get("registeredOrganizationID") == 5:
            family["registeredOrganizationID"] = 12345
    corpus = normalize_core(data, as_of=TODAY)
    if not empty:
        corpus["pledge"] = {
            "201": record("1200.00", effective_date="2026-01-01"),
            "202": record("9999.00", effective_date="2026-01-01", family_key="2"),
        }
        corpus["contribution"] = {
            "301": record("100.01", effective_date="2026-01-01"),
            "302": record("-0.01", effective_date="2026-01-01"),
            "303": record("8888.00", effective_date="2026-01-01", family_key="2"),
        }
    corpus["pledge"].update(extra_pledges or {})
    corpus["contribution"].update(extra_contributions or {})
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=12345, admit=permit)
    for kind, entities in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=entities, admit=permit)
    as_of = snapshot.started_at.date().isoformat()
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor=refresh_cursor(
            snapshot_id=snapshot.pk,
            kind="full",
            started_at=snapshot.started_at,
            window_digest=definition.window.digest,
            evidence={
                "schema": "source-load-v1",
                "window_digest": definition.window.digest,
                "as_of_date": as_of,
                "giving_as_of_date": giving_as_of or as_of,
            },
        )
        if covered
        else {},
        admit=permit,
    )
    snapshot = promote(snapshot, claim, harness.campaign, harness.rings)
    return snapshot, values


@pytest.mark.parametrize("covered,empty", [(True, False), (True, True), (False, False)])
def test_real_financial_totals_are_scoped_available_and_web_readable(
    response_service, covered, empty
):
    """Real SQL queries exclude another Family and never upgrade missing coverage."""
    harness = response_service
    snapshot, values = financial_source(harness, covered=covered, empty=empty)
    with web_login(), work_transaction():
        result = load_financial_inputs(
            snapshot.pk, 1, configuration=values, campaign_id=harness.campaign.pk
        )
    assert result.pledge.canonical == (
        None if not covered else "0.00" if empty else "1200.00"
    )
    assert result.contributions.canonical == (
        None if not covered else "0.00" if empty else "100.00"
    )
    assert result.family_duid == 1
