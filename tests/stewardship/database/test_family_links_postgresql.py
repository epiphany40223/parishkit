"""Family record ids for the shared Family-name link, read once (#1570)."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.reports.family_links import family_record_ids, link_families

pytestmark = pytest.mark.django_db(transaction=True)


def test_one_read_names_every_row_and_none_for_no_duid(response_service):
    """However many rows, one query; a page with no DUID reads nothing."""
    campaign = response_service.campaign
    families = dict(
        FamilyCampaign.objects.filter(campaign=campaign).values_list(
            "family_duid", "pk"
        )
    )
    assert len(families) > 1
    unknown = max(families) + 1000
    # Every Family twice, an unknown DUID and a row with no DUID at all.
    rows = [dict(family_duid=duid) for duid in [*families, *families, unknown]]
    rows.append(dict(other="no DUID"))
    with CaptureQueriesContext(connection) as queries:
        assert link_families(campaign.pk, rows) is rows
    assert len(queries) == 1
    assert [row["family_id"] for row in rows] == [
        *families.values(),
        *families.values(),
        None,
        None,
    ]
    with CaptureQueriesContext(connection) as queries:
        assert family_record_ids(campaign.pk, [None]) == {}
        assert link_families(campaign.pk, []) == []
    assert len(queries) == 0


def test_another_campaign_and_the_current_campaign(response_service):
    """Ids come only from the named campaign; None means the current one,
    still in one query (Refused addresses, outside any campaign)."""
    campaign = response_service.campaign
    duid, pk = (
        FamilyCampaign.objects.filter(campaign=campaign)
        .values_list("family_duid", "pk")
        .first()
    )
    assert family_record_ids(uuid4(), [duid]) == {}
    assert SystemConfiguration.objects.get().current_campaign_id == campaign.pk
    with CaptureQueriesContext(connection) as queries:
        assert family_record_ids(None, [duid]) == {duid: pk}
    assert len(queries) == 1
    rows = link_families(None, [dict(code=duid)], key="code")
    assert rows == [dict(code=duid, family_id=pk)]
