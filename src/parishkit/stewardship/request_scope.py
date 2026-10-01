"""Per-request memory for already-verified, immutable configuration projections.

One Admin request crosses several short transactions (authentication, the
view's work transaction, the post-render re-authorization), and each used to
re-verify the complete selected configuration corpus: about ten queries apiece.
A configuration version's applied projections never change once written, so a
request may reuse the snapshot it verified earlier. Every call still re-reads
the live selection (the manifest, the file digest and the SystemConfiguration
row), so a concurrent activation or restore is seen exactly as before; only the
immutable corpus behind an unchanged selection is not re-verified.

The memory lives in a context variable bound for one web request by
``RequestScopeMiddleware``. It is never shared across requests or threads, and
outside a request (workers, installers, commands) nothing is remembered.

The scope also notes when a request saw a configuration activation in
progress (#429). Views answer that brief interval with their ordinary 503
"temporarily unavailable, retry" response; the middleware marks it with a
short Retry-After and keeps Django from logging it as a server ERROR.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_VERIFIED = ContextVar("stewardship_verified_configuration", default=None)
_ACTIVATING = ContextVar("stewardship_authority_changing", default=None)
# Seconds a client should wait before retrying during an activation.
ACTIVATION_RETRY_SECONDS = 2


@contextmanager
def request_scope():
    """Bind a fresh, empty memory for one request and always discard it after."""
    token = _VERIFIED.set({})
    activating = _ACTIVATING.set([False])
    try:
        yield
    finally:
        _ACTIVATING.reset(activating)
        _VERIFIED.reset(token)


def verified_configurations():
    """The current request's memory, or None outside a request scope."""
    return _VERIFIED.get()


def note_authority_changing():
    """Record that this request met a configuration activation in progress."""
    noted = _ACTIVATING.get()
    if noted is not None:
        noted[0] = True


def authority_changing_noted():
    """Whether this request met a configuration activation in progress."""
    noted = _ACTIVATING.get()
    return noted is not None and noted[0]


def mark_activation_response(response):
    """Answer a 503 met during an activation as a brief, expected wait.

    The page's poller already retries a 503; Retry-After says how soon. The
    activation is an ordinary configuration change, so the response is
    marked as already logged: Django's request logger would otherwise record
    every such 503 at ERROR.
    """
    if response.status_code == 503 and authority_changing_noted():
        if not response.has_header("Retry-After"):
            response["Retry-After"] = str(ACTIVATION_RETRY_SECONDS)
        response._has_been_logged = True
    return response


class RequestScopeMiddleware:
    """Scope the verified-configuration memory to exactly one request."""

    def __init__(self, get_response):
        """Retain the next synchronous Django handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Streaming bodies run after this returns, so they re-verify in full."""
        with request_scope():
            return mark_activation_response(self.get_response(request))
