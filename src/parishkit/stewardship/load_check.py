"""One read-only load check at the parish's real Family count.

The v1 launch requires one load check against the validation deployment at
the parish's actual population; synthetic scale fixtures and complete latency
budgets stay deferred. The check runs inside an admitted web container under
the web's own restricted SQL login, only in Testing mode with a current
campaign, and times the reads the real pages perform: the Family form inputs
for a sample of households (serially, then concurrently) and the first page of
each Admin report. Every measured read runs in its own read-only campaign
guard, so nothing is written, no mail is sent, no session or baseline is
created and Valkey is never contacted. The output is one JSON document of
fixed keys, counts, seconds and timestamps; a refusal is one generic line.
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from threading import Thread
from time import perf_counter
from uuid import uuid4

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .jobs.delivery_states import TERMINAL_DELIVERY_STATES
from .observability import Event, configure_logging, emit_failure
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupBusy, StartupLease

DEFAULT_SAMPLES = 200
SAMPLE_BOUNDS = (1, 1000)
DEFAULT_CONCURRENCY = 4
CONCURRENCY_CAP = 8
REPORT_RUNS = 20
# Architecture reference targets: ordinary cached pages under 2 s at p95,
# filtered report first pages under 3 s, measured next to the 5,000-Family
# reference population.
FORM_TARGET_SECONDS = 2.0
STATISTICS_TARGET_SECONDS = 2.0
REPORT_TARGET_SECONDS = 3.0
REFERENCE_FAMILIES = 5000
REFERENCE_MEMBERS = 10000
# Occurrence outcomes that no longer change; mirrors the schedule model's
# vocabulary without importing Django into the pure aggregation.
TERMINAL_OCCURRENCE_STATES = frozenset({"succeeded", "skipped", "coalesced", "failed"})
TERMINAL_MESSAGE_STATES = frozenset(state.value for state in TERMINAL_DELIVERY_STATES)
OCCURRENCE_STATES = TERMINAL_OCCURRENCE_STATES | {
    "pending",
    "running",
    "delivery_unknown",
}
MESSAGE_STATES = TERMINAL_MESSAGE_STATES | {
    "pending",
    "submitting",
    "retry_wait",
    "delivery_unknown",
}
# The complete output vocabulary. Anything else in the document is a bug that
# must stop the print, because a stray value could be a DUID, name or address.
FIXED_WORDS = frozenset(
    {"load", "pass", "fail", "not_run", "complete", "in_progress", "skipped"}
)
ALLOWED_KEYS = (
    frozenset(
        {
            "check",
            "result",
            "population",
            "family_campaign_rows",
            "portal_eligible_families",
            "active_families",
            "active_members",
            "deliverable_email",
            "reference_families",
            "reference_members",
            "runtime_budget",
            "web_processes",
            "web_threads",
            "database_connections",
            "background_connections",
            "family_form_inputs",
            "samples",
            "concurrency",
            "serial",
            "concurrent",
            "runs",
            "failures",
            "target_seconds",
            "p50",
            "p95",
            "max",
            "pass",
            "reports",
            "statistics",
            "financial_first_page",
            "information_first_page",
            "status",
            "invitation_run",
            "families",
            "earliest_due_at",
            "planning",
            "preparation",
            "dispatch",
            "first",
            "last",
            "seconds",
            "count",
            "per_minute",
            "due_to_last_terminal_seconds",
            "occurrence_states",
            "message_states",
            "background",
            "start",
            "end",
            "queued",
            "running",
        }
    )
    | OCCURRENCE_STATES
    | MESSAGE_STATES
)


# Pure helpers: option admission, sampling, summaries and output safety. None
# of these touch Django, so the unit tests exercise them with plain values.


def bounded_option(value, *, default, bounds):
    """Parse one optional integer option without echoing the supplied text."""
    if value is None:
        return default
    low, high = bounds
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdecimal()
        or not low <= int(value) <= high
    ):
        raise ConfigError("Load check options must be bounded positive integers.")
    return int(value)


def effective_concurrency(requested, web_threads):
    """Never run more readers than the web's own thread budget or the fixed cap."""
    return max(1, min(requested, web_threads, CONCURRENCY_CAP))


def admit_runtime_state(mode, current_campaign_id):
    """The v1 check is a Testing-mode rehearsal against the current campaign only."""
    if mode != "testing" or current_campaign_id is None:
        raise ConfigError(
            "The load check requires Testing mode and a current campaign."
        )


