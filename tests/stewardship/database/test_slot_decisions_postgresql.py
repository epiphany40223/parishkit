"""Refreshes skipped around Family emails and the slot decision record (#632).

The scheduler runs at fixed instants (``production.database_now``) on
2026-09-11 in America/New_York (13:23 EDT is 17:23 UTC). The Family email
windows are given directly (``send_windows.current_windows``), so each test
names the window it needs; the window reader itself is tested at the end
against a real Production campaign. Everything else is the real scheduler,
slot decision guard, refresh-tick guard and data-age reader.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns import work_locks
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source import data_age, production, send_windows
from parishkit.stewardship.source.cadence import listed_due_slots
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.production import produce_refreshes
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshTick,
    SourceSlotDecision,
)
from parishkit.stewardship.source.refresh_rules import stored_settings
from parishkit.stewardship.source.send_windows import Window
from parishkit.stewardship.source.slot_decisions import prune_slot_decisions

from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_family_mail_prepare_ahead_postgresql import (  # noqa: F401
    dispatch_worker,
    families,
    family_mail,
    invited,
)
from .test_refresh_frequency_postgresql import forged_tick, scope_of, with_schedule
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)
ZONE = "America/New_York"


def at(hour, minute=0, day=11):
    """``hour:minute`` EDT on 2026-09-``day`` (UTC-4)."""
    return datetime(2026, 9, day, hour + 4, minute, tzinfo=UTC)


def schedule(full=("00:00", "12:00"), *, skip=True):
    """Full refreshes at ``full``, quick updates hourly; skip around emails."""
    return stored_settings(
        {
            "rules": [
                *({"kind": "full", "at": value} for value in full),
                {"kind": "quick", "every": 60, "from": "01:00", "to": "23:00"},
            ],
            "skips": [],
            "skip_around_family_emails": skip,
        }
    )


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


# When the skip setting took effect in most tests: before every slot here.
SINCE = datetime(2026, 9, 9, 4, tzinfo=UTC)


def loop(monkeypatch, instant, *, windows=(), held=False, login=None, since=SINCE):
    """One scheduler loop at ``instant`` with the given windows and hold.

    The skip setting took effect at ``since`` (before every slot here, by
    default): the real activations are on today's clock, not the test's.
    """
    monkeypatch.setattr(production, "database_now", lambda: instant)
    monkeypatch.setattr(production, "delta_held", lambda now: held)
    monkeypatch.setattr(send_windows, "current_windows", lambda now: list(windows))
    monkeypatch.setattr(data_age, "skip_setting_since", lambda: since)
    if login is None:
        with scheduler_session() as guard:
            return produce_refreshes(guard)
    with task_login(login), scheduler_session() as guard:
        return produce_refreshes(guard)


def decided(decision=None):
    """``{(cause, due): decision}`` of the recorded slot decisions."""
    rows = SourceSlotDecision.objects.all()
    if decision is not None:
        rows = rows.filter(decision=decision)
    return {(row.cause, row.due_at): row.decision for row in rows}


def ticked():
    """``{(cause, due)}`` of the refreshes created."""
    return set(SourceRefreshTick.objects.values_list("command__cause", "due_at"))


def sending(start, end, cause="reminder_sending"):
    """One window from ``start`` to ``end``."""
    return [Window(start, end, cause)]


def test_a_skipped_latest_slot_creates_nothing(tmp_path, monkeypatch):
    """The 13:00 quick update falls in a reminder's send and is skipped.

    The 12:00 full refresh is outside the window and runs. A later loop with
    the window gone (the reminder moved) keeps the skip: the slot never runs.
    """
    with_schedule(tmp_path, **schedule())
    window = sending(at(12, 30), at(13, 30))
    loop(monkeypatch, at(13, 23), windows=window)
    assert decided() == {("delta", at(13)): "skipped"}
    assert ticked() == {("nightly", at(12))}
    row = SourceSlotDecision.objects.get()
    assert row.window_cause == "reminder_sending" and row.nightly_time == "00:00"
    loop(monkeypatch, at(13, 40))
    assert ticked() == {("nightly", at(12))}
    assert decided() == {("delta", at(13)): "skipped"}


def test_downtime_inside_a_window_skips_every_slot_it_covers(tmp_path, monkeypatch):
    """Back at 13:23 after downtime, every due slot in the window is skipped.

    The latest full slot (12:00) is skipped too, so no full refresh runs,
    and the scheduler does not fall back to the 00:00 refresh; the
    data-age alarm does not count the skipped 12:00 refresh.
    """
    with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(13, 23), windows=sending(at(8), at(13, 30)))
    skipped = decided("skipped")
    assert ("nightly", at(12)) in skipped
    assert {("delta", at(hour)) for hour in (8, 9, 10, 11, 13)} <= set(skipped)
    # Quick updates before the window stay undecided: no row, no refresh.
    assert ("delta", at(7)) not in decided()
    assert ticked() == set()
    with transaction.atomic():
        overdue = data_age.overdue_full_slot(at(13, 23), after=at(0, 5), timezone=ZONE)
    assert overdue is None


def test_a_held_slot_stays_held_when_a_moved_window_covers_it(tmp_path, monkeypatch):
    """A send holds 12:00 and 13:00; a reminder then moves over them.

    They were decided held, so they stay due: once the hold ends the latest
    of each kind runs, window or not.
    """
    with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(13, 23), held=True)
    rows = decided()
    assert rows[("nightly", at(12))] == rows[("delta", at(13))] == "held"
    # Every older undecided slot is held as well, none skipped.
    assert set(rows.values()) == {"held"}
    assert ticked() == set()
    # One durable hold entry per held full slot, none for quick ones.
    assert (
        OperationalLog.objects.filter(
            event="source_refresh_held", schema="schedule"
        ).count()
        == SourceSlotDecision.objects.filter(decision="held", cause="nightly").count()
    )
    loop(monkeypatch, at(13, 40), windows=sending(at(11), at(14)))
    assert ticked() == {("nightly", at(12)), ("delta", at(13))}
    assert decided("skipped") == {}


def test_the_newest_held_slot_runs_when_the_latest_is_skipped(tmp_path, monkeypatch):
    """Held at 12:00, then the 13:00 full refresh is skipped: 12:00 catches up."""
    with_schedule(tmp_path, **schedule(("00:00", "12:00", "13:00")))
    loop(monkeypatch, at(12, 23), held=True)
    assert decided("held")[("nightly", at(12))] == "held"
    loop(monkeypatch, at(13, 23), windows=sending(at(13), at(13, 30)))
    # 13:00 is a full time, so it has no quick update.
    assert decided("skipped") == {("nightly", at(13)): "skipped"}
    assert ("nightly", at(12)) in ticked()
    assert ("nightly", at(13)) not in ticked()


def test_a_schedule_that_does_not_skip_records_nothing(tmp_path, monkeypatch):
    """Without the skip setting a window changes nothing, and nothing is recorded."""
    with_schedule(tmp_path, **schedule(skip=False))
    loop(monkeypatch, at(13, 23), windows=sending(at(8), at(14)), held=True)
    loop(monkeypatch, at(13, 40), windows=sending(at(8), at(14)))
    assert decided() == {}
    assert ticked() == {("nightly", at(12)), ("delta", at(13))}


def counted_locks(monkeypatch):
    """A list that grows by one each time a loop takes the work-order lock."""
    taken, real = [], work_locks.lock_work_order

    def counting():
        """Count the lock, then take the real one."""
        taken.append(True)
        return real()

    monkeypatch.setattr(work_locks, "lock_work_order", counting)
    return taken


@pytest.mark.parametrize(
    ("windows", "held", "decision"),
    [(sending(at(7, 30), at(11, 30)), False, "skipped"), ((), True, "held")],
    ids=["window", "hold"],
)
def test_an_idle_loop_still_records_older_decisions(
    tmp_path, monkeypatch, windows, held, decision
):
    """Every current slot has its refresh, yet an older slot must be decided.

    The 13:23 loop runs 12:00 and 13:00 and leaves the older quick updates
    undecided (no window, no hold). At 13:40 a window now covers 08:00 to
    11:00, or a send holds them: the idle shortcut must not return early,
    so the locked pass records the decisions (#715). Once nothing is left
    to decide, the next loop takes no work-order lock.
    """
    with_schedule(tmp_path, **schedule())
    first = loop(monkeypatch, at(13, 23))
    assert decided() == {}
    assert ticked() == {("nightly", at(12)), ("delta", at(13))}
    taken = counted_locks(monkeypatch)
    assert loop(monkeypatch, at(13, 40), windows=windows, held=held) == first
    assert taken
    recorded = decided(decision)
    if decision == "skipped":
        assert set(recorded) == {("delta", at(hour)) for hour in (8, 9, 10, 11)}
    else:
        assert {("delta", at(hour)) for hour in range(1, 12)} <= set(recorded)
    taken.clear()
    assert loop(monkeypatch, at(13, 45), windows=windows, held=held) == first
    assert taken == []
    assert decided(decision) == recorded


def test_an_idle_loop_with_nothing_to_decide_takes_no_lock(tmp_path, monkeypatch):
    """Undecided older slots outside every window, with no hold, write nothing.

    The locked pass would only return the current receipts, so the idle
    shortcut returns them without the work-order lock (#715).
    """
    with_schedule(tmp_path, **schedule())
    first = loop(monkeypatch, at(13, 23), windows=sending(at(2, 30), at(3, 30)))
    taken = counted_locks(monkeypatch)
    assert loop(monkeypatch, at(13, 40), windows=sending(at(2, 30), at(3, 30))) == first
    assert taken == []
    assert decided() == {("delta", at(3)): "skipped"}


def test_the_scheduler_login_records_and_reads_decisions(tmp_path, monkeypatch):
    """The scheduler's own login writes skips and holds and creates refreshes."""
    with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(12, 23), held=True, login=ServiceRole.SCHEDULER)
    loop(
        monkeypatch,
        at(13, 23),
        windows=sending(at(13), at(13, 30)),
        login=ServiceRole.SCHEDULER,
    )
    rows = decided()
    # At 12:23 the send held the 12:00 full refresh and the 11:00 quick
    # update (and every older undecided slot); at 13:23 the 13:00 quick
    # update fell in the window, and the held 12:00 refresh ran.
    assert rows[("nightly", at(12))] == "held"
    assert rows[("delta", at(11))] == "held"
    assert rows[("delta", at(13))] == "skipped"
    assert ("nightly", at(12)) in ticked()


def forge_decision(instant, slot_index=-1, **overrides):
    """Insert a decision row for a listed slot, as a defect or attacker might."""
    tick = SourceRefreshTick.objects.select_related("command__request").first()
    slots = listed_due_slots(
        now=instant,
        timezone=ZONE,
        nightly_time="00:00",
        scope_fingerprint=scope_of(tick),
        full_refresh_times=["00:00", "12:00"],
        quick_refresh_times=[f"{hour:02d}:00" for hour in range(1, 24) if hour != 12],
    )
    slot = overrides.pop("slot", None) or slots[slot_index]
    values = {
        "configuration_id": tick.configuration_id,
        "cause": slot.cause,
        "due_at": slot.due_at,
        "timezone": ZONE,
        "nightly_time": slot.nightly_time or "00:00",
        "slot_key": slot.slot_key,
        "decision": "skipped",
        "window_cause": "reminder_sending",
    } | overrides
    with scheduler_session(), work_transaction():
        SourceSlotDecision.objects.create(**values)
    return slot


def test_the_guard_refuses_skips_the_schedule_does_not_allow(tmp_path, monkeypatch):
    """No skip of the nightly refresh, of a slot that ran, or off its cadence."""
    with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(13, 23))
    slots = listed_due_slots(
        now=at(13, 23),
        timezone=ZONE,
        nightly_time="00:00",
        scope_fingerprint=scope_of(SourceRefreshTick.objects.first()),
        full_refresh_times=["00:00", "12:00"],
        quick_refresh_times=[f"{hour:02d}:00" for hour in range(1, 24) if hour != 12],
    )
    by = {(slot.cause, slot.due_at): slot for slot in slots}
    # The nightly refresh is never skipped.
    with pytest.raises(IntegrityError, match="may not skip this slot"):
        forge_decision(at(13, 23), slot=by[("nightly", at(0))])
    # A slot that already ran is never skipped later.
    with pytest.raises(IntegrityError, match="may not skip this slot"):
        forge_decision(at(13, 23), slot=by[("delta", at(13))])
    # A wrong key is refused.
    with pytest.raises(IntegrityError, match="identity does not match"):
        forge_decision(at(13, 23), slot=by[("delta", at(11))], slot_key="0" * 64)
    # A skip may be recorded for an undecided slot, and then its tick is
    # refused.
    slot = forge_decision(at(13, 23), slot=by[("delta", at(11))])
    with (
        pytest.raises(IntegrityError, match="recorded as skipped"),
        scheduler_session(),
        work_transaction(),
    ):
        receipt = command(cause="delta", actor_id=None)
        SourceRefreshTick.objects.create(
            command_id=receipt.command_id,
            **forged_tick(
                SourceRefreshTick.objects.first(),
                due_at=slot.due_at,
                slot_key=slot.slot_key,
                nightly_time="00:00",
            ),
        )
    # A held decision's slot may still get its tick: the 10:00 quick slot.
    held = forge_decision(
        at(13, 23), slot=by[("delta", at(10))], decision="held", window_cause=None
    )
    with scheduler_session(), work_transaction():
        receipt = command(cause="delta", actor_id=None)
        SourceRefreshTick.objects.create(
            command_id=receipt.command_id,
            **forged_tick(
                SourceRefreshTick.objects.first(),
                due_at=held.due_at,
                slot_key=held.slot_key,
                nightly_time="00:00",
            ),
        )


