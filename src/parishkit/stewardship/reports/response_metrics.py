"""Response funnel metrics for one campaign and mode (#477).

The funnel counts distinct Families at an explicit ``as_of`` cutoff from
durable timestamps only, so the same inputs always give the same numbers
later (the daily digest's parity rule): a delivered invitation's
``finished_at``, the first instants of the durable Family engagement record
(``campaigns/engagement.py``), a submission's ``submitted_at`` and the
immutable occurrence transition that skipped a planned invitation because the
Family had already responded. Nothing here reads Family session rows, which
are deleted 60 minutes after the last activity or four hours after sign-in
(``docs/specs/stewardship/data/spec.md#family-engagement``), or any value that
a later event can change, such as the furthest form step reached or a Family's
current eligibility; those belong to live views, not to a reproducible report.
Mail evidence is bounded by ``as_of`` alone, across every Production cycle of
the campaign, so a withdrawal and re-activation after the cutoff cannot change
an earlier reading.

One statement returns one row per Family of the campaign with each instant as
it stood at ``as_of`` (``FamilyResponse``); the stage totals, the separate
figures ("skipped: already responded", "submitted without a delivered
invitation" and "submitted more than once") and the activity series are pure
functions over those rows, so they are unit tested without a database and a
later page can list the Families behind any count. A submission implies
that the Family had opened the form and got past its first step, and progress
implies the form was open, so those two stages count the earliest instant that
implies them (``FamilyResponse.form_opened_at`` and ``progressed_at``). Each source is
aggregated once per campaign, never per Family. The series buckets the first instants by
campaign-local hour or day, using the campaign's immutable timezone snapshot
like the participation graph; ``activity_series(metrics.families,
ZoneInfo(metrics.timezone), "day")`` re-buckets a result at the other grain
without a second read. The send markers name each invitation and reminder
send with its scheduled time and what it had delivered by ``as_of``.

Mode is the system's: ``production`` (the default) reads live responses and
Production mail; ``testing`` reads one rehearsal epoch: its responses and
engagement, its messages, and the occurrences and skips that fell within the
epoch's lifetime (occurrences carry no epoch). The statements read only
tables and columns the restricted web login reads (``runtime_grants``) and
take no lock: callers run them in ``read_transaction`` after admitting the
campaign report as the other reports do.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import connection

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.send_history import (
    ListedSend,
    SendKey,
    reminder_numbers,
)

# Funnel order. ``skipped_responded``, ``submitted_uninvited`` and
# ``submitted_again`` are reported beside the funnel, not as stages of it.
STAGES = ("invited", "link_followed", "form_opened", "progressed", "submitted")
# A mail scanner that follows a personal link signs the Family in exactly as
# the Family would, so "link followed" cannot tell the two apart; every
# rendering of that stage says so.
LINK_FOLLOWED_NOTE = "Includes mail-scanner prefetches"
GRAINS = ("hour", "day")
# System mode -> (how responses and engagement spell it, how mail spells it).
MODES = {"production": ("live", "production"), "testing": ("test", "testing")}

# The instants a scope covers. Occurrences and their transitions carry no
# rehearsal epoch, so a Testing scope admits only those within its epoch's
# lifetime (from the epoch's creation until it was invalidated); a Production
# scope admits every instant, and an unknown epoch none.
_LIFETIME = """
lifetime AS (
    SELECT e.created_at AS started_at,
        coalesce(e.invalidated_at, 'infinity'::timestamptz) AS ended_at
    FROM stewardship_rehearsal_epoch e WHERE e.id=%(epoch)s
    UNION ALL
    SELECT '-infinity'::timestamptz, 'infinity'::timestamptz
    WHERE %(epoch)s::uuid IS NULL
)
"""
# One row per Family of the campaign, with each instant as it stood at
# as_of: the first delivered invitation, whether a planned invitation was
# skipped because the Family had already responded, the engagement record's
# first instants, and the first submission with how many there were. Each
# source is aggregated once per campaign and joined to the Family rows (hash
# joins at launch scale, about 1,100 Families), never per Family.
_FAMILIES = (
    "WITH "
    + _LIFETIME
    + """, invited AS (
    SELECT m.family_id, min(m.finished_at) AS at
    FROM stewardship_outbox_message m
    WHERE m.campaign_id=%(campaign)s AND m.purpose='initial'
      AND m.mode=%(mail_mode)s
      AND m.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
      AND m.state='delivered' AND m.finished_at<=%(as_of)s
    GROUP BY m.family_id
), skipped AS (
    SELECT DISTINCT o.target
    FROM stewardship_schedule_definition d
    JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id
    JOIN stewardship_occurrence_transition t ON t.occurrence_id=o.id
    JOIN lifetime l ON t.created_at>=l.started_at AND t.created_at<l.ended_at
    WHERE d.campaign_id=%(campaign)s AND d.kind='initial'
      AND o.mode=%(mail_mode)s
      AND t.after_state='skipped' AND t.reason='family_responded'
      AND t.created_at<=%(as_of)s
), responded AS (
    SELECT s.family_id, min(s.submitted_at) AS at, count(*) AS submissions
    FROM stewardship_submission s
    WHERE s.campaign_id=%(campaign)s AND s.mode=%(response_mode)s
      AND s.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
      AND s.submitted_at<=%(as_of)s
    GROUP BY s.family_id
)
SELECT f.id, f.family_duid, i.at, k.target IS NOT NULL,
    CASE WHEN e.first_link_at<=%(as_of)s THEN e.first_link_at END,
    CASE WHEN e.first_form_at<=%(as_of)s THEN e.first_form_at END,
    CASE WHEN e.first_progress_at<=%(as_of)s THEN e.first_progress_at END,
    r.at, coalesce(r.submissions, 0)
