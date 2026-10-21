"""Accessible daily mail/report content over one retained report observation.

This module does no database, clock, recipient or provider I/O. Its durable
owner must authorize access and pin both inputs before calling it. Compiled
chart markup is deliberately separate from parish-authored safe HTML: adding
an inline report image must not enable arbitrary images in Family templates.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from parishkit.email.base import InlineImage
from parishkit.stewardship.source.data_age import DataAge
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.digest_content import CHART_ID

from .digest_comparison import withheld
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


# The Daily digest's blocks, in its order (report-emails spec, "Block
# registry"); Compared with past campaigns draws only when the schedule has
# it on and a past campaign is imported.
DAILY_BLOCKS = (
    "campaign_totals",
    "response_funnel",
    "participation_charts",
    "day_by_day",
    "compared_with_past_campaigns",
)


def render_daily_digest(document, *, public_origin, charts=True):
    """Compile an accessible image/text report using the existing chart renderer.

    Every data-dependent HTML value is escaped; the only image and link markup
    comes from the report compiler and its blocks. The source document is reused without
    recapturing current state. A later owner may prepend sanitized
    parish-authored prose and the mandatory Testing banner, but cannot
    replace these required facts.

    Visuals come first (#720): the opening line with a Response dashboard
    link beside it (#1532), then the campaign totals with their bars, the
    funnel, the chart, the day-by-day table and a Response dashboard button,
    then any comparison section. The subject names the parish, campaign and
    report day (see render_digest_envelope), so the body has no header or
    small print.

    ``charts`` False withholds every server-drawn chart (#477): through
    ``digest_comparison.withheld``, the comparison section's. The
    participation chart, drawn in process, always stays.
    """
    # The blocks read this module's labels and document, so the compiler is
    # imported here rather than at the top, where it would be circular.
    from .report_blocks.compiler import compile_report

    if not isinstance(document, DailyDigestDocument):
        raise TypeError("Daily digest rendering requires an immutable document.")
    if not charts:
        document = replace(document, comparison=withheld(document.comparison))
    # The participation chart is now drawn before the comparison charts (the
    # old compiler drew it last). If both fail, its error surfaces instead of
    # the comparison's ChartRenderError; the outcome is the same, since a
    # failing participation chart fails the withheld retry too.
    report = compile_report(document, DAILY_BLOCKS, public_origin=public_origin)
    (chart,) = (image for image in report.images if image.content_id == CHART_ID)
    return DailyDigestContent(
        report.subject,
        report.html,
        report.text,
        chart,
        tuple(image for image in report.images if image.content_id != CHART_ID),
        charts_withheld=not charts,
    )
