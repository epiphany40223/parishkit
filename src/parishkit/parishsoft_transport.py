"""Opt-in finite read transport for callers that own durable source leases.

Ordinary ParishKit clients keep their existing Session behavior. This adapter
intentionally supports only the shared v2 corpus/change-feed read endpoints.
Each session lazily starts one persistent helper process and reuses it (and
its keep-alive HTTPS connections) for later requests. Provider credentials go
to that helper once over stdin, never argv, an environment variable or a
temporary file. The owning preflight must fence each attempt and close its SQL
connections before returning; the parent still enforces every request's hard
deadline and kills the helper on timeout, lost ownership or any failure.
"""

import json
import math
import os
import re
import selectors
import subprocess
import sys
import time
from contextlib import suppress
from decimal import Decimal

import requests

from parishkit.parishsoft_http_worker import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    PROFILES,
    REQUEST_FIELDS,
    InvalidSourceResponse,
    validate_request,
)


class SourceTransportError(requests.ConnectionError):
    """A drained transport failure participates in shared bounded connection retries.

    Diagnostics contain no provider values. Lost admission and unconfirmed helper
    drainage use separate exception types and must never enter this retry path.
    """


class SourceTransportTimeout(SourceTransportError):
    """The helper was stopped because one request exceeded its deadline."""


class SourceTransportDrainFailure(BaseException):
    """Stop the consumer if the OS cannot confirm that its read helper stopped."""


def _reject_constant(value):
    """NaN and infinities are not JSON source values, despite Python's extension."""
    raise ValueError("Invalid source JSON number.")


