"""Accessible daily mail/report content over one retained report observation.

This module does no database, clock, recipient or provider I/O. Its durable
owner must authorize access and pin both inputs before calling it. Compiled
chart markup is deliberately separate from parish-authored safe HTML: adding
an inline report image must not enable arbitrary images in Family templates.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html import escape
from io import BytesIO
from uuid import UUID
from zoneinfo import ZoneInfo

from parishkit.email.base import InlineImage
from parishkit.stewardship.source.data_age import DataAge
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.digest_content import (
    BLUE,
    CHART_ID,
    EMAIL_CHART_WIDTH,
    STYLES,
    TRACK,
)

from .charts import render_participation
from .links import report_url
from .participation import ParticipationDocument
from .statistics import CampaignStatistics


@dataclass(frozen=True)
class DailyDigestDocument:
    """One exact snapshot, including immutable missed-slot coverage when recovering.

    The report day is the last covered date, which the schedule slot fixes
    (#721): a late, retried or recovery send never moves it. Every figure the
    email and saved page show comes from that day's end-of-day fact, the same
    row the chart ends on, so the chart, the table and the text always agree.
    ``statistics`` is the retained send-time observation; it binds the chart's
    source and submission cutoffs but is not shown, because its counts are
    live at the send time rather than at the end of the report day.

    ``date_format`` is the parish date format of the configuration the
    snapshot pinned (None means the default style), so a compile retried
    after an Admin changes the format still renders as first captured.

    ``source_age`` is the ParishSoft data age and connection as known at the
    observation (#510), stated as one line in the parish's time zone; the
    compiling worker reads it, and a document without it omits the line.
    """

    snapshot_id: UUID
    participation: ParticipationDocument
    statistics: CampaignStatistics
    covered_dates: tuple[date, ...]
    date_format: str | None = None
    source_age: DataAge | None = None

    def __post_init__(self):
        """Reject mixed cutoffs or incomplete coverage before rendering any output."""
        chart, statistics = self.participation, self.statistics
        if (
            not isinstance(self.snapshot_id, UUID)
            or not isinstance(chart, ParticipationDocument)
            or not isinstance(statistics, CampaignStatistics)
            or not isinstance(self.date_format, str | None)
            or not isinstance(self.source_age, DataAge | None)
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
            return f"Daily campaign digest — {format_date(last)}"
        return (
            f"Recovery campaign digest — {format_date(first)} through "
            f"{format_date(last)}"
        )

    @property
    def report_day(self):
        """The chart's last day: the report day's end-of-day participation fact."""
        return self.participation.days[-1]

    @property
    def as_of(self):
        """One plain line naming the moment every figure describes.

        The zone is the abbreviation in force at the end of the report day,
        for example EDT in October and EST after daylight saving time ends.
        """
        local_date = self.report_day.local_date
        zone = ZoneInfo(self.participation.campaign_timezone)
        abbreviation = datetime.combine(local_date, time.max, zone).tzname()
        return (
            f"All figures are as of the end of {format_date(local_date)} "
            f"({abbreviation})."
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


def source_age_line(age, timezone):
    """State the data age and connection in one line, in the parish's zone.

    For example "ParishSoft data as of October 6, 2026 at 8:00 AM EDT.
    Connection: working (last answered October 6, 2026 at 10:15 AM EDT)."
    """

    def at(value):
        """One instant in the digest's single format."""
        return dates.format_instant(value, timezone)

    if age.data_as_of is None:
        data = "ParishSoft data: not yet loaded."
    elif age.full_started_at is not None and age.full_started_at != age.data_as_of:
        data = (
            f"ParishSoft data as of {at(age.data_as_of)}, last full refresh "
            f"{at(age.full_started_at)}."
        )
    else:
        data = f"ParishSoft data as of {at(age.data_as_of)}."
    state, when = age.connection.state, age.connection.at
    if state == "failing":
        line = f"Connection: failing since {at(when)}."
    elif state == "not_checked":
        line = f"Connection: not checked since {at(when)}."
    elif state == "working":
        line = f"Connection: working (last answered {at(when)})."
    else:
        line = "Connection: not checked yet."
    return f"{data} {line}"


def _report_url(document, public_origin):
    """Append only our protected route to the runtime's validated public origin."""
    return report_url(public_origin, document.report_path)


def statistics_cards(statistics):
    """Reuse statistics proportions and exact money formatting without new math."""
    return population_cards(
        statistics.active, financial_enabled=statistics.financial_enabled
    )


def population_cards(active, *, financial_enabled, inactive=False):
    """Format either labeled subtotal; an absent observation is never zero."""
    label = "Inactive" if inactive else "Active"
    rows = [
        (f"{label} Families", f"{active.families:,}" if active else "Unavailable"),
        ("Active Members", f"{active.active_members:,}" if active else "Unavailable"),
        (
            "Families with eligible email",
            active.proportion("eligible_email") if active else "Unavailable",
        ),
        (
            "Families with deliverable email",
            active.proportion("deliverable_email") if active else "Unavailable",
        ),
        (
            "Families that have responded",
            active.proportion("responses") if active else "Unavailable",
        ),
    ]
    if financial_enabled:
        rows.extend(
            [
                (
                    "Current annual pledges",
                    active.annual_pledge.display if active else "Unavailable",
                ),
                (
                    "Configured comparison pledges",
                    active.comparison_pledge.display if active else "Unavailable",
                ),
            ]
        )
    return tuple(rows)


# One label for the card, the table column and the spec (#721).
PLEDGE_HEADING = "Cumulative annual pledges (USD)"


def report_day_cards(document):
    """Label the report day's end-of-day totals, the chart's own last point.

    These replace the send-time population statistics in the daily email and
    its saved page (#721): live counts would disagree with the chart whenever
    Families respond between midnight and the send. The values are the
    table's own last row, so cards and table can never format differently.
    """
    chart = document.participation
    day = document.report_day
    row = participation_row(day, financial_enabled=chart.financial_enabled)
    labels = (
        "Families that have responded",
        f"First submissions on {format_date(day.local_date)}",
    )
    cards = [(labels[0], row[2]), (labels[1], row[1])]
    if chart.financial_enabled:
        cards.append((PLEDGE_HEADING, row[3]))
    return tuple(cards)


def participation_row(day, *, financial_enabled):
    """Format one exact daily fact identically in email, web tables and tooltips."""
    cells = [
        format_date(day.local_date, compact=True),
        f"{day.first_responses:,}" if day.population_available else "Unavailable",
        day.participation,
    ]
    if financial_enabled:
        cells.append(
            f"${day.pledge_total:,.2f}" if day.pledge_available else "Unavailable"
        )
    return tuple(cells)


# The day-by-day table shows at least this many of the latest campaign days.
TABLE_DAYS = 7


def digest_rows(document):
    """Show the last week and every day in a missed range, never a bounded page.

    A recovery digest's covered range can be longer than a week; all of it is
    shown. The chart above the table always has the whole campaign.
    """
    chart = document.participation
    first = min(
        document.covered_dates[0], chart.last_date - timedelta(days=TABLE_DAYS - 1)
    )
    return tuple(
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
        if day.local_date >= first
    )


def _style(name):
    """One compiler-owned inline style attribute from the validator's closed set."""
    return f' style="{STYLES[name]}"'


def _segment(width, colour):
    """One coloured cell of a bar; ``&nbsp;`` keeps Outlook from collapsing it."""
    return f'<td width="{width}%" bgcolor="{colour}"{_style("segment")}>&nbsp;</td>'


def bar(part, whole):
    """A progress bar of table cells: a filled share and a grey remainder.

    Table cells with ``bgcolor`` and percentage widths are the one bar that
    every mail program draws, Outlook for Windows included, and it survives
    image blocking. A non-zero share narrower than 1% still draws a 1% sliver,
    so it never looks like zero. Returns "" when there is nothing to compare
    against, so an unavailable figure never draws as an empty bar.
    """
    if not whole or part is None:
        return ""
    filled = max(1, min(100, round(100 * part / whole))) if part else 0
    cells = _segment(filled, BLUE) if filled else ""
    cells += _segment(100 - filled, TRACK) if filled < 100 else ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("track")}><tbody><tr>{cells}</tr></tbody></table>'
    )


