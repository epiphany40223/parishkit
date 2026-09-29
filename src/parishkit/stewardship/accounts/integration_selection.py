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
# Integration settings a key is never checked against: refresh timing is
# scheduling and the From name is presentation. Everything else in an
# integration's settings is its key scope (see authentication_scope).
NOT_KEY_SCOPE = frozenset({"nightly_time", "full_refresh", "sender_name"})


class OrganizationLocked(ConfigError):
    """The ParishSoft organization cannot change once its data is loaded."""


def loaded_organization():
    """The ParishSoft organization ID of the loaded data, or None before a load."""
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    return SourceCurrent.objects.values_list("organization_id", flat=True).first()


def refuse_organization_change(before, after):
    """Refuse a new ParishSoft organization ID once any data has been loaded.

    ``before`` and ``after`` are integration records by kind. Every refresh
    must read the organization whose data is loaded (``source.requests``),
    so a changed ID would stop them all. The settings form refuses it too;
    this check covers every path that records or installs a change.
    """
    if "parishsoft" not in before or "parishsoft" not in after:
        return
    old, new = (
        records["parishsoft"]["values"]["settings"].get("organization_id")
        for records in (before, after)
    )
    if old == new:
        return
    loaded = loaded_organization()
    if loaded is not None and str(loaded) != new:
        raise OrganizationLocked(
            "The ParishSoft organization cannot change after data is loaded."
        )


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
    for name in NOT_KEY_SCOPE:
        selected.pop(name, None)
    if target == "google_workspace":
        email = records.get("email")
        if email is None:
            raise ConfigError("Outgoing email settings are unavailable.")
        selected.update(email["values"]["settings"])
        # The From name is presentation only, never credential scope.
        selected.pop("sender_name", None)
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
    refuse_organization_change(before, after)
    # Initial setup adds its integrations under the setup readiness owner. After
    # setup (the predecessor already has ParishSoft), an integration added with
    # its first key, such as Slack, needs a current receipt. Its predecessor is
    # whatever key file an earlier removal left installed, not a setting.
    added = after.keys() - before.keys() if "parishsoft" in before else set()
    for target in TARGETS & ((before.keys() & after.keys()) | added):
        old = (
            before[target]["values"]["credential_fingerprint"]
            if target in before
            else None
        )
        proposed = after[target]["values"]["credential_fingerprint"]
        if old != proposed:
            receipt = current_receipt(target, proposed, after)
            if target in before and receipt.expected_fingerprint != old:
                raise ConfigError("Credential replacement has a different predecessor.")


def switching(target, fingerprint):
    """True while ``target``'s key is changing to the installed ``fingerprint``.

    From the moment a credential installer renames a new key into place until
    a configuration request selects its fingerprint, a consumer that compares
    the file with the applied configuration sees a mismatch. That is not a
    bad key: the change is still in progress, or it is installed and waits
    for an Administrator to select **Finish switching to the new key** (for
    example after its automatic selection failed). Consumers hold their work
    and retry later instead of spending their attempts on it (#307 M1).
    Any other mismatch is still a refusal.
    """
    latest = (
        SecretReplacementRequest.objects.filter(target=target)
        .order_by("-created_at", "-pk")
        .values_list("state", "resulting_fingerprint")
        .first()
    )
    return latest is not None and (
        latest[0] in SECRET_PENDING
        or (latest[0] == "applied" and latest[1] == fingerprint)
    )
