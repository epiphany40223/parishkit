"""Internal offline recovery protocol; no web, queue or CLI exposure.

OPS-04 must supply a real offline/startup interlock and operator confirmation
UI before exposing this service. The required trusted context manager must
verify that all online services are stopped and prevent concurrent startup for
its full duration. A plain UUID, boolean or ordinary Admin identity is not that
authority. Tests supply only a synthetic interlock over disposable storage.
"""

from uuid import UUID, uuid4

from django.db import transaction

from parishkit.config import ConfigError

from .configuration_installation import _install_request, coherent_configuration
from .configuration_requests import _own_transaction, _status
from .policy_schema import normalized_email
from .request_admission import intake_base
from .request_models import ConfigurationChangeRequest
from .request_patch import build_candidate
from .runtime_models import SystemConfiguration


def _text(value, limit):
    """Retain required operator attribution without accepting control characters."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= limit
        or value != value.strip()
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        raise ConfigError("Explicit operator name and reason are required.")


def _patch(version, email, operation_id):
    """Generate exactly one manual Admin addition while preserving other origins."""
    rule = next(
        (
            record
            for record in version.document()["sections"].get("login_rules", [])
            if record["values"]["kind"] == "address"
            and record["values"]["email"] == email
        ),
        None,
    )
    manual = {"manual": str(operation_id)}
    if rule is None:
        return [
            {
                "operation": "add",
                "section": "login_rules",
                "id": str(uuid4()),
                "values": {
                    "kind": "address",
                    "email": email,
                    "roles": ["administrator"],
                    "creation_origin": "manual",
                    "creation_operation": str(operation_id),
                    "grants": {"administrator": manual},
                },
            }
        ]
    values = rule["values"]
    if "administrator" in values["roles"]:
        raise ConfigError("The target already has an explicit Administrator grant.")
    return [
        {
            "operation": "update",
            "section": "login_rules",
            "id": rule["id"],
            "values": {
                "roles": sorted([*values["roles"], "administrator"]),
                "grants": values["grants"] | {"administrator": manual},
            },
        }
    ]


def recover_admin(
    store,
    *,
    operation_id,
    operator_name,
    reason,
    deployment_id,
    target_email,
    confirmed_email,
    correlation_id,
    offline_interlock,
):
    """Resume one confirmed offline operation through the ordinary checkpoint engine.

    Interlock acquisition is repeated even for a completed replay. Metadata is
    checked before returning an existing operation, and unresolved unrelated
    YAML/DB disagreement is never overwritten. No provider call is made here.

    Only state='applied' is success. A failed/cancelled receipt is terminal and
    requires diagnosis and a newly confirmed operation ID; replay preserves
    that receipt, never silently retries a different intent. OPS-04 must inspect
    state and failure_code before reporting recovery success.
    """
    if any(
        not isinstance(value, UUID)
        for value in (operation_id, deployment_id, correlation_id)
    ):
        raise TypeError(
            "Explicit operation, deployment and correlation UUIDs are required."
        )
    _text(operator_name, 254)
    _text(reason, 1024)
    email = normalized_email(target_email)
    if normalized_email(confirmed_email) != email:
        raise ConfigError("Recovery target confirmation does not match.")
    if not callable(offline_interlock):
        raise ConfigError("An offline interlock is required.")
    _own_transaction()
    with offline_interlock():
        try:
            runtime = SystemConfiguration.objects.get()
        except (
            SystemConfiguration.DoesNotExist,
            SystemConfiguration.MultipleObjectsReturned,
        ):
            raise ConfigError("Recovery requires one initialized deployment.") from None
        if runtime.pk != deployment_id:
            raise ConfigError("The confirmed deployment does not match.")
        request = ConfigurationChangeRequest.objects.filter(
            authority="operator_recovery", request_key=operation_id
        ).first()
        if request is not None:
            if (
                request.operator_name,
                request.operator_reason,
                request.confirmed_deployment_id,
                request.recovery_target,
            ) != (operator_name, reason, deployment_id, email):
                raise ConfigError(
                    "Recovery operation is already bound to another intent."
                )
        else:
            runtime = coherent_configuration(store)
            base, version = intake_base(runtime.active_configuration.digest)
            schema = (
                "operator-recovery-patch-v2"
                if base.validation_schema == "campaign-foundation-v3"
                else "operator-recovery-patch-v1"
            )
            intent = build_candidate(
                version,
                _patch(version, email, operation_id),
                candidate_id=uuid4(),
                request_schema=schema,
            )
            with transaction.atomic(durable=True):
                request = ConfigurationChangeRequest.objects.create(
                    base=base,
                    patch=intent.patch(),
                    actor_id=None,
                    request_key=operation_id,
                    request_schema=schema,
                    correlation_id=correlation_id,
                    payload_fingerprint=intent.payload_fingerprint,
                    candidate_version_id=intent.candidate.version_id,
                    candidate_digest=intent.candidate.digest,
                    authority="operator_recovery",
                    operator_name=operator_name,
                    operator_reason=reason,
                    confirmed_deployment_id=deployment_id,
                    recovery_target=email,
                )
        result = _install_request(store, request=request, correlation_id=correlation_id)
        if result.state == "applied":
            from .policy_models import AdminRevocation

            coherent_configuration(store)
            if not AdminRevocation.objects.filter(activation__request=request).exists():
                raise ConfigError("Recovery activation evidence is incomplete.")
        return _status(request)
