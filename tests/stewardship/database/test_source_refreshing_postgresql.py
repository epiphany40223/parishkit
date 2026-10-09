"""Real attempt/HTTP/staging pipeline, with only the provider exchange replaced."""

import json
from datetime import UTC, date, datetime

import pytest
from django.db import connection, connections

from parishkit import parishsoft_transport
from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source import loading, refreshing
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.credentials import SourceCredential
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.loading import CONTACT_COVERAGE_KEY
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.refresh_models import SourceRefreshAttempt
from parishkit.stewardship.source.refreshing import _inputs, load_and_stage_attempt
from parishkit.stewardship.source.snapshots import (
    finish_snapshot,
    promote_snapshot,
    stage_entities,
)
from parishkit.stewardship.storage import StorageInvariantError

from ..test_source_loading import provider_pages, with_contact_list
from .campaign_builders import add_draft
from .test_source_attempts_postgresql import setup
from .test_source_requests_postgresql import claim as claim_request
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only the disposable database's idle migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def pages(**options):
    """Scope the complete shared loader fixture to the configured synthetic tenant."""
    values = provider_pages(**options)
    values[0][0]["organizationID"] = 12345
    values[1][0]["registeredOrganizationID"] = 12345
    return values


def fake_provider(monkeypatch, values):
    """Every actual transport preflight still runs, including closed-SQL checks."""
    remaining, calls = list(values), []

    def exchange(payload, **kwargs):
        """Only this private pipe exchange is faked; no real API credential is used."""
        assert connection.connection is None and not connection.in_atomic_block
        calls.append(json.loads(payload)["url"])
        return b"200\n" + json.dumps(remaining.pop(0)).encode()

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    return remaining, calls


def run(credential, execution, lease):
    """Use real independent renewal for the whole observation and staging attempt."""
    with maintain_execution(execution), execution.maintain_source(lease):
        return load_and_stage_attempt(execution, lease, credential)


def test_full_pipeline_stages_one_bound_manifest_without_claiming_success(
    tmp_path, monkeypatch
):
    """A validated load is ready, not current and not a succeeded TaskRun."""
    credential, execution, lease, *_ = setup(tmp_path)
    remaining, calls = fake_provider(monkeypatch, pages())
    result = run(credential, execution, lease)
    attempt = SourceRefreshAttempt.objects.get()
    assert not remaining and len(calls) == len(pages())
    assert result.pk == attempt.snapshot_id and result.state == "ready"
    assert result.cursor["watermark"] == result.started_at.isoformat()
    assert result.cursor["full_snapshot_id"] == str(result.pk)
    assert result.counts["family"] == result.counts["member"] == 1
    assert result.counts["fund"] == 1
    # The manifest keeps the derived counts later refreshes compare with (#320).
    assert result.cursor["load"]["derived_counts"]["portal_eligible_families"] == 1
    assert SourceCurrent.objects.get().snapshot_id is None
    task = TaskRun.objects.get(pk=execution.claim.run_id)
    assert task.state == "running" and task.phase == "validating"
    assert task.progress_current == task.progress_total == sum(result.counts.values())


def with_extra_funds(monkeypatch, extra):
    """Pad the real full load with synthetic Funds so staging needs many batches."""
    from dataclasses import replace

    real = refreshing.load_full_source

    def padded(client, **options):
        """The real validated load, plus ``extra`` copies of its first Fund."""
        loaded = real(client, **options)
        funds = dict(loaded.corpus["fund"])
        payload = next(iter(funds.values()))
        funds.update({f"synthetic-{index}": payload for index in range(extra)})
        corpus = loaded.corpus | {"fund": funds}
        counts = loaded.counts | {"fund": len(funds)}
        return replace(loaded, corpus=corpus, counts=counts)

    monkeypatch.setattr(refreshing, "load_full_source", padded)


def spy_staging(monkeypatch):
    """Record each staged batch's size and every staging progress report."""
    from parishkit.stewardship.jobs.dispatch import Execution

    batches, reports = [], []
    real_stage, real_progress = refreshing.stage_entities, Execution.progress

    def stage(snapshot_id, claim, *, kind, entities, admit):
        batches.append(len(entities))
        return real_stage(snapshot_id, claim, kind=kind, entities=entities, admit=admit)

    def progress(execution, current, total, *, phase=None):
        reports.append((current, phase))
        return real_progress(execution, current, total, phase=phase)

    monkeypatch.setattr(refreshing, "stage_entities", stage)
    monkeypatch.setattr(Execution, "progress", progress)
    return batches, reports


