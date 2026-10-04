"""``pk-stewardship local-seed``: the LOCAL campaign and response seeder (#476).

The seeder gives the local environment a campaign in progress with realistic
Family activity by time travel: it moves the shared fake clock
(``local.clock``) forward through the timeline ``local.seed_timeline``
produces while the real scheduler, worker, mail-dispatch and web code do the
work at the faked instants. Every seeded row is written by production code
under its existing identity; there is no re-timing, no trigger bypass and no
superuser session (see "Seeded campaign and responses" in
docs/specs/stewardship/local-environment/spec.md).

The command runs one step at a time, each as a one-shot container under an
existing service identity, driven by the operator script::

    pk-stewardship local-seed --step STEP --config SERVICE.yaml \\
        --seed N --families N --anchor-date YYYY-MM-DD --now ISO-8601 \\
        [--response-scale M] [--clock-dir DIR]

- ``timeline``: print the intended timeline's summary; no database.
- ``prepare`` (web): configure the campaign's real dates and every Family
  mail schedule through one configuration change request, meet go-live
  readiness and run the real go-live, with the clock at the Friday before
  the start Saturday.
- ``drive`` (web): step the clock through every event before ``now``,
  waiting for work to settle around each jump.
- ``check`` (offline ``migration``): the post-seed invariant check.
- ``finish`` (web): request the real full refresh that promotes the
  late-added Family once the services run in normal mode again.

Every step refuses unless the admitted deployment is LOCAL, before opening
any connection. Every wait has a limit; on expiry the seeder logs which
condition was unmet, the limit and the elapsed time, then stops.
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import (
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)

from . import seed_timeline, seed_web
from .clock import CLOCK_CONTROL_TARGET, CLOCK_MOUNT_TARGET, FakeClock
from .synthetic_parish import MAXIMUM_FAMILIES, generate

LOGGER = logging.getLogger(__name__)
# The seed's own phases, then the BG-12 rehearsal's two steps (local/rehearsal.py).
STEPS = (
    "timeline",
    "wizard",
    "prepare",
    "drive",
    "check",
    "finish",
    "reminder",
    "measure",
)
# The rehearsal steps that need --due-at.
DUE_STEPS = frozenset({"reminder", "measure"})
# Each wait's default limit (specification, "Settled"), and how often the
# seeder re-reads the settled conditions while it waits.
DEFAULT_WAIT_SECONDS = 600
POLL_SECONDS = 1.0
# A wait logs its state this often while it goes on.
PROGRESS_SECONDS = 30
# A Family event this close ahead of fake time runs without a jump: fake time
# runs at real speed, and a jump costs the cache wait plus the settle after
# it, so a Family's presence steps (seconds apart) ride on its session's jump.
# The recorded instant then leads the intended one by at most this much.
# Occurrence instants always jump, so the real services never see them early.
JUMP_THRESHOLD = timedelta(seconds=150)
# The task types the settled conditions cover: those routed to the general,
# source and mail queues (jobs.queue_wait.TASK_QUEUES names them all).
TERMINAL_OCCURRENCE_STATES = frozenset({"succeeded", "skipped"})
FATAL_OCCURRENCE_STATES = frozenset({"delivery_unknown", "coalesced", "failed"})
FATAL_OUTBOX_STATES = frozenset({"delivery_unknown", "permanent_failure"})
FATAL_TASK_STATES = frozenset({"failed", "abandoned"})


class SeedRefused(ConfigError):
    """The step cannot run: wrong profile, bad arguments or an unmet precondition."""


class SeedFailed(ConfigError):
    """A fatal condition appeared, or a wait expired; the seed stops at once."""


@dataclass(frozen=True)
class SeedRequest:
    """The arguments the operator script passes to every step."""

    step: str
    seed: int
    families: int
    anchor_date: date
    now: datetime
    response_scale: float
    clock_directory: Path
    admin_email: str | None = None
    seeded_now: str | None = None
    due_at: datetime | None = None

    @classmethod
    def parse(cls, args):
        """Validate the command line; messages name options, never values."""
        if args.step not in STEPS:
            raise SeedRefused("--step must be one of " + ", ".join(STEPS))
        try:
            seed = int(args.seed)
            families = int(args.families)
            if not 0 <= seed < 2**63 or not 1 <= families <= MAXIMUM_FAMILIES:
                raise ValueError
        except (TypeError, ValueError):
            raise SeedRefused("--seed and --families must be whole numbers.") from None
        try:
            anchor = date.fromisoformat(args.anchor_date)
        except (TypeError, ValueError):
            raise SeedRefused("--anchor-date must be an ISO date.") from None
        try:
            now = datetime.fromisoformat(args.now)
            if now.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            raise SeedRefused(
                "--now must be an ISO-8601 instant with a zone."
            ) from None
        try:
            scale = 1.0 if args.response_scale is None else float(args.response_scale)
            if not scale > 0:
                raise ValueError
        except (TypeError, ValueError):
            raise SeedRefused("--response-scale must be a positive number.") from None
        directory = (
            CLOCK_MOUNT_TARGET if args.clock_dir is None else Path(args.clock_dir)
        )
        seeded_now = getattr(args, "seeded_now", None)
        if seeded_now is not None:
            try:
                if datetime.fromisoformat(seeded_now).utcoffset() is None:
                    raise ValueError
            except (TypeError, ValueError):
                raise SeedRefused("--seeded-now must be an ISO-8601 instant.") from None
        due_at = getattr(args, "due_at", None)
        if args.step in DUE_STEPS or due_at is not None:
            try:
                due_at = datetime.fromisoformat(due_at)
                # Whole minutes: a schedule's time is a wall-clock minute.
                if due_at.utcoffset() is None or due_at.second or due_at.microsecond:
                    raise ValueError
            except (TypeError, ValueError):
                raise SeedRefused(
                    "--due-at must be an ISO-8601 instant on a whole minute."
                ) from None
            due_at = due_at.astimezone(UTC)
        return cls(
            args.step,
            seed,
            families,
            anchor,
            now.astimezone(UTC),
            scale,
            directory,
            getattr(args, "admin_email", None),
            seeded_now,
            due_at,
        )


# Waiting.
def wait_until(what, condition, *, limit=DEFAULT_WAIT_SECONDS, sleep=time.sleep):
    """Poll ``condition`` until it returns a true value, within ``limit`` seconds.

    On expiry the unmet condition, the limit and the elapsed time are logged
    durably (the project rule for every timeout) and SeedFailed is raised.
    ``condition`` returns a true value when met; it may also return a string
    naming what is still unmet, which the log then names.
    """
    started = time.monotonic()
    unmet = what
    reported = 0
    while True:
        result = condition()
        if result is True:
            return time.monotonic() - started
        if isinstance(result, str):
            unmet = result
        elapsed = time.monotonic() - started
        if elapsed - reported >= PROGRESS_SECONDS:
            # A long wait says what it is waiting for, so a stalled seed can
            # be read from the operator's log while it is still running.
            LOGGER.info("still waiting for %s (%s; %.0f s)", what, unmet, elapsed)
            reported = elapsed
        if elapsed >= limit:
            LOGGER.error(
                "seed wait expired: waiting for %s; unmet: %s; limit %.0f s; "
                "elapsed %.1f s",
                what,
                unmet,
                limit,
                elapsed,
            )
            raise SeedFailed(f"Timed out waiting for {what}.")
        sleep(min(POLL_SECONDS, limit - elapsed))


# Settled conditions (real schema; see the specification's "Settled").
def fatal_conditions(since, *, tolerate=frozenset()):
    """Conditions that fail the seed at once, among rows created since ``since``.

    ``tolerate`` names occurrence states that are expected in a phase: phase 4
    tolerates ``coalesced``, which the real refresh reconciliation produces
    when it promotes the late-added Family and folds the Reminders it missed
    into one recovery send (``missed_family_recovery``).
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    found = []
    for label, rows in (
        (
            "occurrence",
            ScheduleOccurrence.objects.filter(
                created_at__gte=since, state__in=FATAL_OCCURRENCE_STATES - tolerate
            ),
        ),
        (
            "outbox message",
            OutboxMessage.objects.filter(
                created_at__gte=since, state__in=FATAL_OUTBOX_STATES
            ),
        ),
        (
            "task",
            TaskRun.objects.filter(created_at__gte=since, state__in=FATAL_TASK_STATES),
        ),
    ):
        for state in sorted(set(rows.values_list("state", flat=True))):
            found.append(f"{label} in {state}")
    return found


