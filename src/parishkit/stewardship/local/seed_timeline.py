"""The seeded campaign's timeline: calendar, response shape and intended events.

The local seeder (#476) builds a campaign in progress by time travel: it moves
the LOCAL fake clock forward through a timeline of intended events while the
real scheduler, worker, mail-dispatch and web code do the work. This module
produces that timeline, with no database and no Docker, so CI can test the
calendar rules, the response shape and determinism at any parish size (see
"Seeded campaign and responses" in
docs/specs/stewardship/local-environment/spec.md).

Given the seed, the parish size, ``--response-scale`` and ``now``, plus the
ordered Portal-eligible Families, it decides:

- the campaign calendar: the start Saturday 8 to 14 days before now, the
  Initial at 10:00 that day, the Monday end 30 days after the start, and the
  Tuesday and Thursday 09:00 Reminders;
- how many Families submit on each elapsed day, from the specified rates by
  deterministic largest-remainder rounding with the Reminder and today floors
  applied inside the allocation, so the cumulative shares hold exactly;
- which Families follow their link, open the form, progress and submit (the
  ordered funnel), at what instants, and with what answers;
- the edge cases: re-submissions, the early responder whose Saturday
  submission precedes its invitation, and a Family with no eligible email.

Every instant is an intended instant. When the seeder runs an event, the
recorded instant may trail it by the processing time; order is preserved.
"""

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from parishkit.config import ConfigError

# Response rates as percentages of the Portal-eligible Families ``E``.
OPENING_RATES = {0: 2.0, 1: 3.0, 2: 1.5}
TAPER_START_RATE, TAPER_END_RATE = 1.0, 0.4
TAPER_START_DAY, TAPER_END_DAY = 3, 13
REMINDER_DAY_FACTOR, DAY_AFTER_REMINDER_FACTOR = 1.6, 1.2
MAXIMUM_RESPONDING_SHARE = 0.9
# The funnel's default ratios to submitted Families, each capped at ``E``.
LINK_RATIO, OPENED_RATIO, PROGRESSED_RATIO = 1.7, 1.4, 1.2
RESUBMISSION_SHARE = 0.03
# Campaign-local times. Reminders go out at 09:00 and the Initial at 10:00;
# activity falls between 07:00 and 22:00 with a Sunday after-Mass bump.
INITIAL_TIME = time(10, 0)
REMINDER_TIME = time(9, 0)
PREPARE_TIME = time(9, 0)
DAY_START, DAY_END = time(7, 0), time(22, 0)
MASS_BUMP = (time(10, 30), time(13, 0))
MASS_BUMP_SHARE = 0.35
SATURDAY, SUNDAY, MONDAY, TUESDAY, THURSDAY = 5, 6, 0, 1, 3
# The Family form's sections, in order, as presence reports them: the same
# names as campaigns.credential_models.PRESENCE_SECTIONS (a test pins this;
# this module stays importable without Django).
SECTIONS = (
    "welcome",
    "census",
    "members",
    "ministry",
    "financial",
    "closing",
    "additional",
    "review",
)
# Occurrence-instant kinds are driven by jumping the clock and waiting for the
# real services; Family kinds are driven by calling the real web entry points.
OCCURRENCE_KINDS = ("boundary_start", "initial", "reminder", "midnight")
FAMILY_KINDS = ("session", "baseline", "presence", "submission")
_KIND_ORDER = {
    kind: index for index, kind in enumerate(OCCURRENCE_KINDS + FAMILY_KINDS)
}
# Answer content drawn for submissions: realistic pledge amounts (the
# synthetic parish's range), Ministry interest, census edits, opt-outs and
# free text.
PLEDGE_AMOUNTS = (250, 500, 600, 1000, 1200, 1500, 2400, 3000, 5000)
NO_PLEDGE_SHARE = 0.3
MINISTRY_INTEREST_SHARE = 0.4
CENSUS_EDIT_SHARE = 0.2
OPT_OUT_SHARE = 0.03
INFORMATION_SHARE = 0.15
INFORMATION_TEXTS = (
    "We will be travelling for part of the campaign.",
    "Please update our mailing address; we moved last spring.",
    "Our daughter would like to help with the youth group.",
    "We prefer to be contacted by email rather than phone.",
    "Thank you for the parish's work this year.",
)


