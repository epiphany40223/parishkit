"""Opt-in finite read transport for callers that own durable source leases.

Ordinary ParishKit clients keep their existing Session behavior. This adapter
intentionally supports only the shared v2 corpus/change-feed read endpoints.
Provider credentials go to a short-lived helper over stdin, never argv, an
environment variable or a temporary file. The owning preflight must fence
each attempt and close its SQL connections before returning.
"""

import json
import math
import subprocess
import sys
import time
from contextlib import suppress
from decimal import Decimal

import requests

from parishkit.parishsoft_http_worker import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    validate_request,
)


class SourceTransportError(requests.ConnectionError):
    """A drained transport failure participates in shared bounded connection retries.

    Diagnostics contain no provider values. Lost admission and unconfirmed helper
    drainage use separate exception types and must never enter this retry path.
    """


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


def _exchange(payload, *, seconds, check):
    """Bound the read helper while checking lease/drain state between short waits.

    communicate retries retain pipe state and input; only the first call supplies
    stdin. The trusted helper limits stdout before writing and stderr is discarded.
    Closed descriptors and isolated Python prevent inherited SQL sockets or
    Python environment injection. The package must be installed, including in dev.
    """
    deadline = time.monotonic() + seconds
    process = None
    try:
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
        pending_input = payload
        while True:
            check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceTransportError("Source request exceeded its deadline.")
            try:
                output, _ = process.communicate(
                    input=pending_input, timeout=min(remaining, 0.25)
                )
                break
            except subprocess.TimeoutExpired:
                pending_input = None
            except OSError:
                raise SourceTransportError(
                    "Source request pipe is unavailable."
                ) from None
        check()
        if process.returncode != 0 or len(output) > MAX_RESPONSE_BYTES + 4:
            raise SourceTransportError(
                "Source request did not return a valid response."
            )
        return output
    finally:
        if process is not None:
            try:
                _stop(process)
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()


class BoundedSourceSession:
    """Requests-compatible GET/POST subset with mandatory per-attempt preflight.

    ``before_request`` accepts the total reserved seconds (read plus five-second
    forced drain); it must acquire fresh fences and return with SQL closed.
    ``check`` fails after lost ownership or shutdown; it may not access SQL.
    Neither callback can be provided by an HTTP request or broker payload.
    """

    def __init__(self, *, before_request, check):
        """Require concrete owning hooks; an absent fence must not default to allow."""
        if not callable(before_request) or not callable(check):
            raise TypeError("Bounded source transport requires owning callbacks.")
        self.headers = requests.structures.CaseInsensitiveDict()
        self.before_request = before_request
        self.check = check

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
        """No persistent socket survives a call; remove the retained key reference."""
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
                }
            )
            payload = json.dumps(
                request, allow_nan=False, separators=(",", ":")
            ).encode()
            if len(payload) > MAX_REQUEST_BYTES:
                raise ValueError("Source request exceeds its byte bound.")
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("Invalid bounded source request.") from None
        self.check()
        self.before_request(math.ceil(timeout) + 5)
        self.check()
        output = _exchange(payload, seconds=timeout, check=self.check)
        status, separator, body = output.partition(b"\n")
        if not separator or not _valid_status(status):
            raise SourceTransportError("Source response status is invalid.")
        response = ExactSourceResponse()
        response.status_code = int(status)
        if 200 <= response.status_code < 300 and not body:
            raise SourceTransportError("Source response contains no JSON body.")
        response.url = url  # No query strings, request headers or original request.
        response.encoding = "utf-8"
        response._content = body if 200 <= response.status_code < 300 else b""
        response._content_consumed = True
        return response


def _valid_status(status):
    """Require an exact three-digit HTTP status, never arbitrary helper text."""
    return len(status) == 3 and status.isdigit() and 100 <= int(status) <= 599
