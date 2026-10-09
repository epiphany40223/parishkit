"""Export lifecycle commands of the Admin command line (ADM-11 PR 8b).

A report export is requested on a page, rendered by the worker into a stored
file, and downloaded from its status page. These commands do each step
through the functions the pages use (``reports.export_services`` and
``reports.export_views.prepare_download``), for any export the Administrator
may see, so an export requested on a page can be followed and fetched from
the command line and the other way round:

- ``export create`` is the Participation page's export form
  (``create_export``), for the generation its fact set names;
- ``export status`` is the export status page's read (``export_state``),
  admitted passively like the page, with no view event (the page records
  none); ``--watch`` follows it until the export stops changing;
- ``export cancel``, ``export retry`` and ``export regenerate`` are the
  status page's buttons (``cancel_export``, ``retry_export``,
  ``regenerate_export``);
- ``export download --stream`` is the page's download: it issues and
  consumes the same one-use grant, streams the stored file under the same
  campaign read guard and fresh check, and records the same
  ``export_downloaded`` events. Its bytes go to standard output as they are
  read; the host wrapper's ``export fetch`` writes them to an owner-only
  file and checks them against ``export status``.

The state-changing commands admit as the page's form post does (recording
activity, so they need a full-scope session) and run inside a command scope
that locks the command session's row and rechecks the Administrator before
and after the effect. Each records the domain events the page's service
records and, in the same transaction, one ``admin_cmd_export_<verb>`` event
(subject: the automation session) only when it made the change: repeating
a request key (or cancelling an export already cancelled) returns the
original and records nothing new. A download records only the page's events,
as ``logs export`` does.

Documents carry identifiers, stored values, counts, sizes and digests only;
an export's rows reach nothing but the file.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import NotAvailable, ReadModel, Unavailable, _held, _recheck

# The export states in which the worker may still change it: the page offers
# Cancel in the first three, and an abandoned run is recovered by the worker.
# A test keeps them equal to ``jobs.models.NONTERMINAL_STATES``.
ACTIVE_STATES = frozenset({"queued", "running", "retry_wait", "abandoned"})
CANCELLABLE_STATES = frozenset({"queued", "running", "retry_wait"})


@dataclass(frozen=True)
class ExportStatus(ReadModel):
    """One export as its status page shows it, plus its stored file's receipt.

    ``state`` is the page's: the latest run's task state, or ``ready``,
    ``expired`` or ``cancelled``. ``file_name``, ``content_type``, ``size``,
    ``sha256`` and ``count`` (rows in the file) are None until the file is
    published (an expired export keeps them); ``export fetch`` checks what
    it wrote against them. ``changes_available`` is the page's own test of
    whether its Cancel, Retry and Regenerate buttons may act now: it is
    false while other campaign work holds the campaign, which is temporary.
    ``can_cancel`` is whether the page offers Cancel now. A watch stops once
    the worker can no longer change the export.
    """

    id: UUID
    campaign_id: UUID
    report: str
    format: str
    state: str
    created_at: str
    expires_at: str | None
    changes_available: bool
    can_cancel: bool
    file_name: str | None
    content_type: str | None
    size: int | None
    sha256: str | None
    count: int | None

    @property
    def terminal(self):
        """Ready, expired, cancelled, failed or succeeded exports stay so."""
        return self.state not in ACTIVE_STATES


@dataclass(frozen=True)
class ExportChange(ReadModel):
    """What ``export create``, ``cancel``, ``retry`` or ``regenerate`` did.

    ``created`` is false for a repeat (a request key used before, or an
    export already cancelled), which changed nothing. ``request_key`` is
    the key used (None for ``cancel``). ``export`` is the ``export status``
    document of the export afterwards: for ``regenerate``, the new export.
    """

    created: bool
    request_key: UUID | None
    export: dict


@dataclass(frozen=True)
class ExportDownload(ReadModel):
    """The file ``export download --stream`` wrote to standard output.

    ``size`` and ``sha256`` are the stored file's receipt, which the page's
    download verifies before it sends a byte; ``count`` is its rows.
    """

    id: UUID
    file_name: str
    content_type: str
    size: int
    sha256: str
    count: int


def export_model(status, publication, *, mutable):
    """The read model of ``export_services.export_state``'s result.

    ``mutable`` is ``campaign_mutable``'s answer for the export's campaign.
    """
    from .reports.export_views import CONTENT_TYPES

    published = publication is not None
    return ExportStatus(
        id=UUID(status["id"]),
        campaign_id=UUID(status["campaign_id"]),
        report=status["report"],
        format=status["format"],
        state=status["state"],
        created_at=status["created_at"],
        expires_at=status["expires_at"],
        changes_available=mutable,
        can_cancel=mutable and status["state"] in CANCELLABLE_STATES,
        # The page's download names its file the same way.
        file_name=f"{status['report']}.{status['format']}" if published else None,
        content_type=CONTENT_TYPES[status["format"]] if published else None,
        size=publication.size if published else None,
        sha256=publication.sha256 if published else None,
        count=publication.row_count if published else None,
    )


def campaign_mutable(campaign_id):
    """Whether the export's campaign accepts export changes now (the page's test).

    The status page asks ``admit_campaign(mutating=True)`` and disables its
    buttons when it refuses, which it does while other campaign work holds
    the campaign; a command run then is refused as ``denied``.
    """
    from .reports.export_services import admit_campaign

    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return False
    return True


# ---------------------------------------------------------------- admission


def _admit(caller, service, *, activity, ministry_jobs=True):
    """The export pages' admission (``export_views._principal``), or refuse.

    ``activity`` is true for a form post (it records activity, so a
    read-only session is refused) and false for the status page's passive
    read. A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin
    from .reports.export_views import _principal

    try:
        return _principal(
            caller,
            service.store,
            read_only=not activity,
            ministry_jobs=ministry_jobs,
        )
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _refusal(caller, service, actor, error):
    """Report an export page's refusal as the command line does.

    The session is rechecked first, so a session that ended meanwhile is
    exit 5 whatever the page refused. Then a missing export, fact set or
    file (an unknown identifier, a generation that is not ready, an expired
    file) is ``not_available``; the page's conflicts (cancelling a
    published export, regenerating one that has not expired, retrying a
    run that is not the latest failed one) are ``stale_version``; a read
    the campaign's guard could not finish is ``unavailable``. Any other
    refusal is raised as the page's own.
    """
    from django.core.exceptions import ObjectDoesNotExist

    from .campaigns.read_guards import ReadUnavailable
    from .jobs.storage import TaskRetryConflict
    from .reports.export_services import ExportConflict, ExportExpired
    from .reports.facts import FactUnavailable
    from .storage import StaleRecordError

    _recheck(caller, service.store, actor)
    if isinstance(error, (ObjectDoesNotExist, FactUnavailable, ExportExpired)):
        raise NotAvailable("That export is not available.") from None
    if isinstance(error, (ExportConflict, TaskRetryConflict)):
        raise StaleRecordError("The export changed; read it again.") from None
    if isinstance(error, ReadUnavailable):
        raise Unavailable("The export cannot be read now.") from None
    raise error


