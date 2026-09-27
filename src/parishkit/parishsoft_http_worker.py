"""Private persistent process for bounded, read-only ParishSoft transport.

The parent owns fencing, retries and each request's wall-clock deadline. This
process has no SQL connection, configuration arguments or credential
environment. One helper serves one parent source session: its first stdin line
carries the API key, and every later line carries one key-free request. Each
reply is one frame (see ``main``) with a status and a bounded body. A single
``requests.Session`` keeps TLS connections alive between requests. The helper
never follows redirects or emits provider/exception text to diagnostics, and
it exits when stdin closes or its parent process disappears.
"""

import json
import logging
import os
import re
import sys
import threading
import time

import requests

from parishkit.parishsoft import DEFAULT_API_BASE_URL

MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_FIELDS = frozenset({"method", "url", "parameters", "timeout"})


class InvalidSourceResponse(ValueError):
    """A deterministic body-contract failure must not be retried as an outage."""


READ_POSTS = frozenset(
    {
        "organizations/search",
        "families/search",
        "members/search",
        "members/contact/list",
    }
)
READ_GETS = re.compile(
    r"(?:families/(?:change/list|group/lookup/list|workgroup/list|"
    r"workgroup/[1-9][0-9]*/list|[1-9][0-9]*(?:/member/list)?)|"
    r"members/(?:workgroup/(?:lookup/list|[1-9][0-9]*/list)|[1-9][0-9]*)|"
    r"ministry/(?:type/list|[1-9][0-9]*/minister/list)|"
    r"offering/(?:[1-9][0-9]*/funds|pledge/list|contributiondetail/list))"
)


def validate_request(value):
    """Reject arbitrary origins/methods, redirects, large input and extra options."""
    if type(value) is not dict or set(value) != REQUEST_FIELDS | {"api_key"}:
        raise ValueError("Invalid bounded source request.")
    method, url = value["method"], value["url"]
    prefix = DEFAULT_API_BASE_URL + "/"
    if type(url) is not str or not url.startswith(prefix):
        raise ValueError("Invalid bounded source endpoint.")
    endpoint = url[len(prefix) :]
    if not (
        (method == "POST" and endpoint in READ_POSTS)
        or (method == "GET" and READ_GETS.fullmatch(endpoint))
    ):
        raise ValueError("Unsupported bounded source operation.")
    key = value["api_key"]
    timeout = value["timeout"]
    if (
        type(key) is not str
        or not 1 <= len(key) <= 4096
        or any(ord(char) < 33 or ord(char) > 126 for char in key)
        or type(value["parameters"]) is not dict
        or type(timeout) not in (int, float)
        or not 0 < timeout <= 240
    ):
        raise ValueError("Invalid bounded source options.")
    return value


def new_session(api_key):
    """Build the one keep-alive Session with no environment-derived authority.

    ``trust_env = False`` excludes proxy variables and netrc credentials; the
    Session starts with no cookies or application request hooks.
    """
    session = requests.Session()
    session.trust_env = False
    session.headers["x-api-key"] = api_key
    return session


def perform(session, request):
    """Buffer at most one bounded decoded response, never a provider error body.

    Socket timeouts are additional protection; the parent can kill this process
    even during DNS resolution or a continually trickling HTTP response. Fully
    reading a success body returns its connection to the Session's keep-alive
    pool; an unread error body closes that connection instead.
    """
    request = validate_request(request)
    options = {
        "params" if request["method"] == "GET" else "json": request["parameters"]
    }
    with session.request(
        request["method"],
        request["url"],
        timeout=request["timeout"],
        stream=True,
        allow_redirects=False,
        **options,
    ) as response:
        if not 200 <= response.status_code < 300:
            return response.status_code, b""
        body = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise InvalidSourceResponse("Source response exceeds its byte bound.")
            body.extend(chunk)
        if not body:
            raise InvalidSourceResponse("Source response has no JSON body.")
        return response.status_code, bytes(body)


def read_line(stream):
    """Return one bounded newline-terminated JSON object, or None at EOF."""
    raw = stream.readline(MAX_REQUEST_BYTES + 1)
    if not raw:
        return None
    if not raw.endswith(b"\n"):
        raise ValueError("Source request frame is incomplete or too large.")
    value = json.loads(raw)
    if type(value) is not dict:
        raise ValueError("Invalid bounded source request.")
    return value


def watch_parent(parent, interval=0.5):
    """Exit immediately if the parent dies, even while an HTTP read is blocked.

    An orphan is re-parented, so a changed parent PID means no owner can still
    enforce this helper's deadline. EOF on stdin covers an idle helper; this
    thread covers a helper blocked in DNS, connect or a trickling response.
    """
    while True:
        if os.getppid() != parent:
            os._exit(1)
        time.sleep(interval)


def serve(stdin, stdout):
    """Answer requests until stdin closes; any failure ends this helper.

    Reply frames are ``b"<status> <length>\\n" + body`` for an HTTP response,
    ``b"INVALID\\n"`` for a deterministic body-contract failure, or
    ``b"ERROR\\n"`` for anything else. After INVALID or ERROR the helper exits,
    so the parent always replaces it rather than reusing uncertain state.
    """
    try:
        first = read_line(stdin)
        if first is None:
            return 0
        if set(first) != {"api_key"}:
            raise ValueError("Invalid bounded source key frame.")
        key = first["api_key"]
        with new_session(key) as session:
            while (frame := read_line(stdin)) is not None:
                if set(frame) != REQUEST_FIELDS:
                    raise ValueError("Invalid bounded source request.")
                status, body = perform(session, frame | {"api_key": key})
                stdout.write(b"%d %d\n" % (status, len(body)) + body)
                stdout.flush()
        return 0
    except InvalidSourceResponse:
        stdout.write(b"INVALID\n")
        stdout.flush()
        return 2
    except Exception:
        # No traceback, original input, response, or request headers may escape.
        stdout.write(b"ERROR\n")
        stdout.flush()
        return 1


def main():
    """Use only private pipes; even malformed input has a constant error response."""
    logging.disable(logging.CRITICAL)
    threading.Thread(target=watch_parent, args=(os.getppid(),), daemon=True).start()
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    except Exception:
        # A vanished parent pipe leaves nobody to answer; exit without output.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
