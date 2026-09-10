"""Internal PostgreSQL materializer and resumable configuration/policy installer.

No service, route, or command exposes this implementation. Actor UUIDs are
attribution, not authorization. Online Admin admission, credential evidence,
offline bootstrap/recovery interlocks, and service grants/mounts remain required
before exposure. Versioned policy is supported internally; operational secret,
campaign and mode changes are not. Request IDs are references, never credentials.
"""

import re
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StorageInvariantError

from .authority import apply_version, recover_active
from .configuration_requests import _identities, _status
from .configuration_snapshots import is_prepared, prepare_snapshot
from .installation_lock import installation_lock
from .request_admission import intake_base
from .request_models import ConfigurationChangeRequest, ConfigurationRequestCheckpoint
from .request_patch import build_candidate
from .runtime_models import ConfigurationActivation, SystemConfiguration


class DatabaseMaterializer:
    """Match the existing file protocol with atomic durable PostgreSQL effects.

    Bound to one immutable request, or to empty-deployment root preparation.
    Nested use of this same instance shares its lock, never a second SQL claim.
    """

    def __init__(
        self, store, *, actor_id, correlation_id, request=None, testing_recipient=None
    ):
        """Bind one request, or an initial recipient committed only at activation."""
        if request is not None and request.authority == "operator_recovery":
            _identities(correlation_id)
            if actor_id is not None or request.actor_id is not None:
                raise ConfigError("Offline recovery cannot impersonate a portal actor.")
        else:
            _identities(actor_id, correlation_id)
        self.store, self.actor_id, self.correlation_id = store, actor_id, correlation_id
        self.request = request
        self.testing_recipient = testing_recipient
        self._guard = None

    @contextmanager
    def lock(self):
        """Pin one connection throughout file writes and separate durable commits."""
        if self._guard is not None:
            self._guard.check()
            yield
            return
        with installation_lock() as guard:
            self._guard = guard
            try:
                yield
            finally:
                self._guard = None

    def _check(self):
        """Reject uncoordinated calls and connections lost between durable steps."""
        if self._guard is None:
            raise StorageInvariantError("Configuration installation requires its lock.")
        self._guard.check()

    def active_digest(self):
        """Return runtime's protected pointer, never the newest prepared snapshot."""
        self._check()
        digest = SystemConfiguration.objects.values_list(
            "active_configuration__digest", flat=True
        ).first()
        self._check()
        return digest

    def is_prepared(self, digest):
        """Verify the full immutable canonical/projection history before activation."""
        self._check()
        prepared = is_prepared(digest)
        self._check()
        return prepared

    def checkpoint(self, state, *, failure_code=""):
        """Append a durable state with atomic safe audit; exact repeats do nothing."""
        self._check()
        if self.request is None:
            return
        with transaction.atomic(durable=True):
            request = ConfigurationChangeRequest.objects.select_for_update().get(
                pk=self.request.pk
            )
            current = _status(request)
            if current.state == state:
                return
            ConfigurationRequestCheckpoint.objects.create(
                request=request,
                sequence=current.sequence + 1,
                state=state,
                failure_code=failure_code,
                actor_id=self.actor_id,
                correlation_id=self.correlation_id,
            )

    def _candidate(self, version):
        """No requestless successor or mismatched request can select a manifest."""
        if self.request is None:
            if version.predecessor_digest is not None:
                raise ConfigError("Only the initial configuration may omit a request.")
        elif (
            version.version_id != self.request.candidate_version_id
            or version.digest != self.request.candidate_digest
            or self.actor_id != self.request.actor_id
        ):
            raise ConfigError("Configuration candidate does not match its request.")

    def prepare(self, version):
        """Commit the snapshot before its checkpoint; retry an intervening crash."""
        self._check()
        self._candidate(version)
        prepare_snapshot(
            version, actor_id=self.actor_id, correlation_id=self.correlation_id
        )
        self.checkpoint("prepared")

    def activate(self, digest):
        """Require exact selected YAML, then commit pointer/request/audit together."""
        self._check()
        selected = self.store.active()
        if (
            selected is None
            or selected.digest != digest
            or not self.is_prepared(digest)
        ):
            raise ConfigError("Activation requires exact prepared YAML authority.")
        self._candidate(selected)
        current = self.active_digest()
        if current == digest:
            return
        if selected.predecessor_digest != current:
            raise ConfigError(
                "Activation cannot skip or roll back configuration history."
            )
        self.checkpoint("yaml_activated")
        self._check()
        with transaction.atomic(durable=True):
            runtime = SystemConfiguration.objects.select_for_update().first()
            if runtime is None:
                if self.request is not None or self.testing_recipient is None:
                    raise ConfigError("Runtime configuration is not initialized.")
                runtime = SystemConfiguration.objects.create(
                    testing_recipient=self.testing_recipient,
                    actor_id=self.actor_id,
                    correlation_id=self.correlation_id,
                )
            ConfigurationActivation.objects.create(
                configuration_id=selected.version_id,
                predecessor_id=runtime.active_configuration_id,
                sequence=runtime.version,
                request=self.request,
                actor_id=self.actor_id,
                correlation_id=self.correlation_id,
            )
            # SQL inserts Applied, safe audit, and the runtime pointer in this
            # same transaction. A failure in any effect rolls them all back.


