"""Bound one full/delta observation to its immutable attempt and staged manifest.

The compiled worker owns lease acquisition/renewal, recovery, atomic domain
effects and Task outcome. This pipeline never declares success or advances the
current pointer: validated staging is not a completed campaign refresh.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import batched
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import connection, connections

from parishkit.parishsoft import ParishSoftConfig
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.local import source_base_url
from parishkit.stewardship.observability import Event, emit
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _scope, begin_refresh_attempt, verify_refresh_attempt
from .canonical import InvalidSourcePayload, canonical_payload
from .cursors import refresh_cursor
from .delta import load_delta_source
from .loading import (
    DEFAULT_MAXIMUM_DROP_PERCENT,
    derived_baseline,
    derived_counts,
    load_full_source,
    maximum_drop_percent,
    valid_derived_counts,
)
from .requests import _window
from .snapshot_models import SourceCurrent, SourceSnapshot
from .snapshots import (
    finish_snapshot,
    finish_unchanged,
    reconstruct_snapshot,
    stage_entities,
)
from .transport import runtime_profile, source_session
from .windows import RefreshWindow

# Each staging batch is one execution.effect(): a transaction that holds the
# deployment-wide work-order lock (736220,1) while it verifies the attempt and
# writes the batch. At 500 rows that hold was 1.3-1.5 s on the validation
# host, and every mail message, page and in-flight check (1 s limit) queued
# behind it, batch after batch, for the whole refresh (#394). Smaller
# batches cost a few more short transactions but let waiters in between.
# stage_entities() refuses more than 500 rows, so this must stay at or below
# that.
STAGING_BATCH_ROWS = 125
# Staging progress is reported about this often, as it was when batches
# were 500 rows, so the smaller batches add no progress transitions.
PROGRESS_ROWS = 500


@dataclass(frozen=True)
class RefreshInputs:
    """Copy only pinned observation inputs; never reuse an ORM admission decision."""

    window: RefreshWindow
    as_of: date
    previous_full_counts: dict | None
    previous_derived_counts: dict | None
    base_cursor: dict | None
    base: dict | None = field(repr=False)


# A load's drop check compares with every full refresh promoted in this many
# days before it, not only the last one (#387). With frequent full refreshes,
# each could drop just under the limit from the one before, so a large loss
# could build up over a few days without any single refusal.
TREND_DAYS = 7


def _resets_trend(snapshot):
    """Whether this full refresh was accepted past the normal drop limit.

    An operator raises the limit for one refresh to accept a known large
    change; the trend then starts again from that refresh, so the next one
    is not compared with the counts before the change. A future one-time
    Admin acceptance of a refused change must count here too (#530).

    This is deliberately broad: any full refresh whose load ran with a
    raised limit resets the trend, whether or not anything actually fell,
    since the manifest records the limit, not why it was raised. That errs
    toward fewer refusals after an operator has looked, which is the
    override's purpose; the counts it accepted become the new baseline.
    """
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    limit = load.get("maximum_drop_percent") if type(load) is dict else None
    return type(limit) is int and limit > DEFAULT_MAXIMUM_DROP_PERCENT


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
            for snapshot in trend[1:]
            if type(snapshot.counts) is dict and type(snapshot.counts.get(kind)) is int
        ]
        largest[kind] = max([value, *older]) if type(value) is int else value
    return largest


def _recorded_derived(snapshot):
    """The derived counts a snapshot's load recorded, or ``None`` if absent."""
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    value = load.get("derived_counts") if type(load) is dict else None
    return value if valid_derived_counts(value) else None


def _base_cursor(current_id):
    """The cursor a quick update reads on from: the newest read of the corpus.

    That is the newest ``unchanged`` quick update read against the current
    snapshot, else the current snapshot itself. An unchanged read saw the
    same corpus as the current snapshot up to its own start, so its
    watermark is as good a boundary as a promoted copy's would have been;
    without it the change-list window would grow from the last promotion
    until the next one, and a week of unchanged reads would force a full
    refresh (#630). Its full-coverage fields were copied from the current
    snapshot's, which the completion guard checks.
    """
    newest = (
        SourceSnapshot.objects.filter(base_id=current_id, state="unchanged")
        .order_by("-started_at")
        .values_list("cursor", flat=True)
        .first()
    )
    if newest is not None:
        return newest
    return SourceSnapshot.objects.values_list("cursor", flat=True).get(pk=current_id)


def _inputs(attempt_id, execution, claim):
    """Read the actual current base and the permanent full-refresh count trend.

    Record counts are compared with each count's largest value over the
    recent full refreshes (``_trend``), and derived counts with those
    refreshes and the current snapshot (#320, #387).
    """
    with execution.effect():
        attempt = verify_refresh_attempt(attempt_id, execution, claim)
        snapshot = attempt.snapshot
        scope = _scope(attempt.request, attempt.credential_fingerprint)
        current = SourceCurrent.objects.get(singleton=True)
        if snapshot.state != "staging" or snapshot.base_id != current.snapshot_id:
            raise InvalidSourcePayload("Refresh requires its unchanged current base.")
        base = None
        cursor = None
        counts = None
        derived = None
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
            counts = _largest_counts(trend)
            if snapshot.kind == "delta":
                base = reconstruct_snapshot(current.snapshot_id)
                cursor = _base_cursor(current.snapshot_id)
            # Eligibility is compared with both the last full and the current
            # snapshot (#320), from the derived counts each load records in its
            # manifest, so normally nothing is reconstructed for this.
            current_row = SourceSnapshot.objects.get(pk=current.snapshot_id)
            recorded = _recorded_derived(current_row)
            if recorded is None:
                # A snapshot promoted before these counts were recorded: count
                # it once from its Family and contact rows (or the delta base).
                recorded = derived_counts(
                    base
                    if base is not None
                    else reconstruct_snapshot(
                        current.snapshot_id, kinds=("family", "contact")
                    )
                )
            derived = derived_baseline(
                *(_recorded_derived(each) for each in trend), recorded
            )
        zone = (
            scope.campaign.active_configuration.timezone
            if scope.campaign is not None
            else Parish.objects.values_list("timezone", flat=True).get(
                configuration_id=scope.runtime.active_configuration_id
            )
        )
        return RefreshInputs(
            _window(scope),
            snapshot.started_at.astimezone(ZoneInfo(zone)).date(),
            counts,
            derived,
            cursor,
            base,
        )


def same_corpus(corpus, base):
    """Whether ``corpus`` stages exactly the payloads ``base`` already holds.

    Compared as staging would store them: by canonical payload digest, so a
    value that is equal in Python but canonically different (``True`` and
    ``1``) counts as a change. A delta copies the base's row objects for
    everything it did not reload, so only rows that are new objects need
    hashing; usually that is just the re-evaluated roster.
    """
    if corpus.keys() != base.keys():
        return False
    for kind, rows in corpus.items():
        before = base[kind]
        if rows.keys() != before.keys():
            return False
        for key, row in rows.items():
            old = before[key]
            if row is not old and (
                canonical_payload(row)[1] != canonical_payload(old)[1]
            ):
                return False
    return True


def load_and_stage_attempt(execution, claim, credential, *, unchanged=None):
    """Observe once with finite private HTTP, then stage in small fenced batches.

    The caller must already maintain Task/source ownership. No SQL transaction
    spans provider I/O. Every attempt/retry repeats credential and current-scope
    validation, and every staging batch repeats it again. Exceptions preserve
    staging for the owning rejection/recovery workflow; none implies success.

    ``unchanged(snapshot, corpus, execution, claim)``, when given, decides
    whether promoting a quick update whose corpus equals the current one
    would change any owning-domain state. When it would not, nothing is
    staged and the snapshot ends ``unchanged`` (#630); the caller then skips
    promotion. Otherwise, and always for a full refresh, staging is as before.
    """
    if connection.in_atomic_block:
        raise StorageInvariantError("Source observation cannot hold a transaction.")
    execution.check()
    if not execution.control.active or execution.control.source_claim != claim:
        raise StorageInvariantError("Source observation requires maintained ownership.")
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
                api_base_url=source_base_url(runtime_profile()),
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
        options = dict(
            window=inputs.window,
            as_of=inputs.as_of,
            previous_full_counts=inputs.previous_full_counts,
            previous_derived_counts=inputs.previous_derived_counts,
            maximum_drop_percent=limit,
        )
        if claim.phase == "full":
            loaded = load_full_source(client, **options)
        else:
            loaded = load_delta_source(
                client,
                **options,
                base=inputs.base,
                base_cursor=inputs.base_cursor,
                started_at=attempt.snapshot.started_at,
            )
    finally:
        session.close()
    cursor = refresh_cursor(
        snapshot_id=attempt.snapshot_id,
        kind=claim.phase,
        started_at=attempt.snapshot.started_at,
        window_digest=inputs.window.digest,
        evidence=loaded.evidence,
        base_cursor=inputs.base_cursor,
    )

    def admitted(action, snapshot):
        """Storage callback is bound to this exact attempt, never a caller boolean."""
        current = verify_refresh_attempt(attempt.pk, execution, claim)
        if snapshot is not None and current.snapshot_id != snapshot.pk:
            raise PermissionError("Staging belongs to another source attempt.")
        return True

    # Most quick updates find nothing (#629): the change list is empty, so
    # the corpus is the current one exactly (a day change that flips a
    # roster's "current" flag is a difference and stages as before). Any
    # doubt falls through to the ordinary staging path below.
    if (
        claim.phase == "delta"
        and unchanged is not None
        and same_corpus(loaded.corpus, inputs.base)
    ):
        with execution.effect():
            if unchanged(attempt.snapshot, inputs.base, execution, claim) is True:
                return finish_unchanged(
                    attempt.snapshot_id, claim, cursor=cursor, admit=admitted
                )

    total, done = sum(loaded.counts.values()), 0
    reported = done
    execution.progress(done, total, phase=TaskPhase.STAGING)
    for kind, rows in loaded.corpus.items():
        for batch in batched(rows.items(), STAGING_BATCH_ROWS):
            with execution.effect():
                stage_entities(
                    attempt.snapshot_id,
                    claim,
                    kind=kind,
                    entities=dict(batch),
                    admit=admitted,
                )
            done += len(batch)
            # Progress is its own transition under the same lock, so report
            # it per PROGRESS_ROWS rather than per batch. The validating
            # report below always carries the final count.
            if done - reported >= PROGRESS_ROWS:
                execution.progress(done, total, phase=TaskPhase.STAGING)
                reported = done
    execution.progress(done, total, phase=TaskPhase.VALIDATING)
    with execution.effect():
        return finish_snapshot(
            attempt.snapshot_id,
            claim,
            expected_counts=loaded.counts,
            cursor=cursor,
            admit=admitted,
        )