def test_staging_writes_small_batches(tmp_path, monkeypatch):
    """Each staging step writes at most STAGING_BATCH_ROWS (#394).

    Steps no longer take the work-order lock (#147, see
    test_source_step_lock_postgresql); small batches keep them short.

    Progress is still reported about every PROGRESS_ROWS rows, not per batch,
    and every row is staged exactly once.
    """
    assert refreshing.STAGING_BATCH_ROWS <= 125 and refreshing.PROGRESS_ROWS == 500
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    with_extra_funds(monkeypatch, 1100)
    batches, reports = spy_staging(monkeypatch)
    result = run(credential, execution, lease)
    total = sum(result.counts.values())
    assert result.state == "ready" and result.counts["fund"] == 1101
    assert max(batches) == refreshing.STAGING_BATCH_ROWS and sum(batches) == total
    assert len(batches) > total // refreshing.STAGING_BATCH_ROWS
    staging = [current for current, phase in reports if phase == "staging"]
    # One report at the start, then one per 500 staged rows, then validating.
    assert staging[0] == 0 and len(staging) == 1 + total // 500
    assert all(b - a >= 500 for a, b in zip(staging, staging[1:], strict=False))
    assert reports[-1] == (total, "validating")


def test_interrupted_staging_keeps_only_whole_committed_batches(tmp_path, monkeypatch):
    """A failure mid-staging leaves whole earlier batches, each row once (#394).

    Each small batch still commits atomically with its own fence check. The
    snapshot stays "staging": a retry is a new claim with a new attempt, and
    the next successful owner retires this one without reusing it (see
    test_successful_new_owner_retires_but_never_reuses_old_staging).
    """
    from parishkit.stewardship.source.snapshots import snapshot_manifest

    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    with_extra_funds(monkeypatch, 400)
    batches, _ = spy_staging(monkeypatch)
    real, funds = refreshing.stage_entities, []

    def fail_second_fund_batch(snapshot_id, claim, *, kind, **options):
        """Stage the first Fund batch, then fail inside the second one."""
        if kind == "fund":
            funds.append(1)
            if len(funds) == 2:
                real(snapshot_id, claim, kind=kind, **options)
                raise RuntimeError("synthetic interruption")
        return real(snapshot_id, claim, kind=kind, **options)

    monkeypatch.setattr(refreshing, "stage_entities", fail_second_fund_batch)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        run(credential, execution, lease)
    snapshot = SourceSnapshot.objects.get()
    staged = snapshot_manifest(snapshot.pk)
    # The failed batch rolled back with its effect; the rows before it stayed.
    assert len(staged["fund"]) == refreshing.STAGING_BATCH_ROWS
    assert sum(len(rows) for rows in staged.values()) == sum(batches[:-1])
    assert snapshot.state == "staging" and not snapshot.counts
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"


@pytest.mark.parametrize("value,logged", [(None, None), ("25", None), ("100", 100)])
def test_every_refresh_under_a_loss_limit_override_logs_a_warning(
    tmp_path, monkeypatch, caplog, value, logged
):
    """#320: a forgotten override shows in normal logs on every refresh."""
    import logging

    from parishkit.stewardship.observability import Event
    from parishkit.stewardship.source.loading import DROP_OVERRIDE_VARIABLE

    monkeypatch.delenv(DROP_OVERRIDE_VARIABLE, raising=False)
    if value is not None:
        monkeypatch.setenv(DROP_OVERRIDE_VARIABLE, value)
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        result = run(credential, execution, lease)
    assert result.state == "ready"
    assert result.cursor["load"]["maximum_drop_percent"] == int(value or 25)
    warnings = [
        record.extra["source_max_drop_percent"]
        for record in caplog.records
        if record.msg is Event.TASK_STARTED and record.levelno == logging.WARNING
    ]
    assert warnings == ([] if logged is None else [logged])


def test_bad_provider_data_does_not_stage_any_entities(tmp_path, monkeypatch):
    """Normalization must finish before any source collection is staged."""
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages(member_change={"birthdate": "INVALID-PRIVATE"}))
    with pytest.raises(InvalidSourcePayload):
        run(credential, execution, lease)
    snapshot = SourceSnapshot.objects.get()
    assert snapshot.state == "staging" and not snapshot.counts
    assert SourceCurrent.objects.get().snapshot_id is None
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"


def test_wrong_loaded_key_cannot_start_observation(tmp_path, monkeypatch):
    """A key mismatch stops before manifest creation or any helper subprocess."""
    _, execution, lease, *_ = setup(tmp_path)
    _, calls = fake_provider(monkeypatch, [])
    with pytest.raises(PermissionError):
        run(SourceCredential(b"WRONG-SYNTHETIC"), execution, lease)
    assert not calls and not SourceSnapshot.objects.exists()


