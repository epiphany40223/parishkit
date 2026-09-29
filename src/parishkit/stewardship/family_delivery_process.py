"""Bounded private Family delivery transport reusing the maintained pipe owner."""

import base64
import hashlib
import json
import math
import subprocess
import sys
import time
from contextlib import suppress
from threading import Event, Lock, Thread

from .accounts.integration_candidates import _object
from .accounts.key_files import MAX_FILE_BYTES
from .family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
    delivery_settings,
)
from .family_delivery_worker import SESSION_MESSAGES, SESSION_SECONDS
from .provider_checks import (
    ProviderCheckDrainFailure,
    ProviderCheckOwnershipLost,
    _check_owner,
    _stop,
    helper_timeout_recorder,
)
from .readiness_delivery import DeliveryOutcome
from .readiness_delivery_process import _submit_private
from .readiness_delivery_worker import MAX_INPUT
from .web.digest_content import MAX_BODY_BYTES, MAX_CHART_BYTES
from .web.weekly_digest_content import MAX_WEEKLY_BODY_BYTES

# JSON may expand each body byte into a six-byte control-character escape;
# base64 chart overhead is below 2x. Admission and the private pipe must agree.
MAX_DIGEST_INPUT = MAX_INPUT + 12 * MAX_BODY_BYTES + 2 * MAX_CHART_BYTES
MAX_WEEKLY_INPUT = MAX_INPUT + 12 * MAX_WEEKLY_BODY_BYTES


def submit_family(value, settings, mail, *, seconds, check, session=None):
    """Missing or malformed acknowledgement is never proof of non-acceptance.

    With ``session`` (a FamilyMailSession) the message goes to that batch's
    long-lived helper (#284); without it, to a one-message helper.
    """
    if not isinstance(mail, FamilyDeliveryMail) or not (
        session is None or isinstance(session, FamilyMailSession)
    ):
        raise ValueError("Invalid private Family submission invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="family_delivery_worker",
        limit=MAX_INPUT,
        session=session,
    )


def submit_digest(value, settings, mail, *, seconds, check):
    """Dispatch only an admitted one-Admin compiled digest, never Family payloads."""
    from .digest_delivery import DigestDeliveryMail

    if not isinstance(mail, DigestDeliveryMail):
        raise ValueError("Invalid private digest submission invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="digest_delivery_worker",
        limit=MAX_DIGEST_INPUT,
    )


def submit_weekly(value, settings, mail, *, seconds, check):
    """Only the separately validated no-attachment weekly type gets this budget."""
    from .weekly_delivery import WeeklyDeliveryMail

    if not isinstance(mail, WeeklyDeliveryMail):
        raise ValueError("Invalid private weekly submission invocation.")
    return _submit_mail(
        value,
        settings,
        mail,
        seconds=seconds,
        check=check,
        helper="weekly_delivery_worker",
        limit=MAX_WEEKLY_INPUT,
    )