def unsettled_conditions(now, since, *, tolerate=frozenset()):
    """What still keeps work from being settled at fake instant ``now``.

    Empty when settled. Tasks and outbox messages still queued, running or in
    a retry wait that has come due, and occurrences due but not yet
    succeeded or skipped (or in a ``tolerate`` state, which phase 4 uses for
    the late-added Family's ``coalesced`` recovery rows: they are final, not
    unfinished); all among rows created since ``since``, so earlier history
    (the setup wizard's) is never waited on.
    """
    from django.db.models import Q

    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage
    from parishkit.stewardship.jobs.queue_wait import TASK_QUEUES

    due_retry = Q(state="retry_wait", not_before__lte=now)
    reasons = []
    tasks = TaskRun.objects.filter(
        created_at__gte=since, task_type__in=TASK_QUEUES
    ).filter(Q(state__in=("queued", "running")) | due_retry)
    count = tasks.count()
    if count:
        reasons.append(f"{count} task(s) queued, running or due for retry")
    messages = OutboxMessage.objects.filter(created_at__gte=since).filter(
        Q(state__in=("pending", "submitting")) | due_retry
    )
    count = messages.count()
    if count:
        reasons.append(f"{count} outbox message(s) pending, submitting or due")
    occurrences = ScheduleOccurrence.objects.filter(
        created_at__gte=since, due_at__lte=now
    ).exclude(state__in=TERMINAL_OCCURRENCE_STATES | tolerate)
    count = occurrences.count()
    if count:
        reasons.append(f"{count} due occurrence(s) not yet succeeded or skipped")
    return reasons


