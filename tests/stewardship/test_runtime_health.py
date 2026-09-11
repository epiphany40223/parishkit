"""Internal-only metrics/readiness keep credentials and diagnostic details private."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory

from parishkit.stewardship.runtime_health import (
    HTTP_KEY,
    HttpMetricsMiddleware,
    RuntimeHealth,
    http_metric_lines,
    record_http,
)


def health():
    """Use an obviously synthetic token and no database/provider dependency."""
    return RuntimeHealth(SimpleNamespace(), object(), Mock(), b"t" * 43)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "Bearer wrong",
        "Bearer " + "t" * 44,
        "Basic " + "t" * 43,
        "Bearer " + "☃" * 43,
        "Bearer " + "t" * 43 + "\n",
    ],
)
def test_metrics_rejects_nonexact_bearer_values(value):
    assert not health().authorized_metrics(value)
    assert health().authorized_metrics("Bearer " + "t" * 43)
    assert "t" * 43 not in repr(health())


def test_internal_ready_and_metrics_routes(settings, monkeypatch):
    """Both the ingress classifier and bearer comparison must admit a scrape."""
    runtime = health()
    settings.STEWARDSHIP_HEALTH_RUNTIME = runtime
    monkeypatch.setattr(RuntimeHealth, "checks", lambda self: {"database": True})
    monkeypatch.setattr(RuntimeHealth, "metrics", lambda self: "safe_gauge 1\n")
    client = Client()
    assert client.get("/health/ready").status_code == 200
    assert client.get("/metrics").status_code == 404
    headers = {"HTTP_AUTHORIZATION": "Bearer " + "t" * 43}
    response = client.get("/metrics", **headers)
    assert response.status_code == 200
    assert response.content == b"safe_gauge 1\n"
    assert "version=0.0.4" in response["Content-Type"]
    assert client.get("/metrics", REMOTE_ADDR="192.0.2.8", **headers).status_code == 404
    settings.STEWARDSHIP_TRUSTED_PROXY_NETWORKS = ("172.29.241.2/32",)
    settings.STEWARDSHIP_PROXY_HOPS = 1
    assert (
        client.get("/metrics", REMOTE_ADDR="172.29.241.2", **headers).status_code == 404
    )
    monkeypatch.setattr(RuntimeHealth, "checks", lambda self: {"database": False})
    assert client.get("/health/ready").content == b"unavailable\n"
    assert client.get("/health/live").status_code == 200


def test_metrics_exception_contains_no_private_error(settings, monkeypatch):
    settings.STEWARDSHIP_HEALTH_RUNTIME = health()

    def fail(self):
        raise RuntimeError("private-secret-data")

    monkeypatch.setattr(RuntimeHealth, "metrics", fail)
    response = Client().get("/metrics", HTTP_AUTHORIZATION="Bearer " + "t" * 43)
    assert response.status_code == 503
    assert response.content == b"unavailable\n"


def test_http_counters_use_only_bounded_closed_fields():
    """Paths, query strings and identities never become telemetry labels."""
    client = Mock()
    record_http(client, 201, 0.049)
    client.register_script.return_value.assert_called_once_with(
        keys=[HTTP_KEY],
        args=[
            "status_2",
            49,
            "le_50",
            "le_100",
            "le_250",
            "le_500",
            "le_1000",
            "le_5000",
        ],
    )
    client.hgetall.return_value = {
        b"count": b"2",
        b"status_2": b"2",
        b"milliseconds": b"70",
        b"private-label": b"private-value",
    }
    result = "\n".join(http_metric_lines(client))
    assert "private" not in result
    assert "stewardship_http_duration_seconds_sum 0.07" in result
    client.hgetall.return_value = {b"count": b"private\nlabel 1"}
    with pytest.raises(ValueError, match="unavailable"):
        http_metric_lines(client)


def test_metrics_failure_does_not_change_request_behavior(settings, monkeypatch):
    settings.STEWARDSHIP_HEALTH_RUNTIME = health()
    record = Mock(side_effect=RuntimeError("private"))
    monkeypatch.setattr("parishkit.stewardship.runtime_health.record_http", record)
    middleware = HttpMetricsMiddleware(lambda request: HttpResponse("ok"))
    assert middleware(RequestFactory().get("/private-path?q=secret")).status_code == 200
    assert record.call_count == 1
    for path in ("/health/live", "/health/ready", "/metrics"):
        assert middleware(RequestFactory().get(path)).status_code == 200
    assert record.call_count == 1