def test_observation_requires_maintained_task_and_source(tmp_path):
    """Durable lease possession alone does not authorize a lengthy provider scan."""
    credential, execution, lease, *_ = setup(tmp_path)
    with pytest.raises(StorageInvariantError, match="maintained"):
        load_and_stage_attempt(execution, lease, credential)
    assert not SourceSnapshot.objects.exists()


def test_campaign_changed_between_reads_cannot_stage_old_request(tmp_path, monkeypatch):
    """A later page repeats current-scope admission rather than trusting startup."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    calls = []

    def exchange(payload, **kwargs):
        """Simulate an independent configuration commit during a provider wait."""
        assert connection.connection is None
        calls.append(1)
        add_draft(store, version, actor)
        connections.close_all()
        return b'200\n[{"organizationID":12345}]'

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    with pytest.raises(PermissionError):
        run(credential, execution, lease)
    assert len(calls) == 1
    snapshot = SourceSnapshot.objects.get()
    assert snapshot.state == "staging" and not snapshot.counts
    assert SourceCurrent.objects.get().snapshot_id is None


def seed_full(credential, execution, lease, *, load=None):
    """Seed complete real converter output without consuming an HTTP safety window.

    ``load(as_of)``, when given, returns the ``SourceLoad`` to seed instead of
    the converter fixture, so its corpus and evidence are promoted as a real
    full refresh's would be.
    """
    from dataclasses import replace

    from parishkit.stewardship.source.corpus import normalize_core

    from ..test_source_corpus import source

    attempt = begin_refresh_attempt(execution, lease, credential)
    snapshot = attempt.snapshot
    # Use the same admitted parish-day observation as the real full loader.
    # UTC's calendar date can already be tomorrow in an evening parish test.
    as_of = _inputs(attempt.pk, execution, lease).as_of
    data = replace(source(), organization_id=12345)
    for family in data.families.values():
        family["registeredOrganizationID"] = 12345
    corpus = normalize_core(data, as_of=as_of)
    evidence = {
        "giving_as_of_date": as_of.isoformat(),
        "anonymous_pledges": 0,
        "anonymous_contributions": 0,
    }
    if load is not None:
        loaded = load(as_of)
        corpus, evidence = loaded.corpus, loaded.evidence
    with execution.effect():
        for kind, rows in corpus.items():
            stage_entities(snapshot.pk, lease, kind=kind, entities=rows, admit=permit)
        cursor = refresh_cursor(
            snapshot_id=snapshot.pk,
            kind="full",
            started_at=snapshot.started_at,
            window_digest=attempt.request.window_digest,
            evidence=evidence,
        )
        snapshot = finish_snapshot(
            snapshot.pk,
            lease,
            expected_counts={k: len(v) for k, v in corpus.items()},
            cursor=cursor,
            admit=permit,
        )
        promote_snapshot(snapshot.pk, lease, admit=permit, reconcile=permit)
        release_source(lease)
    execution.transition("complete")
    return snapshot


@pytest.mark.parametrize(
    "instant,expected",
    [
        (datetime(2026, 1, 1, 2, tzinfo=UTC), date(2025, 12, 31)),
        (datetime(2026, 7, 1, 2, tzinfo=UTC), date(2026, 6, 30)),
    ],
)
def test_refresh_observation_day_uses_parish_timezone(
    tmp_path, monkeypatch, instant, expected
):
    """Exercise winter/summer UTC-midnight boundaries without changing DB clocks."""
    from parishkit.stewardship.source import refreshing

    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    verify = refreshing.verify_refresh_attempt

    def observed_at(*args, **kwargs):
        """Keep real admission, replacing only its returned observation timestamp."""
        checked = verify(*args, **kwargs)
        checked.snapshot.started_at = instant
        return checked

    monkeypatch.setattr(refreshing, "verify_refresh_attempt", observed_at)
    assert _inputs(attempt.pk, execution, lease).as_of == expected


def next_delta():
    """Acquire a new concrete delta claim after the seed full observation ended."""
    execution = claim_request(command(cause="delta", actor_id=None))
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="delta",
        )
    return execution, lease


def test_delta_uses_only_promoted_base_and_preserves_full_provenance(
    tmp_path, monkeypatch
):
    """No indications still yields a complete candidate, not partial truth."""
    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    execution, lease = next_delta()
    remaining, calls = fake_provider(
        monkeypatch,
        [[{"organizationID": 12345}], [], [{"famGroupID": 7, "famGroup": "Active"}]],
    )
    result = run(credential, execution, lease)
    assert not remaining and len(calls) == 3
    assert result.state == "ready" and result.base_id == first.pk
    assert result.counts == first.counts
    assert result.cursor["full_snapshot_id"] == str(first.pk)
    assert SourceCurrent.objects.get().snapshot_id == first.pk


def test_delta_changed_window_requires_full_without_any_provider_read(
    tmp_path, monkeypatch
):
    """A new request cannot apply the previous campaign's delta coverage."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    add_draft(store, version, actor)
    execution, lease = next_delta()
    _, calls = fake_provider(monkeypatch, [])
    with pytest.raises(ChangeFeedIncomplete):
        run(credential, execution, lease)
    assert not calls and SourceCurrent.objects.get().snapshot_id == first.pk
    assert SourceSnapshot.objects.exclude(pk=first.pk).get().state == "staging"