def test_no_skip_without_the_setting(tmp_path, monkeypatch):
    """A schedule that does not skip around Family emails refuses a skip row."""
    with_schedule(tmp_path, **schedule(skip=False))
    loop(monkeypatch, at(13, 23))
    with pytest.raises(IntegrityError, match="may not skip this slot"):
        forge_decision(at(13, 23), slot_index=0)


def test_decisions_are_kept_eight_days_and_then_removed(tmp_path, monkeypatch):
    """Retention removes decisions due more than eight days ago, nothing younger.

    Measured on the real database clock: one quick slot due a day ago and
    one nine days ago.
    """
    with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(13, 23))
    tick = SourceRefreshTick.objects.first()

    def quick_slot(days):
        """A listed quick slot due about ``days`` days before now."""
        slots = listed_due_slots(
            now=datetime.now(UTC) - timedelta(days=days),
            timezone=ZONE,
            nightly_time="00:00",
            scope_fingerprint=scope_of(tick),
            full_refresh_times=["00:00", "12:00"],
            quick_refresh_times=[
                f"{hour:02d}:00" for hour in range(1, 24) if hour != 12
            ],
        )
        return [slot for slot in slots if slot.cause == "delta"][0]

    young = forge_decision(at(13, 23), slot=quick_slot(1))
    old = forge_decision(at(13, 23), slot=quick_slot(9))
    with connection.cursor() as cursor:
        for statement, values in (
            (
                "DELETE FROM stewardship_source_slot_decision WHERE slot_key=%s",
                [young.slot_key],
            ),
            ("UPDATE stewardship_source_slot_decision SET decision='held'", []),
        ):
            with (
                pytest.raises(IntegrityError, match="append-only"),
                transaction.atomic(),
            ):
                cursor.execute(statement, values)
    with task_login(ServiceRole.WORKER), transaction.atomic():
        assert prune_slot_decisions() == 1
    assert set(SourceSlotDecision.objects.values_list("slot_key", flat=True)) == {
        young.slot_key
    }
    assert old.slot_key != young.slot_key


