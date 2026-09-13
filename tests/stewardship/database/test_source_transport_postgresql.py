"""HTTP admission closes SQL and reserves the exact maintained source fence."""

from datetime import timedelta

import pytest
from django.db import connection, transaction

from parishkit import parishsoft_transport
from parishkit.parishsoft import DEFAULT_API_BASE_URL
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.credentials import SourceCredential
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.transport import source_session
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import add_draft
from .test_source_attempts_postgresql import setup, stage

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(tmp_path):
    """Create real configured request/task/source ownership without any provider."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    credential, execution, lease, store, version, actor = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    session = source_session(
        execution,
        lease,
        attempt_id=attempt.pk,
        credential=credential,
    )
    return execution, lease, session, store, version, actor


def test_each_http_attempt_reserves_read_drain_margin_and_closes_sql(
    owner, monkeypatch
):
    """No main-thread connection or transaction survives into the provider wait."""
    execution, lease, session, *_ = owner
    calls = []

    def exchange(payload, **kwargs):
        """Observe the real SQL boundary at the fake provider exchange."""
        assert connection.connection is None
        assert not connection.in_atomic_block
        calls.append(kwargs["seconds"])
        return b"200\n[]"

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    with maintain_execution(execution):
        with execution.maintain_source(lease):
            for timeout in (30, 40):
                assert (
                    session.get(
                        DEFAULT_API_BASE_URL + "/families/change/list", timeout=timeout
                    ).json()
                    == []
                )
                saved = SourceMutationLease.objects.get()
                assert saved.external_deadline >= saved.heartbeat_at + timedelta(
                    seconds=timeout + 5 + 15
                )
        with execution.effect():
            release_source(lease)
    assert calls == [30, 40]


def test_transport_never_closes_caller_transaction_or_starts_http_inside_it(
    owner, monkeypatch
):
    """A wrongly composed caller fails instead of corrupting its outer transaction."""
    execution, lease, session, *_ = owner
    calls = []
    monkeypatch.setattr(
        parishsoft_transport, "_exchange", lambda *args, **kwargs: calls.append(1)
    )
    with (
        maintain_execution(execution),
        execution.maintain_source(lease),
        transaction.atomic(),
    ):
        with pytest.raises(StorageInvariantError, match="inside a transaction"):
            session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
        assert connection.in_atomic_block and not connection.needs_rollback
    assert not calls
    assert SourceMutationLease.objects.get().external_deadline is None


def test_transport_requires_source_lease_attached_to_live_renewer(owner, monkeypatch):
    """A still-valid SQL lease alone does not establish ongoing heartbeat coverage."""
    execution, _, session, *_ = owner
    calls = []
    monkeypatch.setattr(
        parishsoft_transport, "_exchange", lambda *args, **kwargs: calls.append(1)
    )
    with (
        maintain_execution(execution),
        pytest.raises(StorageInvariantError, match="maintained"),
    ):
        session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
    assert not calls
    assert SourceMutationLease.objects.get().external_deadline is None


def test_changed_campaign_denies_the_next_read_before_reserving_or_http(
    owner, monkeypatch
):
    """Per-attempt admission is fresh even when the Session object is reused."""
    execution, lease, session, store, version, actor = owner
    calls = []

    def exchange(*args, **kwargs):
        """Return a complete empty indication array, never real parish data."""
        calls.append(1)
        return b"200\n[]"

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    with maintain_execution(execution), execution.maintain_source(lease):
        session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
        before = SourceMutationLease.objects.get().external_deadline
        add_draft(store, version, actor)
        with pytest.raises(PermissionError):
            session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
        assert connection.connection is None
    assert len(calls) == 1
    assert SourceMutationLease.objects.get().external_deadline == before


def test_changed_session_key_cannot_use_another_keys_attempt(owner, monkeypatch):
    """A reused Session may not drift from the exact loaded-credential receipt."""
    execution, lease, session, *_ = owner
    calls = []
    monkeypatch.setattr(
        parishsoft_transport, "_exchange", lambda *a, **kw: calls.append(1)
    )
    session.headers["x-api-key"] = "DIFFERENT-SYNTHETIC-KEY"
    with maintain_execution(execution), execution.maintain_source(lease):
        with pytest.raises(PermissionError, match="credential"):
            session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
        assert connection.connection is None
    assert not calls and SourceMutationLease.objects.get().external_deadline is None


def test_validated_attempt_cannot_resume_reading_provider(owner, monkeypatch):
    """Validation closes an observation; a later read needs a fresh claim/manifest."""
    from parishkit.stewardship.source.refresh_models import SourceRefreshAttempt

    execution, lease, session, *_ = owner
    stage(SourceRefreshAttempt.objects.get(), execution, lease)
    calls = []
    monkeypatch.setattr(
        parishsoft_transport, "_exchange", lambda *a, **kw: calls.append(1)
    )
    with (
        maintain_execution(execution),
        execution.maintain_source(lease),
        pytest.raises(PermissionError, match="stale"),
    ):
        session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
    assert not calls and SourceMutationLease.objects.get().external_deadline is None


def test_different_loaded_bytes_cannot_borrow_an_attempt(owner, monkeypatch):
    """Header text alone cannot replace the installed exact-byte key receipt."""
    from parishkit.stewardship.source.refresh_models import SourceRefreshAttempt

    execution, lease, _, *_ = owner
    # Same HTTP key, different installed file bytes and therefore receipt.
    session = source_session(
        execution,
        lease,
        attempt_id=SourceRefreshAttempt.objects.get().pk,
        credential=SourceCredential(b"SYNTHETIC-PRIVATE-KEY\n"),
    )
    calls = []
    monkeypatch.setattr(
        parishsoft_transport, "_exchange", lambda *a, **kw: calls.append(1)
    )
    with (
        maintain_execution(execution),
        execution.maintain_source(lease),
        pytest.raises(PermissionError, match="credential"),
    ):
        session.get(DEFAULT_API_BASE_URL + "/families/change/list", timeout=30)
    assert not calls and SourceMutationLease.objects.get().external_deadline is None
