"""Private Family SMTP helper; no retained credentials or diagnostics.

Run with no argument it submits exactly one message and exits. Run with
``--session`` it is the batched helper (#284): one per mail worker batch,
reusing one OAuth token and SMTP connection across many messages. See
``family_delivery_process.FamilyMailSession`` for the parent's side.
"""

import base64
import json
import logging
import os
import select
import sys
import time

from .accounts.integration_candidates import _object
from .accounts.key_files import MAX_FILE_BYTES
from .family_delivery import (
    FamilyDeliveryMail,
    SmtpSession,
    deliver_family,
    delivery_settings,
)
from .readiness_delivery_worker import MAX_INPUT, request_profile

# A batched helper serves at most this many messages over this many seconds,
# then exits; it also exits after HELPER_IDLE_SECONDS without a request. The
# parent rotates earlier (after PARENT_IDLE_SECONDS idle), so it never writes
# to a helper that is about to leave, and a request a helper leaves unread is
# provably unsent anyway: see the "started" line in serve().
SESSION_MESSAGES = 100
SESSION_SECONDS = 600
HELPER_IDLE_SECONDS = 120


def decode_request(raw):
    """Validate the entire closed request before any token exchange or submission."""
    return decode_envelope(raw, mail_class=FamilyDeliveryMail, limit=MAX_INPUT)


def decode_envelope(raw, *, mail_class, limit):
    """Share envelope parsing; compiled helpers choose their own closed mail type.

    Every request names the parent's deployment profile (#476): the helper has
    no environment, so the profile in the request is the only thing that can
    admit the LOCAL mail-catcher transport, and it refuses it for any other.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError("Invalid private delivery request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"settings", "candidate", "mail", "profile"}
        or type(request["candidate"]) is not str
    ):
        raise ValueError("Invalid private delivery request.")
    candidate = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(candidate) <= MAX_FILE_BYTES:
        raise ValueError("Invalid private credential size.")
    settings = delivery_settings(request["settings"])
    mail = mail_class.from_payload(request["mail"])
    if any(getattr(mail, key) != settings[key] for key in ("sender", "reply_to")):
        raise ValueError("Private delivery context differs.")
    return candidate, settings, mail, request_profile(request["profile"])


class LineReader:
    """Read bounded newline-terminated requests from a raw descriptor.

    An incomplete final line (the parent died or was cut off mid-write) is
    never returned: a request is acted on only when all of it arrived.
    """

    def __init__(self, fd):
        """Own no buffering but this one; select() must see every byte."""
        self.fd = fd
        self.buffer = b""

    def readline(self, limit, *, timeout):
        """Return one line without its newline, or None at EOF or timeout."""
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            if len(self.buffer) > limit:
                raise ValueError("Private request exceeds its bound.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            # Short ticks let a closed descriptor end the wait promptly.
            ready, _, _ = select.select([self.fd], [], [], min(remaining, 0.5))
            if not ready:
                continue
            chunk = os.read(self.fd, 65536)
            if not chunk:
                return None
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        if len(line) > limit:
            raise ValueError("Private request exceeds its bound.")
        return line


def decode_session_header(raw):
    """The session's first line: the key, closed Workspace identity and profile."""
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"settings", "candidate", "profile"}
        or type(request["candidate"]) is not str
    ):
        raise ValueError("Invalid private session header.")
    candidate = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(candidate) <= MAX_FILE_BYTES:
        raise ValueError("Invalid private credential size.")
    return (
        candidate,
        delivery_settings(request["settings"]),
        request_profile(request["profile"]),
    )


def decode_session_request(raw, settings):
    """One message request: a sequence number and one closed Family mail."""
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (
        type(request) is not dict
        or set(request) != {"seq", "mail"}
        or type(request["seq"]) is not int
        or not 0 < request["seq"] < 2**53
    ):
        raise ValueError("Invalid private session request.")
    mail = FamilyDeliveryMail.from_payload(request["mail"])
    if any(getattr(mail, key) != settings[key] for key in ("sender", "reply_to")):
        raise ValueError("Private delivery context differs.")
    return request["seq"], mail


def serve(fd, output, *, open_session=SmtpSession, clock=time.monotonic):
    """Serve one batch: a header line, then one request and result at a time.

    For each request the helper first writes ``started <seq>`` and flushes it,
    and only then submits the message. The parent therefore knows that a
    helper which ended before that line never began the submission (the
    message is definitely unsent and safe to hand to a fresh helper), while
    one that ended after it may have (delivery unknown). The result line
    carries the same sequence number, so it can never be taken for another
    message's. Only one request is ever in flight: the parent writes the next
    only after reading this one's result, so a parent that dies leaves at
    most the current message running, and the helper then reads EOF and
    exits. Malformed input raises, emitting nothing further.
    """
    reader = LineReader(fd)
    header = reader.readline(MAX_INPUT, timeout=HELPER_IDLE_SECONDS)
    if header is None:
        return 0
    candidate, settings, profile = decode_session_header(header)
    session = open_session(candidate, settings, profile=profile)
    started = clock()
    try:
        for _ in range(SESSION_MESSAGES):
            if clock() - started >= SESSION_SECONDS:
                break
            line = reader.readline(MAX_INPUT, timeout=HELPER_IDLE_SECONDS)
            if line is None:
                break
            seq, mail = decode_session_request(line, settings)
            output.write(b"started %d\n" % seq)
            output.flush()
            result = session.deliver(mail)
            output.write(
                json.dumps(
                    {"seq": seq, "result": result.wire_payload()},
                    separators=(",", ":"),
                ).encode("ascii")
                + b"\n"
            )
            output.flush()
    finally:
        session.close()
    return 0


def main():
    """Malformed invocation emits no outcome; the parent conservatively holds it."""
    logging.disable(logging.CRITICAL)
    if sys.argv[1:] == ["--session"]:
        try:
            return serve(sys.stdin.fileno(), sys.stdout.buffer)
        except Exception:
            return 1
    try:
        candidate, settings, mail, profile = decode_request(
            sys.stdin.buffer.read(MAX_INPUT + 1)
        )
        result = deliver_family(candidate, settings, mail, profile=profile)
        sys.stdout.write(
            json.dumps(result.wire_payload(), separators=(",", ":")) + "\n"
        )
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
