"""One long-lived private Family helper per batch (#284), with a fake Gmail.

Every message keeps its own exact outcome: a fault in one never loses,
skips or resends another, an uncertain DATA is never resent, and a helper
that dies before starting a message hands it to a fresh helper unsent.
"""

import json
import os
import smtplib
import subprocess
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from parishkit.stewardship import family_delivery_process
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
    SmtpSession,
)
from parishkit.stewardship.family_delivery_process import (
    FamilyMailSession,
    submit_family,
)
from parishkit.stewardship.family_delivery_worker import (
    LineReader,
    decode_session_request,
    serve,
)
from parishkit.stewardship.provider_checks import ProviderCheckOwnershipLost

from .family_mail_session_fakes import (
    CHILD_PATH,
    LIMIT,
    SETTINGS,
    FakeGmailHelpers,
    mail,
)

Status = FamilyDeliveryStatus
KEY = b"synthetic-key"


@pytest.fixture
def gmail(tmp_path):
    """Scripted fake Gmail helpers sharing one fault queue and event log."""
    return FakeGmailHelpers(tmp_path / "gmail")


def send(session, value, *, seconds=10, check=lambda: None, settings=SETTINGS):
    """Submit one message through the production entry point."""
    return submit_family(
        KEY, settings, value, seconds=seconds, check=check, session=session
    )


# --- The in-helper SMTP session ---------------------------------------------


class Server:
    """An in-process fake Gmail for SmtpSession: counts what it receives."""

    def __init__(self, faults=()):
        """``faults`` maps (connection number, stage) to an exception or reply."""
        self.faults = dict(faults)
        self.connections = 0
        self.tokens = 0
        self.data = []
        self.quits = 0

    def credentials(self, expiry):
        """A token exchange that counts itself and expires at ``expiry``."""

        def exchange(value, settings, session):
            self.tokens += 1
            return SimpleNamespace(token="synthetic-token", expiry=expiry)

        return exchange

    def factory(self, host, port, **kwargs):
        """Open one numbered fake connection."""
        self.connections += 1
        server, number = self, self.connections

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                server.quits += 1

            def reply(self, stage, default=250):
                fault = server.faults.pop((number, stage), None)
                if isinstance(fault, BaseException):
                    raise fault
                return fault or (default, b"OK")

            def ehlo(self):
                return self.reply("ehlo")

            def docmd(self, name, value):
                return self.reply("auth", 235)

            def has_extn(self, name):
                return False

            def mail(self, sender, *, options):
                return self.reply("mail")

            def rcpt(self, address):
                return self.reply("rcpt")

            def data(self, content):
                server.data.append((number, content))
                return self.reply("data")

        return Connection()


def smtp_session(monkeypatch, server, *, expiry=None, clock=time.monotonic):
    """A SmtpSession wired to ``server``."""
    expiry = expiry or datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery._credentials",
        server.credentials(expiry),
    )
    return SmtpSession(
        KEY,
        SETTINGS,
        profile=DeploymentProfile.PRODUCTION,
        smtp_factory=server.factory,
        session_factory=nullcontext,
        clock=clock,
    )


def test_one_token_and_connection_serve_many_messages(monkeypatch):
    """The point of #284: no per-message token exchange or TLS handshake."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    results = [session.deliver(mail()) for _ in range(5)]
    assert {result.status for result in results} == {Status.ACCEPTED}
    assert (server.tokens, server.connections, len(server.data)) == (1, 1, 5)
    session.close()
    assert server.quits == 1 and session.value is None


def test_a_connection_dropped_while_idle_is_replaced_without_resending(monkeypatch):
    """A drop before DATA is definitely unsent: one fresh connection is tried."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    assert session.deliver(mail()).status is Status.ACCEPTED
    # Gmail closed the kept connection; the next MAIL finds out.
    server.faults = {(1, "mail"): smtplib.SMTPServerDisconnected("idle")}
    second = mail()
    assert session.deliver(second).status is Status.ACCEPTED
    assert server.connections == 2 and len(server.data) == 2
    assert server.tokens == 1


