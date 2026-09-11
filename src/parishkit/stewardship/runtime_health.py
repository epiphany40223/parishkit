"""Internal readiness and bounded, non-identifying operational metrics.

No check contacts an external provider. Detailed check names belong to the
operator CLI; HTTP readiness exposes only success/unavailability. Metrics have
fixed labels and no parish, family, credential, request path or query values.
"""

import hmac
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic

from .health_probe import HealthProbe
from .runtime_paths import private_directory


@dataclass(frozen=True)
class RuntimeHealth:
    """Startup supplies admitted configuration, authority and authenticated Valkey."""

    configuration: object = field(repr=False)
    store: object = field(repr=False)
    client: object = field(repr=False)
    metrics_token: bytes = field(repr=False)
    _readiness: HealthProbe = field(
        default_factory=HealthProbe, repr=False, compare=False
    )
    _metrics: HealthProbe = field(
        default_factory=HealthProbe, repr=False, compare=False
    )

    def __post_init__(self):
        if (
            type(self.metrics_token) is not bytes
            or re.fullmatch(rb"[A-Za-z0-9_-]{43}", self.metrics_token) is None
        ):
            raise ValueError("Metrics requires its provisioned bearer credential.")

    def checks(self):
        """Read a short-lived bounded observation, never block all request threads."""
        unavailable = dict.fromkeys(
            ("database", "migrations", "configuration", "valkey", "private_storage"),
            False,
        )
        return dict(
            self._readiness.read(self._check_dependencies, unavailable=unavailable)
        )

    def _check_dependencies(self):
        """Probe each independent internal dependency; keep exception values private."""
        from django.db import connection, connections

        from .accounts.configuration_installation import coherent_configuration
        from .runtime_database import require_capacity, require_current_schema

        checks = {
            "database": lambda: require_capacity(self.configuration),
            "migrations": require_current_schema,
            "configuration": lambda: coherent_configuration(self.store),
            "valkey": self._valkey,
            "private_storage": self._storage,
        }
        result = {}
        try:
            # This is the probe thread's own connection, never a response guard's.
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='2s'")
                cursor.execute("SET lock_timeout='1s'")
            for name, probe in checks.items():
                try:
                    probe()
                except Exception:
                    result[name] = False
                else:
                    result[name] = True
        finally:
            connections.close_all()
        return result

    def _valkey(self):
        """The limiter's already authenticated connection must respond promptly."""
        if self.client.ping() is not True:
            raise ValueError("Valkey health is unavailable.")

    def _storage(self):
        """Overridden export/media roots retain owner-only permissions after startup."""
        for name in ("reports", "media"):
            private_directory(self.configuration.paths[name])

    def authorized_metrics(self, authorization):
        """Compare a bounded exact bearer value, never its hash or an input URL."""
        if type(authorization) is not str or not authorization.startswith("Bearer "):
            return False
        try:
            value = authorization[7:].encode("ascii")
        except UnicodeError:
            return False
        return len(value) == 43 and hmac.compare_digest(value, self.metrics_token)

    def metrics(self):
        """Bound the entire scrape, including disk, SQL and broker observations."""
        value = self._metrics.read(self._collect_metrics, unavailable=None)
        if value is None:
            raise ValueError("Metrics observation is unavailable.")
        return value

    def _collect_metrics(self):
        """Probe-owned SQL connections close even when a metric source fails."""
        from django.db import connection, connections

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='2s'")
                cursor.execute("SET lock_timeout='1s'")
            return self._metric_lines()
        finally:
            connections.close_all()

    def _metric_lines(self):
        """Expose finite foundational gauges; later owners add their own metrics."""
        from django.db import connection

        lines = [
            "# HELP stewardship_dependency_up Internal readiness dependency.",
            "# TYPE stewardship_dependency_up gauge",
        ]
        for name, ready in self.checks().items():
            lines.append(
                f'stewardship_dependency_up{{dependency="{name}"}} {int(ready)}'
            )
        lines += [
            "# HELP stewardship_storage_available_bytes Available private-store bytes.",
            "# TYPE stewardship_storage_available_bytes gauge",
        ]
        for name in ("reports", "media"):
            lines.append(
                f'stewardship_storage_available_bytes{{store="{name}"}} '
                f"{shutil.disk_usage(self.configuration.paths[name]).free}"
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"
            )
            connections = cursor.fetchone()[0]
        lines += [
            "# HELP stewardship_database_connections Connections to this database.",
            "# TYPE stewardship_database_connections gauge",
            f"stewardship_database_connections {connections}",
        ]
        lines += http_metric_lines(self.client)
        return "\n".join(lines) + "\n"


