"""Actual web/scheduler isolation and original-login sealed wizard ownership."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.handoff_discovery import publish_handoff
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.sessions import end_admin
from parishkit.stewardship.accounts.setup_credentials import (
    credential_status,
    stage_credential,
)
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential
from parishkit.stewardship.accounts.setup_staging import begin_setup, cancel_setup
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.storage import StaleRecordError

from ..test_integration_candidates import account
from ..test_setup_forms import VALUES
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_expiry_postgresql import sweep
from .test_setup_progress_postgresql import bind_load
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
CANDIDATE = b"synthetic-wizard-key-not-a-live-credential"


def publish(target, *, material=b"s" * 32):
    """Fixture publishes real derived public bytes; no decrypting key enters web."""
    assert target in {"parishsoft", "google_workspace", "slack"}
    private = key(target, material=material)
    role = "pk_stewardship_credential_" + target
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [role])
        assert cursor.fetchone() is None
        cursor.execute(f'CREATE ROLE "{role}" LOGIN NOINHERIT')
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            cursor.execute(
                "GRANT SELECT,INSERT ON stewardship_public_credential_handoff "
                f'TO "{role}"'
            )
            cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')
        publish_handoff(private)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')
    return private


def staged(service, target="parishsoft"):
    """Use real owner services and exact grants for the entire collecting path."""
    private = publish(target)
    with web_login():
        request = login(service)
        attempt = begin_setup(request, service)
        for step in (
            ["mail", "testing"]
            if target == "google_workspace"
            else ["slack"]
            if target == "slack"
            else []
        ):
            values = (
                {"enabled": True, "channel_id": "C123"}
                if step == "slack"
                else VALUES[step]
            )
            attempt = save_section(
                request,
                service,
                attempt.attempt_id,
                step=step,
                values=values,
                expected_version=attempt.version,
            )
        attempt, receipt = stage_credential(
            request,
            service,
            attempt.attempt_id,
            target=target,
            candidate=account() if target == "google_workspace" else CANDIDATE,
            expected_version=attempt.version,
            organization_id=1 if target == "parishsoft" else None,
        )
    return request, attempt, receipt, private


@pytest.mark.parametrize("target", ["parishsoft", "google_workspace", "slack"])
def test_real_web_seals_to_exact_target_without_queuing_live_install(
    setup_service,
    target,
):
    """Only the isolated private key opens staged bytes; receipts expose no secret."""
    request, attempt, receipt, private = staged(setup_service, target)
    row = SetupSealedCredential.objects.get()
    assert private.open(row.pk, row.ciphertext) == (
        account() if target == "google_workspace" else CANDIDATE
    )
    assert CANDIDATE.decode() not in row.ciphertext
    assert not SecretReplacementRequest.objects.exists()
    assert not setup_service.configured()
    with web_login():
        assert credential_status(request, setup_service, attempt.attempt_id) == (
            receipt,
        )
        with pytest.raises(DatabaseError), transaction.atomic():
            SetupSealedCredential.objects.values_list("ciphertext", flat=True).get()
    assert not hasattr(receipt, "ciphertext")


def test_replace_collecting_candidate_uses_exact_attempt_version(setup_service):
    """Concurrent tabs cannot overwrite either a public step or another credential."""
    request, attempt, receipt, private = staged(setup_service)
    with web_login():
        with pytest.raises(StaleRecordError):
            stage_credential(
                request,
                setup_service,
                attempt.attempt_id,
                target="parishsoft",
                candidate=b"stale",
                organization_id=1,
                expected_version=attempt.version - 1,
            )
        changed, result = stage_credential(
            request,
            setup_service,
            attempt.attempt_id,
            target="parishsoft",
            candidate=b"corrected",
            organization_id=2,
            expected_version=attempt.version,
        )
    row = SetupSealedCredential.objects.get()
    assert row.pk == receipt.identifier == result.identifier
    assert result.version == receipt.version + 1
    assert changed.version == attempt.version + 1
    assert private.open(row.pk, row.ciphertext) == b"corrected"
    assert row.settings == {"organization_id": 2}


@pytest.mark.parametrize(
    "target,candidate",
    [
        ("slack", b"x" * 4099),
        ("slack", b"two lines\nhere"),
        ("google_workspace", b"not-json"),
        ("google_workspace", account(token_uri="https://elsewhere.example/token")),
    ],
)
def test_malformed_private_intake_never_replaces_a_staged_candidate(
    setup_service, target, candidate
):
    """Malformed bytes fail locally without a queued check or candidate version."""
    request, attempt, receipt, _ = staged(setup_service, target)
    with web_login(), pytest.raises(ValueError, match="invalid format"):
        stage_credential(
            request,
            setup_service,
            attempt.attempt_id,
            target=target,
            candidate=candidate,
            expected_version=attempt.version,
        )
    assert SetupAttempt.objects.get().version == attempt.version
    assert SetupSealedCredential.objects.get().version == receipt.version


@pytest.mark.parametrize("automatic", [False, True])
def test_original_attempt_terminal_transition_atomically_scrubs(
    setup_service, automatic
):
    """Actual scheduler/web identities can scrub without selecting ciphertext."""
    request, attempt, receipt, _ = staged(setup_service)
    if automatic:
        end_admin(request)
        assert sweep() == 1
        assert sweep() == 0
    else:
        with web_login():
            cancel_setup(request, setup_service, attempt.attempt_id)
    row = SetupSealedCredential.objects.get()
    assert row.pk == receipt.identifier and row.fingerprint == receipt.fingerprint
    assert row.ciphertext is None and row.scrubbed_at is not None
    assert row.settings == {}
    events = AuditEvent.objects.filter(subject_id=row.pk)
    assert set(events.values_list("event_type", flat=True)) == {
        "setup_credential_staged",
        "setup_credential_scrubbed",
    }
    contexts = AuditContext.objects.filter(event_id__in=events).values_list(
        "context", flat=True
    )
    assert all(set(context) == {"version", "outcome"} for context in contexts)
    assert SetupAttempt.objects.get().state == "expired"
    with pytest.raises(DatabaseError), work_transaction():
        SetupSealedCredential.objects.update(
            ciphertext="cannot-repopulate", scrubbed_at=None, version=F("version") + 1
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        SetupSealedCredential.objects.all().delete()


def test_new_login_cannot_adopt_or_retrieve_a_staged_credential(setup_service):
    """Even the same Admin identity cannot cross the original session boundary."""
    _, attempt, _, _ = staged(setup_service)
    with web_login():
        request = login(setup_service)
        with pytest.raises(LookupError):
            credential_status(request, setup_service, attempt.attempt_id)
        with pytest.raises(LookupError):
            stage_credential(
                request,
                setup_service,
                attempt.attempt_id,
                target="parishsoft",
                candidate=b"another-login",
                expected_version=attempt.version,
                organization_id=1,
            )


@pytest.mark.parametrize(
    "mutation",
    [
        {"settings": {"api_key": "not-public"}},
        {"settings": {"organization_id": True}},
        {"settings": {"organization_id": "1"}},
        {"settings": {"organization_id": 0}},
        {"target": "slack"},
        {"attempt_id": uuid4()},
        {"actor_id": None},
        {"ciphertext": ""},
        {"fingerprint": "bad"},
    ],
)
def test_sql_repeats_closed_scope_and_original_ownership(setup_service, mutation):
    """Bypassing Python validation cannot introduce private fields or retarget."""
    staged(setup_service)
    with pytest.raises(DatabaseError), work_transaction():
        SetupSealedCredential.objects.update(**mutation, version=F("version") + 1)


def test_scheduler_cannot_read_or_replace_collecting_ciphertext(setup_service):
    """Scrub-only column grants and the terminal SQL guard remain independent."""
    staged(setup_service)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT ciphertext FROM stewardship_setup_sealed_credential")
        with pytest.raises(DatabaseError), work_transaction():
            SetupSealedCredential.objects.update(
                ciphertext="replacement", version=F("version") + 1
            )


def test_loading_freezes_input_in_python_and_sql(setup_service):
    """A loader never observes a different credential midway through its pages."""
    request, attempt, receipt, _ = staged(setup_service)
    bind_load(attempt.attempt_id)
    version = SetupAttempt.objects.get().version
    with web_login():
        with pytest.raises(PermissionError):
            stage_credential(
                request,
                setup_service,
                attempt.attempt_id,
                target="parishsoft",
                candidate=b"too-late",
                expected_version=version,
                organization_id=1,
            )
        with pytest.raises(DatabaseError), work_transaction():
            SetupSealedCredential.objects.update(
                ciphertext="too-late", version=F("version") + 1
            )
    assert SetupSealedCredential.objects.get().version == receipt.version


@pytest.mark.parametrize(
    "target, organization",
    [
        ("not-a-target", None),
        ("google_workspace", 1),
        ("google_workspace", None),
        ("slack", None),
    ],
)
def test_missing_or_unowned_public_scope_never_accepts_candidate(
    setup_service,
    target,
    organization,
):
    """Declared stored draft settings are prerequisites, not arbitrary POST input."""
    with web_login():
        request = login(setup_service)
        attempt = begin_setup(request, setup_service)
        with pytest.raises(ValueError):
            stage_credential(
                request,
                setup_service,
                attempt.attempt_id,
                target=target,
                candidate=CANDIDATE,
                expected_version=attempt.version,
                organization_id=organization,
            )
        assert not SetupSealedCredential.objects.exists()
