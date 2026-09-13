"""Ephemeral source handoff with actual worker and isolated-target SQL identities."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from contextlib import contextmanager, nullcontext
from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.backends.signals import connection_created
from django.db.models import F

from parishkit.stewardship.accounts.setup_exchange_models import SetupSourceExchange
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.runtime_grants import runtime_grants
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.setup_exchange import (
    publish_recipient,
    receive_credential,
    relay_pending,
)
from parishkit.stewardship.source.setup_handoff import (
    EphemeralSetupRecipient,
    SetupCredentialScope,
)
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE, staged
from .test_setup_progress_postgresql import bind_load
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def target_login(target="parishsoft", *, reconnect=False):
    """Use the exact provisioned target grants, not a broad test-table bypass."""
    assert target in {"parishsoft", "slack", "google_workspace"}
    role = "pk_stewardship_credential_" + target
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{role}" LOGIN NOINHERIT')

    def restrict_connection(sender, connection, **kwargs):
        """Reconnects after private IO retain the same restricted fixture identity."""
        with connection.cursor() as cursor:
            cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')

    try:
        tables, columns = runtime_grants(
            ServiceRole.CREDENTIAL_INSTALLER, target=target
        )
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            for table, privileges in tables.items():
                cursor.execute(
                    f'GRANT {",".join(sorted(privileges))} ON "{table}" TO "{role}"'
                )
            for table, privileges in columns.items():
                for privilege, names in privileges.items():
                    selected = ",".join(f'"{name}"' for name in sorted(names))
                    cursor.execute(
                        f'GRANT {privilege} ({selected}) ON "{table}" TO "{role}"'
                    )
            cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')
        if reconnect:
            connection_created.connect(restrict_connection, weak=False)
        yield
    finally:
        connection_created.disconnect(restrict_connection)
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')


def preparing(service):
    """Publish a real recipient after original setup and both live claims exist."""
    request, attempt, receipt, private = staged(service)
    task, source = bind_load(attempt.attempt_id)
    scope = SetupCredentialScope(
        attempt.attempt_id,
        receipt.identifier,
        TaskClaim(task.run_id, source.task_fence, source.worker_id),
        source.fence,
    )
    recipient = EphemeralSetupRecipient(scope)
    with task_login(ServiceRole.WORKER, exact=True):
        identifier = publish_recipient(recipient.public)
        assert publish_recipient(recipient.public) == identifier
        assert receive_credential(recipient) is None
    return request, attempt, private, source, recipient, identifier


def test_exact_target_relays_once_to_live_ephemeral_worker(setup_service):
    """Private target decrypts its input; worker opens only the fresh relay envelope."""
    _, _, private, _, recipient, identifier = preparing(setup_service)
    with target_login():
        assert relay_pending(private)
        assert not relay_pending(private)
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT google_subject FROM stewardship_portal_user")
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT session_id FROM stewardship_portal_session")
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT canonical FROM stewardship_source_family")
    row = SetupSourceExchange.objects.get(pk=identifier)
    assert row.replied_at is not None and CANDIDATE.decode() not in row.ciphertext
    with task_login(ServiceRole.WORKER, exact=True):
        assert receive_credential(recipient).value == CANDIDATE
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute("SELECT ciphertext FROM stewardship_setup_sealed_credential")


def test_restarted_worker_cannot_replace_or_open_original_recipient(setup_service):
    """No persisted private key or replacement public key can resume a lost process."""
    _, _, private, _, recipient, _ = preparing(setup_service)
    another = EphemeralSetupRecipient(recipient.public.scope)
    with target_login():
        assert relay_pending(private)
    with task_login(ServiceRole.WORKER, exact=True):
        with pytest.raises(StaleRecordError):
            publish_recipient(another.public)
        with pytest.raises(StaleRecordError):
            receive_credential(another)


@pytest.mark.parametrize("after_reply", [False, True])
def test_cancel_scrubs_replies_and_cannot_resume(setup_service, after_reply):
    """The original browser's cancellation clears even an already delivered envelope."""
    request, attempt, private, _, recipient, identifier = preparing(setup_service)
    if after_reply:
        with target_login():
            assert relay_pending(private)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    row = SetupSourceExchange.objects.get(pk=identifier)
    assert row.ciphertext is None and row.scrubbed_at is not None
    with target_login():
        assert not relay_pending(private)
    with task_login(ServiceRole.WORKER, exact=True), pytest.raises(PermissionError):
        receive_credential(recipient)


def test_lost_source_fence_refuses_delivery_and_consumption(setup_service):
    """A known task/recipient does not outlive its independent source reservation."""
    _, _, private, source, recipient, _ = preparing(setup_service)
    release_source(source)
    with target_login():
        assert not relay_pending(private)
    with task_login(ServiceRole.WORKER, exact=True), pytest.raises(PermissionError):
        receive_credential(recipient)


@pytest.mark.parametrize(
    "mutation",
    [
        {"source_fence": 999},
        {"worker_id": uuid4()},
        {"task_fence": 999},
        {"attempt_id": uuid4()},
        {"credential_id": uuid4()},
        {"public_key": b"x" * 32},
    ],
)
@pytest.mark.parametrize("after_reply", [False, True])
@pytest.mark.parametrize("as_target", [False, True])
def test_sql_rejects_identity_rebinding_before_and_after_reply(
    setup_service, mutation, after_reply, as_target
):
    """Neither an installer nor a schema-owner bypass may rewrite immutable scope."""
    _, _, private, _, _, _ = preparing(setup_service)
    if after_reply:
        with target_login():
            assert relay_pending(private)
    with (
        target_login() if as_target else nullcontext(),
        pytest.raises(DatabaseError) as denied,
        work_transaction(),
    ):
        SetupSourceExchange.objects.update(**mutation, version=F("version") + 1)
    # Both identities can UPDATE the journal, but its immutable trigger must
    # reject rebinding before a foreign-key violation can mask missing guards.
    assert denied.value.__cause__.sqlstate == "23514"


def test_forged_recipient_scope_and_unrelated_target_have_no_authority(setup_service):
    """Recipient UUIDs are correlation values, not authorization credentials."""
    _, _, _, _, recipient, _ = preparing(setup_service)
    invalid = replace(
        recipient.public, scope=replace(recipient.public.scope, source_fence=999)
    )
    with task_login(ServiceRole.WORKER, exact=True), pytest.raises(PermissionError):
        publish_recipient(invalid)
    with target_login("slack"), pytest.raises(DatabaseError), transaction.atomic():
        list(SetupSourceExchange.objects.values_list("ciphertext", flat=True))
