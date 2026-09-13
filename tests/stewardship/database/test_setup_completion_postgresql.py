"""Real initial activation commits only with fresh source and Family population."""

# ruff: noqa: F811 -- imported pytest fixtures.

from functools import partial

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.accounts.setup_install_models import SetupCompletion
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.setup_completion import complete_setup
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .credential_builders import keys
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_final_loading_postgresql import load, prepared, queued
from .test_setup_loading_postgresql import pages
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO stewardship_setup_completion DEFAULT VALUES",
        "INSERT INTO stewardship_config_activation DEFAULT VALUES",
        "INSERT INTO stewardship_config_checkpoint DEFAULT VALUES",
        "INSERT INTO stewardship_campaign DEFAULT VALUES",
        "INSERT INTO stewardship_policy_epoch DEFAULT VALUES",
        "INSERT INTO stewardship_schedule_selection DEFAULT VALUES",
        "INSERT INTO stewardship_schedule_definition DEFAULT VALUES",
        "INSERT INTO stewardship_occurrence_transition DEFAULT VALUES",
    ],
)
def test_extra_worker_writes_are_not_general_configuration_authority(statement):
    """Knowing the table/role does not provide the exact live completion context."""
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError) as raised,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert raised.value.__cause__.sqlstate in {"23514", "42501"}


@pytest.mark.parametrize("financial", [False, True])
def test_first_setup_is_atomic_and_has_real_family_codes(
    setup_service, monkeypatch, tmp_path, config_role, financial
):
    """Exercise the restricted worker through actual preparation and final load."""
    _, attempt, identifier = prepared(
        setup_service, monkeypatch, tmp_path, financial=financial
    )
    with web_login():
        assert not setup_is_complete()
    ring = keys()
    task = queued(setup_service, identifier)
    responses = pages()
    if financial:
        from test_parishsoft_source import page

        from ..test_source_giving import contribution, pledge

        responses += [
            page([pledge(organizationID=1)]),
            page([contribution(organizationId=1, memberId=1)]),
        ]
    fake_provider(monkeypatch, responses)
    finalize = partial(
        complete_setup,
        store=setup_service.store,
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
    )
    completed = load(
        setup_service,
        task,
        tmp_path / "parishsoft" / "credential",
        finalize=lambda execution, claim, snapshot: finalize(
            execution, claim, snapshot.pk
        ),
    )
    assert SetupCompletion.objects.get() == completed
    with web_login():
        assert setup_is_complete()
    assert SetupAttempt.objects.get().state == "completed"
    runtime = SystemConfiguration.objects.get()
    assert runtime.current_campaign_id == attempt.attempt_id
    assert runtime.active_configuration_id == completed.activation.configuration_id
    assert runtime.mode == "testing"
    assert SourceCurrent.objects.get().snapshot_id == completed.snapshot_id
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
    population = CampaignCredentialState.objects.get(campaign_id=attempt.attempt_id)
    assert population.source_snapshot_id == completed.snapshot_id
    assert not population.population_dirty
    assert FamilyCampaign.objects.filter(portal_eligible=True).exists()
    assert not FamilyCampaign.objects.filter(
        portal_eligible=True, code_ciphertext__isnull=True
    ).exists()
    assert_scrubbed()


def assert_scrubbed():
    """Use migration identity to verify every target, not RLS-hidden empty results."""
    from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential

    for table in (
        "stewardship_setup_draft_section",
        "stewardship_setup_sealed_credential",
        "stewardship_setup_source_exchange",
        "stewardship_setup_mail_delivery",
        "stewardship_setup_mail_exchange",
    ):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE scrubbed_at IS NULL")
            assert cursor.fetchone() == (0,), table
    assert not SetupSealedCredential.objects.exclude(settings={}).exists()
    assert not SetupSealedCredential.objects.filter(ciphertext__isnull=False).exists()


