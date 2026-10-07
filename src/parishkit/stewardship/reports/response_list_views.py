"""Admin/Staff pages and CSV downloads of the response lists (#477, PR 5).

``reports/<campaign>/responses/<list>/`` shows one list of Families behind
the response funnel (``response_lists``) at the database's current instant,
as a shared Admin table (web/tables.py) that sorts, filters and pages in
place. ``.../csv/`` downloads the complete filtered list, in the page's order,
rendered on request on the web connection (see ``_respond``). Admission, the
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
from django.views.decorators.http import require_POST, require_safe

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
    LISTS,
    ListQuery,
    cells,
    current_snapshot,
    downloads_paused,
    list_csv,
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
    names the list.
    """
    context = {"report_mode": query.mode, "report_filter": query.show}
    if snapshot is not None:
        context["snapshot_id"] = snapshot
    return context


def _audit(action, principal, campaign_id, outcome, count, choices):
    """Record the view or download: who, which campaign, how it ended, how many.

    ``choices`` (``audit_choices``) adds the mode, filter and snapshot; no
    name or DUID is copied. An append takes no row locks, so it runs after
    the read guard closes.
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


def page_context(campaign, spec, query, table, as_of, **options):
    """The list page's template context; a pure function of its inputs.

    ``table`` is the shared TablePage of ``ListedFamily`` rows (empty for a
    Testing view with no rehearsal, which ``no_rehearsal`` marks). The other
    ``options`` are ``can_test``, ``can_export`` and ``paused`` (downloads
    refused by the purge gate).
    """
    key = spec.key
    # Switching mode keeps the filter and a chosen order, from page 1; the
    # fragment lands a full load on the table and marks the link in place.
    sort = table.sort if table.sort != spec.default_sort else None
    size = table.size_value if table.size != 50 else None
    no_rehearsal = options.get("no_rehearsal", False)
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
        "dashboard_url": DashboardQuery(query.mode).url(campaign.pk),
        "production_url": ListQuery("production", query.show).url(
            campaign.pk, key, size=size, sort=sort
        ),
        "testing_url": ListQuery("testing", query.show).url(
            campaign.pk, key, size=size, sort=sort
        ),
        "filter_action": reverse("admin:response_list", args=[campaign.pk, key]),
        "export_action": reverse("admin:response_list_export", args=[campaign.pk, key]),
        "export_timezones": sorted(timezone_names()),
        "show_choices": spec.choices if len(spec.choices) > 1 else (),
        # The download keeps the page's filter and order; refreshed in place,
        # the export form's hidden fields follow them (data-table-sync).
        "export_fields": query.carried() + table.sort_fields,
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

    A page is a GET whose choices travel in the query string; a download
    is a CSRF-protected POST carrying the same choices plus a time zone.

    The download is not a stored export file: it is built in memory on the
    web connection, as the System logs download is, and read under the
    interactive campaign read guard, like the page (data spec, "Campaign read
    guards"). It never uses the dedicated download pool, whose login
    (``DOWNLOAD_READ_TABLES``) reads no campaign data. Its response carries
    the shared download headers.
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
                query, values = ListQuery.parse(spec, parameters, extra={"timezone"})
                zone = values.pop("timezone", "UTC")
                if values.keys() - {"sort"} or (
                    zone != "UTC" and zone not in timezone_names()
                ):
                    raise ValueError("Invalid response list download.")
                values["size"] = "all"
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
                    audit_choices(query, snapshot),
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
                rows = read_list(
                    spec, scope, as_of, spec.choice(query.show), snapshot=snapshot
                )
            count = len(rows)
            table = paginate(rows, values, carry=query.carried(), sorting=spec.sorting)
            if export:
                return iter((list_csv(spec, table.rows, ZoneInfo(zone)),))
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
            **({"content_type": "text/csv"} if export else {}),
        )
        # Admission refused before streaming (busy or closing): the Admin
        # report error page, as the other campaign reports answer.
        if response.status_code == 503 and not response.streaming:
            return report_unavailable()
        handed_off = response.status_code == 200 and response.streaming
        if export and handed_off:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
            for name, value in download_headers(
                f"stewardship-responses-{key}-{stamp}.csv", content_type="text/csv"
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


@require_safe
def response_list(request, campaign_id, key):
    """One list of Families behind the response funnel, as a paged table."""
    return _respond(request, campaign_id, key, export=False)


@require_POST
def response_list_export(request, campaign_id, key):
    """The complete filtered list as CSV, in the order the page shows it."""
    return _respond(request, campaign_id, key, export=True)
