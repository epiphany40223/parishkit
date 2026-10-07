"""Producer traversal boundaries do not need private data or a live scheduler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns import schedule_production as module
from parishkit.stewardship.campaigns.family_schedule_planning import (
    PREPARE_AHEAD,
    FamilyPlanningResult,
)
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.storage import StorageInvariantError

NOW = datetime(2054, 10, 2, 12, tzinfo=UTC)


@pytest.mark.parametrize("limit", [0, 101, True, "20"])
def test_invalid_page_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="bounded process identity"):
        module.FamilyScheduleProducer(uuid4(), limit=limit)


def test_invalid_worker_and_fake_guard_are_rejected():
    """Reject forged caller identities before touching persistence."""
    with pytest.raises(ValueError, match="bounded process identity"):
        module.FamilyScheduleProducer("worker")
    with pytest.raises(TypeError, match="scheduler ownership"):
        module.FamilyScheduleProducer(uuid4())(object())


def test_nested_transaction_is_rejected(monkeypatch):
    """Traversal may not inherit a caller's long-lived transaction."""
    monkeypatch.setattr(module, "connection", Mock(in_atomic_block=True))
    with pytest.raises(StorageInvariantError, match="own its transactions"):
        module.FamilyScheduleProducer(uuid4())(Mock(spec=SchedulerGuard))


class Clock:
    """A settable monotonic clock for the sweep's checks and its budget."""

    def __init__(self):
        """Start at zero."""
        self.value = 0.0

    def __call__(self):
        """Return the current reading."""
        return self.value


class Inputs:
    """What the two fingerprint reads return, editable between loops."""

    def __init__(self, identifiers):
        """Every Family starts with fingerprint 0; nothing is due."""
        self.families = dict.fromkeys(identifiers, 0)
        self.campaign, self.now, self.edges = 0, NOW, ()
        self.family_reads = 0

    def campaign_inputs(self, campaign_id, *, ahead):
        """The campaign clock, campaign-wide fingerprint and clock edges."""
        return self.now, self.campaign, self.edges

    def family_inputs(self, campaign_id):
        """Each Family's fingerprint, counting the reads."""
        self.family_reads += 1
        return dict(self.families)


def sweep(monkeypatch, identifiers, planner, *, mode="production"):
    """Patch one campaign's Families, inputs and planner; return the inputs.

    The change check runs on every loop here, unless a test sets
    CHANGE_CHECK_SECONDS itself.
    """
    monkeypatch.setattr(module, "connection", Mock(in_atomic_block=False))
    runtime = {
        "version": 1,
        "mode": mode,
        "current_campaign_id": uuid4(),
        "active_configuration_id": uuid4(),
    }
    monkeypatch.setattr(module, "_runtime", lambda: runtime)
    inputs = Inputs(identifiers)
    monkeypatch.setattr(module, "_campaign_inputs", inputs.campaign_inputs)
    monkeypatch.setattr(module, "_family_inputs", inputs.family_inputs)
    monkeypatch.setattr(module, "CHANGE_CHECK_SECONDS", 0)
    monkeypatch.setattr(module, "ensure_preparation_epoch", Mock())
    monkeypatch.setattr(module, "plan_family", planner)
    monkeypatch.setattr(module, "_enqueue", Mock(return_value=True))
    inputs.runtime = runtime
    return inputs


def idle_planner():
    """A planner that finds nothing to prepare."""
    return Mock(side_effect=lambda guard, **kw: FamilyPlanningResult(kw["family_id"]))


def planned(planner):
    """The Families a planner mock was asked to plan, in order."""
    return [call.kwargs["family_id"] for call in planner.call_args_list]


def drain(producer, guard, limit=100):
    """Run loops until nothing is queued; return how many loops ran."""
    for loops in range(1, limit + 1):
        producer(guard)
        if not producer.pending:
            return loops
    raise AssertionError("The sweep never drained.")