def _submit_mail(value, settings, mail, *, seconds, check, helper, limit, session=None):
    """Share bounded IPC and outcomes after the caller's typed mail validation."""
    count = len(mail.recipients)
    unknown = FamilyDeliveryResult(FamilyDeliveryStatus.UNKNOWN, count)
    try:
        settings = delivery_settings(settings)
        if (
            type(value) is not bytes
            or not 0 < len(value) <= MAX_FILE_BYTES
            or type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or not 0 < seconds <= 30
            or not callable(check)
            or any(
                getattr(mail, key) != settings[key] for key in ("sender", "reply_to")
            )
        ):
            raise ValueError("Invalid private Family submission invocation.")
        payload = json.dumps(
            {
                "candidate": base64.b64encode(value).decode("ascii"),
                "settings": settings,
                "mail": mail.payload(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
    except (ValueError, TypeError):
        # This try block performs no IO. Do not relabel a deterministic local
        # rejection as possible SMTP acceptance, or suppress an untested address.
        return FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, count)
    if len(payload) > limit:
        return FamilyDeliveryResult(
            FamilyDeliveryStatus.PERMANENT, count, health=ProviderHealth.UNOBSERVED
        )

    def decode(output):
        """Reject extra fields, duplicate keys and another envelope's result."""
        try:
            return FamilyDeliveryResult.from_payload(
                json.loads(output.decode("utf-8"), object_pairs_hook=_object),
                recipient_count=count,
                wire=True,
            )
        except (ValueError, TypeError, RecursionError):
            return unknown

    if session is not None:
        # The same size admission as a one-message helper: the combined
        # envelope above bounds both of the session's lines.
        result = session.submit(
            value, settings, mail.payload(), count=count, seconds=seconds, check=check
        )
    else:
        result = _submit_private(
            payload,
            helper=helper,
            seconds=seconds,
            check=check,
            decode=decode,
        )
    if isinstance(result, FamilyDeliveryResult):
        return result
    if result is DeliveryOutcome.NOT_SENT:
        return FamilyDeliveryResult(FamilyDeliveryStatus.UNAVAILABLE, count)
    return unknown


# The parent replaces an idle batch helper after this long, well before the
# helper's own HELPER_IDLE_SECONDS, so it never writes to one about to leave.
PARENT_IDLE_SECONDS = 60
# A helper's result line is a small closed JSON object.
MAX_RESULT = 4096
# A retired helper gets this long to QUIT and exit after its EOF; then it is
# killed, and the kill is logged like any other helper timeout.
RETIRE_SECONDS = 2
# How often a session's reaper retires an idle helper and reaps retired ones.
REAP_SECONDS = 5
# The helper ended before starting the request: hand it to a fresh helper.
_RESTART = object()


def _spawn_helper():
    """Start one batched Family helper: isolated, no environment, no stderr."""
    return subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-m",
            "parishkit.stewardship.family_delivery_worker",
            "--session",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env={},
    )


class FamilyMailSession:
    """One long-lived private Family helper shared by a mail worker's messages.

    Each Family message is still its own Task with its own lease, fence and
    committed "submitting" state; this object holds no database state and no
    lease. It only keeps one helper process (and so one OAuth token and one
    SMTP connection) alive between those Tasks, so a batch of invitations
    pays for process start, token exchange and TLS/AUTH once, not per message.

    Per message, exactly as with a one-message helper: the owning Task has
    already committed "submitting" and its provider deadline; ``submit``
    writes one request, waits for its "started" line and one result line
    while checking the lease every quarter second, and returns that result
    for the Task to settle. The per-message deadline is enforced here: when
    it passes the helper is killed and reaped and the kill is logged through
    ``helper_timeout_recorder`` (#293/#314). The message is then definitely
    unsent if the helper never wrote its "started" line (read to EOF after
    the kill), delivery unknown if it did, and settled normally if its whole
    result arrived before the kill. Lost ownership kills the helper too. A
    finished result is kept even if a lease check failed after it finished
    (#319).

    Certainty across helper faults: a helper that is gone before its
    "started" line never began the message, so the message is retried once
    on a fresh helper, still under the same deadline (never twice: a helper
    that cannot even start leaves the message definitely unsent). After
    "started", a missing, malformed or mismatched result is delivery unknown
    and the helper is killed; nothing is ever resent automatically.

    A helper is replaced after SESSION_MESSAGES messages, SESSION_SECONDS,
    PARENT_IDLE_SECONDS idle, a different key or settings, or any result that
    reports provider trouble (a limit, an outage or a systemic fault), so a
    fresh token and connection follow any fault. Replacing never blocks a
    message: the old helper is retired (its stdin closed, so it QUITs and
    exits) and reaped later, and one still running RETIRE_SECONDS after that
    is killed and logged. A reaper thread, alive only while the session has
    helpers, retires an idle helper on time and reaps one that exited on its
    own, so neither a key-holding idle helper nor a zombie lingers.
    """

    def __init__(self, *, spawn=_spawn_helper, clock=time.monotonic):
        """Start no helper until the first message needs one."""
        self.spawn = spawn
        self.clock = clock
        self.lock = Lock()
        self.process = None
        self.key = None
        self.opened = 0.0
        self.used = 0.0
        self.count = 0
        self.seq = 0
        # Retired helpers not yet reaped: (process, retired at, timeout hook).
        self.retiring = []
        self.reaper = None

    def submit(self, value, settings, mail, *, count, seconds, check):
        """Submit one admitted mail payload; return a result or DeliveryOutcome."""
        with self.lock:
            _check_owner(check)
            candidate = base64.b64encode(value).decode("ascii")
            key = hashlib.sha256(
                json.dumps([candidate, settings], sort_keys=True).encode("utf-8")
            ).digest()
            # Rotation never waits for a helper, so the message's whole budget
            # starts here, after it.
            self._rotate(key)
            deadline = time.monotonic() + seconds
            on_timeout = helper_timeout_recorder(
                "family_delivery_worker", what="mail_helper", seconds=seconds
            )
            for _ in range(2):
                # After a restart the dead helper was retired: spawn anew.
                header = None
                if self.process is None:
                    try:
                        self.process = self.spawn()
                    except OSError:
                        return DeliveryOutcome.NOT_SENT
                    self.key, self.count = key, 0
                    self.opened = self.used = self.clock()
                    header = _line({"candidate": candidate, "settings": settings})
                    self._start_reaper()
                self.seq += 1
                self.count += 1
                request = _line({"seq": self.seq, "mail": mail})
                outcome = self._exchange(
                    header, request, count, deadline, check, on_timeout
                )
                if outcome is not _RESTART:
                    self.used = self.clock()
                    if not isinstance(outcome, FamilyDeliveryResult) or (
                        outcome.limit is not None
                        or outcome.health
                        in (ProviderHealth.UNAVAILABLE, ProviderHealth.SYSTEMIC)
                    ):
                        self._retire()
                    return outcome
                self._retire()
            return DeliveryOutcome.NOT_SENT

    def _rotate(self, key):
        """Retire a helper that has ended, aged out, idled or has other context."""
        self._reap()
        if self.process is not None and (
            key != self.key
            or self.count >= SESSION_MESSAGES
            or self.clock() - self.opened >= SESSION_SECONDS
        ):
            self._retire()

    def _retire(self):
        """Send the current helper EOF (it QUITs and exits); reap it later."""
        process, self.process = self.process, None
        if process is None:
            return
        with suppress(Exception):
            process.stdin.close()
        self.retiring.append(
            (
                process,
                time.monotonic(),
                helper_timeout_recorder(
                    "family_delivery_worker",
                    what="mail_helper",
                    seconds=RETIRE_SECONDS,
                ),
            )
        )

    def _reap(self, *, wait=False):
        """Retire an idle or exited helper; reap, or kill and log, retired ones.

        With ``wait`` (closing the session) each retired helper is waited for
        until its RETIRE_SECONDS are up, instead of only polled.
        """
        if self.process is not None and (
            self.process.poll() is not None
            or self.clock() - self.used >= PARENT_IDLE_SECONDS
        ):
            self._retire()
        pending = []
        for process, retired, record in self.retiring:
            if wait:
                with suppress(subprocess.TimeoutExpired, OSError):
                    process.wait(
                        timeout=max(0, retired + RETIRE_SECONDS - time.monotonic())
                    )
            if process.poll() is None:
                if time.monotonic() - retired < RETIRE_SECONDS:
                    pending.append((process, retired, record))
                    continue
                _stop(process)
                with suppress(Exception):
                    record(time.monotonic())
            _close_streams(process)
        self.retiring = pending

    def _start_reaper(self):
        """Run the reaper while this session has any helper; it ends by itself."""
        if self.reaper is None or not self.reaper.is_alive():
            self.reaper = Thread(
                target=self._reap_loop, name="family-mail-reaper", daemon=True
            )
            self.reaper.start()

    def _reap_loop(self):
        """Every REAP_SECONDS reap under the session lock, until nothing is left."""
        while True:
            time.sleep(REAP_SECONDS)
            with self.lock:
                self._reap()
                if self.process is None and not self.retiring:
                    self.reaper = None
                    return

    def _exchange(self, header, request, count, deadline, check, on_timeout):
        """One request/result round trip under the lease and the deadline.

        One pump thread owns the pipes (a blocked write or read cannot stall
        lease checks); this thread checks ownership and the deadline, and
        kills the helper when either ends the wait.
        """
        process = self.process
        # "written" is False before any request byte, None while writing and
        # True once the whole request (with its final newline) is written.
        state = {"written": False, "started": False, "line": None}
        done = Event()
        expected = b"started %d\n" % self.seq

        def pump():
            """Write the request, then read its started and result lines."""
            try:
                if header is not None:
                    process.stdin.write(header)
                state["written"] = None  # writing: the request may arrive
                process.stdin.write(request)
                process.stdin.flush()
                state["written"] = True
                line = process.stdout.readline(len(expected))
                if line == expected:
                    state["started"] = True
                    state["line"] = process.stdout.readline(MAX_RESULT)
                else:
                    state["line"] = line
            except Exception:
                # Keep pipe errors private; the state says how far it got.
                pass
            finally:
                done.set()

        thread = Thread(target=pump, name="family-mail-session", daemon=True)
        stopped = None
        thread.start()
        try:
            while not done.is_set():
                try:
                    _check_owner(check)
                except ProviderCheckOwnershipLost:
                    if not done.is_set():
                        raise
                if done.is_set():
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stopped = time.monotonic()
                    break
                done.wait(min(remaining, 0.25))
        except BaseException:
            self._kill(thread)
            raise
        if stopped is not None:
            # Killing ends the pump: it reads the helper's output to EOF (or
            # its blocked write fails), so ``state`` is final once it is joined.
            self._kill(thread)
            with suppress(Exception):
                on_timeout(stopped)
        else:
            thread.join(timeout=5)
        if not state["started"]:
            if stopped is not None:
                # A helper writes "started" before it submits, and the pump
                # read everything the killed helper wrote: without that line
                # it never began this message. Anything else is a protocol
                # fault, so possibly sent.
                return (
                    DeliveryOutcome.NOT_SENT
                    if state["line"] in (None, b"")
                    else DeliveryOutcome.UNKNOWN
                )
            if state["written"] is not True or state["line"] == b"":
                # The request could not be written (the helper was gone), or
                # the helper ended before its "started" line: it never began
                # this message, so no part of it was submitted.
                return _RESTART
            self._kill(thread)
            return DeliveryOutcome.UNKNOWN
        line = state["line"]
        if not line or not line.endswith(b"\n"):
            # Started but no complete result: the helper may have sent it.
            self._kill(thread)
            return DeliveryOutcome.UNKNOWN
        # A complete result is real even if the deadline killed the helper a
        # moment after it was written; it is never discarded.
        try:
            value = json.loads(line.decode("utf-8"), object_pairs_hook=_object)
            if type(value) is not dict or set(value) != {"seq", "result"}:
                raise ValueError("Invalid Family session result.")
            if type(value["seq"]) is not int or value["seq"] != self.seq:
                raise ValueError("Family session result is for another message.")
            return FamilyDeliveryResult.from_payload(
                value["result"], recipient_count=count, wire=True
            )
        except (ValueError, TypeError, RecursionError, AttributeError):
            self._kill(thread)
            return DeliveryOutcome.UNKNOWN

    def _kill(self, thread):
        """Kill and reap the helper, then join its pump; failing that is fatal."""
        process, self.process = self.process, None
        if process is None:
            return
        _stop(process)
        thread.join(timeout=5)
        if thread.is_alive():
            # A pump still inside a pipe call may hold its stream lock.
            raise ProviderCheckDrainFailure("Family mail session could not drain.")
        _close_streams(process)

    def close(self):
        """End every helper: EOF lets each QUIT; a laggard is killed and logged."""
        with self.lock:
            self._retire()
            self._reap(wait=True)


def _line(value):
    """One compact JSON request line for the helper's pipe."""
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _close_streams(process):
    """Close a reaped helper's pipes without raising."""
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with suppress(Exception):
                stream.close()
