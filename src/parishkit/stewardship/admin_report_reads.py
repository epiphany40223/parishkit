"""Aggregate report reads of the Admin command line (ADM-11 PR 8c).

Each ``report …`` command reads one campaign report page through the
functions the page itself uses, and prints counts and aggregates only:

- ``report list`` is the reports root: the current campaign and the reports
  the reader's Admin menu offers (``admin_context._navigation_items``);
- ``report participation`` is the Participation page
  (``guarded_participation`` and ``calculate_statistics``), with the
  ``fact_set_id`` that ``export create --fact-set`` takes;
- ``report responses`` is the response dashboard (``dashboard_context``'s
  ``response_metrics``);
- ``report financial`` and ``report talents`` are the summaries of the
  financial detail and talents reports (``financial_page``,
  ``talents_report``), without their Family and Member rows;
- ``report information`` is the information queue's matching count
  (``information_page``);
- ``report ministry`` is the Ministry summary (``ministry_page``), or with
  ``--ministry`` one Ministry's join or leave request count.

Every command reads the current campaign, as the pages do until the
single-campaign change (#145). It admits passively with the page's own
admission function and capability, so a read-only session can run it;
checks the campaign with the page's ``admit_report_read``; reads inside the
campaign read guard the page's response holds (interactive lifetime, on the
command's own connection: a deadline is logged by the guard with what it
stopped, the limit and the elapsed time); rechecks the session before
reporting anything, including a refusal; and records the page's own view
events through the page's own audit function, started and then succeeded or
failed where the page records both. Where the page's event counts the rows
it displayed, the command records 0: it displays no row.

Documents hold counts, states, stored keys, identifiers, instants and money
totals as decimal strings: never a Family's or Member's name, DUID, contact
or answer, and never search text (no command accepts any).
"""

from dataclasses import dataclass
from functools import partial
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, Unavailable, _held, _recheck

# The report pages ``report list`` names, by URL name, with their commands.
REPORT_COMMANDS = {
    "response_dashboard": "report responses",
    "participation": "report participation",
    "financial_report": "report financial",
    "talents_report": "report talents",
    "information_queue": "report information",
    "ministry_report": "report ministry",
}


@dataclass(frozen=True)
class ReportList(ReadModel):
    """The reports root: the campaign reports open on, and the reports offered.

    ``campaign_id`` is the current campaign when it can be reported, else
    None (the page's "no campaign" page). ``reports`` has one row per report
    the reader's Admin menu offers: ``command``, the report's ``page`` (its
    URL name) and ``available``, false while the menu greys it out (no
    current campaign, or a module the campaign does not include).
    """

    campaign_id: UUID | None
    reports: list


@dataclass(frozen=True)
class ParticipationReport(ReadModel):
    """The Participation page: one generation's statistics and daily table.

    ``status`` is the page's: ``current``, ``updating`` (an older complete
    generation is shown while a newer one is prepared) or ``unavailable``
    (none is complete; the generation members are then None and ``days``
    empty). ``fact_set_id`` is the generation, as ``export create
    --fact-set`` takes it. ``statistics`` holds the active population's
    counts and, as the page shows it (#728), the comparison pledge total of
    every Family (``comparison_pledge_all``); the pledge totals are decimal
    strings, None when unavailable or when the campaign does not include
    Financial stewardship.
    """

    campaign_id: UUID
    scope: str
    status: str
    selected_at: str
    fact_set_id: UUID | None
    source_generation: int | None
    source_as_of: str | None
    submission_watermark: int | None
    first_date: str | None
    last_date: str | None
    financial_enabled: bool
    statistics: dict
    days: list


@dataclass(frozen=True)
class ResponseReport(ReadModel):
    """The response dashboard's funnel at one instant, counts only.

    ``metrics`` is None in Testing mode when the campaign has no active
    rehearsal (the page then shows no figures). Otherwise it holds the
    stage counts in funnel order, the three figures beside the funnel, the
    activity buckets at ``grain``, the Family email sends marked on them
    (each by its Outgoing mail ``send`` token) and each list's length
    (``lists``; the data-quality list has none on the page either).
    """

    campaign_id: UUID
    mode: str
    metrics: dict | None


