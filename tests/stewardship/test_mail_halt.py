"""Clear a halted mail sender without a database (ADM-13 PR 4a, #530).

The halt identity in the stop marker, the circuit's lift, the clear's
reasons in their order and the command's wait for the mailbox check. The
records, guards and the consumers' handshake with them are in
``database/test_mail_halt_clear_postgresql.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship import mail_halt_clear
from parishkit.stewardship.family_delivery import ProviderHealth
from parishkit.stewardship.installer_health import (
    claim_stop,
    read_stop,
    release_stop,
)
from parishkit.stewardship.jobs import mail_halt
from parishkit.stewardship.jobs.family_mail_delivery_tasks import DeliveryCircuit
from parishkit.stewardship.jobs.mail_halt import halt_marker, read_marker
from parishkit.stewardship.mail_halt_clear import (
    Halt,
    MailboxCheckStatus,
    reason,
)

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def test_the_marker_names_one_halt_and_old_markers_name_none():
    """Round trip; an older release's marker, a half-written one and an
    unknown kind name no halt."""
    halt = uuid4()
    assert read_marker(halt_marker(halt, "after_data", 12.5)) == (
        halt,
        "after_data",
        12.5,
    )
    for value in (b"stopped", b"", b"{", b'{"halt": "x"}', None):
        assert read_marker(value) is None
    assert read_marker(halt_marker(halt, "guess", 1.0)) is None


def test_the_first_marker_stands(tmp_path):
    """Two consumers halting at once share the first one's identity."""
    marker = tmp_path / "private" / "mail-systemic-stop"
    first, second = (
        halt_marker(uuid4(), "before_data", 1.0),
        halt_marker(uuid4(), "after_data", 2.0),
    )
    assert claim_stop(marker, first) == first
    assert claim_stop(marker, second) == first
    assert read_stop(marker) == first
    assert read_stop(tmp_path / "absent") is None


def circuits(tmp_path):
    """Two circuits sharing one stop marker, as mail dispatch's consumers."""
    marker = tmp_path / "private" / "mail-systemic-stop"
    return marker, [
        DeliveryCircuit(recovery_seconds=600, systemic_stops=True, shared_stop=marker)
        for _ in range(2)
    ]


def test_a_systemic_stop_gets_an_identity_both_consumers_share(tmp_path):
    """The kind comes from the outcome; the follower adopts the marker's."""
    marker, (first, second) = circuits(tmp_path)
    assert first.current_halt() is None
    first.observe(ProviderHealth.SYSTEMIC)
    halt_id, kind, _ = first.current_halt()
    assert kind == "before_data"
    assert read_marker(read_stop(marker))[0] == halt_id
    assert second.blocks_new_send() is True
    assert second.current_halt() == first.current_halt()


def test_a_follower_reads_a_marker_written_after_it_stopped(tmp_path):
    """A marker still empty when the follower stopped is read again later."""
    marker, (first, _) = circuits(tmp_path)
    marker.parent.mkdir(mode=0o700)
    marker.write_bytes(b"stopped")
    marker.chmod(0o600)
    assert first.blocks_new_send() is True and first.current_halt() is None
    halt = uuid4()
    marker.write_bytes(halt_marker(halt, "after_data", 3.0))
    assert first.current_halt() == (halt, "after_data", 3.0)


def test_lift_lets_one_probe_through(tmp_path):
    """Only the current halt lifts; one outage result then pauses again."""
    marker, (first, _) = circuits(tmp_path)
    first.observe(ProviderHealth.SYSTEMIC)
    halt_id = first.current_halt()[0]
    assert first.lift(uuid4()) is False and first.blocks_new_send() is True
    assert first.lift(halt_id) is True
    assert not marker.exists() and first.blocks_new_send() is False
    assert first.lift(halt_id) is False
    assert first.observe(ProviderHealth.UNAVAILABLE) is True
    assert first.blocks_new_send() is True and not first.stopped
    assert first.sender_state()[0] == "outage_paused"


def test_lift_leaves_a_newer_halts_marker(tmp_path):
    """The other consumer halted again first: its marker stays."""
    marker, (first, second) = circuits(tmp_path)
    first.observe(ProviderHealth.SYSTEMIC)
    old = first.current_halt()[0]
    second.blocks_new_send()
    assert first.lift(old) is True
    first.observe(ProviderHealth.SYSTEMIC, uncertain=True)
    newer = first.current_halt()[0]
    assert second.lift(old) is True and marker.exists()
    assert second.blocks_new_send() is True
    assert second.current_halt()[:2] == (newer, "after_data")


def test_release_stop_removes_only_the_marker_it_names(tmp_path):
    """Its own marker goes; another's is put back; one created while it was
    aside stands; no aside file is left."""
    marker = tmp_path / "private" / "mail-systemic-stop"
    mine, other, newer = (
        halt_marker(uuid4(), "before_data", n) for n in (1.0, 2.0, 3.0)
    )
    assert release_stop(marker, lambda value: True) is False  # No marker.
    claim_stop(marker, other)
    assert release_stop(marker, lambda value: value == mine) is False
    assert read_stop(marker) == other
    assert release_stop(marker, lambda value: value == other) is True
    assert not marker.exists()

    def meanwhile(value):
        """The other consumer creates a newer marker while this one is aside."""
        assert claim_stop(marker, newer) == newer
        return False

    claim_stop(marker, mine)
    assert release_stop(marker, meanwhile) is False
    assert read_stop(marker) == newer
    assert sorted(path.name for path in marker.parent.iterdir()) == [marker.name]


