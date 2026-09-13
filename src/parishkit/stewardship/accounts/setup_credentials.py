"""Original-login sealed wizard intake; no live credential installation occurs."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.web.contracts import check_version

from .handoff_discovery import public_handoff
from .integration_candidates import slack_candidate, workspace_info
from .metrics_credentials import credential_receipt
from .provider_context import validated_context
from .sessions import authenticated_admin, database_now, require_fresh
from .setup_drafts import _owned
from .setup_models import SetupAttempt, SetupDraftSection
from .setup_policy import SetupState
from .setup_secret_models import SetupSealedCredential
from .setup_staging import _expiry, _status

TARGETS = frozenset({"parishsoft", "google_workspace", "slack"})


@dataclass(frozen=True)
class SetupCredentialReceipt:
    """Detached progress contains no plaintext, ciphertext or mutable model."""

    identifier: UUID
    target: str
    fingerprint: str
    version: int
    scrubbed: bool


def _receipt(row):
    """Copy only public receipt fields selected with the web's column grants."""
    return SetupCredentialReceipt(
        row.pk, row.target, row.fingerprint, row.version, row.scrubbed_at is not None
    )


def _context(attempt, target, organization_id):
    """Derive mail/Slack scope from this draft, never caller-submitted overrides."""
    if target not in TARGETS:
        raise ValueError("Unknown setup credential target.")
    if target == "parishsoft":
        return validated_context(target, {"organization_id": organization_id})
    if organization_id is not None:
        raise ValueError("Only ParishSoft accepts an organization.")
    sections = {
        row.step: row.values
        for row in SetupDraftSection.objects.filter(
            attempt=attempt, scrubbed_at=None, step__in=["mail", "testing", "slack"]
        )
    }
    if target == "google_workspace":
        if not {"mail", "testing"} <= sections.keys():
            raise ValueError("Save outgoing mail and Testing settings first.")
        values = sections["mail"] | {
            "recipient": sections["testing"]["testing_recipient"]
        }
    else:
        if not sections.get("slack", {}).get("enabled"):
            raise ValueError("Enable and configure Slack first.")
        values = {"channel_id": sections["slack"]["channel_id"]}
    return validated_context(target, values)


def credential_status(request, service, attempt_id):
    """Passively inspect only the original attempt's safe receipt metadata."""
    with work_transaction():
        _, attempt = _owned(request, service, attempt_id)
        if (
            attempt.state not in {SetupState.EXPIRED, SetupState.COMPLETED}
            and _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("Setup has expired.")
        return tuple(
            _receipt(row)
            for row in SetupSealedCredential.objects.defer("ciphertext")
            .filter(attempt=attempt)
            .order_by("target")
        )


def stage_credential(
    request,
    service,
    attempt_id,
    *,
    target,
    candidate,
    expected_version,
    organization_id=None,
):
    """Seal in memory after fresh original-login admission and exact-version checks.

    Replacing a candidate is allowed only while collecting; the same target row
    and attempt both advance versions. Starting a source load freezes this input.
    An expired attempt cannot regain authority by supplying valid credential bytes.
    """
    with work_transaction():
        actor, attempt = _owned(request, service, attempt_id)
        check_version(attempt, expected_version)
        require_fresh(request)
        if (
            attempt.state != SetupState.COLLECTING
            or _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("Setup cannot accept credentials now.")
        settings = _context(attempt, target, organization_id)
        if target == "parishsoft":
            from parishkit.stewardship.source.credentials import SourceCredential

            from .cryptography import CryptographicError

            try:
                SourceCredential(candidate)
            except CryptographicError:
                raise ValueError(
                    "The ParishSoft credential has an invalid format."
                ) from None
        else:
            try:
                (slack_candidate if target == "slack" else workspace_info)(candidate)
            except ConfigError:
                raise ValueError("The credential has an invalid format.") from None
        row = (
            SetupSealedCredential.objects.defer("ciphertext")
            .filter(attempt=attempt, target=target)
            .first()
        )
        identifier = row.pk if row is not None else uuid4()
        sealed = public_handoff(target).seal(identifier, candidate)
        fingerprint = credential_receipt(candidate, target)
        context = dict(actor_id=actor.identity, correlation_id=current_correlation())
        values = dict(ciphertext=sealed, fingerprint=fingerprint, settings=settings)
        if row is None:
            row = SetupSealedCredential.objects.create(
                id=identifier, attempt=attempt, target=target, **values, **context
            )
        else:
            SetupSealedCredential.objects.filter(pk=row.pk, version=row.version).update(
                **values, **context, version=F("version") + 1
            )
            row.refresh_from_db(fields=["fingerprint", "version", "scrubbed_at"])
        SetupAttempt.objects.filter(pk=attempt.pk, version=attempt.version).update(
            **context, version=F("version") + 1
        )
        authenticated_admin(request, store=service.store, activity=True)
        attempt.refresh_from_db()
        return _status(attempt), _receipt(row)
