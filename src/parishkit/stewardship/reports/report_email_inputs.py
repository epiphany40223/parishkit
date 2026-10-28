"""The inputs one report email send records (report-emails spec, "As of", #1786).

When a send records its inputs, its as-of moment, report day and fact
generation's last day follow from the report's values, the slots it covers
and its capture instant. ``recorded_inputs`` derives them; the database's
``stewardship_report_email_inputs_v1`` derives the same and the
preparation's guard refuses any other, so the two are kept identical (and
tested against each other). ``report_days`` lists the days a send reports,
which its day by day table and opening line show.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local

FACT_BLOCKS = frozenset(
    {
        "campaign_totals",
        "participation_charts",
        "day_by_day",
        "compared_with_past_campaigns",
    }
)
DAY = timedelta(days=1)


@dataclass(frozen=True)
class RecordedInputs:
    """What one send reports on.

    ``report_day`` is the day every fact block reports (None before the
    campaign's first day has ended); ``held`` says the report's own day was
    after the campaign's last day, which it is then held at; ``as_of`` is
    the moment every figure is counted at; ``through_date`` the fact
    generation's last day, None without a fact block or a report day.
    """

    report_day: date | None
    held: bool
    as_of: datetime
    through_date: date | None


def _local_date(instant, zone):
    """The campaign-local date of a UTC instant."""
    return instant.astimezone(ZoneInfo(zone)).date()


def _report_day(slot, values):
    """The day a covered slot reports on: its own (report date) or the day before."""
    day = date.fromisoformat(slot)
    return day if values["slot_basis"] == "report_date" else day - DAY


def recorded_inputs(values, covered, captured_at, start, end, zone):
    """Derive a send's inputs from its report, covered slots and capture instant.

    As of the end of the previous day, the report day is the last covered
    slot's day (for a manual send, the day before the capture); as of when
    the email is made, it is the capture's own day. Either is held at the
    campaign's last day ``end`` (its report end, ``send_inputs``) and is
    None before its first. A report as of the end
    of the previous day counts every figure at the end of that day (never
    after the capture); one as of when it is made, at the capture instant.
    """
    if values["as_of"] == "send_time":
        base = _local_date(captured_at, zone)
    elif covered:
        base = _report_day(covered[-1], values)
    else:
        base = _local_date(captured_at, zone) - DAY
    report_day = None if base < start else min(base, end)
    if values["as_of"] == "send_time":
        as_of = captured_at
    else:
        end_of_day = datetime.combine((report_day or base) + DAY, time.min)
        as_of = min(resolve_local(end_of_day, zone), captured_at)
    facts = any(entry["block"] in FACT_BLOCKS for entry in values["blocks"])
    return RecordedInputs(report_day, base > end, as_of, report_day if facts else None)


def send_inputs(values, covered, captured_at, projection, *, campaign_id, watermark):
    """``recorded_inputs`` with the report day held at the report end (#2298).

    ``projection`` is the campaign configuration the send reads (its dates
    and zone). The report end is the later of the end date and the day of
    the newest live response at or under ``watermark``, the send's recorded
    submission watermark (None: every live response, for a Preview), so a
    send after a response Staff entered after the close reports that day,
    and recomputing a sent report's inputs later gives the same day. The
    preparation's guard computes the same end in SQL (frozen migration
    0192).
    """
    from .inputs import newest_response, report_end

    end = report_end(
        projection.end_date,
        newest_response(campaign_id, watermark),
        projection.timezone,
    )
    return recorded_inputs(
        values, covered, captured_at, projection.start_date, end, projection.timezone
    )


def report_days(values, covered, inputs, start):
    """The report days a send covers, oldest first (spec, "Catch-up").

    A recovery send as of the end of the previous day covers every missed
    report day in the campaign (each held at its last day); any other send
    covers its one report day, or none before the campaign's first day.
    """
    if inputs.report_day is None:
        return ()
    if values["as_of"] != "previous_day_end" or len(covered) < 2:
        return (inputs.report_day,)
    days = {
        min(day, inputs.report_day)
        for day in (_report_day(slot, values) for slot in covered)
        if day >= start
    }
    return tuple(sorted(days))
