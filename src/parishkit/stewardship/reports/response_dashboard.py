"""The response funnel read of the Response dashboard and ``report responses``.

The Response dashboard (``reports/responses/``, ``workspace_views.dashboard``,
#1537) shows the response funnel (``response_metrics``) as it stands at the
database's current instant: its Summary panel's Families by stage row is a
stat tile per stage with its share of Invited. The tiles give every count,
share and bar, so the page draws no funnel chart; nor does the emailed
daily report (#1370), so no funnel chart is drawn anywhere. The page reads
the metrics once; the tiles are a pure function of that one result.
``report responses`` (the command line) reads the same metrics, with the
activity buckets at a grain and each list's length, which the page no
longer shows.

Production is the default for everyone. Testing responses are shown only in
explicit Admin testing views (``docs/specs/stewardship/reports/spec.md``), so
only Administrators may choose ``mode=testing``, which reads the campaign's
active rehearsal epoch. The funnel holds counts only, never a Family's name
or identifier; the directory's Response filters list the Families behind
them (#933).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_models import RehearsalEpoch
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.stat_tiles import tile

from .chart_specs import STAGE_LABELS, share
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
class FamilyList:
    """One list of Families behind a funnel count (``report responses``).

    ``selects`` picks the funnel rows (``response_metrics.FamilyResponse``)
    the list holds, so its length equals the figure it sits behind; None
    marks the ParishSoft data-to-check list, chosen by the current
    ParishSoft data rather than by the funnel, which has no length here.
    The directory's Response filters list the same Families (#933).
    """

    title: str
    description: str
    selects: Callable | None = None


# The lists behind the counts. Their keys name the ``lists`` counts of
# ``pk-admin report responses``.
LISTS = {
    "submitted": FamilyList(
        gettext_lazy("Families that submitted"),
        gettext_lazy("Every Family with a submission"),
        lambda family: family.submitted_at is not None,
    ),
    "started": FamilyList(
        gettext_lazy("Families that started but did not submit"),
        gettext_lazy("Opened the form, nothing submitted yet"),
        lambda family: (
            family.form_opened_at is not None and family.submitted_at is None
        ),
    ),
    "not-opened": FamilyList(
        gettext_lazy("Invited Families that never opened the form"),
        gettext_lazy("Invitation delivered, form never opened"),
        lambda family: family.invited_at is not None and family.form_opened_at is None,
    ),
    "more-than-once": FamilyList(
        gettext_lazy("Families that submitted more than once"),
        gettext_lazy("More than one submission"),
        lambda family: family.submissions > 1,
    ),
    "data-quality": FamilyList(
        gettext_lazy("ParishSoft data to check"),
        gettext_lazy("Blank mailing name or envelope number 0"),
    ),
}


@dataclass(frozen=True)
class DashboardQuery:
    """``report responses``' options: the system mode and the activity grain.

    Both are closed vocabularies. ``grain`` is ``auto`` unless the reader
    chose one. The Response dashboard page's own options are its
    ``workspace.ReportQuery``.
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
    """One stat tile per funnel stage (ui-conventions "Stat tiles", #1540).

    Each tile has its stage's count and its note, if any. Once an
    invitation has been delivered, every stage but Invited also states its
    share compared with Invited, and every tile draws that share as a bar
    (#720): Invited's own bar is full, the reference the others are read
    against. Before then no tile has a share or a bar.
    """
    invited = metrics.stage("invited")
    return [
        tile(
            stage.key,
            STAGE_LABELS[stage.key],
            stage.count,
            details=(
                _("%(share)s compared with invited")
                % {"share": share(stage.count, invited)}
                if invited and stage.key != "invited"
                else "",
                stage.note or "",
            ),
            share=(stage.count, invited),
        )
        for stage in metrics.stages
    ]


def list_counts(families):
    """How many Families each funnel-chosen list holds, by key.

    The data-quality list is left out: counting it needs the ParishSoft
    read, and the dashboard reads only the funnel.
    """
    return {
        key: sum(1 for family in families if spec.selects(family))
        for key, spec in LISTS.items()
        if spec.selects is not None
    }


def rehearsal_epoch(campaign_id):
    """The campaign's active rehearsal epoch, or None when it has none."""
    return (
        RehearsalEpoch.objects.filter(campaign_id=campaign_id, state="active")
        .values_list("id", flat=True)
        .first()
    )


def read_metrics(campaign_id, mode):
    """The funnel of ``mode`` at the database's current instant, or None.

    Runs inside the campaign read guard's read-only transaction. A Testing
    read uses the campaign's active rehearsal epoch; with none there is
    nothing to show (None).
    """
    epoch = rehearsal_epoch(campaign_id) if mode == "testing" else None
    if mode == "testing" and epoch is None:
        return None
    return response_metrics(ResponseScope(campaign_id, mode, epoch), database_now())


def funnel_panels(metrics):
    """The Response dashboard's Families by stage row (#1537): a stat tile per
    stage, and the Invited count that decides whether shares are shown."""
    return {
        "metrics": metrics,
        "stage_tiles": tiles(metrics),
        "invited": metrics.stage("invited"),
    }


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


def _audit(principal, campaign_id, outcome, *, automatic=False):
    """Record that the funnel was read: who, which campaign, how it ended.

    No reported value is copied into the audit; an append takes no row locks,
    so it runs outside the read guard, as the other report audits do.
    ``automatic`` marks a view the open page made by itself to bring newer
    figures in (#1620); a reader's own view records no such key.
    """
    context = {"outcome": outcome}
    if automatic:
        context["automatic"] = True
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
            context=context,
        )
