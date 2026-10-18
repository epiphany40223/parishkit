"""Exact setup confirmation commits one frozen journal, never partial activation."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, transaction

from parishkit.stewardship.accounts import setup_notifications as notifications
from parishkit.stewardship.accounts.models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.setup_confirmation import freeze_setup
from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_mail import (
    begin_submission,
    finish_submission,
    request_sample,
)
from parishkit.stewardship.accounts.setup_models import (
    SetupAttempt,
    SetupConfigurationIntent,
)
from parishkit.stewardship.accounts.setup_preview import PREVIEW_SALT, prepare_preview
from parishkit.stewardship.accounts.setup_readiness_models import SetupReadinessBinding
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..test_setup_forms import VALUES
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_exchange_postgresql import target_login
from .test_setup_mail_postgresql import claim, signed_test
from .test_setup_notifications_postgresql import ready
from .test_setup_preview_postgresql import complete_draft
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def prepared(
    service, monkeypatch, tmp_path, *, slack=False, outcome=DeliveryOutcome.ACCEPTED
):
    """Use real source/preview/intake and isolated result owners; no provider IO."""
    if slack:
        request, attempt, _, _, _ = ready(service, monkeypatch, tmp_path)
        monkeypatch.setattr(
            notifications, "submit_notification", Mock(return_value=outcome)
        )
        with target_login("slack", reconnect=True):
            notifications.run_pending(
                key("slack", material=b"n" * 32), check=lambda: None
            )
    else:
        request, attempt, _ = complete_draft(service, monkeypatch, tmp_path)
    with web_login():
        token = signing.dumps(
            prepare_preview(request, service).binding(), salt=PREVIEW_SALT
        )
        mail = request_sample(
            request,
            service,
            test_token=signed_test(request, service),
            request_key=uuid4(),
        )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(mail)
        begin_submission(mail.identifier, owner)
        finish_submission(mail.identifier, owner, outcome)
    return request, attempt, token


@pytest.mark.parametrize("slack", [False, True])
def test_exact_freeze_is_idempotent_without_configuring_any_product_state(
    setup_service, monkeypatch, tmp_path, slack
):
    """The exact candidate, readiness and frozen attempt survive as one journal."""
    request, attempt, token = prepared(
        setup_service, monkeypatch, tmp_path, slack=slack
    )
    base = setup_service.store.active()
    with web_login():
        receipt = freeze_setup(request, setup_service, preview_token=token)
        assert freeze_setup(request, setup_service, preview_token=token) == receipt
        assert receipt.state == "staged"
        assert SetupConfigurationIntent.objects.count() == 1
        proof = SetupReadinessBinding.objects.get()
        assert proof.testing_recipient == VALUES["testing"]["testing_recipient"]
        assert (proof.slack_delivery_id is not None) is slack
        frozen = SetupAttempt.objects.get(pk=attempt.attempt_id)
        assert frozen.state == "frozen" and frozen.version == attempt.version + 1
        with pytest.raises(PermissionError):
            save_section(
                request,
                setup_service,
                attempt.attempt_id,
                step="parish",
                values=VALUES["parish"],
                expected_version=frozen.version,
            )
    assert not setup_service.configured()
    assert setup_service.store.active() == base


@pytest.mark.parametrize("outcome", [DeliveryOutcome.NOT_SENT, DeliveryOutcome.UNKNOWN])
def test_nonaccepted_test_cannot_be_mistaken_for_readiness(
    setup_service, monkeypatch, tmp_path, outcome
):
    """A tested credential or uncertain provider effect does not prove delivery."""
    request, attempt, token = prepared(
        setup_service, monkeypatch, tmp_path, outcome=outcome
    )
    with web_login(), pytest.raises(ValueError, match="successful test"):
        freeze_setup(request, setup_service, preview_token=token)
    assert SetupAttempt.objects.get(pk=attempt.attempt_id).state == "collecting"
    assert not SetupConfigurationIntent.objects.exists()


def test_another_pending_test_prevents_confirmation_even_after_prior_acceptance(
    setup_service, monkeypatch, tmp_path
):
    """Freeze must not race a later explicit provider effect still in its queue."""
    request, _, token = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        request_sample(
            request,
            setup_service,
            test_token=signed_test(request, setup_service),
            request_key=uuid4(),
        )
        with pytest.raises(ValueError, match="pending"):
            freeze_setup(request, setup_service, preview_token=token)
        assert not SetupConfigurationIntent.objects.exists()


def test_frozen_confirmation_cannot_accept_a_different_signed_binding(
    setup_service, monkeypatch, tmp_path
):
    """Even a valid signature cannot reinterpret the original frozen request."""
    request, _, token = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        freeze_setup(request, setup_service, preview_token=token)
        changed = signing.loads(token, salt=PREVIEW_SALT) | {"candidate": "a" * 64}
        with pytest.raises(StaleRecordError):
            freeze_setup(
                request,
                setup_service,
                preview_token=signing.dumps(changed, salt=PREVIEW_SALT),
            )


def test_failed_binding_insert_rolls_back_request_and_freeze(
    setup_service, monkeypatch, tmp_path
):
    """A failed last intake statement cannot strand a frozen original login."""
    request, attempt, token = prepared(setup_service, monkeypatch, tmp_path)
    monkeypatch.setattr(
        SetupReadinessBinding.objects,
        "create",
        Mock(side_effect=DatabaseError("synthetic")),
    )
    with web_login(), pytest.raises(DatabaseError):
        freeze_setup(request, setup_service, preview_token=token)
    assert SetupAttempt.objects.get(pk=attempt.attempt_id).state == "collecting"
    assert not SetupConfigurationIntent.objects.exists()
    assert not ConfigurationChangeRequest.objects.filter(
        request_schema__in=["initial-setup-patch-v7", "initial-setup-users-v10"]
    ).exists()


def test_a_later_edit_keeps_the_accepted_test_as_readiness(
    setup_service, monkeypatch, tmp_path
):
    """An edit outside a test's own inputs needs a new exact preview, but the
    earlier accepted test still counts (#1578): the SQL readiness guard
    admits it, bound to its credential and the settings sealed with it."""
    request, attempt, token = prepared(setup_service, monkeypatch, tmp_path)
    delivery = SetupMailDelivery.objects.get()
    with web_login():
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="parish",
            values=VALUES["parish"] | {"name": "Revised parish"},
            expected_version=attempt.version,
        )
        with pytest.raises(StaleRecordError):
            freeze_setup(request, setup_service, preview_token=token)
        current = signing.dumps(
            prepare_preview(request, setup_service).binding(), salt=PREVIEW_SALT
        )
        freeze_setup(request, setup_service, preview_token=current)
    assert SetupReadinessBinding.objects.get().mail_delivery_id == delivery.pk


def test_a_changed_test_input_invalidates_the_prior_delivery_readiness(
    setup_service, monkeypatch, tmp_path
):
    """A changed Testing recipient, with the key entered again for it, makes
    the earlier accepted test stale: Finish setup needs a new one (#1578)."""
    from ..test_integration_candidates import account

    request, attempt, _ = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        status = save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="testing",
            values={"testing_recipient": "another@example.org"},
            expected_version=attempt.version,
        )
        stage_credential(
            request,
            setup_service,
            attempt.attempt_id,
            target="google_workspace",
            candidate=account(),
            expected_version=status.version,
        )
        current = signing.dumps(
            prepare_preview(request, setup_service).binding(), salt=PREVIEW_SALT
        )
        with pytest.raises(ValueError, match="successful test"):
            freeze_setup(request, setup_service, preview_token=current)
    assert not SetupConfigurationIntent.objects.exists()


def test_confirmation_requires_its_own_transaction_and_original_live_attempt(
    setup_service, monkeypatch, tmp_path
):
    """Caller rollback or cancellation cannot silently erase/repurpose a receipt."""
    request, attempt, token = prepared(setup_service, monkeypatch, tmp_path)
    with transaction.atomic(), pytest.raises(StorageInvariantError):
        freeze_setup(request, setup_service, preview_token=token)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
        with pytest.raises(PermissionError):
            freeze_setup(request, setup_service, preview_token=token)


def test_readiness_history_is_not_editable_or_deletable_in_sql(
    setup_service, monkeypatch, tmp_path
):
    """Retained readiness cannot be rewritten to point at another recipient."""
    from django.db import connection

    request, _, token = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        freeze_setup(request, setup_service, preview_token=token)
    with (
        pytest.raises(DatabaseError),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_setup_readiness_binding "
            "SET testing_recipient='other@example.org'"
        )
    with (
        pytest.raises(DatabaseError),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_setup_readiness_binding")
