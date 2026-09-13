"""Bound one full/delta observation to its immutable attempt and staged manifest.

The compiled worker owns lease acquisition/renewal, recovery, atomic domain
effects and Task outcome. This pipeline never declares success or advances the
current pointer: validated staging is not a completed campaign refresh.
"""

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
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _scope, begin_refresh_attempt, verify_refresh_attempt
from .canonical import InvalidSourcePayload
from .cursors import refresh_cursor
from .delta import load_delta_source
from .loading import load_full_source
from .requests import _window
from .snapshot_models import SourceCurrent, SourceSnapshot
from .snapshots import finish_snapshot, reconstruct_snapshot, stage_entities
from .transport import source_session
from .windows import RefreshWindow


@dataclass(frozen=True)
class RefreshInputs:
    """Copy only pinned observation inputs; never reuse an ORM admission decision."""

    window: RefreshWindow
    as_of: date
    previous_full_counts: dict | None
    base_cursor: dict | None
    base: dict | None = field(repr=False)


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
        zone = (
            scope.campaign.active_configuration.timezone
            if scope.campaign is not None
            else Parish.objects.get(
                configuration_id=scope.runtime.active_configuration_id
            ).timezone
        )
        return RefreshInputs(
            _window(scope),
            snapshot.started_at.astimezone(ZoneInfo(zone)).date(),
            counts,
            cursor,
            base,
        )


def load_and_stage_attempt(execution, claim, credential):
    """Observe once with finite private HTTP, then stage in fenced 500-row batches.

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
        options = dict(
            window=inputs.window,
            as_of=inputs.as_of,
            previous_full_counts=inputs.previous_full_counts,
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
    execution.progress(done, total, phase=TaskPhase.STAGING)
    for kind, rows in loaded.corpus.items():
        for batch in batched(rows.items(), 500):
            with execution.effect():
                stage_entities(
                    attempt.snapshot_id,
                    claim,
                    kind=kind,
                    entities=dict(batch),
                    admit=admitted,
                )
            done += len(batch)
            execution.progress(done, total, phase=TaskPhase.STAGING)
    execution.progress(done, total, phase=TaskPhase.VALIDATING)
    with execution.effect():
        return finish_snapshot(
            attempt.snapshot_id,
            claim,
            expected_counts=loaded.counts,
            cursor=cursor,
            admit=admitted,
        )