def database_now():
    """PostgreSQL's wall clock: the shared fake clock as the stores see it."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def settle(
    since, *, limit=DEFAULT_WAIT_SECONDS, sleep=time.sleep, tolerate=frozenset()
):
    """Wait until work has settled; fail at once on a fatal condition."""

    def condition():
        """Settled, or the first reason it is not; fatal conditions raise."""
        from django.db import connections

        fatal = fatal_conditions(since, tolerate=tolerate)
        if fatal:
            LOGGER.error("seed stopped: %s", "; ".join(fatal))
            raise SeedFailed("A fatal work state appeared: " + "; ".join(fatal))
        reasons = unsettled_conditions(database_now(), since, tolerate=tolerate)
        connections.close_all()
        return True if not reasons else "; ".join(reasons)

    return wait_until("work to settle", condition, limit=limit, sleep=sleep)


# Phase 2: driving the timeline.
def occurrence_evidence(event, campaign_id, timezone, *, mode="production"):
    """The database evidence that the real services acted on an occurrence instant.

    After the clock jumps to an occurrence instant the seeder waits for the
    row the real code writes for it, read from the real schema, rather than
    for a scheduler heartbeat: the campaign ``active`` at its start boundary,
    an occurrence due at the Initial's or a Reminder's exact instant, and the
    daily-fact row for the campaign-local day a midnight closed. Returns a
    ``wait_until`` condition (True, or what is still unmet). ``mode`` is the
    occurrences' mode, production for a seeded (live) campaign; tests count
    Testing-mode occurrences, the only kind a draft campaign admits.
    """
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.reports.models import CampaignDailyFact

    if event.kind == "boundary_start":

        def active():
            state = Campaign.objects.values_list("state", flat=True).get(pk=campaign_id)
            return state == "active" or f"the campaign to become active (now {state})"

        return active
    if event.kind in {"initial", "reminder"}:
        from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

        # The scheduler plans Families in pages (schedule_production), with
        # its cursor carried across loops, so the first occurrence is not the
        # whole plan. Expected: every Family active and email-eligible with no
        # effective submission at the jump; the planned set must cover them
        # (it may also hold a skipped occurrence for a Family that submitted
        # just before, such as the early responder). Zero expected is met.
        expected = FamilyCampaign.objects.filter(
            campaign_id=campaign_id,
            active=True,
            portal_eligible=True,
            email_eligible=True,
            effective_submission_id__isnull=True,
        ).count()

        def planned():
            targets = (
                ScheduleOccurrence.objects.filter(
                    definition__campaign_id=campaign_id, mode=mode, due_at=event.at
                )
                .values("target")
                .distinct()
                .count()
            )
            if targets >= expected:
                return True
            return (
                f"occurrences due at {event.at.isoformat(timespec='seconds')} "
                f"({targets} of {expected} Families planned)"
            )

        return planned
    if event.kind == "midnight":
        closed = (event.at.astimezone(ZoneInfo(timezone)) - timedelta(days=1)).date()

        def facts():
            if CampaignDailyFact.objects.filter(local_date=closed).exists():
                return True
            return f"the daily-fact row for {closed.isoformat()}"

        return facts
    raise SeedRefused(f"No occurrence evidence is defined for {event.kind}.")


@dataclass
class DriveReport:
    """What one drive did: counts, timings and the seeded now."""

    events: int = 0
    jumps: int = 0
    late: int = 0
    settle_seconds: float = 0.0
    seeded_now: datetime | None = None


def drive_timeline(timeline, clock, *, settle, driver, evidence, log=LOGGER.info):
    """Visit every intended event in order (specification, "Historical ordering").

    Before every jump work must have settled, since a jump can expire a held
    lease. Occurrence instants are always jumped to (never run early), then
    the seeder waits for ``evidence(event)`` (the row the real services write
    for that instant) and settles: the real scheduler plans and the worker
    and mail-dispatch prepare and send. Family events are jumped to when more
    than JUMP_THRESHOLD ahead and then run through ``driver``. An event whose
    intended instant fake time has already passed runs at once (``late``).
    The seeded now is the fake time at the end.
    """
    report = DriveReport()
    for index, event in enumerate(timeline.events, start=1):
        report.settle_seconds += settle()
        if event.is_occurrence or event.at - clock.now() > JUMP_THRESHOLD:
            if clock.jump_to(event.at) is None:
                report.late += 1
            else:
                report.jumps += 1
        else:
            # Close enough ahead to run at once (JUMP_THRESHOLD).
            report.late += 1
        if event.is_occurrence:
            report.settle_seconds += wait_until(
                f"the {event.kind} at {event.at.isoformat(timespec='seconds')}",
                evidence(event),
            )
            report.settle_seconds += settle()
        else:
            driver.run(event)
        report.events = index
        log(
            "seed event %d/%d %s%s at %s (fake now %s)",
            index,
            len(timeline.events),
            event.kind,
            "" if event.family is None else f" family={event.family}",
            event.at.isoformat(timespec="seconds"),
            clock.now().isoformat(timespec="seconds"),
        )
    report.settle_seconds += settle()
    report.seeded_now = clock.now()
    return report


# Phase 1 helpers: configuration through the real change-request path.
def schedule_patch(document, campaign_id, calendar):
    """The one configuration patch that applies the real dates and schedules.

    The campaign's dates become the calendar's; the existing Initial schedule
    moves to 10:00 on the start Saturday; every existing Reminder schedule is
    removed and the calendar's Tuesday and Thursday 09:00 Reminders are added,
    each with the Reminder email revision already in use (or the Initial's
    when the wizard configured no Reminder). Digest schedules are untouched.
    """
    campaign_id = str(campaign_id)
    initials, reminders = campaign_schedules(document, campaign_id)
    template, subject = reminder_reference(
        document, campaign_id, schedules=(initials, reminders)
    )
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": campaign_id,
            "values": {
                "start_date": calendar.start.isoformat(),
                "end_date": calendar.end.isoformat(),
            },
        },
        {
            "operation": "update",
            "section": "schedules",
            "id": initials[0]["id"],
            "values": {
                "date": calendar.start.isoformat(),
                "time": seed_timeline.INITIAL_TIME.isoformat(timespec="seconds"),
            },
        },
    ]
    patch += [
        {"operation": "remove", "section": "schedules", "id": row["id"]}
        for row in reminders
    ]
    patch += [
        {
            "operation": "add",
            "section": "schedules",
            "id": str(uuid4()),
            "values": {
                "campaign_id": campaign_id,
                "kind": "reminder",
                "date": instant.date().isoformat(),
                "time": instant.time().isoformat(timespec="seconds"),
                "weekday": None,
                "subject": subject,
                "template_version": template,
            },
        }
        for instant in calendar.reminders
    ]
    return patch


def campaign_schedules(document, campaign_id):
    """The campaign's Initial and Reminder schedule rows; exactly one Initial."""
    schedules = [
        row
        for row in document["sections"].get("schedules", [])
        if row["values"]["campaign_id"] == str(campaign_id)
    ]
    initials = [row for row in schedules if row["values"]["kind"] == "initial"]
    reminders = [row for row in schedules if row["values"]["kind"] == "reminder"]
    if len(initials) != 1:
        raise SeedRefused("The campaign needs exactly one Initial schedule.")
    return initials, reminders


