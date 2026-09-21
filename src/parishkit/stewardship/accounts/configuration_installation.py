"""Internal PostgreSQL materializer and resumable configuration/policy installer.

No service, route, or command exposes this implementation. Actor UUIDs are
attribution, not authorization, with one exception: a login-policy change
confirmed by a portal user is applied only while that user is still an
Administrator, rechecked here under the installation lock and again inside the
activation transaction with the identity row share-locked. Online Admin
admission, credential evidence, offline bootstrap/recovery interlocks, and
service grants/mounts remain required before exposure. Versioned policy is
supported internally; operational secret, campaign and mode changes are not.
Request IDs are references, never credentials.
"""

import re
from contextlib import contextmanager
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StorageInvariantError

from .authority import apply_version, recover_active
from .configuration_errors import ConfigurationReadinessUnavailable
from .configuration_requests import _identities, _status
from .configuration_snapshots import is_prepared, prepare_snapshot
from .installation_lock import installation_lock
from .request_admission import intake_base
from .request_models import ConfigurationChangeRequest, ConfigurationRequestCheckpoint
from .request_patch import MANUAL_POLICY_REQUEST_SCHEMAS, build_candidate
from .runtime_models import ConfigurationActivation, SystemConfiguration


class DatabaseMaterializer:
    """Match the existing file protocol with atomic durable PostgreSQL effects.

    Bound to one immutable request, or to empty-deployment root preparation.
    Nested use of this same instance shares its lock, never a second SQL claim.
    """

    def __init__(
        self,
        store,
        *,
        actor_id,
        correlation_id,
        request=None,
        testing_recipient=None,
        deployment_id=None,
        admit_campaign=None,
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
        if deployment_id is not None and not isinstance(deployment_id, UUID):
            raise ConfigError("An explicit deployment UUID is required.")
        self.deployment_id = deployment_id
        self.admit_campaign = admit_campaign
        # A login-policy request's activation-time actor recheck, run inside
        # the activation transaction with the identity row share-locked.
        self.admit_actor = None
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
            self._record(state, failure_code)

    def _record(self, state, failure_code):
        """Append the checkpoint inside the caller's transaction, request row locked."""
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
        from .branding_validation import validate_installation as validate_branding
        from .integration_selection import validate_installation as validate_credentials
        from .ministry_activity import validate_installation as validate_activity

        validate_activity(version.document())
        validate_branding(version.document(), actor_id=self.actor_id)
        validate_credentials(version.document())
        from parishkit.stewardship.campaigns.admission import validate_installation

        validate_installation(
            version.document(), request_id=self.request.pk if self.request else None
        )
        self._campaign_admission()
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
        refused = False
        with transaction.atomic(durable=True):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736220, 1])
            runtime = SystemConfiguration.objects.select_for_update().first()
            self._campaign_admission()
            if self.admit_actor is not None and not self.admit_actor():
                # The refusal is committed under the very lock that judged it,
                # so no crash before the YAML restore can let a later pass, the
                # actor authorized again by then, judge the request afresh.
                self._record("failed", "actor_unauthorized")
                refused = True
            else:
                self._apply(runtime, selected)
        if refused:
            raise ActorUnauthorized("The confirming Administrator is no longer one.")

    def _apply(self, runtime, selected):
        """Commit the pointer, request and audit effects inside the activation."""
        if runtime is None:
            if self.request is not None or self.testing_recipient is None:
                raise ConfigError("Runtime configuration is not initialized.")
            runtime = SystemConfiguration.objects.create(
                **({"id": self.deployment_id} if self.deployment_id else {}),
                testing_recipient=self.testing_recipient,
                actor_id=self.actor_id,
                correlation_id=self.correlation_id,
            )
        activation = ConfigurationActivation.objects.create(
            configuration_id=selected.version_id,
            predecessor_id=runtime.active_configuration_id,
            sequence=runtime.configuration_sequence,
            request=self.request,
            actor_id=self.actor_id,
            correlation_id=self.correlation_id,
        )
        from .chair_reconciliation import reconcile_configuration_chairs

        reconcile_configuration_chairs(activation)
        # SQL inserts Applied, safe audit, and the runtime pointer in this
        # same transaction. A failure in any effect rolls them all back.

    def _campaign_admission(self):
        """Exceptional edits require fresh owning proof, even after YAML selection."""
        if self.request is None:
            return
        from parishkit.stewardship.campaigns.configuration_intents import verify_intent

        verify_intent(self.request.pk, self.admit_campaign)

    def restore_aborted_candidate(self):
        """Recover an exact durable abort, preserving every applied configuration.

        Abort attribution lives on its immutable decision; technical request
        checkpoints retain the original request actor even when another current
        Admin resolves it. The caller must recheck that Admin's authority first.
        """
        from parishkit.stewardship.campaigns.models import CampaignConfigurationAbort

        from .setup_models import SetupConfigurationAbort

        self._check()
        request = self.request
        journal = (
            SetupConfigurationAbort
            if request is not None
            and request.request_schema == "initial-setup-patch-v7"
            else CampaignConfigurationAbort
        )
        if (
            request is None
            or not journal.objects.filter(intent__request=request).exists()
        ):
            raise StorageInvariantError(
                "Exceptional cancellation requires its journal."
            )
        status = _status(request)
        if status.state == "failed" and status.failure_code == "invalid_candidate":
            return status
        runtime = SystemConfiguration.objects.get()
        if ConfigurationActivation.objects.filter(request=request).exists():
            raise StorageInvariantError("Cannot cancel an applied configuration.")
        selected = self.store.active()
        if runtime.active_configuration_id != request.base_id:
            # The journal prevented this candidate from ever applying. Another
            # valid install may advance after the abort crash; finish only its
            # receipt, without rewinding that newer, coherent authority.
            if (
                selected is None
                or selected.version_id != runtime.active_configuration_id
            ):
                raise StorageInvariantError(
                    "Exceptional cancellation has unrelated YAML authority."
                )
            self.checkpoint("failed", failure_code="invalid_candidate")
            return _status(request)
        if selected is None or selected.version_id not in {
            request.base_id,
            request.candidate_version_id,
        }:
            raise StorageInvariantError(
                "Exceptional cancellation has unrelated YAML authority."
            )
        self._check()
        if selected.version_id != request.base_id:
            self.store.select(self.store.read_version(request.base_id))
        self._check()
        self.checkpoint("failed", failure_code="invalid_candidate")
        return _status(request)