@dataclass(frozen=True)
class FinancialReport(ReadModel):
    """The financial detail report's summary over every pledge, without rows.

    ``frequencies`` and ``shares`` count by stored key (a share method by
    its option id, as the campaign's share settings list it, including an
    option no longer offered); ``annual_total`` is a decimal string.
    """

    campaign_id: UUID
    source_generation: int
    source_as_of: str
    comparison_start: str | None
    comparison_end: str | None
    giving_through: str | None
    families: int
    annual_total: str | None
    frequencies: dict
    shares: list
    no_share: int
    cannot_give: int


@dataclass(frozen=True)
class TalentsReport(ReadModel):
    """The talents and limitations report's summary, without its rows.

    ``talents`` counts Members per talent by its stored key, as the
    campaign's talent settings list it (including a talent no longer
    offered); ``collects_talents`` is false when the campaign offers none.
    """

    campaign_id: UUID
    collects_talents: bool
    members: int
    cannot_serve: int
    cannot_attend: int
    talents: list


@dataclass(frozen=True)
class InformationReport(ReadModel):
    """How many information queue items match the page's filters."""

    campaign_id: UUID
    filters: dict
    source_as_of: str
    matching: int


@dataclass(frozen=True)
class MinistryReport(ReadModel):
    """The Ministry summary page, or one Ministry's request count.

    Without ``ministry``: one page of ``summaries`` (each Ministry's DUID as
    ``ministry``, its name, state and request counts) and ``matching``, every matching
    Ministry. With ``ministry`` and ``requests`` (``join`` or ``leave``):
    ``matching`` is that list's request count under ``filters``, and
    ``summaries`` that Ministry's row; no Member is listed.
    """

    campaign_id: UUID
    ministry: int | None
    requests: str | None
    filters: dict
    page: int
    size: int
    matching: int
    summaries: list


# ---------------------------------------------------------------- the reads


def _admit(caller, service, principal):
    """The page's admission (its ``_principal``), passive, or refuse.

    ``principal`` is the page's admission function; it is called read-only,
    so no activity is recorded and a read-only session reads. A session
    that ended since the command was admitted is exit 5 (``session_ended``),
    not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    try:
        return principal(caller, service.store, read_only=True)
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _current():
    """The current campaign's id; no current campaign is ``not_available``."""
    from .accounts.runtime_models import SystemConfiguration

    configuration = SystemConfiguration.objects.first()
    if configuration is None or configuration.restore_review_required:
        raise Unavailable("Reports cannot be read now.")
    if configuration.current_campaign_id is None:
        raise NotAvailable("There is no current campaign.")
    return configuration.current_campaign_id


def _refusals():
    """The page refusals ``_refusal`` reports (imported once Django is set up)."""
    from django.core.exceptions import ObjectDoesNotExist

    from .campaigns.read_guards import ReadUnavailable
    from .reports.facts import FactUnavailable
    from .reports.statistics import StatisticsUnavailable
    from .storage import StorageInvariantError

    # StatisticsUnavailable is a ValueError: listed here, it is the page's
    # 503 (``unavailable``), never a refused option (``invalid``).
    return (
        PermissionError,
        ObjectDoesNotExist,
        NotAvailable,
        ReadUnavailable,
        FactUnavailable,
        StatisticsUnavailable,
        StorageInvariantError,
    )


def _read_failures():
    """What a guarded report read may raise: the refusals, and its failures.

    Inside the guard, a database error (the guard's own cancellation at its
    deadline included) and a ``ValueError`` (a value the read could not
    shape, or ``StatisticsUnavailable``) are failures of the read, which the
    pages answer with 503, never a refusal of the reader or their options:
    the options were parsed before the read began.
    """
    from django.db import DatabaseError

    return (*_refusals(), DatabaseError, ValueError)


def _refusal(caller, service, actor, error, *, shaping=False):
    """Report a report page's refusal or failure as the command line does.

    The session is rechecked first, so a session that ended meanwhile is
    exit 5 whatever the page refused. The campaign no longer being the
    current one (the page's 410) is ``stale_version``; a missing record, or
    a report the campaign does not include, is ``not_available``; a read
    the campaign's guard, inputs or database could not finish, or whose
    values could not be shaped (the page's 503), is ``unavailable`` (exit
    3: retry). ``shaping`` marks a page that also logs a shaping failure
    (``report_shaping_failed``), which every retry would reproduce. Any
    other refusal is raised as the page's own (``denied``).
    """
    from django.core.exceptions import ObjectDoesNotExist

    from .observability import Event, emit_failure
    from .storage import StaleRecordError
    from .web.refusals import UserFacingGone

    _recheck(caller, service.store, actor)
    if shaping and isinstance(error, ValueError):
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
    if isinstance(error, UserFacingGone):
        raise StaleRecordError("The current campaign changed; read again.") from None
    if isinstance(error, (ObjectDoesNotExist, NotAvailable)):
        raise NotAvailable("That report is not available.") from None
    if not isinstance(error, PermissionError):
        raise Unavailable("The report cannot be read now.") from None
    raise error