def reminder_reference(document, campaign_id, *, schedules=None):
    """The email revision and subject a new Reminder schedule names.

    A Reminder schedule must name the campaign's Reminder email revision with
    that revision's subject (content_schema.validate_content_records). The
    wizard's default content always has one; a configuration without it
    falls back to an existing Reminder's or the Initial's reference, which
    validation then checks. ``schedules`` is ``campaign_schedules``' answer
    when the caller already has it.
    """
    campaign_id = str(campaign_id)
    initials, reminders = schedules or campaign_schedules(document, campaign_id)
    emails = [
        row
        for row in document["sections"].get("content", [])
        if row["values"]["campaign_id"] == campaign_id
        and row["values"]["kind"] == "email"
        and row["values"]["slot"] == "reminder"
    ]
    source = (emails or reminders or initials)[0]
    template = source["id"] if emails else source["values"]["template_version"]
    return template, source["values"]["subject"]


def pending_configuration_requests(since):
    """Configuration change requests recorded since ``since`` that are not final."""
    from parishkit.stewardship.accounts.request_models import (
        ConfigurationChangeRequest,
    )

    pending = 0
    for request in ConfigurationChangeRequest.objects.filter(created_at__gte=since):
        latest = request.checkpoints.order_by("-sequence").first()
        if latest is None or latest.state not in {"applied", "failed", "cancelled"}:
            pending += 1
        elif latest.state == "failed":
            raise SeedFailed("The seed's configuration change request failed.")
    return pending


# Phase 3: the invariant check.
# Each check is a count query, an admitted comparison and the message raised
# when it fails; the DO block is assembled from them so every line stays
# readable and the self-verifying shape (RAISE EXCEPTION unless the condition
# holds) is the same for every check.
# The order the real code writes them in: an occurrence's due instant, then
# the outbox delivery it produced, then the fulfillment that records it; a
# Family's session, then its form baseline, then its submission, then the
# receipt.
MONOTONE_CHECKS = (
    (
        "SELECT count(*) FROM stewardship_schedule_fulfillment f "
        "JOIN stewardship_schedule_occurrence o ON o.id = f.occurrence_id "
        "WHERE f.created_at < o.due_at",
        "fulfillments before their occurrence",
    ),
    (
        "SELECT count(*) FROM stewardship_schedule_fulfillment f "
        "JOIN stewardship_schedule_occurrence o ON o.id = f.occurrence_id "
        "JOIN stewardship_outbox_message m ON m.id = o.outbox_id "
        "WHERE m.finished_at IS NOT NULL AND f.created_at < m.finished_at "
        "AND f.disposition = 'delivered'",
        "delivered fulfillments before their message was delivered",
    ),
    (
        "SELECT count(*) FROM stewardship_outbox_message m "
        "JOIN stewardship_schedule_occurrence o ON o.outbox_id = m.id "
        "WHERE m.created_at < o.due_at",
        "messages before their occurrence",
    ),
    (
        "SELECT count(*) FROM stewardship_family_form_baseline b "
        "JOIN stewardship_family_session s ON s.id = b.family_session_id "
        "WHERE b.created_at < s.authenticated_at",
        "baselines before their session",
    ),
    (
        "SELECT count(*) FROM stewardship_submission s "
        "JOIN stewardship_family_form_baseline b ON b.id = s.baseline_id "
        "WHERE s.submitted_at < b.created_at",
        "submissions before their baseline",
    ),
    (
        "SELECT count(*) FROM stewardship_submission_receipt r "
        "JOIN stewardship_submission s ON s.id = r.submission_id "
        "WHERE r.created_at < s.submitted_at",
        "receipts before their submission",
    ),
)
# Timestamp columns compared against the seeded now, by table. Expiry,
# deadline, due and boundary columns are future by design and so omitted
# (the specification's allowlist).
_SEEDED_NOW_COLUMNS = (
    ("stewardship_family_session", ("authenticated_at", "last_activity_at")),
    ("stewardship_family_form_baseline", ("created_at",)),
    ("stewardship_submission", ("created_at", "submitted_at")),
    ("stewardship_submission_receipt", ("created_at",)),
    ("stewardship_outbox_message", ("created_at", "submitted_at", "finished_at")),
    ("stewardship_schedule_occurrence", ("created_at",)),
    ("stewardship_schedule_fulfillment", ("created_at",)),
    ("stewardship_daily_fact", ("created_at",)),
    (
        "stewardship_family_engagement",
        ("first_link_at", "first_form_at", "first_progress_at", "last_seen_at"),
    ),
    ("stewardship_task_run", ("created_at",)),
    ("stewardship_audit_event", ("created_at",)),
)


def _raise_unless(query, comparison, message):
    """One PL/pgSQL check: count, then RAISE EXCEPTION unless the count passes."""
    return (
        f"    {query} INTO n;\n"
        f"    IF NOT (n {comparison}) THEN\n"
        f"        RAISE EXCEPTION 'seed invariant: % {message}', n;\n"
        f"    END IF;"
    )


