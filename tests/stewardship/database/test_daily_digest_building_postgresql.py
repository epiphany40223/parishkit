"""Daily and interactive reports share exact facts without consuming UI demand."""

from functools import partial
from unittest.mock import patch

import pytest
from django.db import DatabaseError

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_building import (
    admit_daily_facts,
    begin_daily_facts,
    load_daily_document,
    retain_daily_content,
)
from parishkit.stewardship.reports.digest_capture import capture_daily_snapshot
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestReady,
)
from parishkit.stewardship.reports.materialization import materialize_fact_set
from parishkit.stewardship.reports.models import CampaignFactPin

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_daily_digest_capture_postgresql import prepare
from .test_daily_digest_planning_postgresql import INSTANT

pytestmark = pytest.mark.django_db(transaction=True)


def build(harness, *, configure=None):
    """Run the actual pinned input and fact owners, leaving mail unallocated."""
    claim = prepare(harness, configure=configure)
    with task_login(ServiceRole.WORKER, exact=True):
        with work_transaction():
            capture_daily_snapshot(claim)
            facts = begin_daily_facts(claim)
        assert facts.state == "building"
        materialize_fact_set(facts.pk, claim, admit=partial(admit_daily_facts, claim))
        with work_transaction():
            document = load_daily_document(claim, facts.pk)
        content = render_daily_digest(document, public_origin="https://parish.example")
    return claim, document, content


def test_ready_daily_content_pins_exact_facts_before_fanout(response_service):
    with campaign_clock(INSTANT):
        claim, document, content = build(response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            ready = retain_daily_content(claim, document, content)
        assert ready.snapshot_id == document.snapshot_id
        assert ready.fact_set_id == document.participation.fact_set_id
        assert bytes(ready.chart) == content.chart.data
        assert ready.html == content.html and ready.text == content.text
        assert ready.recipients == ["admin@example.org"]
        assert (
            DailyDigestPreparation.objects.get(task_id=claim.run_id).phase == "fanout"
        )
        assert CampaignFactPin.objects.filter(
            fact_set_id=ready.fact_set_id,
            parent_kind="digest",
            parent_id=ready.snapshot_id,
        ).exists()


def test_missing_fact_pin_cannot_publish_daily_content(response_service):
    with campaign_clock(INSTANT):
        claim, document, content = build(response_service)
        with (
            patch("parishkit.stewardship.reports.digest_building.pin_facts"),
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError, match="retained fact protection"),
            work_transaction(),
        ):
            retain_daily_content(claim, document, content)
        assert not DailyDigestReady.objects.exists()
        assert DailyDigestPreparation.objects.get(task_id=claim.run_id).phase == "facts"
