"""Private one-request process for bounded, read-only ParishSoft transport.

The parent owns fencing, retries and the wall-clock deadline. This process
has no SQL connection, configuration arguments or credential environment. Its
bounded stdin carries one request; stdout carries a status and bounded body.
It never follows redirects or emits provider/exception text to diagnostics.
"""

import json
import logging
import re
import sys

import requests

from parishkit.parishsoft import DEFAULT_API_BASE_URL

MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
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
    if type(value) is not dict or set(value) != {
        "method",
        "url",
        "parameters",
        "api_key",
        "timeout",
    }:
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


def perform(request):
    """Buffer at most one bounded decoded response, never a provider error body.

    Socket timeouts are additional protection; the parent can kill this process
    even during DNS resolution or a continually trickling HTTP response. A new
    Session has no inherited proxy/netrc, cookies or application request hooks.
    """
    request = validate_request(request)
    with requests.Session() as session:
        session.trust_env = False
        session.headers["x-api-key"] = request["api_key"]
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
                    raise ValueError("Source response exceeds its byte bound.")
                body.extend(chunk)
            if not body:
                raise ValueError("Source response has no JSON body.")
            return response.status_code, bytes(body)


def main():
    """Use only private pipes; even malformed input has a constant error response."""
    logging.disable(logging.CRITICAL)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("Source request exceeds its byte bound.")
        request = json.loads(raw)
        status, body = perform(request)
        sys.stdout.buffer.write(str(status).encode("ascii") + b"\n" + body)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # No traceback, original input, response, or request headers may escape.
        sys.stdout.buffer.write(b"ERROR\n")
        sys.stdout.buffer.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
