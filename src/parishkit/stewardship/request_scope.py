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

The middleware also finishes a response that a view built for a
configuration activation in progress (#429), marked ACTIVATION_HOLD: it adds
a short Retry-After and keeps Django from logging that expected 503 as a
server ERROR. Every other 503 is logged as before.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_VERIFIED = ContextVar("stewardship_verified_configuration", default=None)
# The response attribute a view sets on the 503 it answers for an activation.
ACTIVATION_HOLD = "stewardship_activation_hold"
# Seconds a client should wait before retrying during an activation.
ACTIVATION_RETRY_SECONDS = 2


@contextmanager
def request_scope():
    """Bind a fresh, empty memory for one request and always discard it after."""
    token = _VERIFIED.set({})
    try:
        yield
    finally:
        _VERIFIED.reset(token)


def verified_configurations():
    """The current request's memory, or None outside a request scope."""
    return _VERIFIED.get()


def mark_activation_response(response):
    """Finish a 503 a view answered for an activation as an expected wait.

    The page's poller already retries a 503; Retry-After says how soon. Only
    a response the view marked ACTIVATION_HOLD is marked as already logged
    (the view's wait logged its own WARNING): Django's request logger would
    otherwise record it at ERROR. Any other 503 keeps its ERROR line.
    """
    if response.status_code == 503 and getattr(response, ACTIVATION_HOLD, False):
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