def _no_abort():
    """There is no transport to stop at the guard's deadline.

    The guard cancels its SQL and closes its connection, so the read fails
    and the command stops; the guard logs what it stopped, the limit and
    the elapsed time (``read_guards.CampaignReadGuard._expire``).
    """


def _report(
    caller,
    service,
    *,
    principal,
    read,
    audit=None,
    started=False,
    admit=None,
    shaping=False,
):
    """Run one report page's read as the page's response does, and audit it.

    ``principal`` is the page's admission function; ``read(guard, actor,
    campaign_id)`` the page's read inside the campaign read guard, returning
    ``(model, values)`` where ``values`` are the page's audit counts;
    ``audit(actor, campaign_id, outcome, values)`` the page's audit, called
    with ``started`` first when the page records it, then once with
    succeeded or failed (with no values for a failure, as the page records
    the counts it had reached, which a failed read has not).

    The guard's ``authorize`` repeats the page's admission and campaign
    check inside the guard, so a role change or another current campaign
    ends the read as on the page. ``admit(actor, campaign_id)`` is a page's
    own further check before anything is recorded (the dashboard's Testing
    rule, the Ministry report's campaigns, a report's module).
    ``shaping`` marks a page that logs a value it could not shape.
    """
    from django.db import DatabaseError

    from .audit.schemas import Outcome
    from .campaigns.read_guards import CampaignReadGuard, GuardedResponse
    from .observability import Event, emit_failure
    from .reports.read_admission import admit_report_read
    from .storage import StorageInvariantError

    def step():
        """Admit, read under the guard, recheck and audit, as the page."""
        actor = _admit(caller, service, principal)
        campaign_id = _current()
        try:
            if admit is not None:
                admit(actor, campaign_id)
            admit_report_read(campaign_id)
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        if audit is not None and started:
            audit(actor, campaign_id, Outcome.STARTED, None)
        holder = {"actor": actor, "result": None, "done": False}

        def authorize(guard):
            """The page's fresh admission and campaign check inside the guard."""
            fresh = principal(caller, service.store, read_only=True)
            if fresh.identity != actor.identity:
                raise PermissionError("Report access changed.")
            holder["actor"] = fresh
            admit_report_read(campaign_id)

        def content():
            """The page's read, under the guard and its fresh admission."""
            holder["result"] = read(guard, holder["actor"], campaign_id)
            # As the pages check before releasing what they read.
            guard.check()
            return iter(())

        guard = CampaignReadGuard([campaign_id], authorize=authorize, abort=_no_abort)
        try:
            try:
                response = GuardedResponse(guard, content)
                response.close()
            except _read_failures() as error:
                _refusal(caller, service, actor, error, shaping=shaping)
            model, values = holder["result"]
            _recheck(caller, service.store, actor)
            # The read is complete: from here no failed event is recorded.
            # Unlike the page, which has already sent its bytes, a failure
            # to record the success withholds the document (exit 3).
            holder["done"] = True
            if audit is not None:
                audit(actor, campaign_id, Outcome.SUCCEEDED, values)
            return model
        finally:
            if audit is not None and not holder["done"]:
                # The page's ``finish(False)``: a failed read is recorded,
                # and failing to record it never hides the read's own error.
                try:
                    audit(actor, campaign_id, Outcome.FAILED, None)
                except (DatabaseError, StorageInvariantError) as error:
                    emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

    return _held(step)


def _instant(value):
    """An instant as UTC ISO 8601, or None."""
    from .admin_reads import plain

    return plain(value) if value is not None else None


def _money(amount):
    """A ``MoneyAmount`` as a decimal string, or None when unavailable."""
    return amount.canonical if amount is not None else None


def _modules(campaign_id):
    """The campaign's modules (financial, ministry, census)."""
    from .campaigns.models import Campaign

    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    return (campaign.active_configuration.values or {}).get("modules", ())