def test_lift_never_removes_a_marker_the_other_consumer_just_created(
    tmp_path, monkeypatch
):
    """The race the two-step read-then-unlink lost: both consumers lift halt
    H; while the first checks the marker, the second lifts too, sends its
    probe and halts again under H2. H2's marker stays, and the first
    follows H2 instead of probing under a third identity."""
    marker, (first, second) = circuits(tmp_path)
    first.observe(ProviderHealth.SYSTEMIC)
    halt_id = first.current_halt()[0]
    second.blocks_new_send()
    real = mail_halt.read_marker
    raced = []

    def racing(value):
        """Run the second consumer's lift and failed probe inside the gap."""
        if not raced:
            raced.append(True)
            assert second.lift(halt_id) is True
            second.observe(ProviderHealth.SYSTEMIC)
        return real(value)

    monkeypatch.setattr(mail_halt, "read_marker", racing)
    assert first.lift(halt_id) is True
    monkeypatch.setattr(mail_halt, "read_marker", real)
    renewed = second.current_halt()[0]
    assert renewed != halt_id and read_marker(read_stop(marker))[0] == renewed
    assert first.blocks_new_send() is True
    assert first.current_halt()[0] == renewed


HALT = Halt(halt_id=uuid4(), kind="before_data", began_at=NOW - timedelta(minutes=10))


def check(status, *, created=-1, claimed=-1, finished=-1):
    """A mailbox check ``created``, ``claimed`` and ``finished`` minutes from NOW."""
    at = {
        name: None if value is None else NOW + timedelta(minutes=value)
        for name, value in (
            ("created_at", created),
            ("claimed_at", claimed),
            ("finished_at", finished),
        )
    }
    return MailboxCheckStatus(check_id=uuid4(), status=status, **at)


@pytest.mark.parametrize(
    ("halt", "mailbox", "unsettled", "expected"),
    [
        (None, check("passed"), 0, "not_halted"),
        (HALT, check("running", finished=None), 0, "checking"),
        (HALT, None, 0, "check_needed"),
        (HALT, check("refused"), 0, "check_failed"),
        (HALT, check("unanswered", claimed=None, finished=None), 0, "check_failed"),
        # A refusal from before this halt asks for a new check.
        (
            HALT,
            check("refused", created=-20, claimed=-20, finished=-20),
            0,
            "check_needed",
        ),
        # Passed, but claimed before the halt began, or too long ago.
        (HALT, check("passed", created=-11, claimed=-11), 0, "check_needed"),
        (HALT, check("passed", created=-7, claimed=-7, finished=-6), 0, "check_needed"),
        (HALT, check("passed"), 2, "unsettled"),
        (HALT, check("passed"), 0, None),
    ],
)
def test_the_reasons_come_in_order(halt, mailbox, unsettled, expected):
    """The first reason that applies is the one the reader sees."""
    assert reason(halt, mailbox, unsettled, NOW) == expected
    if expected is not None:
        assert mail_halt_clear.REASONS[expected]


def test_the_command_waits_for_a_live_check(monkeypatch):
    """It reads the preview again until the check answers, within its bound."""
    live = SimpleNamespace(halt=HALT, check=check("waiting", finished=None))
    done = SimpleNamespace(
        halt=HALT,
        check=check("passed"),
        waiting=3,
        unsettled=0,
        unsettled_ids=(),
        paused=False,
        reason=None,
        token="signed",
    )
    reads = [live, live, done]
    monkeypatch.setattr(
        mail_halt_clear, "preview_clear", lambda caller, store: ("actor", reads.pop(0))
    )
    monkeypatch.setattr(
        mail_halt_clear, "read_preview", lambda store, actor: reads.pop(0)
    )
    slept = []
    result = mail_halt_clear.mail_clear_preview(
        object(), SimpleNamespace(store=None), sleep=slept.append, clock=lambda: 0.0
    )
    assert slept == [2, 2] and not reads
    document = result.to_document()
    assert document["mailbox_check"]["status"] == "passed"
    assert document["preview"] == {"token": "signed"} and document["waiting"] == 3
    # Past its bound it stops waiting and prints the live check.
    stuck = SimpleNamespace(**{**vars(done), "check": live.check, "token": None})
    monkeypatch.setattr(
        mail_halt_clear, "preview_clear", lambda caller, store: ("actor", stuck)
    )
    times = iter([0.0, 500.0])
    result = mail_halt_clear.mail_clear_preview(
        object(),
        SimpleNamespace(store=None),
        sleep=slept.append,
        clock=lambda: next(times),
    )
    assert result.mailbox_check["status"] == "waiting" and slept == [2, 2]
