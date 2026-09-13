"""Fresh full source coverage for the exact first campaign's selected periods.

The earlier catalog snapshot never becomes financial evidence by relabelling
it. This load owns a new Task/source fence and never activates product state.
"""

from itertools import batched
from pathlib import Path

from django.db import connection, connections

from parishkit.parishsoft import ParishSoftConfig
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.parishsoft_transport import BoundedSourceSession
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .credentials import SourceCredential
from .cursors import refresh_cursor
from .errors import SourceCredentialChanged, SourceScopeChanged, local_read_admission
from .leases import reserve_source_request, verify_source
from .loading import load_full_source
from .setup_final_tasks import require_final_task
from .snapshots import begin_snapshot, finish_snapshot, stage_entities


def load_final_setup_source(execution, claim, *, store, credential_path):
    """Read under maintained ownership, outside SQL, with fresh per-page checks."""
    if (
        connection.in_atomic_block
        or not execution.control.active
        or execution.control.source_claim != claim
        or not isinstance(credential_path, Path)
    ):
        raise StorageInvariantError(
            "Final setup loading requires maintained ownership."
        )
    credential = SourceCredential.read(credential_path)

    def verify():
        """Selected input, actual mounted bytes and both fences must still agree."""
        status = _status(lock_task_claim(execution.claim))
        scope = require_final_task(status, store=store)
        verify_source(claim)
        if (claim.task_id, claim.task_fence, claim.worker_id) != (
            status.run_id,
            status.fence,
            status.worker_id,
        ):
            raise PermissionError("Final setup source ownership changed.")
        if (
            credential.fingerprint != scope.fingerprint
            or SourceCredential.read(credential_path).fingerprint != scope.fingerprint
        ):
            raise SourceCredentialChanged("Final setup source credential changed.")
        return scope

    def admitted(action, snapshot):
        """Never accept a historical catalog or another execution's ready manifest."""
        verify()
        return action == "stage" and (
            snapshot is None
            or (snapshot.task_id, snapshot.source_fence) == (claim.task_id, claim.fence)
        )

    with execution.effect():
        scope = verify()
        snapshot = begin_snapshot(
            claim, organization_id=scope.organization_id, admit=admitted
        )

    @local_read_admission
    def before_request(seconds):
        """Each page/retry reserves real drainage before releasing its SQL socket."""
        if connection.in_atomic_block:
            raise StorageInvariantError("Final setup HTTP cannot hold a transaction.")
        try:
            with execution.effect():
                if verify() != scope:
                    raise SourceScopeChanged("Final setup source input changed.")
                if session.headers.get("x-api-key") != credential.api_key:
                    raise SourceCredentialChanged("Final setup source key changed.")
                reserve_source_request(
                    claim, timeout_seconds=seconds, safety_seconds=15
                )
        finally:
            connections.close_all()

    session = BoundedSourceSession(before_request=before_request, check=execution.check)
    session.headers["x-api-key"] = credential.api_key
    try:
        client = CoherentParishSoftClient(
            ParishSoftConfig(credential.api_key, Path("."), cache_enabled=False),
            organization_id=scope.organization_id,
            session=session,
        )
        execution.progress(0, 0, phase=TaskPhase.FETCHING)
        connections.close_all()
        loaded = load_full_source(
            client,
            window=scope.window,
            as_of=snapshot.started_at.astimezone(scope.timezone).date(),
        )
    finally:
        session.close()
    total, done = sum(loaded.counts.values()), 0
    execution.progress(done, total, phase=TaskPhase.STAGING)
    for kind, rows in loaded.corpus.items():
        for batch in batched(rows.items(), 500):
            with execution.effect():
                stage_entities(
                    snapshot.pk, claim, kind=kind, entities=dict(batch), admit=admitted
                )
            done += len(batch)
            execution.progress(done, total, phase=TaskPhase.STAGING)
    execution.progress(done, total, phase=TaskPhase.VALIDATING)
    with execution.effect():
        verify()
        return finish_snapshot(
            snapshot.pk,
            claim,
            expected_counts=loaded.counts,
            cursor=refresh_cursor(
                snapshot_id=snapshot.pk,
                kind="full",
                started_at=snapshot.started_at,
                window_digest=scope.window.digest,
                evidence=loaded.evidence,
            ),
            admit=admitted,
        )