def _refusals():
    """The exceptions ``_refusal`` reports (imported once Django is set up)."""
    from django.core.exceptions import ObjectDoesNotExist

    from .campaigns.read_guards import ReadUnavailable
    from .jobs.storage import TaskRetryConflict
    from .reports.export_services import ExportConflict
    from .reports.facts import FactUnavailable

    # ExportExpired is a PermissionError. ExportConflict is the only
    # ValueError here: other ones (a request key bound to another export,
    # an unknown time zone) stay the page's ``invalid``.
    return (
        PermissionError,
        ObjectDoesNotExist,
        FactUnavailable,
        ExportConflict,
        TaskRetryConflict,
        ReadUnavailable,
    )


@contextmanager
def _command_scope(caller, service, actor, *, campaign_id=None):
    """The page's transaction, with the command session locked and rechecked.

    An export request and regeneration take only their campaign's export
    lock (``export_transaction``), as the page does; a cancel or retry takes
    the work order (``work_transaction``), as the services do. Either comes
    first, then the lock on the command session's row, so a revocation that
    wins it is seen before the effect; the Administrator is rechecked before
    and after it, and a session that ended rolls the effect back (exit 5).
    """
    from .accounts.models import PortalSession
    from .campaigns.work_locks import export_transaction, work_transaction

    lock = export_transaction(campaign_id) if campaign_id else work_transaction()
    with lock:
        PortalSession.objects.select_for_update().filter(
            session_id=caller.session.session_key
        ).first()
        _same(_recheck(caller, service.store, actor), actor)
        yield
        _same(_recheck(caller, service.store, actor), actor)


