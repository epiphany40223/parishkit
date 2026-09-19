"""Post-cleanup readiness uses real setup, cleanup, preparation and web roles."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from datetime import timedelta
from time import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing
from django.db import connection
from django.db.models import F
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.confirmation_commands import confirm, verify_preview
from parishkit.stewardship.accounts.confirmation_preview import collect_preview
from parishkit.stewardship.accounts.confirmation_readiness import collect_readiness
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now, issue_admin
from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import TASK_TYPE
from parishkit.stewardship.campaigns.catchup_tasks import catchup_handler
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
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
from .test_taskrun_postgresql import act

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
    links = request.getfixturevalue("ready_links")
    browser, links_path = links[:2]
    preparation, arguments = prepare(links)
    login, service = arguments[:2]
    campaign = preparation.transition.campaign
    path = f"{links_path}/{preparation.pk}/confirm"
    with web_login():
        settings_path = f"/admin/campaign/{campaign.pk}/settings"
        assert not browser.get(settings_path).context["production_progress_available"]
        activity = PortalSession.objects.get(
            pk=login.portal_session.pk
        ).last_activity_at
        page = browser.get(path)
        assert page.status_code == 200, page.content
        assert page["Cache-Control"] == "no-store"
        assert not page.context["fresh"]
        assert page.context["preview"] is None
        assert browser.head(path).status_code == 200
        assert (
            PortalSession.objects.get(pk=login.portal_session.pk).last_activity_at
            == activity
        )
        assert browser.post(path, {"action": "verify"}).status_code == 403
        assert (
            post(browser, path, {"action": "verify", "actor": "other"}).status_code
            == 400
        )
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
        browser.cookies["pk_admin"] = login.session.session_key
        page = post(browser, path, {"action": "verify"})
        assert page.status_code == 200, page.content
        assert page.context["fresh"]
        token = page.context["confirmation_token"]
        values = {"action": "confirm", "preview": token, "typed": "Production"}
        assert post(browser, path, values | {"typed": "Testing"}).status_code == 400
        response = post(browser, path, values)
        assert response.status_code == 302, response.content
        assert post(browser, path, values).status_code == 302
        receipt = ProductionConfirmation.objects.get()
        assert confirm(*arguments, token=token, typed="Production").pk == receipt.pk
        with monkeypatch.context() as patch:
            patch.setattr(signing, "time", SimpleNamespace(time=lambda: time() + 301))
            with pytest.raises(signing.SignatureExpired):
                signing.loads(
                    token, salt="stewardship-production-confirmation-v1", max_age=300
                )
            assert post(browser, path, values).status_code == 302
            assert ProductionConfirmation.objects.count() == 1
        with override_settings(STEWARDSHIP_PUBLIC_ORIGIN="http://localhost:8001"):
            assert confirm(*arguments, token=token, typed="Production").pk == receipt.pk
        progress_path = response["Location"]
        page = browser.get(progress_path)
        assert page.status_code == 200, page.content
        assert page["Cache-Control"] == "no-store"
        assert (b"Campaign active" if active else b"Campaign scheduled") in page.content
        assert browser.get(settings_path).context["production_progress_available"]
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
        demand = demands.get()
        with work_transaction():
            act(
                act(_status(TaskRun.objects.get(pk=demand.task_root_id)), "claim"),
                "permanent_failure",
            )
        with web_login():
            page = browser.get(progress_path)
            assert b"Preparing initial campaign mail" in page.content
            retry = {"control": page.context["control"]}
            assert retry["control"]
            competing = {"control": browser.get(progress_path).context["control"]}
            assert post(browser, progress_path, retry).status_code == 302
            assert post(browser, progress_path, retry).status_code == 302
            assert post(browser, progress_path, competing).status_code == 409
            assert not browser.get(progress_path).context["complete"]
        # Current eligibility can change after confirmation. Freeze the group's
        # actual result, not a later recomputation against mutable Family data.
        with work_transaction():
            FamilyCampaign.objects.filter(campaign=campaign).update(
                email_deliverable=False, version=F("version") + 1
            )
        latest = TaskRun.objects.filter(root_id=demand.task_root_id).latest(
            "retry_sequence"
        )
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert execute_hint(
                latest.pk,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={"activation_catchup": catchup_handler()},
            )
        with web_login():
            page = browser.get(progress_path)
            assert page.context["complete"]
            family_outcome = page.context["outcomes"][0]
            assert family_outcome["preview"] > 0
            assert family_outcome["actual"] == 0
            assert family_outcome["difference"] == -family_outcome["preview"]
            assert b"Differences so far are not final reductions" not in page.content
        with work_transaction():
            FamilyCampaign.objects.filter(campaign=campaign).update(
                email_deliverable=True, version=F("version") + 1
            )
        with web_login():
            assert browser.get(progress_path).context["outcomes"][0] == family_outcome
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    with web_login():
        assert browser.get(progress_path).status_code == 403
        assert post(browser, path, values).status_code == 403