def _require_module(campaign_id, module):
    """Refuse a report whose module the campaign does not include.

    The Admin menu greys the report out; the page refuses it. The command
    says the report is ``not_available`` for this campaign.
    """
    if module not in _modules(campaign_id):
        raise NotAvailable(f"This campaign does not include the {module} module.")


# ------------------------------------------------------------- report list


def read_report_list(caller, service):
    """``report list``: the reports root and the report entries of the menu.

    Admits as the reports root does (``CAMPAIGN_REPORT``, or a Ministry
    leader, whom the root sends to the Ministry report; passively); the
    root records no view event, so neither does this. The menu decides
    which report rows the reader is offered. The campaign is the
    current one when the root would open its report (a reportable state
    that ``admit_report_read`` admits), else None.
    """
    from .accounts.admin_context import _current_campaign, _navigation_items
    from .accounts.policy import Capability, allows
    from .accounts.runtime_models import SystemConfiguration
    from .reports.export_views import _principal
    from .reports.read_admission import admit_report_read
    from .reports.workspace_views import campaign_ids

    def step():
        """Admit, choose the campaign, build the menu rows and recheck."""
        actor = _admit(caller, service, partial(_principal, ministry_jobs=True))
        configuration = SystemConfiguration.objects.select_related(
            "active_configuration__parish", "current_campaign__active_configuration"
        ).first()
        if configuration is None or configuration.restore_review_required:
            raise Unavailable("Reports cannot be read now.")
        current = configuration.current_campaign_id
        try:
            if current is not None and current in campaign_ids():
                admit_report_read(current)
            else:
                current = None
        except _refusals() as error:
            _refusal(caller, service, actor, error)
        items = _navigation_items(
            actor,
            allows(actor, Capability.CONFIGURE),
            _current_campaign(configuration),
            configuration,
        )
        reports = [
            {
                "command": REPORT_COMMANDS[item.name],
                "page": item.name,
                "available": item.reason is None,
            }
            for item in items
            if item.name in REPORT_COMMANDS
        ]
        _recheck(caller, service.store, actor)
        return ReportList(campaign_id=current, reports=reports)

    return _held(step)


# ------------------------------------------------------- participation


def population(statistics, *, financial_enabled):
    """One population's counts; pledge totals only with Financial stewardship."""
    if statistics is None:
        return None
    return {
        "families": statistics.families,
        "active_members": statistics.active_members,
        "eligible_email": statistics.eligible_email,
        "deliverable_email": statistics.deliverable_email,
        "responses": statistics.responses,
        "annual_pledge": _money(statistics.annual_pledge)
        if financial_enabled
        else None,
    }


def participation_model(campaign_id, query, selection, statistics):
    """The command's projection of the Participation page's read."""
    document = selection.document
    days = [
        {
            "date": day.local_date.isoformat(),
            "first_responses": day.first_responses
            if day.population_available
            else None,
            "cumulative_responses": day.cumulative_responses
            if day.population_available
            else None,
            "cohort_denominator": day.cohort_denominator
            if day.population_available
            else None,
            "pledge_total": format(day.pledge_total, "f")
            if day.pledge_available and day.pledge_total is not None
            else None,
        }
        for day in (document.days if document is not None else ())
    ]
    financial = statistics.financial_enabled
    return ParticipationReport(
        campaign_id=campaign_id,
        scope=query.scope,
        status=selection.status,
        selected_at=_instant(selection.selected_at),
        fact_set_id=document.fact_set_id if document is not None else None,
        source_generation=document.source_generation if document else None,
        source_as_of=_instant(document.source_as_of) if document else None,
        submission_watermark=document.submission_watermark if document else None,
        first_date=_instant(document.first_date) if document else None,
        last_date=_instant(document.last_date) if document else None,
        financial_enabled=financial,
        statistics={
            "observed_at": _instant(statistics.observed_at),
            "source_generation": statistics.source_generation,
            "source_as_of": _instant(statistics.source_as_of),
            "submission_watermark": statistics.submission_watermark,
            "active": population(statistics.active, financial_enabled=financial),
            # One aggregate over every Family's comparison pledges (#728).
            "comparison_pledge_all": _money(statistics.comparison_pledge_all)
            if financial
            else None,
        },
        days=days,
    )


