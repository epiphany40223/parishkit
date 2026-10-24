"""Bound one full observation to its immutable attempt and staged manifest.

The compiled worker owns lease acquisition/renewal, recovery, atomic domain
effects and Task outcome. This pipeline never declares success or advances the
current pointer: validated staging is not a completed campaign refresh.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import batched
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from django.db import connection, connections

from parishkit.parishsoft import ParishSoftConfig
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.observability import Event, emit
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import (
    _scope,
    begin_refresh_attempt,
    source_step,
    verify_refresh_attempt,
)
from .canonical import InvalidSourcePayload
from .cursors import refresh_cursor
from .drop_acceptance import ACCEPTANCE_KEY, acceptor
from .loading import (
    DEFAULT_MAXIMUM_DROP_PERCENT,
    DERIVED_COUNTS,
    derived_baseline,
    derived_counts,
    load_full_source,
    maximum_drop_percent,
    recorded_contact_missing,
    valid_derived_counts,
)
from .requests import _window
from .snapshot_models import SourceCurrent, SourceSnapshot
from .snapshots import (
    analyze_staging,
    complete_snapshot,
    reconstruct_snapshot,
    stage_entities,
    validate_snapshot,
)
from .transport import source_session
from .windows import RefreshWindow
from .workgroups import (
    EVIDENCE_KEY,
    configured_name,
    keep_when_missing,
    load_reminder_workgroup,
    recorded,
    warn_if_missing,
)

# Each staging batch is one source step (attempts.source_step): a transaction
# that verifies the attempt and writes the batch. It used to hold the
# deployment-wide work-order lock (736220,1); at 500 rows that hold was
# 1.3-1.5 s on the validation host, and every mail message, page and in-flight
# check queued behind it (#394). Batches no longer take that lock (#147), but
# small ones still keep each transaction, and its task and lease row locks,
# short. stage_entities() refuses more than 500 rows, so this must stay at or
# below that.
STAGING_BATCH_ROWS = 125
# Staging progress is reported no more often than every PROGRESS_ROWS rows
# AND every PROGRESS_SECONDS. Each report is a task transition, which still
# takes the work-order lock (#147), so mail and Family forms queue behind it.
# Spaced by rows alone, a full refresh at 1,100 Families made about 20
# reports, one every 150-250 ms; the time floor cuts that to a few (#895).
# The rows floor keeps a slow staging run from reporting on every batch.
PROGRESS_ROWS = 500
PROGRESS_SECONDS = 2.0


@dataclass(frozen=True)
class RefreshInputs:
    """Copy only pinned observation inputs; never reuse an ORM admission decision."""

    window: RefreshWindow
    as_of: date
    previous_full_counts: dict | None
    previous_derived_counts: dict | None
    # The campaign's Reminder WorkGroup name (#861), and what the current
    # snapshot recorded for it; see source.workgroups.
    workgroup_name: str | None = None
    base_workgroup: dict | None = None
    # Members the last full refresh's contact list left out (#387 M4), or
    # None when that refresh did not record it; see loading.
    previous_contact_missing: int | None = None


# A load's drop check compares with every full refresh promoted in this many
# days before it, not only the last one (#387). With frequent full refreshes,
# each could drop just under the limit from the one before, so a large loss
# could build up over a few days without any single refusal.
TREND_DAYS = 7


def _resets_trend(snapshot):
    """Whether the operator accepted this full refresh past the drop limit.

    An operator raises the limit for one refresh to accept a known large
    change; the trend then starts again from that refresh, so the next one
    is not compared with the counts before the change.

    This is deliberately broad: any full refresh whose load ran with a
    raised limit resets the trend, whether or not anything actually fell,
    since the manifest records the limit, not why it was raised. That errs
    toward fewer refusals after an operator has looked, which is the
    override's purpose; the counts it accepted become the new baseline. An
    Administrator's one-time acceptance is narrower (``_accepted_measures``).
    """
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    limit = load.get("maximum_drop_percent") if type(load) is dict else None
    return type(limit) is int and limit > DEFAULT_MAXIMUM_DROP_PERCENT


def _accepted_measures(snapshot):
    """The counts an Administrator's one-time acceptance let fall here (#530).

    A full refresh that went through under an acceptance records it in its
    manifest (``drop_acceptance.ACCEPTANCE_KEY``, written only by the worker)
    with the counts that failed the drop check. Only those counts start
    their trend again from this refresh, so the accepted change is not
    refused again; every other count keeps its week of history, so the
    acceptance never weakens the check of a count it did not cover.
    Anything malformed resets nothing.
    """
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    accepted = load.get(ACCEPTANCE_KEY) if type(load) is dict else None
    failed = accepted.get("failed") if type(accepted) is dict else None
    if type(failed) is not list:
        return frozenset()
    return frozenset(name for name in failed if type(name) is str)


def _within(trend, name):
    """The trend's snapshots that still count for ``name``, newest first.

    That is every one up to and including the newest whose acceptance let
    ``name`` fall (``_accepted_measures``); older values of that count are
    from before the accepted change.
    """
    for snapshot in trend:
        yield snapshot
        if name in _accepted_measures(snapshot):
            return


def _trend(full, since):
    """The full refreshes a load compares with: ``full`` (the newest) and any
    promoted from ``since`` on, newest first, up to and including the first
    that reset the trend."""
    recent = (
        SourceSnapshot.objects.filter(
            organization_id=full.organization_id,
            state="promoted",
            kind="full",
            generation__lt=full.generation,
            promoted_at__gte=since,
        )
        .order_by("-generation")
        .only("counts", "cursor", "generation")
    )
    trend = [full]
    for snapshot in (full, *recent):
        if snapshot is not full:
            trend.append(snapshot)
        if _resets_trend(snapshot):
            break
    return trend


def _largest_counts(trend):
    """Each record count's largest value over the trend (the newest's kinds).

    A count an Administrator's acceptance let fall stops at that refresh
    (``_within``).

    A malformed newest value is passed through unchanged, for the count
    check to refuse as it always has; older malformed values are skipped.
    """
    newest = trend[0].counts
    if type(newest) is not dict:
        return newest
    largest = {}
    for kind, value in newest.items():
        older = [
            snapshot.counts[kind]
            for snapshot in list(_within(trend, kind))[1:]
            if type(snapshot.counts) is dict and type(snapshot.counts.get(kind)) is int
        ]
        largest[kind] = max([value, *older]) if type(value) is int else value
    return largest


def _recorded_derived(snapshot):
    """The derived counts a snapshot's load recorded, or ``None`` if absent."""
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    value = load.get("derived_counts") if type(load) is dict else None
    return value if valid_derived_counts(value) else None


