"""Digest commands of the Admin command line (ADM-11 PR 8d).

``digest daily SNAPSHOT_ID`` and ``digest weekly SNAPSHOT_ID`` read a
retained daily or weekly report, the page an emailed digest links to,
through the page's own functions (``digest_views`` and ``weekly_views``):
the stored snapshot, never the email's markup. A daily report is the
retained participation figures; a weekly report is the selected information
items' identifiers and states, never a Family's text. Each admits passively
with its page's admission (any session), reads inside the campaign read
guard the page's response holds (its deadline logs what it stopped, the
limit and the elapsed time), rechecks the session, and records the page's
``daily_digest_viewed`` or ``weekly_digest_viewed`` (started, then
succeeded or failed) through the page's own audit function.

``digest weekly-request`` is the Send a weekly report now page's form
(``weekly_manual.request_manual_report``) in the page's command scope:
the same once-at-a-time rules (a digest still in progress or awaiting
review is refused by the database, as on the page), the same task and
events, plus ``admin_cmd_digest_weekly_request`` when it created the
request. Its key is the page's ``command_id``: repeating it returns the
original request. The page asks the reader to tick that a manual report
may repeat items already reported; the command asks for the same
acknowledgement at the confirmation prompt, or takes ``--yes``.

``digest list`` is the Emailed reports page (``emailed_views.listing``):
the current campaign's retained daily and, for an Administrator, weekly
reports, by id and date only, so the other digest commands can be pointed
at them. It admits passively as the page does and, as the page's read,
records no event.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import NotAvailable, ReadModel, _held, _recheck
from .admin_report_reads import (
    _admit,
    _no_abort,
    _read_failures,
    _refusal,
    _refusals,
)


@dataclass(frozen=True)
class DailyDigest(ReadModel):
    """A retained daily report: the figures the email was compiled from.

    ``participation`` is the retained generation (its ``fact_set_id``,
    source and dates); ``statistics`` the active population's counts, with
    pledge totals as decimal strings when the campaign includes Financial
    stewardship; ``days`` the daily table. ``comparison`` is the retained
    Compared with past campaigns section (#1018 slice 7) as the page shows
    it: its day, date, the years compared, the headline ``figures`` (each
    ``figure``, ``value`` and ``comparisons`` text, the small-count
    protection already applied) and each imported year's checksum, without
    the charts' specifications; None when the digest has no section.
    """

    kind: str
    snapshot_id: UUID
    campaign_id: UUID
    mode: str
    observed_at: str
    participation: dict
    statistics: dict
    days: list
    comparison: dict | None = None


@dataclass(frozen=True)
class WeeklyDigest(ReadModel):
    """A retained weekly report: its selected items' identifiers and states.

    ``items`` is one page (``page`` of ``pages``) of the selection: each
    item's ``item_id``, whether it is new ``information`` or a correction,
    its ``captured`` and ``current`` disposition (stored keys) and whether
    that ``changed``. ``manual`` is true for a report an Administrator
    requested. The Families' text is never printed.
    """

    kind: str
    snapshot_id: UUID
    campaign_id: UUID
    manual: bool
    observed_at: str
    information_count: int
    correction_count: int
    total: int
    page: int
    pages: int
    items: list


@dataclass(frozen=True)
class WeeklyRequest(ReadModel):
    """The manual weekly report a ``digest weekly-request`` created or found.

    ``created`` is false for a repeated key, which returns the original
    request's task unchanged. Follow ``task`` with ``task show --watch``.
    """

    created: bool
    request_key: UUID
    task: dict


@dataclass(frozen=True)
class DigestList(ReadModel):
    """The Emailed reports page's lists: report ids and dates only.

    ``daily`` and ``weekly`` are the newest ``shown`` reports, newest first;
    ``weekly`` is None for a reader who is not an Administrator, who sees
    the daily list only, as on the page. ``weekly_request_available`` is
    whether the campaign has the Weekly Admin digest schedule that
    ``digest weekly-request`` needs. ``requested`` is the ``--requested``
    manual request's ``task_id`` and, once captured, ``report_id``; None
    when not asked for, or when the key is not a current-campaign request.
    """

    campaign_id: UUID | None
    daily: list
    weekly: list | None
    weekly_request_available: bool
    requested: dict | None
    shown: int


def acknowledgement():
    """The manual report page's acknowledgement, as its checkbox words it."""
    from .reports.weekly_manual_views import ManualReportForm

    return str(ManualReportForm.base_fields["acknowledge"].label)


def _section(section):
    """The command's projection of a retained comparison section, or None.

    Each headline figure is its name, value and comparison lines; the
    charts' specifications are left out (the page draws them).
    """
    if section is None:
        return None
    return {
        "day": section["day"],
        "report_date": section["report_date"],
        "compared_with": section["compared_with"],
        "figures": [
            {
                "figure": tile["label"],
                "value": tile["value"],
                "comparisons": tile["lines"],
            }
            for tile in section["tiles"]
        ],
        "archives": section["archives"],
    }


def daily_model(snapshot_id, campaign_id, document, mode):
    """The command's projection of a retained daily report's document."""
    from .admin_report_reads import _instant, day_rows, population

    chart, statistics = document.participation, document.statistics
    return DailyDigest(
        kind="daily",
        snapshot_id=snapshot_id,
        campaign_id=campaign_id,
        mode=mode,
        observed_at=_instant(statistics.observed_at),
        participation={
            "fact_set_id": chart.fact_set_id,
            "scope": chart.population_scope,
            "source_generation": chart.source_generation,
            "source_as_of": _instant(chart.source_as_of),
            "submission_watermark": chart.submission_watermark,
            "first_date": _instant(chart.first_date),
            "last_date": _instant(chart.last_date),
            "financial_enabled": chart.financial_enabled,
        },
        statistics={
            "source_generation": statistics.source_generation,
            "source_as_of": _instant(statistics.source_as_of),
            "submission_watermark": statistics.submission_watermark,
            "active": population(
                statistics.active, financial_enabled=statistics.financial_enabled
            ),
        },
        days=day_rows(chart),
        comparison=_section(document.comparison),
    )


def weekly_model(snapshot_id, campaign_id, context):
    """The command's projection of the weekly report page's context."""
    from .admin_report_reads import _instant

    return WeeklyDigest(
        kind="weekly",
        snapshot_id=snapshot_id,
        campaign_id=campaign_id,
        manual=context["manual"],
        observed_at=_instant(context["snapshot"].observed_at),
        information_count=context["information_count"],
        correction_count=context["correction_count"],
        total=context["total"],
        page=context["page"],
        pages=context["pages"],
        items=[
            {
                "item_id": row["value"].item_id,
                "information": row["information"],
                "captured": row["captured_key"],
                "current": row["current_key"],
                "changed": row["changed"],
            }
            for row in context["rows"]
        ],
    )


# ------------------------------------------------- digest daily and weekly


def _campaign(kind, snapshot_id):
    """The campaign of the retained ``kind`` report, or ``not_available``."""
    from .reports.digest_models import DailyDigestSnapshot
    from .reports.weekly_models import WeeklyDigestSnapshot

    model = DailyDigestSnapshot if kind == "daily" else WeeklyDigestSnapshot
    campaign_id = (
        model.objects.filter(pk=snapshot_id)
        .values_list("campaign_id", flat=True)
        .first()
    )
    if campaign_id is None:
        raise NotAvailable("No such retained report.")
    return campaign_id


def read_digest(caller, service, snapshot_id, *, kind, page):
    """``digest daily`` or ``digest weekly``: a retained report, as its page.

    ``kind`` is ``daily`` or ``weekly``. ``page`` (weekly only, as the page
    pages its items) is parsed by the page's ``page_number``; a page past
    the last is ``not_available``. The session is rechecked before anything
    is reported, a refusal included.
    """
    from .admin_reads import query
    from .audit.schemas import Outcome
    from .campaigns.read_guards import CampaignReadGuard, GuardedResponse
    from .observability import Event, emit_failure
    from .reports import digest_views, weekly_views
    from .reports.export_services import admit_campaign
    from .reports.export_views import _principal as report_principal
    from .reports.weekly_presentation import page_number
    from .storage import StorageInvariantError

    number = page_number(query(page=page))
    principal = weekly_views._principal if kind == "weekly" else report_principal

    def step():
        """Admit, read under the guard, recheck and audit, as the page."""
        # Each kind admits as its page does: a daily report for report
        # readers, a weekly one for Administrators only.
        actor = _admit(caller, service, principal)
        try:
            campaign_id = _campaign(kind, snapshot_id)
        except NotAvailable:
            # As the page: the session is checked before the id is judged.
            _recheck(caller, service.store, actor)
            raise
        views = weekly_views if kind == "weekly" else digest_views
        try:
            admit_campaign(campaign_id, mutating=False)
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        views.record_view(actor, snapshot_id, campaign_id, Outcome.STARTED)
        holder = {"result": None, "done": False}

        def authorize(guard):
            """The page's fresh admission and retention checks inside the guard."""
            fresh = principal(caller, service.store, read_only=True)
            if fresh.identity != actor.identity:
                raise PermissionError("Report access changed.")
            views.check_retained(snapshot_id, campaign_id)

        def content():
            """The page's read of the retained report."""
            if kind == "daily":
                document, mode = digest_views.retained_document(snapshot_id)
                holder["result"] = daily_model(snapshot_id, campaign_id, document, mode)
            else:
                context = weekly_views.retained_context(snapshot_id, page=number)
                holder["result"] = weekly_model(snapshot_id, campaign_id, context)
            guard.check()
            return iter(())

        guard = CampaignReadGuard([campaign_id], authorize=authorize, abort=_no_abort)
        try:
            try:
                GuardedResponse(guard, content).close()
            except _read_failures() as error:
                _refusal(caller, service, actor, error)
            _recheck(caller, service.store, actor)
            holder["done"] = True
            views.record_view(actor, snapshot_id, campaign_id, Outcome.SUCCEEDED)
            return holder["result"]
        finally:
            if not holder["done"]:
                # The page's ``finish(False)``; failing to record it never
                # hides the read's own error.
                try:
                    views.record_view(actor, snapshot_id, campaign_id, Outcome.FAILED)
                except (DatabaseError, StorageInvariantError) as error:
                    emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

    return _held(step)


# --------------------------------------------------------------- digest list


def list_model(campaign_id, lists, *, administrator):
    """The command's projection of the page's ``listing``: ids and dates only.

    The page's ``pk`` becomes ``snapshot_id``; a weekly row's
    ``preparation_id`` (the manual request's key) is left out, as the page
    shows only whether the report was ``manual``.
    """
    from .reports.emailed_views import SHOWN

    return DigestList(
        campaign_id=campaign_id,
        daily=[
            {
                "snapshot_id": row["pk"],
                "through_date": row["through_date"],
                "covered_dates": row["covered_dates"],
                "observed_at": row["observed_at"],
            }
            for row in lists["daily"]
        ],
        weekly=[
            {
                "snapshot_id": row["pk"],
                "manual": row["manual"],
                "observed_at": row["observed_at"],
            }
            for row in lists["weekly"]
        ]
        if administrator
        else None,
        weekly_request_available=lists["weekly_schedule"],
        requested=lists["requested"],
        shown=SHOWN,
    )


def read_list(caller, service, *, requested):
    """``digest list``: the Emailed reports page's lists, as the page reads them.

    Admits passively with the page's admission (report readers, any
    session scope), reads the page's ``listing`` for the current campaign
    (None, and empty lists, when there is none) and rechecks the session
    before anything is reported. ``requested`` is a manual request key or
    None. The page's read records no event, so neither does this. Like the
    page's ``.get()``, a missing configuration row fails closed: its
    ``ObjectDoesNotExist`` is ``not_available``, never empty lists.
    """
    from .accounts.runtime_models import SystemConfiguration
    from .reports.emailed_views import listing
    from .reports.export_views import _principal

    def step():
        """Admit, read the page's lists and recheck."""
        actor = _admit(caller, service, _principal)
        administrator = "administrator" in actor.roles
        # .get(), as the page: a missing row must not read as "no campaign".
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        lists = listing(campaign_id, administrator=administrator, requested=requested)
        _recheck(caller, service.store, actor)
        return list_model(campaign_id, lists, administrator=administrator)

    return _held(step)


# --------------------------------------------------- digest weekly-request


def request_weekly(caller, service, *, request_key, context):
    """``digest weekly-request``: the Send a weekly report now form.

    Admits as the page's form post does (recording activity, so a full-scope
    session), for the current campaign, which needs a weekly digest
    schedule (``not_available`` without one, as the page explains). In the
    page's command scope it calls the page's ``request_manual_report`` with
    the applied configuration (the page's form binds the one shown) and
    ``request_key`` as the ``command_id``, and records
    ``admin_cmd_digest_weekly_request`` only when it created the request. A
    digest still in progress or awaiting review is refused by the database,
    as on the page: ``stale_version``. A key bound to another request is
    ``invalid``. ``context["committed"]`` is set once the request may have
    committed (exit 6 then names the key).
    """
    from .accounts.runtime_models import SystemConfiguration
    from .admin_operations import _admit as admit_form
    from .admin_operations import (
        _ended_or_raise,
        _raise_stale_on_conflict,
        task_retry_model,
    )
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .reports.weekly_manual import request_manual_report
    from .reports.weekly_manual_views import _has_weekly_schedule
    from .reports.weekly_models import WeeklyManualRequest

    actor = admit_form(caller, service)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """Request the report inside the page's command scope."""
        written.clear()
        system = SystemConfiguration.objects.get()
        campaign_id = system.current_campaign_id
        if campaign_id is None:
            raise NotAvailable("There is no current campaign.")
        if not _has_weekly_schedule(campaign_id):
            raise NotAvailable("The campaign has no weekly report schedule.")
        with command_scope(caller, service, actor):
            repeat = WeeklyManualRequest.objects.filter(pk=request_key).exists()
            task = request_manual_report(
                service.store,
                actor.identity,
                campaign_id,
                command_id=request_key,
                configuration_id=system.active_configuration_id,
            )
            if not repeat:
                record_action(
                    Action.ADMIN_CMD_DIGEST_WEEKLY_REQUEST,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
        retry = task_retry_model(task, created=not repeat, request_key=request_key)
        return WeeklyRequest(
            created=retry.created, request_key=request_key, task=retry.task
        )

    try:
        model = _held(step)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        # The page's 409: a digest still in progress or awaiting review, or
        # a scope that changed meanwhile.
        _raise_stale_on_conflict(error)
        raise
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return model
