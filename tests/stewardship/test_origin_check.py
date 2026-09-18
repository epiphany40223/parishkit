"""The readiness adapter checks only the configured origin and fails closed."""

import io
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import origin_check, origin_check_worker
from parishkit.stewardship.deployment import DeploymentProfile


def test_fixed_helper_uses_bounded_noncredential_transport(monkeypatch):
    """Verify ParishKit's invocation contract, not subprocess's implementation."""

    def run(arguments, **options):
        """Observe the compiled command without invoking an external resolver."""
        assert arguments[1:] == [
            "-I",
            "-m",
            "parishkit.stewardship.origin_check_worker",
        ]
        assert options["timeout"] == 5 and options["env"] == {}
        assert json.loads(options["input"]) == {
            "origin": "https://parish.example.org",
            "profile": "production",
        }
        return SimpleNamespace(returncode=0, stdout=b"ready\n")

    monkeypatch.setattr(origin_check.subprocess, "run", run)
    assert origin_check.check_public_origin(
        "https://parish.example.org", DeploymentProfile.PRODUCTION
    )


@pytest.mark.parametrize("outcome", [b"unknown\n", b"ready\nextra", b"", None])
def test_unavailable_or_unrecognized_resolution_is_not_readiness(monkeypatch, outcome):
    """A timeout or unexpected helper output cannot grant destructive permission."""

    def run(*args, **kwargs):
        """Model only this adapter's external boundary outcome."""
        if outcome is None:
            raise subprocess.TimeoutExpired("fixed-helper", 5)
        return SimpleNamespace(returncode=0, stdout=outcome)

    monkeypatch.setattr(origin_check.subprocess, "run", run)
    assert not origin_check.check_public_origin(
        "https://parish.example.org", DeploymentProfile.PRODUCTION
    )


def test_dns_helper_resolves_only_validated_host_and_port(monkeypatch):
    """No HTTP request or arbitrary path is sent to the resolver."""

    def lookup(host, port, **options):
        """Record public DNS input without needing live external services."""
        assert (host, port, options) == (
            "parish.example.org",
            443,
            {"type": socket.SOCK_STREAM},
        )
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]

    monkeypatch.setattr(origin_check_worker.socket, "getaddrinfo", lookup)
    assert origin_check_worker.resolve(
        {"origin": "https://parish.example.org", "profile": "production"}
    )
    with pytest.raises(ConfigError):
        origin_check_worker.resolve(
            {"origin": "https://parish.example.org/private", "profile": "production"}
        )


def test_helper_scrubs_private_resolver_errors(monkeypatch):
    """Even an exception containing local resolver detail emits only the verdict."""

    def fail(*args, **kwargs):
        """Supply the failure text that must not cross this process protocol."""
        raise OSError("private resolver diagnostic")

    payload = json.dumps({"origin": "http://localhost", "profile": "test"}).encode()
    output = io.BytesIO()
    monkeypatch.setattr(origin_check_worker.socket, "getaddrinfo", fail)
    monkeypatch.setattr(
        origin_check_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload))
    )
    monkeypatch.setattr(
        origin_check_worker.sys, "stdout", SimpleNamespace(buffer=output)
    )
    origin_check_worker.main()
    assert output.getvalue() == b"unavailable\n"
