"""Family-level export commands of the Admin command line (ADM-11 PR 8e).

These commands request the report exports whose rows name Families and
Members, through the export forms' own services:

- ``export financial`` is the financial report's export form
  (``financial_exports.create_financial_export``);
- ``export information`` is the information queue's export form
  (``information_exports.create_information_export``);
- ``export ministry`` is the Ministry report's export form, for the summary
  or one Ministry's join or leave list
  (``ministry_exports.create_ministry_export``);
- ``export ministry-packet`` is its follow-up packet form (the same service
  with ``action="packet"``).

Each creates the same durable export record as the page, so its rows reach
nothing but the file: the command prints the ``export status`` document,
and the file is fetched with the host wrapper's owner-only ``export fetch``
(PR 8b), never printed. Each admits as its page's form post does, with the
page's own admission function and capability (recording activity, so a
full-scope session); the export's own service then rechecks the capability
inside its lock, as for the page. Creating a financial export is one of
the specification's fresh-gated actions (#547): a live full-scope session
stands in for the page's recent sign-in through the caller-aware
``require_fresh``; it adds no hold of its own.

Each runs in the export lifecycle's command scope (the campaign's export
lock, the command session's row locked, the Administrator rechecked before
and after) and records the service's own ``export_requested`` and, only
when it created the export, ``admin_cmd_export_<kind>``. A repeated
request key returns the original export (``created`` false); a key used
for a different export is ``invalid``, as the page's 409.

Filters are the page's own, parsed by its query class, given as
``--filter NAME=VALUE``. A search is refused: its text names a Family and
would stay in the shell's history and the process list.
"""

from .admin_exports import (
    ExportChange,
    _change,
    _command_event,
    _command_scope,
    _refusal,
    _status_document,
)
from .admin_reads import NotAvailable, Unavailable, _recheck

# The one filter the commands refuse: free text that names a Family.
SEARCH = "search"


def filter_values(pairs):
    """``--filter NAME=VALUE`` pairs as the page's form fields, or refuse.

    Each name may be given once; a search is refused (``invalid``), as its
    text would name a Family on the command line. The page's query class
    validates the rest, so its refusals are the page's.
    """
    values = {}
    for pair in pairs or ():
        name, separator, value = pair.partition("=")
        if not separator or not name or name in values or name in {SEARCH, "page"}:
            raise ValueError("Each --filter is a page filter NAME=VALUE, once.")
        values[name] = value
    return values


def _page_refusals():
    """The page refusals a form check may raise before the export lock."""
    from .campaigns.read_guards import ReadUnavailable

    return (PermissionError, ReadUnavailable)


def _admit(caller, service, principal):
    """The page's form admission (its ``_principal``, recording activity).

    A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    try:
        return principal(caller, service.store)
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _create(caller, service, *, principal, action, parse, create, request_key, context):
    """Request one Family-level export as its page's form does.

    ``principal`` is the page's admission; ``parse(campaign_id)`` reads the
    form's values once the reader is admitted, in the page's order (it may
    run the page's campaign check first); ``create(actor, campaign_id,
    parsed)`` calls the page's export service for the current campaign (the
    pages report the current campaign only, until #145); ``action`` is the
    command's ``admin_cmd_export_<kind>`` event. A database refusal that
    changed nothing is ``unavailable``, as the pages' 503.
    """
    from .admin_report_reads import _current
    from .reports.export_models import ExportRequest

    actor = _admit(caller, service, principal)
    context["request_id"] = str(request_key)
    try:
        campaign_id = _current()
    except (NotAvailable, Unavailable):
        # As the page: the session is checked before the refusal is told.
        _recheck(caller, service.store, actor)
        raise
    try:
        parsed = parse(campaign_id)
    except _page_refusals() as error:
        # A campaign the page refuses (another current campaign, a read it
        # cannot admit now), after the session recheck, as 8b reports it.
        _refusal(caller, service, actor, error)

    def step(written):
        """Request the export inside the page's export lock."""
        with _command_scope(caller, service, actor, campaign_id=campaign_id):
            repeat = ExportRequest.objects.filter(
                requester_id=actor.identity, request_key=request_key
            ).exists()
            job = create(actor, campaign_id, parsed)
            if not repeat:
                _command_event(caller, actor, action)
                written.append(True)
            document = _status_document(service, actor, job.pk)
        return ExportChange(
            created=not repeat, request_key=request_key, export=document
        )

    return _change(caller, service, actor, step, context=context, conflicts=False)


