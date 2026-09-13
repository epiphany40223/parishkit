"""Actual isolated-target relay of setup Workspace input to one mail worker."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_mail import begin_submission
from parishkit.stewardship.accounts.setup_mail_exchange import (
    publish_recipient,
    receive_credential,
    relay_pending,
)
from parishkit.stewardship.accounts.setup_mail_exchange_models import SetupMailExchange
from parishkit.stewardship.accounts.setup_mail_handoff import (
    EphemeralMailRecipient,
    MailCredentialScope,
)
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_exchange_postgresql import target_login
from .test_setup_mail_postgresql import claim, prepared
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def preparing(service, monkeypatch, tmp_path):
    """Use actual original-owner intake and a fresh claimed mail SQL identity."""
    request, attempt, _, _, delivery = prepared(service, monkeypatch, tmp_path)
    row = SetupMailDelivery.objects.get(pk=delivery.identifier)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        recipient = EphemeralMailRecipient(
            MailCredentialScope(
                row.pk, row.credential_id, row.credential_version, owner
            )
        )
        exchange_id = publish_recipient(recipient.public)
        assert publish_recipient(recipient.public) == exchange_id
        assert receive_credential(recipient) is None
    private = key("google_workspace", material=b"g" * 32)
    return request, attempt, recipient, private, exchange_id


def test_isolated_workspace_relays_once_without_reading_message_or_family_data(
    setup_service, monkeypatch, tmp_path
):
    """Real encryption and real grants expose only the intended private candidate."""
    _, _, recipient, private, identifier = preparing(
        setup_service, monkeypatch, tmp_path
    )
    with target_login("google_workspace"):
        assert relay_pending(private)
        assert not relay_pending(private)
        for statement in (
            "SELECT mail FROM stewardship_setup_mail_delivery",
            "SELECT canonical FROM stewardship_source_family",
            "SELECT session_id FROM stewardship_portal_session",
        ):
            with (
                pytest.raises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as c,
            ):
                c.execute(statement)
    row = SetupMailExchange.objects.get(pk=identifier)
    assert row.replied_at is not None and "synthetic-private" not in row.ciphertext
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        from ..test_integration_candidates import account

        assert receive_credential(recipient).value == account()
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT ciphertext FROM stewardship_setup_sealed_credential")


def test_restarted_worker_cannot_replace_or_consume_original_recipient(
    setup_service, monkeypatch, tmp_path
):
    """A same-claim fresh process still cannot reconstruct the lost private key."""
    _, _, recipient, private, _ = preparing(setup_service, monkeypatch, tmp_path)
    replacement = EphemeralMailRecipient(recipient.public.scope)
    with target_login("google_workspace"):
        assert relay_pending(private)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        with pytest.raises(StaleRecordError):
            publish_recipient(replacement.public)
        with pytest.raises(StaleRecordError):
            receive_credential(replacement)
        with pytest.raises(StaleRecordError):
            publish_recipient(
                replace(
                    recipient.public,
                    scope=replace(recipient.public.scope, credential_version=999),
                )
            )


@pytest.mark.parametrize("replied", [False, True])
def test_cancel_scrubs_relay_and_prevents_late_repopulation(
    setup_service, monkeypatch, tmp_path, replied
):
    """Original cancellation removes every encrypted reply without retaining a key."""
    request, attempt, recipient, private, identifier = preparing(
        setup_service, monkeypatch, tmp_path
    )
    if replied:
        with target_login("google_workspace"):
            assert relay_pending(private)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT ciphertext FROM stewardship_setup_mail_exchange")
    row = SetupMailExchange.objects.get(pk=identifier)
    assert row.ciphertext is None and row.scrubbed_at is not None
    with target_login("google_workspace"):
        assert not relay_pending(private)
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        pytest.raises(PermissionError),
    ):
        receive_credential(recipient)


def test_submitting_journal_can_no_longer_relay_or_recover_credential(
    setup_service, monkeypatch, tmp_path
):
    """Only the original receiving worker keeps private bytes through submission."""
    _, _, recipient, private, _ = preparing(setup_service, monkeypatch, tmp_path)
    with target_login("google_workspace"):
        assert relay_pending(private)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        receive_credential(recipient)
        begin_submission(
            recipient.public.scope.delivery_id, recipient.public.scope.claim
        )
        with pytest.raises(PermissionError):
            receive_credential(recipient)


@pytest.mark.parametrize(
    "mutation",
    [
        {"delivery_id": uuid4()},
        {"run_id": uuid4()},
        {"task_fence": 99},
        {"worker_id": uuid4()},
        {"public_key": b"x" * 32},
    ],
)
def test_sql_cannot_rebind_mail_exchange(
    setup_service, monkeypatch, tmp_path, mutation
):
    """Even a schema-owner update cannot rewrite retained original claim metadata."""
    preparing(setup_service, monkeypatch, tmp_path)
    with (
        pytest.raises(
            DatabaseError, match="identity|binding|original ownership|reply once"
        ),
        work_transaction(),
    ):
        SetupMailExchange.objects.update(**mutation, version=F("version") + 1)


def test_general_worker_and_other_targets_have_no_mail_relay_access(
    setup_service, monkeypatch, tmp_path
):
    """A general task consumer or ParishSoft installer cannot read Workspace replies."""
    preparing(setup_service, monkeypatch, tmp_path)
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        list(SetupMailExchange.objects.values_list("ciphertext", flat=True))
    with target_login("parishsoft"), pytest.raises(DatabaseError), transaction.atomic():
        list(SetupMailExchange.objects.values_list("ciphertext", flat=True))