HTTP_KEY = "stewardship:ops:v1:http"
BUCKETS_MS = (50, 100, 250, 500, 1000, 5000)
HTTP_SCRIPT = """
redis.call('HINCRBY', KEYS[1], ARGV[1], 1)
redis.call('HINCRBY', KEYS[1], 'count', 1)
redis.call('HINCRBY', KEYS[1], 'milliseconds', ARGV[2])
for i=3,#ARGV do redis.call('HINCRBY', KEYS[1], ARGV[i], 1) end
return 1
"""


def record_http(client, status, seconds):
    """One atomic best-effort observation shared by web processes and replicas."""
    if type(status) is not int or not 100 <= status <= 599:
        raise ValueError("HTTP metric status is invalid.")
    milliseconds = max(0, min(3600000, round(seconds * 1000)))
    script = client.register_script(HTTP_SCRIPT)
    script(
        keys=[HTTP_KEY],
        args=[
            f"status_{status // 100}",
            milliseconds,
            *(f"le_{bound}" for bound in BUCKETS_MS if milliseconds <= bound),
        ],
    )


def http_metric_lines(client):
    """Untrusted/corrupt cache values cannot inject Prometheus labels or text."""
    raw = client.hgetall(HTTP_KEY)

    def count(name):
        """Accept only bounded nonnegative integer counters from the metric hash."""
        value = raw.get(name.encode(), raw.get(name, b"0"))
        if isinstance(value, str):
            value = value.encode("ascii")
        if type(value) is not bytes or re.fullmatch(rb"[0-9]{1,19}", value) is None:
            raise ValueError("HTTP metrics are unavailable.")
        return int(value)

    lines = [
        "# HELP stewardship_http_responses_total HTTP responses by status class.",
        "# TYPE stewardship_http_responses_total counter",
    ]
    for status in range(1, 6):
        lines.append(
            f'stewardship_http_responses_total{{class="{status}xx"}} '
            f"{count(f'status_{status}')}"
        )
    lines += [
        "# HELP stewardship_http_duration_seconds Request duration before streaming.",
        "# TYPE stewardship_http_duration_seconds histogram",
    ]
    for bound in BUCKETS_MS:
        lines.append(
            f'stewardship_http_duration_seconds_bucket{{le="{bound / 1000:g}"}} '
            f"{count(f'le_{bound}')}"
        )
    lines += [
        f'stewardship_http_duration_seconds_bucket{{le="+Inf"}} {count("count")}',
        f"stewardship_http_duration_seconds_count {count('count')}",
        f"stewardship_http_duration_seconds_sum {count('milliseconds') / 1000:g}",
    ]
    return lines


class HttpMetricsMiddleware:
    """Best-effort telemetry must not change request authentication or availability."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Internal health scrapes do not inflate user-request counters."""
        from django.conf import settings

        start = monotonic()
        response = self.get_response(request)
        runtime = getattr(settings, "STEWARDSHIP_HEALTH_RUNTIME", None)
        if isinstance(runtime, RuntimeHealth) and request.path_info not in {
            "/health/live",
            "/health/ready",
            "/metrics",
        }:
            # Independent health/admission owners report Valkey outages.
            with suppress(Exception):
                record_http(runtime.client, response.status_code, monotonic() - start)
        return response
