"""A real initial setup, accepted test mail and restricted Admin cleanup intake."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

import pytest
from django.core import signing
from django.db import DatabaseError, connection
from django.db.models import F

from parishkit.stewardship.accounts import (
    campaign_mail,
    go_live_commands,
    go_live_progress,
)
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_installation import CredentialInstaller
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupManifest,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.rehearsals import prepare_rehearsals
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.source.setup_completion import complete_setup

from .credential_builders import keys
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_campaign_mail_postgresql import deliver
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_setup_credential_installation_postgresql import MATERIAL
from .test_setup_exchange_postgresql import target_login
from .test_setup_final_loading_postgresql import load, prepared, queued
from .test_setup_loading_postgresql import pages
from .test_setup_mail_views_postgresql import web_login
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready_cleanup(setup_service, monkeypatch, tmp_path, config_role, settings):
    """Reuse real setup once per case; replace only external source/mail/DNS calls."""
    request, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    ring = keys()
    task = queued(setup_service, identifier)
    fake_provider(monkeypatch, pages())
    load(
        setup_service,
        task,
        tmp_path / "parishsoft" / "credential",
        finalize=lambda execution, claim, snapshot: complete_setup(
            execution,
            claim,
            snapshot.pk,
            store=setup_service.store,
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
        ),
    )
    setup_service.configured = setup_is_complete
    # Initial installers retain their rollback until the configured marker.
    # Let those real owners observe completion and settle their ACK receipts.
    for target in ("parishsoft", "google_workspace"):
        files = CredentialFiles(
            tmp_path / target / "credential", key(target, material=MATERIAL[target])
        )
        with target_login(target):
            assert (
                CredentialInstaller(files, validate=lambda value: True).run_once().state
                == "applied"
            )
    campaign = Campaign.objects.get()
    template = ContentVersion.objects.get(slot="initial")
    with web_login():
        preview = campaign_mail.prepare(
            request, setup_service, campaign.pk, template.record_id
        )
        campaign_mail.request_sample(
            request,
            setup_service,
            campaign.pk,
            template.record_id,
            preview_token=signing.dumps(preview.binding(), salt=campaign_mail.SALT),
        )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.campaign_mail_tasks.submit_sample",
        lambda *args, **kwargs: DeliveryOutcome.ACCEPTED,
    )
    result = deliver(
        (setup_service, None, None, tmp_path / "google_workspace" / "credential")
    )
    assert result.state == "accepted"
    # Populate disposable rehearsal detail through its ordinary storage owner;
    # cleanup tests must delete real artifacts, not only traverse an empty gate.
    prepare_rehearsals(
        campaign_id=campaign.pk,
        family_ids=list(
            FamilyCampaign.objects.filter(campaign=campaign).values_list(
                "pk", flat=True
            )
        ),
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
        purpose=CampaignWorkKind.READINESS_TEST,
        admit=lambda *args: True,
        actor_id=request.portal_session.principal_id,
    )
    assert RehearsalCredential.objects.exists()
    settings.STEWARDSHIP_PUBLIC_ORIGIN = "http://localhost:8000"
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "test"
    monkeypatch.setattr(go_live_commands, "check_public_origin", lambda *args: True)
    return request, setup_service, campaign


def test_ready_admin_creates_exact_sealed_cleanup_and_replays(ready_cleanup):
    """No fabricated receipt, unrestricted runtime role or signed-only admission."""
    request, service, campaign = ready_cleanup
    with web_login():
        inputs, verified, token = go_live_commands.verify_preview(
            request, service, campaign.pk
        )
        assert not inputs.problems, inputs.problems
        assert verified and token
        result = go_live_commands.start_cleanup(
            request, service, campaign.pk, preview_token=token, acknowledge=True
        )
        replay = go_live_commands.start_cleanup(
            request, service, campaign.pk, preview_token=token, acknowledge=True
        )
        assert replay == result
        assert result.state == "cleanup_queued"
        assert ProductionCleanupManifest.objects.filter(
            request_id=result.request_id
        ).exists()
        assert CampaignCredentialState.objects.get(campaign=campaign).go_live_gate
    assert ProductionTransitionRequest.objects.count() == 1
    assert CampaignMailTest.objects.get().state == "accepted"


def test_web_cannot_acquire_unjournaled_gate(ready_cleanup):
    """A direct column write cannot strand rehearsal access without owned intent."""
    _, _, campaign = ready_cleanup
    with web_login(), pytest.raises(DatabaseError) as error, work_transaction():
        CampaignCredentialState.objects.filter(campaign=campaign).update(
            go_live_gate=True, rehearsal_epoch_id=None, version=F("version") + 1
        )
    assert error.value.__cause__.sqlstate == "23514"
    assert not CampaignCredentialState.objects.get(campaign=campaign).go_live_gate
    with (
        web_login(),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_outbox_message")
    assert error.value.__cause__.sqlstate == "42501"


@pytest.mark.parametrize("complete", [False, True])
def test_admin_cancels_queued_or_completed_cleanup_without_activation(
    ready_cleanup, complete
):
    """A web request never impersonates a worker; finished deletions stay finished."""
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login
    from .test_cleanup_tasks_postgresql import run

    request, service, campaign = ready_cleanup
    with web_login():
        _, _, token = go_live_commands.verify_preview(request, service, campaign.pk)
        status = go_live_commands.start_cleanup(
            request, service, campaign.pk, preview_token=token, acknowledge=True
        )
    if complete:
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert run(status)
        assert not RehearsalCredential.objects.exists()
    with web_login():
        preview = go_live_progress.progress(
            request, service, campaign.pk, status.request_id
        )
        cancelled = go_live_progress.control(
            request,
            service,
            campaign.pk,
            status.request_id,
            token=preview["controls"]["cancel"],
        )
        assert cancelled.state == "cancelled"
        assert (
            go_live_progress.control(
                request,
                service,
                campaign.pk,
                status.request_id,
                token=preview["controls"]["cancel"],
            )
            == cancelled
        )
        assert not CampaignCredentialState.objects.get(campaign=campaign).go_live_gate
        assert not go_live_progress.progress(
            request, service, campaign.pk, status.request_id
        )["controls"]
    assert RehearsalCredential.objects.exists() is not complete


def test_http_acknowledgement_progress_and_cancel_are_private_and_passive(
    ready_cleanup, settings, real_limiter
):
    """Actual HTTP/CSRF/session checks wrap the same admitted command owners."""
    from django.test import Client

    from parishkit.stewardship.accounts.authentication import AuthRuntime
    from parishkit.stewardship.accounts.models import PortalSession

    from .test_setup_views_postgresql import post

    request, service, campaign = ready_cleanup
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        service.store, real_limiter, setup_is_complete
    )
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    path = f"/admin/campaign/{campaign.pk}/go-live"
    with web_login():
        assert browser.get(path).status_code == 200
        response = post(browser, path, {"action": "verify"})
        assert response.status_code == 200, response.content
        token = response.context["cleanup_token"]
        assert token and b"Start Testing cleanup" in response.content
        values = {"action": "cleanup", "preview_token": token, "acknowledge": "yes"}
        assert browser.post(path, values).status_code == 403
        assert post(browser, path, values | {"acknowledge": "no"}).status_code == 400
        assert post(browser, path, values | {"actor": "other"}).status_code == 400
        assert not ProductionTransitionRequest.objects.exists()
        started = post(browser, path, values)
        assert started.status_code == 302, started.content
        location = started["Location"]
        activity = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        progress = browser.get(location)
        assert progress.status_code == 200, progress.content
        assert progress["Cache-Control"] == "no-store"
        assert b"Testing cleanup progress" in progress.content
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == activity
        )
        assert Client().get(location).status_code in {302, 403}
        controls = progress.context["controls"]
        assert set(controls) == {"cancel"}
        assert (
            browser.post(location, {"control": controls["cancel"]}).status_code == 403
        )
        assert (
            post(browser, location, {"control": controls["cancel"]}).status_code == 302
        )
        finished = browser.get(location)
        assert finished.context["status"].state == "cancelled"


def test_admin_retry_resumes_failed_cleanup_without_recreating_inventory(
    ready_cleanup, monkeypatch
):
    """Exhausted real worker attempts retain their gate and exact manifest."""
    from parishkit.stewardship.campaigns import cleanup_tasks
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_background_grants_postgresql import task_login
    from .test_cleanup_tasks_postgresql import run
    from .test_taskrun_postgresql import act

    request, service, campaign = ready_cleanup
    with web_login():
        _, _, token = go_live_commands.verify_preview(request, service, campaign.pk)
        status = go_live_commands.start_cleanup(
            request, service, campaign.pk, preview_token=token, acknowledge=True
        )
    task = _status(TaskRun.objects.get(pk=status.task_id))
    for _ in range(4):
        task = act(task, "claim", lease_seconds=60)
        task = act(task, "retryable_failure")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")

    def failure(*args, **kwargs):
        """Inject one application batch failure, retaining all real worker fences."""
        raise DatabaseError("synthetic private database failure")

    with monkeypatch.context() as patch:
        patch.setattr(cleanup_tasks, "apply_checkpoint", failure)
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert run(status)
    with web_login():
        page = go_live_progress.progress(
            request, service, campaign.pk, status.request_id
        )
        assert page["status"].state == "cleanup_failed"
        assert set(page["controls"]) == {"cancel", "retry"}
        retry_token = page["controls"]["retry"]
        retried = go_live_progress.control(
            request, service, campaign.pk, status.request_id, token=retry_token
        )
        assert retried.state == "cleanup_queued"
        assert (
            go_live_progress.control(
                request, service, campaign.pk, status.request_id, token=retry_token
            )
            == retried
        )
        assert ProductionCleanupManifest.objects.count() == 1
        assert CampaignCredentialState.objects.get(campaign=campaign).go_live_gate
    child = TaskRun.objects.filter(root_id=status.task_id).latest("retry_sequence")
    from dataclasses import replace

    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert run(replace(retried, task_id=child.pk))
    assert (
        ProductionTransitionRequest.objects.get(pk=status.request_id).state
        == "cleanup_complete"
    )
    assert not RehearsalCredential.objects.exists()