def _same(current, actor):
    """Refuse when the session now names another Administrator."""
    if current.identity != actor.identity:
        raise PermissionError("The command's Administrator changed.")


def _change(caller, service, actor, step, *, context, conflicts=True):
    """Run a state-changing step and classify what its failure means.

    ``step(written)`` runs inside ``_command_scope`` and appends to
    ``written`` once its effect and event are written: a database error
    after that may have struck the commit (exit 6, with the request key);
    before it, the transaction rolled back and nothing changed. A refusal
    is reported through ``_refusal``. With ``conflicts`` false (the
    Family-level export forms, whose pages answer every database refusal
    with 503), a database refusal that did not commit is ``unavailable``
    instead of ``stale_version``: their guards refuse inputs or a capture
    that are not available now, never a concurrent change to read again.
    """
    from .observability import _guard_refusal

    written = []

    def attempt():
        """One attempt; one repeated after an activating change starts over."""
        written.clear()
        return step(written)

    from .admin_operations import _raise_stale_on_conflict

    try:
        model = _held(attempt)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        if not conflicts:
            if context.get("committed"):
                raise
            _recheck(caller, service.store, actor)
            raise Unavailable("The export cannot be requested now; retry.") from None
        # A check or uniqueness conflict a concurrent change caused is the
        # page's 409, as for the delivery commands: stale_version.
        _raise_stale_on_conflict(error)
        raise
    except _refusals() as error:
        _refusal(caller, service, actor, error)
    context["committed"] = True
    return model


def _command_event(caller, actor, action):
    """Record the command's ``admin_cmd_export_<verb>`` event (``outcome`` only)."""
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action

    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor.identity,
        subject_id=caller.automation_session_id,
        context={"outcome": Outcome.SUCCEEDED},
    )


def _status_document(service, actor, export_id):
    """The ``export status`` document of one export, read in the caller's scope."""
    from .reports.export_services import export_state

    status, publication = export_state(service.store, actor.identity, export_id)
    mutable = campaign_mutable(UUID(status["campaign_id"]))
    return export_model(status, publication, mutable=mutable).to_document()


# ---------------------------------------------------------------- commands


def read_export(caller, service, export_id):
    """``export status``: the export status page's read.

    Admits passively as the page does (any session), reads through
    ``export_state`` and rechecks the session before reporting anything,
    including a refusal. The page records no view event, so neither does
    this. An unknown export is ``not_available``.
    """

    def step():
        """Admit, read and recheck, as the page."""
        from .reports.export_services import export_state

        actor = _admit(caller, service, activity=False)
        try:
            status, publication = export_state(service.store, actor.identity, export_id)
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        mutable = campaign_mutable(UUID(status["campaign_id"]))
        _recheck(caller, service.store, actor)
        return export_model(status, publication, mutable=mutable)

    return _held(step)