def choose_samples(households, count):
    """Pick the largest households first, then an even spread across id order.

    ``households`` pairs each Family DUID with its snapshot Member count. Big
    households are the expensive form reads; the spread keeps the sample from
    describing only them. The choice is deterministic so two runs on the same
    population time the same Families.
    """
    by_size = sorted(households, key=lambda item: (-item[1], item[0]))
    chosen = [duid for duid, _ in by_size[: (count + 1) // 2]]
    taken = set(chosen)
    remaining = sorted(duid for duid, _ in households if duid not in taken)
    needed = min(count - len(chosen), len(remaining))
    if needed > 0:
        step = len(remaining) / needed
        chosen.extend(remaining[int(index * step)] for index in range(needed))
    return chosen


def nearest_rank(values, quantile):
    """The nearest-rank percentile of ascending values, as the CI budgets use."""
    return values[max(0, int(len(values) * quantile) - 1)]


def summarize(timings, *, target):
    """Describe one measured operation: p50/p95/max seconds, failures and verdict.

    A ``None`` timing is a sample that was refused or timed out. Any failure
    fails the operation: a refused page is worse than a slow one.
    """
    measured = sorted(value for value in timings if value is not None)
    failures = len(timings) - len(measured)
    result = {
        "runs": len(timings),
        "failures": failures,
        "target_seconds": target,
        "p50": round(nearest_rank(measured, 0.5), 4) if measured else None,
        "p95": round(nearest_rank(measured, 0.95), 4) if measured else None,
        "max": round(measured[-1], 4) if measured else None,
    }
    result["pass"] = bool(measured) and failures == 0 and result["p95"] < target
    return result


def form_section(serial, concurrent, *, samples, concurrency):
    """Combine the serial and concurrent Family form input measurements."""
    first = summarize(serial, target=FORM_TARGET_SECONDS)
    second = summarize(concurrent, target=FORM_TARGET_SECONDS)
    return {
        "samples": samples,
        "concurrency": concurrency,
        "serial": first,
        "concurrent": second,
        "failures": first["failures"] + second["failures"],
        "target_seconds": FORM_TARGET_SECONDS,
        "pass": first["pass"] and second["pass"],
    }


def spread(instants):
    """First and last instant, their distance and the per-minute rate.

    The rate is undefined for a single instant or an instantaneous burst, so it
    is reported as null rather than an inflated number.
    """
    if not instants:
        return None
    first, last = min(instants), max(instants)
    seconds = (last - first).total_seconds()
    return {
        "first": first.isoformat(),
        "last": last.isoformat(),
        "seconds": round(seconds, 3),
        "count": len(instants),
        "per_minute": round(len(instants) * 60 / seconds, 2) if seconds > 0 else None,
    }


def state_counts(rows):
    """Count rows per state, keyed by the fixed state vocabulary only."""
    result = {}
    for row in rows:
        result[row["state"]] = result.get(row["state"], 0) + 1
    return result


def invitation_timeline(occurrences, messages):
    """Aggregate a Testing initial-invitation run from its durable rows.

    Occurrences carry ``target``, ``created_at`` (planning), ``due_at`` and
    ``state``; outbox messages carry ``created_at`` (preparation),
    ``finished_at`` (dispatch) and ``state``. Without any occurrence the run
    never happened. The result is context only; it never affects the verdict.
    """
    occurrences, messages = list(occurrences), list(messages)
    if not occurrences:
        return {"status": "not_run"}
    due = min(row["due_at"] for row in occurrences)
    finished = [
        row["finished_at"]
        for row in messages
        if row["state"] in TERMINAL_MESSAGE_STATES and row["finished_at"] is not None
    ]
    complete = all(
        row["state"] in TERMINAL_OCCURRENCE_STATES for row in occurrences
    ) and all(row["state"] in TERMINAL_MESSAGE_STATES for row in messages)
    return {
        "status": "complete" if complete else "in_progress",
        "families": len({row["target"] for row in occurrences}),
        "earliest_due_at": due.isoformat(),
        "planning": spread([row["created_at"] for row in occurrences]),
        "preparation": spread([row["created_at"] for row in messages]),
        "dispatch": spread(finished),
        "due_to_last_terminal_seconds": round((max(finished) - due).total_seconds(), 3)
        if finished
        else None,
        "occurrence_states": state_counts(occurrences),
        "message_states": state_counts(messages),
    }


def build_document(*, population, budget, form, reports, invitation_run, background):
    """Assemble the output and decide the verdict from the measured sections."""
    passed = form["pass"] and all(
        section.get("status") == "skipped" or section.get("pass") is True
        for section in reports.values()
    )
    return {
        "check": "load",
        "result": "pass" if passed else "fail",
        "population": population,
        "runtime_budget": {
            "web_processes": budget.web_processes,
            "web_threads": budget.web_threads,
            "database_connections": budget.database_connections,
            "background_connections": budget.background_connections,
        },
        "family_form_inputs": form,
        "reports": reports,
        "invitation_run": invitation_run,
        "background": background,
    }


def _timestamp(value):
    """Only ISO-8601 instants are acceptable free text in the output."""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def safe_document(document):
    """Refuse to print anything but fixed keys, numbers, booleans and timestamps.

    Same rule as smoke/health: no DUID, name, address, code, token, SQL or
    exception text can reach the console, even through a future refactor that
    adds a field by accident.
    """
    if isinstance(document, dict):
        for key, value in document.items():
            if key not in ALLOWED_KEYS:
                raise ValueError("Unexpected load check key.")
            safe_document(value)
    elif isinstance(document, list):
        for value in document:
            safe_document(value)
    elif isinstance(document, str):
        if document not in FIXED_WORDS and not _timestamp(document):
            raise ValueError("Unexpected load check text.")
    elif document is not None and type(document) not in {int, float, bool}:
        raise ValueError("Unexpected load check value.")


def run_threads(items, workers, operation, *, release):
    """Apply ``operation`` to ``items`` on worker threads, keeping item order.

    Each thread owns one database connection for its whole share of the work,
    like a web thread does, and calls ``release`` when finished. A worker's
    unexpected error is re-raised here instead of vanishing with the thread.
    """
    results, errors = [None] * len(items), []

    def run(offset):
        try:
            for index in range(offset, len(items), workers):
                results[index] = operation(items[index])
        except BaseException as error:
            errors.append(error)
        finally:
            release()

    threads = [
        Thread(target=run, args=(offset,), daemon=True)
        for offset in range(min(workers, len(items)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    return results


# Django reads. Everything below runs under the admitted web login; every
# campaign read is inside its own READ ONLY guard transaction.


@dataclass(frozen=True)
class Scope:
    """The current campaign, the runtime row and the promoted snapshot identity."""

    campaign: object
    system: object
    snapshot_id: object


def _no_authorization(guard):
    """Family form reads have no report principal to re-check inside the guard."""


def _no_abort():
    """There is no HTTP transport to terminate on a guard deadline."""


def _guard(campaign_id, *, authorize=_no_authorization):
    """One response-lifetime read guard: READ ONLY, timeouts, shared purge lock."""
    from .campaigns.read_guards import CampaignReadGuard

    return CampaignReadGuard([campaign_id], authorize=authorize, abort=_no_abort)


def _close_connection():
    """Release a worker thread's own Django connection."""
    from django.db import connection

    connection.close()


def _failure_types():
    """Outcomes a real page would report as unavailable, counted, not raised."""
    from django.db import DatabaseError

    from .campaigns.read_guards import ReadUnavailable
    from .reports.statistics import StatisticsUnavailable
    from .responses.inputs import FormInputsUnavailable

    return (
        ReadUnavailable,
        FormInputsUnavailable,
        StatisticsUnavailable,
        DatabaseError,
    )


def timed(operation):
    """Seconds for one guarded read, or None when the read was unavailable."""
    started = perf_counter()
    try:
        operation()
    except _failure_types():
        return None
    return perf_counter() - started


def current_scope():
    """The runtime row and current campaign, or a refusal outside Testing."""
    from .accounts.runtime_models import SystemConfiguration

    system = SystemConfiguration.objects.select_related(
        "active_configuration__parish", "current_campaign__active_configuration"
    ).get()
    admit_runtime_state(system.mode, system.current_campaign_id)
    return system, system.current_campaign


def promoted_snapshot():
    """The promoted, uncompacted current snapshot; a plain read, never FOR UPDATE."""
    from .source.models import SourceCurrent, SourceSnapshot

    snapshot_id = SourceCurrent.objects.values_list("snapshot_id", flat=True).get()
    snapshot = (
        SourceSnapshot.objects.only("id", "state", "compacted_at")
        .filter(pk=snapshot_id)
        .first()
        if snapshot_id is not None
        else None
    )
    if snapshot is None or snapshot.state != "promoted" or snapshot.compacted_at:
        raise ConfigError("The load check requires a promoted source snapshot.")
    return snapshot.pk


def household_sizes(campaign_id, snapshot_id):
    """Each portal-eligible Family DUID paired with its snapshot Member count.

    Only portal-eligible Families can open the form, so only their inputs are
    a page the parish will see; an inactive Family's inputs are unavailable by
    design and would count as failures.
    """
    from django.db.models import Count

    from .campaigns.credential_models import FamilyCampaign
    from .source.version_models import SnapshotMember

    duids = list(
        FamilyCampaign.objects.filter(campaign_id=campaign_id, portal_eligible=True)
        .order_by("family_duid")
        .values_list("family_duid", flat=True)
    )
    sizes = {
        row["payload__family_key"]: row["members"]
        for row in SnapshotMember.objects.filter(snapshot_id=snapshot_id)
        .values("payload__family_key")
        .annotate(members=Count("id"))
    }
    return [(duid, sizes.get(str(duid), 0)) for duid in duids]


def form_input_arguments(scope):
    """The exact keyword arguments the Family form's baseline issuance passes."""
    applied = scope.system.active_configuration
    return {
        "configuration": scope.campaign.active_configuration.values,
        "document": applied.canonical_document,
        "campaign_id": scope.campaign.pk,
        "parish_name": applied.parish.name,
    }


def time_form_inputs(scope, arguments, family_duid):
    """Time one Family's form inputs read inside its own read guard."""
    from .responses.source_inputs import load_census_inputs

    def read():
        with _guard(scope.campaign.pk):
            load_census_inputs(scope.snapshot_id, family_duid, **arguments)

    return timed(read)


def measure_reports(scope):
    """Time each Admin report's first page as its view performs it.

    The financial and information pages take a synthetic Administrator
    principal: the SQL projections bind only the campaign, filters and page,
    so no sign-in or session is involved. Financial detail is skipped, not
    failed, when the campaign does not enable that module.
    """
    from .accounts.policy import Principal
    from .reports.financial import (
        PAGE_SIZE,
        FinancialQuery,
        financial_page,
        giving_proof,
    )
    from .reports.information import InformationQuery, information_page
    from .reports.read_admission import admit_report_read
    from .reports.statistics import calculate_statistics
    from .reports.statistics_selection import capture_statistics

    campaign = scope.campaign
    principal = Principal(uuid4(), frozenset({"administrator"}))
    statistics = []

    def authorize(guard):
        """Re-admit the campaign inside the guard exactly as the pages do."""
        admit_report_read(campaign.pk)

    def statistics_read():
        with _guard(campaign.pk, authorize=authorize):
            statistics.append(calculate_statistics(capture_statistics(campaign.pk)))

    def financial_read():
        with _guard(campaign.pk, authorize=authorize):
            financial_page(
                campaign.pk,
                FinancialQuery.parse({}),
                principal,
                proof=giving_proof(campaign),
                parish_name=scope.system.active_configuration.parish.name,
                configuration=campaign.active_configuration.values,
                page_size=PAGE_SIZE,
            )

    def information_read():
        with _guard(campaign.pk, authorize=authorize):
            information_page(campaign.pk, InformationQuery(disposition="all"))

    def runs(operation, target):
        return summarize([timed(operation) for _ in range(REPORT_RUNS)], target=target)

    reports = {"statistics": runs(statistics_read, STATISTICS_TARGET_SECONDS)}
    if "financial" in campaign.active_configuration.values.get("modules", ()):
        reports["financial_first_page"] = runs(financial_read, REPORT_TARGET_SECONDS)
    else:
        reports["financial_first_page"] = {"status": "skipped"}
    reports["information_first_page"] = runs(information_read, REPORT_TARGET_SECONDS)
    return reports, statistics[-1] if statistics else None


def family_campaign_rows(campaign_id):
    """The set the scheduler traverses: every Family row of the current campaign."""
    from .campaigns.credential_models import FamilyCampaign

    return FamilyCampaign.objects.filter(campaign_id=campaign_id).count()


def population_counts(statistics, rows, eligible):
    """The measured population next to the architecture reference figures."""
    active = statistics.active if statistics is not None else None
    return {
        "family_campaign_rows": rows,
        "portal_eligible_families": eligible,
        "active_families": active.families if active else None,
        "active_members": active.active_members if active else None,
        "deliverable_email": active.deliverable_email if active else None,
        "reference_families": REFERENCE_FAMILIES,
        "reference_members": REFERENCE_MEMBERS,
    }


def invitation_rows(campaign_id):
    """Planning, preparation and dispatch rows of a Testing initial-invitation run."""
    from .campaigns.schedule_models import ScheduleOccurrence
    from .jobs.outbox_models import OutboxMessage

    occurrences = ScheduleOccurrence.objects.filter(
        definition__campaign_id=campaign_id,
        definition__kind="initial",
        mode="testing",
    ).values("target", "created_at", "due_at", "state")
    messages = OutboxMessage.objects.filter(
        campaign_id=campaign_id, purpose="initial", mode="testing"
    ).values("created_at", "finished_at", "state")
    return list(occurrences), list(messages)


def background_counts():
    """Queued and running background work, so slow samples have their context."""
    from django.db.models import Count

    from .jobs.models import TaskRun

    result = {"queued": 0, "running": 0}
    rows = (
        TaskRun.objects.filter(state__in=tuple(result))
        .values("state")
        .annotate(count=Count("id"))
    )
    result.update({row["state"]: row["count"] for row in rows})
    return result


def measure(budget, *, samples, concurrency):
    """Run every measurement against the current Testing campaign."""
    system, campaign = current_scope()
    start = background_counts()
    with _guard(campaign.pk):
        snapshot_id = promoted_snapshot()
        rows = family_campaign_rows(campaign.pk)
        households = household_sizes(campaign.pk, snapshot_id)
    scope = Scope(campaign, system, snapshot_id)
    chosen = choose_samples(households, samples)
    workers = effective_concurrency(concurrency, budget.web_threads)
    read = partial(time_form_inputs, scope, form_input_arguments(scope))
    serial = [read(duid) for duid in chosen]
    concurrent = run_threads(chosen, workers, read, release=_close_connection)
    reports, statistics = measure_reports(scope)
    with _guard(campaign.pk):
        timeline = invitation_timeline(*invitation_rows(campaign.pk))
    return build_document(
        population=population_counts(statistics, rows, len(households)),
        budget=budget,
        form=form_section(serial, concurrent, samples=len(chosen), concurrency=workers),
        reports=reports,
        invitation_run=timeline,
        background={"start": start, "end": background_counts()},
    )


def load_check_command(configuration, *, samples, concurrency):
    """Admit exactly as the detailed health check does, then measure.

    The web's own restricted SQL login and read-only mounts are required; the
    shared lifecycle lease keeps an offline operator from racing the reads. No
    Valkey client is built: nothing here needs the broker.
    """
    from django.db import connections

    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_web import admit_lifecycle_mounts
    from .service_boundaries import admit_online_service

    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("The load check requires the admitted web profile.")
    admit_lifecycle_mounts(configuration)
    with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
        configure_operator_database(configuration)
        try:
            admit_runtime_database(configuration)
            return measure(
                configuration.runtime_budget, samples=samples, concurrency=concurrency
            )
        finally:
            connections.close_all()


def execute_load_check(args):
    """Console entry: one fixed JSON document, or one generic refusal."""
    configure_logging()
    try:
        if args.config is None:
            raise ConfigError("The load check requires an explicit configuration.")
        samples = bounded_option(
            args.samples, default=DEFAULT_SAMPLES, bounds=SAMPLE_BOUNDS
        )
        concurrency = bounded_option(
            args.concurrency, default=DEFAULT_CONCURRENCY, bounds=(1, CONCURRENCY_CAP)
        )
        document = load_check_command(
            load_deployment(args.config), samples=samples, concurrency=concurrency
        )
        safe_document(document)
    except StartupBusy:
        print(
            "ERROR: offline maintenance is in progress; retry the load check",
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: load check refused or failed; verify the web profile, Testing "
            "mode, a current campaign and a promoted source",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(document, sort_keys=True))
    return 0 if document["result"] == "pass" else 1