def test_missing_campaign_resets_traversal_without_reading_families(monkeypatch):
    """Removing the current campaign also discards the old traversal state."""
    inputs = sweep(monkeypatch, [], Mock())
    inputs.runtime["current_campaign_id"] = None
    producer = module.FamilyScheduleProducer(uuid4())
    producer.campaign_id, producer.pending = uuid4(), {uuid4(): None}
    assert producer(Mock(spec=SchedulerGuard)) == ()
    assert producer.campaign_id is None and not producer.pending
    assert inputs.family_reads == 0


@pytest.mark.parametrize(
    "error", [PermissionError("scope"), StorageInvariantError("group")]
)
def test_one_failed_group_does_not_starve_the_next(monkeypatch, error):
    """A scoped failure advances only that Family, with safe invariant reporting."""
    identifiers = sorted([uuid4(), uuid4()])
    completed = FamilyPlanningResult(identifiers[-1])
    planner = Mock(side_effect=[error, completed])
    sweep(monkeypatch, identifiers, planner)
    failure = Mock()
    monkeypatch.setattr(module, "emit_failure", failure)
    producer = module.FamilyScheduleProducer(uuid4(), limit=2)
    assert producer(Mock(spec=SchedulerGuard)) == (completed,)
    assert not producer.pending and producer.last == identifiers[-1]
    assert planner.call_count == 2
    assert failure.call_count == int(isinstance(error, StorageInvariantError))


def test_an_unexpected_error_leaves_the_family_queued_at_the_back(monkeypatch):
    """Only a handled outcome dequeues a Family; a crash retries it next loop."""
    identifiers = sorted([uuid4(), uuid4()])
    planner = Mock(side_effect=RuntimeError("database went away"))
    sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    with pytest.raises(RuntimeError):
        producer(Mock(spec=SchedulerGuard))
    assert list(producer.pending) == [identifiers[1], identifiers[0]]


def test_a_family_that_keeps_failing_does_not_block_the_others(monkeypatch):
    """Head-of-line: the failing Family moves back, so the rest are planned."""
    identifiers = sorted(uuid4() for _ in range(3))

    def plan(guard, **kw):
        """The first Family always fails unexpectedly."""
        if kw["family_id"] == identifiers[0]:
            raise RuntimeError("lock timeout")
        return FamilyPlanningResult(kw["family_id"])

    planner = Mock(side_effect=plan)
    sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            producer(guard)
    assert planned(planner) == [
        identifiers[0],
        identifiers[1],
        identifiers[2],
        identifiers[0],
    ]
    assert list(producer.pending) == [identifiers[0]]


class Commit:
    """A stand-in work-order transaction whose commit may fail."""

    fail = False
    opened = 0

    def __enter__(self):
        """Open the chunk."""
        Commit.opened += 1
        return self

    def __exit__(self, kind, error, trace):
        """Commit (or roll back on error); fail the commit when told to."""
        if kind is None and Commit.fail:
            raise RuntimeError("could not serialize access")
        return False


class Pace:
    """A bulk pace that always fits: one chunk per page."""

    def fits(self):
        """Never end the chunk early."""
        return True

    def ran(self, started):
        """Ignore the timing."""


def bulk(monkeypatch):
    """Patch the bulk chunk's lock, pace, savepoint and scope memory."""
    from contextlib import nullcontext

    from parishkit.stewardship.campaigns import work_locks
    from parishkit.stewardship.jobs import admission, family_mail_bulk

    Commit.fail, Commit.opened = False, 0
    monkeypatch.setattr(work_locks, "work_transaction", Commit)
    monkeypatch.setattr(family_mail_bulk, "_Pace", Pace)
    monkeypatch.setattr(admission, "remembered_scopes", nullcontext)
    monkeypatch.setattr(module, "transaction", Mock(atomic=nullcontext))
    monkeypatch.setattr(module, "_warn_refresh_in_lead_window", Mock())