@pytest.mark.parametrize("cause,phase", [("delta", "delta"), ("manual", "full")])
def test_refresh_compares_eligibility_with_the_current_snapshot(tmp_path, cause, phase):
    """#320: both refresh kinds compare eligibility with the current snapshot.

    The seeded snapshot predates recorded derived counts, so they are counted
    from its rows; the last full snapshot (the same one) has none to add.
    """
    from parishkit.stewardship.source.loading import derived_counts
    from parishkit.stewardship.source.snapshots import reconstruct_snapshot

    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    execution = claim_request(
        command(cause=cause)
        if cause == "manual"
        else command(cause=cause, actor_id=None)
    )
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase=phase,
        )
    attempt = begin_refresh_attempt(execution, lease, credential)
    inputs = _inputs(attempt.pk, execution, lease)
    expected = derived_counts(reconstruct_snapshot(first.pk))
    assert inputs.previous_derived_counts == expected
    assert expected["portal_eligible_families"] > 0
    assert expected["valid_email_contacts"] > 0
    assert (inputs.base is None) == (phase == "full")


def test_refresh_inputs_compare_with_the_full_refresh_trend(tmp_path, monkeypatch):
    """#387: both baselines come from the trend of recent full refreshes.

    An older full in the trend with larger counts raises both the record and
    the derived baseline; the newest full's own kinds are kept.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.source.loading import derived_counts
    from parishkit.stewardship.source.snapshots import reconstruct_snapshot

    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    recorded = derived_counts(reconstruct_snapshot(first.pk))
    older = SimpleNamespace(
        counts={kind: value + 50 for kind, value in first.counts.items()},
        cursor={"load": {"derived_counts": {k: v + 7 for k, v in recorded.items()}}},
    )
    seen = []

    def trend(full, since):
        """The real newest full, plus a larger older one from the same week."""
        seen.append((full.pk, since))
        return [full, older]

    monkeypatch.setattr(refreshing, "_trend", trend)
    execution = claim_request(command(cause="manual"))
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )
    attempt = begin_refresh_attempt(execution, lease, credential)
    inputs = _inputs(attempt.pk, execution, lease)
    ((full_id, since),) = seen
    assert full_id == first.pk
    assert attempt.snapshot.started_at - since == refreshing.timedelta(
        days=refreshing.TREND_DAYS
    )
    assert inputs.previous_full_counts == older.counts
    assert inputs.previous_derived_counts == older.cursor["load"]["derived_counts"]


def next_full():
    """Acquire a new manual full claim after the seed full observation ended."""
    execution = claim_request(command(cause="manual"))
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )
    return execution, lease


def listed_full(tmp_path):
    """An offline full load of ``pages()`` whose contact list has its Member.

    Seeding it (rather than running it through the faked transport) leaves no
    HTTP safety window, so a second full refresh can start at once.
    """
    from test_parishsoft import Session
    from test_parishsoft_source import response

    from parishkit.parishsoft import ParishSoftConfig
    from parishkit.parishsoft_source import CoherentParishSoftClient
    from parishkit.stewardship.source.loading import load_full_source
    from parishkit.stewardship.source.windows import RefreshWindow

    values = pages()
    member_id = values[4][0]["memberDUID"]
    values = with_contact_list(values, {"memberDUID": member_id, "emailAddress": None})

    def load(as_of):
        """The real full loader over a synthetic Session; no network."""
        client = CoherentParishSoftClient(
            ParishSoftConfig(api_key="SYNTHETIC", cache_dir=tmp_path / "unused"),
            organization_id=12345,
            session=Session([response(value) for value in values]),
        )
        return load_full_source(client, window=RefreshWindow(None, ()), as_of=as_of)

    return load


def test_a_first_full_load_records_contact_coverage_without_refusing(
    tmp_path, monkeypatch
):
    """#387 M4: with no recorded baseline the coverage is only recorded.

    Even with no margin at all, the contact list leaving out the only Member
    is recorded in the manifest, and the load is ready as before.
    """
    monkeypatch.setattr(loading, "CONTACT_MISSING_MARGIN", 0)
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages())
    result = run(credential, execution, lease)
    assert result.state == "ready"
    assert result.cursor["load"][CONTACT_COVERAGE_KEY] == {
        "members": 1,
        "contact_infos": 0,
        "missing": 1,
    }


def test_a_jump_in_members_left_off_the_contact_list_is_retried(tmp_path, monkeypatch):
    """#387 M4: a rise past the margin is a retryable shifted scan.

    The promoted full recorded nobody left out; the next full's contact list
    leaves out its one Member. With the margin lowered to fit a one-Member
    fixture, that is a cut-short list: retryable, nothing staged, and the
    previous snapshot stays current.
    """
    from parishkit.parishsoft_pagination import ShiftedSourceScan
    from parishkit.stewardship.source.failures import classify_read_failure

    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease, load=listed_full(tmp_path))
    assert first.cursor["load"][CONTACT_COVERAGE_KEY]["missing"] == 0
    monkeypatch.setattr(loading, "CONTACT_MISSING_MARGIN", 0)
    execution, lease = next_full()
    fake_provider(monkeypatch, pages())
    with pytest.raises(ShiftedSourceScan) as raised:
        run(credential, execution, lease)
    failure = classify_read_failure(raised.value, has_source_claim=True)
    assert failure.retry and failure.failure == "shifted_scan"
    # Members, contact infos, missing, baseline missing, allowance: what the
    # process log reports so the operator can judge the refusal.
    assert failure.coverage == (1, 0, 1, 0, 0)
    refused = SourceSnapshot.objects.exclude(pk=first.pk).get()
    assert refused.state == "staging" and not refused.counts
    assert SourceCurrent.objects.get().snapshot_id == first.pk


def test_ordinary_contact_list_variation_promotes_and_becomes_the_baseline(
    tmp_path, monkeypatch
):
    """#387 M4: a rise within the margin promotes and records its own count."""
    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease, load=listed_full(tmp_path))
    execution, lease = next_full()
    seen = []
    real_inputs = refreshing._inputs

    def inputs(*args):
        """The real inputs, kept so the test sees the baseline passed on."""
        seen.append(real_inputs(*args))
        return seen[-1]

    monkeypatch.setattr(refreshing, "_inputs", inputs)
    fake_provider(monkeypatch, pages())
    result = run(credential, execution, lease)
    assert seen[0].previous_contact_missing == 0
    assert result.state == "ready"
    assert result.cursor["load"][CONTACT_COVERAGE_KEY] == {
        "members": 1,
        "contact_infos": 0,
        "missing": 1,
    }
    with execution.effect():
        promote_snapshot(result.pk, lease, admit=permit, reconcile=permit)
    assert SourceCurrent.objects.get().snapshot_id == result.pk != first.pk
    current = SourceSnapshot.objects.get(pk=result.pk)
    assert loading.recorded_contact_missing(current.cursor) == 1


