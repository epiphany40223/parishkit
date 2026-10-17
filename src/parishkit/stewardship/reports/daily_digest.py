"""Accessible daily mail/report content over one retained report observation.

This module does no database, clock, recipient or provider I/O. Its durable
owner must authorize access and pin both inputs before calling it. Compiled
chart markup is deliberately separate from parish-authored safe HTML: adding
an inline report image must not enable arbitrary images in Family templates.
"""

from dataclasses import dataclass, field, replace
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
from .digest_comparison import render_images, section_html, section_text, withheld
from .digest_funnel import TESTING_NOTE, funnel_text, stage_rows
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

    ``comparison`` is the Compared with past campaigns section
    (``digest_comparison.section``, #1018 slice 7) when the schedule has it
    on and a past campaign is imported: computed once by the compiling
    worker and retained with the ready row, which the saved page reads
    back, so later imports or removals never change it. None leaves the
    section out.
    """

    snapshot_id: UUID
    participation: ParticipationDocument
    statistics: CampaignStatistics
    covered_dates: tuple[date, ...]
    date_format: str | None = None
    source_age: DataAge | None = None
    funnel: ResponseMetrics | None = None
    mode: str | None = None
    comparison: dict | None = None

    def __post_init__(self):
        """Reject mixed cutoffs or incomplete coverage before rendering any output."""
        chart, statistics = self.participation, self.statistics
        if (
            not isinstance(self.snapshot_id, UUID)
            or not isinstance(self.comparison, dict | None)
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
            f"All data is as of the end of {format_date(local_date)} ({abbreviation})."
        )

    @property
    def opening(self):
        """The email's one opening line: what the report is and its as-of moment.

        For example "Here is yesterday’s report; all data is as of the end of
        October 16, 2026 (EDT)." (#1532). A recovery digest names the dates
        it covers instead of "yesterday". The saved report page keeps the
        plain ``as_of`` sentence, since it is not "yesterday" when read later.
        """
        first, last = self.covered_dates[0], self.covered_dates[-1]
        report = (
            "Here is yesterday’s report"
            if first == last
            else f"Here is the report for {format_date(first)} through "
            f"{format_date(last)}"
        )
        # "All data is as of …." continues the sentence in lower case.
        return f"{report}; a{self.as_of[1:]}"


@dataclass(frozen=True, repr=False)
class DailyDigestContent:
    """Compiled body and bounded in-memory charts; no recipients or file paths.

    ``comparison`` holds the Compared with past campaigns section's chart
    images (#1018 slice 7), none when the digest has no section or
    ``charts_withheld``: the last compile attempt could not draw a server
    chart, so the digest is sent without them, its tables and text part
    carrying every number. ``funnel`` holds the Response funnel section's
    two chart images (#477 slice 2) only when a ready row compiled before
    #1370 removed them is sent again (``digest_fanout``); a new compile
    never has them.
    """

    subject: str
    html: str
    text: str
    chart: InlineImage = field(repr=False)
    comparison: tuple = field(default=(), repr=False)
    funnel: tuple = field(default=(), repr=False)
    charts_withheld: bool = False

    @property
    def images(self):
        """Every server-drawn chart's (content id, PNG) pair, in email order."""
        return (
            tuple((image.content_id, image.data) for image in self.funnel)
            + self.comparison_images
        )

    @property
    def comparison_images(self):
        """The section's (content id, PNG bytes) pairs, as the validator takes them."""
        return tuple((image.content_id, image.data) for image in self.comparison)


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


# The label of the email's links to the Response dashboard (#1532).
DASHBOARD_LABEL = "Open the Response dashboard"


def response_dashboard_url(public_origin, mode):
    """The Response dashboard's address on the public origin (#1532).

    The one place the daily email names the dashboard's route, so a later
    move of the page changes only this function. A Testing digest links the
    dashboard's Testing view, where its funnel is (``TESTING_NOTE``).
    """
    from django.urls import reverse

    url = report_url(public_origin, reverse("admin:response_dashboard"))
    return url + "?mode=testing" if mode == "testing" else url


def comparison_url(section, public_origin):
    """The Campaign comparison report on the section's day (``?day=N``).

    The page takes only that whole number (campaign-history spec, "Page
    layout"); it comes from the compiled section, never from input.
    """
    from django.urls import reverse

    path = report_url(public_origin, reverse("admin:campaign_comparison"))
    return f"{path}?day={int(section['day'])}"


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


# The day-by-day table shows at least this many of the latest campaign days
# (five since #1532).
TABLE_DAYS = 5


def digest_rows(document):
    """Show the last five days and every day in a missed range, never a page.

    A recovery digest's covered range can be longer than that; all of it is
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

    One row per stage like the campaign totals (no charts since #1370):
    the stage (with its note), a bar of its share of email invites sent, and
    its count and share. The figures the dashboard reports beside the funnel
    are left out (#1532). A Testing digest says where the funnel is.
    """
    if document.funnel is None:
        if document.mode == "testing":
            return f"<p{_style('caption')}>{plain(TESTING_NOTE)}</p>"
        return ""
    metrics = document.funnel
    invited = metrics.stage("invited")
    rows = [
        (f"{label} ({note.lower()})" if note else label, count, invited, portion)
        for label, count, portion, note in stage_rows(metrics)
    ]
    html = f"<h2{_style('heading')}>Response funnel</h2>"
    html += (
        '<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" border="0"{_style("rows")}><tbody>'
    )
    html += "".join(
        f'<tr><td width="34%"{_style("label")}>{plain(label)}</td>'
        f'<td width="40%"{_style("bar")}>{bar(count, whole)}</td>'
        f'<td width="26%" align="right"{_style("value")}>'
        f"<strong>{plain(f'{count:,}')}</strong>{plain(f' ({portion})')}"
        "</td></tr>"
        for label, count, whole, portion in rows
    )
    return html + "</tbody></table>"


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


def render_daily_digest(document, *, public_origin, charts=True):
    """Compile an accessible image/text report using the existing chart renderer.

    Every data-dependent HTML value is escaped; the only image and link markup
    comes from this compiler. The source document is reused without recapturing
    current state. A later owner may prepend sanitized parish-authored prose
    and the mandatory Testing banner, but cannot replace these required facts.

    ``charts`` False withholds every server-drawn chart (#477): through
    ``digest_comparison.withheld``, the comparison section's. The
    participation chart, drawn in process, always stays.
    """
    if not isinstance(document, DailyDigestDocument):
        raise TypeError("Daily digest rendering requires an immutable document.")
    if not charts:
        document = replace(document, comparison=withheld(document.comparison))
    # Pin the snapshot configuration's style, not the worker's active one.
    with dates.using(document.date_format):
        return _render_daily_digest(
            document, public_origin=public_origin, charts=charts
        )


def day_table_html(headings, rows, plain):
    """The "Day by day" table, two rows per day so it fits a phone (#1559).

    Four columns (date, first submissions, participation, pledges) need about
    380px, wider than a 320px phone leaves inside the email's card, and mail
    programs cannot scroll a table. So each day's date heads its own row
    across the table, a row-group heading for screen readers, and its values
    follow in one row under the value columns' headings. ``plain`` escapes a
    text node. The first value column has no left padding, so the table
    starts flush with its caption.
    """
    values = headings[1:]
    span = len(values)

    def cells(tag, items, style, extra=""):
        """One row of right-aligned cells; only the later ones are padded."""
        return "".join(
            f'<{tag}{extra} align="right"{_style(style if index else style + "-lead")}>'
            f"{plain(item)}</{tag}>"
            for index, item in enumerate(items)
        )

    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("table")}><caption{_style("table-caption")}>'
        "Day by day</caption><thead><tr>"
        + cells("th", values, "th", ' scope="col"')
        + "</tr></thead>"
    )
    for day, *figures in rows:
        html += (
            f'<tbody><tr><th scope="rowgroup" colspan="{span}" align="left"'
            f"{_style('day')}>{plain(day)}</th></tr>"
            f"<tr>{cells('td', figures, 'td')}</tr></tbody>"
        )
    return html + "</table>"


def _render_daily_digest(document, *, public_origin, charts=True):
    """Compile the report body; the caller has pinned the parish date format.

    Visuals come first (#720): the opening line with a Response dashboard
    link beside it (#1532), then one line per campaign total with its bar,
    the chart, the day-by-day table and a Response dashboard button.
    The subject names the parish, campaign and report day (see
    render_digest_envelope), so the body has no header or small print. Every
    value is escaped; every attribute comes from this compiler and the
    validator's closed set.
    """
    chart = document.participation
    url = response_dashboard_url(public_origin, document.mode)
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append(PLEDGE_HEADING)
    totals = report_day_bars(document)
    rows = digest_rows(document)
    # Say each fact once (#720): the opening line carries the report-day
    # definition (#721) and is the only place that names the time zone. A
    # recovery digest says which days it covers there. The ParishSoft
    # connection line is on the saved report page, not in the email.
    # Match weekly display normalization; retained observations remain exact.
    # The strict HTML compiler boundary rejects NBSP parser rewrites.
    opening, alt = (
        " ".join(label.split()) for label in (document.opening, chart_alt(document))
    )
    link_line = f"{DASHBOARD_LABEL} (staff login required): {url}"
    text = f"{opening}\n{link_line}"
    text += "\n\n" + "\n".join(f"{label}: {value}" for label, value, _b, _n in totals)
    if document.funnel is not None:
        text += "\n\n" + funnel_text(document.funnel)
    elif document.mode == "testing":
        text += "\n\n" + TESTING_NOTE
    text += "\n\n" + " | ".join(headings)
    text += "\n" + "\n".join(" | ".join(row) for row in rows)
    text += "\n\n" + link_line
    section = document.comparison
    if section is not None:
        link = comparison_url(section, public_origin)
        text += "\n\n" + section_text(section, link)

    def plain(value):
        """Escape a text node; canonical HTML leaves quotes literal there."""
        return escape(value, quote=False)

    # Prominent (#1016): body size and colour, not the muted caption style.
    # The dashboard link sits right-aligned on the same line (#1532), in a
    # two-cell presentation table: floats and flexbox are unreliable in
    # Outlook and Gmail. The link cell is 30% wide, top-aligned and wraps into
    # short lines, so the sentence keeps most of a phone's width.
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"{_style("track")}><tbody><tr>'
        f"<td{_style('opening')}>{plain(opening)}</td>"
        f'<td width="30%" align="right"{_style("opening-link")}>'
        f'<a href="{escape(url, quote=True)}"{_style("link")} '
        f'rel="noopener noreferrer">{plain(DASHBOARD_LABEL)}</a></td>'
        "</tr></tbody></table>"
    )
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
    html += day_table_html(headings, rows, plain)
    html += button(url, DASHBOARD_LABEL)
    images = ()
    if section is not None:
        # The section follows the report it compares (#1018 slice 7), with
        # its charts from the chart engine's server renderer.
        html += section_html(section, link)
        images = tuple(InlineImage(data, name) for name, data in render_images(section))
    stream = BytesIO()
    render_participation(chart, stream, format="png", email=True)
    return DailyDigestContent(
        document.title,
        html,
        text,
        InlineImage(stream.getvalue(), CHART_ID),
        images,
        charts_withheld=not charts,
    )