@dataclass(frozen=True)
class Event:
    """One intended event: an instant (UTC), a kind, and its Family and data."""

    at: datetime
    kind: str
    family: object = None
    data: dict = field(default_factory=dict)

    @property
    def is_occurrence(self):
        """Occurrence instants are driven by the clock, not by a web call."""
        return self.kind in OCCURRENCE_KINDS


@dataclass(frozen=True)
class Calendar:
    """The campaign calendar in the campaign timezone."""

    timezone: str
    now: datetime
    start: date
    end: date
    initial_at: datetime
    reminders: tuple
    prepare_at: datetime

    @property
    def today(self):
        """Now's campaign-local date."""
        return self.now.astimezone(ZoneInfo(self.timezone)).date()

    @property
    def elapsed_days(self):
        """Complete campaign-local days elapsed before today (8 to 14)."""
        return (self.today - self.start).days

    def local(self, day, at):
        """A campaign-local wall time as an aware instant."""
        return datetime.combine(day, at, tzinfo=ZoneInfo(self.timezone))

    def past_reminders(self):
        """Reminders that have fallen due before now."""
        return tuple(instant for instant in self.reminders if instant < self.now)


def calendar(now, timezone):
    """The campaign calendar for ``now`` (see "Campaign calendar").

    The start is the most recent Saturday at least 8 days before now's local
    date; the end is the Monday 30 days later; Reminders fall at 09:00 every
    Tuesday and Thursday from the first Tuesday after the start through the
    last Thursday before the end; phase 1 runs at 09:00 on the Friday before
    the start, which is always later than now minus 17 days.
    """
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ConfigError("The timeline needs an aware instant for now.")
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    start = today - timedelta(days=8)
    while start.weekday() != SATURDAY:
        start -= timedelta(days=1)
    end = start + timedelta(days=30)
    reminders = tuple(
        datetime.combine(start + timedelta(days=offset), REMINDER_TIME, tzinfo=zone)
        for offset in range(1, 30)
        if (start + timedelta(days=offset)).weekday() in {TUESDAY, THURSDAY}
    )
    return Calendar(
        timezone=timezone,
        now=now.astimezone(UTC),
        start=start,
        end=end,
        initial_at=datetime.combine(start, INITIAL_TIME, tzinfo=zone),
        reminders=reminders,
        prepare_at=datetime.combine(
            start - timedelta(days=1), PREPARE_TIME, tzinfo=zone
        ),
    )


def baseline_rate(day):
    """The unscaled submission rate (percent of ``E``) for elapsed day ``day``."""
    if day in OPENING_RATES:
        return OPENING_RATES[day]
    if day >= TAPER_END_DAY:
        return TAPER_END_RATE
    span = TAPER_END_DAY - TAPER_START_DAY
    return TAPER_START_RATE - (TAPER_START_RATE - TAPER_END_RATE) * (
        (day - TAPER_START_DAY) / span
    )


def reminder_factor(cal, day):
    """1.6 on a Reminder day, 1.2 the day after, 1 otherwise.

    The specification's cumulative table counts each Reminder's 24-hour
    windows by calendar day, which is what reproduces its figures exactly.
    """
    reminder_days = {instant.date() for instant in cal.reminders}
    day_date = cal.start + timedelta(days=day)
    if day_date in reminder_days:
        return REMINDER_DAY_FACTOR
    if day_date - timedelta(days=1) in reminder_days:
        return DAY_AFTER_REMINDER_FACTOR
    return 1.0


def daily_rate(cal, day):
    """The unscaled rate for a day with its Reminder uptick applied."""
    return baseline_rate(day) * reminder_factor(cal, day)


def cumulative_share(cal, days):
    """The unscaled share of ``E`` submitted before day ``days`` (a fraction)."""
    return sum(daily_rate(cal, day) for day in range(days)) / 100


def effective_scale(cal, response_scale):
    """``--response-scale`` capped so no more than 90% of ``E`` respond by now."""
    if not isinstance(response_scale, (int, float)) or not response_scale > 0:
        raise ConfigError("The response scale must be a positive number.")
    total = cumulative_share(cal, cal.elapsed_days + 1)
    return min(float(response_scale), MAXIMUM_RESPONDING_SHARE / total)


def _round(value):
    """Round half up: the specification says "nearest integer", not banker's."""
    return int(math.floor(value + 0.5))


