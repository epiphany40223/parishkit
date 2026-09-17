"""Accessible daily mail/report content over one retained report observation.

This module does no database, clock, recipient or provider I/O. Its durable
owner must authorize access and pin both inputs before calling it. Compiled
chart markup is deliberately separate from parish-authored safe HTML: adding
an inline report image must not enable arbitrary images in Family templates.
"""

from dataclasses import dataclass, field
from datetime import date
from html import escape
from io import BytesIO
from urllib.parse import urlsplit
from uuid import UUID

from parishkit.email.base import InlineImage
from parishkit.stewardship.web.digest_content import CHART_ALT, CHART_ID

from .charts import render_participation
from .participation import ParticipationDocument
from .statistics import CampaignStatistics


@dataclass(frozen=True)
class DailyDigestDocument:
    """One exact snapshot, including immutable missed-slot coverage when recovering.

    Statistics describe the current population at generation. The chart uses
    historical daily populations, so their totals need not be identical. Their
    source and submission cutoffs must nevertheless come from one observation.
    """

    snapshot_id: UUID
    participation: ParticipationDocument
    statistics: CampaignStatistics
    covered_dates: tuple[date, ...]

    def __post_init__(self):
        """Reject mixed cutoffs or incomplete coverage before rendering any output."""
        chart, statistics = self.participation, self.statistics
        if (
            not isinstance(self.snapshot_id, UUID)
            or not isinstance(chart, ParticipationDocument)
            or not isinstance(statistics, CampaignStatistics)
        ):
            raise ValueError("Daily digest requires typed immutable report inputs.")
        if (
            chart.population_scope != "historical"
            or chart.browser_timezone != chart.campaign_timezone
            or chart.campaign_id != statistics.campaign_id
            or chart.source_generation != statistics.source_generation
            or chart.source_as_of != statistics.source_as_of
            or chart.submission_watermark != statistics.submission_watermark
            or chart.requested_at != statistics.observed_at
            or chart.financial_enabled != statistics.financial_enabled
            or statistics.source_id is None
            or statistics.active is None
            or statistics.inactive is not None
        ):
            raise ValueError("Daily digest inputs must share one exact observation.")
        if (
            type(self.covered_dates) is not tuple
            or not self.covered_dates
            or any(type(value) is not date for value in self.covered_dates)
            or tuple(sorted(set(self.covered_dates))) != self.covered_dates
            or not chart.days
            or self.covered_dates[0] < chart.first_date
            or self.covered_dates[-1] != chart.last_date
        ):
            raise ValueError("Daily digest coverage requires complete ordered dates.")

    @property
    def title(self):
        """Make recovery ranges explicit, even when some intervening slots succeeded."""
        first, last = self.covered_dates[0], self.covered_dates[-1]
        if first == last:
            return f"Daily campaign digest — {last.isoformat()}"
        return (
            f"Recovery campaign digest — {first.isoformat()} through {last.isoformat()}"
        )

    @property
    def report_path(self):
        """Select a protected snapshot; this identity is not a bearer credential."""
        return f"/admin/reports/daily-digests/{self.snapshot_id}/"


@dataclass(frozen=True, repr=False)
class DailyDigestContent:
    """Compiled body and bounded in-memory chart; no recipients or file paths."""

    subject: str
    html: str
    text: str
    chart: InlineImage = field(repr=False)


def _report_url(document, public_origin):
    """Append only our protected route to the runtime's validated public origin."""
    if type(public_origin) is not str or any(
        ord(char) <= 32 or ord(char) == 127 for char in public_origin
    ):
        raise ValueError("Daily digest requires a public HTTP origin.")
    try:
        parsed = urlsplit(public_origin)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and "\\" not in public_origin
        )
        # Accessing port also rejects malformed/out-of-range port numbers.
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Daily digest requires a public HTTP origin.")
    return public_origin.rstrip("/") + document.report_path


def _cards(statistics):
    """Reuse statistics proportions and exact money formatting without new math."""
    active = statistics.active
    rows = [
        ("Active Families", f"{active.families:,}"),
        ("Active Members", f"{active.active_members:,}"),
        ("Families with eligible email", active.proportion("eligible_email")),
        ("Families with deliverable email", active.proportion("deliverable_email")),
        ("Families that have responded", active.proportion("responses")),
    ]
    if statistics.financial_enabled:
        rows.extend(
            [
                ("Current annual pledges", active.annual_pledge.display),
                ("Configured comparison pledges", active.comparison_pledge.display),
            ]
        )
    return tuple(rows)


def _daily_rows(document):
    """Show every day in the missed range, never just a bounded discovery page."""
    chart = document.participation
    rows = []
    for day in chart.days:
        if day.local_date < document.covered_dates[0]:
            continue
        cells = [
            day.local_date.isoformat(),
            f"{day.first_responses:,}" if day.population_available else "Unavailable",
            day.participation,
        ]
        if chart.financial_enabled:
            cells.append(
                f"${day.pledge_total:,.2f}" if day.pledge_available else "Unavailable"
            )
        rows.append(tuple(cells))
    return tuple(rows)


def render_daily_digest(document, *, public_origin):
    """Compile an accessible image/text report using the existing chart renderer.

    Every data-dependent HTML value is escaped; the only image and link markup
    comes from this compiler. The source document is reused without recapturing
    current state. A later owner may prepend sanitized parish-authored prose
    and the mandatory Testing banner, but cannot replace these required facts.
    """
    if not isinstance(document, DailyDigestDocument):
        raise TypeError("Daily digest rendering requires an immutable document.")
    chart = document.participation
    url = _report_url(document, public_origin)
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append("Cumulative annual pledges (USD)")
    cards = _cards(document.statistics)
    rows = _daily_rows(document)
    labels = (
        chart.parish_name,
        chart.campaign_name,
        document.title,
        f"Campaign dates use {chart.campaign_timezone}.",
        "Daily chart and table: Historical as of day.",
        "Statistics: Current active population at generation.",
        chart.as_of_label,
    )
    text = "\n".join(labels)
    text += "\n\n" + "\n".join(f"{label}: {value}" for label, value in cards)
    text += "\n\n" + " | ".join(headings)
    text += "\n" + "\n".join(" | ".join(row) for row in rows)
    text += "\n\nOpen this exact report (staff login required): " + url
    html = "".join("<p>" + escape(label) + "</p>" for label in labels)
    html += "<h2>Current population statistics</h2><dl>"
    html += "".join(
        "<dt>" + escape(label) + "</dt><dd>" + escape(value) + "</dd>"
        for label, value in cards
    )
    html += "</dl><h2>Daily participation</h2>"
    html += f'<img src="cid:{CHART_ID}" alt="{CHART_ALT}" width="720">'
    html += "<table><caption>Historical as of day</caption><thead><tr>"
    html += "".join('<th scope="col">' + escape(label) + "</th>" for label in headings)
    html += "</tr></thead><tbody>"
    html += "".join(
        "<tr>" + "".join("<td>" + escape(value) + "</td>" for value in row) + "</tr>"
        for row in rows
    )
    html += "</tbody></table>"
    html += (
        '<p><a href="'
        + escape(url, quote=True)
        + '" rel="noopener noreferrer">Open this exact report '
        "(staff login required)</a></p>"
    )
    stream = BytesIO()
    render_participation(chart, stream, format="png")
    return DailyDigestContent(
        document.title, html, text, InlineImage(stream.getvalue(), CHART_ID)
    )