def test_a_fresh_connection_failure_is_not_retried(monkeypatch):
    """Only a kept connection earns the one retry; a new one reports the outage."""
    server = Server({(1, "mail"): smtplib.SMTPServerDisconnected("down")})
    session = smtp_session(monkeypatch, server)
    result = session.deliver(mail())
    assert result.status is Status.UNAVAILABLE and server.connections == 1
    assert server.data == [] and session.smtp is None


def test_a_drop_during_data_is_unknown_and_not_retried(monkeypatch):
    """After DATA the message may have been accepted: never resent."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    assert session.deliver(mail()).status is Status.ACCEPTED
    server.faults = {(1, "data"): smtplib.SMTPServerDisconnected("mid-DATA")}
    result = session.deliver(mail())
    assert result.status is Status.UNKNOWN and len(server.data) == 2
    # The next message starts over on a new connection.
    assert session.deliver(mail()).status is Status.ACCEPTED
    assert server.connections == 2 and len(server.data) == 3


@pytest.mark.parametrize(
    "stage,reply,status,limit",
    [
        ("rcpt", (550, b"5.1.1 No such user"), Status.PERMANENT, None),
        ("mail", (550, b"5.4.5 Daily user sending limit exceeded."), None, "daily"),
        ("data", (452, b"4.2.2 Mailbox full"), Status.TRANSIENT, None),
    ],
)
def test_a_refusal_ends_its_connection_but_not_the_batch(
    monkeypatch, stage, reply, status, limit
):
    """A refused message leaves no open transaction behind for the next one."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    assert session.deliver(mail("a@example.org")).status is Status.ACCEPTED
    server.faults = {(1, stage): reply}
    result = session.deliver(mail("a@example.org"))
    assert result.status is (status or Status.TRANSIENT) and result.limit == limit
    assert session.smtp is None
    assert session.deliver(mail("a@example.org")).status is Status.ACCEPTED
    assert server.connections == 2 and server.tokens == 1


def test_connections_are_replaced_by_age_and_count(monkeypatch):
    """A connection never outlives CONNECTION_SECONDS or CONNECTION_MESSAGES."""
    now = [0.0]
    server = Server()
    session = smtp_session(monkeypatch, server, clock=lambda: now[0])
    monkeypatch.setattr("parishkit.stewardship.family_delivery.CONNECTION_MESSAGES", 2)
    for _ in range(3):
        session.deliver(mail())
    assert server.connections == 2
    now[0] += 300
    session.deliver(mail())
    assert server.connections == 3


def test_a_token_is_refreshed_before_it_expires(monkeypatch):
    """A new connection within TOKEN_MARGIN of expiry exchanges a new token."""
    now = [0.0]
    server = Server()
    soon = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=4)
    session = smtp_session(monkeypatch, server, expiry=soon, clock=lambda: now[0])
    session.deliver(mail())
    assert server.tokens == 1
    now[0] += 300
    session.deliver(mail())
    assert server.tokens == 2
    # A token with plenty of life left is kept across connections.
    later = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    session.credentials.expiry = later
    now[0] += 300
    session.deliver(mail())
    assert server.tokens == 2 and server.connections == 3


@pytest.mark.parametrize("retryable", [True, False])
def test_a_token_failure_sends_nothing(monkeypatch, retryable):
    """A failed exchange is definitely unsent; nothing is kept for later."""
    from google.auth.exceptions import RefreshError

    server = Server()
    session = smtp_session(monkeypatch, server)

    def refused(*args):
        raise RefreshError("private", retryable=retryable)

    monkeypatch.setattr("parishkit.stewardship.family_delivery._credentials", refused)
    result = session.deliver(mail())
    assert result.status is (Status.UNAVAILABLE if retryable else Status.SYSTEMIC)
    assert server.connections == 0 and session.credentials is None


# --- The helper's request loop -----------------------------------------------


def test_line_reader_never_returns_a_partial_request():
    """A request cut off before its newline is never acted on."""
    read, write = os.pipe()
    os.write(write, b'{"seq":1}\n{"seq":2')
    os.close(write)
    reader = LineReader(read)
    assert reader.readline(100, timeout=1) == b'{"seq":1}'
    assert reader.readline(100, timeout=1) is None
    os.close(read)


