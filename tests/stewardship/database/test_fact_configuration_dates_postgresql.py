"""Current campaign configuration can legitimately move the local fact date back."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.reports.demand import request_rebuild
from parishkit.stewardship.reports.facts import FactUnavailable, publish_fact_set
from parishkit.stewardship.reports.models import CampaignFactPointer

from .campaign_builders import change
from .fact_builders import fact_fixture, staged_facts
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("edit", ["timezone", "end_date"])
def test_current_configuration_replaces_later_old_local_date(tmp_path, edit):
    """Use actual configuration installation and both Python/SQL publication guards."""
    inputs, owner, source = fact_fixture(tmp_path)
    if edit == "end_date":
        inputs = replace(inputs, through_date=inputs.through_date + timedelta(days=1))
    old, _ = staged_facts(inputs, owner, source)
    publish_fact_set(old.pk, owner, admit=permit, interactive=True)
    with transaction.atomic():
        request_rebuild(inputs, admit=permit)
    store = AuthorityStore(tmp_path, validate_sections)
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(inputs.campaign_id),
                "values": {
                    edit: "Pacific/Honolulu" if edit == "timezone" else "2026-10-02"
                },
            }
        ],
    )
    assert result.state == "applied"
    current = replace(
        inputs,
        timezone_configuration_id=Campaign.objects.get(
            pk=inputs.campaign_id
        ).active_configuration_id,
        through_date=inputs.through_date - timedelta(days=1),
    )
    with transaction.atomic():
        demand = request_rebuild(current, admit=permit)
    assert demand.requested_through_date == current.through_date
    new, _ = staged_facts(current, owner, source)
    publish_fact_set(new.pk, owner, admit=permit, interactive=True)
    assert CampaignFactPointer.objects.get().fact_set_id == new.pk
    # Neither a still-older date in the current configuration nor a late old
    # configuration builder may use this exception to rewind interactive state.
    for invalid in (
        replace(current, through_date=current.through_date - timedelta(days=1)),
        inputs,
    ):
        with pytest.raises(FactUnavailable), transaction.atomic():
            request_rebuild(invalid, admit=permit)
    with (
        pytest.raises(IntegrityError) as failure,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_fact_pointer SET fact_set_id=%s,version=version+1",
            [old.pk],
        )
    assert failure.value.__cause__.sqlstate == "23514"
