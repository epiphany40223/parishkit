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
from parishkit.stewardship.web.digest_content import CHART_ID, EMAIL_CHART_WIDTH
from parishkit.stewardship.web.report_markup import BLUE, STYLES, TRACK, button

from .charts import render_participation
from .digest_funnel import (
    CAPTION,
    TESTING_NOTE,
    figure_rows,
    funnel_text,
    stage_rows,
)
from .links import report_url
from .participation import ParticipationDocument
from .response_metrics import ResponseMetrics
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

    ``funnel`` is the response funnel at the end of the report day
    (``digest_funnel``, #477), counted from durable timestamps by the
    compiling worker and again by the saved page, so the two agree; ``mode``
    is the digest's system mode. A Testing digest has no funnel and says
    where it is instead; a document without a mode shows neither.
    """

    snapshot_id: UUID
    participation: ParticipationDocument
    statistics: CampaignStatistics
    covered_dates: tuple[date, ...]
    date_format: str | None = None
    source_age: DataAge | None = None
    funnel: ResponseMetrics | None = None
    mode: str | None = None

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
        # Its Emailed reports address (NAV-12); emails sent before link the
        # old one, which redirects here. Imported here so the document
        # module itself stays free of Django.
        from django.urls import reverse

        return reverse("admin:daily_digest_snapshot", args=[self.snapshot_id])


@dataclass(frozen=True, repr=False)
class DailyDigestContent:
    """Compiled body and bounded in-memory chart; no recipients or file paths."""

    subject: str
    html: str
    text: str
    chart: InlineImage = field(repr=False)


def source_age_line(age, timezone, *, zone=True):
    """State the data age and connection in one line, in the parish's zone.

    For example "ParishSoft data as of October 6, 2026 at 8:00 AM EDT.
    Connection: working (last answered October 6, 2026 at 10:15 AM EDT)."
    With ``zone=False`` the instants are compact and name no zone, for the
    email's small print: its as-of line already states the zone once (#720).
    """

    def at(value):
        """One instant in the digest's single format."""
        return dates.format_instant(value, timezone, compact=not zone)

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


# The one comparison figure (#728): the parish-wide total, plainly labelled.
ALL_FAMILIES_COMPARISON = "Last year's pledges (all Families)"


def statistics_cards(statistics):
    """Format the active population's cards; an absent observation is never zero.

    With giving enabled, the cards end with the current annual pledges and the
    all-Families comparison total (#728). The comparison figure is not a
    subtotal of these Families: it covers every Family in the ParishSoft data.
    """
    active = statistics.active
    labels = [
        "Active Families",
        "Active Members",
        "Families with eligible email",
        "Families with deliverable email",
        "Families that have responded",
    ]
    values = (
        [
            f"{active.families:,}",
            f"{active.active_members:,}",
            active.proportion("eligible_email"),
            active.proportion("deliverable_email"),
            active.proportion("responses"),
        ]
        if active
        else ["Unavailable"] * len(labels)
    )
    if statistics.financial_enabled:
        labels += ["Current annual pledges", ALL_FAMILIES_COMPARISON]
        values += [
            active.annual_pledge.display if active else "Unavailable",
            statistics.comparison_pledge_all.display,
        ]
    return tuple(zip(labels, values, strict=True))


# One label for the card, the table column and the spec (#721).
PLEDGE_HEADING = "Cumulative annual pledges (USD)"
RESPONDED_LABEL = "Families that have responded"
FIRST_LABEL = "First submissions that day"


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
    # The as-of line names the report day, so the label does not repeat it.
    cards = [(RESPONDED_LABEL, row[2]), (FIRST_LABEL, row[1])]
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
    # A non-zero share keeps a 1% sliver; an incomplete one never looks full.
    filled = round(100 * part / whole)
    if part:
        filled = max(1, filled if part >= whole else min(99, filled))
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
    # Bars follow each card's identity, not its position in the list.
    measures = {
        RESPONDED_LABEL: (day.cumulative_responses, day.cohort_denominator, ""),
        FIRST_LABEL: (
            day.first_responses,
            peak,
            f" (busiest day: {peak:,})" if peak else "",
        ),
    }
    bars = []
    for label, value in report_day_cards(document):
        part, whole, note = measures.get(label, (None, None, ""))
        if not day.population_available:
            part, note = None, ""
        bars.append((label, value, bar(part, whole), note))
    return tuple(bars)


def funnel_html(document, plain):
    """The response funnel section of the email, or "" when there is none.

    One row per stage like the campaign totals: the stage (with its note),
    a bar of its share of Invited, and its count and share; then the three
    figures reported beside it. A Testing digest says where the funnel is.
    """
    if document.funnel is None:
        if document.mode == "testing":
            return f"<p{_style('caption')}>{plain(TESTING_NOTE)}</p>"
        return ""
    metrics = document.funnel
    invited = metrics.stage("invited")
    html = f"<h2{_style('heading')}>Response funnel</h2>"
    html += f"<p{_style('caption')}>{plain(CAPTION)}</p>"
    html += (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("rows")}><tbody>'
    )
    for label, count, portion, note in stage_rows(metrics):
        detail = f" ({note.lower()})" if note else ""
        html += (
            f'<tr><td width="34%"{_style("label")}>{plain(label)}{plain(detail)}</td>'
            f'<td width="40%"{_style("bar")}>{bar(count, invited)}</td>'
            f'<td width="26%" align="right"{_style("value")}>'
            f"<strong>{plain(f'{count:,}')}</strong>{plain(f' ({portion})')}</td></tr>"
        )
    html += "</tbody></table>"
    # The three figures as one small table, so they read as a group.
    html += (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("rows")}><tbody>'
    )
    html += "".join(
        f'<tr><td width="74%"{_style("label")}>{plain(label)}</td>'
        f'<td width="26%" align="right"{_style("value")}>{plain(f"{count:,}")}</td>'
        "</tr>"
        for label, count in figure_rows(metrics)
    )
    html += "</tbody></table>"
    return html


