"""Admin/Staff response dashboard: the funnel, its figures and charts (#477).

One page per campaign (``reports/<campaign>/responses/``) shows the response
funnel (``response_metrics``) as it stands at the database's current instant:
a tile per stage with its share of Invited, the three figures reported beside
the funnel, and the funnel and activity charts (``chart_specs``), which the
browser draws from the embedded specifications. The page reads the metrics
once; the totals, the activity buckets at the chosen grain and the chart specs
are pure functions of that one result. Nothing renders on the server, so a
page view never starts the chart helper process.

Production is the default for everyone. Testing responses are shown only in
explicit Admin testing views (``docs/specs/stewardship/reports/spec.md``), so
only Administrators may choose ``mode=testing``, which reads the campaign's
active rehearsal epoch. The page shows counts only, never a Family's name or
identifier; the lists behind the counts are a later page.
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_models import RehearsalEpoch
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .chart_specs import STAGE_LABELS, activity_chart, funnel_chart, share
from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read
from .response_metrics import (
    GRAINS,
    MODES,
    ResponseScope,
    activity_series,
    response_metrics,
)

# The activity chart is hourly while everything it shows (the first activity
# or the first marked send, through the cutoff) spans at most this long, and
# daily after that, unless the reader chooses a grain.
HOURLY_SPAN = timedelta(days=3)
AUTO = "auto"


@dataclass(frozen=True)
class DashboardQuery:
    """The page's only URL state: the system mode and the activity grain.

    Both are closed vocabularies, so the URL never carries anything
    identifying. ``grain`` is ``auto`` unless the reader chose one.
    """

    mode: str = "production"
    grain: str = AUTO

    @classmethod
    def parse(cls, parameters):
        """Refuse unknown, repeated or out-of-vocabulary parameters."""
        values = filters(parameters, allowed={"mode", "grain"})
        mode = values.get("mode", "production")
        grain = values.get("grain", AUTO)
        if mode not in MODES or grain not in (AUTO, *GRAINS):
            raise ValueError("Invalid response dashboard options.")
        return cls(mode, grain)

    def url(self, campaign_id, **changes):
        """This page's URL with ``changes`` applied, leaving defaults out."""
        chosen = replace(self, **changes)
        values = {}
        if chosen.mode != "production":
            values["mode"] = chosen.mode
        if chosen.grain != AUTO:
            values["grain"] = chosen.grain
        path = reverse("admin:response_dashboard", args=[campaign_id])
        return path + ("?" + urlencode(values) if values else "")


def chosen_grain(metrics, requested):
    """The grain to chart: the reader's choice, or one fitting the span.

    The span runs from the earliest instant the chart shows (the first link,
    form or submission, or the first marked send's scheduled time) to the
    cutoff; with nothing to show the hourly chart is as good as any.
    """
    if requested != AUTO:
        return requested
    starts = [
        at
        for family in metrics.families
        for at in (family.link_at, family.form_opened_at, family.submitted_at)
        if at is not None
    ] + [send.scheduled for send in metrics.sends]
    if not starts or metrics.as_of - min(starts) <= HOURLY_SPAN:
        return "hour"
    return "day"


def at_grain(metrics, grain):
    """``metrics`` with its activity series re-bucketed at ``grain``, no re-read."""
    if grain == metrics.grain:
        return metrics
    activity = activity_series(metrics.families, ZoneInfo(metrics.timezone), grain)
    return replace(metrics, grain=grain, activity=activity)


def tiles(metrics):
    """One tile per funnel stage: label, count, share of Invited and its note."""
    invited = metrics.stage("invited")
    return [
        {
            "key": stage.key,
            "label": STAGE_LABELS[stage.key],
            "count": stage.count,
            "share": share(stage.count, invited),
            "note": stage.note,
        }
        for stage in metrics.stages
    ]


def figures(metrics):
    """The three figures reported beside the funnel, as (label, count) pairs."""
    return [
        (_("Invitation not sent: already responded"), metrics.skipped_responded),
        (_("Submitted without a delivered invitation"), metrics.submitted_uninvited),
        (_("Submitted more than once"), metrics.submitted_again),
    ]


