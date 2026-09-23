"""One read-only load check at the parish's real Family count.

The v1 launch requires one load check against the validation deployment at
the parish's actual population; synthetic scale fixtures and complete latency
budgets stay deferred. The check runs inside an admitted web container under
the web's own restricted SQL login, only while the Testing Family portal is
open for the current campaign, and times the reads the real pages perform:
the Family form inputs for a sample of portal-eligible households (serially,
then on a bounded number of threads) and the first page of each Admin report.
Every measured read runs in its own read-only campaign guard and every other
read in a bounded READ ONLY transaction, so nothing is written, no mail is
sent, no session or baseline is created and Valkey is never contacted. The
output is one JSON document of fixed keys, counts, seconds and timestamps; a
refusal is one generic line.
"""

import json
import logging
import math
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from threading import Lock, Thread
from time import monotonic, perf_counter
from uuid import uuid4

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .jobs.delivery_states import TERMINAL_DELIVERY_STATES, DeliveryState
from .observability import Event, configure_logging, emit, emit_failure
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupBusy, StartupLease

DEFAULT_SAMPLES = 200
SAMPLE_BOUNDS = (1, 1000)
DEFAULT_CONCURRENCY = 4
CONCURRENCY_CAP = 8
REPORT_RUNS = 20
# Bounded stopping: a phase ends after this many unavailable reads and the
# whole run at the wall-clock cap; whatever is left is reported as not run.
MAX_PHASE_FAILURES = 5
RUN_SECONDS_CAP = 15 * 60
# Architecture reference targets: ordinary cached pages under 2 s at p95,
# filtered report first pages under 3 s, measured next to the 5,000-Family
# reference population.
FORM_TARGET_SECONDS = 2.0
STATISTICS_TARGET_SECONDS = 2.0
REPORT_TARGET_SECONDS = 3.0
REFERENCE_FAMILIES = 5000
REFERENCE_MEMBERS = 10000
# Per-sample outcomes other than a timing: an unavailable read is None; a
# Family the portal no longer admits is skipped; a sample the bounded stop
# never reached is not run. The sentinels themselves are only counted, never
# listed, but the same words are also fixed status values ({"status":
# "skipped"}, {"status": "not_run"}), so both stay in FIXED_WORDS.
SKIPPED = "skipped"
NOT_RUN = "not_run"
# Occurrence outcomes that no longer change. The schedule model owns this
# vocabulary; it is repeated here so the pure aggregation stays free of Django
# and pinned equal by a unit test.
OCCURRENCE_STATES = frozenset(
    {
        "pending",
        "running",
        "delivery_unknown",
        "succeeded",
        "skipped",
        "coalesced",
        "failed",
    }
)
TERMINAL_OCCURRENCE_STATES = frozenset({"succeeded", "skipped", "coalesced", "failed"})
MESSAGE_STATES = frozenset(state.value for state in DeliveryState)
TERMINAL_MESSAGE_STATES = frozenset(state.value for state in TERMINAL_DELIVERY_STATES)
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
            "web_connection_headroom",
            "database_connections",
            "background_connections",
            "family_form_inputs",
            "samples",
            "concurrency",
            "threads",
            "serial",
            "concurrent",
            "runs",
            "failures",
            "skipped",
            "not_run",
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


class LoadCheckRefused(ConfigError):
    """Admission failed before or during the run; no verdict may be published."""


class InvalidOptions(LoadCheckRefused):
    """--samples or --concurrency is outside its bounds."""


class NoConnectionHeadroom(LoadCheckRefused):
    """The deployment's rollout_overlap leaves the web login no spare connection."""


class NoEligibleFamilies(LoadCheckRefused):
    """The current campaign has no portal-eligible Family, so nothing to measure."""


class SourceChanged(RuntimeError):
    """The promoted source moved during the run, so the samples mix populations."""


class PortalClosed(RuntimeError):
    """The Testing Family portal closed mid-run; later samples measure nothing."""


