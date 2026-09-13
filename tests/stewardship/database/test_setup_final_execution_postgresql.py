"""The compiled final handler consumes real scheduler work, not a test callback."""

# ruff: noqa: F811 -- imported pytest fixtures.

from dataclasses import replace
from uuid import uuid4

import pytest
from django.test import Client

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source.setup_final_execution import finalization_handler
from parishkit.stewardship.source.setup_final_production import produce_finalization
from parishkit.stewardship.source.setup_final_tasks import TASK_TYPE
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .credential_builders import keys
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_setup_cancellation_views_postgresql import client_for
from .test_setup_final_loading_postgresql import prepared, wait_for_source
from .test_setup_loading_postgresql import pages
from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import setup_http  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def test_incoherent_finalization_does_not_abort_scheduler_tick(
    setup_http, monkeypatch, tmp_path, config_role, caplog
):
    """An actual prepared receipt with unreadable YAML is ineligible, not fatal."""
    from parishkit.stewardship.accounts.authority import AuthorityStore
    from parishkit.stewardship.accounts.setup_install_models import (
        SetupPreparationReceipt,
    )
    from parishkit.stewardship.observability import Event, SafeJsonFormatter

    prepared(setup_http, monkeypatch, tmp_path)
    original = AuthorityStore.active

    def unavailable(store):
        """Fail only this installation's selection read, not unrelated metadata."""
        if store is setup_http.store:
            raise ConfigError("Synthetic incoherent selection.")
        return original(store)

    monkeypatch.setattr(AuthorityStore, "active", unavailable)
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        assert produce_finalization(setup_http.store, guard) == ()
        guard.check()
        assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()
    events = [record for record in caplog.records if record.msg == Event.TASK_FAILED]
    assert len(events) == 1
    assert (
        events[0].extra["correlation_id"]
        == SetupPreparationReceipt.objects.get().correlation_id
    )
    assert "Synthetic incoherent selection" not in SafeJsonFormatter().format(events[0])


@pytest.mark.parametrize("failure", [None, "credential", "invalid_source"])
def test_real_finalization_producer_and_compiled_worker(
    setup_http, monkeypatch, tmp_path, config_role, failure, settings
):
    """Observe success/retry/failure through real restricted Task/source owners."""
    setup_service = setup_http
    request, _, _ = prepared(setup_service, monkeypatch, tmp_path)
    browser = client_for(request)
    ring = keys()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        produced = produce_finalization(setup_service.store, guard)
        assert len(produced) == 1
        assert produce_finalization(setup_service.store, guard) == produced
    with web_login():
        progress = browser.get("/admin/setup/cancel")
        assert progress.status_code == 200, progress.content
        assert progress.context["prepared"]
        assert progress.context["source"]["id"] == produced[0]
        assert b"synthetic-private" not in progress.content
    path = tmp_path / "parishsoft" / "credential"
    if failure == "credential":
        write_private(path, b"different-synthetic-key")
    responses = pages(
        member_change={"birthdate": "invalid-synthetic-date"}
        if failure == "invalid_source"
        else None
    )
    fake_provider(monkeypatch, responses)
    wait_for_source()
    handler = finalization_handler(
        setup_service.store,
        credential_path=path,
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        execution = claim_hint(
            produced[0],
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: handler},
        )
        assert execution is not None
        with maintain_execution(execution):
            handler.execute(execution)
        assert execution.control.finished.is_set()
    assert (
        TaskRun.objects.get(pk=produced[0]).state
        == {
            None: "succeeded",
            "credential": "retry_wait",
            "invalid_source": "failed",
        }[failure]
    )
    assert setup_is_complete() is (failure is None)
    assert (SourceCurrent.objects.get().snapshot_id is not None) is (failure is None)
    assert SetupAttempt.objects.get().state == (
        "completed" if failure is None else "frozen"
    )
    if failure == "invalid_source":
        rejected = AuditEvent.objects.filter(event_type="source_rejected").last()
        assert rejected is not None
        assert rejected.correlation_id == execution.correlation_id
    if failure is None:
        settings.STEWARDSHIP_AUTH_RUNTIME = replace(
            setup_http, setup_complete=setup_is_complete
        )
        with web_login():
            response = browser.get("/admin/setup/cancel")
            assert response.status_code == 302, response.content
            assert response["Location"] == "/admin/"
            assert response["Cache-Control"] == "no-store"
            assert Client().get("/admin/setup/cancel").status_code == 403
        cleanup_completed_setup()


def cleanup_completed_setup():
    """Dispose the catalog through real roles, preserving current/shared payloads."""
    from parishkit.stewardship.source.setup_cleanup import (
        cleanup_handler,
        produce_setup_cleanup,
    )
    from parishkit.stewardship.source.snapshots import snapshot_manifest
    from parishkit.stewardship.source.version_models import ENTITY_MODELS

    current = SourceCurrent.objects.get().snapshot_id
    before = snapshot_manifest(current)
    catalog = SourceSnapshot.objects.exclude(pk=current).get()
    wait_for_source()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        identifiers = produce_setup_cleanup(guard)
        assert len(identifiers) == 1
        assert produce_setup_cleanup(guard) == ()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            identifiers[0],
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"setup_source_cleanup": cleanup_handler()},
        )
    assert TaskRun.objects.get(pk=identifiers[0]).state == "succeeded"
    assert SourceCurrent.objects.get().snapshot_id == current
    assert snapshot_manifest(current) == before
    catalog.refresh_from_db()
    assert catalog.state == "rejected" and catalog.generation is None
    for _, membership in ENTITY_MODELS.values():
        assert not membership.objects.filter(snapshot=catalog).exists()
    assert setup_is_complete()