def test_a_failed_bulk_commit_leaves_its_families_queued(monkeypatch):
    """Bulk Families are dequeued only when their chunk commits."""
    identifiers = sorted(uuid4() for _ in range(3))
    planner = idle_planner()
    sweep(monkeypatch, identifiers, planner)
    bulk(monkeypatch)
    producer = module.FamilyScheduleProducer(uuid4(), bulk=True)
    guard = Mock(spec=SchedulerGuard)
    Commit.fail = True
    with pytest.raises(RuntimeError, match="serialize"):
        producer(guard)
    assert list(producer.pending) == identifiers
    Commit.fail = False
    assert len(producer(guard)) == 3
    assert not producer.pending and Commit.opened == 2


def test_a_rolled_back_bulk_chunk_requeues_all_of_its_families(monkeypatch):
    """A failure mid-chunk keeps the chunk's earlier Families queued too."""
    identifiers = sorted(uuid4() for _ in range(3))

    def plan(guard, **kw):
        """The second Family fails, rolling back the first's plan."""
        if kw["family_id"] == identifiers[1]:
            raise RuntimeError("lock timeout")
        return FamilyPlanningResult(kw["family_id"])

    sweep(monkeypatch, identifiers, Mock(side_effect=plan))
    bulk(monkeypatch)
    producer = module.FamilyScheduleProducer(uuid4(), bulk=True)
    with pytest.raises(RuntimeError):
        producer(Mock(spec=SchedulerGuard))
    assert list(producer.pending) == [identifiers[0], identifiers[2], identifiers[1]]


def test_sweep_plans_a_full_page_while_it_finds_mail(monkeypatch):
    """A launch send plans 100 Families per loop, then 20 once idle (#394)."""
    identifiers = [uuid4() for _ in range(250)]

    def plan(guard, *, family_id, worker_id):
        """Only the first page has new mail to prepare."""
        selected = uuid4() if planner.call_count <= 100 else None
        return FamilyPlanningResult(family_id, selected=selected)

    planner = Mock(side_effect=plan)
    sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    assert producer.limit == module.FAMILY_SWEEP_LIMIT == 100
    guard = Mock(spec=SchedulerGuard)
    assert len(producer(guard)) == 100
    assert producer.busy and producer.last == sorted(identifiers)[99]
    assert len(producer(guard)) == 100
    assert not producer.busy
    assert len(producer(guard)) == module.IDLE_FAMILY_SWEEP_LIMIT == 20
    assert planned(planner) == sorted(identifiers)[:220]


def test_sweep_stops_at_its_time_budget_and_resumes(monkeypatch):
    """A slow loop keeps the rest queued rather than skipping past them."""
    identifiers = sorted(uuid4() for _ in range(5))
    clock = Clock()
    monkeypatch.setattr(module, "monotonic", clock)
    readings = iter([10, 31, 99])

    def plan(guard, **kw):
        """Each Family takes the next step of wall time."""
        clock.value = next(readings)
        return FamilyPlanningResult(kw["family_id"])

    sweep(monkeypatch, identifiers, Mock(side_effect=plan))
    emitted = Mock()
    monkeypatch.setattr(module, "emit", emitted)
    producer = module.FamilyScheduleProducer(uuid4(), seconds=30)
    assert module.FamilyScheduleProducer(uuid4()).seconds == 15
    assert len(producer(Mock(spec=SchedulerGuard))) == 2
    # The budget ended the page: the other three stay queued, in order.
    assert list(producer.pending) == identifiers[2:]
    # The budget ending the page is logged with its limit and elapsed time.
    emitted.assert_called_once_with(
        module.Event.WORK_BUDGET_REACHED,
        timeout="family_sweep_budget",
        limit_seconds=30,
        elapsed_seconds=31,
    )


