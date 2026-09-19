"""Post-cleanup readiness uses real setup, cleanup, preparation and web roles."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.confirmation_preview import collect_preview
from parishkit.stewardship.accounts.confirmation_readiness import collect_readiness
from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import TASK_TYPE
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.storage import StaleRecordError

from .test_activation_views_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_background_grants_postgresql import task_login
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post

pytestmark = pytest.mark.django_db(transaction=True)


def test_current_post_cleanup_readiness_is_bounded_and_detects_impact_changes(
    ready_links,
):
    """Enumeration is a preview operation, not part of final readiness rechecks."""
    browser, path, ring, login = ready_links
    with web_login():
        page = browser.get(path)
        assert (
            post(browser, path, {"control": page.context["prepare"]}).status_code == 302
        )
    preparation = ProductionTokenPreparation.objects.select_related("transition").get()
    arguments = (
        login,
        runtime(),
        preparation.transition.campaign_id,
        preparation.transition_id,
        preparation.pk,
    )
    with web_login(), work_transaction(), pytest.raises(StaleRecordError):
        collect_readiness(*arguments)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            preparation.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: token_handler(public=ring.public)},
        )
    with web_login():
        with work_transaction(), CaptureQueriesContext(connection) as queries:
            first = collect_readiness(*arguments)
        assert not first.problems
        assert not any(
            'FROM "stewardship_family_campaign"' in row["sql"]
            or 'FROM "stewardship_schedule_occurrence"' in row["sql"]
            for row in queries
        )
        preview = collect_preview(*arguments)
        assert not preview.problems
        assert preview.expires_at > first.observed_at
        assert preview.readiness.digest == first.digest
        assert preview.families.counts.active >= 1
    with work_transaction():
        FamilyCampaign.objects.update(email_deliverable=False, version=F("version") + 1)
    with web_login(), work_transaction():
        changed = collect_readiness(*arguments)
        assert changed.impact_revision > first.impact_revision
        assert changed.digest != first.digest
    preparation.transition.campaign.refresh_from_db()
    assert preparation.transition.campaign.state == "draft"
    assert preparation.transition.campaign.active_token_generation_id is None