@pytest.mark.parametrize("target", ["parishsoft", "google_workspace"])
def test_prepared_receipt_does_not_replace_current_consumer_proof(
    setup_service, monkeypatch, tmp_path, config_role, target
):
    """Fault-inject a lost ACK after preparation, then exercise real worker SQL."""
    from django.db import transaction

    from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest

    _, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    request_id = SecretReplacementRequest.objects.get(target=target).pk
    # Only the disposable schema owner may simulate damaged retained evidence.
    # Runtime admission and all triggers are fully enabled during finalization.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_credential_consumer_ack DISABLE TRIGGER USER"
        )
        try:
            cursor.execute(
                "DELETE FROM stewardship_credential_consumer_ack WHERE request_id=%s",
                [request_id],
            )
        finally:
            cursor.execute(
                "ALTER TABLE stewardship_credential_consumer_ack ENABLE TRIGGER USER"
            )
    ring = keys()
    task = queued(setup_service, identifier)
    fake_provider(monkeypatch, pages())

    def finalize(execution, claim, snapshot):
        """The prepared YAML is valid, but no partial activation may now commit."""
        with pytest.raises(DatabaseError) as error:
            complete_setup(
                execution,
                claim,
                snapshot.pk,
                store=setup_service.store,
                general=ring.general,
                mac=ring.mac,
                public=ring.public,
            )
        assert error.value.__cause__.sqlstate == "23514"
        assert not SetupCompletion.objects.exists()
        assert SourceCurrent.objects.get().snapshot_id is None
        assert SetupAttempt.objects.get().state == "frozen"
        assert not FamilyCampaign.objects.exists()
        assert TaskRun.objects.get(pk=task.run_id).state == "running"

    load(setup_service, task, tmp_path / "parishsoft" / "credential", finalize=finalize)


def test_late_failures_roll_back_every_effect_and_allow_same_live_owner_retry(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Failed population, marker and Task completion never expose partial setup."""
    from parishkit.stewardship.source import setup_completion as owner

    _, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    ring = keys()
    task = queued(setup_service, identifier)
    fake_provider(monkeypatch, pages())
    baseline = SystemConfiguration.objects.get().active_configuration_id

    def finalize(execution, claim, snapshot):
        """Reuse one real staged corpus/fence while injected transactions roll back."""
        finish = partial(
            complete_setup,
            execution,
            claim,
            snapshot.pk,
            store=setup_service.store,
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
        )
        for stage in ("families", "marker", "task", "omit_task", "omit_population"):
            with monkeypatch.context() as patch:

                def fail(*args, **kwargs):
                    """Simulate failure inside the still-uncommitted effect."""
                    raise RuntimeError("synthetic atomic completion failure")

                if stage == "families":
                    patch.setattr(owner, "reconcile_source_chairs", fail)
                elif stage == "marker":
                    patch.setattr(SetupCompletion.objects, "create", fail)
                elif stage == "omit_population":
                    patch.setattr(
                        owner, "reconcile_source_families", lambda *a, **k: None
                    )
                else:
                    original = owner.change_run

                    def finish_then_fail(*args, original=original, **kwargs):
                        """An inner Task save is not a committed completion."""
                        original(*args, **kwargs)
                        fail()

                    patch.setattr(
                        owner,
                        "change_run",
                        finish_then_fail if stage == "task" else lambda **kwargs: None,
                    )
                with pytest.raises((RuntimeError, DatabaseError)):
                    finish()
            assert not execution.control.finished.is_set()
            assert not SetupCompletion.objects.exists()
            assert not FamilyCampaign.objects.exists()
            assert SourceCurrent.objects.get().snapshot_id is None
            assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "ready"
            assert TaskRun.objects.get(pk=task.run_id).state == "running"
            assert SetupAttempt.objects.get().state == "frozen"
            runtime = SystemConfiguration.objects.get()
            assert runtime.active_configuration_id == baseline
            assert runtime.current_campaign_id is None
        return finish()

    completed = load(
        setup_service,
        task,
        tmp_path / "parishsoft" / "credential",
        finalize=finalize,
    )
    assert SetupCompletion.objects.get() == completed