def _derived_baseline(trend, counted):
    """Each derived count's largest value over the trend and the current data.

    As ``loading.derived_baseline`` (``None`` when nothing usable is
    recorded), except that a count an Administrator's acceptance let fall
    only looks back to that refresh (``_within``). ``counted`` is the current
    snapshot's derived counts, which always count.
    """
    current = [counted] if valid_derived_counts(counted) else []
    if derived_baseline(*map(_recorded_derived, trend), *current) is None:
        return None
    baseline = {}
    for name in DERIVED_COUNTS:
        values = [
            recorded[name]
            for recorded in map(_recorded_derived, _within(trend, name))
            if recorded is not None
        ] + [each[name] for each in current]
        # Empty only if neither the accepted refresh nor the current data
        # recorded the count; then the whole trend counts, as before.
        baseline[name] = max(
            values
            or [
                recorded[name]
                for recorded in map(_recorded_derived, trend)
                if recorded is not None
            ]
        )
    return baseline


def _inputs(attempt_id, execution, claim):
    """Read the actual current base and the permanent full-refresh count trend.

    Record counts are compared with each count's largest value over the
    recent full refreshes (``_trend``), and derived counts with those
    refreshes and the current snapshot (#320, #387).

    Only the scope and pointer checks run in the work-order effect. Counting
    an older current snapshot's rows, when its manifest recorded no derived
    counts, runs after it, outside the global lock (#147): that snapshot is
    promoted, so its rows are immutable, and ``reconstruct_snapshot`` keeps
    it from compaction while it reads, or refuses. Nothing here can go stale
    unseen, because promotion requires this snapshot's base to still be the
    current one.
    """
    with execution.effect():
        attempt = verify_refresh_attempt(attempt_id, execution, claim)
        snapshot = attempt.snapshot
        scope = _scope(attempt.request, attempt.credential_fingerprint)
        current = SourceCurrent.objects.get(singleton=True)
        if snapshot.state != "staging" or snapshot.base_id != current.snapshot_id:
            raise InvalidSourcePayload("Refresh requires its unchanged current base.")
        counts = None
        counted = None
        trend = None
        base_workgroup = None
        contact_missing = None
        if current.snapshot_id is not None:
            full = (
                SourceSnapshot.objects.filter(
                    organization_id=snapshot.organization_id,
                    state="promoted",
                    kind="full",
                    generation__lte=current.generation,
                )
                .order_by("-generation")
                .first()
            )
            if full is None:
                raise InvalidSourcePayload("Refresh has no complete full baseline.")
            trend = _trend(full, snapshot.started_at - timedelta(days=TREND_DAYS))
            contact_missing = recorded_contact_missing(full.cursor)
            counts = _largest_counts(trend)
            # Eligibility is compared with both the last full and the current
            # snapshot (#320), from the derived counts each load records in its
            # manifest, so normally nothing is reconstructed for this.
            current_row = SourceSnapshot.objects.get(pk=current.snapshot_id)
            base_workgroup = recorded(current_row.cursor)
            counted = _recorded_derived(current_row)
        zone = (
            scope.campaign.active_configuration.timezone
            if scope.campaign is not None
            else Parish.objects.values_list("timezone", flat=True).get(
                configuration_id=scope.runtime.active_configuration_id
            )
        )
        # Only a campaign still taking responses skips Reminders; a refresh
        # with no campaign, or for an archived one, reads no WorkGroup.
        live = scope.campaign is not None and scope.campaign.state != "archived"
        window = _window(scope)
        as_of = snapshot.started_at.astimezone(ZoneInfo(zone)).date()
        workgroup_name = (
            configured_name(scope.campaign.active_configuration.values)
            if live
            else None
        )
    # The corpus reads, outside the work order (see above).
    derived = None
    if current.snapshot_id is not None:
        if counted is None:
            # A snapshot promoted before these counts were recorded: count
            # it once from its Family and contact rows.
            counted = derived_counts(
                reconstruct_snapshot(current.snapshot_id, kinds=("family", "contact"))
            )
        derived = _derived_baseline(trend, counted)
    return RefreshInputs(
        window,
        as_of,
        counts,
        derived,
        workgroup_name,
        base_workgroup,
        contact_missing,
    )