def prepare_initial_configuration(
    store, version, *, testing_recipient, actor_id, correlation_id, deployment_id=None
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
        deployment_id=deployment_id,
    )
    with materializer.lock():
        runtime = SystemConfiguration.objects.first()
        if runtime is not None and (
            runtime.testing_recipient != testing_recipient
            or (deployment_id is not None and runtime.pk != deployment_id)
        ):
            raise ConfigError(
                "Existing runtime configuration does not match initialization."
            )
        selected = store.active()
        if selected is not None and selected.digest == version.digest:
            if runtime is None:
                # Offline pre-migration bootstrap may have selected the exact
                # root before application tables existed. Only this confirmed
                # empty-runtime path may prepare it before ordinary recovery.
                materializer.prepare(version)
            recover_active(store, materializer)
        apply_version(store, materializer, version)


def install_request(store, *, request_id, correlation_id, admit_campaign=None):
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
    return _install_request(
        store,
        request=request,
        correlation_id=correlation_id,
        admit_campaign=admit_campaign,
    )


class ActorUnauthorized(Exception):
    """The Administrator who confirmed a login-policy change no longer is one."""


def actor_authorized(request, *, lock):
    """Whether the confirming portal user still holds `manage_users` under the base.

    Evaluated against the policy being replaced, with the same evaluator a
    sign-in uses. With `lock`, inside the activation transaction, the identity
    row is read FOR SHARE, so a disable or an address refresh cannot commit
    between this read and the activation it protects; the configuration
    installer holds the one column-level UPDATE grant PostgreSQL requires for
    that share lock, the same way the runtime roles lock their own rows. The
    lock order stays the one every holder uses, work lock then runtime row then
    identity row, and an identity writer never waits on the work lock, so no
    cycle forms. An actor that is not a portal user, such as operator recovery
    or a system producer, is admitted by its own boundary and is not judged
    here; offline roles install such requests alone and need no such grant.
    """
    from .policy import Capability, Principal, allows, confirmed_seeded, resolve_roles

    if request.actor_id is None:
        return True
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT email, hosted_domain, disabled FROM stewardship_portal_user "
            "WHERE id=%s" + (" FOR SHARE" if lock else ""),
            [request.actor_id],
        )
        row = cursor.fetchone()
    if row is None:
        return True
    email, hosted_domain, disabled = row
    if disabled:
        return False
    records = [
        record
        for record in request.base.canonical_document["sections"].get("login_rules", [])
        if record["values"].get("email") == email
        or record["values"].get("domain") == email.rsplit("@", 1)[1]
    ]
    roles, ministries = resolve_roles(
        email, hosted_domain, records, confirmed_seeded(request.base, email=email)
    )
    return allows(
        Principal(request.actor_id, roles, ministries), Capability.MANAGE_USERS
    )