def test_a_quick_update_after_the_last_full_is_not_the_contact_baseline(
    tmp_path, monkeypatch
):
    """#387 M4: the baseline is the last promoted full, not the current snapshot.

    A quick update does not read the contact list, so it records no count. If
    a quick update promoted after the last full became the baseline, the next
    full would see none and silently skip the check.
    """
    from parishkit.stewardship.source import transport

    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease, load=listed_full(tmp_path))
    execution, lease = next_delta()
    fake_provider(
        monkeypatch,
        [[{"organizationID": 12345}], [], [{"famGroupID": 7, "famGroup": "Active"}]],
    )
    with monkeypatch.context() as patch:
        # The faked calls reserve no HTTP safety window, so the next full
        # can take the source at once; safety windows are tested elsewhere.
        patch.setattr(transport, "reserve_source_request", lambda *a, **k: None)
        quick = run(credential, execution, lease)
    with execution.effect():
        promote_snapshot(quick.pk, lease, admit=permit, reconcile=permit)
        release_source(lease)
    execution.transition("complete")
    assert SourceCurrent.objects.get().snapshot_id == quick.pk != first.pk
    promoted = SourceSnapshot.objects.get(pk=quick.pk)
    assert loading.recorded_contact_missing(promoted.cursor) is None
    execution, lease = next_full()
    attempt = begin_refresh_attempt(execution, lease, credential)
    assert _inputs(attempt.pk, execution, lease).previous_contact_missing == 0