def test_a_request_must_match_the_session_identity():
    """A request cannot change the sender its session was admitted with."""
    value = mail().payload() | {"sender": "other@example.org"}
    raw = json.dumps({"seq": 1, "mail": value}).encode()
    with pytest.raises(ValueError):
        decode_session_request(raw, SETTINGS)


def test_serve_writes_started_before_each_submission(monkeypatch):
    """ "started" precedes every submission; results carry their sequence."""
    seen = []

    class Recorder:
        def __init__(self, value, settings, *, profile):
            assert value == KEY and settings == SETTINGS
            assert profile is DeploymentProfile.PRODUCTION

        def deliver(self, value):
            # The parent can already read this message's started line.
            seen.append(bytes(output))
            return FamilyDeliveryResult(Status.ACCEPTED, len(value.recipients))

        def close(self):
            seen.append("closed")

    output = bytearray()

    class Output:
        def write(self, value):
            output.extend(value)

        def flush(self):
            pass

    read, write = os.pipe()
    header = {
        "candidate": "c3ludGhldGljLWtleQ==",
        "settings": SETTINGS,
        "profile": "production",
    }
    lines = [header] + [{"seq": seq, "mail": mail().payload()} for seq in (1, 2)]
    os.write(write, b"".join(json.dumps(line).encode() + b"\n" for line in lines))
    os.close(write)
    assert serve(read, Output(), open_session=Recorder) == 0
    os.close(read)
    assert seen[0].endswith(b"started 1\n") and seen[1].endswith(b"started 2\n")
    assert seen[-1] == "closed"
    results = [json.loads(line) for line in output.splitlines()[1::2]]
    assert [result["seq"] for result in results] == [1, 2]


# --- The parent's batched session, with real helper processes -----------------


def test_a_batch_uses_one_helper_token_and_connection(gmail):
    """Many messages, one process, one token exchange, one SMTP connection."""
    session = gmail.session()
    batch = [mail() for _ in range(4)]
    results = [send(session, value) for value in batch]
    assert {result.status for result in results} == {Status.ACCEPTED}
    assert gmail.count("spawn") == gmail.count("token") == gmail.count("connect") == 1
    assert all(gmail.accepted(value) == 1 for value in batch)
    process = session.process
    session.close()
    assert process.poll() is not None and gmail.count("quit") == 1


def test_a_connection_drop_before_data_mid_batch_sends_once(gmail):
    """The idle drop is healed inside the helper; no message is sent twice."""
    session = gmail.session()
    first, second, third = mail(), mail(), mail()
    assert send(session, first).status is Status.ACCEPTED
    gmail.script(("mail", ["disconnect"]))
    assert send(session, second).status is Status.ACCEPTED
    assert send(session, third).status is Status.ACCEPTED
    assert [gmail.data(value) for value in (first, second, third)] == [1, 1, 1]
    assert gmail.count("connect") == 2 and gmail.count("spawn") == 1
    session.close()


def test_a_connection_drop_during_data_mid_batch_is_unknown(gmail):
    """The uncertain message is never resent; the rest of the batch continues."""
    session = gmail.session()
    first, second, third = mail(), mail(), mail()
    assert send(session, first).status is Status.ACCEPTED
    gmail.script(("data", ["disconnect"]))
    result = send(session, second)
    assert result.status is Status.UNKNOWN
    assert result.health is ProviderHealth.UNAVAILABLE
    assert send(session, third).status is Status.ACCEPTED
    assert [gmail.data(value) for value in (first, second, third)] == [1, 1, 1]
    session.close()


def test_a_helper_that_dies_before_starting_hands_the_message_on(gmail):
    """No "started" line means no submission: a fresh helper sends it once."""
    session = gmail.session()
    first, second = mail(), mail()
    assert send(session, first).status is Status.ACCEPTED
    gmail.script(("request", ["exit"]))
    assert send(session, second).status is Status.ACCEPTED
    assert gmail.count("spawn") == 2 and gmail.data(second) == 1
    session.close()


