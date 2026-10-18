"""The Response dashboard (#1537): its Summary tiles and the daily chart.

One page (``reports/responses/``, the current campaign) shows, top to
bottom, one Summary panel of stat tiles (the current active population
statistics, then Families by stage from ``response_dashboard``), and the
daily participation chart and table with their export, each read under the
page's one campaign read guard. Production is the default; an
Administrator's Testing rehearsal view (``mode=testing``) shows the
rehearsal's Families by stage only, since the statistics, the daily chart
and the export count Production responses.
"""

from contextlib import ExitStack
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, passive_read
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.domain import Money
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.stat_tiles import tile
from parishkit.stewardship.web.tables import paginate

from .chart_specs import participation_chart
from .digest_presentation import participation_table_context
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable
from .ministries import can_report, unscoped
from .models import POPULATION_SCOPES
from .read_admission import admit_report_read
from .response_dashboard import _admit_mode, funnel_panels, read_metrics
from .response_dashboard import _audit as _audit_funnel
from .selection import guarded_participation, newest_figures
from .statistics import StatisticsUnavailable, calculate_statistics
from .statistics_selection import capture_statistics
from .workspace import (
    DAILY_SIZE,
    DAILY_SIZES,
    DAILY_SORTING,
    SCOPE_LABELS,
    RenderedReport,
    ReportQuery,
)

REPORTABLE = ("draft", "scheduled", "active", "closed", "archived")


def campaign_ids():
    """Discover only lock identities; labels and report data wait for admission."""
    return tuple(
        Campaign.objects.filter(state__in=REPORTABLE)
        .order_by("-created_at", "id")
        .values_list("id", flat=True)
    )


@require_GET
def index(request):
    """Open the current campaign's Response dashboard, or Ministry requests.

    ``/admin/reports/`` keeps its own meaning rather than opening the group's
    first entry (admin-portal spec, "Menu groups"): the Response dashboard,
    or Ministry requests for a viewer who may not open it (a Ministry
    leader), which checks its own access. Default navigation is per
    request, never a mutation of the current pointer.
    """
    try:
        service = runtime()
        actor = authenticated_admin(request, store=service.store, activity=True)
        if not allows(actor, Capability.CAMPAIGN_REPORT):
            if not can_report(actor):
                raise unscoped(actor, "Campaign reporting is unavailable.")
            response = redirect("admin:ministry_report")
            response["Cache-Control"] = "no-store"
            return response
        query = ReportQuery.parse(request.GET)
        choices = campaign_ids()
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).get()
        # Only the current campaign is reported until #145 (rule 10). With no
        # current campaign the root still opens the Response dashboard, whose
        # greyed page says why in the menu's words (#1128), rather than a
        # page of its own that no link reaches (navigation rule 1).
        if current not in choices:
            response = redirect("admin:response_dashboard")
        else:
            admit_report_read(current)
            response = redirect(query.url())
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except SAFE_FAILURES:
        return report_unavailable()
    except ValueError:
        return private_response("Invalid report filters.\n", status=400)


def _audit(principal, campaign_id, outcome, *, automatic=False):
    """Parish-owned references survive purge without copying reported values.

    ``automatic`` marks a view the open page made by itself to bring newer
    figures in (#1620); a reader's own view records no such key.
    """
    context = {"outcome": outcome}
    if automatic:
        context["automatic"] = True
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.PARTICIPATION_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            subject_id=campaign_id,
            context=context,
        )