FROM stewardship_family_campaign f
LEFT JOIN invited i ON i.family_id=f.id
LEFT JOIN skipped k ON k.target='family:'||f.id::text
LEFT JOIN stewardship_family_engagement e ON e.family_id=f.id
    AND e.mode=%(response_mode)s
    AND e.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
LEFT JOIN responded r ON r.family_id=f.id
WHERE f.campaign_id=%(campaign)s
ORDER BY f.family_duid, f.id
"""
)
# Every invitation and reminder send of the mode that had planned an email by
# as_of (a *send* is one revision of one Family schedule in one mode and
# Production cycle, as the Family email progress and sends pages count it),
# with its scheduled time and what it had delivered by then. Each send is
# keyed by its own occurrences' cycle, so no send depends on the cycle in
# force when the report is read. The occurrences are read through the
# definition index and each email by primary key (the LIMIT keeps it a
# per-row lookup as the outbox grows; see ``send_progress._EMAIL``).
_SENDS = (
    "WITH "
    + _LIFETIME
    + """
SELECT d.id, d.kind, x.revision_id, x.production_cycle, r.due_at,
    x.delivered, x.first_at, x.last_at
FROM stewardship_schedule_definition d
CROSS JOIN LATERAL (
    SELECT o.revision_id, o.production_cycle,
        count(*) FILTER (WHERE m.delivered) AS delivered,
        min(m.finished_at) FILTER (WHERE m.delivered) AS first_at,
        max(m.finished_at) FILTER (WHERE m.delivered) AS last_at
    FROM stewardship_schedule_occurrence o
    JOIN lifetime l ON o.created_at>=l.started_at AND o.created_at<l.ended_at
    LEFT JOIN LATERAL (
        SELECT m.state='delivered' AND m.finished_at<=%(as_of)s
            AND m.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s AS delivered,
            m.finished_at
        FROM stewardship_outbox_message m WHERE m.id=o.outbox_id LIMIT 1
    ) m ON true
    WHERE o.definition_id=d.id AND o.mode=%(mail_mode)s
      AND o.created_at<=%(as_of)s
    GROUP BY o.revision_id, o.production_cycle
) x
JOIN stewardship_schedule_revision r ON r.id=x.revision_id
WHERE d.campaign_id=%(campaign)s AND d.kind IN ('initial','reminder')
ORDER BY r.due_at, d.id, x.revision_id, x.production_cycle
"""
)


def _earliest(*instants):
    """The earliest of the instants that are known, or None when none is."""
    known = [at for at in instants if at is not None]
    return min(known) if known else None


def _instant(value, name):
    """Require a timezone-aware instant; a naive datetime is a programming error."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware instant.")
    return value


@dataclass(frozen=True)
class ResponseScope:
    """One campaign in one system mode.

    ``mode`` is ``production`` (live responses, Production mail; the default)
    or ``testing``, which needs the rehearsal epoch whose responses and mail
    are meant: Testing data is per rehearsal, and a Production scope has no
    epoch.
    """

    campaign_id: UUID
    mode: str = "production"
    rehearsal_epoch_id: UUID | None = None

    def __post_init__(self):
        """Refuse an unknown mode or an epoch that does not fit the mode."""
        if not isinstance(self.campaign_id, UUID):
            raise TypeError("A response scope names one campaign by UUID.")
        if self.mode not in MODES:
            raise ValueError("Response metrics mode must be production or testing.")
        if (self.mode == "testing") != isinstance(self.rehearsal_epoch_id, UUID):
            raise ValueError(
                "A Testing scope names its rehearsal epoch; Production has none."
            )

    @property
    def response_mode(self):
        """How responses and engagement spell the mode: live or test."""
        return MODES[self.mode][0]

    @property
    def mail_mode(self):
        """How outbox messages and occurrences spell the mode."""
        return MODES[self.mode][1]


