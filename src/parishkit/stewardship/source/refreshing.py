"""Bound one full/delta observation to its immutable attempt and staged manifest.

The compiled worker owns lease acquisition/renewal, recovery, atomic domain
effects and Task outcome. This pipeline never declares success or advances the
current pointer: validated staging is not a completed campaign refresh.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from itertools import batched
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import connection, connections

from parishkit.parishsoft import ParishSoftConfig
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.observability import Event, emit
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _scope, begin_refresh_attempt, verify_refresh_attempt
from .canonical import InvalidSourcePayload
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
from .snapshots import finish_snapshot, reconstruct_snapshot, stage_entities
from .transport import source_session
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


def _recorded_derived(snapshot):
    """The derived counts a snapshot's load recorded, or ``None`` if absent."""
    load = snapshot.cursor.get("load") if type(snapshot.cursor) is dict else None
    value = load.get("derived_counts") if type(load) is dict else None
    return value if valid_derived_counts(value) else None


def _inputs(attempt_id, execution, claim):
    """Read the actual current base and permanent last-full count evidence."""
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
            counts = full.counts
            if snapshot.kind == "delta":
                base = reconstruct_snapshot(current.snapshot_id)
                cursor = SourceSnapshot.objects.get(pk=current.snapshot_id).cursor
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
            derived = derived_baseline(_recorded_derived(full), recorded)
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


def load_and_stage_attempt(execution, claim, credential):
    """Observe once with finite private HTTP, then stage in small fenced batches.

    The caller must already maintain Task/source ownership. No SQL transaction
    spans provider I/O. Every attempt/retry repeats credential and current-scope
    validation, and every staging batch repeats it again. Exceptions preserve
    staging for the owning rejection/recovery workflow; none implies success.
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
            ParishSoftConfig(credential.api_key, Path("."), cache_enabled=False),
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
