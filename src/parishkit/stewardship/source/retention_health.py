"""Notice when source snapshot retention keeps being skipped.

Retention runs at the start of every ParishSoft refresh and never fails it:
any failure is logged and the next refresh tries again (``compaction``). A
cause that persists (for example an outdated guard) would otherwise stop
retention silently while the database grows. Each skipped run keeps one
durable ``source_retention_skipped`` operational log entry, and every run
that reaches retention writes compaction evidence; both carry the refresh
task's correlation ID, which identifies the run.

The worker observes this at the end of each run's retention, since only a
run changes the answer; the scheduler's idle wake-up check cannot read the
log's event column, and needs no new grant this way.

Retention that keeps stopping at its own limits (a lock, a statement or its
time budget, #827) is not skipped and logs no skip, so it is caught
separately (#833, ``retention_stalled``): the same incident opens when the
newest runs have all stopped at a limit and old work is still left undone.
"""

from datetime import timedelta

from django.db import connection
from django.db.models import Max
from django.db.models.functions import Now

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.jobs.operational_content import IncidentKind, IncidentLevel
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.operational_storage import (
    record_observation,
    record_recovery,
)
from parishkit.stewardship.observability import Event

from .snapshot_models import SourceCompactionBatch

# Consecutive skipped runs that open the incident. One skip is routine (a
# refresh that lost its lease, a brief outage); three in a row is a pattern.
FAILING_RUNS = 3
# Only this recent history is read, so the skip query uses the operational
# log's (created_at, id) index instead of scanning years of entries (#359
# review L3); the compaction evidence table is small. A streak older than
# this is not a current failure anyway.
LOOKBACK = timedelta(days=30)
# Retention that keeps stopping (#833): the newest runs that must all have
# stopped at a limit (about a day of full refreshes and quick updates), and
# how long work retention could do must have been waiting. Defaults the
# Administrator may change.
STALLED_RUNS = 12
STALLED_AFTER = timedelta(days=2)
# The events a retention stop writes (timeout log): an INFO budget or lock
# stop, or a WARNING statement timeout. The worker may read the event and
# correlation columns, not the context, so a renewal timeout in the same run
# counts too; the backlog condition is what makes the signal specific.
STOP_EVENTS = (Event.WORK_BUDGET_REACHED.value, Event.TASK_TIMED_OUT.value)
# The task types whose runs perform this retention (compact_before_refresh).
REFRESH_TASK_TYPES = ("source_refresh",)


def _runs():
    """Each run that reached retention in LOOKBACK: {correlation id: {"skip"
    or "evidence": its newest such time}}."""
    since = Now() - LOOKBACK
    runs = {}
    for source, rows in (
        (
            "skip",
            OperationalLog.objects.filter(
                event=Event.SOURCE_RETENTION_SKIPPED.value, created_at__gte=since
            ),
        ),
        ("evidence", SourceCompactionBatch.objects.filter(created_at__gte=since)),
    ):
        for run, newest in (
            rows.values("correlation_id")
            .annotate(newest=Max("created_at"))
            .values_list("correlation_id", "newest")
        ):
            runs.setdefault(run, {})[source] = newest
    return runs


def _old_backlog():
    """Whether work retention could do has waited more than STALLED_AFTER.

    Either a compacted corpus whose memberships are still not reclaimed two
    days after it was marked, or a report fact generation that is disposable
    now and was superseded by a ready one created more than two days ago
    (timed from that successor, so a just-superseded generation is not old).
    Bounded probes with the worker's own grants.
    """
    from .version_models import ENTITY_MODELS

    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp() - %s", [STALLED_AFTER])
        cutoff = cursor.fetchone()[0]
        for _, membership in ENTITY_MODELS.values():
            if membership.objects.filter(snapshot__compacted_at__lt=cutoff).exists():
                return True
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM stewardship_daily_fact_set old "
            "WHERE old.state='ready' AND EXISTS (SELECT 1 "
            "FROM stewardship_daily_fact_set newer "
            "WHERE newer.campaign_id=old.campaign_id "
            "AND newer.population_scope=old.population_scope "
            "AND newer.state='ready' AND newer.id<>old.id "
            "AND newer.created_at>=old.created_at AND newer.created_at<%s) "
            "AND stewardship_fact_disposable(old.id))",
            [cutoff],
        )
        return cursor.fetchone()[0]