@dataclass(frozen=True)
class FamilyResponse:
    """One Family's funnel instants as they stood at the metrics' ``as_of``.

    Every instant is None until the Family reached that stage by ``as_of``.
    ``invited_at`` is when the first initial email to the Family was
    delivered (the provider accepted it); ``skipped_responded`` says a
    planned invitation to the Family was later skipped because it had
    already responded (a Family that responded before any invitation was
    planned has no occurrence to skip, and is seen in ``submitted_uninvited``);
    ``link_at``, ``form_at`` and ``progress_at`` are the engagement record's
    first instants as recorded; ``submitted_at`` is the Family's first
    submission and ``submissions`` how many it had made. The funnel counts
    ``form_opened_at`` and ``progressed_at``, which a submission implies.
    """

    family_id: UUID
    family_duid: int
    invited_at: datetime | None
    skipped_responded: bool
    link_at: datetime | None
    form_at: datetime | None
    progress_at: datetime | None
    submitted_at: datetime | None
    submissions: int

    @property
    def form_opened_at(self):
        """When the Family first opened the form, as the funnel counts it.

        Progress and a submission both happen on the form, so either implies
        it was open: the earliest of the recorded open, the recorded progress
        and the first submission. This counts Families whose form opens
        predate the engagement record or were never recorded (a presence
        heartbeat can record progress without a form open), so Form opened
        always contains Progressed.
        """
        return _earliest(self.form_at, self.progress_at, self.submitted_at)

    @property
    def progressed_at(self):
        """When the Family first got past the form's first step, as counted.

        A submission passes every step, so it implies progress: the earlier
        of the recorded instant and the first submission. Progress was not
        recorded before the engagement record's release (1.2.0), so without
        this a Family that submitted earlier would count as submitted but
        never progressed.
        """
        return _earliest(self.progress_at, self.submitted_at)

    @property
    def stages(self):
        """The funnel stages this Family had reached, in funnel order.

        Link followed stays as recorded: a Family can sign in by typing its
        code instead of following its link, so a submission implies nothing
        about the link.
        """
        reached = (
            self.invited_at,
            self.link_at,
            self.form_opened_at,
            self.progressed_at,
            self.submitted_at,
        )
        return tuple(
            stage for stage, at in zip(STAGES, reached, strict=True) if at is not None
        )

    @property
    def submitted_uninvited(self):
        """Whether the Family submitted with no delivered invitation by ``as_of``."""
        return self.submitted_at is not None and self.invited_at is None


@dataclass(frozen=True)
class Stage:
    """One funnel stage: its key, how many distinct Families reached it, a note."""

    key: str
    count: int
    note: str = ""


@dataclass(frozen=True)
class ActivityBucket:
    """Families reaching a stage for the first time in one local hour or day.

    ``start`` is the bucket's first instant in the campaign's timezone (a
    repeated autumn hour keeps its ``fold``, so the two hours stay apart).
    The three counts are first instants only, so over every bucket they add
    up to the funnel's link followed, form opened and submitted totals.
    """

    start: datetime
    links: int
    forms: int
    submissions: int


@dataclass(frozen=True)
class SendMarker:
    """One Family email send to mark on the activity series.

    ``key`` names the send (schedule, revision, mode and the Production
    cycle its occurrences belong to); ``name`` is its plain name as the
    Family email sends page gives it (Invitation, Reminder N);
    ``scheduled`` its revision's due time; ``delivered`` how many of its
    emails had been delivered by ``as_of``, with the first and last
    delivery instants in ``first_delivered_at`` and ``last_delivered_at``
    (None while nothing was delivered).
    """

    key: SendKey
    kind: str
    name: str
    scheduled: datetime
    delivered: int
    first_delivered_at: datetime | None
    last_delivered_at: datetime | None


@dataclass(frozen=True)
class ResponseMetrics:
    """The funnel of one scope at one instant, with the rows behind it.

    ``stages`` is in funnel order; ``skipped_responded``,
    ``submitted_uninvited`` and ``submitted_again`` are the figures reported
    beside it. ``activity`` is the first-instant series by ``grain`` in
    ``timezone`` (buckets with nothing in them are left out) and ``sends``
    the markers for it; ``activity_series`` re-buckets ``families`` at the
    other grain without another read.
    """

    scope: ResponseScope
    as_of: datetime
    timezone: str
    grain: str
    families: tuple[FamilyResponse, ...]
    stages: tuple[Stage, ...]
    skipped_responded: int
    submitted_uninvited: int
    submitted_again: int
    activity: tuple[ActivityBucket, ...]
    sends: tuple[SendMarker, ...]

    def stage(self, key):
        """The count of one stage by key."""
        return next(stage.count for stage in self.stages if stage.key == key)