def read_participation(caller, service, *, scope):
    """``report participation``: the Participation page's generation and statistics.

    ``scope`` is the page's option, parsed by the page's ``ReportQuery`` (a
    refusal is ``invalid``). Records the page's
    ``participation_viewed``, started and then succeeded or failed.
    """
    from .admin_reads import query as parameters
    from .reports.export_views import _principal
    from .reports.selection import guarded_participation
    from .reports.statistics import calculate_statistics
    from .reports.statistics_selection import capture_statistics
    from .reports.workspace import ReportQuery
    from .reports.workspace_views import _audit

    query = ReportQuery.parse(parameters(scope=scope))

    def read(guard, actor, campaign_id):
        """The page's selection and statistics, as ``_page_context`` reads them."""
        with guarded_participation(
            guard,
            campaign_id=campaign_id,
            population_scope=query.scope,
            browser_timezone=query.timezone,
        ) as selection:
            statistics = calculate_statistics(capture_statistics(campaign_id))
            return participation_model(campaign_id, query, selection, statistics), None

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=lambda actor, campaign_id, outcome, values: _audit(
            actor, campaign_id, outcome
        ),
        started=True,
    )


# ----------------------------------------------------------- responses


def response_model(campaign_id, query, context):
    """The command's projection of the response dashboard's page context."""
    from .reports.response_lists import list_counts

    metrics = context["metrics"]
    if metrics is None:
        return ResponseReport(campaign_id=campaign_id, mode=query.mode, metrics=None)
    return ResponseReport(
        campaign_id=campaign_id,
        mode=query.mode,
        metrics={
            "as_of": _instant(metrics.as_of),
            "timezone": metrics.timezone,
            "grain": metrics.grain,
            "stages": [
                {"key": stage.key, "count": stage.count} for stage in metrics.stages
            ],
            "skipped_responded": metrics.skipped_responded,
            "submitted_uninvited": metrics.submitted_uninvited,
            "submitted_again": metrics.submitted_again,
            "activity": [
                {
                    "start": _instant(bucket.start),
                    "links": bucket.links,
                    "forms": bucket.forms,
                    "submissions": bucket.submissions,
                }
                for bucket in metrics.activity
            ],
            "sends": [
                {
                    "send": send.key.token,
                    "kind": send.kind,
                    "scheduled": _instant(send.scheduled),
                    "delivered": send.delivered,
                    "first_delivered_at": _instant(send.first_delivered_at),
                    "last_delivered_at": _instant(send.last_delivered_at),
                }
                for send in metrics.sends
            ],
            "lists": list_counts(metrics.families),
        },
    )


def read_responses(caller, service, *, mode, grain):
    """``report responses``: the response dashboard, counts only.

    ``mode`` and ``grain`` are the page's options, parsed by its
    ``DashboardQuery``; Testing is for Administrators, as on the page.
    Records the page's ``response_dashboard_viewed`` once the read ends.
    """
    from .admin_reads import query as parameters
    from .reports.response_dashboard import (
        DashboardQuery,
        _admit_mode,
        _audit,
        _principal,
        dashboard_context,
    )

    query = DashboardQuery.parse(parameters(mode=mode, grain=grain))

    def read(guard, actor, campaign_id):
        """The page's one guarded read of the funnel, shaped as the page."""
        # The page repeats its Testing rule inside the guard, too.
        _admit_mode(actor, query)
        context = dashboard_context(
            campaign_id, query, can_test="administrator" in actor.roles
        )
        return response_model(campaign_id, query, context), None

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=lambda actor, campaign_id, outcome, values: _audit(
            actor, campaign_id, outcome
        ),
        # Before anything is recorded, as on the page: a refused Testing
        # read records nothing.
        admit=lambda actor, campaign_id: _admit_mode(actor, query),
        shaping=True,
    )


# ----------------------------------------------------------- financial


def financial_model(campaign_id, result):
    """The command's projection of ``financial_page``'s summary."""
    summary, metadata = result["summary"], result["metadata"]
    return FinancialReport(
        campaign_id=campaign_id,
        source_generation=metadata["source_generation"],
        source_as_of=_instant(metadata["source_as_of"]),
        comparison_start=metadata.get("comparison_start"),
        comparison_end=metadata.get("comparison_end"),
        giving_through=_instant(metadata["giving_through"]),
        families=summary["families"],
        annual_total=_money(summary["annual_total"]),
        frequencies=dict(summary["frequency_counts"]),
        shares=[
            {"id": key, "count": count}
            for key, count in sorted(summary["share_counts"].items())
        ],
        no_share=summary["no_share"],
        cannot_give=summary.get("cannot_give") or 0,
    )


