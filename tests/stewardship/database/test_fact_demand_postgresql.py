"""Durable debounce windows do not lose events arriving during calculation."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.reports.demand import (
    claim_rebuild,
    complete_rebuild,
    request_rebuild,
)
from parishkit.stewardship.reports.facts import publish_fact_set
from parishkit.stewardship.reports.models import CampaignFactRebuildDemand
from parishkit.stewardship.storage import StorageInvariantError

from .fact_builders import fact_fixture, staged_facts
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def due_demand(inputs):
    """Use a past event instant without sleeping or altering actual task lease time."""
    with transaction.atomic():
        now = database_now() - timedelta(seconds=6)
        with patch(
            "parishkit.stewardship.reports.demand.database_now", return_value=now
        ):
            return request_rebuild(inputs, admit=permit)


def test_identical_hints_do_not_delay_due_work(tmp_path):
    """Duplicate deliveries neither revise the request nor restart its debounce."""
    inputs, owner, _ = fact_fixture(tmp_path)
    first = due_demand(inputs)
    with transaction.atomic():
        repeat = request_rebuild(inputs, admit=permit)
    assert repeat.version == first.version
    assert repeat.pending_due_at == first.pending_due_at
    claimed = claim_rebuild(
        inputs.campaign_id, inputs.population_scope, owner, admit=permit
    )
    assert claimed.claimed_revision == 1
    assert claimed.pending_due_at is None


def test_later_event_survives_completion_and_claims_next_exact_inputs(tmp_path):
    """Finishing revision 1 cannot erase the independent revision-2 event window."""
    inputs, owner, source = fact_fixture(tmp_path)
    due_demand(inputs)
    first = claim_rebuild(
        inputs.campaign_id, inputs.population_scope, owner, admit=permit
    )
    newer = replace(inputs, submission_watermark=2)
    second = due_demand(newer)
    assert second.pending_revision == 2 and second.claimed_revision == 1
    record, _ = staged_facts(inputs, owner, source)
    publish_fact_set(record.pk, owner, admit=permit, interactive=True)
    finished = complete_rebuild(first.pk, owner, revision=1, admit=permit)
    assert finished.pending_revision == 2
    assert finished.pending_due_at == second.pending_due_at
    assert finished.claimed_generation_id is None
    next_claim = claim_rebuild(
        inputs.campaign_id, inputs.population_scope, owner, admit=permit
    )
    assert next_claim.claimed_revision == 2
    assert next_claim.claimed_generation.submission_watermark == 2


def test_debounce_caps_continuous_events_at_thirty_seconds(tmp_path):
    """A continuous event stream cannot postpone an interactive rebuild forever."""
    inputs, _, _ = fact_fixture(tmp_path)
    first = due_demand(inputs)
    with (
        transaction.atomic(),
        patch(
            "parishkit.stewardship.reports.demand.database_now",
            return_value=first.pending_first_at + timedelta(seconds=29),
        ),
    ):
        last = request_rebuild(replace(inputs, submission_watermark=2), admit=permit)
    assert last.pending_due_at == first.pending_first_at + timedelta(seconds=30)


def test_event_transaction_rolls_back_its_demand_and_never_claims_early(tmp_path):
    """The domain event and its materialization request have one commit boundary."""
    inputs, owner, _ = fact_fixture(tmp_path)
    with pytest.raises(StorageInvariantError):
        request_rebuild(inputs, admit=permit)
    with pytest.raises(RuntimeError, match="domain failure"), transaction.atomic():
        request_rebuild(inputs, admit=permit)
        raise RuntimeError("domain failure")
    assert not CampaignFactRebuildDemand.objects.exists()
    with transaction.atomic():
        request_rebuild(inputs, admit=permit)
    assert (
        claim_rebuild(inputs.campaign_id, inputs.population_scope, owner, admit=permit)
        is None
    )


def test_sql_cannot_clear_pending_window_or_rewrite_frozen_claim(tmp_path):
    """A raw writer cannot lose demand by omitting the event/claim protocol."""
    inputs, owner, _ = fact_fixture(tmp_path)
    due_demand(inputs)
    claim_rebuild(inputs.campaign_id, inputs.population_scope, owner, admit=permit)
    due_demand(replace(inputs, submission_watermark=2))
    for change in (
        "pending_first_at=NULL,pending_last_at=NULL,pending_due_at=NULL",
        "claimed_revision=2",
        "requested_submission_watermark=0",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                f"UPDATE stewardship_fact_demand SET {change},version=version+1"
            )