def test_a_helper_that_cannot_start_twice_leaves_the_message_unsent(gmail):
    """Two helpers gone before starting: definitely unsent, an ordinary retry."""
    session = gmail.session()
    value = mail()
    gmail.script(("request", ["exit"]), ("request", ["exit"]))
    result = send(session, value)
    assert result.status is Status.UNAVAILABLE and gmail.data(value) == 0
    assert gmail.count("spawn") == 2 and session.process is None


def test_a_helper_that_dies_mid_message_leaves_it_unknown(gmail):
    """After "started" a lost helper may have sent: unknown, never resent."""
    session = gmail.session()
    first, second, third = mail(), mail(), mail()
    assert send(session, first).status is Status.ACCEPTED
    gmail.script(("data", ["exit"]))
    assert send(session, second).status is Status.UNKNOWN
    assert send(session, third).status is Status.ACCEPTED
    assert [gmail.data(value) for value in (first, second, third)] == [1, 1, 1]
    assert gmail.count("spawn") == 2
    session.close()


def test_the_deadline_kills_the_helper_and_logs_it(gmail, monkeypatch):
    """A hung submission is killed at its deadline and recorded (#293/#314)."""
    recorded = []

    def recorder(helper, *, what, seconds):
        recorded.append((helper, what, seconds))
        return lambda stopped: recorded.append(stopped)

    monkeypatch.setattr(family_delivery_process, "helper_timeout_recorder", recorder)
    session = gmail.session()
    assert send(session, mail()).status is Status.ACCEPTED
    process = session.process
    gmail.script(("data", ["hang"]))
    started = time.monotonic()
    result = send(session, mail(), seconds=1.5)
    assert result.status is Status.UNKNOWN and time.monotonic() - started < 5
    assert process.poll() is not None and session.process is None
    assert recorded[-2] == ("family_delivery_worker", "mail_helper", 1.5)
    assert type(recorded[-1]) is float
    # The next message gets a fresh helper.
    assert send(session, mail()).status is Status.ACCEPTED
    session.close()


def test_lost_ownership_kills_the_helper(gmail):
    """A lease lost mid-message stops the helper; recovery owns the outcome."""
    session = gmail.session()
    assert send(session, mail()).status is Status.ACCEPTED
    process = session.process
    gmail.script(("data", ["hang"]))
    calls = []

    def check():
        calls.append(1)
        if gmail.count("hang"):
            raise RuntimeError("lease lost")

    with pytest.raises(ProviderCheckOwnershipLost):
        send(session, mail(), check=check)
    assert process.poll() is not None and session.process is None


def test_a_finished_result_survives_a_late_lease_failure(gmail):
    """#319: an accepted result that finished is kept even if a check then fails."""
    session = gmail.session()
    value = mail()
    finished = Event()

    def check():
        # The first check admits the send; later ones fail once Gmail accepted.
        if gmail.accepted(value):
            finished.wait(0.3)
            raise RuntimeError("lease check failed late")

    assert send(session, value, check=check).status is Status.ACCEPTED
    session.close()


def test_a_mailbox_limit_mid_batch_retires_the_helper(gmail):
    """A limit refusal is definitive and defers; the next message gets new state."""
    session = gmail.session()
    first, second, third = mail(), mail(), mail()
    assert send(session, first).status is Status.ACCEPTED
    gmail.script(("mail", LIMIT))
    result = send(session, second)
    assert (result.status, result.limit) == (Status.TRANSIENT, "daily")
    assert session.process is None and gmail.data(second) == 0
    assert send(session, third).status is Status.ACCEPTED
    assert gmail.count("spawn") == 2 and gmail.count("token") == 2
    session.close()


def test_a_permanent_refusal_is_per_message(gmail):
    """An invalid address fails only its own message; the helper carries on."""
    session = gmail.session()
    bad, good = mail("gone@example.org"), mail()
    gmail.script(("rcpt", ["reply", 550, "5.1.1 No such user"]))
    result = send(session, bad)
    assert result.status is Status.PERMANENT and result.permanent == (0,)
    assert send(session, good).status is Status.ACCEPTED
    assert gmail.data(bad) == 0 and gmail.count("spawn") == 1
    session.close()