def read_financial(caller, service):
    """``report financial``: the financial detail report's summary.

    Reads the page's ``financial_page`` without filters (one row per page,
    which is never printed) for its summary over every pledge. Records the
    page's ``financial_report_viewed``, started and then succeeded with
    ``count`` 0 (no row displayed) and the page's ``matching_count``.
    """
    from .accounts.runtime_models import SystemConfiguration
    from .campaigns.models import Campaign
    from .reports.financial import FinancialQuery, financial_page, giving_proof
    from .reports.financial_views import _audit, _principal

    def read(guard, actor, campaign_id):
        """The page's read of its first page, for the summary it carries.

        Without Financial stewardship the page refuses inside its read,
        after its started event, so the command does too (started, then
        failed): the events stay the page's.
        """
        _require_module(campaign_id, "financial")
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=campaign_id
        )
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        result = financial_page(
            campaign_id,
            FinancialQuery(),
            actor,
            proof=giving_proof(campaign),
            parish_name=system.active_configuration.parish.name,
            configuration=campaign.active_configuration.values,
            page_size=1,
        )
        return financial_model(campaign_id, result), {"total": result["total"]}

    def audit(actor, campaign_id, outcome, values):
        """The page's audit: rows displayed (none) and the matching count."""
        _audit(actor, campaign_id, outcome, 0, (values or {}).get("total", 0))

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=audit,
        started=True,
        shaping=True,
    )


# ------------------------------------------------------------- talents


def talents_model(campaign_id, result):
    """The command's projection of ``talents_report``'s summary."""
    summary = result["summary"]
    return TalentsReport(
        campaign_id=campaign_id,
        collects_talents=result["collects_talents"],
        members=summary["members"],
        cannot_serve=summary["cannot_serve"],
        cannot_attend=summary["cannot_attend"],
        talents=[
            {"key": key, "count": count}
            for key, count in sorted(summary["talent_counts"].items())
        ],
    )


def read_talents(caller, service):
    """``report talents``: the talents and limitations report's summary.

    Reads the page's ``talents_report`` without filters (``TalentQuery()``,
    offered as the page offers it) and prints its summary only. Records the
    page's ``talents_report_viewed`` once the read ends, with ``count`` 0
    (no Member or Family row is displayed) and the page's ``audit_choices``:
    the offered filter and, when it held still during the read, the
    ParishSoft snapshot the report read.
    """
    from .audit.schemas import Action
    from .campaigns.models import Campaign
    from .reports.response_lists import current_snapshot
    from .reports.talent_views import _audit, _principal, audit_choices
    from .reports.talents import TalentQuery, talents_report

    def read(guard, actor, campaign_id):
        """The page's read, with the page's offered (default) filters."""
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=campaign_id
        )
        configuration = campaign.active_configuration.values
        shown = TalentQuery().offered(configuration)
        # As the page: the snapshot is named only when no refresh promoted
        # another between the statements of the read.
        before = current_snapshot()
        result = talents_report(campaign_id, shown, actor, configuration=configuration)
        snapshot = before if current_snapshot() == before else None
        return talents_model(campaign_id, result), audit_choices(shown, snapshot)

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=lambda actor, campaign_id, outcome, values: _audit(
            Action.TALENTS_REPORT_VIEWED,
            actor,
            campaign_id,
            outcome,
            0,
            values or audit_choices(TalentQuery(), None),
        ),
        shaping=True,
    )


# --------------------------------------------------------- information


def read_information(caller, service, filters):
    """``report information``: how many information items match the filters.

    ``filters`` are the queue's non-identifying filters (``disposition``,
    ``needed``, ``completed``, ``start``, ``end``; None when not given),
    parsed by the page's ``InformationQuery`` (a refusal is ``invalid``).
    Records the page's ``information_viewed``, started and then succeeded.
    """
    from .reports.information import InformationQuery, information_page
    from .reports.information_views import _audit, _principal

    query = InformationQuery.parse(
        {key: value for key, value in filters.items() if value is not None}
    )

    def read(guard, actor, campaign_id):
        """The queue's read of one row, for the matching count it carries."""
        result = information_page(campaign_id, query, page_size=1)
        return (
            InformationReport(
                campaign_id=campaign_id,
                filters={
                    key: value
                    for key, value in query.form_values().items()
                    if key not in {"search", "sort"}
                },
                source_as_of=_instant(result["metadata"]["source_as_of"]),
                matching=result["total"],
            ),
            None,
        )

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=lambda actor, campaign_id, outcome, values: _audit(
            actor, campaign_id, outcome
        ),
        started=True,
    )