def chart_alt(document):
    """Alt text that carries the chart's key numbers, not a picture description."""
    chart = document.participation
    totals = " ".join(
        f"{label}: {value}." for label, value in report_day_cards(document)
    )
    return (
        f"Daily participation chart, {format_date(chart.first_date)} to "
        f"{format_date(chart.last_date)}. {totals} Exact values follow in the "
        "table."
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

    Visuals come first (#720): the as-of line, then one line per campaign
    total with its bar, the chart, the day-by-day table and the report link.
    The subject names the parish, campaign and report day (see
    render_digest_envelope), so the body has no header or small print. Every
    value is escaped; every attribute comes from this compiler and the
    validator's closed set.
    """
    chart = document.participation
    url = _report_url(document, public_origin)
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append(PLEDGE_HEADING)
    totals = report_day_bars(document)
    rows = digest_rows(document)
    # Say each fact once (#720): the as-of line is the report-day definition
    # (#721) and the only place that names the time zone. A recovery digest
    # also says which days it covers. The ParishSoft connection line is on
    # the saved report page, not in the email.
    first, last = document.covered_dates[0], document.covered_dates[-1]
    as_of = document.as_of
    if first != last:
        as_of = f"Covers {format_date(first)} through {format_date(last)}. {as_of}"
    # Match weekly display normalization; retained observations remain exact.
    # The strict HTML compiler boundary rejects NBSP parser rewrites.
    as_of, alt = (" ".join(label.split()) for label in (as_of, chart_alt(document)))
    text = as_of
    text += "\n\n" + "\n".join(f"{label}: {value}" for label, value, _b, _n in totals)
    if document.funnel is not None:
        text += "\n\n" + funnel_text(document.funnel)
    elif document.mode == "testing":
        text += "\n\n" + TESTING_NOTE
    text += "\n\n" + " | ".join(headings)
    text += "\n" + "\n".join(" | ".join(row) for row in rows)
    text += "\n\nOpen this exact report (staff login required): " + url

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    html = f"<p{_style('caption')}>{plain(as_of)}</p>"
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
    html += funnel_html(document, plain)
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
    html += button(url, "Open this exact report")
    stream = BytesIO()
    render_participation(chart, stream, format="png", email=True)
    return DailyDigestContent(
        document.title, html, text, InlineImage(stream.getvalue(), CHART_ID)
    )