def invariant_sql(seeded_now, counts, *, start=None):
    """The ``DO`` block phase 3 runs under ``migration`` (specification, phase 3).

    It raises unless parent and child timestamps are monotone (occurrence,
    delivery, fulfillment; session, baseline, submission, receipt), a daily-
    fact row exists for every elapsed campaign-local day from ``start`` (the
    campaign's start date; without it the count is only bounded below), the
    live submission count equals what the timeline implies, and no seeded
    timestamp in the tables the seed writes through is later than the seeded
    now, other than the allowlisted future-by-design columns. The check runs
    with the services stopped and before phase 4, so nothing is later than
    the seeded now by construction, and this proves it.
    """
    if not isinstance(seeded_now, datetime) or seeded_now.utcoffset() is None:
        raise SeedRefused("The invariant check needs the seeded now.")
    submissions = int(counts.get("submission", 0))
    days = int(counts.get("midnight", 0))
    at = seeded_now.astimezone(UTC).isoformat()
    checks = [
        _raise_unless(query, "= 0", message) for query, message in MONOTONE_CHECKS
    ]
    if start is None:
        checks.append(
            _raise_unless(
                "SELECT count(DISTINCT local_date) FROM stewardship_daily_fact "
                "WHERE created_at <= seeded_now",
                f">= {days}",
                f"daily fact days, expected at least {days}",
            )
        )
    else:
        if type(start) is not date:
            raise SeedRefused("The invariant check needs the campaign's start date.")
        checks.append(
            _raise_unless(
                "SELECT count(DISTINCT local_date) FROM stewardship_daily_fact "
                f"WHERE local_date >= '{start.isoformat()}' "
                f"AND local_date < '{(start + timedelta(days=days)).isoformat()}'",
                f"= {days}",
                f"elapsed days with a daily-fact row, expected exactly {days}",
            )
        )
    checks.append(
        _raise_unless(
            "SELECT count(*) FROM stewardship_submission "
            "WHERE mode = 'live' AND created_at <= seeded_now",
            f"= {submissions}",
            f"live submissions, expected {submissions}",
        )
    )
    checks += [
        _raise_unless(
            f"SELECT count(*) FROM {table} WHERE {column} > seeded_now",
            "= 0",
            f"{table}.{column} later than the seeded now",
        )
        for table, columns in _SEEDED_NOW_COLUMNS
        for column in columns
    ]
    body = "\n".join(checks)
    return (
        "DO $$\n"
        "DECLARE\n"
        f"    seeded_now timestamptz := '{at}';\n"
        "    n bigint;\n"
        "BEGIN\n"
        f"{body}\n"
        "    RAISE NOTICE 'seed invariants hold at %', seeded_now;\n"
        "END $$;\n"
    )


# Step runners.
def timeline_for(request, eligible, **options):
    """The timeline for a request and the eligible Family keys."""
    return seed_timeline.build(
        request.seed,
        request.families,
        request.now,
        eligible,
        response_scale=request.response_scale,
        **options,
    )


def synthetic_eligible(request):
    """The Portal-eligible Families as the synthetic parish alone implies them.

    Active Families whose heads have an email address, ordered by DUID. The
    database steps use the real FamilyCampaign rows instead; this is for the
    ``timeline`` preview, which opens no connection.
    """
    parish = generate(request.seed, request.families, request.anchor_date)
    with_email = {
        row["familyDUID"]
        for row in parish.members
        if row["memberType"] in {"Head", "Husband", "Wife"}
        and (row.get("emailAddress") or "").strip()
    }
    return sorted(
        row["familyDUID"]
        for row in parish.families
        if row.get("familyActive", True) and row["familyDUID"] in with_email
    )


def database_eligible(campaign):
    """The campaign's Portal-eligible Families from the real rows, by DUID.

    Returns the Family identities with an eligible email, the one Family
    without (the specification's "no eligible email" case, or None) and the
    campaign's Ministry DUIDs, so every database step builds the same
    timeline from the same inputs.
    """
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    rows = list(
        FamilyCampaign.objects.filter(campaign=campaign, portal_eligible=True)
        .order_by("family_duid")
        .values_list("pk", "email_eligible")
    )
    eligible = [pk for pk, email in rows if email]
    without = [pk for pk, email in rows if not email]
    ministries = tuple(campaign.active_configuration.values.get("ministry_duids", []))
    return eligible, (without[0] if without else None), ministries


def campaign_timeline(request, campaign):
    """The timeline for the current campaign from the database's Families."""
    eligible, no_email, ministries = database_eligible(campaign)
    return timeline_for(
        request,
        eligible,
        timezone=campaign.active_configuration.timezone,
        ministries=ministries,
        no_email_family=no_email,
    )


def timeline_summary(request):
    """The ``timeline`` step's document: calendar, counts and the funnel."""
    result = timeline_for(request, synthetic_eligible(request))
    cal = result.calendar
    return {
        "step": "timeline",
        "seed": request.seed,
        "families": request.families,
        "response_scale": result.response_scale,
        "now": cal.now.isoformat(timespec="seconds"),
        "timezone": cal.timezone,
        "start": cal.start.isoformat(),
        "end": cal.end.isoformat(),
        "initial_at": cal.initial_at.isoformat(timespec="seconds"),
        "prepare_at": cal.prepare_at.isoformat(timespec="seconds"),
        "reminders": [r.isoformat(timespec="seconds") for r in cal.reminders],
        "elapsed_days": cal.elapsed_days,
        "eligible": len(result.eligible),
        "daily_submissions": list(result.daily_submissions),
        "today_submissions": result.today_submissions,
        "stages": {stage: len(keys) for stage, keys in result.stages.items()},
        "events": result.counts(),
        "first_event": result.events[0].at.isoformat(timespec="seconds"),
        "last_event": result.events[-1].at.isoformat(timespec="seconds"),
    }