def export_financial(caller, service, *, filters, fmt, zone, request_key, context):
    """``export financial``: the financial report's export form.

    As the page, the campaign must be one reports read (``admit_report_read``
    before the filters are read), and the capability is ``FINANCIAL_DETAIL``.
    As the page's view (``financial_export_views.create``), it then calls
    ``require_fresh`` before ``create_financial_export``: the gate lives in
    the view, not the service, so the command makes the call itself.
    """
    from .accounts.sessions import require_fresh
    from .audit.schemas import Action
    from .reports.financial import FinancialQuery
    from .reports.financial_exports import create_financial_export
    from .reports.financial_views import _principal
    from .reports.read_admission import admit_report_read

    def parse(campaign_id):
        """The page's campaign check, then its filters, in the page's order."""
        admit_report_read(campaign_id)
        return FinancialQuery.parse(filter_values(filters))

    def create(actor, campaign_id, query):
        """The page's fresh gate and service, rechecking the campaign inside
        its lock."""
        admit_report_read(campaign_id)
        # The automation branch locks the session row and records the
        # automation_fresh_gate event and notice in this transaction, so they
        # commit or roll back with the export; a read-only session is denied.
        # The notice names the campaign.
        caller.campaign_id = campaign_id
        require_fresh(caller)
        return create_financial_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            query=query,
            format=fmt,
            browser_timezone=zone,
            request_key=request_key,
        )

    return _create(
        caller,
        service,
        principal=_principal,
        action=Action.ADMIN_CMD_EXPORT_FINANCIAL,
        parse=parse,
        create=create,
        request_key=request_key,
        context=context,
    )


def export_information(
    caller, service, *, filters, history, fmt, zone, request_key, context
):
    """``export information``: the information queue's export form.

    ``history`` is the form's "include workflow history" choice. The
    capability is ``ADDITIONAL_FOLLOWUP``, as the page's.
    """
    from .audit.schemas import Action
    from .reports.information import InformationQuery
    from .reports.information_exports import create_information_export
    from .reports.information_views import _principal

    def create(actor, campaign_id, query):
        """The page's service."""
        return create_information_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            query=query,
            history=history,
            format=fmt,
            browser_timezone=zone,
            request_key=request_key,
        )

    return _create(
        caller,
        service,
        principal=_principal,
        action=Action.ADMIN_CMD_EXPORT_INFORMATION,
        parse=lambda campaign_id: InformationQuery.parse(filter_values(filters)),
        create=create,
        request_key=request_key,
        context=context,
    )


def _ministry_duid(value):
    """The page's Ministry check: 1 through 2**31 - 1, or ``invalid``."""
    if not 0 < value < 2**31:
        raise ValueError("A Ministry DUID is 1 through 2147483647.")
    return value


def export_ministry(
    caller, service, *, filters, ministry, requests, fmt, zone, request_key, context
):
    """``export ministry``: the Ministry report's export form.

    Without ``ministry`` it exports the summary (``action`` ``summary``);
    with ``ministry`` and ``requests`` (``join`` or ``leave``) that list, as
    the page's form on the join and leave lists. The page's admission
    (``can_report``) and the service's own scope check apply.
    """
    from .audit.schemas import Action
    from .reports.ministries import MinistryQuery
    from .reports.ministry_exports import create_ministry_export, offered_ministries
    from .reports.ministry_views import _principal

    def parse(campaign_id):
        """The page's form checks, once the reader is admitted.

        ``size`` chooses only the screen's window, which the page's export
        form never sends, so it is refused like ``page``.
        """
        if (ministry is None) != (requests is None):
            raise ValueError("--ministry and --requests go together.")
        if ministry is not None:
            _ministry_duid(ministry)
        values = filter_values(filters)
        if "size" in values:
            raise ValueError("The export has no page size.")
        return MinistryQuery.parse(values, detail=ministry is not None)

    def create(actor, campaign_id, query):
        """The page's service, for a Ministry this campaign offers.

        As the packet form's check: a Ministry the campaign does not offer
        is a bad selection (``invalid``), not an outage to retry.
        """
        if ministry is not None and ministry not in offered_ministries(campaign_id):
            raise ValueError("That Ministry is not in this campaign.")
        return create_ministry_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            query=query,
            ministry_id=ministry,
            action=requests or "summary",
            format=fmt,
            browser_timezone=zone,
            request_key=request_key,
        )

    return _create(
        caller,
        service,
        principal=_principal,
        action=Action.ADMIN_CMD_EXPORT_MINISTRY,
        parse=parse,
        create=create,
        request_key=request_key,
        context=context,
    )


def export_ministry_packet(
    caller, service, *, ministries, history, fmt, zone, request_key, context
):
    """``export ministry-packet``: the Ministry report's follow-up packet form.

    ``ministries`` are the ticked Ministries (none: every authorized one),
    at most the page's limit, each once; ``history`` the form's history
    choice. The page's admission and the service's scope check apply.
    """
    from .audit.schemas import Action
    from .reports.ministry_exports import MAX_PACKET_MINISTRIES, create_ministry_export
    from .reports.ministry_views import _principal

    def parse(campaign_id):
        """The packet form's selection checks, once the reader is admitted."""
        chosen = tuple(ministries or ())
        if len(chosen) > MAX_PACKET_MINISTRIES or len(set(chosen)) != len(chosen):
            raise ValueError("Choose each Ministry once, up to the page's limit.")
        for value in chosen:
            _ministry_duid(value)
        return tuple(sorted(chosen)) or None

    def create(actor, campaign_id, selected):
        """The page's service, as the packet form calls it."""
        return create_ministry_export(
            service.store,
            actor.identity,
            campaign_id=campaign_id,
            action="packet",
            ministries=selected,
            history=history,
            format=fmt,
            browser_timezone=zone,
            request_key=request_key,
        )

    return _create(
        caller,
        service,
        principal=_principal,
        action=Action.ADMIN_CMD_EXPORT_MINISTRY_PACKET,
        parse=parse,
        create=create,
        request_key=request_key,
        context=context,
    )
