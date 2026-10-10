"""Admin/Staff pages and downloads of the response lists (#477, PR 5).

``reports/responses/<list>/`` shows one list of Families behind
the response funnel (``response_lists``) at the database's current instant,
as a shared Admin table (web/tables.py) that sorts, filters and pages in
place. A name, DUID or envelope-number search (#849) is posted to the same
page in a CSRF-protected body, never put in a URL; while one is applied the
table is a POST table, whose controls carry it as hidden fields. ``.../csv/``
downloads the complete filtered list, in the page's order, as CSV, XLSX or
PDF (#850; the route predates the other two formats), rendered on request on
the web connection (see ``_respond``). Admission, the
campaign read guard, the role recheck inside it and the audit follow the
response dashboard (``response_dashboard``); a download also needs
``REPORT_EXPORT`` and is refused while the campaign's purge gate is closed.
Testing responses are shown to Administrators only, as on the dashboard.
"""

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.exports import download_headers
from parishkit.stewardship.web.refusals import Refusal
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import paginate

from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read
from .response_dashboard import DashboardQuery, rehearsal_epoch
from .response_lists import (
    FORMATS,
    LISTS,
    ListQuery,
    cells,
    current_snapshot,
    downloads_paused,
    list_details,
    list_file,
    read_list,
)
from .response_metrics import ResponseScope

DOWNLOADS_PAUSED = Refusal(
    _("Downloads of this campaign's lists are paused while it is prepared for purge."),
    fix=_(
        "The lists can still be read on screen. Files downloaded earlier are "
        "not affected."
    ),
)