def _unique_object(pairs):
    """A duplicate JSON key cannot silently select one conflicting field value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate source JSON field.")
        result[key] = value
    return result


class ExactSourceResponse(requests.Response):
    """Preserve decimal amounts and reject malformed UTF-8/ambiguous JSON fields."""

    def json(self, **kwargs):
        """Never let a float round-trip alter financial values before normalization."""
        if kwargs:
            raise ValueError("Source JSON decoding has fixed semantics.")
        try:
            return json.loads(
                self.content.decode("utf-8"),
                parse_float=Decimal,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("Source response contains invalid JSON.") from None


def _stop(process):
    """Kill and reap exactly this helper; never target a group or unrelated PID."""
    if process.poll() is not None:
        return
    try:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        raise SourceTransportDrainFailure("Source transport could not drain.") from None


# A reply header is "<status> <length>\n", "INVALID\n" or "ERROR\n"; anything
# longer than this without a newline is garbled rather than a slow header.
_MAX_HEADER_BYTES = 32
_HEADER = re.compile(rb"([1-9][0-9]{2}) (0|[1-9][0-9]{0,8})\n")


class _SourceHelper:
    """One persistent read helper process owned by one BoundedSourceSession.

    The process starts lazily with the session's key as its first stdin line
    and then serves one request at a time over the same pipes, so interpreter
    startup and TLS setup are paid once instead of per request. Any failure,
    timeout or lost ownership during an exchange stops it; the next request
    starts a fresh helper. Closed descriptors, an empty environment and
    isolated Python prevent inherited SQL sockets or environment injection.
    The package must be installed, including in development.
    """

    def __init__(self):
        """Start with no process and no retained credential."""
        self.process = None
        self.key = None

    def __repr__(self):
        """The retained key never enters diagnostics."""
        return "_SourceHelper()"

    def bind(self, key):
        """Replace a helper started under a different key before it is reused."""
        if key != self.key:
            self.stop()
            self.key = key

    def stop(self):
        """Kill and reap the helper (if any) and close both of its pipes."""
        process, self.process = self.process, None
        if process is None:
            return
        try:
            _stop(process)
        finally:
            for stream in (process.stdin, process.stdout):
                with suppress(OSError):
                    stream.close()

    def close(self):
        """Stop the helper and drop the key; used when the session closes."""
        try:
            self.stop()
        finally:
            self.key = None

    def _start(self):
        """Launch the helper and return its private key frame for stdin."""
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={},
            )
        except OSError:
            raise SourceTransportError(
                "Source request process is unavailable."
            ) from None
        self.process = process
        # Deadline enforcement relies on never blocking on either pipe.
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        return json.dumps({"api_key": self.key}, separators=(",", ":")).encode() + b"\n"

    def _idle_ok(self):
        """An idle helper must be alive with nothing unsolicited on stdout.

        Readable stdout between requests means EOF (the helper died) or stray
        bytes that would corrupt the next reply, so either forces a restart.
        """
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            return self.process.poll() is None and not selector.select(0)

    def exchange(self, payload, *, deadline, check):
        """Send one request line and read its reply frame before the deadline.

        Both pipes are non-blocking and polled in short waits so ``check`` can
        observe lost ownership or shutdown while the provider is slow.
        """
        pending = b""
        if self.process is not None and not self._idle_ok():
            self.stop()
        if self.process is None:
            pending = self._start()
        pending += payload + b"\n"
        reply = bytearray()
        length = None
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE)
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while True:
                check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceTransportTimeout(
                        "Source request exceeded its deadline."
                    )
                for ready, _events in selector.select(min(remaining, 0.25)):
                    if ready.fileobj is self.process.stdin:
                        pending = pending[self._write(pending) :]
                        if not pending:
                            selector.unregister(self.process.stdin)
                        continue
                    # Never read past the current frame: the header is short,
                    # and a known body length bounds every later read.
                    want = (
                        _MAX_HEADER_BYTES - len(reply)
                        if length is None
                        else length - len(reply)
                    )
                    chunk = self._read(max(want, 1))
                    reply += chunk
                    if length is None:
                        length, reply = _parse_header(reply)
                    if length is not None and len(reply) > length:
                        raise SourceTransportError("Source reply frame is invalid.")
                if length is not None and len(reply) == length and not pending:
                    check()
                    return bytes(reply)

    def _write(self, data):
        """Write what the pipe accepts now; a closed pipe means the helper died."""
        try:
            return os.write(self.process.stdin.fileno(), data)
        except BlockingIOError:
            return 0
        except OSError:
            raise SourceTransportError("Source request pipe is unavailable.") from None

    def _read(self, size):
        """Read available reply bytes; EOF mid-reply means the helper died."""
        try:
            chunk = os.read(self.process.stdout.fileno(), size)
        except BlockingIOError:
            return b""
        except OSError:
            raise SourceTransportError("Source request pipe is unavailable.") from None
        if not chunk:
            raise SourceTransportError(
                "Source request did not return a valid response."
            )
        return chunk


def _parse_header(reply):
    """Split a complete reply header, returning (body length, status + body).

    Returns (None, reply) until the header's newline arrives. The returned
    bytes use the historical ``b"<status>\\n" + body`` form, and the length
    counts the status line so callers compare against the whole buffer.
    """
    line, separator, rest = bytes(reply).partition(b"\n")
    if not separator:
        if len(reply) >= _MAX_HEADER_BYTES:
            raise SourceTransportError("Source reply frame is invalid.")
        return None, reply
    if line == b"INVALID":
        raise InvalidSourceResponse("Source response violates its body contract.")
    match = _HEADER.fullmatch(line + b"\n")
    if match is None:
        raise SourceTransportError("Source request did not return a valid response.")
    status, size = match.group(1), int(match.group(2))
    if size > MAX_RESPONSE_BYTES:
        raise InvalidSourceResponse("Source response violates its body contract.")
    if size and not 200 <= int(status) < 300:
        raise SourceTransportError("Source reply frame is invalid.")
    return len(status) + 1 + size, bytearray(status + b"\n" + rest)


def _exchange(payload, *, seconds, check, helper, on_timeout=None):
    """Run one request on the persistent helper under a hard wall-clock deadline.

    Requests' own timeout only bounds socket idleness, so the parent enforces
    the total deadline. Any exception, including lost ownership from ``check``,
    kills and reaps the helper so no request outlives its admission; the next
    request then starts a fresh helper. An unconfirmed reap is fatal. At the
    deadline, ``on_timeout(seconds, elapsed)`` runs right after the kill, so
    reporting never delays it.
    """
    started = time.monotonic()
    try:
        return helper.exchange(payload, deadline=started + seconds, check=check)
    except BaseException as error:
        elapsed = time.monotonic() - started
        helper.stop()
        if on_timeout is not None and isinstance(error, SourceTransportTimeout):
            with suppress(Exception):
                on_timeout(seconds, elapsed)
        raise


class BoundedSourceSession:
    """Requests-compatible GET/POST subset with mandatory per-attempt preflight.

    ``before_request`` accepts the total reserved seconds (read plus five-second
    forced drain); it must acquire fresh fences and return with SQL closed.
    ``check`` fails after lost ownership or shutdown; it may not access SQL.
    Neither callback can be provided by an HTTP request or broker payload.
    """

    def __init__(self, *, before_request, check, profile, on_timeout=None):
        """Require concrete owning hooks; an absent fence must not default to allow.

        ``profile`` is the deployment profile name (one of the helper's
        ``PROFILES``) every request frame carries, so the helper, which cannot
        read the profile itself, admits exactly that profile's base URL.
        ``on_timeout(seconds, elapsed)`` is told, just after the helper is
        killed, that a request exceeded its deadline, so the owner can record
        it.
        """
        if not callable(before_request) or not callable(check):
            raise TypeError("Bounded source transport requires owning callbacks.")
        if type(profile) is not str or profile not in PROFILES:
            raise TypeError("Bounded source transport requires a deployment profile.")
        if on_timeout is not None and not callable(on_timeout):
            raise TypeError("A source timeout observer must be callable.")
        self.profile = profile
        self.on_timeout = on_timeout
        self.headers = requests.structures.CaseInsensitiveDict()
        self.before_request = before_request
        self.check = check
        self._helper = _SourceHelper()

    def __repr__(self):
        """Credentials held in Session-compatible headers never enter diagnostics."""
        return "BoundedSourceSession()"

    def get(self, url, *, params=None, timeout):
        """Use the fixed read protocol, excluding requests' broad option surface."""
        return self._request("GET", url, params, timeout)

    def post(self, url, *, json=None, timeout):
        """POST is restricted to the four documented read/search endpoints."""
        return self._request("POST", url, json, timeout)

    def close(self):
        """Kill and reap the helper, closing its sockets, and drop retained keys."""
        try:
            self._helper.close()
        finally:
            self.headers.clear()

    def _request(self, method, url, parameters, timeout):
        """Validate before preflight or process creation, then return safe metadata."""
        try:
            request = validate_request(
                {
                    "method": method,
                    "url": url,
                    "parameters": {} if parameters is None else parameters,
                    "api_key": self.headers.get("x-api-key"),
                    "timeout": timeout,
                    "profile": self.profile,
                }
            )
            # The key reaches the helper only in its start frame, not per request.
            payload = json.dumps(
                {field: request[field] for field in sorted(REQUEST_FIELDS)},
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
            if len(payload) > MAX_REQUEST_BYTES:
                raise ValueError("Source request exceeds its byte bound.")
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("Invalid bounded source request.") from None
        self.check()
        self.before_request(math.ceil(timeout) + 5)
        self.check()
        self._helper.bind(request["api_key"])
        output = _exchange(
            payload,
            seconds=timeout,
            check=self.check,
            helper=self._helper,
            on_timeout=self.on_timeout,
        )
        status, separator, body = output.partition(b"\n")
        if not separator or not _valid_status(status):
            raise SourceTransportError("Source response status is invalid.")
        response = ExactSourceResponse()
        response.status_code = int(status)
        if 200 <= response.status_code < 300 and not body:
            raise InvalidSourceResponse("Source response contains no JSON body.")
        response.url = url  # No query strings, request headers or original request.
        response.encoding = "utf-8"
        response._content = body if 200 <= response.status_code < 300 else b""
        response._content_consumed = True
        return response


def _valid_status(status):
    """Require an exact three-digit HTTP status, never arbitrary helper text."""
    return len(status) == 3 and status.isdigit() and 100 <= int(status) <= 599
