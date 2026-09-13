"""Actual competing connections exercise Family admission and source ordering."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from time import monotonic, sleep

import pytest
from django.db import connection, connections

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.baselines import (
    FamilyAdmissionDenied,
    end_baseline,
    issue_baseline,
)
from parishkit.stewardship.responses.models import (
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source import snapshots
from parishkit.stewardship.source.compaction import compact_source
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceCurrent, SourceSnapshotPin

from .response_builders import response_source
from .source_builders import running_source_task
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def wait_for_work_lock(pid):
    """Require a real blocked SQL contender, not a sleep-based ordering guess."""
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s "
                "AND locktype='advisory' AND classid=736220 AND objid=1 "
                "AND objsubid=2 AND NOT granted)",
                [pid],
            )
            if cursor.fetchone()[0]:
                return
        sleep(0.01)
    pytest.fail("Contender did not reach the common work lock")


def contender(ready, action):
    """Own a distinct connection and always close it before fixture teardown."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            ready.put(cursor.fetchone()[0])
        return action()
    finally:
        connections.close_all()


@pytest.mark.parametrize("first_owner", ["promotion", "submission"])
@pytest.mark.parametrize("relevant", [False, True])
def test_source_promotion_and_submit_serialize(
    live_response_service, first_owner, relevant
):
    """Either winner yields one coherent response or an explicit fresh review."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Family edit"
    data = response_source()
    if relevant:
        data.members[3]["firstName"] = "Source edit"
    else:
        data.families[2]["lastName"] = "Unrelated Family edit"
    snapshot, claim = prepare(data)
    ready = Queue()

    def promote_now():
        """Use the real source owner and all its transactional domain effects."""
        return promote(snapshot, claim, harness.campaign, harness.rings)

    def submit_now():
        """Use the original admitted browser baseline, not a rebuilt shortcut."""
        return submit(harness, form, answers)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            future = pool.submit(
                contender,
                ready,
                submit_now if first_owner == "promotion" else promote_now,
            )
            wait_for_work_lock(ready.get(timeout=5))
            result = promote_now() if first_owner == "promotion" else submit_now()
            assert not future.done()
        later = future.result(timeout=10)
    result = later if first_owner == "promotion" else result
    if first_owner == "promotion" and relevant:
        assert result.submission is None and result.refreshed is not None
        assert result.refreshed.baseline.source_id == snapshot.pk
        assert not Submission.objects.exists()
        assert not SubmissionReceiptOccurrence.objects.exists()
    else:
        response = result.submission
        assert response is not None and result.refreshed is None
        assert (
            Submission.objects.count()
            == SubmissionReceiptOccurrence.objects.count()
            == 1
        )
        assert response.reviewed_source_id == form.baseline.source_id
        assert response.validation_source_id == (
            snapshot.pk if first_owner == "promotion" else form.baseline.source_id
        )
        proposal = ProposedChange.objects.get(submission=response)
        assert proposal.execution == ("conflict" if relevant else "pending")
        for source_id in {
            response.reviewed_source_id,
            response.validation_source_id,
            proposal.current_source_id,
        }:
            assert SourceSnapshotPin.objects.filter(
                parent_kind="submission", parent_id=response.pk, snapshot_id=source_id
            ).exists()


def test_simultaneous_duplicate_submit_has_one_atomic_winner(live_response_service):
    """Two simultaneous retries cannot create two responses or two receipts."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    ready = Queue()

    def submit_once():
        """A lost/revoked session is an expected loser, not a second acceptance."""
        try:
            return "accepted" if submit(harness, form, answers).submission else "stale"
        except FamilyAdmissionDenied:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        with work_transaction():
            futures = [pool.submit(contender, ready, submit_once) for _ in range(2)]
            for _ in futures:
                wait_for_work_lock(ready.get(timeout=5))
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == ["accepted", "denied"]
    assert (
        Submission.objects.count() == SubmissionReceiptOccurrence.objects.count() == 1
    )


@pytest.mark.parametrize("first_owner", ["promotion", "baseline"])
def test_baseline_creation_races_real_promotion(live_response_service, first_owner):
    """Issuance pins whichever complete source wins the shared admission order."""
    harness = live_response_service
    data = response_source()
    data.members[3]["firstName"] = "New source name"
    snapshot, claim = prepare(data)
    ready = Queue()

    def begin_now():
        """Issue and materialize the real Family-scoped pinned form inputs."""
        return issue_baseline(harness.request, harness.service)

    def promote_now():
        """Promote a complete source generation and its derived Family effects."""
        return promote(snapshot, claim, harness.campaign, harness.rings)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            future = pool.submit(
                contender,
                ready,
                begin_now if first_owner == "promotion" else promote_now,
            )
            wait_for_work_lock(ready.get(timeout=5))
            result = promote_now() if first_owner == "promotion" else begin_now()
        later = future.result(timeout=10)
    form = later if first_owner == "promotion" else result
    expected_source = snapshot.pk if first_owner == "promotion" else harness.snapshot.pk
    expected_name = "New source name" if first_owner == "promotion" else "Member"
    assert form.baseline.source_id == expected_source
    assert (
        next(
            field
            for field in form.inputs.fields
            if field.entity == "member" and field.field == "first_name"
        ).source.value
        == expected_name
    )
    assert SourceSnapshotPin.objects.filter(
        parent_kind="form_baseline",
        parent_id=form.baseline.pk,
        snapshot_id=expected_source,
    ).exists()


@pytest.mark.parametrize("finish", ["submit", "cancel"])
def test_compaction_waits_for_final_form_pin_lifetime(request, monkeypatch, finish):
    """Compaction sees a permanent Submit pin or completed cancellation, never a gap."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        old_time = (cursor.fetchone()[0] - timedelta(days=100)).replace(
            hour=10, minute=0, second=0
        )
    with monkeypatch.context() as patch:
        patch.setattr(snapshots, "_now", lambda: old_time)
        harness = request.getfixturevalue("live_response_service")
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    # A later snapshot is that day's retention anchor, leaving the actual form
    # baseline compactable except for its real expiring/permanent parent pin.
    with monkeypatch.context() as patch:
        patch.setattr(snapshots, "_now", lambda: old_time + timedelta(hours=1))
        anchor, claim = prepare(response_source())
        promote(anchor, claim, harness.campaign, harness.rings)
    current, claim = prepare(response_source())
    promote(current, claim, harness.campaign, harness.rings)
    claim = acquire_source(**running_source_task(), phase="compaction")
    ready = Queue()

    def compact_now():
        """Run the actual bounded compactor with its own live source ownership."""
        try:
            return compact_source(claim, admit=permit)
        finally:
            release_source(claim)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            SourceCurrent.objects.select_for_update().get()
            future = pool.submit(contender, ready, compact_now)
            pid = ready.get(timeout=5)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT cardinality(pg_blocking_pids(%s)) > 0", [pid]
                    )
                    if cursor.fetchone()[0]:
                        break
                sleep(0.01)
            else:
                pytest.fail("Compactor did not wait for the current source lock")
            if finish == "submit":
                assert submit(harness, form, answers).submission is not None
            else:
                end_baseline(form.baseline, state="cancelled")
        result = future.result(timeout=10)
    assert result.snapshot_count == (0 if finish == "submit" else 1)
    harness.snapshot.refresh_from_db()
    assert (harness.snapshot.compacted_at is None) == (finish == "submit")
