"""The Response dashboard's Summary, as its export captures and files it (#1557).

The page's Summary panel shows two rows of stat tiles: the Active population
statistics (``calculate_statistics``) and Families by stage (the response
funnel, ``response_dashboard.read_metrics``). The export carries all the data
on the page, so when a participation export is requested its transaction
reads both sets of figures as the page does (``capture``) and stores them,
as plain labels, values and notes, in the request's immutable
``ParticipationExportSnapshot`` (frozen migration 0142). The worker turns
that document back into the Summary section of every format
(``ParticipationSummary.rows``): the export's files never re-read a figure,
so a response after the request cannot change them.

Only aggregate counts and totals are captured, never a Family's name or
identifier, and only English labels, as every report file is written.
"""

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from django.utils import translation
from django.utils.translation import gettext as _

from .chart_specs import STAGE_LABELS, share
from .charts import DAILY_SECTION
from .money import MoneyAmount, source_cents

SUMMARY = "Summary"
# The daily section, as the PDF's chart page names it.
DAILY = DAILY_SECTION
# The Summary section's columns, in every format (UX conventions, "Downloaded
# files"): the tile row a figure sits in, its label, its value, a stage's
# share compared with Invited and the tile's note or unknown reason.
COLUMNS = ("Group", "Figure", "Value", "Share compared with Invited", "Note")
POPULATION = "Active population"
STAGES = "Families by stage"
DATA_AS_OF = "Data as of"


def population_figures(statistics):
    """The Active population tiles' figures: ``(key, label, value, reason)``.

    The active Families and Members, and with giving enabled two pledge
    figures (#728): the current annual pledges, and last year's pledges of
    every Family in the ParishSoft data, whatever its status today. A value
    is a count, a known ``MoneyAmount`` or None when unknown, with the one
    reason the page gives for it; a known value has no reason. The page's
    tiles (``workspace_views.statistics_panel``) and the export's capture
    both read these.
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
        pledges = active.annual_pledge if active else MoneyAmount(None)
        everyone = statistics.comparison_pledge_all
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
            (
                "annual_pledges",
                _("Current annual pledges"),
                pledges if pledges.available else None,
                annual_reason,
            ),
            (
                "comparison_pledges_all",
                _("Last year's pledges (all Families)"),
                everyone if everyone.available else None,
                everyone_reason,
            ),
        ]
    return figures


def summary_document(statistics, metrics):
    """The capture's document: the page's Summary as plain JSON values.

    ``observed_at`` is the moment the statistics were counted (the page's
    "Data as of"); each population row is a label, its value (a whole count,
    ``{"money": "1234.50"}`` or None) and its note (the unknown value's
    reason); each stage row is a label, its count and its note. SQL checks
    exactly this shape (migration 0142).
    """
    with translation.override("en"):
        population = []
        for _key, label, value, reason in population_figures(statistics):
            if isinstance(value, MoneyAmount):
                value = {"money": value.canonical}
            population.append(
                {
                    "label": str(label),
                    "value": value,
                    "note": "" if value is not None else str(reason),
                }
            )
        stages = [
            {
                "label": str(STAGE_LABELS[stage.key]),
                "count": stage.count,
                "note": str(stage.note or ""),
            }
            for stage in metrics.stages
        ]
    return {
        "observed_at": statistics.observed_at.isoformat(),
        "population": population,
        "stages": stages,
    }


def unread_statistics(campaign_id):
    """A stand-in for statistics that could not be read: every figure unknown.

    It has what ``population_figures`` reads: no active population (so each
    figure is None with the page's no-data reason) and whether the
    campaign's configuration enables giving (so the pledge rows are there,
    as on the page). Its Data as of is the request's instant: the moment
    the figures were found unreadable, not a count.
    """
    from parishkit.stewardship.accounts.sessions import database_now
    from parishkit.stewardship.campaigns.models import Campaign

    modules = (
        Campaign.objects.select_related("active_configuration")
        .get(pk=campaign_id)
        .active_configuration.values.get("modules", ())
    )
    return SimpleNamespace(
        active=None,
        financial_enabled="financial" in modules,
        financial=None,
        comparison_pledge_all=MoneyAmount(None),
        observed_at=database_now(),
    )


def capture(campaign_id):
    """Read the page's Summary now, in the caller's transaction (``create_export``).

    The same reads as the page's Production view: the statistics from their
    one-statement input capture, and the Production funnel at the
    database's current instant. The page offers no export while the
    statistics cannot be read; a request made as they became unreadable
    still captures the funnel, with every Active population figure unknown
    and the page's reason, never zero.
    """
    from .response_dashboard import read_metrics
    from .statistics import StatisticsUnavailable, calculate_statistics
    from .statistics_selection import capture_statistics

    try:
        statistics = calculate_statistics(capture_statistics(campaign_id))
    except StatisticsUnavailable:
        statistics = unread_statistics(campaign_id)
    return summary_document(statistics, read_metrics(campaign_id, "production"))


@dataclass(frozen=True)
class ParticipationSummary:
    """One captured Summary, detached for the renderers.

    ``population`` rows are ``(label, value, note)`` with a count, a
    ``MoneyAmount`` or None; ``stages`` rows are ``(label, count, note)``,
    Invited first, as the funnel lists them.
    """

    observed_at: datetime
    population: tuple
    stages: tuple

    @classmethod
    def from_document(cls, payload):
        """Rebuild the summary from its stored capture document."""
        observed = datetime.fromisoformat(payload["observed_at"])
        if observed.utcoffset() is None:
            raise ValueError("A summary's Data as of must be an aware instant.")
        population = tuple(
            (
                row["label"],
                MoneyAmount(source_cents(row["value"]["money"]))
                if isinstance(row["value"], dict)
                else row["value"],
                row["note"],
            )
            for row in payload["population"]
        )
        stages = tuple(
            (row["label"], row["count"], row["note"]) for row in payload["stages"]
        )
        return cls(observed, population, stages)

    def rows(self):
        """The Summary section's rows under ``COLUMNS``, typed for the renderers.

        A value is a count, a ``MoneyAmount``, the Data as of instant, or
        None when unknown. A stage's share is ``(count, invited)``, given as
        the page does, once an invitation has been delivered and never for
        Invited itself; None otherwise.
        """
        rows = [
            (POPULATION, label, value, None, note)
            for label, value, note in self.population
        ]
        rows.append((POPULATION, DATA_AS_OF, self.observed_at, None, ""))
        # The funnel lists Invited first (response_metrics.STAGES), and the
        # capture keeps its order, so the first stage is the reference.
        invited = self.stages[0][1] if self.stages else 0
        for index, (label, count, note) in enumerate(self.stages):
            ratio = (count, invited) if invited and index else None
            rows.append((STAGES, label, count, ratio, note))
        return rows


def share_text(ratio):
    """A stage's share in the page's words ("41%"), or blank for none."""
    return "" if ratio is None else share(*ratio)
