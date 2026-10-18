"""Original-login public draft ownership, distinct from active YAML and secrets."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.db.models import F
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import check_version
from parishkit.stewardship.web.refusals import UserFacingDenied, UserFacingError

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
    # The bound load's Task state (#1353): a failed load leaves setup
    # collecting, so only "succeeded" means the parish data is loaded.
    source_task_state: str | None = None
    # Whether that load's staged data is still valid (staged_load): a load
    # that succeeded can go stale when its credential changes, and then
    # needs loading again (#1353).
    source_loaded: bool = False
    # Wizard pages the admin has explicitly reviewed (see mark_reviewed).
    reviewed: dict = field(default_factory=dict)


# Pages that save nothing of their own record an explicit review here. It is
# wizard progress in the attempt's own login session (which the admin-portal
# spec allows), not staged configuration: the setup draft's steps and keys are
# fixed by SQL, and an attempt never outlives the session it is bound to.
REVIEWED = "setup_reviewed"


def mark_reviewed(request, attempt_id, page, value):
    """Remember that this attempt's ``page`` was reviewed as ``value``.

    ``value`` names what was reviewed (the draft version shown on the review
    page), so a later change to that data makes the page count as not done
    again.
    """
    reviewed = request.session.get(REVIEWED, {})
    if reviewed.get("attempt") != str(attempt_id):
        reviewed = {"attempt": str(attempt_id)}
    request.session[REVIEWED] = reviewed | {page: value}


def _reviewed(request, attempt_id):
    """This attempt's review marks from the session; another attempt's are ignored."""
    reviewed = request.session.get(REVIEWED, {})
    if reviewed.get("attempt") != str(attempt_id):
        return {}
    return {key: value for key, value in reviewed.items() if key != "attempt"}


def staged_load(attempt):
    """The attempt's exact, still-valid staged load result, or None.

    Valid means: the bound load succeeded, its exchange is unscrubbed and
    on the Task's fence, the staged ParishSoft credential still has the
    version and fingerprint the load read, and its snapshot is ready on the
    same fences. Review, campaign staging and the wizard's Load parish data
    step all use this one predicate (#1353), and the attempt's audit trigger
    repeats it in SQL to refuse a Load again of a still-valid load.
    """
    from .setup_exchange_models import SetupSourceResult

    if attempt.source_task_id is None:
        return None
    return (
        SetupSourceResult.objects.filter(
            exchange__attempt=attempt,
            exchange__scrubbed_at=None,
            exchange__task__root_id=attempt.source_task_id,
            exchange__task__state="succeeded",
            exchange__task_fence=F("exchange__task__fence"),
            exchange__credential__scrubbed_at=None,
            exchange__credential_version=F("exchange__credential__version"),
            exchange__fingerprint=F("exchange__credential__fingerprint"),
            snapshot__state="ready",
            snapshot__task_id=F("exchange__task_id"),
            snapshot__source_fence=F("exchange__source_fence"),
        )
        .order_by("-snapshot__completed_at", "-pk")
        .first()
    )


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
        row = _owned(request, service, attempt_id)[1]
        if row is None:
            return None
        window = _window(row, request.portal_session)
        sections = {}
        if row.state not in {SetupState.EXPIRED, SetupState.COMPLETED}:
            if _expiry(row, database_now()) is not None:
                raise UserFacingDenied(
                    _("This setup has expired."),
                    fix=_("Start a new setup from the setup overview."),
                    link=reverse("admin:setup"),
                    link_label=_("Open the setup overview"),
                )
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
            row.source_task.state if row.source_task_id else None,
            row.state == SetupState.COLLECTING and staged_load(row) is not None,
            _reviewed(request, row.pk),
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
    """Save several system steps under one original-owner lock and version.

    Only the wizard's own steps (``setup_forms.FORMS``) can be saved:
    ``validate_values`` refuses any other step, including the first-campaign
    steps a draft saved before #142 may still hold. A save that leaves Slack
    off also discards the staged Slack token and a queued Slack test
    (``setup_credentials.discard_slack``, #1499).
    """
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
            raise UserFacingDenied(
                _("This setup is no longer accepting changes."),
                fix=_(
                    "It may have expired, been cancelled, or be finishing. The "
                    "setup overview shows its current state."
                ),
                link=reverse("admin:setup"),
                link_label=_("Open the setup overview"),
            )
        if "parish" in updates and attempt.source_task_id is not None:
            original = SetupDraftSection.objects.get(attempt=attempt, step="parish")
            if updates["parish"]["timezone"] != original.values["timezone"]:
                raise UserFacingError(
                    _("The parish data load fixes this setup's timezone."),
                    fix=_("Cancel and start a new setup to change it."),
                )
        if "branding" in updates:
            from .branding_staging import staged_bundle

            staged_bundle(
                request,
                service,
                UUID(updates["branding"]["bundle_id"]),
                setup_attempt_id=attempt.pk,
            )
        context = dict(actor_id=actor.identity, correlation_id=current_correlation())
        for step in sorted(updates):
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
        if "slack" in updates and not updates["slack"]["enabled"]:
            # Slack off continues as if it had never been on (#1499). After
            # the version bump, so no queued test of it is live any more.
            from .setup_credentials import discard_slack

            discard_slack(attempt, actor_id=actor.identity)
        authenticated_admin(request, store=service.store, activity=True)
        attempt.refresh_from_db()
        return _status(attempt)
