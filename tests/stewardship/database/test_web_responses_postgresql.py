"""Real PostgreSQL and transport checks for HTTP response-lifetime ownership."""

import socket
from threading import Thread

import pytest
from django.db import connection, connections
from django.test import RequestFactory

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import (
    DownloadPool,
    ReadLimits,
    ReadUnavailable,
)
from parishkit.stewardship.web.responses import campaign_response

from .campaign_builders import draft_campaign

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def channel():
    """Server metadata contains a genuine transport, never an HTTP header."""
    server, client = socket.socketpair()
    request = RequestFactory().get("/admin/report/")
    request.META["gunicorn.socket"] = server
    try:
        yield request, server, client
    finally:
        server.close()
        client.close()


@pytest.mark.parametrize("download", [False, True])
def test_http_owns_guard_through_lazy_bytes_and_close(
    tmp_path, channel, download, settings
):
    """Lazy queries use the response's read-only connection until WSGI closes."""
    _, campaign, _ = draft_campaign(tmp_path)
    observed = []
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool()

    def content():
        """A later iteration must still own the same live guarded connection."""
        observed.append(connection.connection)
        yield Campaign.objects.get(pk=campaign.pk).state.encode()
        observed.append(connection.connection)
        yield b"done"

    response = campaign_response(
        channel[0],
        [campaign.pk],
        authorize=lambda _: None,
        open_content=content,
        **({"filename": "report.csv", "content_type": "text/csv"} if download else {}),
    )
    original = connections["default"]
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert b"".join(response.streaming_content) == b"draftdone"
    assert observed[0] is observed[1]
    response.close()
    assert response.closed
    assert not connection.in_atomic_block
    if download:
        assert original.connection is None


def test_missing_server_transport_fails_before_private_data(tmp_path):
    """Unsupported servers cannot silently buffer around deadline enforcement."""
    _, campaign, _ = draft_campaign(tmp_path)
    request = RequestFactory().get("/", HTTP_GUNICORN_SOCKET="forged")
    response = campaign_response(
        request,
        [campaign.pk],
        authorize=lambda _: pytest.fail("authorized"),
        open_content=lambda: pytest.fail("opened"),
    )
    assert response.status_code == 503
    assert response["Retry-After"] == "5"


def test_capacity_failure_precedes_content_and_headers(tmp_path, channel):
    """A saturated process returns a normal retry response, not a partial file."""
    _, campaign, _ = draft_campaign(tmp_path)
    pool = DownloadPool(ReadLimits(process_pool_size=1))
    pool.acquire()
    try:
        response = campaign_response(
            channel[0],
            [campaign.pk],
            authorize=lambda _: pytest.fail("authorized"),
            open_content=lambda: pytest.fail("opened"),
            filename="report.csv",
            content_type="text/csv",
            pool=pool,
        )
        assert response.status_code == 503
        assert "Content-Disposition" not in response
    finally:
        pool.release()


def test_disconnect_closes_without_consuming_content(tmp_path, channel):
    """The WSGI close hook releases the database even without a first iteration."""
    _, campaign, _ = draft_campaign(tmp_path)
    terminal = []
    response = campaign_response(
        channel[0],
        [campaign.pk],
        authorize=lambda _: None,
        open_content=lambda: iter([b"private"]),
        on_close=terminal.append,
    )
    assert connection.in_atomic_block
    response.close()
    response.close()
    assert not connection.in_atomic_block
    assert list(response.streaming_content) == []
    assert terminal == [False]


def test_timeout_interrupts_real_socket_before_releasing_download(tmp_path, channel):
    """A timer-thread expiry closes the channel and cannot emit another chunk."""
    _, campaign, _ = draft_campaign(tmp_path)
    pool = DownloadPool(ReadLimits(process_pool_size=1))
    terminal = []
    response = campaign_response(
        channel[0],
        [campaign.pk],
        authorize=lambda _: None,
        open_content=lambda: iter([b"private"]),
        filename="report.csv",
        content_type="text/csv",
        pool=pool,
        on_close=terminal.append,
    )
    content = response._iterator
    thread = Thread(target=content.guard._expire)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    channel[2].settimeout(1)
    assert channel[2].recv(1) == b""
    assert channel[1].fileno() >= 0  # The WSGI server still owns descriptor cleanup.
    assert list(response.streaming_content) == []
    response.close()
    assert terminal == [False]
    pool.acquire()
    pool.release()


def test_source_failure_releases_response(tmp_path, channel):
    """Failed lazy serialization cannot retain the guarded transaction."""
    _, campaign, _ = draft_campaign(tmp_path)
    terminal = []

    def broken():
        """A synthetic serializer failure occurs only when the server consumes."""
        yield b"first"
        raise ValueError("synthetic")

    response = campaign_response(
        channel[0],
        [campaign.pk],
        authorize=lambda _: None,
        open_content=broken,
        on_close=terminal.append,
    )
    stream = response.streaming_content
    assert next(stream) == b"first"
    with pytest.raises(ValueError, match="synthetic"):
        next(stream)
    response.close()
    assert not connection.in_atomic_block
    assert terminal == [False]


def test_close_failure_still_records_one_failed_terminal_outcome(
    tmp_path, channel, monkeypatch
):
    """Django suppresses closer exceptions, so final audit must run in finally."""
    _, campaign, _ = draft_campaign(tmp_path)
    terminal = []
    response = campaign_response(
        channel[0],
        [campaign.pk],
        authorize=lambda _: None,
        open_content=lambda: iter([b"private"]),
        on_close=terminal.append,
    )
    content = response._iterator
    original = content.response.close

    def failed_close():
        """Release the genuine SQL guard, then simulate a teardown failure."""
        original()
        raise RuntimeError("synthetic teardown failure")

    monkeypatch.setattr(content.response, "close", failed_close)
    response.close()
    response.close()
    assert terminal == [False]
    assert not connection.in_atomic_block


def test_guard_authorization_failure_returns_no_private_bytes(tmp_path, channel):
    """Fresh rejection before guard entry is a retryable nonstreaming response."""
    _, campaign, _ = draft_campaign(tmp_path)

    def refuse(_):
        raise ReadUnavailable("Synthetic revoked admission")

    response = campaign_response(
        channel[0],
        [campaign.pk],
        authorize=refuse,
        open_content=lambda: pytest.fail("opened private data"),
    )
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert not response.streaming and not connection.in_atomic_block
