"""Original-login public draft ownership, distinct from active YAML and secrets."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import check_version

from .sessions import authenticated_admin, database_now
from .setup_forms import validate_values
from .setup_models import SetupAttempt, SetupDraftSection
from .setup_policy import SetupState
from .setup_staging import SetupStatus, _admit, _expiry, _status, _window


@dataclass(frozen=True)
class DraftView:
    """A detached, non-secret snapshot safe to render only to its admitted owner."""

    status: SetupStatus
    sections: dict
    idle_at: datetime
    absolute_at: datetime
    watchdog_at: datetime | None
    source_task_id: UUID | None = None


def _owned(request, service, attempt_id=None):
    """Lock the original attempt under work order and current bootstrap authority."""
    if attempt_id is not None and not isinstance(attempt_id, UUID):
        raise ValueError("Invalid setup attempt.")
    actor, configuration = _admit(request, service, activity=False)
    rows = SetupAttempt.objects.select_for_update(of=("self",)).filter(
        owner_id=actor.identity, session_id=request.portal_session.pk
    )
    if attempt_id is not None:
        rows = rows.filter(pk=attempt_id)
    row = rows.select_related("source_task").first()
    if attempt_id is not None and row is None:
        raise LookupError("Setup is unavailable.")
    if row is not None and row.base_id != configuration.active_configuration_id:
        raise StaleRecordError("The setup base changed.")
    return actor, row


def view_draft(request, service, attempt_id=None):
    """A passive GET cannot extend idle expiry, create staging or resume new logins."""
    with work_transaction():
        _, row = _owned(request, service, attempt_id)
        if row is None:
            return None
        window = _window(row, request.portal_session)
        sections = {}
        if row.state not in {SetupState.EXPIRED, SetupState.COMPLETED}:
            if _expiry(row, database_now()) is not None:
                raise PermissionError("Setup has expired.")
            sections = {
                section.step: deepcopy(section.values)
                for section in SetupDraftSection.objects.filter(
                    attempt=row, scrubbed_at=None
                )
            }
        return DraftView(
            _status(row),
            sections,
            window.idle_at,
            window.absolute_at,
            window.watchdog_at,
            row.source_task_id,
        )


def save_section(request, service, attempt_id, *, step, values, expected_version):
    """Persist one validated step; concurrent tabs use the entire attempt's version."""
    return save_sections(
        request,
        service,
        attempt_id,
        updates={step: values},
        expected_version=expected_version,
    )


def save_sections(request, service, attempt_id, *, updates, expected_version):
    """One original-owner lock and version cover all dependent temporary edits."""
    from .setup_content_values import CONTENT_STEPS
    from .setup_schedule_values import reconcile_preparation

    if type(updates) is not dict or not updates:
        raise ValueError("Setup edits are required.")
    updates = {step: validate_values(step, values) for step, values in updates.items()}
    with work_transaction():
        actor, attempt = _owned(request, service, attempt_id)
        check_version(attempt, expected_version)
        if (
            attempt.state != SetupState.COLLECTING
            or _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("Setup cannot accept settings now.")
        if "parish" in updates and attempt.source_task_id is not None:
            original = SetupDraftSection.objects.get(attempt=attempt, step="parish")
            if updates["parish"]["timezone"] != original.values["timezone"]:
                raise ValueError(
                    "The source load fixes this setup's timezone. "
                    "Cancel and start a new setup to change it."
                )
        if "branding" in updates:
            from .branding_staging import staged_bundle

            staged_bundle(
                request,
                service,
                UUID(updates["branding"]["bundle_id"]),
                setup_attempt_id=attempt.pk,
            )
        if "campaign" in updates:
            from .setup_campaign import admit_campaign_values

            admit_campaign_values(request, service, attempt_id, updates["campaign"])
        for step in CONTENT_STEPS:
            if step not in updates:
                continue
            from .setup_content import admit_content_values

            admit_content_values(request, service, attempt_id, step, updates[step])
        updates = reconcile_preparation(request, service, attempt_id, updates)
        context = dict(actor_id=actor.identity, correlation_id=current_correlation())
        # The parent goes first when dates and schedules are saved together;
        # SQL repeats each child's original source/campaign proof independently.
        for step in sorted(updates, key=lambda item: (item != "campaign", item)):
            values = updates[step]
            row = SetupDraftSection.objects.filter(attempt=attempt, step=step).first()
            if row is None:
                SetupDraftSection.objects.create(
                    attempt=attempt, step=step, values=values, **context
                )
            else:
                SetupDraftSection.objects.filter(pk=row.pk, version=row.version).update(
                    values=values, version=F("version") + 1, **context
                )
        SetupAttempt.objects.filter(pk=attempt.pk, version=attempt.version).update(
            version=F("version") + 1, **context
        )
        authenticated_admin(request, store=service.store, activity=True)
        attempt.refresh_from_db()
        return _status(attempt)
