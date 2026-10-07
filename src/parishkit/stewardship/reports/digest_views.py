"""Pinned daily reports: current Staff/Admin authority through response closure."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import paginate, table_parameters

from .digest_building import retained_daily_document
from .digest_models import DailyDigestReady, DailyDigestSnapshot
from .digest_presentation import chart_layout, snapshot_context
from .export_services import admit_campaign
from .export_views import _principal
from .facts import FactUnavailable
from .statistics import StatisticsUnavailable
from .workspace import DAILY_SORTING


def daily_rows(context, parameters):
    """Sort and page the pinned report's daily table like the live report's.

    ``context`` is ``snapshot_context``; ``parameters`` the validated page,
    size and sort. Rows pair each retained day with its formatted cells, so
    the server sorts by exact values (see ``workspace.DAILY_SORTING``).
    """
    chart = context["chart"]
    table = paginate(
        list(zip(chart.days, context["rows"], strict=True)),
        parameters,
        sorting=DAILY_SORTING,
    )
    keys = ["date", "first", "participation", "pledge"]
    return {
        "table": table,
        "columns": list(zip(keys, context["headings"], strict=False)),
    }


def record_view(principal, snapshot_id, campaign_id, outcome):
    """Retain the opaque snapshot reference, not figures or recipient names.

    The page and the command line's ``digest daily`` record the same event.
    """
    # An audit append takes no row locks, so it need not join the writers'
    # work order; waiting there stalled report pages behind every source
    # promotion and installer.
    with transaction.atomic():
        # Audit attribution follows the current applied projection; report
        # calculation still uses its immutable historical one.
        current = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.DAILY_DIGEST_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=snapshot_id,
            parish_id=current.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome},
        )


def check_retained(snapshot_id, campaign_id):
    """Inside the read barrier: the snapshot is still retained and ready."""
    admit_campaign(campaign_id, mutating=False)
    if not DailyDigestSnapshot.objects.filter(
        pk=snapshot_id, campaign_id=campaign_id
    ).exists():
        raise ReadUnavailable("Retained report is unavailable.")
    if not DailyDigestReady.objects.filter(snapshot_id=snapshot_id).exists():
        raise ReadUnavailable("Retained report is not ready.")


def retained_rows(snapshot_id, *, chart=False):
    """The pinned snapshot and its ready row, read under the read barrier.

    The page plots the ready row's chart bytes, so it asks for them with
    ``chart``; the command line's ``digest daily`` does not load them.
    """
    selected = DailyDigestSnapshot.objects.select_related(
        "configuration__parish", "timezone_configuration", "preparation"
    ).get(pk=snapshot_id)
    fields = ("id", "snapshot_id", "fact_set_id") + (("chart",) if chart else ())
    ready = DailyDigestReady.objects.only(*fields).get(snapshot=selected)
    return selected, ready


def retained_document(snapshot_id):
    """The pinned report's document and mode, read under the read barrier.

    Never consults current facts: the document is the retained observation
    the email was compiled from.
    """
    selected, ready = retained_rows(snapshot_id)
    return retained_daily_document(selected, ready.fact_set), selected.preparation.mode


@require_GET
def snapshot(request, snapshot_id, *, representation="html"):
    """A UUID selects retained data, not permission; chart bytes are equally private."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        # Only the HTML page's daily table takes (closed) paging and sort
        # parameters; chart images take none.
        try:
            paging = filters(
                request.GET,
                allowed=table_parameters() if representation == "html" else set(),
            )
            if representation not in {"html", "png", "download"}:
                raise ValueError("Unknown report representation.")
            if paging:
                paginate([], paging, sorting=DAILY_SORTING)
        except ValueError:
            return private_response("Invalid report request.\n", status=400)
        # Only scope metadata is read before the response-lifetime barrier.
        retained = DailyDigestSnapshot.objects.only(
            "id", "campaign_id", "configuration_id"
        ).get(pk=snapshot_id)
        admit_campaign(retained.campaign_id, mutating=False)
        finalized = False

        def audit(outcome):
            """The page's view event for this snapshot."""
            record_view(principal, snapshot_id, retained.campaign_id, outcome)

        def finish(completed):
            """Record server completion after closure, never imply browser receipt."""
            nonlocal finalized
            if not finalized:
                try:
                    audit(Outcome.SUCCEEDED if completed else Outcome.FAILED)
                except (DatabaseError, StorageInvariantError) as error:
                    # Headers may already be sent. Preserve the original result
                    # and signal the missing terminal audit without exception
                    # text, report contents, or claiming the write succeeded.
                    emit_failure(error, event=Event.REPORT_AUDIT_FAILED)
                else:
                    finalized = True

        try:
            audit(Outcome.STARTED)
        except BaseException:
            # Failed intent admission has no started read to finish. Avoid a
            # second audit failure masking the original, sanitized response.
            finish = None
            raise

        def authorize(guard):
            """Reload session/roles after acquiring the purge/read barrier."""
            _principal(request, service.store, read_only=True)
            check_retained(snapshot_id, retained.campaign_id)

        def content():
            """Prepare bounded bytes before headers; never consult current facts."""
            if representation != "html":
                chart = DailyDigestReady.objects.values_list("chart", flat=True).get(
                    snapshot_id=snapshot_id
                )
                return iter((bytes(chart),))
            selected, ready = retained_rows(snapshot_id, chart=True)
            document = retained_daily_document(selected, ready.fact_set)
            mode = selected.preparation.mode
            context = snapshot_context(
                document,
                mode=mode,
                chart_url=reverse("admin:daily_digest_chart", args=[snapshot_id]),
                download_url=reverse("admin:daily_digest_download", args=[snapshot_id]),
                plot=chart_layout(bytes(ready.chart)),
            )
            context |= daily_rows(context, paging)
            return iter(
                (
                    render_to_string(
                        "stewardship/daily-digest.html", context, request=request
                    ).encode(),
                )
            )

        response = campaign_response(
            request,
            [retained.campaign_id],
            authorize=authorize,
            open_content=content,
            content_type="text/html; charset=utf-8"
            if representation == "html"
            else "image/png",
            filename=f"daily-digest-{snapshot_id}.png"
            if representation == "download"
            else None,
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        ReadUnavailable,
        FactUnavailable,
        StatisticsUnavailable,
        StorageInvariantError,
    ):
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)
