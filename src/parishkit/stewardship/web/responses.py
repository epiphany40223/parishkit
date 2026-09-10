"""WSGI response-lifetime adapters for the campaign storage read barriers.

These synchronous adapters must be entered, consumed and closed on one WSGI
thread. Authentication/session activity and report audit writes precede entry;
later middleware must not write through the response-owned read-only connection.
No fallback silently converts a guarded response to ASGI or a buffered iterator.
"""

import socket
from contextlib import suppress
from threading import Event, Lock

from django.http import StreamingHttpResponse

from parishkit.stewardship.campaigns.read_guards import (
    CampaignReadGuard,
    DownloadPool,
    GuardedResponse,
    ReadUnavailable,
)

from .exports import download_headers
from .security import private_response

DOWNLOAD_POOL = DownloadPool()


def unavailable():
    """Use a stable, accessible retry response without exposing internal reasons."""
    response = private_response(
        "This information is temporarily unavailable. Please retry.\n", status=503
    )
    response["Retry-After"] = "5"
    return response


def _socket_abort(request):
    """Use the server-owned socket, never a header or browser-supplied callback."""
    channel = request.META.get("gunicorn.socket")
    if not isinstance(channel, socket.socket):
        raise ReadUnavailable("This server cannot enforce guarded response deadlines.")

    def abort():
        """Interrupt blocked socket writes before returning capacity to the pool."""
        try:
            channel.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Already-disconnected transports cannot emit further bytes.
            pass
        finally:
            channel.close()

    return abort


class _Content:
    """Serialize producer steps with timeout cancellation without thread handoff."""

    def __init__(self, request, campaigns, authorize, open_content, pool):
        self.stopped, self.producing = Event(), Lock()
        self.transport_abort = _socket_abort(request)
        self.guard = CampaignReadGuard(
            campaigns, authorize=authorize, abort=self.abort, pool=pool
        )
        with self.producing:
            self.response = GuardedResponse(self.guard, open_content)

    def __iter__(self):
        return self

    def __next__(self):
        """A cancelled producer may not start again, even after a lock is released."""
        with self.producing:
            if self.stopped.is_set():
                self.close()
                raise StopIteration
            return next(self.response)

    def abort(self):
        """Prove both transport and producer stopped before claiming cancellation.

        Cancellation of SQL precedes waiting for the producer so a blocked query
        can unwind. A misbehaving CPU producer cannot be killed safely: failure
        leaves local capacity reserved until its owning thread finally closes.
        """
        from psycopg import Error

        self.stopped.set()
        self.transport_abort()
        if self.guard._raw is not None:
            with suppress(Error):
                self.guard._raw.cancel()
        if not self.producing.acquire(timeout=1):
            raise ReadUnavailable("The response producer has not stopped.")
        self.producing.release()

    def close(self):
        """WSGI close/disconnect calls this on the owner; repeated calls are safe."""
        self.stopped.set()
        self.response.close()


def campaign_response(
    request,
    campaigns,
    *,
    authorize,
    open_content,
    filename=None,
    content_type="text/html; charset=utf-8",
    pool=DOWNLOAD_POOL,
):
    """Authorize before headers; keep guard through bytes and transport disconnect.

    ``open_content`` must open files/query data only when called under this guard.
    Filename selects bounded download admission; HTML uses the interactive class.
    Missing transport support fails before querying or opening private content.
    """
    headers = (
        download_headers(filename, content_type=content_type)
        if filename is not None
        else {"Cache-Control": "no-store", "Content-Type": content_type}
    )
    try:
        content = _Content(
            request,
            campaigns,
            authorize,
            open_content,
            pool if filename is not None else None,
        )
    except ReadUnavailable:
        return unavailable()
    return StreamingHttpResponse(content, headers=headers)