class CampaignUnavailable(RuntimeError):
    """A post-measurement campaign read was refused or timed out; run it again."""


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
        raise InvalidOptions("Load check options must be bounded positive integers.")
    return int(value)


@dataclass(frozen=True)
class _Budgeted:
    """The one attribute database provisioning reads from a deployment."""

    runtime_budget: object


def web_headroom(budget):
    """Spare web-login connections while the web service itself is running.

    Provisioning bounds the web login for ``rollout_overlap`` simultaneous
    service generations; one generation is serving and holds its whole share,
    so only the remaining generations' share is free for the check's readers.
    The same function computes the limit, so the two cannot drift apart.
    """
    from .database_provisioning import role_limit

    limit = role_limit(_Budgeted(budget), ServiceRole.WEB)
    return limit - limit // budget.rollout_overlap


def effective_concurrency(requested, web_threads, headroom):
    """Never run more readers than web threads, spare connections or the cap."""
    return max(1, min(requested, web_threads, headroom, CONCURRENCY_CAP))


def admit_runtime_state(mode, current_campaign_id):
    """The v1 check is a Testing-mode rehearsal against the current campaign only."""
    if mode != "testing" or current_campaign_id is None:
        raise LoadCheckRefused(
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
    """The nearest-rank percentile of ascending values: the ceil(n*q)-th value."""
    index = math.ceil(len(values) * quantile) - 1
    return values[min(len(values) - 1, max(0, index))]


def summarize(timings, *, target):
    """Describe one measured operation: p50/p95/max seconds, outcomes and verdict.

    A ``None`` timing is a read that was unavailable; ``SKIPPED`` a sample
    whose Family alone lost its eligibility; ``NOT_RUN`` a sample the bounded
    stop never reached. Any unavailable or unreached sample fails the
    operation: a refused or unmeasured page is worse than a slow one. Skips
    are tolerated only while at least half of the chosen samples were still
    measured (the half rule); fewer measured samples cannot describe the
    population, so the operation fails.
    """
    measured = sorted(value for value in timings if type(value) in {int, float})
    not_run = timings.count(NOT_RUN)
    half_measured = 2 * len(measured) >= len(timings)
    result = {
        "runs": len(timings) - not_run,
        "failures": timings.count(None),
        "skipped": timings.count(SKIPPED),
        "not_run": not_run,
        "target_seconds": target,
        "p50": round(nearest_rank(measured, 0.5), 4) if measured else None,
        "p95": round(nearest_rank(measured, 0.95), 4) if measured else None,
        "max": round(measured[-1], 4) if measured else None,
    }
    result["pass"] = (
        bool(measured)
        and half_measured
        and result["failures"] == 0
        and not_run == 0
        and result["p95"] < target
    )
    return result


def form_section(serial, concurrent, *, samples, concurrency, threads):
    """Combine the serial and concurrent Family form input measurements."""
    first = summarize(serial, target=FORM_TARGET_SECONDS)
    second = summarize(concurrent, target=FORM_TARGET_SECONDS)
    return {
        "samples": samples,
        "concurrency": concurrency,
        "threads": threads,
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
    """Aggregate one Testing initial-invitation run from its durable rows.

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


def build_document(
    *, population, budget, headroom, form, reports, invitation_run, background
):
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
            "web_connection_headroom": headroom,
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


class Stopper:
    """Bound one phase: stop after repeated unavailable reads or at the deadline.

    ``deadline`` is a ``monotonic()`` instant shared by every phase of the run,
    so the whole check ends at the wall-clock cap; the failure count is per
    phase. Threads share one instance, hence the lock.
    """

    def __init__(self, deadline):
        self.deadline = deadline
        self.failures = 0
        self._lock = Lock()

    def stopped(self):
        """True once the phase must stop; unreached samples become NOT_RUN."""
        return self.failures >= MAX_PHASE_FAILURES or monotonic() >= self.deadline

    def record(self, value):
        """Count an unavailable read towards the phase's failure stop."""
        if value is None:
            with self._lock:
                self.failures += 1


def phase(items, operation, stop):
    """Apply ``operation`` to ``items`` in order until the stopper says stop."""
    results = []
    for item in items:
        if stop.stopped():
            results.append(NOT_RUN)
            continue
        value = operation(item)
        stop.record(value)
        results.append(value)
    return results


def run_threads(items, workers, operation, *, release, stop):
    """Apply ``operation`` to ``items`` on worker threads, keeping item order.

    Each thread takes every ``workers``-th item, like a web thread serving its
    share of requests; every guarded read closes its connection on exit
    (``CampaignReadGuard.close``) and the next reconnects, which is exactly
    web's CONN_MAX_AGE=0 behaviour, so the thread count bounds simultaneous
    connections. ``release`` closes whatever connection a thread still holds.
    Returns the results and the number of threads actually started; a worker's
    unexpected error is re-raised here instead of vanishing with the thread.
    """
    results, errors = [None] * len(items), []

    def run(offset):
        try:
            for index in range(offset, len(items), workers):
                if stop.stopped():
                    results[index] = NOT_RUN
                    continue
                results[index] = operation(items[index])
                stop.record(results[index])
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
    return results, len(threads)


# Django reads. Everything below runs under the admitted web login; every
# campaign read is inside its own READ ONLY guard transaction and every other
# read inside a bounded READ ONLY transaction.


@dataclass(frozen=True)
class Scope:
    """The open Testing campaign, the coherent runtime row and its rehearsal epoch."""

    campaign: object
    runtime: object
    epoch_id: object


@dataclass(frozen=True)
class Source:
    """The promoted snapshot identity and generation the whole run must share."""

    snapshot_id: object
    generation: int


def _no_authorization(guard):
    """The form guards re-admit the portal and Family themselves, inside the guard."""


def _no_abort():
    """There is no HTTP transport to terminate on a guard deadline."""


def _guard(campaign_id, *, authorize=_no_authorization):
    """One response-lifetime read guard: READ ONLY, timeouts, shared purge lock."""
    from .campaigns.read_guards import CampaignReadGuard

    return CampaignReadGuard([campaign_id], authorize=authorize, abort=_no_abort)


@contextmanager
def bounded_read():
    """A READ ONLY transaction with the guard's interactive statement/lock limits.

    Reads that are not campaign-scoped (the runtime pointer, background task
    counts) must still never run unbounded in autocommit.
    """
    from django.db import connection, transaction

    from .campaigns.read_guards import DEFAULT_LIMITS

    with transaction.atomic(durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            for name, seconds in (
                ("lock_timeout", DEFAULT_LIMITS.lock_seconds),
                ("statement_timeout", DEFAULT_LIMITS.interactive_seconds),
            ):
                cursor.execute(
                    "SELECT set_config(%s, %s, true)", [name, str(seconds * 1000)]
                )
        yield


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
    """Seconds for one guarded read, None when unavailable, SKIPPED when declined."""
    started = perf_counter()
    try:
        if operation() is SKIPPED:
            return SKIPPED
    except _failure_types():
        return None
    return perf_counter() - started


def current_campaign_id():
    """The current campaign pointer, or a refusal outside Testing."""
    from .accounts.runtime_models import SystemConfiguration

    row = SystemConfiguration.objects.values("mode", "current_campaign_id").get()
    admit_runtime_state(row["mode"], row["current_campaign_id"])
    return row["current_campaign_id"]


def open_scope(store, campaign_id):
    """Admit the run only while the Testing Family portal is open, as login does.

    This composes the same non-mutating predicates the Family login's scope
    check uses (``coherent_configuration``, ``portal_admitted`` on current
    facts and the domain clock, the campaign credential row's go-live and
    population gates, an active rehearsal epoch) plus the export admission's
    work-gate rule, without the login's service object or any row lock.
    """
    from .accounts.configuration_installation import coherent_configuration
    from .campaigns.credential_models import CampaignCredentialState
    from .campaigns.lifecycle import portal_admitted
    from .campaigns.runtime import _now, campaign_facts
    from .campaigns.runtime_models import CampaignWorkGate

    runtime = coherent_configuration(store)
    admit_runtime_state(runtime.mode, runtime.current_campaign_id)
    campaign = runtime.current_campaign
    if campaign.pk != campaign_id or not portal_admitted(
        campaign_facts(campaign, runtime), _now()
    ):
        raise LoadCheckRefused("The Testing Family portal is not open.")
    scope = (
        CampaignCredentialState.objects.filter(
            campaign=campaign, go_live_gate=False, population_dirty=False
        )
        .select_related("rehearsal_epoch")
        .first()
    )
    if (
        scope is None
        or scope.rehearsal_epoch_id is None
        or scope.rehearsal_epoch.state != "active"
        or CampaignWorkGate.objects.filter(campaign=campaign)
        .exclude(state="released")
        .exists()
    ):
        raise LoadCheckRefused("The Testing Family portal is not open.")
    return Scope(campaign, runtime, scope.rehearsal_epoch_id)


def portal_open(store, campaign_id):
    """The per-sample recheck: the same admission, answered instead of raised."""
    try:
        open_scope(store, campaign_id)
    except ConfigError:
        return False
    return True


def family_admitted(campaign_id, family_duid):
    """A sampled Family is still one the form would serve.

    This is exactly the Family predicate of the login and form admission
    (``campaign`` and ``portal_eligible``); the source generation is not
    compared there either, so a promotion awaiting reconciliation does not
    skip every Family. Whole-run source coherence is checked separately.
    """
    from .campaigns.credential_models import FamilyCampaign

    return FamilyCampaign.objects.filter(
        campaign_id=campaign_id, family_duid=family_duid, portal_eligible=True
    ).exists()


def current_source():
    """The promoted, uncompacted current snapshot; a plain read, never FOR UPDATE."""
    from .source.models import SourceCurrent, SourceSnapshot

    current = SourceCurrent.objects.values("snapshot_id", "generation").get()
    snapshot = (
        SourceSnapshot.objects.only("id", "state", "compacted_at")
        .filter(pk=current["snapshot_id"])
        .first()
        if current["snapshot_id"] is not None
        else None
    )
    if snapshot is None or snapshot.state != "promoted" or snapshot.compacted_at:
        raise LoadCheckRefused("The load check requires a promoted source snapshot.")
    return Source(snapshot.pk, current["generation"])


def require_same_source(source):
    """READ COMMITTED guards see a promotion; a moved source voids the samples."""
    try:
        unchanged = current_source() == source
    except LoadCheckRefused:
        unchanged = False
    if not unchanged:
        raise SourceChanged("The promoted source changed during the load check.")


def family_campaign_rows(campaign_id):
    """The set the scheduler traverses: every Family row of the current campaign."""
    from .campaigns.credential_models import FamilyCampaign

    return FamilyCampaign.objects.filter(campaign_id=campaign_id).count()


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
    applied = scope.runtime.active_configuration
    return {
        "configuration": scope.campaign.active_configuration.values,
        "document": applied.canonical_document,
        "campaign_id": scope.campaign.pk,
        "parish_name": applied.parish.name,
    }


def time_form_inputs(store, scope, source, arguments, family_duid):
    """Time one Family's form inputs read inside its own read guard.

    Inside the guard the portal and the Family are re-admitted first, as the
    form does before it reads. A closed portal ends the whole run: nothing
    after it could be measured, so no verdict may be published. A single
    Family no longer admitted is skipped, not counted as a slow or failed page.
    """
    from .responses.source_inputs import load_census_inputs

    campaign_id = scope.campaign.pk

    def read():
        with _guard(campaign_id):
            if not portal_open(store, campaign_id):
                raise PortalClosed("The Testing Family portal closed mid-run.")
            if not family_admitted(campaign_id, family_duid):
                return SKIPPED
            load_census_inputs(source.snapshot_id, family_duid, **arguments)

    return timed(read)


def measure_reports(scope, deadline):
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
                parish_name=scope.runtime.active_configuration.parish.name,
                configuration=campaign.active_configuration.values,
                page_size=PAGE_SIZE,
            )

    def information_read():
        with _guard(campaign.pk, authorize=authorize):
            information_page(campaign.pk, InformationQuery(disposition="all"))

    def runs(operation, target):
        timings = phase(
            range(REPORT_RUNS), lambda _: timed(operation), Stopper(deadline)
        )
        return summarize(timings, target=target)

    reports = {"statistics": runs(statistics_read, STATISTICS_TARGET_SECONDS)}
    if "financial" in campaign.active_configuration.values.get("modules", ()):
        reports["financial_first_page"] = runs(financial_read, REPORT_TARGET_SECONDS)
    else:
        reports["financial_first_page"] = {"status": "skipped"}
    reports["information_first_page"] = runs(information_read, REPORT_TARGET_SECONDS)
    return reports, statistics[-1] if statistics else None


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


def invitation_rows(campaign_id, epoch_id):
    """Planning, preparation and dispatch rows of the current Testing run only.

    The active rehearsal epoch is the Testing run's identity: every outbox row
    carries it, so messages are selected by it directly. Occurrences do not
    record an epoch; they are attributed through their outbox binding, plus
    any occurrence still unbound and unfinished, which can only belong to the
    run in progress. Earlier rehearsals' rows are left out.
    """
    from django.db.models import Q

    from .campaigns.schedule_models import ScheduleOccurrence
    from .jobs.outbox_models import OutboxMessage

    messages = list(
        OutboxMessage.objects.filter(
            campaign_id=campaign_id,
            purpose="initial",
            mode="testing",
            rehearsal_epoch_id=epoch_id,
        ).values("id", "created_at", "finished_at", "state")
    )
    occurrences = list(
        ScheduleOccurrence.objects.filter(
            definition__campaign_id=campaign_id,
            definition__kind="initial",
            mode="testing",
        )
        .filter(
            Q(outbox_id__in=[row["id"] for row in messages])
            | Q(outbox_id__isnull=True, state__in=("pending", "running"))
        )
        .values("target", "created_at", "due_at", "state")
    )
    for row in messages:
        del row["id"]
    return occurrences, messages


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


def measure(budget, store, *, samples, concurrency):
    """Run every measurement against the open Testing campaign, bounded in time."""
    from django.db import DatabaseError

    from .campaigns.read_guards import ReadUnavailable

    headroom = web_headroom(budget)
    if headroom < 1:
        raise NoConnectionHeadroom(
            "The web login has no spare connection for a reader."
        )
    workers = effective_concurrency(concurrency, budget.web_threads, headroom)
    deadline = monotonic() + RUN_SECONDS_CAP
    with bounded_read():
        campaign_id = current_campaign_id()
        start = background_counts()
    try:
        with _guard(campaign_id):
            scope = open_scope(store, campaign_id)
            source = current_source()
            rows = family_campaign_rows(campaign_id)
            households = household_sizes(campaign_id, source.snapshot_id)
    except (ReadUnavailable, DatabaseError):
        # Admission, not measurement: a purging campaign, a lock timeout on
        # the purge barrier or a cancelled statement means the check cannot
        # start, so it is refused rather than reported as an error.
        raise LoadCheckRefused("Campaign reads are unavailable.") from None
    if not households:
        raise NoEligibleFamilies("No portal-eligible Family to measure.")
    chosen = choose_samples(households, samples)
    read = partial(time_form_inputs, store, scope, source, form_input_arguments(scope))
    serial = phase(chosen, read, Stopper(deadline))
    concurrent, threads = run_threads(
        chosen, workers, read, release=_close_connection, stop=Stopper(deadline)
    )
    try:
        with _guard(campaign_id):
            require_same_source(source)
        reports, statistics = measure_reports(scope, deadline)
        with _guard(campaign_id):
            timeline = invitation_timeline(
                *invitation_rows(campaign_id, scope.epoch_id)
            )
            require_same_source(source)
    except (ReadUnavailable, DatabaseError):
        # The campaign became unavailable after the samples were taken; the
        # samples are fine, but the run cannot finish, so the operator reruns.
        raise CampaignUnavailable("Campaign reads were lost mid-run.") from None
    with bounded_read():
        end = background_counts()
    return build_document(
        population=population_counts(statistics, rows, len(households)),
        budget=budget,
        headroom=headroom,
        form=form_section(
            serial,
            concurrent,
            samples=len(chosen),
            concurrency=workers,
            threads=threads,
        ),
        reports=reports,
        invitation_run=timeline,
        background={"start": start, "end": end},
    )


def load_check_command(configuration, *, samples, concurrency):
    """Admit exactly as the detailed health check does, then measure.

    The web's own restricted SQL login and read-only mounts are required; the
    shared lifecycle lease keeps an offline operator from racing the reads. No
    Valkey client is built: nothing here needs the broker.
    """
    from django.db import connections

    from .accounts.authority import AuthorityStore
    from .accounts.configuration_schema import validate_sections
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
                configuration.runtime_budget,
                AuthorityStore(configuration.paths["authority"], validate_sections),
                samples=samples,
                concurrency=concurrency,
            )
        finally:
            connections.close_all()


def _refusal(error):
    """Fixed refusal wording chosen by exception type, never by its text.

    The specific refusals point the operator at the one thing to change; the
    remaining refusals share the deployment-side checklist.
    """
    if isinstance(error, InvalidOptions):
        return (
            "invalid --samples or --concurrency; --samples takes 1 to 1000 and "
            "--concurrency 1 to 8"
        )
    if isinstance(error, NoConnectionHeadroom):
        return (
            "no spare web database connections; the deployment's rollout_overlap "
            "leaves no headroom for a reader"
        )
    if isinstance(error, NoEligibleFamilies):
        return "no portal-eligible Families in the current campaign; nothing to measure"
    return (
        "load check refused; verify the web profile, Testing mode, an open "
        "current campaign and a promoted source"
    )


def execute_load_check(args):
    """Console entry: one fixed JSON document, or one generic line per outcome.

    Refusals (configuration, admission, a closed portal) and a source that
    moved mid-run are classified apart from an unexpected error, so the
    operator knows whether to fix the deployment, simply rerun, or read the
    process log.
    """
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
    except SourceChanged:
        emit(Event.FACT_DRIFT, level=logging.WARNING)
        print("ERROR: source changed during the check; run it again", file=sys.stderr)
        return 2
    except PortalClosed:
        emit(Event.STARTUP_REJECTED, level=logging.WARNING)
        print(
            "ERROR: the Testing Family portal closed during the check; run it again",
            file=sys.stderr,
        )
        return 2
    except CampaignUnavailable:
        emit(Event.STARTUP_REJECTED, level=logging.WARNING)
        print(
            "ERROR: the campaign became unavailable during the check; run it again",
            file=sys.stderr,
        )
        return 2
    except (ConfigError, PermissionError) as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print("ERROR: " + _refusal(error), file=sys.stderr)
        return 2
    except Exception as error:
        emit_failure(error, event=Event.TASK_FAILED)
        print(
            "ERROR: load check stopped by an unexpected error; see the process log",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(document, sort_keys=True))
    return 0 if document["result"] == "pass" else 1