def prepare_initial_configuration(
    store, version, *, testing_recipient, actor_id, correlation_id
):
    """Internal empty-runtime setup, not the operational bootstrap command.

    The recipient is database runtime state, not inserted into parish YAML.
    Matching interrupted initialization is retryable; a configured deployment
    cannot use this primitive to replace its recipient or initial authority.
    """
    if (
        type(testing_recipient) is not str
        or len(testing_recipient) > 254
        or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", testing_recipient) is None
    ):
        raise ConfigError("A valid Testing recipient is required.")
    try:
        validate_email(testing_recipient)
    except ValidationError:
        raise ConfigError("A valid Testing recipient is required.") from None
    if version.predecessor_digest is not None:
        raise ConfigError("Initialization requires a root configuration.")
    materializer = DatabaseMaterializer(
        store,
        actor_id=actor_id,
        correlation_id=correlation_id,
        testing_recipient=testing_recipient,
    )
    with materializer.lock():
        runtime = SystemConfiguration.objects.first()
        if runtime is not None and runtime.testing_recipient != testing_recipient:
            raise ConfigError(
                "Existing runtime configuration does not match initialization."
            )
        selected = store.active()
        if selected is not None and selected.digest == version.digest:
            recover_active(store, materializer)
        apply_version(store, materializer, version)


def install_request(store, *, request_id, correlation_id):
    """Resume an intent without claiming work that cannot yet be admitted.

    Operational preflight errors leave staged requests cancellable. Once YAML
    has been selected, recovery completes the exact prepared candidate instead
    of discarding it. No arbitrary exception text enters checkpoints or audit.
    """
    _identities(request_id, correlation_id)
    request = (
        ConfigurationChangeRequest.objects.select_related("base")
        .filter(pk=request_id)
        .first()
    )
    if request is None:
        raise LookupError("Configuration request is unavailable.")
    if request.authority != "admin":
        raise ConfigError("Operator recovery requires the offline workflow.")
    return _install_request(store, request=request, correlation_id=correlation_id)


def _install_request(store, *, request, correlation_id):
    """Shared checkpoint engine after its distinct online/offline authority boundary."""
    materializer = DatabaseMaterializer(
        store, actor_id=request.actor_id, correlation_id=correlation_id, request=request
    )
    with materializer.lock():
        current = _status(request)
        if current.state in {"cancelled", "failed", "applied"}:
            return current
        selected = store.active()
        active_digest = materializer.active_digest()
        if active_digest is None:
            raise ConfigError("Runtime configuration is not initialized.")
        yaml_digest = selected.digest if selected else None
        if yaml_digest != active_digest:
            if yaml_digest != request.candidate_digest:
                raise ConfigError("Another configuration requires recovery first.")
            recover_active(store, materializer)
            return _status(request)

        failure_code = ""
        intent = None
        if active_digest != request.base.digest:
            failure_code = "stale_base"
        else:
            # Active-base damage is deployment state, not invalid user intent.
            # Schema-environment failures likewise propagate before claiming.
            _, base = intake_base(request.base.digest)
            try:
                intent = build_candidate(
                    base,
                    request.patch,
                    candidate_id=request.candidate_version_id,
                    request_schema=request.request_schema,
                )
                if request.request_schema == "foundation-policy-patch-v2":
                    from .policy_schema import validate_manual_operation

                    validate_manual_operation(
                        base.document()["sections"].get("login_rules", []),
                        intent.candidate.document()["sections"].get("login_rules", []),
                        request.pk,
                    )
                from .request_admission import check_historical_additions

                check_historical_additions(request.base_id, request.patch)
                if (
                    intent.candidate.digest != request.candidate_digest
                    or intent.payload_fingerprint != request.payload_fingerprint
                ):
                    raise ConfigError("Configuration request metadata is inconsistent.")
            except ConfigError:
                failure_code = "invalid_candidate"

        if current.state == "staged":
            # Recheck cancellation under the row lock after read-only preflight.
            with transaction.atomic(durable=True):
                locked = ConfigurationChangeRequest.objects.select_for_update().get(
                    pk=request.pk
                )
                current = _status(locked)
                if current.state == "cancelled":
                    return current
                ConfigurationRequestCheckpoint.objects.create(
                    request=locked,
                    sequence=current.sequence + 1,
                    state="validating",
                    actor_id=request.actor_id,
                    correlation_id=correlation_id,
                )
        if failure_code:
            materializer.checkpoint("failed", failure_code=failure_code)
            return _status(request)
        apply_version(store, materializer, intent.candidate)
        return _status(request)


def coherent_configuration(store):
    """Check file/DB agreement, not whole-application readiness or authorization.

    Two manifest reads detect a concurrent selection. Callers performing a
    mutation must also hold their owning workflow's admission/serialization
    guard; a successful point-in-time read is not a reusable readiness token.
    """
    selected = store.active()
    runtime = SystemConfiguration.objects.first()
    if (
        selected is None
        or runtime is None
        or runtime.active_configuration_id != selected.version_id
    ):
        raise ConfigError("Configuration is incomplete or requires recovery.")
    snapshot, version = intake_base(selected.digest)
    if (
        snapshot.pk != runtime.active_configuration_id
        or version != selected
        or store.manifest_reference() != (selected.version_id, selected.digest)
    ):
        raise ConfigError("Configuration is incomplete or requires recovery.")
    # Reuse the exact projection instance just verified, including its prefetch
    # cache. Downstream policy reads must not reload the same immutable corpus.
    runtime.active_configuration = snapshot
    return runtime