def test_a_helper_is_rotated_by_idle_time_count_and_context(gmail, monkeypatch):
    """Idle, used-up or differently keyed helpers are replaced before use."""
    now = [0.0]
    session = gmail.session(clock=lambda: now[0])
    monkeypatch.setattr(family_delivery_process, "SESSION_MESSAGES", 2)
    send(session, mail())
    send(session, mail())
    assert gmail.count("spawn") == 1
    send(session, mail())
    assert gmail.count("spawn") == 2
    now[0] += family_delivery_process.PARENT_IDLE_SECONDS
    send(session, mail())
    assert gmail.count("spawn") == 3
    send(session, mail(), settings=SETTINGS | {"sender_name": "Parish Office"})
    assert gmail.count("spawn") == 4
    session.close()


def test_a_malformed_or_foreign_result_is_unknown(monkeypatch):
    """A result for another sequence number is never taken for this message's."""

    class Helper:
        def __init__(self):
            self.read, self.stdin_fd = os.pipe()
            stdout_fd, self.write = os.pipe()
            self.stdin = os.fdopen(self.stdin_fd, "wb")
            self.stdout = os.fdopen(stdout_fd, "rb")
            self.returncode = None
            Thread(target=self.run, daemon=True).start()

        def run(self):
            reader = LineReader(self.read)
            reader.readline(10**6, timeout=5)
            reader.readline(10**6, timeout=5)
            result = FamilyDeliveryResult(Status.ACCEPTED, 2).wire_payload()
            os.write(self.write, b"started 1\n")
            line = {"seq": 7, "result": result}
            os.write(self.write, json.dumps(line).encode() + b"\n")

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9
            os.close(self.write)

        def wait(self, timeout=None):
            return self.returncode

    session = FamilyMailSession(spawn=Helper)
    assert send(session, mail()).status is Status.UNKNOWN
    assert session.process is None


def test_the_real_helper_runs_isolated_without_network():
    """The production spawn: -I, no environment, the key only over stdin.

    A synthetic key fails to parse inside the helper, before any network
    access, which is a definitive systemic refusal; the helper is retired.
    """
    session = FamilyMailSession()
    result = send(session, mail())
    assert result.status is Status.SYSTEMIC and session.process is None


def test_spawn_failure_is_definitely_unsent():
    """A helper that cannot be started never saw the message."""

    def unavailable():
        raise OSError("no processes")

    session = FamilyMailSession(spawn=unavailable)
    assert send(session, mail()).status is Status.UNAVAILABLE


def test_a_session_rejects_an_unknown_session_type():
    """Only a FamilyMailSession may carry a Family message."""
    with pytest.raises(ValueError):
        submit_family(
            KEY, SETTINGS, mail(), seconds=5, check=lambda: None, session=object()
        )


def test_real_spawn_arguments_are_isolated(monkeypatch):
    """The session helper gets the same isolation as a one-message helper."""
    seen = {}

    def popen(args, **kwargs):
        seen.update(kwargs, args=args)
        raise OSError("not started")

    monkeypatch.setattr(subprocess, "Popen", popen)
    session = FamilyMailSession()
    assert send(session, mail()).status is Status.UNAVAILABLE
    assert seen["args"][1:] == [
        "-I",
        "-m",
        "parishkit.stewardship.family_delivery_worker",
        "--session",
    ]
    assert seen["env"] == {} and seen["stderr"] is subprocess.DEVNULL
    assert seen["close_fds"] is True


# --- Retiring, reaping and logging helpers that end (review of #334) ----------


