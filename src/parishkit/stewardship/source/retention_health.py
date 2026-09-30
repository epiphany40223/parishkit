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
"""

from datetime import timedelta

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
    latest = sorted(runs.values(), key=lambda run: max(run.values()), reverse=True)[
        :FAILING_RUNS
    ]
    return len(latest) == FAILING_RUNS and all(
        "skip" in run and ("evidence" not in run or run["skip"] > run["evidence"])
        for run in latest
    )


def observe_retention_health():
    """Open (WARNING, escalating if it persists) or resolve the retention episode."""
    if retention_failing():
        record_observation(
            IncidentKind.SOURCE_RETENTION_FAILING,
            IncidentLevel.WARNING,
            policy=configured_policy(),
        )
    else:
        record_recovery(IncidentKind.SOURCE_RETENTION_FAILING)
