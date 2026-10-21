"""One report email's compiled source and its subject (report-emails spec, #1786).

A ``ReportDocument`` is what the one compiler (``report_blocks.compiler``)
draws a report email from: everything the report's blocks read, captured
once from the send's pinned inputs (report-emails spec, "Report
snapshot"). It does no database, clock or recipient I/O, as the digest
documents do not.

The blocks were drawn first from the Daily and Weekly digest documents;
this document answers the same attributes, adding what a report email
needs: an opening line for either as-of choice, the empty statement of a
block with nothing to show (``drawable``), and the additional information
block's "N open requests" heading.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.dates import format_date, format_time

from .participation import ParticipationDocument
from .response_metrics import ResponseMetrics
from .weekly_digest import WeeklyInformation

PREVIOUS_DAY_END, SEND_TIME = "previous_day_end", "send_time"


def send_path(send_id):
    """A send's saved copy: the one place the report emails name its address.

    The page itself ships with the Report emails page (spec, "Saved
    copies"); the address is fixed here so the emails compiled before then
    already link it.
    """
    return f"/admin/mail/report-emails/sends/{UUID(str(send_id))}/"


@dataclass(frozen=True, repr=False)
class ReportDocument:
    """A report email's immutable source (report-emails spec, "Compiling a report").

    ``send_id`` names the send's saved copy. ``as_of`` is the report's
    as-of choice and ``as_of_at`` its moment: the end of the report day, or
    the capture instant. ``covered_dates`` are the report days a send as
    of the end of the previous day covers (several for a recovery send;
    none before the campaign's first day has ended) and, for a send as of
    when it is made, the current campaign day. ``participation`` is the
    pinned fact generation's document, None when the report has no fact
    block or no campaign day has ended. ``funnel`` is counted at
    ``as_of_at`` in Production only; ``comparison`` is the Compared with
    past campaigns section, None when it is left out; ``information`` the
    open additional information requests, oldest first.
    """

    send_id: UUID
    name: str
    as_of: str
    as_of_at: datetime
    campaign_timezone: str
    mode: str
    covered_dates: tuple[date, ...] = ()
    participation: ParticipationDocument | None = None
    funnel: ResponseMetrics | None = None
    comparison: dict | None = None
    information: tuple[WeeklyInformation, ...] = ()
    manual: bool = False
    recovery: bool = False
    held: bool = False
    date_format: str | None = None

    def __post_init__(self):
        """Reject an inconsistent source before any block draws from it."""
        if (
            not isinstance(self.send_id, UUID)
            or self.as_of not in {PREVIOUS_DAY_END, SEND_TIME}
            or type(self.as_of_at) is not datetime
            or self.as_of_at.utcoffset() is None
            or self.mode not in {"production", "testing"}
            or (self.funnel is not None and self.mode != "production")
            or type(self.covered_dates) is not tuple
            or tuple(sorted(set(self.covered_dates))) != self.covered_dates
            or type(self.information) is not tuple
            or any(type(row) is not WeeklyInformation for row in self.information)
            or not all(
                type(flag) is bool for flag in (self.manual, self.recovery, self.held)
            )
        ):
            raise ValueError("A report email requires a typed, consistent source.")
        chart = self.participation
        if chart is not None and (
            not isinstance(chart, ParticipationDocument)
            or not chart.days
            or not self.covered_dates
            or self.covered_dates[-1] != chart.last_date
            or self.covered_dates[0] < chart.first_date
        ):
            raise ValueError("A report's facts must end on its last covered day.")
        ZoneInfo(self.campaign_timezone)

    # The attributes the blocks read from the digest documents.
    corrections = ()
    open_requests = True

    @property
    def opens(self):
        """Whether the email opens with its as-of line without a fact block."""
        return self.as_of == PREVIOUS_DAY_END

    @property
    def report_day(self):
        """The report day's end-of-day fact, which every fact block reports."""
        return self.participation.days[-1]

    def drawable(self, block):
        """Whether ``block`` has something to draw, else its empty statement.

        A fact block needs the pinned facts; the additional information
        block needs an open request. The other blocks always draw.
        """
        if block.fact:
            return self.participation is not None or block.key == (
                "compared_with_past_campaigns"
            )
        if block.key == "additional_information":
            return bool(self.information)
        return True

    def _local(self):
        """The as-of moment in the campaign's zone."""
        return self.as_of_at.astimezone(ZoneInfo(self.campaign_timezone))

    @property
    def opening(self):
        """The opening line (spec, "Shared email layout").

        "Here is yesterday’s report; all data is as of the end of October 6,
        2026 (EDT)." for one report day, "Here is the report for … through
        …; …" for a recovery range, "Here is the report; all data is as of
        7:00 AM, October 7, 2026 (EDT)." as of when it is made, and "Here
        is the report; …" for a report day held at the campaign's last day.
        The zone is the one in force at the as-of moment.
        """
        local = self._local()
        if self.as_of == SEND_TIME:
            when = f"{format_time(local)}, {format_date(local.date())}"
            return f"Here is the report; all data is as of {when} ({local.tzname()})."
        last = self.last_day
        zone = ZoneInfo(self.campaign_timezone)
        abbreviation = datetime.combine(last, time.max, zone).tzname()
        if len(self.covered_dates) > 1:
            report = (
                f"Here is the report for {format_date(self.covered_dates[0])} "
                f"through {format_date(last)}"
            )
        else:
            report = "Here is the report" if self.held else "Here is yesterday’s report"
        return (
            f"{report}; all data is as of the end of {format_date(last)} "
            f"({abbreviation})."
        )

    @property
    def last_day(self):
        """The last report day: the last covered date, or the as-of moment's day."""
        if self.covered_dates:
            return self.covered_dates[-1]
        # No campaign day has ended: the report day is still the day before
        # the send, which is the day the as-of moment ends.
        return self._local().date() if self.as_of == SEND_TIME else self._previous()

    def _previous(self):
        """The calendar day that ends at the as-of moment (its local midnight)."""
        local = self._local()
        return date.fromordinal(local.date().toordinal() - 1)

    @property
    def when(self):
        """The report's date for its subject (spec, "Subject")."""
        if self.as_of == SEND_TIME:
            return format_date(self._local().date())
        if len(self.covered_dates) > 1:
            return (
                f"{format_date(self.covered_dates[0])} through "
                f"{format_date(self.covered_dates[-1])}"
            )
        return format_date(self.last_day)

    @property
    def title(self):
        """The report's name and date, which ``report_subject`` completes."""
        return f"{self.name} — {self.when}"

    @property
    def report_path(self):
        """The send's saved copy, which the report links."""
        return send_path(self.send_id)


def report_subject(authored, document, *, campaign, parish, test=False):
    """The subject: the report, its date, the campaign and parish, each once.

    The report-emails spec's "Subject" rule, which today's digest subjects
    follow (``jobs.digest_content.identified_subject``) with the report's
    name in place of "daily report" or "weekly report": the Administrator's
    subject (placeholders already filled) is kept, and only what it lacks
    is appended. The name is appended starting in lower case unless the
    subject already names a report, digest or summary; when the subject has
    the name's first word, only the rest of the name is. An empty subject
    becomes the name as typed. A subject too long for the limit loses the
    end of the Administrator's text, never what is appended.
    """
    from parishkit.stewardship.jobs.digest_content import (
        MAX_SUBJECT,
        REPORT_WORDS,
        _has_word,
    )

    base = " ".join(authored.split())
    name = " ".join(document.name.split())
    if not REPORT_WORDS.search(base):
        lowered = name[:1].lower() + name[1:]
        first, _, rest = lowered.partition(" ")
        label = rest if rest and _has_word(base, first) else lowered
        base = f"{base} — {label}" if base else name
    when = document.when
    suffix = f", {when}" if when.casefold() not in base.casefold() else ""
    notes = [
        word
        for word, flag in (
            ("manual", document.manual),
            ("recovery", document.recovery),
            ("test", test),
        )
        if flag and not _has_word(base, word)
    ]
    notes += [
        value
        for value in (campaign, parish)
        if value and value.casefold() not in (base + suffix).casefold()
    ]
    if notes:
        suffix += f" ({', '.join(notes)})"
    room = MAX_SUBJECT - len(suffix)
    if len(base) > room:
        base = base[: max(room - 1, 0)].rstrip() + "…"
    subject = base + suffix
    return subject if len(subject) <= MAX_SUBJECT else subject[: MAX_SUBJECT - 1] + "…"