@pytest.mark.parametrize("seconds", [0, 61, True, "30"])
def test_invalid_time_budget_is_rejected(seconds):
    with pytest.raises(ValueError, match="bounded process identity"):
        module.FamilyScheduleProducer(uuid4(), seconds=seconds)


def test_reselected_held_work_does_not_keep_the_full_page(monkeypatch):
    """Only newly allocated preparation keeps the 100-Family page.

    A held occurrence (a paused send) is selected again every loop and
    reuses its ticket; that is not new work.
    """
    identifiers = [uuid4() for _ in range(250)]
    planner = Mock(
        side_effect=lambda guard, **kw: FamilyPlanningResult(
            kw["family_id"], selected=uuid4()
        )
    )
    sweep(monkeypatch, identifiers, planner)
    monkeypatch.setattr(module, "_enqueue", Mock(return_value=False))
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    assert len(producer(guard)) == 100
    assert not producer.busy
    assert len(producer(guard)) == 20


def test_enqueue_reports_only_a_new_ticket(monkeypatch):
    """An existing ticket for the occurrence is not new preparation work."""
    from types import SimpleNamespace

    from parishkit.stewardship.jobs import family_mail_models, family_mail_tasks

    existing, fresh = uuid4(), uuid4()
    model = Mock()
    model.objects.filter.return_value.values_list.return_value = [existing]
    monkeypatch.setattr(family_mail_models, "FamilyMailPreparation", model)
    for ticket, expected in (
        (SimpleNamespace(pk=existing), False),
        (SimpleNamespace(pk=fresh), True),
        (None, False),
    ):
        monkeypatch.setattr(
            family_mail_tasks, "enqueue_preparation", Mock(return_value=ticket)
        )
        assert module._enqueue(Mock(), uuid4()) is expected


def test_sweep_logs_its_counts_for_debugging(monkeypatch, caplog):
    """Each sweep writes a DEBUG line with what it visited (#394)."""
    import logging

    sweep(monkeypatch, [uuid4() for _ in range(3)], idle_planner())
    emitted = Mock()
    monkeypatch.setattr(module, "emit", emitted)
    with caplog.at_level(logging.DEBUG, logger="parishkit.stewardship.debug"):
        module.FamilyScheduleProducer(uuid4())(Mock(spec=SchedulerGuard))
    emitted.assert_not_called()
    assert "visited 3 of a 100-Family page" in caplog.text
    assert "budget not reached" in caplog.text


def test_a_held_gate_resumes_with_a_full_pass_at_the_full_page(monkeypatch):
    """After Testing preparation was held, every Family is planned again."""
    identifiers = sorted(uuid4() for _ in range(3))
    planner = idle_planner()
    sweep(monkeypatch, identifiers, planner, mode="testing")
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    drain(producer, guard)
    held = Mock(side_effect=PermissionError)
    monkeypatch.setattr(module, "ensure_preparation_epoch", held)
    producer.busy = False
    assert producer(guard) == ()
    assert producer.busy and not producer.known
    monkeypatch.setattr(module, "ensure_preparation_epoch", Mock())
    planner.reset_mock()
    drain(producer, guard)
    assert planned(planner) == identifiers


def test_production_does_not_look_for_a_rehearsal_epoch(monkeypatch):
    """Only Testing needs the epoch helper; Production skips its reads."""
    sweep(monkeypatch, [uuid4()], idle_planner())
    module.FamilyScheduleProducer(uuid4())(Mock(spec=SchedulerGuard))
    module.ensure_preparation_epoch.assert_not_called()


def test_an_idle_system_plans_nothing_after_its_first_pass(monkeypatch):
    """Once every Family is planned, unchanged inputs plan no Family (#640)."""
    identifiers = [uuid4() for _ in range(45)]
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    drain(producer, guard)
    assert sorted(planned(planner)) == sorted(identifiers)
    planner.reset_mock()
    guard.reset_mock()
    for _ in range(50):
        assert producer(guard) == ()
    planner.assert_not_called()
    # An idle loop checks scheduler ownership once, not per Family.
    assert guard.check.call_count == 50
    # One read per check; nothing more per Family.
    assert inputs.family_reads <= 1 + 50