def _install_request(store, *, request, correlation_id, admit_campaign=None):
    """Shared checkpoint engine after its distinct online/offline authority boundary."""
    materializer = DatabaseMaterializer(
        store,
        actor_id=request.actor_id,
        correlation_id=correlation_id,
        request=request,
        admit_campaign=admit_campaign,
    )
    with materializer.lock():
        current = _status(request)
        # First, before the abort recoveries, which refuse a manifest naming
        # neither this request's base nor its candidate.
        _restore_refused(store, materializer._check)
        from .setup_installation import recover_setup_abort

        setup_abort = recover_setup_abort(materializer)
        if setup_abort is not None:
            return setup_abort
        from parishkit.stewardship.campaigns.configuration_intents import (
            recover_configuration_abort,
        )

        aborted = recover_configuration_abort(materializer)
        if aborted is not None:
            return aborted
        if current.state in {"cancelled", "failed", "applied"}:
            from parishkit.stewardship.campaigns.configuration_intents import (
                verify_intent_receipt,
            )

            verify_intent_receipt(request.pk, admit_campaign)
            return current
        if request.request_schema == "initial-setup-patch-v7":
            from .setup_preparation import prepare_setup_configuration

            return prepare_setup_configuration(materializer)
        # A login-policy change, including a Ministry assignment, confirmed by
        # a portal user is applied only while that Administrator is still one
        # under the policy being replaced. The activation transaction rechecks
        # it with the identity row share-locked, on the ordinary path and on
        # the recovery path alike, and commits the refusal itself, so no crash
        # around the YAML restore can let a later pass activate the request.
        policy_change = any(
            type(item) is dict and item.get("section") == "login_rules"
            for item in request.patch
        )
        if policy_change:
            materializer.admit_actor = lambda: actor_authorized(request, lock=True)
        selected = store.active()
        active_digest = materializer.active_digest()
        if active_digest is None:
            raise ConfigError("Runtime configuration is not initialized.")
        yaml_digest = selected.digest if selected else None
        if yaml_digest != active_digest:
            if yaml_digest != request.candidate_digest:
                raise ConfigError("Another configuration requires recovery first.")
            try:
                recover_active(store, materializer)
            except ActorUnauthorized:
                _restore_refused(store, materializer._check)
            return _status(request)

        failure_code = ""
        intent = None
        if active_digest != request.base.digest:
            failure_code = "stale_base"
        elif policy_change and not actor_authorized(request, lock=False):
            # A disabled identity or a rule revoked since confirmation leaves
            # the base digest unchanged, so the stale-base check alone would
            # still activate their grant. Refused here, before any file is
            # written; the recheck at activation catches a change that lands
            # between the two. Outside the candidate validation, so an error
            # judging the actor is never recorded as a malformed candidate.
            failure_code = "actor_unauthorized"
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
                if request.request_schema in MANUAL_POLICY_REQUEST_SCHEMAS:
                    from .policy_schema import validate_manual_operation

                    validate_manual_operation(
                        base.document()["sections"].get("login_rules", []),
                        intent.candidate.document()["sections"].get("login_rules", []),
                        request.pk,
                    )
                from .request_admission import check_historical_additions

                check_historical_additions(request.base_id, request.patch)
                from .ministry_activity import (
                    validate_installation as validate_activity,
                )

                validate_activity(intent.candidate.document())
                from parishkit.stewardship.campaigns.admission import (
                    validate_installation,
                )

                validate_installation(
                    intent.candidate.document(), request_id=request.pk
                )
                if (
                    intent.candidate.digest != request.candidate_digest
                    or intent.payload_fingerprint != request.payload_fingerprint
                ):
                    raise ConfigError("Configuration request metadata is inconsistent.")
                from .branding_validation import (
                    validate_installation as validate_branding,
                )

                validate_branding(
                    intent.candidate.document(), actor_id=request.actor_id
                )
                from .integration_selection import (
                    validate_installation as validate_credentials,
                )

                validate_credentials(intent.candidate.document())
            except ConfigurationReadinessUnavailable:
                # Retain the exact durable request for a later installer pass;
                # unfinished normalization/replacement is not malformed intent.
                raise
            except ConfigError:
                failure_code = "invalid_candidate"
            if not failure_code:
                # Owning authorization/readiness refusals are not malformed
                # candidate content. Leave their durable intent retryable.
                materializer._campaign_admission()

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
        try:
            apply_version(store, materializer, intent.candidate)
        except ActorUnauthorized:
            _restore_refused(store, materializer._check)
        return _status(request)


def restore_refused(store):
    """Finish, with no request in hand, a refusal's restore a crash cut short.

    The installer queue selects only resumable requests and a refused request
    is terminal, so an idle installer pass runs this; otherwise the deployment
    would stay incoherent, every Admin page refused and no new request able
    to be recorded, until an operator intervened.
    """
    with installation_lock() as guard:
        _restore_refused(store, guard.check)


def _restore_refused(store, check):
    """Select the base YAML again after an activation refused its actor.

    The refusal is recorded inside the activation transaction, before the
    restore, so a crash between the two leaves a failed request's candidate
    selected over an unchanged base. Every install, and every idle installer
    pass, finishes that restore, as an exceptional abort's recovery does;
    until then no other request can proceed, since its base is not what the
    file names. `check` is the installation lock's connection check.
    """
    check()
    selected = store.active()
    active_digest = SystemConfiguration.objects.values_list(
        "active_configuration__digest", flat=True
    ).first()
    if selected is None or active_digest is None or selected.digest == active_digest:
        return
    for refused in ConfigurationChangeRequest.objects.filter(
        candidate_version_id=selected.version_id, base__digest=active_digest
    ):
        status = _status(refused)
        if status.state == "failed" and status.failure_code == "actor_unauthorized":
            store.select(store.read_version(refused.base_id))
            check()
            return


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
