"""Bind replacement fingerprints to target-owned, consumer-acknowledged evidence.

These are installation receipts, not delivery-readiness proofs. Bootstrap and
adding/removing an integration remain their separate owning workflows; ordinary
replacement of an existing reference must not nominate arbitrary fingerprints.
"""

from parishkit.config import ConfigError
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

from .configuration_errors import ConfigurationReadinessUnavailable
from .configuration_models import AppliedConfigurationVersion
from .provider_context import validated_context
from .provider_models import ProviderValidationContext
from .secret_models import (
    SECRET_PENDING,
    CredentialConsumerAcknowledgement,
    SecretReplacementRequest,
)

TARGETS = frozenset({"parishsoft", "google_workspace", "slack"})


class StaleCredentialReceipt(ConfigError):
    """A retained receipt no longer describes current authentication inputs."""


def integration_records(document):
    """Copy the validated public records without consulting credential storage."""
    return {
        row["values"]["kind"]: row
        for row in document["sections"].get("integrations", [])
    }


def authentication_scope(target, records, *, recipient=None):
    """Match authentication inputs without treating an old recipient as readiness."""
    selected = dict(records[target]["values"]["settings"])
    if target == "parishsoft":
        selected.pop("nightly_time", None)
        organization = selected.get("organization_id")
        if (
            not isinstance(organization, str)
            or not organization.isascii()
            or not organization.isdecimal()
        ):
            raise ConfigError("Integration authentication scope is unavailable.")
        selected["organization_id"] = int(organization)
        if str(selected["organization_id"]) != organization:
            raise ConfigError("A canonical organization ID is required.")
    elif target == "google_workspace":
        email = records.get("email")
        if email is None:
            raise ConfigError("Outgoing email settings are unavailable.")
        selected.update(email["values"]["settings"])
        selected["recipient"] = recipient
    return validated_context(target, selected)


def current_receipt(target, fingerprint, records):
    """Require the latest completed target replacement and every consumer ACK."""
    if target not in TARGETS or target not in records or fingerprint is None:
        raise ConfigError("An acknowledged integration credential is required.")
    if SecretReplacementRequest.objects.filter(
        target=target, state__in=SECRET_PENDING
    ).exists():
        raise ConfigurationReadinessUnavailable(
            "A credential replacement is still in progress."
        )
    receipt = (
        SecretReplacementRequest.objects.filter(target=target, state="applied")
        .order_by("-created_at", "-pk")
        .first()
    )
    required = sorted(
        role.value for role, names in ALLOWED_SECRETS.items() if target in names
    )
    if (
        receipt is None
        or receipt.resulting_fingerprint != fingerprint
        or sorted(receipt.required_consumers) != required
    ):
        raise StaleCredentialReceipt("The selected credential receipt is not current.")
    acknowledgements = dict(
        CredentialConsumerAcknowledgement.objects.filter(request=receipt).values_list(
            "consumer", "fingerprint"
        )
    )
    if acknowledgements != {consumer: fingerprint for consumer in required}:
        raise ConfigError("Credential consumer acknowledgements are incomplete.")
    context = ProviderValidationContext.objects.filter(
        request=receipt, target=target
    ).first()
    if context is None or context.settings != authentication_scope(
        target, records, recipient=context.settings.get("recipient")
    ):
        raise StaleCredentialReceipt(
            "The credential was checked against different settings."
        )
    return receipt


def validate_installation(document):
    """Recheck changed existing references before preparing/selecting any YAML files."""
    if document["predecessor_digest"] is None:
        return
    previous = AppliedConfigurationVersion.objects.get(
        digest=document["predecessor_digest"]
    )
    before = integration_records(previous.canonical_document)
    after = integration_records(document)
    for target in TARGETS & before.keys() & after.keys():
        old = before[target]["values"]["credential_fingerprint"]
        proposed = after[target]["values"]["credential_fingerprint"]
        if old != proposed:
            receipt = current_receipt(target, proposed, after)
            if receipt.expected_fingerprint != old:
                raise ConfigError("Credential replacement has a different predecessor.")