def test_the_web_and_worker_logins_read_the_skips(tmp_path, monkeypatch):
    """The pages and the health check apply the recorded skips (#632)."""
    with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(13, 23), windows=sending(at(11, 30), at(12, 30)))
    for role in (ServiceRole.WEB, ServiceRole.WORKER):
        with task_login(role), transaction.atomic():
            assert data_age.skipped_full_after(at(0)) == {at(12)}


def turn_skip(tmp_path_store, skip):
    """Apply a configuration change that turns the skip setting on or off."""
    _, store, _, actor = tmp_path_store
    current = store.active()
    integration = current.document()["sections"]["integrations"][0]
    settings = integration["values"]["settings"]
    rules = settings["refresh_rules"] | {"skip_around_family_emails": skip}
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": integration["id"],
            "values": {"settings": settings | {"refresh_rules": rules}},
        }
    ]
    assert change(store, current, actor, patch).state == "applied"


def test_turning_the_setting_off_never_runs_a_skipped_slot(tmp_path, monkeypatch):
    """A skipped latest slot stays skipped after the setting is turned off.

    The scheduler creates no tick for it (the guard would refuse one and roll
    back the loop); the loop goes on, and the next slot runs as usual.
    """
    configured = with_schedule(tmp_path, **schedule())
    loop(monkeypatch, at(13, 23), windows=sending(at(12, 30), at(13, 30)))
    assert decided() == {("delta", at(13)): "skipped"}
    turn_skip(configured, False)
    loop(monkeypatch, at(13, 40))
    assert ("delta", at(13)) not in ticked()
    loop(monkeypatch, at(14, 5))
    assert ("delta", at(14)) in ticked()
    assert decided() == {("delta", at(13)): "skipped"}