def test_family_inputs_are_read_at_most_once_per_check_interval(monkeypatch):
    """Between checks a loop reads no fingerprints at all."""
    clock = Clock()
    monkeypatch.setattr(module, "monotonic", clock)
    inputs = sweep(monkeypatch, [uuid4()], idle_planner())
    monkeypatch.setattr(module, "CHANGE_CHECK_SECONDS", 15)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    for second in range(30):
        clock.value = second
        producer(guard)
    assert inputs.family_reads == 2


def test_a_changed_family_alone_is_planned_again(monkeypatch):
    """A response, eligibility or occurrence change re-plans only its Family."""
    identifiers = [uuid4() for _ in range(30)]
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    drain(producer, guard)
    planner.reset_mock()
    changed = identifiers[7]
    inputs.families[changed] = 1
    producer(guard)
    assert planned(planner) == [changed]
    planner.reset_mock()
    producer(guard)
    planner.assert_not_called()


def test_new_families_are_planned_and_departed_ones_dropped(monkeypatch):
    """A promotion's new Family is queued; one no longer listed is dropped."""
    identifiers = sorted(uuid4() for _ in range(3))
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4(), limit=1)
    guard = Mock(spec=SchedulerGuard)
    producer(guard)
    assert list(producer.pending) == identifiers[1:]
    newcomer = uuid4()
    del inputs.families[identifiers[2]]
    inputs.families[newcomer] = 0
    drain(producer, guard)
    # The newcomer changed on its own, so it goes ahead of the pass.
    assert planned(planner) == [identifiers[0], newcomer, identifiers[1]]


def test_a_changed_family_goes_ahead_of_a_pass_under_way(monkeypatch):
    """A response is planned next, not after the rest of a full pass."""
    identifiers = sorted(uuid4() for _ in range(5))
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4(), limit=2)
    guard = Mock(spec=SchedulerGuard)
    producer(guard)
    inputs.families[identifiers[4]] = 1
    producer(guard)
    assert planned(planner) == [*identifiers[:2], identifiers[4], identifiers[2]]


def test_a_family_just_planned_goes_behind_the_rest(monkeypatch):
    """Its own planning (a send) moved it; it must not crowd out the others."""
    identifiers = sorted(uuid4() for _ in range(5))
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4(), limit=2)
    guard = Mock(spec=SchedulerGuard)
    producer(guard)
    inputs.families[identifiers[1]] = inputs.families[identifiers[4]] = 1
    producer(guard)
    assert planned(planner) == [*identifiers[:2], identifiers[4], identifiers[2]]
    assert list(producer.pending) == [identifiers[3], identifiers[1]]


def test_a_campaign_wide_change_plans_every_family(monkeypatch):
    """A schedule, configuration, gate or lifecycle change queues everyone."""
    identifiers = [uuid4() for _ in range(30)]
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    drain(producer, guard)
    for change in ("campaign", "runtime"):
        planner.reset_mock()
        if change == "campaign":
            inputs.campaign = 1
        else:
            inputs.runtime["version"] = 2
        drain(producer, guard)
        assert sorted(planned(planner)) == sorted(identifiers)


def test_a_requeued_pass_continues_after_the_last_family(monkeypatch):
    """Churn mid-pass never restarts at the first Family, so none starves."""
    identifiers = sorted(uuid4() for _ in range(5))
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4(), limit=2)
    guard = Mock(spec=SchedulerGuard)
    producer(guard)
    inputs.campaign = 1
    producer(guard)
    assert planned(planner) == identifiers[:4]
    # The two planned after the change are not queued again.
    assert list(producer.pending) == [identifiers[4], *identifiers[:2]]


