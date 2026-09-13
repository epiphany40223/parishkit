"""Stage an initial corpus using only an ephemeral, original-login credential.

No current-source pointer, Family code or configured marker changes here. The
immutable result connects final setup preparation to the exact validated load.
"""

from itertools import batched
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import connection, connections

from parishkit.parishsoft import ParishSoftConfig
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.parishsoft_transport import BoundedSourceSession
from parishkit.stewardship.accounts.setup_exchange_models import (
    SetupSourceExchange,
    SetupSourceResult,
)
from parishkit.stewardship.accounts.setup_models import SetupDraftSection
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .cursors import refresh_cursor
from .errors import SourceCredentialChanged, local_read_admission
from .leases import reserve_source_request, verify_source
from .loading import load_full_source
from .setup_admission import bound_attempt, require_live_setup
from .setup_exchange import _live
from .snapshot_models import SourceCurrent
from .snapshots import begin_snapshot, finish_snapshot, stage_entities
from .windows import RefreshWindow


def verify_setup_exchange(execution, claim, exchange_id, credential):
    """Recheck immutable recipient, actual bytes, original login and both fences."""
    if not execution.control.active or execution.control.source_claim != claim:
        raise StorageInvariantError("Setup loading requires maintained ownership.")
    status = _status(lock_task_claim(execution.claim))
    attempt = require_live_setup(bound_attempt(status))
    verify_source(claim)
    row = SetupSourceExchange.objects.get(pk=exchange_id)
    _live(row)
    if (
        row.attempt_id != attempt.pk
        or row.task_id != execution.claim.run_id
        or row.task_fence != execution.claim.fence
        or row.worker_id != execution.claim.worker_id
        or row.source_fence != claim.fence
        or row.replied_at is None
        or SourceCurrent.objects.get(singleton=True).snapshot_id is not None
    ):
        raise PermissionError("The setup source observation is no longer current.")
    if row.fingerprint != credential.fingerprint:
        raise SourceCredentialChanged("The setup source credential differs.")
    return row


def load_setup_source(execution, claim, *, exchange_id, credential):
    """Read outside SQL; stage bounded batches and retain ready-only evidence."""
    if connection.in_atomic_block:
        raise StorageInvariantError("Setup observation cannot hold a transaction.")

    def admitted(action, snapshot):
        """The same private observation owns every storage effect, including retries."""
        verify_setup_exchange(execution, claim, exchange_id, credential)
        return action == "stage" and (
            snapshot is None
            or (snapshot.task_id, snapshot.source_fence) == (claim.task_id, claim.fence)
        )

    with execution.effect():
        exchange = verify_setup_exchange(execution, claim, exchange_id, credential)
        # Only public metadata is selected; the worker cannot read the original
        # candidate ciphertext or target installer's persistent private key.
        settings = SetupSealedCredential.objects.values_list("settings", flat=True).get(
            pk=exchange.credential_id
        )
        organization_id = settings["organization_id"]
        profile = SetupDraftSection.objects.values_list("values", flat=True).get(
            attempt_id=exchange.attempt_id, step="parish", scrubbed_at=None
        )
        zone = ZoneInfo(profile["timezone"])
        snapshot = begin_snapshot(
            claim, organization_id=organization_id, admit=admitted
        )

    @local_read_admission
    def before_request(seconds):
        """Every retry reserves drainage under fresh admission, then closes SQL."""
        if connection.in_atomic_block:
            raise StorageInvariantError("Setup HTTP cannot hold a transaction.")
        try:
            with execution.effect():
                verify_setup_exchange(execution, claim, exchange_id, credential)
                if session.headers.get("x-api-key") != credential.api_key:
                    raise SourceCredentialChanged(
                        "The setup source credential differs."
                    )
                reserve_source_request(
                    claim, timeout_seconds=seconds, safety_seconds=15
                )
        finally:
            connections.close_all()

    window = RefreshWindow(None, ())
    session = BoundedSourceSession(before_request=before_request, check=execution.check)
    session.headers["x-api-key"] = credential.api_key
    try:
        client = CoherentParishSoftClient(
            ParishSoftConfig(credential.api_key, Path("."), cache_enabled=False),
            organization_id=organization_id,
            session=session,
        )
        execution.progress(0, 0, phase=TaskPhase.FETCHING)
        connections.close_all()
        loaded = load_full_source(
            client, window=window, as_of=snapshot.started_at.astimezone(zone).date()
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
        snapshot = finish_snapshot(
            snapshot.pk,
            claim,
            expected_counts=loaded.counts,
            cursor=refresh_cursor(
                snapshot_id=snapshot.pk,
                kind="full",
                started_at=snapshot.started_at,
                window_digest=window.digest,
                evidence=loaded.evidence,
            ),
            admit=admitted,
        )
        verify_setup_exchange(execution, claim, exchange_id, credential)
        SetupSourceResult.objects.create(
            exchange_id=exchange_id, snapshot=snapshot, actor_id=claim.worker_id
        )
        return snapshot