def largest_remainder(total, weights):
    """Share ``total`` across ``weights`` by largest remainder (deterministic)."""
    if total <= 0 or not weights or sum(weights) <= 0:
        return [0] * len(weights)
    scale = total / sum(weights)
    exact = [weight * scale for weight in weights]
    counts = [math.floor(value) for value in exact]
    remainder = total - sum(counts)
    # Ties break toward the earlier day, so the result is reproducible.
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - counts[i]), i))
    for index in order[:remainder]:
        counts[index] += 1
    return counts


def reminder_floor(cal, day, eligible, scale):
    """The submission floor for a complete 24-hour window after a past Reminder.

    At least one submission, and at least the (scaled) taper baseline rounded
    up, so the uptick is visible even where rounding would hide it. Zero when
    the day holds no Reminder or its window is not yet complete.
    """
    instant = cal.local(cal.start + timedelta(days=day), REMINDER_TIME)
    if instant.date() not in {r.date() for r in cal.reminders}:
        return 0
    if instant + timedelta(hours=24) > cal.now:
        return 0
    return max(1, math.ceil(baseline_rate(day) * scale * eligible / 100))


def allocate(cal, eligible, scale):
    """Submissions per elapsed day before today, floors applied inside.

    The total is the cumulative share times ``E``, rounded; the days then
    share it by largest remainder on their rates, and each floor that is not
    met takes a submission from the nearest later day without a floor (or,
    if none, the nearest earlier one), so the total is unchanged.
    """
    days = cal.elapsed_days
    total = _round(cumulative_share(cal, days) * scale * eligible)
    counts = largest_remainder(total, [daily_rate(cal, day) for day in range(days)])
    floors = [reminder_floor(cal, day, eligible, scale) for day in range(days)]
    if total:
        # The early responder (a start-Saturday submission before the Initial
        # is prepared) is an edge case every seed carries, so day 0 gets at
        # least one submission whenever there is any to give.
        floors[0] = max(floors[0], 1)
    for day in range(days):
        while counts[day] < floors[day]:
            donors = [
                other
                for other in list(range(day + 1, days)) + list(range(day - 1, -1, -1))
                if floors[other] == 0 and counts[other] > 0
            ]
            if not donors:
                break
            counts[donors[0]] -= 1
            counts[day] += 1
    return counts


def today_count(cal, eligible, scale):
    """Submissions today before now: today's rate scaled by the day elapsed."""
    zone = ZoneInfo(cal.timezone)
    now = cal.now.astimezone(zone)
    opening = cal.local(cal.today, DAY_START)
    closing = cal.local(cal.today, DAY_END)
    elapsed = (min(now, closing) - opening) / (closing - opening)
    fraction = min(1.0, max(0.0, elapsed))
    return _round(daily_rate(cal, cal.elapsed_days) * scale * eligible / 100 * fraction)


