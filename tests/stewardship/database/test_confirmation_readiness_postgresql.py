"""Post-cleanup readiness uses real setup, cleanup, preparation and web roles."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.confirmation_commands import confirm, verify_preview
from parishkit.stewardship.accounts.confirmation_preview import collect_preview
from parishkit.stewardship.accounts.confirmation_readiness import collect_readiness
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now, issue_admin
from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import TASK_TYPE
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.storage import StaleRecordError

from . import test_setup_preparation_postgresql as setup_inputs
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


def prepare(ready_links):
    """Finish actual worker preparation for final-owner scenarios."""
    browser, path, ring, login = ready_links
    with web_login():
        page = browser.get(path)
        assert (
            post(browser, path, {"control": page.context["prepare"]}).status_code == 302
        )
    preparation = ProductionTokenPreparation.objects.select_related(
        "transition__campaign"
    ).get()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            preparation.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: token_handler(public=ring.public)},
        )
    return preparation, (
        login,
        runtime(),
        preparation.transition.campaign_id,
        preparation.transition_id,
        preparation.pk,
    )


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


@pytest.mark.parametrize("active", [False, True])
def test_fresh_confirmation_atomically_activates_and_replays(
    request, monkeypatch, active
):
    """Use real restricted SQL; no direct runtime or campaign update grant is added."""
    start = database_now().date() + timedelta(days=-2 if active else 2)
    original_campaign, original_schedule = (
        setup_inputs.first_campaign,
        setup_inputs.schedule,
    )

    def dated_campaign(*args, **kwargs):
        """Stage an actually current or future campaign through original setup."""
        return original_campaign(
            *args,
            **(
                kwargs
                | {
                    "start_date": start.isoformat(),
                    "end_date": (start + timedelta(days=30)).isoformat(),
                }
            ),
        )

    def dated_schedule(*args, **kwargs):
        """Keep the genuine initial invitation inside that configured interval."""
        return original_schedule(*args, **(kwargs | {"date": start.isoformat()}))

    monkeypatch.setattr(setup_inputs, "first_campaign", dated_campaign)
    monkeypatch.setattr(setup_inputs, "schedule", dated_schedule)
    preparation, arguments = prepare(request.getfixturevalue("ready_links"))
    login, service = arguments[:2]
    campaign = preparation.transition.campaign
    with web_login():
        preview, verified, token = verify_preview(*arguments)
        assert verified and token and not preview.problems
        with pytest.raises(PermissionError, match="after cleanup"):
            confirm(*arguments, token=token, typed="Production")
        issue_admin(
            login,
            login.portal_session.principal_id,
            store=service.store,
            authenticated_at=database_now(),
        )
        receipt = confirm(*arguments, token=token, typed="Production")
        assert confirm(*arguments, token=token, typed="Production").pk == receipt.pk
    campaign.refresh_from_db()
    preparation.transition.refresh_from_db()
    assert campaign.state == ("active" if active else "scheduled")
    assert campaign.active_token_generation_id == receipt.generation_id
    assert campaign.structural_locked
    assert SystemConfiguration.objects.get().mode == "production"
    assert preparation.transition.state == "activated"
    demands = ActivationCatchUpDemand.objects.filter(campaign=campaign)
    assert demands.count() == int(active)
    if active:
        assert demands.get().task_root_id is not None