def wait_until(condition, seconds=10):
    """Poll ``condition`` until true or fail after ``seconds``."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.05)


@pytest.fixture
def timeouts(monkeypatch):
    """Capture every record_timeout call the real recorder makes."""
    calls = []
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts.record_timeout",
        lambda event, **facts: calls.append((str(event), facts)),
    )
    return calls


def test_close_kills_and_logs_a_helper_that_will_not_quit(gmail, timeouts):
    """A helper still running RETIRE_SECONDS after EOF is killed and logged."""
    session = gmail.session()
    assert send(session, mail()).status is Status.ACCEPTED
    process = session.process
    gmail.script(("quit", ["hang"]))
    started = time.monotonic()
    session.close()
    assert 1.5 < time.monotonic() - started < 5 and process.poll() is not None
    ((event, facts),) = timeouts
    assert event == "helper_timed_out"
    assert (facts["helper"], facts["what"], facts["limit_seconds"]) == (
        "family_delivery_worker",
        "mail_helper",
        family_delivery_process.RETIRE_SECONDS,
    )
    assert facts["elapsed_seconds"] >= family_delivery_process.RETIRE_SECONDS


def test_a_polite_close_logs_nothing(gmail, timeouts):
    """A helper that QUITs on EOF is reaped without a timeout entry."""
    session = gmail.session()
    send(session, mail())
    session.close()
    assert timeouts == [] and gmail.count("quit") == 1


def test_rotation_never_spends_the_next_messages_budget(gmail, timeouts, monkeypatch):
    """Replacing a helper does not wait for it; a laggard is reaped and logged."""
    monkeypatch.setattr(family_delivery_process, "REAP_SECONDS", 0.1)
    session = gmail.session()
    assert send(session, mail()).status is Status.ACCEPTED
    old = session.process
    gmail.script(("quit", ["hang"]))
    other = SETTINGS | {"sender_name": "Parish Office"}
    started = time.monotonic()
    # The new helper starts at once, with the full deadline for its message.
    assert send(session, mail(), settings=other, seconds=3).status is (Status.ACCEPTED)
    assert time.monotonic() - started < 2 and old.poll() is None
    wait_until(lambda: old.poll() is not None)
    wait_until(lambda: timeouts)
    assert timeouts[0][1]["limit_seconds"] == family_delivery_process.RETIRE_SECONDS
    session.close()


def test_a_deadline_before_started_is_definitely_unsent(gmail, timeouts):
    """Killed before its "started" line, the helper never began the message."""
    session = gmail.session()
    value = mail()
    gmail.script(("request", ["hang"]))
    result = send(session, value, seconds=1.5)
    assert result.status is Status.UNAVAILABLE and gmail.data(value) == 0
    assert [event for event, _ in timeouts] == ["helper_timed_out"]
    assert session.process is None


def test_a_result_written_just_before_the_kill_is_kept(timeouts):
    """A complete result read after a deadline kill is the message's outcome."""
    result = FamilyDeliveryResult(Status.ACCEPTED, 2).wire_payload()

    class Helper:
        """Starts at once, but its result only lands as it is killed."""

        def __init__(self):
            self.read, stdin_fd = os.pipe()
            stdout_fd, self.write = os.pipe()
            self.stdin = os.fdopen(stdin_fd, "wb")
            self.stdout = os.fdopen(stdout_fd, "rb")
            self.returncode = None
            os.write(self.write, b"started 1\n")

        def poll(self):
            return self.returncode

        def kill(self):
            # The helper finished an instant before the kill landed.
            line = json.dumps({"seq": 1, "result": result}).encode() + b"\n"
            os.write(self.write, line)
            os.close(self.write)
            os.close(self.read)
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    session = FamilyMailSession(spawn=Helper)
    assert send(session, mail(), seconds=0.3).status is Status.ACCEPTED
    assert [event for event, _ in timeouts] == ["helper_timed_out"]


def test_a_helper_that_exits_idle_is_reaped_promptly(gmail, monkeypatch):
    """A helper leaving on its own idle timeout is reaped without waiting for mail."""
    monkeypatch.setattr(family_delivery_process, "REAP_SECONDS", 0.2)
    (gmail.directory / "idle").write_text("1")
    session = gmail.session()
    assert send(session, mail()).status is Status.ACCEPTED
    process = session.process
    # The helper leaves after a second idle; the reaper notices and reaps it.
    wait_until(lambda: session.process is None and not session.retiring)
    assert process.poll() == 0 and gmail.count("quit") == 1
    wait_until(lambda: session.reaper is None)
    # The next message simply gets a fresh helper.
    assert send(session, mail()).status is Status.ACCEPTED
    assert gmail.count("spawn") == 2
    session.close()


