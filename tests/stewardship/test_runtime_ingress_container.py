"""Explicit opt-in stock-image validation; never contact ACME or real providers."""

import json
import os
import subprocess

import pytest

from parishkit.stewardship.runtime_ingress import render_caddy
from parishkit.stewardship.runtime_topology import CADDY_IMAGE

from .test_runtime_topology import configuration_at

pytestmark = pytest.mark.skipif(
    os.environ.get("PARISHKIT_RUN_RUNTIME_TESTS") != "1",
    reason="Requires explicitly opted-in disposable Docker runtime validation",
)


def test_stock_caddy_accepts_real_generated_configuration(tmp_path):
    """Adapt offline using the exact release digest and inspect resulting handlers."""
    configuration = configuration_at(tmp_path, production=True)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_BIND_SERVICE",
            "--security-opt",
            "no-new-privileges:true",
            CADDY_IMAGE,
            "caddy",
            "adapt",
            "--config",
            "-",
            "--adapter",
            "caddyfile",
        ],
        input=render_caddy(configuration),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    adapted = json.loads(result.stdout)
    assert adapted["admin"]["disabled"] is True
    servers = adapted["apps"]["http"]["servers"]
    assert len(servers) == 1
    server = next(iter(servers.values()))
    assert server["listen"] == [":8443"]
    assert server["protocols"] == ["h1", "h2"]
    assert server["read_header_timeout"] == 10_000_000_000
    assert server["write_timeout"] == 380_000_000_000
    for logger in adapted["logging"]["logs"].values():
        encoder = logger["encoder"]
        assert encoder["format"] == "filter"
        assert encoder["fields"]["request"]["filter"] == "delete"
        assert encoder["fields"]["resp_headers"]["filter"] == "delete"
    serialized = json.dumps(adapted)
    assert "172.29.241.10:8000" in serialized
    assert "health_checks" not in serialized
    assert "acme-v02.api.letsencrypt.org" in serialized
