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

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS
from parishkit.stewardship.storage import StaleRecordError

from .configuration_requests import _status, record_request
from .integration_selection import (
    NOT_KEY_SCOPE,
    authentication_scope,
    integration_records,
)
from .metrics_credentials import credential_receipt
from .policy import Capability, allows
from .privileged_actions import sealed_secret_request
from .request_models import ConfigurationChangeRequest
from .request_patch import OPTIONAL_INTEGRATIONS, credential_request_schema
from .secret_models import SECRET_PENDING, SecretReplacementRequest
from .secret_requests import cancel_secret_request
from .sessions import authenticated_admin, require_fresh
from .setup_credentials import admit_candidate

# Derives the selection request's key from the secret request's identity, so
# the status line can find it and an identical browser retry reuses both.
SELECTION_NAMESPACE = UUID("6f0f6c55-3e0b-4c43-9a71-5d2b1c6e7a10")
PENDING_CONFIGURATION = ("staged", "validating", "prepared", "yaml_activated")
# The installer and consumer have this long to finish before rollback.
STAGING_LIFETIME = timedelta(hours=1)

# What stops while a new key is installed but not selected: every consumer
# compares the installed file with the selected fingerprint (#307 M1).
STOPPED = {
    "parishsoft": _("ParishSoft refreshes are stopped"),
    "google_workspace": _("email is held and not sent"),
    "slack": _("Slack alerts are not sent"),
}

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


def original_selection(row):
    """The selection request the key's own save recorded, if there is one."""
    return ConfigurationChangeRequest.objects.filter(
        actor_id=row.requested_by_id, request_key=selection_key(row.pk)
    ).first()


def _switching(row):
    """True while any configuration request selecting this key is still queued.

    That is the save's own selection request or a later **Finish switching**
    request, by any Administrator: each one sets the integration's
    fingerprint to the key's.
    """
    requests = ConfigurationChangeRequest.objects.filter(
        patch__contains=[
            {"values": {"credential_fingerprint": row.resulting_fingerprint}}
        ]
    ).select_related("base")
    return any(_status(request).state in PENDING_CONFIGURATION for request in requests)


def leftover_key(target):
    """The key file an unconfigured integration still has installed, or None.

    Removing an integration (Slack) removes only its settings; the installed
    file stays until a new key replaces it. That file is the latest key the
    installer applied for the target.
    """
    return (
        SecretReplacementRequest.objects.filter(target=target, state="applied")
        .order_by("-created_at", "-pk")
        .values_list("resulting_fingerprint", flat=True)
        .first()
    )


def _was_selected(row):
    """True once any applied configuration request selected this key."""
    requests = ConfigurationChangeRequest.objects.filter(
        patch__contains=[
            {"values": {"credential_fingerprint": row.resulting_fingerprint}}
        ]
    ).select_related("base")
    return any(_status(request).state == "applied" for request in requests)


def switch_patch(row, records):
    """The configuration patch that finishes switching to ``row``'s key.

    It repeats the key's original selection on the current settings, so
    **Finish switching** recovers from a selection that failed, for example
    because another settings change was applied first. Only the key-scope
    settings saved with the key (what the provider check used, such as the
    Slack channel) are carried over, merged onto the current settings, so a
    newer change to anything else (such as the refresh schedule) is kept.
    When the save added the integration, the whole new record is added again,
    unless the key was in use and the integration was removed since: a
    removed integration comes back only with a newly pasted, checked key. A
    key staged without a selection request selects its fingerprint alone.
    """
    original = original_selection(row)
    item = original.patch[0] if original is not None else None
    if item is not None and item["operation"] == "add":
        if row.target in records:
            raise StaleRecordError("This integration was set up again since.")
        if _was_selected(row):
            raise StaleRecordError("This integration was removed since.")
        return [item]
    record = records.get(row.target)
    if record is None:
        raise StaleRecordError("This integration is no longer configured.")
    values = {"credential_fingerprint": row.resulting_fingerprint}
    saved = item["values"].get("settings") if item is not None else None
    if saved is not None:
        current = record["values"]["settings"]
        merged = current | {
            name: value for name, value in saved.items() if name not in NOT_KEY_SCOPE
        }
        if merged != current:
            values["settings"] = merged
    return [
        {
            "operation": "update",
            "section": "integrations",
            "id": record["id"],
            "values": values,
        }
    ]


# How long a finished key change stays on its integration's settings page,
# unless an Administrator dismisses it sooner.
RESULT_VISIBLE = timedelta(hours=1)
# Finished outcomes an Administrator may dismiss; "pending" and "unselected"
# still need attention, so they keep showing.
DISMISSIBLE = ("updated", "failed")