# ------------------------------------------------------------ ministry


# The Ministry summary row members the command prints, by the selection's
# names (``schema/ministry_reports.sql``): identity, state and counts. The
# Ministry's DUID is printed as ``ministry``, the value ``--ministry`` takes.
MINISTRY_SUMMARY = {
    "duid": "ministry",
    "name": "name",
    "active": "active",
    "in_campaign": "in_campaign",
    "joining": "joining",
    "leaving": "leaving",
    "unresolved": "unresolved",
    "requests": "requests",
    "completed": "completed",
}


def ministry_model(campaign_id, query, result, *, ministry, requests):
    """The command's projection of ``ministry_page``: summaries and a count."""
    return MinistryReport(
        campaign_id=campaign_id,
        ministry=ministry,
        requests=requests,
        filters={
            key: value
            for key, value in query.form_values().items()
            if key not in {"search", "sort"}
        },
        page=query.page,
        size=query.page_size,
        matching=result["total"],
        summaries=[
            {name: row.get(key) for key, name in MINISTRY_SUMMARY.items()}
            for row in result["summaries"]
        ],
    )


def read_ministry(caller, service, filters, *, ministry, requests):
    """``report ministry``: the Ministry summary, or one list's request count.

    ``filters`` are the page's (``activity``, ``history``, ``state``,
    ``start``, ``end``, ``sort``, ``page``, ``size``; None when not given),
    parsed by the page's ``MinistryQuery``, which refuses detail filters on
    the summary (``invalid``). With ``ministry`` (a Ministry DUID, which the
    page bounds to 1 through 2**31 - 1) and ``requests`` (``join`` or
    ``leave``) it reads that list, as the page's join and leave lists do,
    and prints only its count. A page past the last shows the last page, as
    the page's ``clamp_query`` does. Records the page's
    ``ministry_report_viewed`` as the page does: started, then one per
    Ministry shown, with ``count`` the summary rows shown (0 for a list).
    """
    from .reports.ministries import MinistryQuery, campaign_ids, ministry_page
    from .reports.ministry_views import _audit, _principal
    from .reports.report_paging import clamp_query

    if (ministry is None) != (requests is None):
        raise ValueError("--ministry and --requests go together.")
    if ministry is not None and not 0 < ministry < 2**31:
        raise ValueError("A Ministry DUID is 1 through 2147483647.")
    # The query the page records: a clamped page replaces the asked one.
    shown = {
        "query": MinistryQuery.parse(
            {key: value for key, value in filters.items() if value is not None},
            detail=ministry is not None,
        )
    }

    def admit(actor, campaign_id):
        """The page's campaign check (``ministries.campaign_ids``), before
        anything is recorded: the Ministry module, a usable ParishSoft
        snapshot and, for a Ministry leader, one of their Ministries in the
        campaign; otherwise the page shows its "no campaign" page."""
        if campaign_id not in campaign_ids(actor):
            raise NotAvailable("No Ministry report is available for this campaign.")

    def read(guard, actor, campaign_id):
        """The page's read of the summary page or the chosen list."""

        def page():
            """One page of the role-scoped selection for the current query."""
            return ministry_page(
                campaign_id,
                shown["query"],
                actor,
                ministry_id=ministry,
                action=requests or "join",
            )

        result = page()
        # A page past the end shows the last page, as on the page.
        moved = clamp_query(shown["query"], result["total"], shown["query"].page_size)
        if moved is not None:
            shown["query"] = moved
            result = page()
        model = ministry_model(
            campaign_id, shown["query"], result, ministry=ministry, requests=requests
        )
        values = {
            "count": 0 if ministry is not None else len(result["summaries"]),
            "total": result["total"],
            "scope": (ministry,)
            if ministry is not None
            else tuple(row["duid"] for row in result["summaries"]),
        }
        return model, values

    def audit(actor, campaign_id, outcome, values):
        """The page's audit: one event per Ministry shown once the read ends."""
        values = values or {}
        _audit(
            actor,
            campaign_id,
            shown["query"],
            outcome,
            values.get("count", 0),
            values.get("total", 0),
            values.get("scope", ()),
        )

    return _report(
        caller,
        service,
        principal=_principal,
        read=read,
        audit=audit,
        started=True,
        admit=admit,
    )