def test_slots_due_before_the_setting_took_effect_are_not_decided(
    tmp_path, monkeypatch
):
    """Turned on at 12:30, the setting does not skip the overdue 12:00 refresh."""
    with_schedule(tmp_path, **schedule())
    loop(
        monkeypatch,
        at(13, 23),
        windows=sending(at(11), at(13, 30)),
        since=at(12, 30),
    )
    assert decided() == {("delta", at(13)): "skipped"}
    assert ("nightly", at(12)) in ticked()


def test_the_skip_setting_takes_effect_at_its_activation(tmp_path):
    """``skip_setting_since`` is the start of the latest run that skips."""
    from parishkit.stewardship.accounts.runtime_models import ConfigurationActivation

    configured = with_schedule(tmp_path, **schedule())

    def newest():
        """The newest activation's time."""
        return ConfigurationActivation.objects.order_by("-sequence").first().created_at

    with transaction.atomic():
        assert data_age.skip_setting_since() == newest()
    first = newest()
    turn_skip(configured, False)
    with transaction.atomic():
        assert data_age.skip_setting_since() is None
    turn_skip(configured, True)
    with transaction.atomic():
        assert data_age.skip_setting_since() == newest() > first


def test_the_window_reader_finds_a_production_reminder(invited):  # noqa: F811
    """A real Production reminder's lead window and send, under the scheduler login.

    The campaign's invitation has been sent, so the reminder's estimated
    send length comes from it.
    """
    harness, path, provider, due_at = invited
    with task_login(ServiceRole.SCHEDULER), transaction.atomic():
        windows = send_windows.current_windows(due_at - timedelta(hours=1))
    reminder = [w for w in windows if w.cause.startswith("reminder")]
    assert [w.cause for w in reminder] == ["reminder_preparing", "reminder_sending"]
    preparing, sending_window = reminder
    assert preparing.start == due_at - timedelta(hours=2)
    assert preparing.end == sending_window.start == due_at
    assert timedelta(hours=1) <= sending_window.end - due_at <= timedelta(hours=2)


