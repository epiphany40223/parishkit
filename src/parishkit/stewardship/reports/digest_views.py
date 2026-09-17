"""Pinned daily reports: current Staff/Admin authority through response closure."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
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
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .digest_building import retained_daily_document
from .digest_models import DailyDigestReady, DailyDigestSnapshot
from .digest_presentation import snapshot_context
from .export_services import admit_campaign
from .export_views import _principal
from .facts import FactUnavailable
from .statistics import StatisticsUnavailable


@require_GET
def snapshot(request, snapshot_id, *, representation="html"):
    """A UUID selects retained data, not permission; chart bytes are equally private."""
    finish, handed_off = None, False
    try:
        if request.GET or representation not in {"html", "png", "download"}:
            return private_response("Invalid report request.\n", status=400)
        service = runtime()
        principal = _principal(request, service.store)
        # Only scope metadata is read before the response-lifetime barrier.
        retained = DailyDigestSnapshot.objects.only(
            "id", "campaign_id", "configuration_id"
        ).get(pk=snapshot_id)
        admit_campaign(retained.campaign_id, mutating=False)
        finalized = False

        def audit(outcome):
            """Retain the opaque snapshot reference, not figures or recipient names."""
            with work_transaction():
                # Audit attribution follows the current applied projection;
                # report calculation still uses its immutable historical one.
                current = SystemConfiguration.objects.select_related(
                    "active_configuration__parish"
                ).get()
                record_action(
                    Action.DAILY_DIGEST_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=principal.identity,
                    subject_id=snapshot_id,
                    parish_id=current.active_configuration.parish.pk,
                    campaign_id=retained.campaign_id,
                    context={"outcome": outcome},
                )

        def finish(completed):
            """Record server completion after closure, never imply browser receipt."""
            nonlocal finalized
            if not finalized:
                finalized = True
                audit(Outcome.SUCCEEDED if completed else Outcome.FAILED)

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
            admit_campaign(retained.campaign_id, mutating=False)
            if not DailyDigestSnapshot.objects.filter(
                pk=snapshot_id, campaign_id=retained.campaign_id
            ).exists():
                raise ReadUnavailable("Retained report is unavailable.")

        def content():
            """Prepare bounded bytes before headers; never consult current facts."""
            if representation != "html":
                chart = DailyDigestReady.objects.values_list("chart", flat=True).get(
                    snapshot_id=snapshot_id
                )
                return iter((bytes(chart),))
            selected = DailyDigestSnapshot.objects.select_related(
                "configuration__parish", "timezone_configuration", "preparation"
            ).get(pk=snapshot_id)
            ready = DailyDigestReady.objects.only(
                "id", "snapshot_id", "fact_set_id"
            ).get(snapshot=selected)
            document = retained_daily_document(selected, ready.fact_set)
            context = snapshot_context(
                document,
                mode=selected.preparation.mode,
                chart_url=reverse("admin:daily_digest_chart", args=[snapshot_id]),
                download_url=reverse("admin:daily_digest_download", args=[snapshot_id]),
            )
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
        return denial(status=503, retry=5)
    finally:
        if finish is not None and not handed_off:
            finish(False)
