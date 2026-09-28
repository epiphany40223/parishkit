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
"""

from contextlib import contextmanager
from contextvars import ContextVar

_VERIFIED = ContextVar("stewardship_verified_configuration", default=None)


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


class RequestScopeMiddleware:
    """Scope the verified-configuration memory to exactly one request."""

    def __init__(self, get_response):
        """Retain the next synchronous Django handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Streaming bodies run after this returns, so they re-verify in full."""
        with request_scope():
            return self.get_response(request)