def test_the_reaper_retires_an_idle_helper_on_time(gmail, monkeypatch):
    """No key-holding helper sits idle past PARENT_IDLE_SECONDS."""
    monkeypatch.setattr(family_delivery_process, "REAP_SECONDS", 0.1)
    now = [0.0]
    session = gmail.session(clock=lambda: now[0])
    send(session, mail())
    process = session.process
    time.sleep(0.3)
    assert process.poll() is None
    now[0] += family_delivery_process.PARENT_IDLE_SECONDS
    wait_until(lambda: process.poll() is not None)
    assert gmail.count("quit") == 1
    wait_until(lambda: session.reaper is None)


PARENT = """
import os, sys
from tests.stewardship.family_mail_session_fakes import FakeGmailHelpers, SETTINGS, mail
from parishkit.stewardship.family_delivery_process import submit_family

gmail = FakeGmailHelpers(sys.argv[1])
session = gmail.session()
result = submit_family(
    b"k", SETTINGS, mail(), seconds=10, check=lambda: None, session=session
)
print(result.status.value, session.process.pid, flush=True)
os._exit(0)  # the mail worker dies without closing anything
"""


def test_a_helper_whose_parent_dies_quits_and_exits(gmail, tmp_path):
    """A dead mail worker leaves no helper behind: EOF ends it after a QUIT."""
    script = tmp_path / "parent.py"
    script.write_text(PARENT)
    output = subprocess.run(
        [sys.executable, str(script), str(gmail.directory)],
        capture_output=True,
        check=True,
        timeout=30,
        # A mail worker has a recorded deployment profile (#476); the parent
        # labels its helper's header with it, as runtime assembly would.
        env={
            "PYTHONPATH": CHILD_PATH,
            "DJANGO_SETTINGS_MODULE": "parishkit.stewardship.settings.test",
        },
    ).stdout.split()
    assert output[0] == b"accepted"
    pid = int(output[1])

    def gone():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    wait_until(gone)
    assert gmail.count("quit") == 1 and gmail.count("accepted") == 1


@pytest.mark.parametrize("batched", [True, False])
def test_the_transport_switch_selects_the_session(batched):
    """``family_mail_transport: per_message`` gives the handler no session."""
    from pathlib import Path

    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        _unavailable,
        delivery_handler,
    )

    handler = delivery_handler(
        None, credential_path=Path("/synthetic/unread"), batched=batched
    )
    session = handler.execute.keywords["session"]
    assert isinstance(session, FamilyMailSession) if batched else session is None
    # A scheduler never holds a session (or any provider path), whatever the
    # setting: its execute refuses outright.
    scheduler = delivery_handler(None, scheduler=True, batched=batched)
    assert scheduler.execute is _unavailable


@pytest.mark.parametrize(
    "transport,level", [("batched", "INFO"), ("per_message", "WARNING")]
)
def test_mail_dispatch_logs_its_transport_once(caplog, transport, level):
    """The effective transport is visible in the mail worker's startup log."""
    import logging

    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        log_family_mail_transport,
    )

    with caplog.at_level(logging.INFO):
        log_family_mail_transport(transport)
    ((record,),) = [caplog.records]
    assert record.levelname == level
    assert ("per_message" in record.getMessage()) is (transport == "per_message")


def test_only_reap_kills_a_retired_helper(gmail, timeouts, monkeypatch):
    """A laggard is killed by reap(), which runs before a message commits.

    ``submit`` only retires helpers, so a fatal failure to reap one can never
    strike while a message is already "submitting".
    """
    # No background reaper interferes within this test.
    monkeypatch.setattr(family_delivery_process, "REAP_SECONDS", 3600)
    session = gmail.session()
    assert send(session, mail()).status is Status.ACCEPTED
    old = session.process
    gmail.script(("quit", ["hang"]))
    other = SETTINGS | {"sender_name": "Parish Office"}
    assert send(session, mail(), settings=other).status is Status.ACCEPTED
    time.sleep(family_delivery_process.RETIRE_SECONDS + 0.2)
    assert send(session, mail(), settings=other).status is Status.ACCEPTED
    assert old.poll() is None and timeouts == []
    session.reap()
    assert old.poll() is not None
    assert [event for event, _ in timeouts] == ["helper_timed_out"]
    session.close()