def run_step(request, configuration):
    """Run one database-backed step."""
    from .rehearsal import measure_step, reminder_step

    return {
        "wizard": wizard_step,
        "prepare": prepare_step,
        "drive": drive_step,
        "check": check_step,
        "finish": finish_step,
        "reminder": reminder_step,
        "measure": measure_step,
    }[request.step](request, configuration)


def admit_seeder_mounts(configuration, mounts):
    """The web mount policy, plus exactly one writable clock-control mount.

    The seeder's one-shot container has web's mounts and the clock directory
    a second time, writable, at CLOCK_CONTROL_TARGET. That one mount is set
    aside here and every other mount goes through the ordinary web policy
    (``validate_mounts``), so the seeder cannot carry any mount web may not.
    """
    from parishkit.stewardship.service_boundaries import validate_mounts

    if os.geteuid() == 0:
        raise SeedRefused("The seeder cannot run as root.")
    if configuration.profile is not DeploymentProfile.LOCAL:
        raise SeedRefused("The seeder runs only in the local profile.")
    control = [mount for mount in mounts if mount.target == CLOCK_CONTROL_TARGET]
    if len(control) != 1 or control[0].read_only:
        raise SeedRefused(
            "The seeder needs the clock directory writable at its control path."
        )
    rest = [mount for mount in mounts if mount.target != CLOCK_CONTROL_TARGET]
    if validate_mounts(configuration, rest) is not ServiceRole.WEB:
        raise SeedRefused("This seed step runs under the web service identity.")


def configure_web_process(configuration):
    """Configure this one-shot process as the web service configures itself.

    LOCAL only, under the web identity, with web's mounts plus the writable
    clock-control mount (``admit_seeder_mounts``); then ``configure_web_runtime``
    does everything web does after its own mount admission: keyrings, the
    SQL-role and coherent-authority admissions, the limiter and the Family
    and Admin runtimes.
    """
    from parishkit.stewardship.runtime_web import admit_lifecycle_mounts
    from parishkit.stewardship.service_boundaries import kernel_mounts

    if configuration.profile is not DeploymentProfile.LOCAL:
        raise SeedRefused("The seeder runs only in the local profile.")
    if configuration.service_role is not ServiceRole.WEB:
        raise SeedRefused("This seed step runs under the web service identity.")
    admit_seeder_mounts(configuration, kernel_mounts())
    admit_lifecycle_mounts(configuration)
    from parishkit.stewardship.runtime_web import configure_web_runtime

    configure_web_runtime(configuration)


def current_campaign():
    """The setup wizard's first campaign, which the seed reuses; refused if none."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    campaign_id = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).get()
    if campaign_id is None:
        raise SeedRefused("The setup wizard's first campaign is required.")
    return Campaign.objects.select_related("active_configuration").get(pk=campaign_id)


def admin(request):
    """A signed-in Admin request through the shared identity core."""
    if request.admin_email is None:
        raise SeedRefused("This seed step requires --admin-email.")
    return seed_web.sign_in_admin(seed_web.new_request(), request.admin_email)


def wizard_step(request, configuration):
    """Complete the setup wizard unattended (LOCAL convenience; see seed_web)."""
    configure_web_process(configuration)
    # Model imports only after Django is configured.
    from parishkit.stewardship.accounts.setup_completion import setup_is_complete

    if setup_is_complete():
        return {"step": "wizard", "result": "setup was already complete"}
    if request.admin_email is None:
        raise SeedRefused("This seed step requires --admin-email.")
    service = seed_web.admin_runtime()
    resumed = seed_web.resume_wizard(service, request.admin_email)
    attempt_id = None
    if resumed is None:
        web = admin(request)
    else:
        # An earlier run's attempt: continue it under its own live session.
        web, attempt_id = resumed
        LOGGER.info("resuming the setup attempt %s", attempt_id)
    today = (
        FakeClock(request.clock_directory)
        .now()
        .astimezone(ZoneInfo(seed_web.WIZARD_PARISH["timezone"]))
    )
    # A first campaign in the near future; the seed moves its dates later.
    start = today.date() + timedelta(days=10)
    dates = (start, start + timedelta(days=30))
    seed_web.run_wizard(
        web, service, wait=wait_until, campaign_dates=dates, attempt_id=attempt_id
    )
    # The freeze hands over to the credential installers; the operator script
    # then recreates the consumers from compose.json and acknowledges each
    # request inside them (runbook, first installation, step 5), which is
    # what completes setup. The request ids are the sealed credentials' ids.
    from parishkit.stewardship.accounts.setup_models import SetupAttempt
    from parishkit.stewardship.accounts.setup_secret_models import (
        SetupSealedCredential,
    )

    attempt = (
        SetupAttempt.objects.filter(state="frozen").order_by("-created_at").first()
    )
    if attempt is None:
        raise SeedFailed("The setup attempt is not frozen after the wizard.")
    requests = {
        row.target: str(row.pk)
        for row in SetupSealedCredential.objects.filter(
            attempt=attempt, scrubbed_at=None
        ).only("id", "target")
    }
    return {
        "step": "wizard",
        "result": "frozen",
        "campaign_dates": [day.isoformat() for day in dates],
        "requests": requests,
    }


def task_root_finished(root_id, what):
    """A wait condition: the task chain rooted at ``root_id`` has succeeded.

    A failed or abandoned run ends the seed at once; any other state names
    ``what`` as still unmet.
    """
    from parishkit.stewardship.jobs.models import TaskRun

    run = TaskRun.objects.filter(root_id=root_id).order_by("-retry_sequence").first()
    if run is None:
        return f"{what} to be recorded"
    if run.state == "succeeded":
        return True
    if run.state in FATAL_TASK_STATES:
        raise SeedFailed(f"{what} ended in {run.state}.")
    return f"{what} ({run.state})"


def _initial_revision(service, campaign):
    """The email revision the campaign's Initial schedule sends."""
    rows = [
        row
        for row in service.store.active().document()["sections"]["schedules"]
        if row["values"]["campaign_id"] == str(campaign.pk)
        and row["values"]["kind"] == "initial"
    ]
    if len(rows) != 1:
        raise SeedRefused("The campaign needs exactly one Initial schedule.")
    return UUID(rows[0]["values"]["template_version"])


