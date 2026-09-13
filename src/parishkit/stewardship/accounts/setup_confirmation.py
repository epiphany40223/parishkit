"""Freeze exact original-Admin intent and readiness in one durable transaction.

This internal service does not install credentials, select YAML or configure the
application. The final confirmation UI must not expose it until the complete
installer/consumer/activation workflow is registered and operational.
"""

from dataclasses import dataclass
from uuid import UUID

from django.core import signing
from django.db import connection
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .configuration_requests import _status, check_historical_additions
from .models import ConfigurationChangeRequest
from .sessions import authenticated_admin, database_now
from .setup_delivery_models import SetupMailDelivery
from .setup_drafts import _owned
from .setup_models import SetupAttempt, SetupConfigurationIntent
from .setup_notification_models import SetupSlackDelivery
from .setup_preview import PREVIEW_SALT, verify_preview
from .setup_readiness_models import SetupReadinessBinding
from .setup_staging import _expiry


@dataclass(frozen=True)
class ReadyInputs:
    """Only stable receipt identities cross into the frozen installation journal."""

    source_result: UUID
    mail_delivery: UUID
    slack_delivery: UUID | None


def ready_inputs(preview):
    """Require accepted tests for this exact revision, not merely valid credentials."""
    selected = {}
    draft = preview.draft
    schedules = preview.compiled.candidate.document()["sections"].get("schedules", [])
    if sum(row["values"]["kind"] == "initial" for row in schedules) != 1:
        raise ValueError(
            "Save one initial invitation schedule before final confirmation."
        )
    for name, model, required in (
        ("mail", SetupMailDelivery, True),
        ("slack", SetupSlackDelivery, draft.sections["slack"]["enabled"]),
    ):
        rows = model.objects.filter(attempt_id=draft.status.attempt_id)
        if rows.filter(state__in=["queued", "submitting"]).exists():
            raise ValueError("Wait for pending setup tests before final confirmation.")
        selected[name] = None
        if required:
            selected[name] = (
                rows.filter(
                    state="accepted",
                    attempt_version=draft.status.version,
                    candidate_digest=preview.compiled.candidate.digest,
                    credential__version=F("credential_version"),
                    credential__fingerprint=F("fingerprint"),
                    credential__scrubbed_at=None,
                )
                .order_by("-finished_at", "-id")
                .values_list("id", flat=True)
                .first()
            )
            if selected[name] is None:
                raise ValueError(
                    "A successful test of the exact setup preview is required."
                )
    return ReadyInputs(
        UUID(draft.sections["campaign"]["source_result"]),
        selected["mail"],
        selected["slack"],
    )


def freeze_setup(request, service, *, preview_token):
    """One transaction binds configuration intake, original attempt and readiness.

    Ordinary request intake deliberately rejects v7. Its separate transaction
    would leave an unbound request or a frozen attempt after a failure, so this
    narrowly scoped original-setup owner performs the indivisible intake here.
    Within the original signed-preview and live-session window, a replay may
    observe its original frozen receipt. It never creates a second request,
    bypasses authentication expiry or extends the original login's idle expiry.
    """
    if connection.in_atomic_block or not connection.get_autocommit():
        raise StorageInvariantError("Setup confirmation must own its transaction.")
    if type(preview_token) is not str or len(preview_token) > 4096:
        raise ValueError("Invalid setup confirmation.")
    binding = signing.loads(preview_token, salt=PREVIEW_SALT, max_age=900)
    with work_transaction():
        actor, attempt = _owned(request, service)
        if attempt is None or _expiry(attempt, database_now()) is not None:
            raise PermissionError("Original setup confirmation is unavailable.")
        existing = (
            SetupConfigurationIntent.objects.select_related("request")
            .filter(attempt=attempt)
            .first()
        )
        if existing is not None:
            expected = {
                "attempt": str(attempt.pk),
                "version": existing.attempt_version - 1,
                "base": existing.request.base.digest,
                "candidate": existing.request.candidate_digest,
                "request_key": str(existing.request.request_key),
            }
            if attempt.state != "frozen" or binding != expected:
                raise StaleRecordError("Setup confirmation changed.")
            return _status(existing.request)
        preview = verify_preview(request, service, preview_token)
        ready = ready_inputs(preview)
        candidate = preview.compiled.candidate
        check_historical_additions(attempt.base_id, preview.compiled.patch())
        # SQL's deferred binding constraint prohibits committing either half
        # without the original frozen intent in this same transaction.
        configuration_request = ConfigurationChangeRequest.objects.create(
            id=preview.operation_id,
            base_id=attempt.base_id,
            patch=preview.compiled.patch(),
            actor_id=actor.identity,
            request_key=preview.request_key,
            request_schema="initial-setup-patch-v7",
            correlation_id=current_correlation(),
            payload_fingerprint=preview.compiled.payload_fingerprint,
            candidate_version_id=candidate.version_id,
            candidate_digest=candidate.digest,
        )
        authenticated_admin(request, store=service.store, activity=True)
        SetupAttempt.objects.filter(pk=attempt.pk, version=attempt.version).update(
            state="frozen",
            actor_id=actor.identity,
            correlation_id=current_correlation(),
            version=F("version") + 1,
        )
        intent = SetupConfigurationIntent.objects.create(
            attempt=attempt,
            request=configuration_request,
            attempt_version=attempt.version + 1,
            actor_id=actor.identity,
        )
        SetupReadinessBinding.objects.create(
            intent=intent,
            source_result_id=ready.source_result,
            mail_delivery_id=ready.mail_delivery,
            slack_delivery_id=ready.slack_delivery,
            testing_recipient=preview.draft.sections["testing"]["testing_recipient"],
            actor_id=actor.identity,
        )
        return _status(configuration_request)