def report_day_bars(document):
    """Pair each report-day card with what its bar measures, if anything.

    Families that have responded are drawn against the Families they are out
    of; the day's first submissions against the campaign's busiest day so far,
    stated beside the number. Pledges have no end-of-day target (the
    comparison pledges are a live figure, #721), so they have no bar.
    """
    chart = document.participation
    day = document.report_day
    available = [entry for entry in chart.days if entry.population_available]
    peak = max((entry.first_responses for entry in available), default=0)
    bars = []
    for index, (label, value) in enumerate(report_day_cards(document)):
        if not day.population_available or index > 1:
            bars.append((label, value, "", ""))
        elif index == 0:
            bars.append(
                (
                    label,
                    value,
                    bar(day.cumulative_responses, day.cohort_denominator),
                    "",
                )
            )
        else:
            note = f" (busiest day: {peak:,})" if peak else ""
            bars.append((label, value, bar(day.first_responses, peak), note))
    return tuple(bars)


def chart_alt(document):
    """Alt text that carries the chart's key numbers, not a picture description."""
    chart = document.participation
    cards = report_day_cards(document)
    return (
        f"Daily participation chart, {format_date(chart.first_date)} to "
        f"{format_date(chart.last_date)}. {cards[0][0]}: {cards[0][1]}. "
        f"{cards[1][0]}: {cards[1][1]}. Exact values follow in the table."
    )


