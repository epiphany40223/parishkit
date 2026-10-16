"""Private persistent process for bounded ParishSoft transport.

The parent owns fencing, retries and each request's wall-clock deadline. This
process has no SQL connection, configuration arguments or credential
environment. One helper serves one parent source session: its first stdin line
carries the API key, and every later line carries one key-free request. Each
reply is one frame (see ``main``) with a status and a bounded body. A single
``requests.Session`` keeps TLS connections alive between requests. The helper
never follows redirects or emits provider/exception text to diagnostics, and
it exits when stdin closes or its parent process disappears.

It is read-only unless its key frame also carries ``"contact_writes": true``,
which only the census publication session sends (#528, BG-09). Such a
helper additionally admits the one Member contact PUT, but never in a
``local`` request; every other helper refuses any PUT frame, whatever its
parent asks.
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
# Every request names the deployment profile it was made under and the one
# ParishSoft base URL its deployment chose. The helper starts with an empty
# environment and cannot learn either itself; the two fields decide the one
# base URL the allowlist admits.
REQUEST_FIELDS = frozenset(
    {"method", "url", "parameters", "timeout", "profile", "base_url"}
)
# The profiles a request may name (the stewardship DeploymentProfile values; a
# test keeps the two aligned). This shared module owns the base-URL rule so
# the helper stays independent of the application's deployment code.
PROFILES = frozenset({"development", "test", "production", "local"})
LOCAL_PROFILE = "local"
LOCAL_SOURCE_BASE_URL = "http://fake-parishsoft:8080/api/v2"
# The only two ParishSoft base URLs a request may name (#1399): the real API,
# in every profile, and the local laptop environment's fake, only in a
# ``local`` request. The deployment's ``parishsoft_base_url`` setting picks
# one; ``default_base_url`` is the choice when it is unset.
BASE_URLS = frozenset({DEFAULT_API_BASE_URL, LOCAL_SOURCE_BASE_URL})


def _profile(profile):
    """Return ``profile`` if it is one of the helper's profile names."""
    if type(profile) is not str or profile not in PROFILES:
        raise ValueError("Invalid bounded source profile.")
    return profile


def default_base_url(profile):
    """The base URL a profile reaches when its deployment names none.

    The fake in LOCAL and the real API in every other profile, which is what
    each profile reached before the setting existed.
    """
    if _profile(profile) == LOCAL_PROFILE:
        return LOCAL_SOURCE_BASE_URL
    return DEFAULT_API_BASE_URL


def admitted_base_url(profile, base_url):
    """The one base URL a request made under ``profile`` may contact.

    ``base_url`` is the deployment's choice. It must be one of the two
    ``BASE_URLS``, and the fake is admitted only for a ``local`` request, so
    a Production helper refuses it even if a parent passed it.
    """
    if (
        type(base_url) is not str
        or base_url not in BASE_URLS
        or (base_url == LOCAL_SOURCE_BASE_URL and _profile(profile) != LOCAL_PROFILE)
    ):
        raise ValueError("Invalid bounded source base URL.")
    _profile(profile)
    return base_url


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
# The one write (#528, BG-09): ParishSoft's v2 Member contact PUT. The Family
# contact PUT stays refused until a verified Family address read exists.
CONTACT_WRITES = re.compile(r"members/[1-9][0-9]{0,17}/contact")


def validate_request(value, *, contact_writes=False):
    """Reject arbitrary origins/methods, redirects, large input and extra options.

    ``contact_writes`` admits the Member contact PUT too, except in a
    ``local`` request; only a helper started for census publication passes
    it.
    """
    if type(value) is not dict or set(value) != REQUEST_FIELDS | {"api_key"}:
        raise ValueError("Invalid bounded source request.")
    method, url = value["method"], value["url"]
    prefix = admitted_base_url(value["profile"], value["base_url"]) + "/"
    if type(url) is not str or not url.startswith(prefix):
        raise ValueError("Invalid bounded source endpoint.")
    endpoint = url[len(prefix) :]
    if not (
        (method == "POST" and endpoint in READ_POSTS)
        or (method == "GET" and READ_GETS.fullmatch(endpoint))
        or (
            # A LOCAL deployment may read the real ParishSoft (#1399) but
            # never writes to it, whatever its parent asks.
            contact_writes is True
            and value["profile"] != LOCAL_PROFILE
            and method == "PUT"
            and CONTACT_WRITES.fullmatch(endpoint)
        )
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


def perform(session, request, *, contact_writes=False):
    """Buffer at most one bounded decoded response, never a provider error body.

    A successful PUT may answer with no body; every read must have one.

    Socket timeouts are additional protection; the parent can kill this process
    even during DNS resolution or a continually trickling HTTP response. Fully
    reading a success body returns its connection to the Session's keep-alive
    pool; an unread error body closes that connection instead.
    """
    request = validate_request(request, contact_writes=contact_writes)
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
        if not body and request["method"] != "PUT":
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
        if set(first) not in ({"api_key"}, {"api_key", "contact_writes"}) or (
            first.get("contact_writes", True) is not True
        ):
            raise ValueError("Invalid bounded source key frame.")
        key = first["api_key"]
        writes = "contact_writes" in first
        with new_session(key) as session:
            while (frame := read_line(stdin)) is not None:
                if set(frame) != REQUEST_FIELDS:
                    raise ValueError("Invalid bounded source request.")
                status, body = perform(
                    session, frame | {"api_key": key}, contact_writes=writes
                )
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
