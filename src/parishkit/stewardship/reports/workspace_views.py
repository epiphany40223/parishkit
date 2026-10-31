"""The Response dashboard (#1537): its Summary tiles and the daily chart.

One page (``reports/responses/``, the current campaign) shows, top to
bottom, its Families counted choice (#2324), one Summary panel of stat tiles
(the Population statistics, then Families by stage from
``response_dashboard``), and the daily participation chart and table with
their export, each read under the page's one campaign read guard. The
choice's population (``scope``) sets every one of those figures. Whose
responses are shown follows the site's mode (``shown_mode``): in Testing
mode, with an active rehearsal, an Administrator sees the rehearsal's
Families by stage only, since the statistics, the daily chart and the export
count Production responses; every other reader sees Production (#477).
"""

from contextlib import ExitStack
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
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
from .directories import admit_mode
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES, _principal
from .facts import FactUnavailable
from .ministries import can_report, unscoped
from .models import POPULATION_SCOPES
from .money import MoneyAmount
from .participation import SCOPE_CHOICE_LABEL
from .participation_summary import population_figures
from .read_admission import admit_report_read
from .response_dashboard import _audit as _audit_funnel
from .response_dashboard import (
    funnel_panels,
    read_metrics,
    scoped_metrics,
    shown_mode,
)
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

    The query is parsed first, so only a malformed address is a 400 (an
    old link's ``mode`` included, #2324); a fault while shaping the page is
    the report's 503. Whose responses are shown is the site's mode
    (``shown_mode``), read once before the guard; a Testing view is for
    Administrators only, checked before anything is read and again inside
    the guard (#477). Every view records
    ``response_dashboard_viewed`` (the funnel read) when it ends; a
    Production view also records
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
        mode = shown_mode(campaign_id, principal)
        production = mode == "production"
        # Testing responses are shown to Administrators only (#477): checked
        # before anything is read and again inside the guard.
        admit_mode(principal, mode)
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
            admit_mode(fresh, mode)
            principal = fresh
            for identifier in identities:
                admit_report_read(identifier)
            response_guard = guard

        def content():
            """Prepare before headers and transfer generation protection to WSGI."""
            nonlocal participation_read
            with ExitStack() as stack:
                metrics = read_metrics(campaign_id, mode)
                participation, cause, shown = None, None, None
                if production:
                    participation, cause, shown = _participation(
                        stack, response_guard, campaign_id, query
                    )
                    participation_read = participation is not None
                # The stage tiles count the Families counted population, as
                # every other figure does (#2324). When only the daily facts
                # could not be read, the statistics still name it, so a
                # scope in the address still counts; when they cannot be
                # read either, the tiles count every Family and the page
                # says so (``counted``), never silently.
                duids = None
                if participation is not None:
                    duids = participation[1].duids.get(query.scope)
                elif cause == "facts":
                    duids = _population(campaign_id, query.scope)
                if duids is not None:
                    metrics = scoped_metrics(metrics, duids)
                context = _page_context(
                    campaign_id, query, mode, participation, metrics, cause, shown
                )
                if participation is None and production:
                    context["counted"] = duids is not None
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
    """The daily chart's selection and statistics, as ``(result, cause, shown)``.

    The selection's generation protection joins ``stack``. Inputs that
    cannot be read (``FactUnavailable``, ``StatisticsUnavailable``) leave
    the funnel shown and these panels out (#1537), rather than refusing the
    whole page; a database failure still ends it. ``result`` is then None and
    ``cause`` is "facts" (the daily chart is not ready) or "statistics" (the
    ParishSoft inputs are missing or unreadable). ``shown`` is the chart
    generation the selection chose, or when it chose none or the facts could
    not be read the newest one now (None when there is none): an
    unavailable page's figures status re-reads the page once another
    generation is ready (#1586). The
    swallowed error is debug-logged, as ``report_unavailable`` does, so a
    real code fault stays visible to a maintainer.
    """
    shown = None
    try:
        selected = stack.enter_context(
            guarded_participation(
                guard,
                campaign_id=campaign_id,
                population_scope=query.scope,
                browser_timezone=query.timezone,
            )
        )
        if selected.document is not None:
            shown = selected.document.fact_set_id
        return (
            (selected, calculate_statistics(capture_statistics(campaign_id))),
            None,
            shown,
        )
    except FactUnavailable:
        debug_swallowed("participation facts unavailable")
        # The selection already passes over a busy generation, so facts
        # that fail here seldom clear by themselves: compare with the
        # newest generation now, so the page re-reads only once another is
        # ready, never once a minute while nothing changed (#1586).
        return None, "facts", _newest(campaign_id, query.scope)
    except StatisticsUnavailable:
        debug_swallowed("participation statistics unavailable")
        if shown is None:
            # No generation could be read: compare with the newest, for
            # the same reason.
            shown = _newest(campaign_id, query.scope)
        return None, "statistics", shown


def _population(campaign_id, scope):
    """The DUIDs of ``scope``'s Families from the live statistics, or None.

    Read when the daily facts could not be (#2324): the statistics need no
    facts, so the Families by stage tiles can still count the population
    the address names. None when the statistics cannot be read either.
    """
    try:
        return calculate_statistics(capture_statistics(campaign_id)).duids.get(scope)
    except StatisticsUnavailable:
        debug_swallowed("population statistics unavailable")
        return None


def _newest(campaign_id, scope):
    """The newest generation now, or None when there is none to name."""
    try:
        return newest_figures(campaign_id, scope)[0]
    except FactUnavailable:
        return None


# The pages that show Participation figures, by the in-place region their
# re-read names (the one holding the figures status, #1586).
FIGURES_REGIONS = {
    "dashboard": "response-dashboard",
    "comparison": "comparison-report",
}


def figures_status(
    view, scope, shown, updating, *, newer=False, follow=True, retry=False
):
    """The figures status region's context (report-figures-status.html).

    ``view`` is the page (a FIGURES_REGIONS key), ``scope`` the population
    scope its figures are for, ``shown`` the generation the page shows (None
    when it shows none) and ``updating`` whether the newest figures are
    still being prepared. ``newer`` (the status read's answer only) says a
    newer generation than ``shown`` is ready, so the page re-reads itself.
    ``follow`` False draws the notice but never watches it: a page that is
    itself a refusal (a Campaign comparison day out of range) would only be
    refused again by its own re-read. ``retry`` marks a page that could not
    read its figures at all (the Response dashboard's unavailable line): the
    region is watched at a fixed, slow pace (60 s, as each re-read it leads
    to is an audited view) and shows no Updating notice, since the
    unavailable line already says what is missing; its address carries
    ``retry=1`` so each status answer keeps both (#1586).
    """
    fields = {"view": view, "scope": scope, "shown": shown or ""}
    if retry:
        fields["retry"] = "1"
    return {
        "figures_status": {
            "view": view,
            "region": FIGURES_REGIONS[view],
            "updating": updating and not retry,
            "retry": retry,
            "newer": newer,
            "follow": follow,
            "url": reverse("admin:response_dashboard_status") + "?" + urlencode(fields),
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
    (UX conventions, "Pages that update themselves"). ``retry=1`` (its only
    value) comes from a page that could not read its figures at all, and is
    answered at that page's slow pace (``figures_status``). The read never
    extends the sign-in.
    """
    try:
        service = runtime()
        _principal(request, service.store, read_only=True)
        fields = request.GET
        view = fields.get("view", "")
        scope = fields.get("scope", "")
        if (
            set(fields) - {"view", "scope", "shown", "retry"}
            or view not in FIGURES_REGIONS
            or scope not in POPULATION_SCOPES
            or fields.get("retry", "1") != "1"
            # Only the dashboard draws an unavailable line.
            or ("retry" in fields and view != "dashboard")
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
    context = figures_status(
        view, scope, shown, updating, newer=newest != shown, retry="retry" in fields
    )
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


def statistics_panel(statistics, scope):
    """The Summary panel's Population row of stat tiles (#720, #1540).

    The figures are ``participation_summary.population_figures`` for the
    page's Families counted ``scope`` (#2324), which the export's capture
    also reads (#1557): its Families and Members, and with giving enabled
    the two pledge totals (#728), so not a subtotal of these Families.
    Without an observation every tile reads "—" with the reason, never zero.
    The responded and email-reach counts are not shown (#1537): the
    Families by stage row gives the responses, and ``report participation``
    still reports all three.
    """
    return {
        "tiles": tuple(
            tile(
                key,
                label,
                # A tile formats Money; the statistics hold exact cents.
                Money(value.cents) if isinstance(value, MoneyAmount) else value,
                details=(note,) if note and value is not None else (),
                unknown_reason="" if value is not None else reason,
            )
            for key, label, value, reason, note in population_figures(statistics, scope)
        )
    }


def scope_links(query):
    """Each Families counted segment's address (#1535, #2324): this page with
    that scope, the daily table back on its first page, its sort and size
    and the zone kept."""
    return {scope: query.url(page=1, scope=scope) for scope in SCOPE_LABELS}


def _page_context(
    campaign_id, query, mode, participation, metrics, cause=None, shown=None
):
    """Format detached shared observations; never recompute money in templates.

    ``mode`` is the responses shown (``shown_mode``). ``metrics`` is that
    mode's funnel, already counted over the page's population when the
    statistics were read (None: the rehearsal ended while the page was
    read). ``participation`` is the daily chart's selection and the
    statistics, read in Production only: None in Testing, which shows no
    statistics, daily chart, export or Families counted choice, or when
    they could not be read, which the page says; ``cause`` ("facts" or
    "statistics") picks its wording, and ``shown`` (the generation that
    read chose, if any) is what the unavailable line's figures status
    compares newer figures with, so the page brings them in by itself once
    they can be read (#1586).
    """
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    context = {
        "campaign": campaign,
        "query": query,
        "testing": mode == "testing",
        "metrics": None,
        "participation_unavailable": mode == "production" and participation is None,
        "participation_cause": cause,
        # The Families counted choice's words, kept in one place (#2324).
        "scope_choice_label": SCOPE_CHOICE_LABEL,
        "scope_labels": SCOPE_LABELS,
        "scope_label": SCOPE_LABELS[query.scope],
    }
    if metrics is not None:
        context.update(funnel_panels(metrics))
    if participation is None:
        if context["participation_unavailable"]:
            # The figures status stays beside the unavailable line, at its
            # slow retry pace, so the page never asks for a reload (#1586).
            context |= figures_status(
                "dashboard", query.scope, shown, False, retry=True
            )
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
        **statistics_panel(statistics, query.scope),
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