def prepare_step(request, configuration):
    """Phase 1: configure, meet readiness and run the real go-live (scheduled).

    With the clock at the Friday before the start: one configuration change
    request applies the real dates and every Family mail schedule (installed
    by the running configuration installer); a full refresh against the fake,
    an Initial email sample to Mailpit and the already current provider
    receipts
    meet readiness; then the real go-live flow runs under the Admin session,
    each verify followed at once by its act: readiness and cleanup, link
    preparation, a fresh sign-in, and the Production confirmation, whose
    worker task activates the campaign as ``scheduled`` because the start is
    still in the future.
    """
    configure_web_process(configuration)
    # Model imports only after Django is configured.
    from parishkit.stewardship.accounts.admin_editing import principal
    from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
    from parishkit.stewardship.accounts.configuration_requests import record_request
    from parishkit.stewardship.campaigns.activation_models import (
        ProductionTokenPreparation,
    )
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.campaigns.production_models import (
        ProductionTransitionRequest,
    )
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.observability import current_correlation

    campaign = current_campaign()
    if campaign.state != "draft":
        raise SeedRefused(f"The campaign is {campaign.state}; a draft is required.")
    cal = seed_timeline.calendar(request.now, campaign.active_configuration.timezone)
    clock = FakeClock(request.clock_directory)
    clock.jump_to(cal.prepare_at.astimezone(UTC))
    since = clock.now()
    timings = {}
    web = admin(request)
    service = seed_web.admin_runtime()
    actor = principal(web, service).identity

    def timed(name, what, condition):
        """Wait for a condition and keep how long it took."""
        timings[name] = round(wait_until(what, condition), 1)

    # 1. The real dates and every Family mail schedule, as one change request.
    active = service.store.active()
    record_request(
        base_digest=active.digest,
        patch=schedule_patch(active.document(), campaign.pk, cal),
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=current_correlation(),
    )
    timed(
        "configuration",
        "the configuration change request to be installed",
        lambda: (
            pending_configuration_requests(since) == 0
            or "a configuration request is still pending"
        ),
    )
    campaign = current_campaign()
    # 2. Readiness: a full refresh against the fake, then the Initial sample.
    # The request may be coalesced onto a refresh already pending (one the
    # scheduler planned before the jump, which `since` would not cover), so
    # the wait follows the receipt's own task root, not the settle alone.
    receipt = seed_web.request_full_refresh(web, service)
    timed(
        "refresh",
        "the full ParishSoft refresh to succeed",
        lambda: task_root_finished(receipt.task_root_id, "the full refresh"),
    )
    settle(since)
    if not FamilyCampaign.objects.filter(
        campaign=campaign, portal_eligible=True, email_eligible=True
    ).exists():
        raise SeedRefused("No Portal-eligible Family with an email was found.")
    # Sending needs a fresh sign-in (five minutes); the waits above may have
    # used it up, so step up again, as an Admin would on the page.
    seed_web.sign_in_admin(web, request.admin_email)
    seed_web.sample_mail(
        web, service, campaign.pk, _initial_revision(service, campaign)
    )

    def tested():
        """The sample reached the catcher through mail-dispatch (or failed)."""
        row = (
            CampaignMailTest.objects.filter(campaign_id=campaign.pk)
            .order_by("-created_at")
            .first()
        )
        if row is not None and row.state == "accepted":
            return True
        if row is not None and row.state not in {"queued", "submitting"}:
            raise SeedFailed(f"The email sample ended in {row.state}.")
        return "the Initial email sample"

    timed("sample_mail", "the Initial email sample to be accepted", tested)
    settle(since)
    # 3. The real go-live: readiness and cleanup.
    seed_web.sign_in_admin(web, request.admin_email)
    status = seed_web.start_go_live(web, service, campaign.pk)
    transition_id = status.request_id

    def cleaned():
        """Testing cleanup finished (or failed) on the worker."""
        row = ProductionTransitionRequest.objects.get(pk=transition_id)
        if row.state == "cleanup_complete":
            return True
        if row.state in {"cleanup_failed", "cancelled"}:
            raise SeedFailed(f"The go-live cleanup ended in {row.state}.")
        return f"the Testing cleanup ({row.state})"

    timed("cleanup", "the Testing cleanup to complete", cleaned)
    settle(since)
    # Link preparation, then a fresh sign-in (confirmation needs one after
    # cleanup) and the Production confirmation.
    seed_web.prepare_links(web, service, campaign.pk, transition_id)

    def prepared():
        """The link preparation task succeeded (or failed)."""
        preparation = (
            ProductionTokenPreparation.objects.filter(transition_id=transition_id)
            .order_by("-created_at")
            .first()
        )
        if preparation is None:
            return "the link preparation to be recorded"
        task = (
            TaskRun.objects.filter(root_id=preparation.task_id)
            .order_by("-retry_sequence")
            .first()
        )
        if task.state == "succeeded":
            return True
        if task.state in FATAL_TASK_STATES:
            raise SeedFailed(f"Link preparation ended in {task.state}.")
        return f"the link preparation ({task.state})"

    timed("links", "the link preparation to succeed", prepared)
    settle(since)
    preparation = (
        ProductionTokenPreparation.objects.filter(transition_id=transition_id)
        .order_by("-created_at")
        .first()
    )
    seed_web.sign_in_admin(web, request.admin_email)
    seed_web.confirm_production(
        web, service, campaign.pk, transition_id, preparation.pk
    )

    def scheduled():
        """The worker's activation task took the campaign to scheduled."""
        state = Campaign.objects.values_list("state", flat=True).get(pk=campaign.pk)
        if state == "scheduled":
            return True
        if state not in {"draft"}:
            raise SeedFailed(f"The campaign became {state}, not scheduled.")
        return "the activation task"

    timed("activation", "the campaign to be scheduled", scheduled)
    timings["final_settle"] = round(settle(since), 1)
    return {
        "step": "prepare",
        "campaign": str(campaign.pk),
        "prepare_at": cal.prepare_at.isoformat(timespec="seconds"),
        "fake_now": clock.now().isoformat(timespec="seconds"),
        "timings": timings,
    }