def untitled(chart):
    """``chart`` without the title drawn inside it: the page's heading names it.

    The emailed image keeps its title; on the page the panel's heading shows
    ``chart.title``, which also stays the view's accessible name.
    """
    spec = {key: value for key, value in chart.spec.items() if key != "title"}
    return replace(chart, spec=spec)


def rehearsal_epoch(campaign_id):
    """The campaign's active rehearsal epoch, or None when it has none."""
    return (
        RehearsalEpoch.objects.filter(campaign_id=campaign_id, state="active")
        .values_list("id", flat=True)
        .first()
    )


def page_context(campaign, query, metrics, *, can_test=False):
    """The template context for ``metrics`` (None: no Testing rehearsal to show).

    A pure function of its inputs, so the page is tested without a database.
    The activity series is re-bucketed at the chosen grain before the charts
    are built; ``can_test`` offers the Production/Testing switch.
    """
    context = {
        "campaign": campaign,
        "query": query,
        "testing": query.mode == "testing",
        "can_test": can_test,
        "production_url": query.url(campaign.pk, mode="production"),
        "testing_url": query.url(campaign.pk, mode="testing"),
        "metrics": None,
    }
    if metrics is None:
        return context
    metrics = at_grain(metrics, chosen_grain(metrics, query.grain))
    return context | {
        "metrics": metrics,
        "tiles": tiles(metrics),
        "figures": figures(metrics),
        "charts": [untitled(funnel_chart(metrics)), untitled(activity_chart(metrics))],
        "invited": metrics.stage("invited"),
        "hour_url": query.url(campaign.pk, grain="hour"),
        "day_url": query.url(campaign.pk, grain="day"),
    }


def dashboard_context(campaign_id, query, *, can_test=False):
    """Read the funnel once at the database's current instant and shape the page.

    Runs inside the campaign read guard's read-only transaction. A Testing
    view reads the campaign's active rehearsal epoch; with none, it shows no
    figures.
    """
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    epoch = rehearsal_epoch(campaign_id) if query.mode == "testing" else None
    metrics = None
    if query.mode == "production" or epoch is not None:
        metrics = response_metrics(
            ResponseScope(campaign_id, query.mode, epoch), database_now()
        )
    return page_context(campaign, query, metrics, can_test=can_test)


def _principal(request, store, *, read_only=False):
    """A current Admin or Staff user, reloaded from policy on every call."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The response dashboard is unavailable.")
    return principal


def _admit_mode(principal, query):
    """Testing responses are shown to Administrators only."""
    if query.mode == "testing" and "administrator" not in principal.roles:
        raise PermissionError("Testing responses are shown to Administrators only.")


def _audit(principal, campaign_id, outcome):
    """Record that the dashboard was read: who, which campaign, how it ended.

    No reported value is copied into the audit; an append takes no row locks,
    so it runs outside the read guard, as the other report audits do.
    """
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.RESPONSE_DASHBOARD_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome},
        )


@require_safe
def dashboard(request, campaign_id):
    """The response dashboard of one campaign, read under the campaign guard.

    Admission, the purge protection, the role recheck inside the guard and
    the completion audit follow the other campaign reports (``talent_views``).
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        try:
            query = DashboardQuery.parse(request.GET)
        except ValueError:
            return private_response("Invalid report filters.\n", status=400)
        _admit_mode(principal, query)
        admit_report_read(campaign_id)
        finalized = False

        def finish(completed):
            """Audit completion once, after the read-only transaction closes."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the page is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Response dashboard access changed.")
            _admit_mode(fresh, query)
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Render the page from the one guarded read."""
            context = dashboard_context(
                campaign_id, query, can_test="administrator" in principal.roles
            )
            page = render_to_string(
                "stewardship/response-dashboard.html", context, request=request
            )
            return iter((page.encode(),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
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
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except ValueError as error:
        # The page is built before the response starts, so a shaping fault
        # lands here; the reader's options were already validated above.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)