def create_export_command(
    caller, service, *, fact_set_id, fmt, zone, request_key, context
):
    """``export create``: the Participation page's export form.

    The page posts the generation it shows (its fact set) to that
    generation's campaign, so the campaign is the fact set's own; an
    unknown fact set, or one that is not ready, is ``not_available``. The
    same key returns the original export (``created`` false); a key used
    for a different export is ``invalid``, as the page's 409.
    """
    from .audit.schemas import Action
    from .reports.export_models import ExportRequest
    from .reports.export_services import create_export
    from .reports.models import CampaignDailyFactSet

    actor = _admit(caller, service, activity=True, ministry_jobs=False)
    context["request_id"] = str(request_key)
    campaign_id = (
        CampaignDailyFactSet.objects.filter(pk=fact_set_id)
        .values_list("campaign_id", flat=True)
        .first()
    )
    if campaign_id is None:
        _recheck(caller, service.store, actor)
        raise NotAvailable("No such participation generation.")

    def step(written):
        """Request the export inside the page's export lock."""
        with _command_scope(caller, service, actor, campaign_id=campaign_id):
            repeat = ExportRequest.objects.filter(
                requester_id=actor.identity, request_key=request_key
            ).exists()
            job = create_export(
                service.store,
                actor.identity,
                campaign_id=campaign_id,
                fact_set_id=fact_set_id,
                format=fmt,
                browser_timezone=zone,
                request_key=request_key,
            )
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_CREATE)
                written.append(True)
            document = _status_document(service, actor, job.pk)
        return ExportChange(
            created=not repeat, request_key=request_key, export=document
        )

    return _change(caller, service, actor, step, context=context)


def cancel_export_command(caller, service, export_id, *, context):
    """``export cancel``: the status page's Cancel.

    Cancelling an export already cancelled returns it (``created`` false);
    a published one is ``stale_version``, as the page's 409.
    """
    from .audit.schemas import Action
    from .reports.export_models import ExportCancellation
    from .reports.export_services import cancel_export

    actor = _admit(caller, service, activity=True)

    def step(written):
        """Cancel inside the work order, as the service does."""
        with _command_scope(caller, service, actor):
            repeat = ExportCancellation.objects.filter(request_id=export_id).exists()
            cancel_export(service.store, actor.identity, export_id)
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_CANCEL)
                written.append(True)
            document = _status_document(service, actor, export_id)
        return ExportChange(created=not repeat, request_key=None, export=document)

    return _change(caller, service, actor, step, context=context)


def retry_export_command(caller, service, export_id, *, request_key, context):
    """``export retry``: the status page's Retry for a failed export.

    The key is the page's retry ``request_key``: repeating it returns the
    original retry (``created`` false). A run that is not the latest failed
    one is ``stale_version``.
    """
    from .audit.schemas import Action
    from .reports.export_models import ExportRequest
    from .reports.export_services import retry_export

    actor = _admit(caller, service, activity=True)
    context["request_id"] = str(request_key)

    def step(written):
        """Retry inside the work order, as the service does."""
        with _command_scope(caller, service, actor):
            job = ExportRequest.objects.select_related("task").get(pk=export_id)
            repeat = job.task.chain_runs.filter(retry_command_id=request_key).exists()
            retry_export(
                service.store, actor.identity, export_id, request_key=request_key
            )
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_RETRY)
                written.append(True)
            document = _status_document(service, actor, export_id)
        return ExportChange(
            created=not repeat, request_key=request_key, export=document
        )

    return _change(caller, service, actor, step, context=context)


def _fresh_regeneration(service, actor, export_id):
    """Whether regenerating this export needs a recent sign-in (#547).

    A new Family-directory, mail-merge or financial file is a new copy of
    the codes or financial detail, so the status page asks for the same
    fresh sign-in as creating one (``export_ui.FRESH_REPORTS``). As the
    page does, the export's owner and capability are checked first
    (``authorize``), so its kind is never revealed to anyone else. An
    export that does not exist is False: ``regenerate_export`` refuses it.
    """
    from .reports.export_models import ExportRequest
    from .reports.export_services import authorize
    from .reports.export_ui import FRESH_REPORTS

    export = (
        ExportRequest.objects.only("report", "requester_id", "authorization_scope")
        .filter(pk=export_id)
        .first()
    )
    if export is None:
        return False
    authorize(service.store, actor.identity, request=export)
    return export.report in FRESH_REPORTS


def regeneration_prompts(caller, service, export_id):
    """Whether ``export regenerate`` asks at the prompt for this export.

    Only a fresh-gated regeneration does (``_fresh_regeneration``). Read
    before the command's transaction opens, as the prompt must be; the
    command scope decides again under its lock. Admits as the page's form
    post does, so a refusal here is the command's refusal.
    """
    from django.db import transaction

    actor = _admit(caller, service, activity=True)
    with transaction.atomic():
        return _fresh_regeneration(service, actor, export_id)