def test_the_window_reader_has_no_windows_while_paused(invited):  # noqa: F811
    """Production delivery paused: no windows; resumed: the reminder's again."""
    from .test_outbox_boundaries_postgresql import control

    harness, path, provider, due_at = invited
    control(harness.campaign, "pause")
    with transaction.atomic():
        assert send_windows.current_windows(due_at - timedelta(hours=1)) == []
    control(harness.campaign, "resume")
    with transaction.atomic():
        assert send_windows.current_windows(due_at - timedelta(hours=1))


def test_the_window_reader_has_no_windows_in_testing(families):  # noqa: F811
    """A campaign in Testing mode has no windows, whatever is scheduled."""
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition

    due_at = ScheduleDefinition.objects.get().current_revision.due_at
    with transaction.atomic():
        assert send_windows.current_windows(due_at + timedelta(minutes=5)) == []


def test_the_estimate_falls_back_to_the_other_kinds_completed_send(invited):  # noqa: F811
    """The reminder has not been sent, so its estimate is the invitation's."""
    harness, path, provider, due_at = invited
    campaign = harness.campaign.pk
    with transaction.atomic():
        assert send_windows._last_length(campaign, "reminder", due_at) is None
        assert send_windows._last_length(campaign, "initial", due_at) is not None
        estimates = send_windows._estimates(campaign, due_at)
    assert estimates["reminder"] == estimates["initial"]