@require_GET
def dashboard(request, campaign_id):
    """The Response dashboard of one campaign, read under the campaign guard.

    The query is parsed first, so only a malformed address is a 400; a
    fault while shaping the page is the report's 503. A Testing view is
    for Administrators only, checked before anything is read and again
    inside the guard. Every view records ``response_dashboard_viewed`` (the
    funnel read) when it ends; a Production view also records
    ``participation_viewed``, started and then succeeded or failed, since
    only it reads the statistics and the daily chart. When those cannot be
    read from their inputs, the page still shows the funnel, says the
    other panels are unavailable, and records that read as failed.
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        # The page's own re-read when newer figures are ready (#1586) is
        # passive: it never extends the sign-in, though it is audited, and
        # marked automatic (#1620). The header can only add that mark: every
        # view is audited whatever the request sends.
        automatic = passive_read(request)
        principal = _principal(request, service.store, read_only=automatic)
        try:
            query = ReportQuery.parse(request.GET)
        except ValueError:
            return private_response("Invalid report filters.\n", status=400)
        production = query.mode == "production"
        _admit_mode(principal, query)
        identities = (campaign_id,)
        admit_report_read(campaign_id)
        if production:
            _audit(principal, campaign_id, Outcome.STARTED, automatic=automatic)
        finalized = False
        response_guard = None
        # False once the statistics or daily facts could not be read.
        participation_read = True

        def finish(completed):
            """Audit only server completion after the read-only transaction closes."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            audits = [(_audit_funnel, completed)]
            if production:
                audits.append((_audit, completed and participation_read))
            for audit, succeeded in audits:
                try:
                    audit(
                        principal,
                        campaign_id,
                        Outcome.SUCCEEDED if succeeded else Outcome.FAILED,
                        automatic=automatic,
                    )
                except (DatabaseError, StorageInvariantError) as error:
                    emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Refresh roles and lifecycle inside all acquired campaign barriers."""
            nonlocal response_guard, principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Report access changed.")
            _admit_mode(fresh, query)
            principal = fresh
            for identifier in identities:
                admit_report_read(identifier)
            response_guard = guard

        def content():
            """Prepare before headers and transfer generation protection to WSGI."""
            nonlocal participation_read
            with ExitStack() as stack:
                metrics = read_metrics(campaign_id, query.mode)
                participation, cause = None, None
                if production:
                    participation, cause = _participation(
                        stack, response_guard, campaign_id, query
                    )
                    participation_read = participation is not None
                context = _page_context(
                    campaign_id, query, participation, metrics, principal, cause
                )
                payload = render_to_string(
                    "stewardship/response-dashboard.html", context, request=request
                ).encode()
                response_guard.check()
                return RenderedReport(payload, stack.pop_all())

        response = campaign_response(
            request,
            identities,
            authorize=authorize,
            open_content=content,
            content_type="text/html; charset=utf-8",
            on_close=finish,
        )
        # Admission refused before streaming (busy or closing): the Admin
        # report error page, as the other campaign reports answer.
        if response.status_code == 503 and not response.streaming:
            return report_unavailable()
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (StatisticsUnavailable, FactUnavailable, StorageInvariantError):
        return report_unavailable()
    except SAFE_FAILURES:
        return report_unavailable()
    except ValueError as error:
        # The page is built before the response starts, so a shaping fault
        # lands here; the reader's options were already validated above.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)


def _participation(stack, guard, campaign_id, query):
    """The daily chart's selection and statistics, as ``(result, cause)``.

    The selection's generation protection joins ``stack``. Inputs that
    cannot be read (``FactUnavailable``, ``StatisticsUnavailable``) leave
    the funnel shown and these panels out (#1537), rather than refusing the
    whole page; a database failure still ends it. ``result`` is then None and
    ``cause`` is "facts" (the daily chart is not ready) or "statistics" (the
    ParishSoft inputs are missing or unreadable). The swallowed error is
    debug-logged, as ``report_unavailable`` does, so a real code fault stays
    visible to a maintainer.
    """
    try:
        selected = stack.enter_context(
            guarded_participation(
                guard,
                campaign_id=campaign_id,
                population_scope=query.scope,
                browser_timezone=query.timezone,
            )
        )
        return (
            selected,
            calculate_statistics(capture_statistics(campaign_id)),
        ), None
    except FactUnavailable:
        debug_swallowed("participation facts unavailable")
        return None, "facts"
    except StatisticsUnavailable:
        debug_swallowed("participation statistics unavailable")
        return None, "statistics"


# The pages that show Participation figures, by the in-place region their
# re-read names (the one holding the figures status, #1586).
FIGURES_REGIONS = {
    "dashboard": "response-dashboard",
    "comparison": "comparison-report",
}


def figures_status(view, scope, shown, updating, *, newer=False):
    """The figures status region's context (report-figures-status.html).

    ``view`` is the page (a FIGURES_REGIONS key), ``scope`` the population
    scope its figures are for, ``shown`` the generation the page shows (None
    when it shows none) and ``updating`` whether the newest figures are
    still being prepared. ``newer`` (the status read's answer only) says a
    newer generation than ``shown`` is ready, so the page re-reads itself.
    """
    query = urlencode({"view": view, "scope": scope, "shown": shown or ""})
    return {
        "figures_status": {
            "view": view,
            "region": FIGURES_REGIONS[view],
            "updating": updating,
            "newer": newer,
            "url": reverse("admin:response_dashboard_status") + "?" + query,
        }
    }


@require_GET
def dashboard_status(request, campaign_id):
    """Passive figures status an open report polls; never audited (#1586).

    The Response dashboard and Campaign comparison audit every view, so
    instead of re-reading themselves on a timer they poll this fragment: it
    says only whether newer figures than the page's (``shown``) are ready
    and whether the newest are still being prepared, never a figure. When
    newer ones are ready, the page re-reads its own address once, passively
    (UX conventions, "Pages that update themselves"). The read never
    extends the sign-in.
    """
    try:
        service = runtime()
        _principal(request, service.store, read_only=True)
        fields = request.GET
        view = fields.get("view", "")
        scope = fields.get("scope", "")
        if (
            set(fields) - {"view", "scope", "shown"}
            or view not in FIGURES_REGIONS
            or scope not in POPULATION_SCOPES
        ):
            raise ValueError("Invalid figures status options.")
        shown = UUID(fields["shown"]) if fields.get("shown") else None
        admit_report_read(campaign_id)
        newest, updating = newest_figures(campaign_id, scope)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (FactUnavailable, StorageInvariantError):
        return report_unavailable()
    except SAFE_FAILURES:
        return report_unavailable()
    except ValueError:
        return private_response("Invalid report options.\n", status=400)
    context = figures_status(view, scope, shown, updating, newer=newest != shown)
    response = render(request, "stewardship/report-figures-status.html", context)
    response["Cache-Control"] = "no-store"
    return response


def daily_table(chart, presented, query):
    """The daily table's sorted, paged context for one participation chart.

    ``presented`` is ``participation_table_context(chart)``. Each day is paired
    with its formatted cells, so the server sorts by the exact stored values
    while the table shows the shared formatting. The whole document is
    already in memory, so the shared navigator shows the total and allows
    any page or "All".
    """
    table = paginate(
        list(zip(chart.days, presented["rows"], strict=True)),
        {"page": str(query.page), "size": query.size, "sort": query.sort},
        default=int(DAILY_SIZE),
        carry=query.carried(),
        sorting=DAILY_SORTING,
        sizes=DAILY_SIZES,
    )
    keys = ["date", "first", "participation", "pledge"]
    return {
        "table": table,
        "rows": [cells for _day, cells in table.rows],
        "columns": list(zip(keys, presented["headings"], strict=False)),
    }


def known_money(amount):
    """A ``MoneyAmount`` as ``Money`` for a stat tile, or ``None`` if unknown."""
    return Money(amount.cents) if amount.available else None


def statistics_panel(statistics):
    """The Summary panel's Active population row of stat tiles (#720, #1540).

    The active Families and Members, and with giving enabled two pledge
    tiles (#728): the current annual pledges, and last year's pledges of
    every Family in the ParishSoft data, whatever its status today (so not a
    subtotal of these Families). Without an observation every tile reads
    "—" with the reason, never zero. The responded and email-reach counts
    are not shown (#1537): the Families by stage row gives the responses,
    and ``report participation`` still reports all three.
    """
    active = statistics.active
    no_data = _("No ParishSoft data has been loaded for this campaign yet.")
    figures = [
        ("active_families", _("Active Families"), active and active.families, no_data),
        (
            "active_members",
            _("Active Members"),
            active and active.active_members,
            no_data,
        ),
    ]
    if statistics.financial_enabled:
        pledges = known_money(active.annual_pledge) if active else None
        everyone = known_money(statistics.comparison_pledge_all)
        # One reason per cause, so each says what is actually missing.
        if active is None:
            annual_reason = everyone_reason = no_data
        elif statistics.financial is None:
            annual_reason = everyone_reason = _(
                "Giving is turned on, but this campaign has no giving "
                "definition yet, so pledges cannot be totalled."
            )
        else:
            annual_reason = _(
                "The total is added up only when every responding Family's "
                "pledge amount is known."
            )
            everyone_reason = _(
                "The ParishSoft data kept for this campaign does not hold "
                "every pledge for last year's period, so the total is not "
                "added up."
            )
        figures += [
            ("annual_pledges", _("Current annual pledges"), pledges, annual_reason),
            (
                "comparison_pledges_all",
                _("Last year's pledges (all Families)"),
                everyone,
                everyone_reason,
            ),
        ]
    return {
        "tiles": tuple(
            tile(key, label, value, unknown_reason="" if value is not None else reason)
            for key, label, value, reason in figures
        )
    }


def scope_links(query):
    """Each Chart population segment's address (#1535): this page with that
    scope, the daily table back on its first page, its sort and size and
    the zone kept."""
    return {scope: query.url(page=1, scope=scope) for scope in SCOPE_LABELS}


def mode_links(query):
    """Each Production / Testing rehearsal segment's address (#1537).

    This page in that mode, the daily table back on its first page, its
    population, sort, size and the zone kept.
    """
    return {mode: query.url(page=1, mode=mode) for mode in ("production", "testing")}


def _page_context(campaign_id, query, participation, metrics, principal, cause=None):
    """Format detached shared observations; never recompute money in templates.

    ``metrics`` is the funnel of the mode shown (None: a Testing view with
    no rehearsal). ``participation`` is the daily chart's selection and the
    statistics, read in Production only: None in a Testing view, which
    shows no statistics, daily chart or export, or when they could not be
    read, which the page says; ``cause`` ("facts" or "statistics") picks its
    wording.
    """
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    context = {
        "campaign": campaign,
        "query": query,
        "testing": query.mode == "testing",
        # Testing responses are shown to Administrators only (#477).
        "can_test": "administrator" in principal.roles,
        "mode_links": mode_links(query),
        "metrics": None,
        "participation_unavailable": query.mode == "production"
        and participation is None,
        "participation_cause": cause,
    }
    if metrics is not None:
        context.update(funnel_panels(metrics))
    if participation is None:
        return context
    selected, statistics = participation
    mutable = True
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        mutable = False
    context |= {
        "scope_links": scope_links(query),
        "selection": selected,
        "statistics": statistics,
        **statistics_panel(statistics),
        "export_key": uuid4(),
        # No list of earlier exports: exports are point-in-time (#1398).
        "export_allowed": mutable,
        # The chart's Updating status, which follows the newest figures
        # (#1586).
        **figures_status(
            "dashboard",
            query.scope,
            selected.document.fact_set_id if selected.document is not None else None,
            selected.updating,
        ),
    }
    if selected.document is not None:
        context.update(participation_table_context(selected.document))
        context.update(daily_table(selected.document, context, query))
        context["row_count"] = len(selected.document.days)
        # The daily chart drawn in the browser (#720); the exports and the
        # daily email keep the static drawing.
        context["daily_chart"] = participation_chart(selected.document)
    return context
