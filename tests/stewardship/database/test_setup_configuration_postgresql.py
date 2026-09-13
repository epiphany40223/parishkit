"""Real SQL journals and file-crash recovery for selected-but-unapplied setup."""

# Pytest deliberately injects the imported shared setup fixture by name.
# ruff: noqa: F811

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.configuration_installation import (
    DatabaseMaterializer,
    install_request,
)
from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import (
    ConfigurationActivation,
    SystemConfiguration,
)
from parishkit.stewardship.accounts.setup_cancellation import (
    cancel_finalizing_setup,
    cancellation_status,
)
from parishkit.stewardship.accounts.setup_installation import abort_setup_configuration
from parishkit.stewardship.accounts.setup_models import (
    SetupAttempt,
    SetupConfigurationAbort,
    SetupConfigurationIntent,
)
from parishkit.stewardship.accounts.setup_staging import begin_setup, cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StorageInvariantError

from ..test_setup_configuration import build, setup_patch
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import (  # noqa: F401
    as_config_installer,
    config_role,
)
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


def frozen(service, *, branding=False):
    """Complete a synthetic task with all guards enabled, then freeze its attempt."""
    request = login(service)
    attempt = begin_setup(request, service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    if branding:
        from parishkit.stewardship.accounts.branding_staging import (
            stage_branding,
            staged_bundle,
        )

        from .test_branding_setup_postgresql import graphics

        media = service.store.root / "media"
        media.mkdir(mode=0o700)
        bundle = stage_branding(
            request,
            service,
            media,
            graphics(),
            base_digest=service.store.active().digest,
            setup_attempt_id=row.pk,
        )
        _, assets = staged_bundle(request, service, bundle, setup_attempt_id=row.pk)
        row._test_branding = {asset.label: str(asset.pk) for asset in assets}
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    act(act(task, "claim"), "complete")
    with work_transaction():
        for state in ("collecting", "frozen"):
            SetupAttempt.objects.filter(pk=row.pk).update(
                state=state, version=F("version") + 1
            )
    row.refresh_from_db()
    return request, row


def values(service, attempt):
    """Build exact immutable request metadata without bypassing database admission."""
    patch = setup_patch()
    if hasattr(attempt, "_test_branding"):
        next(row for row in patch if row["section"] == "parish")["values"][
            "branding"
        ] = attempt._test_branding
    candidate = build(service.store.active(), patch)
    return candidate, dict(
        actor_id=attempt.owner_id,
        correlation_id=uuid4(),
        request_key=uuid4(),
        request_schema="initial-setup-patch-v7",
        base_id=attempt.base_id,
        patch=candidate.patch(),
        payload_fingerprint=candidate.payload_fingerprint,
        candidate_version_id=candidate.candidate.version_id,
        candidate_digest=candidate.candidate.digest,
    )


def bound(service):
    """This storage fixture is not evidence of provider/source completion readiness."""
    browser, attempt = frozen(service, branding=True)
    candidate, fields = values(service, attempt)
    with work_transaction():
        request = ConfigurationChangeRequest.objects.create(**fields)
        intent = SetupConfigurationIntent.objects.create(
            attempt=attempt,
            request=request,
            attempt_version=attempt.version,
            actor_id=attempt.owner_id,
            correlation_id=uuid4(),
        )
    return browser, attempt, intent, candidate.candidate


def selected(service):
    """Simulate the exact crash gap; never fake an applied pointer or disable SQL."""
    browser, attempt, intent, candidate = bound(service)
    prepare_snapshot(candidate, actor_id=attempt.owner_id, correlation_id=uuid4())
    materializer = DatabaseMaterializer(
        service.store,
        request=intent.request,
        actor_id=attempt.owner_id,
        correlation_id=uuid4(),
    )
    with materializer.lock():
        materializer.checkpoint("validating")
        materializer.checkpoint("prepared")
        service.store.write_version(candidate)
        service.store.select(candidate)
        materializer.checkpoint("yaml_activated")
    return browser, attempt, intent, candidate


def expire_selected(attempt):
    """The real SQL expiry guard verifies the original live cancellation owner."""
    with work_transaction():
        SetupAttempt.objects.filter(pk=attempt.pk).update(
            state="expired",
            expired_at=None,
            expiry_reason="cancelled",
            actor_id=attempt.owner_id,
            version=F("version") + 1,
        )


def test_setup_request_cannot_commit_without_original_attempt_binding(setup_service):
    """The deferred check permits atomic insertion, never an unbound durable request."""
    _, attempt = frozen(setup_service)
    _, fields = values(setup_service, attempt)
    with (
        pytest.raises(IntegrityError, match="original-attempt binding"),
        work_transaction(),
    ):
        ConfigurationChangeRequest.objects.create(**fields)
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize("mutation", ["actor", "version", "base"])
def test_intent_rejects_changed_attempt_binding(setup_service, mutation):
    """An exact actor alone is insufficient: original frozen version/base also bind."""
    _, attempt = frozen(setup_service, branding=mutation == "base")
    _, fields = values(setup_service, attempt)
    if mutation == "base":
        other = build(setup_service.store.active(), fields["patch"]).candidate
        prepare_snapshot(other, actor_id=attempt.owner_id, correlation_id=uuid4())
        fields["base_id"] = other.version_id
    with pytest.raises(IntegrityError) as error, work_transaction():
        request = ConfigurationChangeRequest.objects.create(**fields)
        SetupConfigurationIntent.objects.create(
            attempt_id=attempt.pk,
            request=request,
            attempt_version=attempt.version + (mutation == "version"),
            actor_id=uuid4() if mutation == "actor" else attempt.owner_id,
            correlation_id=uuid4(),
        )
    assert error.value.__cause__.sqlstate == "23514"
    assert not ConfigurationChangeRequest.objects.exists()


def test_unexpired_setup_cannot_be_aborted(setup_service):
    """A mismatch is not cancellation authority; preserve both facts untouched."""
    _, attempt, _, candidate = selected(setup_service)
    with pytest.raises(StorageInvariantError, match="expired"):
        abort_setup_configuration(
            setup_service.store, attempt_id=attempt.pk, correlation_id=uuid4()
        )
    assert not SetupConfigurationAbort.objects.exists()
    assert setup_service.store.active() == candidate
    assert SystemConfiguration.objects.get().active_configuration_id == attempt.base_id


def test_selected_setup_abort_restores_bootstrap_once(setup_service):
    """Journaled cancellation fails its request without activating any successor."""
    _, attempt, intent, _ = selected(setup_service)
    expire_selected(attempt)
    receipt = abort_setup_configuration(
        setup_service.store, attempt_id=attempt.pk, correlation_id=uuid4()
    )
    assert receipt.state == "failed"
    assert receipt.failure_code == "invalid_candidate"
    assert setup_service.store.active().version_id == attempt.base_id
    assert SystemConfiguration.objects.get().active_configuration_id == attempt.base_id
    assert SetupConfigurationAbort.objects.get().reason == "cancelled"
    assert not ConfigurationActivation.objects.filter(request=intent.request).exists()
    assert (
        abort_setup_configuration(
            setup_service.store, attempt_id=attempt.pk, correlation_id=uuid4()
        )
        == receipt
    )
    assert SetupConfigurationAbort.objects.count() == 1


def test_setup_abort_journal_survives_file_failure_and_runs_before_forward_recovery(
    setup_service, monkeypatch
):
    """A failed select cannot erase cancellation or accidentally activate on retry."""
    _, attempt, intent, candidate = selected(setup_service)
    expire_selected(attempt)
    original = setup_service.store.select

    def fail_select(version):
        """Simulate interruption before the predecessor's atomic manifest write."""
        raise OSError("synthetic file failure")

    monkeypatch.setattr(setup_service.store, "select", fail_select)
    with pytest.raises(OSError):
        abort_setup_configuration(
            setup_service.store, attempt_id=attempt.pk, correlation_id=uuid4()
        )
    assert SetupConfigurationAbort.objects.count() == 1
    assert setup_service.store.active() == candidate
    monkeypatch.setattr(setup_service.store, "select", original)
    result = install_request(
        setup_service.store, request_id=intent.request_id, correlation_id=uuid4()
    )
    assert result.state == "failed"
    assert setup_service.store.active().version_id == attempt.base_id
    assert not ConfigurationActivation.objects.filter(request=intent.request).exists()


def test_setup_intent_and_abort_are_immutable_in_sql(setup_service):
    """Neither ORM bulk updates nor direct deletes can rewrite retained authority."""
    _, attempt, intent, _ = selected(setup_service)
    expire_selected(attempt)
    abort_setup_configuration(
        setup_service.store, attempt_id=attempt.pk, correlation_id=uuid4()
    )
    for model in (SetupConfigurationIntent, SetupConfigurationAbort):
        with pytest.raises(StorageInvariantError):
            model.objects.update(actor_id=uuid4())
        from django.db import connection

        table = connection.ops.quote_name(model._meta.db_table)
        for sql, parameters in (
            (f"DELETE FROM {table}", []),
            (f"UPDATE {table} SET actor_id=%s", [uuid4()]),
        ):
            with (
                pytest.raises(IntegrityError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(sql, parameters)
    assert SetupConfigurationIntent.objects.get().pk == intent.pk


def test_normal_cancel_before_selection_still_uses_original_session(setup_service):
    """Adding the journal does not require an installer for untouched collecting UI."""
    request = login(setup_service)
    attempt = begin_setup(request, setup_service)
    assert cancel_setup(request, setup_service, attempt.attempt_id).state == "expired"
    assert not SetupConfigurationAbort.objects.exists()


def test_original_login_can_cancel_selected_setup_without_readmitting_other_work(
    setup_service,
):
    """The narrow predecessor policy permits cancellation, never general editing."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.setup_drafts import view_draft

    browser, attempt, intent, candidate = selected(setup_service)
    assert cancellation_status(browser, setup_service).state == "frozen"
    with pytest.raises(ConfigError):
        view_draft(browser, setup_service, attempt.pk)
    result = cancel_finalizing_setup(browser, setup_service, attempt.pk)
    assert result.state == "expired"
    assert setup_service.store.active() == candidate  # Web cannot restore files.
    assert not SetupConfigurationAbort.objects.exists()
    # The ordinary configuration installer now discovers expiry, journals it,
    # and restores authority before considering forward recovery.
    receipt = install_request(
        setup_service.store, request_id=intent.request_id, correlation_id=uuid4()
    )
    assert receipt.state == "failed"
    assert setup_service.store.active().version_id == attempt.base_id


def test_new_login_cannot_adopt_an_original_attempt_for_cancellation(setup_service):
    """Even the same Admin account's second session cannot access the exception."""
    original = login(setup_service)
    other = login(setup_service)
    # Selection's own original login is deliberately distinct from both.
    owner, attempt, _, candidate = selected(setup_service)
    for browser in (original, other):
        with pytest.raises(LookupError):
            cancel_finalizing_setup(browser, setup_service, attempt.pk)
    assert cancellation_status(owner, setup_service).state == "frozen"
    assert setup_service.store.active() == candidate


def test_cancel_fails_closed_for_revoked_original_session_and_unrelated_manifest(
    setup_service,
):
    """The exception still verifies live login and the exact current manifest."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.sessions import end_admin

    browser, attempt, _, candidate = selected(setup_service)
    root = setup_service.store.read_version(attempt.base_id)
    unrelated = build(root, setup_patch()).candidate
    setup_service.store.write_version(unrelated)
    setup_service.store.select(unrelated)
    with pytest.raises(ConfigError):
        cancel_finalizing_setup(browser, setup_service, attempt.pk)
    setup_service.store.select(candidate)
    end_admin(browser)
    with pytest.raises(PermissionError):
        cancel_finalizing_setup(browser, setup_service, attempt.pk)
    assert SetupAttempt.objects.get(pk=attempt.pk).state == "frozen"


def test_restricted_config_installer_consumes_setup_expiry_proof(
    setup_service, config_role
):
    """No session, source payload or setup UPDATE privileges are needed to recover."""
    from parishkit.stewardship.accounts.configuration_service import (
        admit_configuration_database,
    )

    browser, attempt, intent, _ = selected(setup_service)
    cancel_finalizing_setup(browser, setup_service, attempt.pk)
    with as_config_installer():
        admit_configuration_database()
        result = install_request(
            setup_service.store, request_id=intent.request_id, correlation_id=uuid4()
        )
    assert result.state == "failed"
    assert setup_service.store.active().version_id == attempt.base_id


def test_setup_logo_cleanup_waits_for_completed_manifest_restoration(
    setup_service, monkeypatch
):
    """Actual scheduler/worker remove only wizard files released by finished abort."""
    from parishkit.stewardship.accounts.branding_models import BrandingBundle
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.jobs.dispatch import execute_hint

    from .test_background_grants_postgresql import task_login
    from .test_branding_cleanup_postgresql import arguments, produce

    browser, attempt, intent, _ = selected(setup_service)
    bundle = BrandingBundle.objects.get(setup_attempt_id=attempt.pk)
    media = setup_service.store.root / "media"
    cancel_finalizing_setup(browser, setup_service, attempt.pk)
    assert produce() == ()
    original = setup_service.store.select

    def unavailable(version):
        """Crash after durable abort but before manifest restoration finishes."""
        raise OSError("synthetic manifest interruption")

    monkeypatch.setattr(setup_service.store, "select", unavailable)
    with pytest.raises(OSError):
        install_request(
            setup_service.store, request_id=intent.request_id, correlation_id=uuid4()
        )
    assert SetupConfigurationAbort.objects.count() == 1
    assert produce() == ()
    monkeypatch.setattr(setup_service.store, "select", original)
    install_request(
        setup_service.store, request_id=intent.request_id, correlation_id=uuid4()
    )
    (task,) = produce()
    assert task.domain_request_id == bundle.pk
    with task_login(ServiceRole.WORKER, reconnect=True):
        assert execute_hint(task.run_id, **arguments(media))
    bundle.refresh_from_db()
    assert bundle.state == "scrubbed"
    assert not (media / "branding" / bundle.pk.hex).exists()
    assert SetupConfigurationAbort.objects.count() == 1


def test_unrelated_prepared_configuration_still_pins_aborted_setup_logo(setup_service):
    """Cancellation cannot release an asset referenced by any other retained version."""
    from parishkit.stewardship.accounts.authority import parse_version
    from parishkit.stewardship.accounts.configuration_schema import validate_sections

    from .test_branding_cleanup_postgresql import produce

    browser, attempt, intent, candidate = selected(setup_service)
    other = candidate.document()
    other["version_id"] = str(uuid4())
    retained = parse_version(other, validate_sections=validate_sections)
    prepare_snapshot(retained, actor_id=attempt.owner_id, correlation_id=uuid4())
    cancel_finalizing_setup(browser, setup_service, attempt.pk)
    receipt = install_request(
        setup_service.store, request_id=intent.request_id, correlation_id=uuid4()
    )
    assert receipt.state == "failed"
    assert setup_service.store.active().version_id == attempt.base_id
    assert SetupConfigurationAbort.objects.filter(intent=intent).count() == 1
    assert produce() == ()
