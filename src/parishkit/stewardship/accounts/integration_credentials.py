"""Save a replacement integration key from its settings page, in one step.

The Administrator pastes the key on the integration's own page. This module
seals it for the target's isolated installer and, in the same web request,
records the configuration request that will select it. The installer checks
the key with the provider and installs it; the running consumer acknowledges
it (see ``credential_runtime.acknowledge_rotations``); the configuration
installer then applies the selection, which it holds while the replacement is
still in progress. Nobody has to recreate a container or run a command.

The page shows one plain-language line about the latest change. Request
states and fingerprints stay in the audit log and the technical status page.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS
from parishkit.stewardship.storage import StaleRecordError

from .configuration_requests import _status, record_request
from .integration_selection import authentication_scope, integration_records
from .metrics_credentials import credential_receipt
from .policy import Capability, allows
from .privileged_actions import sealed_secret_request
from .request_models import ConfigurationChangeRequest
from .request_patch import OPTIONAL_INTEGRATIONS, credential_request_schema
from .secret_models import SECRET_PENDING, SecretReplacementRequest
from .secret_requests import cancel_secret_request
from .sessions import authenticated_admin, require_fresh

# Derives the selection request's key from the secret request's identity, so
# the status line can find it and an identical browser retry reuses both.
SELECTION_NAMESPACE = UUID("6f0f6c55-3e0b-4c43-9a71-5d2b1c6e7a10")
PENDING_CONFIGURATION = ("staged", "validating", "prepared", "yaml_activated")
# The installer and consumer have this long to finish before rollback.
STAGING_LIFETIME = timedelta(hours=1)

FAILED = {
    "parishsoft": _(
        "ParishSoft did not accept the new API key for this organization ID. "
        "Check the key and the organization ID, then try again."
    ),
    "google_workspace": _(
        "Google did not accept the new service-account key for the delegated "
        "mailbox. Check that the key belongs to the service account with "
        "domain-wide delegation, then try again."
    ),
    "slack": _(
        "Slack did not accept the new bot token for this channel. Check that "
        "the app is installed and invited to the channel, then try again."
    ),
}


@dataclass(frozen=True)
class CredentialSummary:
    """The one line an Administrator needs about the latest key change."""

    kind: str
    at: datetime
    message: str
    request_id: UUID


def selection_key(request_id):
    """The selection request's key is a pure function of the secret request."""
    return uuid5(SELECTION_NAMESPACE, str(request_id))


def _selection(row):
    """Return the linked selection request's latest state, if one was recorded."""
    selection = (
        ConfigurationChangeRequest.objects.filter(
            actor_id=row.requested_by_id, request_key=selection_key(row.pk)
        )
        .select_related("base")
        .first()
    )
    return None if selection is None else _status(selection).state


def summary(target, record):
    """Describe the latest key change for ``target`` in plain language.

    ``record`` is the applied integration record, whose fingerprint shows
    whether an installed key is the one actually in use.
    """
    row = (
        SecretReplacementRequest.objects.filter(target=target)
        .order_by("-created_at", "-pk")
        .first()
    )
    if row is None:
        return None
    if row.state in SECRET_PENDING:
        return CredentialSummary(
            "pending",
            row.created_at,
            _("Checking and installing the new key. This usually takes a minute."),
            row.pk,
        )
    if row.state == "applied":
        if record["values"]["credential_fingerprint"] == row.resulting_fingerprint:
            return CredentialSummary(
                "updated", row.updated_at, _("Key updated."), row.pk
            )
        if _selection(row) in PENDING_CONFIGURATION:
            return CredentialSummary(
                "pending",
                row.created_at,
                _("The new key is installed. Switching to it now."),
                row.pk,
            )
        return CredentialSummary(
            "unselected",
            row.updated_at,
            _("The new key is installed but not yet in use."),
            row.pk,
        )
    if row.state == "expired":
        message = _(
            "The key change did not finish within an hour, so it was undone. Try again."
        )
    elif row.state == "cancelled":
        message = _("The key change was cancelled.")
    else:
        message = FAILED[target]
    return CredentialSummary("failed", row.updated_at, message, row.pk)


def save_credential(
    request, service, configuration, actor, *, target, intent, value, record, settings
):
    """Seal ``value`` and queue its selection together with ``settings``.

    ``intent`` is the page's verified signed intent: it fixes the request and
    staging identities, so a browser retry returns the original receipts. The
    key is checked against ``settings``, the settings the Administrator is
    saving, which the selection request then applies with the fingerprint.
    If the selection cannot be recorded, the staged key is cancelled rather
    than left to install without anything selecting it.

    ``record`` is None when adding Slack after setup: the selection request
    then adds the integration record together with its first key.
    """
    require_fresh(request)
    records = integration_records(configuration.active_configuration.canonical_document)
    identifier = UUID(intent["request"])
    adding = record is None
    if adding:
        if target not in OPTIONAL_INTEGRATIONS:
            raise LookupError("Integration is unavailable.")
        # A retry of the same save derives the same new record identity.
        record = {
            "id": str(uuid5(SELECTION_NAMESPACE, f"record:{identifier}")),
            "values": {"kind": target, "settings": {}, "credential_fingerprint": None},
        }
    proposed = {**record, "values": {**record["values"], "settings": settings}}
    scope = authentication_scope(
        target,
        records | {target: proposed},
        recipient=configuration.testing_recipient,
    )
    from .handoff_discovery import public_handoff

    sealed = public_handoff(target).seal(identifier, value)
    fingerprint = credential_receipt(value, target)
    sealed_secret_request(
        request,
        configuration_digest=intent["base"],
        request_id=identifier,
        target=target,
        staging_reference=UUID(intent["staging"]),
        staging_lifetime=STAGING_LIFETIME,
        expected_fingerprint=record["values"]["credential_fingerprint"],
        correlation_id=current_correlation(),
        sealed_candidate=sealed,
        candidate_fingerprint=fingerprint,
        required_consumers=tuple(
            role.value for role, names in ALLOWED_SECRETS.items() if target in names
        ),
        provider_settings=scope,
    )
    values = {"credential_fingerprint": fingerprint}
    if adding:
        values |= {"kind": target, "settings": settings}
    elif settings != record["values"]["settings"]:
        values["settings"] = settings

    def admit():
        """Repeat authorization, freshness and base under the work lock."""
        with work_transaction():
            fresh = authenticated_admin(request, store=service.store, read_only=True)
            if not allows(fresh, Capability.CONFIGURE) or fresh.identity != (
                actor.identity
            ):
                return False
            require_fresh(request)
            return True

    base = service.store.active()
    if base is None or base.digest != intent["base"]:
        raise StaleRecordError("The integration settings changed.")
    try:
        record_request(
            base_digest=intent["base"],
            patch=[
                {
                    "operation": "add" if adding else "update",
                    "section": "integrations",
                    "id": record["id"],
                    "values": values,
                }
            ],
            actor_id=actor.identity,
            request_key=selection_key(identifier),
            correlation_id=current_correlation(),
            admit=admit,
            request_schema=credential_request_schema(base.document()),
        )
    except Exception:
        cancel_secret_request(
            request_id=identifier,
            actor_id=actor.identity,
            correlation_id=current_correlation(),
        )
        raise
    return identifier
