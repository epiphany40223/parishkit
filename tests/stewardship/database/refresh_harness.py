"""Run real compiled source refreshes as the worker, against fake ParishSoft.

Shared by the tests that need the worker's real handler and Phase 2 effects
under the restricted worker login, faking only ParishSoft's exchange. These
helpers lived in the quick-update tests until quick updates were removed
(#1147); a later refresh is now always a full refresh.
"""

from uuid import uuid4

from django.db import transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.runtime_background import bind_authority
from parishkit.stewardship.source import transport
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.effects import refresh_reconciler
from parishkit.stewardship.source.execution import refresh_handler
from parishkit.stewardship.source.leases import _now as lease_now
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.windows import RefreshWindow

from ..test_source_corpus import source
from .campaign_builders import add_draft
from .credential_builders import keys
from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import configured
from .test_source_execution_postgresql import run
from .test_source_leases_postgresql import delay
from .test_source_refreshing_postgresql import fake_provider, pages
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit


def stage(snapshot, claim, corpus, cursor):
    """Use real complete counts, membership maps and SQL referential validation."""
    for kind, rows in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=rows, admit=permit)
    return finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor=cursor,
        admit=permit,
    )


def delta_cursor(*, snapshot_id, started_at, window_digest, evidence, base_cursor):
    """The cursor an older quick update (delta refresh) recorded, before #1147.

    ``cursors.refresh_cursor`` now builds only a full refresh's cursor; a
    delta named the full refresh it read on from (``base_cursor``'s), which
    the database's manifest guard still checks. Tests that build such older
    rows use this.
    """
    full = refresh_cursor(
        snapshot_id=snapshot_id,
        started_at=started_at,
        window_digest=window_digest,
        evidence=evidence,
    )
    return full | {
        "full_snapshot_id": base_cursor["full_snapshot_id"],
        "full_started_at": base_cursor["full_started_at"],
    }


def promoted_full():
    """Promote a full corpus through the storage layer alone; return it."""
    window = RefreshWindow(uuid4(), ())
    claim = acquire_source(**running_source_task(), phase="full")
    first = begin_snapshot(claim, organization_id=5, admit=permit)
    corpus = normalize_core(source(), as_of=first.started_at.date())
    cursor = refresh_cursor(
        snapshot_id=first.pk,
        started_at=first.started_at,
        window_digest=window.digest,
        evidence={
            "giving_as_of_date": first.started_at.date().isoformat(),
            "anonymous_pledges": 0,
            "anonymous_contributions": 0,
        },
    )
    first = stage(first, claim, corpus, cursor)
    promote_snapshot(first.pk, claim, admit=permit, reconcile=permit)
    release_source(claim)
    return first, corpus, cursor


def force_unchanged(snapshot, **overrides):
    """Write the ``unchanged`` state directly, with optional wrong evidence.

    Nothing records that state since quick updates were removed (#1147), but
    the database still guards it for the older rows that hold it; this
    writes it without any Python check, so the SQL guard alone decides.
    """
    with transaction.atomic():
        row = SourceSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        base = SourceSnapshot.objects.get(pk=row.base_id)
        row.counts = dict(base.counts)
        row.content_digest = base.content_digest
        row.validation = {"schema": "source-unchanged-v1"}
        row.completed_at = lease_now()
        row.state = "unchanged"
        row.version += 1
        for key, value in overrides.items():
            setattr(row, key, value)
        row.save()


def compiled_handler(tmp_path, credential, suppressed):
    """The worker's real handler with its real effects.

    ``suppressed`` is a mutable set standing in for provider mail refusals,
    so a test can add a bounce between refreshes.
    """
    ring = keys()

    def suppressions(scope):
        """Synthetic suppression owner; returns the current refusals."""
        return frozenset(suppressed)

    path = tmp_path / "source-key"
    path.write_bytes(credential.value)
    path.chmod(0o600)
    return refresh_handler(
        credential_path=path,
        reconcile=refresh_reconciler(
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressions=suppressions,
        ),
    )


def short_drain(monkeypatch):
    """Shorten each request's drain window, so a second refresh can start.

    Every ParishSoft request reserves the source lease until its timeout plus
    a safety margin has passed; the next refresh would otherwise wait that
    long behind the first. Shortened, the tests wait two seconds.
    """
    real = transport.reserve_source_request

    def reserve(claim, **_):
        """Reserve the lease for one second plus one second of safety."""
        return real(claim, timeout_seconds=1, safety_seconds=1)

    monkeypatch.setattr(transport, "reserve_source_request", reserve)


class Refreshes:
    """A configured tenant whose real worker runs full refreshes.

    ``suppressed`` stands in for provider mail refusals, so a test can add a
    bounce between refreshes. ``campaign`` adds a draft campaign first.
    """

    def __init__(self, tmp_path, monkeypatch, *, campaign=True):
        credential, self.store, version, actor = configured(tmp_path)
        if campaign:
            add_draft(self.store, version, actor)
        short_drain(monkeypatch)
        self.monkeypatch = monkeypatch
        self.suppressed = set()
        self.compiled = compiled_handler(tmp_path, credential, self.suppressed)
        self._run(command(), pages())
        self.full = SourceSnapshot.objects.get(state="promoted")

    def _run(self, receipt, values):
        """Run one refresh as the restricted worker against fake ParishSoft."""
        remaining, calls = fake_provider(self.monkeypatch, values)
        with task_login(ServiceRole.WORKER, reconnect=True):
            bound = bind_authority({TASK_TYPE: self.compiled}, self.store)[TASK_TYPE]
            assert run(receipt, bound)
        assert not remaining
        assert TaskRun.objects.get(pk=receipt.task_root_id).state == "succeeded"
        return calls

    def again(self):
        """Run one more full refresh; return its promoted snapshot."""
        delay(2.05)  # The previous refresh's request drain window.
        self._run(command(), pages())
        return SourceSnapshot.objects.filter(kind="full").latest("started_at")