def _made_progress(runs):
    """Whether any of these runs removed something: compaction evidence with
    a non-zero count, or a removed report fact generation's record. A large
    backlog being drained steadily is housekeeping, not a stall."""
    from django.db.models import Q

    from parishkit.stewardship.reports.models import FactCompactionRecord

    return (
        SourceCompactionBatch.objects.filter(correlation_id__in=runs)
        .filter(
            Q(snapshot_count__gt=0) | Q(membership_count__gt=0) | Q(payload_count__gt=0)
        )
        .exists()
        or FactCompactionRecord.objects.filter(correlation_id__in=runs).exists()
    )


def _ended_at_limit(run):
    """Whether a run's newest retention outcome is a stop: it has a stop
    entry no older than its newest evidence or skip."""
    if "stop" not in run:
        return False
    others = [moment for source, moment in run.items() if source != "stop"]
    return not others or run["stop"] >= max(others)


def retention_stalled():
    """True when the newest STALLED_RUNS runs all ended their retention at a
    limit, none of them removed anything, and old work is still undone (#833).

    A run ended at a limit when a ParishSoft refresh task's correlation id
    has a retention stop entry (STOP_EVENTS) in LOOKBACK no older than that
    run's newest evidence or skip: a retried attempt that then got through
    writes newer evidence and does not count. (A timeout later in the same
    refresh, after retention, would still count; the progress and backlog
    conditions keep that from opening the incident alone.) Runs are found
    through their skip entries and evidence, as for ``retention_failing``,
    and also through those stop entries, since a run stopped by its budget
    before its first batch writes no evidence.
    """
    from parishkit.stewardship.jobs.models import TaskRun

    since = Now() - LOOKBACK
    runs = _runs()
    refreshes = TaskRun.objects.filter(
        task_type__in=REFRESH_TASK_TYPES, created_at__gte=since
    ).values("correlation_id")
    for run, newest in (
        OperationalLog.objects.filter(
            event__in=STOP_EVENTS,
            created_at__gte=since,
            correlation_id__in=refreshes,
        )
        .values("correlation_id")
        .annotate(newest=Max("created_at"))
        .values_list("correlation_id", "newest")
    ):
        runs.setdefault(run, {})["stop"] = newest
    latest = sorted(runs.items(), key=lambda item: max(item[1].values()), reverse=True)[
        :STALLED_RUNS
    ]
    return (
        len(latest) == STALLED_RUNS
        and all(_ended_at_limit(run) for _, run in latest)
        and not _made_progress([correlation for correlation, _ in latest])
        and _old_backlog()
    )


def retention_failing():
    """True when the newest ``FAILING_RUNS`` runs that reached retention skipped it.

    A run is one refresh task's correlation ID. It reached retention if it
    logged a skip or wrote compaction evidence, and it skipped if its newest
    skip entry is newer than its newest evidence. A run whose fact cleanup
    failed logs its skip after its snapshot batches' evidence, so it counts
    as skipped; a retried attempt or a fallback full refresh that shares the
    run's correlation ID and then gets through retention writes newer
    evidence, so the run counts as a success (#359 review L2).
    """
    latest = sorted(_runs().values(), key=lambda run: max(run.values()), reverse=True)[
        :FAILING_RUNS
    ]
    return len(latest) == FAILING_RUNS and all(
        "skip" in run and ("evidence" not in run or run["skip"] > run["evidence"])
        for run in latest
    )


def observe_retention_health():
    """Open (WARNING, escalating if it persists) or resolve the retention episode.

    It opens when retention keeps being skipped, or keeps stopping at its
    limits with old work undone (#833); the first run that is neither
    resolves it.
    """
    if retention_failing() or retention_stalled():
        record_observation(
            IncidentKind.SOURCE_RETENTION_FAILING,
            IncidentLevel.WARNING,
            policy=configured_policy(),
        )
    else:
        record_recovery(IncidentKind.SOURCE_RETENTION_FAILING)