def test_a_stuck_send_keeps_its_window_open_only_to_the_cap(
    families,  # noqa: F811
    monkeypatch,
):
    """Prepared but not sent, the invitation's window runs to its due time + 2 h.

    Its work stays active (the send is stuck), so its window stays open past
    the estimate, but a slot at or after the cap is decided as usual. The
    unfinished send is not a completed one, so the estimate falls back to an
    hour. The threshold is lowered to fit the fixture's Families.
    """
    from .test_family_mail_prepare_ahead_postgresql import (
        activate_response_service,
        campaign_clock,
        complete_empty_catchup,
        due,
        plan,
        prepare_all,
    )

    harness, path = families
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    due_at = due()
    with campaign_clock(due_at):
        plan()
        prepare_all(harness)
    monkeypatch.setattr(send_windows, "ACTIVE_MINIMUM", 2)
    later = due_at + timedelta(minutes=150)
    with transaction.atomic():
        windows = send_windows.current_windows(later)
        assert send_windows._last_length(harness.campaign.pk, "initial", later) is None
    # Active work is counted in the current Production cycle only.
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition

    definition = ScheduleDefinition.objects.get(kind="initial")
    cycle = harness.campaign.production_cycle
    with transaction.atomic():
        assert send_windows._active(definition.pk, due_at, cycle, minimum=1)
        assert not send_windows._active(definition.pk, due_at, cycle + 1, minimum=1)
    (window,) = windows
    assert window.cause == "initial_sending"
    assert window.end == due_at + timedelta(hours=2)
    assert send_windows.window_cause(windows, due_at + timedelta(minutes=119))
    assert send_windows.window_cause(windows, due_at + timedelta(hours=2)) is None


def test_a_delivery_change_between_reading_and_deciding_decides_nothing(
    tmp_path, monkeypatch
):
    """Windows read before the lock are not used if the delivery state moved.

    Here the pause looks different under the lock than before it, so the
    loop records nothing and the latest slots run as for a schedule that
    does not skip.
    """
    with_schedule(tmp_path, **schedule())
    real = production._delivery_state
    calls = []

    def state():
        """A different pause before the lock than under it."""
        calls.append(None)
        value = real()
        return value if len(calls) > 1 else (*value[:2], not value[2])

    monkeypatch.setattr(production, "_delivery_state", state)
    loop(monkeypatch, at(13, 23), windows=sending(at(12, 30), at(13, 30)))
    assert len(calls) == 2
    assert decided() == {}
    assert ticked() == {("nightly", at(12)), ("delta", at(13))}


def test_a_closed_campaign_has_no_windows(invited):  # noqa: F811
    """In Production, a campaign that is no longer active has no windows."""
    from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries

    from .campaign_builders import admit_test_work, campaign_clock, claimed_task

    harness, path, provider, due_at = invited
    campaign = harness.campaign
    actor = uuid4()
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    with campaign_clock(campaign.active_configuration.ends_at):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    campaign.refresh_from_db()
    assert campaign.state != "active"
    with transaction.atomic():
        assert send_windows.current_windows(due_at - timedelta(hours=1)) == []


def test_late_finishes_do_not_stretch_the_estimate(invited):  # noqa: F811
    """A message finalised after due + SEND_ALLOWANCE is ignored.

    The invitation's messages reached their final state just now (the
    database clock); measured from a due time three hours ago they all
    finished late and are ignored, from one hour ago they count.
    """
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage
    from parishkit.stewardship.jobs.ownership import database_now

    messages = OutboxMessage.objects.filter(purpose="initial", state="delivered")
    with transaction.atomic():
        now = database_now()
        assert messages.exists()
        assert send_windows.last_final(messages, now - timedelta(hours=3)) is None
        recent = send_windows.last_final(messages, now - timedelta(hours=1))
    assert recent is not None and recent <= now