def load_and_stage_attempt(execution, claim, credential):
    """Observe once with finite private HTTP, then stage in small fenced batches.

    The caller must already maintain Task/source ownership. No SQL transaction
    spans provider I/O. Every attempt/retry repeats credential and current-scope
    validation, and every staging batch repeats it again. Exceptions preserve
    staging for the owning rejection/recovery workflow; none implies success.
    Only a full refresh observes ParishSoft; quick updates were removed (#1147).
    """
    if connection.in_atomic_block:
        raise StorageInvariantError("Source observation cannot hold a transaction.")
    execution.check()
    if not execution.control.active or execution.control.source_claim != claim:
        raise StorageInvariantError("Source observation requires maintained ownership.")
    if claim.phase != "full":
        raise StorageInvariantError("Only a full refresh observes ParishSoft.")
    attempt = begin_refresh_attempt(execution, claim, credential)
    inputs = _inputs(attempt.pk, execution, claim)
    session = source_session(
        execution, claim, attempt_id=attempt.pk, credential=credential
    )
    try:
        # Cache is disabled by both config and the coherent client. Its required
        # compatibility Path is never created/read/written by this pipeline.
        client = CoherentParishSoftClient(
            ParishSoftConfig(
                credential.api_key,
                Path("."),
                cache_enabled=False,
                api_base_url=session.base_url,
            ),
            organization_id=attempt.request.organization_id,
            session=session,
        )
        execution.progress(0, 0, phase=TaskPhase.FETCHING)
        connections.close_all()
        limit = maximum_drop_percent()
        if limit != DEFAULT_MAXIMUM_DROP_PERCENT:
            # An operator override is meant for one refresh; say so on every
            # refresh it applies to, so a forgotten one shows in normal logs.
            emit(
                Event.TASK_STARTED,
                level=logging.WARNING,
                task_id=execution.claim.run_id,
                source_max_drop_percent=limit,
            )
        loaded = load_full_source(
            client,
            window=inputs.window,
            as_of=inputs.as_of,
            previous_full_counts=inputs.previous_full_counts,
            previous_derived_counts=inputs.previous_derived_counts,
            maximum_drop_percent=limit,
            previous_contact_missing=inputs.previous_contact_missing,
            # An Administrator's one-time acceptance of a refused large
            # change (#530).
            accept=acceptor(attempt.request.organization_id, execution.claim.run_id),
        )
        # The Reminder WorkGroup (#861) is read on every refresh, after the
        # corpus: one list call plus the one WorkGroup's
        # members, and nothing at all when the campaign names none.
        # A name ParishSoft does not have keeps the set applied before it
        # (#1305), so a misspelled rename skips the same Families until a
        # refresh finds the WorkGroup.
        workgroup = keep_when_missing(
            load_reminder_workgroup(client, inputs.workgroup_name),
            inputs.base_workgroup,
        )
    finally:
        session.close()
    evidence = loaded.evidence
    if workgroup is not None:
        warn_if_missing(workgroup, task_id=execution.claim.run_id)
        evidence = {**evidence, EVIDENCE_KEY: workgroup}
    cursor = refresh_cursor(
        snapshot_id=attempt.snapshot_id,
        started_at=attempt.snapshot.started_at,
        window_digest=inputs.window.digest,
        evidence=evidence,
    )

    def admitted(action, snapshot):
        """Storage callback is bound to this exact attempt, never a caller boolean."""
        current = verify_refresh_attempt(attempt.pk, execution, claim)
        if snapshot is not None and current.snapshot_id != snapshot.pk:
            raise PermissionError("Staging belongs to another source attempt.")
        return True

    def staged(action, snapshot):
        """``admitted`` for a source step: a staging batch or the validation."""
        current = verify_refresh_attempt(attempt.pk, execution, claim, step=True)
        if action != "stage" or current.snapshot_id != snapshot.pk:
            raise PermissionError("Staging belongs to another source attempt.")
        return True

    total, done = sum(loaded.counts.values()), 0
    reported = done
    execution.progress(done, total, phase=TaskPhase.STAGING)
    reported_at = monotonic()
    for kind, rows in loaded.corpus.items():
        for batch in batched(rows.items(), STAGING_BATCH_ROWS):
            # A source step, outside the global work order (#147): a batch
            # writes only this attempt's staging, which promotion (under the
            # order) verifies again before it becomes source truth.
            with source_step(execution):
                stage_entities(
                    attempt.snapshot_id,
                    claim,
                    kind=kind,
                    entities=dict(batch),
                    admit=staged,
                )
            done += len(batch)
            # Progress is its own task transition, which still takes the
            # work-order lock (#147), so report it only once both
            # PROGRESS_ROWS and PROGRESS_SECONDS have passed, not per batch.
            # The validating report below always carries the final count.
            if (
                done - reported >= PROGRESS_ROWS
                and monotonic() - reported_at >= PROGRESS_SECONDS
            ):
                execution.progress(done, total, phase=TaskPhase.STAGING)
                reported, reported_at = done, monotonic()
    execution.progress(done, total, phase=TaskPhase.VALIDATING)
    # Refresh the membership tables' planner statistics now, in a source step
    # outside the global work order (#895, change 8). The ready transition's
    # snapshot guard re-derives the corpus evidence under that lock before
    # the completion trigger analyzes the tables, so with statistics that
    # predate this snapshot it held the lock for about 1 s at 1,100 Families
    # instead of about 0.1 s. Its own commit makes them visible to
    # validation and to the ready transition.
    with source_step(execution):
        analyze_staging(
            attempt.snapshot_id, claim, admit=staged, task_id=execution.claim.run_id
        )
    # Validation reads the whole staged corpus and the base's manifest
    # (150-270 ms at 1,100 Families), so it is a source step, outside the
    # global work order (#895), like the staging batches whose rows it reads.
    # Only the one-row ready transition stays under the order. It re-verifies
    # the attempt's scope under the lock, and the snapshot guard re-derives
    # the counts and digest from the membership and refuses any difference.
    with source_step(execution):
        evidence = validate_snapshot(
            attempt.snapshot_id,
            claim,
            expected_counts=loaded.counts,
            cursor=cursor,
            admit=staged,
        )
    with execution.effect():
        return complete_snapshot(attempt.snapshot_id, claim, evidence, admit=admitted)