def _settled_long_ago(row):
    """True when a finished key change is older than the page shows it for."""
    return timezone.now() - row.updated_at > RESULT_VISIBLE


def _dismissed(row):
    """True once any Administrator dismissed this key change's status line."""
    return AuditEvent.objects.filter(
        event_type=Action.CREDENTIAL_RESULT_DISMISSED.value, subject_id=row.pk
    ).exists()


def dismiss(target, record, request_id, *, actor_id, parish_id):
    """Hide a finished key change's status line for every Administrator.

    The dismissal is an audit event about the request, so nothing else needs
    to store it. Only the line the page currently shows can be dismissed, and
    only when it is a finished outcome: a stale page, a change that has since
    been superseded or one that still needs action records nothing.
    """
    with transaction.atomic():
        latest = summary(target, record)
        if latest is None or latest.request_id != request_id:
            return
        if latest.kind not in DISMISSIBLE:
            return
        record_action(
            Action.CREDENTIAL_RESULT_DISMISSED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=request_id,
            parish_id=parish_id,
        )


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
    if (
        record["id"] is None
        and row.state == "applied"
        and not _switching(row)
        and _was_selected(row)
    ):
        # The integration was removed after this key was in use (Remove
        # Slack): nothing needs action, and the page offers setting it up
        # again. A key whose own "add" never applied still shows below.
        return None
    # A finished change is news for an hour (or until an Administrator
    # dismisses it), not forever; its history stays on the details page and
    # in the audit log. A key that is installed but not yet in use still
    # needs action, so it keeps showing.
    in_use = record["values"]["credential_fingerprint"] == row.resulting_fingerprint
    needs_action = row.state == "applied" and not in_use
    finished = row.state not in SECRET_PENDING and not needs_action
    if finished and (_settled_long_ago(row) or _dismissed(row)):
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
        if _switching(row):
            return CredentialSummary(
                "pending",
                row.created_at,
                _("The new key is installed. Switching to it now."),
                row.pk,
            )
        # The key is in place but nothing will select it: the automatic
        # switch failed (or never existed). Consumers refuse the mismatch, so
        # this needs action now, not later.
        return CredentialSummary(
            "unselected",
            row.updated_at,
            _(
                "The new key is installed, but switching to it did not finish, "
                "so %(stopped)s. Select Finish switching to the new key now."
            )
            % {"stopped": STOPPED[target]},
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


def unfinished_switches(configuration):
    """Integrations whose new key is installed but whose switch did not finish.

    The Admin home page lists these, because their consumers are stopped or
    holding mail until an Administrator selects Finish switching, and nobody
    may open the integration's own page for days. Normally one small query:
    the latest key change per integration, all already in use.
    """
    records = integration_records(configuration.active_configuration.canonical_document)
    latest = (
        SecretReplacementRequest.objects.filter(target__in=STOPPED)
        .order_by("target", "-created_at", "-pk")
        .distinct("target")
    )
    stuck = []
    for row in latest:
        record = records.get(row.target)
        if row.state != "applied" or (
            record is not None
            and record["values"]["credential_fingerprint"] == row.resulting_fingerprint
        ):
            continue
        unset = {"id": None, "values": {"credential_fingerprint": None}}
        line = summary(row.target, record or unset)
        if line is not None and line.kind == "unselected":
            stuck.append(row.target)
    return stuck


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
    then adds the integration record together with its first key. Removing
    Slack leaves its last key file installed, so setting it up again replaces
    that file: the new request names it as the predecessor.

    A Workspace or Slack key is first admitted under the deployment profile
    (``admit_candidate``, #476): LOCAL takes only the mail-catcher document
    and no Slack token; every other profile refuses the mail-catcher document.
    """
    require_fresh(request)
    if target in {"google_workspace", "slack"}:
        from parishkit.stewardship.deployment import recorded_profile

        try:
            admit_candidate(target, value, recorded_profile())
        except ConfigError:
            raise ValueError("The credential has an invalid format.") from None
    records = integration_records(configuration.active_configuration.canonical_document)
    identifier = UUID(intent["request"])
    adding = record is None
    if adding:
        if target not in OPTIONAL_INTEGRATIONS:
            raise LookupError("Integration is unavailable.")
        # Setting up again reuses the removed integration's record identity,
        # which history keeps stable; otherwise a retry of the same save
        # derives the same new one.
        from .request_admission import historical_record_id

        record = {
            "id": historical_record_id(configuration.active_configuration.pk, target)
            or str(uuid5(SELECTION_NAMESPACE, f"record:{identifier}")),
            "values": {"kind": target, "settings": {}, "credential_fingerprint": None},
        }
        predecessor = leftover_key(target)
    else:
        predecessor = record["values"]["credential_fingerprint"]
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
        expected_fingerprint=predecessor,
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