def stage_counts(families):
    """The funnel totals over the rows: distinct Families per stage, in order.

    A Family is one row, so counting rows that reached a stage counts
    distinct Families.
    """
    return tuple(
        Stage(
            key,
            sum(1 for family in families if key in family.stages),
            LINK_FOLLOWED_NOTE if key == "link_followed" else "",
        )
        for key in STAGES
    )


def bucket_start(instant, zone, grain):
    """The campaign-local hour or day that contains ``instant``, as an aware start.

    The local wall time is truncated and kept in ``zone``. An hour keeps its
    ``fold``, so the two occurrences of a repeated autumn hour become two
    buckets that sort by their real instants; a day is one bucket whatever
    the fold of the instant in it, so its midnight is always the first.
    """
    if grain not in GRAINS:
        raise ValueError("Activity is bucketed by hour or day.")
    local = instant.astimezone(zone)
    if grain == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    return local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)


def activity_series(families, zone, grain="hour"):
    """First links, forms and submissions per local bucket, in time order.

    Only buckets with something in them are returned; the chart fills the
    rest of its axis itself.
    """
    buckets = {}
    for family in families:
        for field, at in (
            ("links", family.link_at),
            ("forms", family.form_opened_at),
            ("submissions", family.submitted_at),
        ):
            if at is None:
                continue
            start = bucket_start(at, zone, grain)
            # Aware datetimes compare equal across fold, so key on it too.
            counts = buckets.setdefault(
                (start.replace(tzinfo=None), start.fold),
                {"start": start, "links": 0, "forms": 0, "submissions": 0},
            )
            counts[field] += 1
    return tuple(
        ActivityBucket(**counts)
        for counts in sorted(
            buckets.values(), key=lambda counts: counts["start"].astimezone(UTC)
        )
    )


def _families(cursor, values):
    """Read the per-Family rows (see ``_FAMILIES``)."""
    cursor.execute(_FAMILIES, values)
    return tuple(FamilyResponse(*row) for row in cursor.fetchall())


def _sends(cursor, values):
    """Read the send markers (see ``_SENDS``), named as the sends page names them."""
    cursor.execute(_SENDS, values)
    found = cursor.fetchall()
    numbers = reminder_numbers(cursor, values["campaign"]) if found else {}
    markers = []
    for definition, kind, revision, cycle, scheduled, *delivery in found:
        delivered, first_at, last_at = delivery
        key = SendKey(definition, revision, values["mail_mode"], cycle)
        listed = ListedSend(key, kind, scheduled, numbers.get(definition), False)
        markers.append(
            SendMarker(
                key, kind, str(listed.name), scheduled, delivered, first_at, last_at
            )
        )
    return tuple(markers)


def response_metrics(scope, as_of, *, grain="hour"):
    """The response funnel of ``scope`` as it stood at ``as_of``.

    The caller owns the (read-only) transaction and has admitted the
    campaign report. Mail evidence is bounded by ``as_of`` alone, across
    every Production cycle, so a later withdrawal and re-activation cannot
    change an earlier reading; the campaign's immutable timezone snapshot
    buckets the activity series. A cutoff of "now" can miss rows committing
    at that moment; a report meant to be reproduced should use a cutoff in
    the past, as the digests do.
    """
    if not isinstance(scope, ResponseScope):
        raise TypeError("Response metrics need a ResponseScope.")
    _instant(as_of, "as_of")
    if grain not in GRAINS:
        raise ValueError("Activity is bucketed by hour or day.")
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=scope.campaign_id
    )
    timezone = campaign.active_configuration.timezone
    values = {
        "campaign": scope.campaign_id,
        "epoch": scope.rehearsal_epoch_id,
        "response_mode": scope.response_mode,
        "mail_mode": scope.mail_mode,
        "as_of": as_of,
    }
    with connection.cursor() as cursor:
        families = _families(cursor, values)
        sends = _sends(cursor, values)
    return ResponseMetrics(
        scope=scope,
        as_of=as_of,
        timezone=timezone,
        grain=grain,
        families=families,
        stages=stage_counts(families),
        skipped_responded=sum(1 for family in families if family.skipped_responded),
        submitted_uninvited=sum(1 for family in families if family.submitted_uninvited),
        submitted_again=sum(1 for family in families if family.submissions > 1),
        activity=activity_series(families, ZoneInfo(timezone), grain),
        sends=sends,
    )
