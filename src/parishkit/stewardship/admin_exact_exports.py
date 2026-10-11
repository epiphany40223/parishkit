"""Exact daily export commands of the Admin command line (ADM-11 PR 8h).

An exact export is the Participation page's export of the current figures,
for when no complete generation is ready yet: the page freezes the current
inputs into an ``ExactExportRequest``, a background task calculates them and
then hands off to an ordinary export record (``ExactExportResolution``),
whose file the export lifecycle (PR 8b) downloads. These commands use the
page's own services (``reports.exact_services``):

- ``export exact create`` is the page's exact-export form, for the current
  campaign (reports are current-only until #145);
- ``export exact status`` is the exact status page's read, admitted
  passively, with no view event (the page records none); ``--watch`` follows
  it until nothing remains to wait for, and once handed off it names the
  ``export_id`` that ``export fetch`` saves;
- ``export exact cancel`` and ``export exact retry`` are the status page's
  buttons, before or after the handoff, as the page delegates them.

The change commands admit as the page's form posts do (recording activity,
so a full-scope session) and run in the export lifecycle's command scope,
recording the services' own events and, only when they made the change,
``admin_cmd_export_exact_<verb>``. Documents carry identifiers, states and
instants only.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_exports import (
    ACTIVE_STATES,
    CANCELLABLE_STATES,
    _admit,
    _change,
    _command_event,
    _command_scope,
    _refusal,
    _refusals,
    campaign_mutable,
)
from .admin_reads import NotAvailable, ReadModel, _held, _recheck


@dataclass(frozen=True)
class ExactStatus(ReadModel):
    """One exact export as its status page shows it.

    ``state`` is the calculation's task state until the handoff, then the
    downstream export's (``ready``, ``expired`` and so on), or
    ``cancelled``. ``export_id`` is the handed-off export, for ``export
    fetch``; None before the handoff. ``changes_available`` is whether the
    page's buttons may act now, and ``can_cancel`` whether it offers Cancel.
    """

    id: UUID
    campaign_id: UUID
    report: str
    format: str
    population_scope: str
    created_at: str
    state: str
    export_id: UUID | None
    expires_at: str | None
    changes_available: bool
    can_cancel: bool

    @property
    def terminal(self):
        """Nothing left to wait for: handed off and settled, cancelled or failed."""
        if self.state in ACTIVE_STATES:
            return False
        return self.export_id is not None or self.state in {"cancelled", "failed"}


@dataclass(frozen=True)
class ExactChange(ReadModel):
    """What ``export exact create``, ``cancel`` or ``retry`` did.

    ``created`` is false for a repeat (a request key used before, or a
    request already cancelled), which changed nothing; ``exact`` is the
    request's ``export exact status`` document afterwards.
    """

    created: bool
    request_key: UUID | None
    exact: dict


def exact_model(status, *, mutable):
    """The read model of ``exact_services.exact_export_status``'s result."""
    return ExactStatus(
        id=UUID(status["id"]),
        campaign_id=UUID(status["campaign_id"]),
        report=status["report"],
        format=status["format"],
        population_scope=status["population_scope"],
        created_at=status["created_at"],
        state=status["state"],
        export_id=UUID(status["export_id"]) if status["export_id"] else None,
        expires_at=status["expires_at"],
        changes_available=mutable,
        can_cancel=mutable and status["state"] in CANCELLABLE_STATES,
    )


def _exact_document(service, actor, request_id):
    """The ``export exact status`` document, read in the caller's scope."""
    from .reports.exact_services import exact_export_status

    status = exact_export_status(service.store, actor.identity, request_id)
    mutable = campaign_mutable(UUID(status["campaign_id"]))
    return exact_model(status, mutable=mutable).to_document()


def read_exact(caller, service, request_id):
    """``export exact status``: the exact status page's read, passively.

    The session is rechecked before anything is reported, a refusal
    included. An unknown request is ``not_available``.
    """
    from .reports.exact_services import exact_export_status

    def step():
        """Admit, read and recheck, as the page."""
        actor = _admit(caller, service, activity=False, ministry_jobs=False)
        try:
            status = exact_export_status(service.store, actor.identity, request_id)
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        mutable = campaign_mutable(UUID(status["campaign_id"]))
        _recheck(caller, service.store, actor)
        return exact_model(status, mutable=mutable)

    return _held(step)


