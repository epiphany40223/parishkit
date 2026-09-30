"""A batched Family helper process with a scripted fake Gmail (#284 tests).

Run as ``python fake_gmail_helper.py STATE_DIR``. It is the real helper loop
(``family_delivery_worker.serve``) and the real ``SmtpSession``; only the
token exchange and the SMTP server are fakes. Being a real process, it is
killed, reaped and read to EOF exactly like the production helper.

``STATE_DIR/faults.json`` is a shared queue of faults, consumed in order by
this and any later helper process: when a helper reaches the stage named by
the first fault, that fault is removed and applied. Stages are ``request``
(a request line was read, before its "started" line), ``connect``, ``ehlo``,
``auth``, ``mail``, ``rcpt``, ``data`` and ``quit``. Actions are
``["reply", code, text]``, ``["disconnect"]`` (the server drops the
connection), ``["exit"]`` (the helper process dies at once) and ``["hang"]``.
Every token, connection, QUIT and DATA is appended to ``events.jsonl``, so a
test can count exactly what Gmail would have received. A number in
``STATE_DIR/idle`` replaces the helper's own idle timeout, in seconds.
"""

import json
import os
import smtplib
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from parishkit.stewardship import family_delivery, family_delivery_worker

STATE = Path(sys.argv[1]) if __name__ == "__main__" else None


def record(event, **values):
    """Append one event line; each write is a single small append."""
    with open(STATE / "events.jsonl", "a") as log:
        log.write(json.dumps({"event": event, "pid": os.getpid()} | values) + "\n")


def fault(stage):
    """Pop and return the head fault when it names ``stage``, else None."""
    path = STATE / "faults.json"
    faults = json.loads(path.read_text()) if path.exists() else []
    if not faults or faults[0]["stage"] != stage:
        return None
    path.write_text(json.dumps(faults[1:]))
    action = faults[0]["action"]
    if action[0] == "exit":
        record("exit", stage=stage)
        os._exit(3)
    if action[0] == "hang":
        record("hang", stage=stage)
        time.sleep(3600)
    return action


def credentials(value, settings, session):
    """A fresh synthetic token valid for an hour; each exchange is recorded."""
    record("token")
    return SimpleNamespace(
        token="synthetic-token",
        expiry=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
    )


class FakeGmail:
    """One scripted SMTP connection to smtp.gmail.com."""

    def __init__(self, host, port, **kwargs):
        """Open a connection unless a fault refuses or drops it."""
        action = fault("connect")
        if action and action[0] == "disconnect":
            raise ConnectionRefusedError("synthetic refusal")
        if action and action[0] == "reply":
            raise smtplib.SMTPConnectError(action[1], action[2].encode())
        self.closed = False
        record("connect")

    def __enter__(self):
        """Mirror smtplib's context manager."""
        return self

    def __exit__(self, *args):
        """QUIT, as smtplib does on leaving the context."""
        if not self.closed:
            fault("quit")
            record("quit")

    def _reply(self, stage, default=250):
        """Apply this stage's fault, or answer ``default``."""
        if self.closed:
            raise smtplib.SMTPServerDisconnected("connection closed")
        action = fault(stage)
        if action and action[0] == "disconnect":
            self.closed = True
            raise smtplib.SMTPServerDisconnected("synthetic drop")
        if action and action[0] == "reply":
            return action[1], action[2].encode()
        return default, b"OK"

    def ehlo(self):
        """Greet; 250 unless scripted."""
        return self._reply("ehlo")

    def docmd(self, name, value):
        """AUTH XOAUTH2; 235 unless scripted."""
        return self._reply("auth", 235)

    def has_extn(self, name):
        """No SMTPUTF8 is needed by the synthetic ASCII mail."""
        return False

    def mail(self, sender, *, options):
        """MAIL FROM; 250 unless scripted."""
        return self._reply("mail")

    def rcpt(self, address):
        """RCPT TO; 250 unless scripted."""
        return self._reply("rcpt")

    def data(self, content):
        """DATA: every reached DATA is recorded with its message's identity.

        A drop here happens after the message was transmitted, which is
        exactly the uncertain case: Gmail may or may not have accepted it.
        """
        key = message_from_bytes(content)["Message-ID"]
        record("data", key=key)
        reply = self._reply("data")
        record("accepted" if reply[0] == 250 else "refused", key=key, code=reply[0])
        return reply


def main():
    """Serve one batch exactly as the production helper does."""
    family_delivery._credentials = credentials
    decode = family_delivery_worker.decode_session_request

    def request(raw, settings):
        fault("request")
        return decode(raw, settings)

    family_delivery_worker.decode_session_request = request
    idle = STATE / "idle"
    if idle.exists():
        family_delivery_worker.HELPER_IDLE_SECONDS = float(idle.read_text())
    record("spawn")
    return family_delivery_worker.serve(
        sys.stdin.fileno(),
        sys.stdout.buffer,
        open_session=partial(
            family_delivery.SmtpSession,
            smtp_factory=FakeGmail,
            session_factory=nullcontext,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