def render_daily_digest(document, *, public_origin):
    """Compile an accessible image/text report using the existing chart renderer.

    Every data-dependent HTML value is escaped; the only image and link markup
    comes from this compiler. The source document is reused without recapturing
    current state. A later owner may prepend sanitized parish-authored prose
    and the mandatory Testing banner, but cannot replace these required facts.
    """
    if not isinstance(document, DailyDigestDocument):
        raise TypeError("Daily digest rendering requires an immutable document.")
    # Pin the snapshot configuration's style, not the worker's active one.
    with dates.using(document.date_format):
        return _render_daily_digest(document, public_origin=public_origin)


def _render_daily_digest(document, *, public_origin):
    """Compile the report body; the caller has pinned the parish date format.

    Visuals come first (#720): a short header and the as-of caption, then one
    line per campaign total with its bar, the chart, and the day-by-day table.
    The ParishSoft connection line and the sign-in note are small print at the
    end. Every value is escaped; every attribute comes from this compiler and
    the validator's closed set.
    """
    chart = document.participation
    url = _report_url(document, public_origin)
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append(PLEDGE_HEADING)
    totals = report_day_bars(document)
    rows = digest_rows(document)
    footer = "Staff sign-in required."
    if document.source_age is not None:
        # The ParishSoft line is connection health at the send, not the time
        # the figures describe; say so, so it cannot read as a second "as of".
        footer += " When this email was made: " + source_age_line(
            document.source_age, chart.campaign_timezone
        )
    # Match weekly display normalization; retained observations remain exact.
    # The strict HTML compiler boundary rejects NBSP parser rewrites.
    eyebrow, title, as_of, footer, alt = (
        " ".join(label.split())
        for label in (
            f"{chart.parish_name} · {chart.campaign_name}",
            document.title,
            document.as_of,
            footer,
            chart_alt(document),
        )
    )
    text = "\n".join((eyebrow, title, as_of))
    text += "\n\n" + "\n".join(f"{label}: {value}" for label, value, _b, _n in totals)
    text += "\n\n" + " | ".join(headings)
    text += "\n" + "\n".join(" | ".join(row) for row in rows)
    text += "\n\nOpen this exact report (staff login required): " + url
    text += "\n\n" + footer

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    html = f"<p{_style('eyebrow')}>{plain(eyebrow)}</p>"
    html += f"<h2{_style('title')}>{plain(title)}</h2>"
    html += f"<p{_style('caption')}>{plain(as_of)}</p>"
    html += f"<h2{_style('heading')}>Campaign totals</h2>"
    html += (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("rows")}><tbody>'
    )
    html += "".join(
        f'<tr><td width="34%"{_style("label")}>{plain(label)}</td>'
        f'<td width="40%"{_style("bar")}>{bar_html}</td>'
        f'<td width="26%" align="right"{_style("value")}>'
        f"<strong>{plain(value)}</strong>{plain(note)}</td></tr>"
        for label, value, bar_html, note in totals
    )
    html += "</tbody></table>"
    html += f"<h2{_style('heading')}>Daily participation</h2>"
    html += (
        f'<img src="cid:{CHART_ID}" alt="{escape(alt, quote=True)}" '
        f'width="{EMAIL_CHART_WIDTH}"'
        f"{_style('image')}>"
    )
    html += (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("table")}><caption{_style("table-caption")}>'
        "Day by day</caption><thead><tr>"
    )
    html += "".join(
        f'<th scope="col" align="{"left" if index == 0 else "right"}"'
        f"{_style('th')}>{plain(label)}</th>"
        for index, label in enumerate(headings)
    )
    html += "</tr></thead><tbody>"
    html += "".join(
        "<tr>"
        + "".join(
            f'<td align="{"left" if index == 0 else "right"}"{_style("td")}>'
            f"{plain(value)}</td>"
            for index, value in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    html += "</tbody></table>"
    html += (
        f'<p{_style("action")}><a href="'
        + escape(url, quote=True)
        + f'"{_style("button")} rel="noopener noreferrer">Open this exact report'
        "</a></p>"
    )
    html += f"<p{_style('footer')}>{plain(footer)}</p>"
    stream = BytesIO()
    render_participation(chart, stream, format="png", email=True)
    return DailyDigestContent(
        document.title, html, text, InlineImage(stream.getvalue(), CHART_ID)
    )