def _principal(request, store, *, read_only=False, export=False):
    """A current Admin or Staff user, reloaded from policy on every call."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT) or (
        export and not allows(principal, Capability.REPORT_EXPORT)
    ):
        raise PermissionError("The response lists are unavailable.")
    return principal


def _admit_mode(principal, query):
    """Testing responses are shown to Administrators only."""
    if query.mode == "testing" and "administrator" not in principal.roles:
        raise PermissionError("Testing responses are shown to Administrators only.")


def audit_choices(query, snapshot):
    """The audit context naming what was read (#556).

    The system mode and the ``show`` choice are closed words, and the
    snapshot is the ParishSoft data the names came from (left out when
    nothing was read, as for Testing with no rehearsal). The event type
    names the list. The search text can name a Family, so only whether one
    was applied is kept (``search_used``, #849), never the text.
    """
    context = {
        "report_mode": query.mode,
        "report_filter": query.show,
        "search_used": bool(query.search),
    }
    if snapshot is not None:
        context["snapshot_id"] = snapshot
    return context


def _audit(action, principal, campaign_id, outcome, count, choices):
    """Record the view or download: who, which campaign, how it ended, how many.

    ``choices`` (``audit_choices`` and ``sort_choice``) adds the mode,
    filter, order and snapshot; no name or DUID is copied. An append takes
    no row locks, so it runs after the read guard closes.
    """
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            action,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome, "count": count} | choices,
        )


def _scope(campaign_id, query):
    """The funnel scope to read, or None for Testing with no active rehearsal."""
    if query.mode == "production":
        return ResponseScope(campaign_id)
    epoch = rehearsal_epoch(campaign_id)
    return None if epoch is None else ResponseScope(campaign_id, "testing", epoch)


def sort_choice(spec, values):
    """The audit context naming the list's order (#851): its closed sort token.

    ``values`` are the table choices already checked by ``paginate``, so the
    token is one of the list's own (``audit.schemas.REPORT_SORTS``); without
    one it is the list's default. The page size and page number are not
    recorded: they choose which slice of the same Families is on screen,
    and the row count with the mode, filter and order already says which
    Families the list held.
    """
    return {"report_sort": spec.sorting.parse(values)}


def page_context(campaign, spec, query, table, as_of, **options):
    """The list page's template context; a pure function of its inputs.

    ``table`` is the shared TablePage of ``ListedFamily`` rows (empty for a
    Testing view with no rehearsal, which ``no_rehearsal`` marks). The other
    ``options`` are ``can_test``, ``can_export`` and ``paused`` (downloads
    refused by the purge gate).

    With a search applied the table becomes a POST table posting back to
    the list, so its headings and navigators carry the search privately.
    """
    key = spec.key
    # Switching mode keeps the filter and a chosen order, from page 1; the
    # fragment lands a full load on the table and marks the link in place.
    # It is a link, so it leaves a search behind.
    sort = table.sort if table.sort != spec.default_sort else None
    size = table.size_value if table.size != 50 else None
    page = str(table.number) if table.number > 1 else None
    no_rehearsal = options.get("no_rehearsal", False)
    list_url = reverse("admin:response_list", args=[key])
    if query.search:
        table = replace(table, method="post", action=list_url)
    return {
        "campaign": campaign,
        "spec": spec,
        "query": query,
        "testing": query.mode == "testing",
        "no_rehearsal": no_rehearsal,
        "can_test": options.get("can_test", False),
        "can_export": options.get("can_export", False),
        "paused": options.get("paused", False),
        "as_of": as_of,
        "table": replace(table, rows=[(row, cells(spec, row)) for row in table.rows]),
        "breadcrumb_label": spec.title,
        "dashboard_url": DashboardQuery(query.mode).url(),
        "production_url": ListQuery("production", query.show).url(
            key, size=size, sort=sort
        ),
        "testing_url": ListQuery("testing", query.show).url(key, size=size, sort=sort),
        # The address bar after an in-place answer (data-page-address, #536):
        # only the closed choices, so Back and Reload keep them; never the
        # search, which a reload therefore clears.
        "page_address": ListQuery(query.mode, query.show).url(
            key, sort=sort, size=size, page=page
        ),
        "filter_action": list_url,
        "export_action": reverse("admin:response_list_export", args=[key]),
        "export_timezones": sorted(timezone_names()),
        # CSV first, the default; the labels are the formats' own names.
        "export_formats": tuple((name, name.upper()) for name in FORMATS),
        "can_download": not (
            options.get("paused", False) or no_rehearsal or not table.count
        ),
        "show_choices": spec.choices if len(spec.choices) > 1 else (),
        # The download keeps the page's filter, search and order; refreshed in
        # place, the export form's hidden fields follow them (data-table-sync).
        "export_fields": query.posted() + table.sort_fields,
    }


class DownloadsPaused(Exception):
    """The campaign's purge gate closed: no new download may start."""


def _paused_response():
    """The 409 a refused download answers, with its plain-language reason."""
    return validation_response(
        [FieldError(ErrorCode.UNAVAILABLE)], status=409, refusal=DOWNLOADS_PAUSED
    )


def _respond(request, campaign_id, key, *, export):
    """Shared admission, purge protection, audit and failure handling.

    A page is a GET whose closed choices travel in the query string, or a
    CSRF-protected POST carrying them and a search, which never enters a
    URL; a POST with anything in its query string is refused. A download is
    a CSRF-protected POST carrying the same choices plus a time zone and a
    format (CSV, XLSX or PDF; CSV when none is sent).

    The download is not a stored export file: it is built in memory on the
    web connection, as the System logs download is, and read under the
    interactive campaign read guard, like the page (data spec, "Campaign read
    guards"). It never uses the dedicated download pool, whose login
    (``DOWNLOAD_READ_TABLES``) reads no campaign data. Its response carries
    the shared download headers. The largest list (about 1,100 Families,
    some 34 pages) renders as PDF in about 8 seconds on a development
    machine, within the read guard's 60-second deadline (#850 measured it
    before choosing this over the stored export lifecycle).
    """
    spec = LISTS.get(key)
    if spec is None:
        raise Http404("No such response list.")
    finish, handed_off = None, False
    action = spec.exported if export else spec.viewed
    try:
        service = runtime()
        principal = _principal(request, service.store, export=export)
        try:
            if export:
                if request.GET:
                    raise ValueError("A download's choices travel in its form.")
                parameters = request.POST.copy()
                parameters.pop("csrfmiddlewaretoken", None)
                query, values = ListQuery.parse(
                    spec, parameters, extra={"timezone", "format"}, private=True
                )
                zone = values.pop("timezone", "UTC")
                file_format = values.pop("format", "csv")
                if (
                    values.keys() - {"sort"}
                    or (zone != "UTC" and zone not in timezone_names())
                    or file_format not in FORMATS
                ):
                    raise ValueError("Invalid response list download.")
                values["size"] = "all"
            elif request.method == "POST":
                if request.GET:
                    raise ValueError("A search travels only in its form.")
                parameters = request.POST.copy()
                parameters.pop("csrfmiddlewaretoken", None)
                query, values = ListQuery.parse(spec, parameters, private=True)
            else:
                query, values = ListQuery.parse(spec, request.GET)
            # Refuse a bad sort, size or page now, as a 400.
            paginate([], values, sorting=spec.sorting)
        except ValueError:
            return private_response("Invalid report filters.\n", status=400)
        _admit_mode(principal, query)
        admit_report_read(campaign_id)
        finalized, count, snapshot = False, 0, None

        def finish(completed):
            """Audit completion once, after the read-only transaction closes.

            A download the purge gate refused is audited too, as failed.
            """
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    action,
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                    audit_choices(query, snapshot) | sort_choice(spec, values),
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        if export and downloads_paused(campaign_id):
            return _paused_response()

        def authorize(guard):
            """A role change, or a purge gate closing on a download, ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True, export=export)
            if fresh.identity != principal.identity:
                raise PermissionError("Response list access changed.")
            _admit_mode(fresh, query)
            principal = fresh
            admit_report_read(campaign_id)
            if export and downloads_paused(campaign_id):
                raise DownloadsPaused

        def _download(campaign, spec, query, table, as_of):
            """The download's bytes, in the parish's date format.

            Rendered eagerly in the view, when ``campaign_response`` opens
            the content under the read guard. The parish's date format is
            lent explicitly so the file never depends on the request's date
            format middleware still being active around that call.
            """
            parish = (
                SystemConfiguration.objects.select_related(
                    "active_configuration__parish"
                )
                .get()
                .active_configuration.parish
            )
            details = list_details(
                spec,
                query,
                parish=parish.name,
                campaign=campaign.active_configuration.name,
                as_of=as_of,
                zone=ZoneInfo(zone),
                count=table.count,
            )
            with dates.using(parish.date_format):
                return list_file(
                    spec,
                    table.rows,
                    ZoneInfo(zone),
                    file_format,
                    details=details,
                    as_of=as_of,
                )

        def content():
            """Read the list once under the guard and render the page or file."""
            nonlocal count, snapshot
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            as_of = database_now()
            scope = _scope(campaign_id, query)
            rows = []
            if scope is not None:
                # Read once and pass in, so the audit names the snapshot
                # the names actually came from.
                snapshot = current_snapshot()
                rows = read_list(spec, scope, as_of, query, snapshot=snapshot)
            count = len(rows)
            # A POST table's controls carry the search as hidden fields.
            table = paginate(rows, values, carry=query.posted(), sorting=spec.sorting)
            if export:
                return iter((_download(campaign, spec, query, table, as_of),))
            context = page_context(
                campaign,
                spec,
                query,
                table,
                as_of,
                no_rehearsal=scope is None,
                can_test="administrator" in principal.roles,
                can_export=allows(principal, Capability.REPORT_EXPORT),
                paused=downloads_paused(campaign_id),
            )
            page = render_to_string(
                "stewardship/response-list.html", context, request=request
            )
            return iter((page.encode(),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
            **({"content_type": FORMATS[file_format]} if export else {}),
        )
        # Admission refused before streaming (busy or closing): the Admin
        # report error page, as the other campaign reports answer.
        if response.status_code == 503 and not response.streaming:
            return report_unavailable()
        handed_off = response.status_code == 200 and response.streaming
        if export and handed_off:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
            for name, value in download_headers(
                f"stewardship-responses-{key}-{stamp}.{file_format}",
                content_type=FORMATS[file_format],
            ).items():
                response[name] = value
        return response
    except DownloadsPaused:
        return _paused_response()
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except ValueError as error:
        # The choices were validated above, so this is a shaping fault.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_http_methods(["GET", "HEAD", "POST"])
def response_list(request, campaign_id, key):
    """One list of Families behind the response funnel, as a paged table.

    A POST is a read: the filter form's search (and its table's controls
    while a search is applied), never a change.
    """
    return _respond(request, campaign_id, key, export=False)


@require_POST
def response_list_export(request, campaign_id, key):
    """The complete filtered list as CSV, XLSX or PDF, in the page's order."""
    return _respond(request, campaign_id, key, export=True)