def drive_step(request, configuration):
    """Phase 2: occurrence instants through the clock, Family events through web."""
    configure_web_process(configuration)
    campaign = current_campaign()
    result = campaign_timeline(request, campaign)
    since = result.calendar.prepare_at.astimezone(UTC)
    clock = FakeClock(request.clock_directory)
    driver = seed_web.FamilyWebDriver(seed_web.family_runtime(), campaign)
    timezone = campaign.active_configuration.timezone
    report = drive_timeline(
        result,
        clock,
        settle=lambda: settle(since),
        driver=driver,
        evidence=lambda event: occurrence_evidence(event, campaign.pk, timezone),
    )
    return {
        "step": "drive",
        "campaign": str(campaign.pk),
        "eligible": len(result.eligible),
        "events": report.events,
        "jumps": report.jumps,
        "late_or_near": report.late,
        "settle_seconds": round(report.settle_seconds, 1),
        "counts": result.counts(),
        "stages": {stage: len(keys) for stage, keys in result.stages.items()},
        # Full precision: the check compares row timestamps against it.
        "seeded_now": report.seeded_now.isoformat(),
    }


def check_step(request, configuration):
    """Phase 3: the invariant DO block under the offline migration identity."""
    from django.db import connection

    from parishkit.stewardship.offline_boundaries import admit_offline_service
    from parishkit.stewardship.operator_commands import configure_operator_database

    if admit_offline_service(configuration) is not ServiceRole.MIGRATION:
        raise SeedRefused("The invariant check requires the migration profile.")
    configure_operator_database(configuration)
    result = campaign_timeline(request, current_campaign())
    seeded_now = (
        datetime.fromisoformat(request.seeded_now)
        if request.seeded_now
        else request.now
    )
    with connection.cursor() as cursor:
        cursor.execute(
            invariant_sql(seeded_now, result.counts(), start=result.calendar.start)
        )
    return {"step": "check", "result": "invariants hold"}


def finish_step(request, configuration):
    """Phase 4: the real full refresh that releases the late-added Family."""
    configure_web_process(configuration)
    since = datetime.now(UTC) - timedelta(minutes=1)
    web = admin(request)
    seed_web.request_full_refresh(web, seed_web.admin_runtime())
    # The promoted late-added Family's missed Reminders are coalesced into
    # one recovery send by the real reconciliation; that is the point of
    # this phase, not a failure.
    waited = settle(since, tolerate=frozenset({"coalesced"}))
    return {"step": "finish", "refresh_wait_seconds": round(waited, 1)}


def execute_local_seed(args):
    """Console entry: refuse outside LOCAL before any connection, then run a step.

    The profile is resolved exactly as every other service resolves it
    (``--config``, ``PARISHKIT_STEWARDSHIP_PROFILE``, ``--profile``); the
    deployment must be LOCAL. Refusals print fixed wording: the inputs may
    hold paths the operator did not mean to show.
    """
    from parishkit.stewardship.observability import configure_logging

    configure_logging()
    try:
        deployment = load_deployment(
            Path(args.config) if args.config is not None else None,
            overrides=(
                {"PARISHKIT_STEWARDSHIP_PROFILE": args.profile}
                if args.profile is not None
                else None
            ),
        )
        local = deployment.profile is DeploymentProfile.LOCAL
    except (ConfigError, OSError):
        local = False
    if not local:
        print("ERROR: the local seeder runs only in the local profile", file=sys.stderr)
        return 2
    try:
        request = SeedRequest.parse(args)
    except SeedRefused as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    started = time.monotonic()
    try:
        if request.step == "timeline":
            document = timeline_summary(request)
        else:
            document = run_step(request, deployment)
    except SeedFailed as error:
        LOGGER.error("seed step %s failed: %s", request.step, error)
        print(f"ERROR: seed step {request.step} failed; see the log", file=sys.stderr)
        return 1
    except SeedRefused as error:
        LOGGER.error("seed step %s refused: %s", request.step, error)
        print(f"ERROR: seed step {request.step} refused; see the log", file=sys.stderr)
        return 2
    except Exception as error:
        LOGGER.exception("seed step %s stopped by an unexpected error", request.step)
        print(
            f"ERROR: seed step {request.step} stopped by an unexpected error "
            f"({type(error).__name__}); see the log",
            file=sys.stderr,
        )
        return 1
    document["elapsed_seconds"] = round(time.monotonic() - started, 1)
    print(json.dumps(document, sort_keys=True))
    return 0
