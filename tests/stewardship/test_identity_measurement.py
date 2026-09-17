"""Failure diagnostics preserve identity budgets without exposing private SQL."""

import json

import pytest

from .database import test_identity_performance_postgresql as benchmark


def measurement(monkeypatch, durations, *, query_count=1):
    """Supply deterministic elapsed times and private fake query metadata."""
    ticks = iter(value for duration in durations for value in (0, duration))
    monkeypatch.setattr(benchmark, "perf_counter", lambda: next(ticks))
    samples = iter(durations)

    class Queries:
        """Model captured queries while retaining a sentinel that must stay private."""

        def __init__(self, connection):
            duration = next(samples)
            self.captured_queries = [
                {"time": str(duration / 2), "sql": "PRIVATE-CREDENTIAL-SENTINEL"}
                for _ in range(query_count)
            ]

        def __enter__(self):
            return self

        def __exit__(self, *error):
            return False

        def __len__(self):
            return len(self.captured_queries)

    monkeypatch.setattr(benchmark, "CaptureQueriesContext", Queries)


def operation():
    """The instrumented operation itself has no database or provider I/O."""


def test_timing_failure_reports_p95_and_outlier_without_private_sql(monkeypatch):
    measurement(monkeypatch, [0.1] * 18 + [2.1, 4.0])
    with pytest.raises(AssertionError) as error:
        benchmark._measure(operation)
    message = str(error.value)
    assert "PRIVATE" not in message
    evidence = json.loads(message.splitlines()[0])
    assert evidence["operation"] == "operation"
    assert evidence["sample_queries"] == [1] * 20
    assert evidence["p95_sample_queries"] == [{"ordinal": 1, "seconds": 1.05}]
    assert evidence["slowest_sample_queries"] == [{"ordinal": 1, "seconds": 2.0}]


def test_query_budget_failure_also_has_private_value_free_evidence(monkeypatch):
    measurement(monkeypatch, [0.1] * 20, query_count=65)
    with pytest.raises(AssertionError) as error:
        benchmark._measure(operation)
    message = str(error.value)
    assert "PRIVATE" not in message
    assert json.loads(message.splitlines()[0])["sample_queries"] == [65] * 20


def test_success_retains_exact_percentile_and_query_count(monkeypatch):
    measurement(monkeypatch, [0.1] * 18 + [1.999, 4.0], query_count=64)
    assert benchmark._measure(operation) == {
        "queries_max": 64,
        "p95_seconds": 1.999,
    }


def test_two_second_boundary_still_fails(monkeypatch):
    measurement(monkeypatch, [0.1] * 18 + [2.0, 2.0])
    with pytest.raises(AssertionError):
        benchmark._measure(operation)