def test_due_and_lead_window_times_wake_a_full_pass(monkeypatch):
    """Passing the next clock edge plans every Family once; earlier edges don't."""
    identifiers = [uuid4() for _ in range(4)]
    planner = idle_planner()
    inputs = sweep(monkeypatch, identifiers, planner)
    due = NOW + timedelta(hours=3)
    inputs.edges = (NOW - timedelta(days=1), NOW, due - PREPARE_AHEAD, due)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    drain(producer, guard)
    assert producer.wake == due - PREPARE_AHEAD
    for instant, expected in (
        (due - PREPARE_AHEAD - timedelta(seconds=1), 0),
        (due - PREPARE_AHEAD, 4),
        (due - timedelta(seconds=1), 0),
        (due, 4),
        (due + timedelta(days=1), 0),
    ):
        planner.reset_mock()
        inputs.now = instant
        drain(producer, guard)
        assert planner.call_count == expected, instant
    assert producer.wake is None


def test_the_safety_net_plans_every_family_hourly(monkeypatch):
    """Even with no visible change, every Family is planned each hour."""
    clock = Clock()
    monkeypatch.setattr(module, "monotonic", clock)
    identifiers = [uuid4() for _ in range(3)]
    planner = idle_planner()
    sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    drain(producer, guard)
    planner.reset_mock()
    clock.value = module.FULL_SWEEP_SECONDS - 1
    producer(guard)
    planner.assert_not_called()
    clock.value = module.FULL_SWEEP_SECONDS
    drain(producer, guard)
    assert sorted(planned(planner)) == sorted(identifiers)


def test_a_restart_plans_every_family_again(monkeypatch):
    """Traversal state is memory only, so a new producer starts with a full pass."""
    identifiers = [uuid4() for _ in range(3)]
    planner = idle_planner()
    sweep(monkeypatch, identifiers, planner)
    guard = Mock(spec=SchedulerGuard)
    drain(module.FamilyScheduleProducer(uuid4()), guard)
    planner.reset_mock()
    drain(module.FamilyScheduleProducer(uuid4()), guard)
    assert sorted(planned(planner)) == sorted(identifiers)


def test_an_idle_check_costs_time_linear_in_the_families(monkeypatch):
    """The idle comparison is linear in Families, however busy the CPU (#690).

    The PostgreSQL idle-sweep test bounds statements and the read's plan, but
    on packed CI runners its wall clock can only guard against catastrophe.
    This times the Python side alone at two populations ten times apart,
    interleaved so a busy machine slows both alike, and compares the best
    sample of each: a linear comparison costs about ten times as much at ten
    times the Families (less, with fixed per-loop work), a quadratic one
    about a hundred times.
    """
    from time import perf_counter

    guard = Mock(spec=SchedulerGuard)

    def idle(count):
        """A producer whose first pass is done, and its patched inputs."""
        inputs = sweep(monkeypatch, [uuid4() for _ in range(count)], idle_planner())
        producer = module.FamilyScheduleProducer(uuid4())
        producer(guard)
        # The first loop queued every Family; consider them all planned.
        producer.pending.clear()
        return producer, inputs

    def sample(producer, inputs, samples):
        """Time one idle loop of ``producer`` against its ``inputs``."""
        monkeypatch.setattr(module, "_runtime", lambda: inputs.runtime)
        monkeypatch.setattr(module, "_campaign_inputs", inputs.campaign_inputs)
        monkeypatch.setattr(module, "_family_inputs", inputs.family_inputs)
        started = perf_counter()
        assert producer(guard) == ()
        samples.append(perf_counter() - started)

    small, large = idle(270), idle(2700)
    small_samples, large_samples = [], []
    for _ in range(15):
        sample(*small, small_samples)
        sample(*large, large_samples)
    ratio = min(large_samples) / min(small_samples)
    assert ratio < 30, (ratio, small_samples, large_samples)