class _Draw:
    """Deterministic draws for one timeline, seeded by every input."""

    def __init__(self, seed, families, response_scale, cal):
        """One generator for the whole timeline; every input is in its seed."""
        self.random = random.Random(
            f"{seed}:{families}:{response_scale}:{cal.now.isoformat()}:{cal.timezone}"
        )
        self.cal = cal

    def time_of_day(self, day, *, earliest=None):
        """A campaign-local wall time: 07:00 to 22:00, Sunday after-Mass bump.

        ``earliest`` bounds the draw from below (after a Reminder, or the
        campaign's Initial on the start Saturday). Today's draws stay before
        now; the caller clamps that.
        """
        start = max(DAY_START, earliest) if earliest else DAY_START
        if day.weekday() == SUNDAY and self.random.random() < MASS_BUMP_SHARE:
            low, high = max(start, MASS_BUMP[0]), MASS_BUMP[1]
            if low < high:
                return self._between(low, high)
        return self._between(start, DAY_END)

    def _between(self, low, high):
        """A wall time uniformly between two wall times (minute resolution)."""
        low_minutes = low.hour * 60 + low.minute
        high_minutes = high.hour * 60 + high.minute
        minutes = self.random.randint(low_minutes, max(low_minutes, high_minutes - 1))
        return time(minutes // 60, minutes % 60, self.random.randint(0, 59))

    def answers(self, ministries):
        """Varied submission content; the seeder maps it onto the real form."""
        pledge = (
            None
            if self.random.random() < NO_PLEDGE_SHARE
            else self.random.choice(PLEDGE_AMOUNTS)
        )
        interest = []
        if ministries and self.random.random() < MINISTRY_INTEREST_SHARE:
            interest = sorted(
                self.random.sample(list(ministries), k=min(2, len(ministries)))
            )
        census = None
        if self.random.random() < CENSUS_EDIT_SHARE:
            census = self.random.choice(("changed_email", "proposed_member"))
        return {
            "pledge": pledge,
            "ministry_interest": interest,
            "census_edit": census,
            "email_opt_out": self.random.random() < OPT_OUT_SHARE,
            "information": self.random.choice(INFORMATION_TEXTS)
            if self.random.random() < INFORMATION_SHARE
            else "",
        }


@dataclass(frozen=True)
class Timeline:
    """The whole intended timeline and the counts it implies."""

    calendar: Calendar
    seed: int
    families: int
    response_scale: float
    eligible: tuple
    events: tuple
    daily_submissions: tuple
    today_submissions: int
    stages: dict
    early_responder: object
    no_email_family: object

    @property
    def submitted(self):
        """Families with at least one submission."""
        return self.stages["submitted"]

    def counts(self):
        """Per-kind event counts, the invariant check's expected row counts."""
        result = {}
        for event in self.events:
            result[event.kind] = result.get(event.kind, 0) + 1
        return result

    def family_events(self):
        """The events the seeder drives through the web, in order."""
        return tuple(event for event in self.events if not event.is_occurrence)

    def occurrences(self):
        """The occurrence instants the seeder jumps to, in order."""
        return tuple(event for event in self.events if event.is_occurrence)


def build(
    seed,
    families,
    now,
    eligible,
    *,
    response_scale=1,
    timezone="America/New_York",
    ministries=(),
    no_email_family=None,
):
    """Build the timeline for one seed, parish size, scale and now.

    ``eligible`` is the ordered sequence of Portal-eligible Family keys (the
    seeder passes Family identities ordered by DUID; tests pass any stable
    keys). ``ministries`` are the keys a submission may express interest in.
    ``no_email_family`` names the Portal-eligible Family with no eligible
    email, which never responds (it has no link to follow).
    """
    if type(seed) is not int or isinstance(seed, bool) or not 0 <= seed < 2**63:
        raise ConfigError("The timeline seed must be an integer.")
    if type(families) is not int or families < 1:
        raise ConfigError("The timeline needs the parish's Family count.")
    eligible = tuple(key for key in eligible if key != no_email_family)
    if len(set(eligible)) != len(eligible):
        raise ConfigError("Eligible Family keys must be unique.")
    cal = calendar(now, timezone)
    scale = effective_scale(cal, response_scale)
    count = len(eligible)
    daily = allocate(cal, count, scale)
    today = today_count(cal, count, scale)
    draw = _Draw(seed, families, response_scale, cal)
    submitted_total = min(count, sum(daily) + today)
    # The funnel: who follows the link, opens the form, progresses, submits.
    order = list(eligible)
    draw.random.shuffle(order)
    linked = min(count, max(submitted_total, _round(LINK_RATIO * submitted_total)))
    opened = min(linked, max(submitted_total, _round(OPENED_RATIO * submitted_total)))
    progressed = min(
        opened, max(submitted_total, _round(PROGRESSED_RATIO * submitted_total))
    )
    stages = {
        "submitted": tuple(order[:submitted_total]),
        "progressed": tuple(order[submitted_total:progressed]),
        "opened": tuple(order[progressed:opened]),
        "linked": tuple(order[opened:linked]),
    }
    events = list(_occurrence_events(cal))
    events += _ensure_variety(
        _family_events(cal, draw, daily, today, stages, ministries), draw, ministries
    )
    # Today's floor: at least one session or submission today before now. A
    # link-only Family's session moves to today, so the funnel counts hold;
    # with none to move, a Family outside the funnel follows its link today.
    if not any(
        not e.is_occurrence and e.at.astimezone(ZoneInfo(timezone)).date() == cal.today
        for e in events
    ):
        if stages["linked"]:
            family = stages["linked"][-1]
            events = [e for e in events if e.family != family]
        else:
            family = (list(order[linked:]) or list(order))[0]
        events += _session_only(cal, draw, family, cal.today, is_today=True)
    # The early responder is the first day-0 submitter; a parish too small
    # for any submission before today has none.
    early = stages["submitted"][0] if daily and daily[0] else None
    events.sort(key=lambda event: (event.at, _KIND_ORDER[event.kind]))
    return Timeline(
        calendar=cal,
        seed=seed,
        families=families,
        response_scale=scale,
        eligible=eligible,
        events=tuple(events),
        daily_submissions=tuple(daily),
        today_submissions=today,
        stages=stages,
        early_responder=early,
        no_email_family=no_email_family,
    )


def _ensure_variety(events, draw, ministries):
    """Guarantee each answer kind appears at least once among the submissions.

    The draws are proportional, so a small parish could by chance submit no
    Ministry interest, census edit (of either kind: a changed email or a
    proposed new Member, #498) or free text at all; report testing wants at
    least one of each. Distinct early submissions are given the missing
    kinds, which changes no count and keeps the draw deterministic. A census
    edit is never given to a submission that already holds the other kind,
    so supplying one cannot remove the other.
    """
    submissions = [index for index, e in enumerate(events) if e.kind == "submission"]
    wanted = (
        ("ministry_interest", sorted(list(ministries)[:1])),
        ("census_edit", "changed_email"),
        # Free text before the proposed Member: a tiny parish with too few
        # submissions for every kind keeps the original three kinds first.
        ("information", INFORMATION_TEXTS[0]),
        ("census_edit", "proposed_member"),
    )
    taken = set()
    for key, value in wanted:
        if key == "ministry_interest" and not ministries:
            continue
        held = [events[i].data["answers"][key] for i in submissions]
        if (value in held) if key == "census_edit" else any(held):
            continue
        free = [
            i
            for i in submissions
            if i not in taken
            and not (key == "census_edit" and events[i].data["answers"][key])
        ]
        if not free:
            continue
        index = free[0]
        taken.add(index)
        event = events[index]
        answers = {**event.data["answers"], key: value}
        events[index] = Event(
            event.at, event.kind, event.family, {**event.data, "answers": answers}
        )
    return events


def _occurrence_events(cal):
    """The clock-driven instants before now: boundary, Initial, Reminders, midnights."""
    zone = ZoneInfo(cal.timezone)
    yield Event(cal.local(cal.start, time(0, 0)).astimezone(UTC), "boundary_start")
    yield Event(cal.initial_at.astimezone(UTC), "initial")
    for instant in cal.past_reminders():
        yield Event(instant.astimezone(UTC), "reminder")
    for day in range(1, cal.elapsed_days + 1):
        midnight = datetime.combine(cal.start + timedelta(days=day), time(0, 0), zone)
        if midnight < cal.now:
            yield Event(midnight.astimezone(UTC), "midnight")


def _family_events(cal, draw, daily, today, stages, ministries):
    """Every Family's session, form, presence and submission events."""
    zone = ZoneInfo(cal.timezone)
    days = list(range(cal.elapsed_days + 1))
    counts = list(daily) + [today]
    events = []
    submitters = list(stages["submitted"])
    index = 0
    for day, count in zip(days, counts, strict=True):
        day_date = cal.start + timedelta(days=day)
        for position in range(count):
            if index >= len(submitters):
                break
            family = submitters[index]
            index += 1
            if day == 0 and position == 0:
                # The early responder: submits before the 10:00 Initial, so
                # its invitation is skipped once prepared.
                instant = cal.local(day_date, time(9, 15, draw.random.randint(0, 59)))
            else:
                instant = _activity_instant(cal, draw, day_date, zone)
            events += _submission_events(cal, draw, family, instant, ministries)
    # Re-submissions: 3% of submitting Families (at least one), a later version
    # one to five days after the first, still before now.
    resubmitting = max(1, _round(RESUBMISSION_SHARE * len(submitters)))
    firsts = {e.family: e.at for e in events if e.kind == "submission"}
    for family in submitters[:resubmitting]:
        first = firsts[family]
        for later in range(1, 6):
            day_date = (first.astimezone(zone) + timedelta(days=later)).date()
            if day_date > cal.today:
                break
            instant = _activity_instant(cal, draw, day_date, zone)
            if instant > first + timedelta(hours=12):
                events += _submission_events(
                    cal, draw, family, instant, ministries, version=2
                )
                break
    # Non-submitting stages follow the submission days' distribution.
    weights = [max(count, 0) for count in counts]
    if sum(weights) == 0:
        weights = [1] * len(counts)
    for stage, builder in (
        ("progressed", _progressed_only),
        ("opened", _opened_only),
        ("linked", _session_only),
    ):
        for family in stages[stage]:
            day = draw.random.choices(days, weights=weights, k=1)[0]
            day_date = cal.start + timedelta(days=day)
            events += builder(
                cal, draw, family, day_date, is_today=day_date == cal.today
            )
    return events


def _activity_instant(cal, draw, day_date, zone):
    """A Family activity instant on a day, after the day's mail, before now."""
    earliest = None
    if day_date == cal.start:
        earliest = (cal.initial_at + timedelta(minutes=5)).time()
    elif day_date in {r.date() for r in cal.reminders}:
        earliest = (
            datetime.combine(day_date, REMINDER_TIME) + timedelta(minutes=5)
        ).time()
    instant = datetime.combine(
        day_date, draw.time_of_day(day_date, earliest=earliest), zone
    )
    if day_date == cal.today:
        instant = _clamp_today(cal, draw, instant, margin=timedelta(minutes=2))
    return instant.astimezone(UTC)


def _clamp_today(cal, draw, instant, *, margin):
    """Keep one of today's instants on today's local date and before now.

    A drawn wall time later than now moves to shortly before now; ``margin``
    leaves room for the events that follow it (a form's presence steps). Early
    in the local day the margin shrinks so the instant stays after midnight.
    """
    zone = ZoneInfo(cal.timezone)
    midnight = cal.local(cal.today, time(0, 0))
    now = cal.now.astimezone(zone)
    margin = min(margin, (now - midnight) / 2)
    latest = now - margin
    if instant > latest or instant < midnight:
        room = int((latest - midnight).total_seconds() // 60)
        instant = latest - timedelta(minutes=draw.random.randint(0, min(30, room)))
    return instant


def _submission_events(cal, draw, family, submitted_at, ministries, *, version=1):
    """Session, baseline, presence through every section, then the submission."""
    session_at = submitted_at - timedelta(minutes=draw.random.randint(6, 40))
    baseline_at = session_at + timedelta(seconds=draw.random.randint(20, 90))
    events = [
        Event(session_at, "session", family, {"version": version}),
        Event(baseline_at, "baseline", family, {"version": version}),
    ]
    # Presence steps follow the baseline seconds apart: close enough that the
    # seeder runs them without a clock jump each (seeder.JUMP_THRESHOLD), so
    # a Family's form costs two jumps (session, submission), not nine.
    at = baseline_at
    for section in SECTIONS:
        at += timedelta(seconds=draw.random.randint(5, 20))
        events.append(Event(at, "presence", family, {"section": section}))
    events.append(
        Event(
            submitted_at,
            "submission",
            family,
            {"version": version, "answers": draw.answers(ministries)},
        )
    )
    return events


def _bounded_instant(cal, draw, day_date, *, is_today):
    """A session instant on a day, before now when the day is today."""
    zone = ZoneInfo(cal.timezone)
    instant = datetime.combine(day_date, draw.time_of_day(day_date), zone)
    if is_today:
        # Room for the form steps that follow a session (under two minutes).
        instant = _clamp_today(cal, draw, instant, margin=timedelta(minutes=3))
    return instant.astimezone(UTC)


def _session_only(cal, draw, family, day_date, *, is_today=False):
    """A link followed and nothing more (a prefetch-like session)."""
    return [
        Event(
            _bounded_instant(cal, draw, day_date, is_today=is_today), "session", family
        )
    ]


def _opened_only(cal, draw, family, day_date, *, is_today=False):
    """A link followed and the form opened at the welcome section."""
    at = _bounded_instant(cal, draw, day_date, is_today=is_today)
    return [
        Event(at, "session", family),
        Event(at + timedelta(seconds=30), "baseline", family),
        Event(at + timedelta(seconds=45), "presence", family, {"section": "welcome"}),
    ]


def _progressed_only(cal, draw, family, day_date, *, is_today=False):
    """A Family that stopped partway through the form."""
    at = _bounded_instant(cal, draw, day_date, is_today=is_today)
    stop = draw.random.randint(2, len(SECTIONS) - 1)
    events = [
        Event(at, "session", family),
        Event(at + timedelta(seconds=30), "baseline", family),
    ]
    for step, section in enumerate(SECTIONS[:stop], start=1):
        events.append(
            Event(
                at + timedelta(seconds=30 + 15 * step),
                "presence",
                family,
                {"section": section},
            )
        )
    return events