def regenerate_export_command(caller, service, export_id, *, request_key, context):
    """``export regenerate``: the status page's Regenerate for an expired file.

    It requests a new export from the original's retained inputs; the
    document is the new export's. Repeating the key returns it (``created``
    false). An export that has not expired is ``stale_version``. For a
    directory, mail-merge or financial export the page asks for a fresh
    sign-in first (#547): the command calls the caller-aware
    ``require_fresh`` in the export lock, where a live full-scope session
    passes and ``automation_fresh_gate`` is recorded with the new export.
    """
    from .accounts.sessions import require_fresh
    from .audit.schemas import Action
    from .reports.export_models import ExportRequest
    from .reports.export_services import regenerate_export

    actor = _admit(caller, service, activity=True)
    context["request_id"] = str(request_key)
    # The service reads the campaign before its lock the same way; an
    # export's campaign never changes.
    campaign_id = (
        ExportRequest.objects.filter(pk=export_id)
        .values_list("campaign_id", flat=True)
        .first()
    )
    if campaign_id is None:
        _recheck(caller, service.store, actor)
        raise NotAvailable("No such export.")

    def step(written):
        """Regenerate inside the campaign's export lock, as the service does."""
        with _command_scope(caller, service, actor, campaign_id=campaign_id):
            repeat = ExportRequest.objects.filter(
                requester_id=actor.identity, request_key=request_key
            ).exists()
            if _fresh_regeneration(service, actor, export_id):
                # The fresh-gate notice names the campaign. A repeated key
                # makes nothing, so it records no fresh-gate event or notice;
                # the session must still stand in for the sign-in, as the
                # page steps up on a repeat too.
                caller.campaign_id = campaign_id
                require_fresh(caller, record=not repeat)
            job = regenerate_export(
                service.store, actor.identity, export_id, request_key=request_key
            )
            if not repeat:
                _command_event(caller, actor, Action.ADMIN_CMD_EXPORT_REGENERATE)
                written.append(True)
            document = _status_document(service, actor, job.pk)
        return ExportChange(
            created=not repeat, request_key=request_key, export=document
        )

    return _change(caller, service, actor, step, context=context)


def _no_abort():
    """There is no HTTP transport to stop at the guard's deadline.

    The guard cancels its SQL and closes its connection, so the next chunk's
    check fails and the command stops; the guard logs what it stopped, the
    limit and the elapsed time (``read_guards.CampaignReadGuard._expire``).
    """


def download_export(caller, service, export_id, *, write, limits):
    """``export download --stream``: the status page's download.

    Admits as the page's download post does (recording activity, so it
    needs a full-scope session), issues and consumes the page's one-use
    grant, which records the opening ``export_downloaded`` with the row
    count, then reads the stored file under the page's campaign read guard
    and fresh check (``prepare_download``), handing each chunk to ``write``
    as it is read. The closing ``export_downloaded`` records ``succeeded``
    once every byte was handed over, ``failed`` otherwise, as the page's
    response does. The guard runs on this command's own connection with
    ``limits`` (the deployment's download lifetime). An expired file is
    ``not_available``.
    """
    from .campaigns.read_guards import CampaignReadGuard, GuardedResponse
    from .reports.export_services import issue_download
    from .reports.export_views import prepare_download

    actor = _admit(caller, service, activity=True)
    try:
        grant = issue_download(service.store, actor.identity, export_id)
        download = prepare_download(caller, service, actor, grant.pk)
    except _refusals() as error:
        _refusal(caller, service, actor, error)
    completed = False
    try:
        guard = CampaignReadGuard(
            [download.job.campaign_id],
            authorize=download.authorize,
            abort=_no_abort,
            limits=limits,
        )
        response = GuardedResponse(guard, download.open_content)
        try:
            for chunk in response:
                write(chunk)
        finally:
            response.close()
        completed = True
    except _refusals() as error:
        _refusal(caller, service, actor, error)
    finally:
        download.finish(completed)
    publication = download.publication
    return ExportDownload(
        id=download.job.pk,
        file_name=download.file_name,
        content_type=download.content_type,
        size=publication.size,
        sha256=publication.sha256,
        count=publication.row_count,
    )