def create_exact(caller, service, *, scope, fmt, zone, request_key, context):
    """``export exact create``: the Participation page's exact-export form.

    For the current campaign; no current campaign is ``not_available``, and
    no current inputs to freeze (no promoted source) is ``not_available``.
    The same key returns the original request (``created`` false); a key
    used for another request is ``invalid``, as the page's 409.
    """
    from .admin_reads import Unavailable
    from .admin_report_reads import _current
    from .audit.schemas import Action
    from .reports.exact_models import ExactExportRequest
    from .reports.exact_services import create_exact_export

    actor = _admit(caller, service, activity=True, ministry_jobs=False)
    context["request_id"] = str(request_key)
    try:
        campaign_id = _current()
    except (NotAvailable, Unavailable):
        _recheck(caller, service.store, actor)
        raise

    def step(written):
        """Request the exact export inside the work order, as the service does."""
        with _command_scope(caller, service, actor):
            repeat = ExactExportRequest.objects.filter(
                requester_id=actor.identity, request_key=request_key
            ).exists()
            job = create_exact_export(
                service.store,
                actor.identity,
                campaign_id=campaign_id,
                population_scope=scope,
                format=fmt,
                browser_timezone=zone,
                request_key=request_key,
            )
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_EXACT_CREATE)
                written.append(True)
            document = _exact_document(service, actor, job.pk)
        return ExactChange(created=not repeat, request_key=request_key, exact=document)

    return _change(caller, service, actor, step, context=context)


def _cancelled(request_id):
    """Whether this exact request, or the export it handed off to, is cancelled."""
    from .reports.exact_models import ExactExportCancellation, ExactExportResolution
    from .reports.export_models import ExportCancellation

    if ExactExportCancellation.objects.filter(request_id=request_id).exists():
        return True
    export_id = (
        ExactExportResolution.objects.filter(request_id=request_id)
        .values_list("export_id", flat=True)
        .first()
    )
    return (
        export_id is not None
        and ExportCancellation.objects.filter(request_id=export_id).exists()
    )


def cancel_exact(caller, service, request_id, *, context):
    """``export exact cancel``: the status page's Cancel, before or after handoff.

    Cancelling a request already cancelled returns it (``created`` false); a
    handed-off export that is published is ``stale_version``, as the page.
    """
    from .audit.schemas import Action
    from .reports.exact_services import cancel_exact_export

    actor = _admit(caller, service, activity=True, ministry_jobs=False)

    def step(written):
        """Cancel inside the work order, as the service does."""
        with _command_scope(caller, service, actor):
            repeat = _cancelled(request_id)
            cancel_exact_export(service.store, actor.identity, request_id)
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_EXACT_CANCEL)
                written.append(True)
            document = _exact_document(service, actor, request_id)
        return ExactChange(created=not repeat, request_key=None, exact=document)

    return _change(caller, service, actor, step, context=context)


def _retried(request_id, request_key):
    """Whether this key already made the retry the service would replay.

    Decided the way ``retry_exact_export`` routes the retry: once a handoff
    exists, only the downstream export's chain counts (the service delegates
    to it), otherwise only the calculation's. A key that retried the
    calculation before the handoff is therefore not a repeat afterwards; the
    service treats it as a new downstream retry, refused (``stale_version``)
    unless the downstream run failed, as on the page.
    """
    from .reports.exact_models import ExactExportRequest, ExactExportResolution

    resolution = (
        ExactExportResolution.objects.filter(request_id=request_id)
        .select_related("export__task")
        .first()
    )
    if resolution is not None:
        task = resolution.export.task
    else:
        task = ExactExportRequest.objects.select_related("task").get(pk=request_id).task
    return task.chain_runs.filter(retry_command_id=request_key).exists()


def retry_exact(caller, service, request_id, *, request_key, context):
    """``export exact retry``: the status page's Retry, before or after handoff.

    The key is the page's retry ``request_key``: repeating it returns the
    original retry (``created`` false). Once the request has handed off, a
    retry is the downstream export's, so a key used before the handoff is a
    new retry there (``stale_version`` unless that export failed). A run that
    is not the latest failed one, or a cancelled request, is
    ``stale_version``.
    """
    from .audit.schemas import Action
    from .reports.exact_services import retry_exact_export

    actor = _admit(caller, service, activity=True, ministry_jobs=False)
    context["request_id"] = str(request_key)

    def step(written):
        """Retry inside the work order, as the service does."""
        with _command_scope(caller, service, actor):
            repeat = _retried(request_id, request_key)
            retry_exact_export(
                service.store, actor.identity, request_id, request_key=request_key
            )
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_EXACT_RETRY)
                written.append(True)
            document = _exact_document(service, actor, request_id)
        return ExactChange(created=not repeat, request_key=request_key, exact=document)

    return _change(caller, service, actor, step, context=context)
