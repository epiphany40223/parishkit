"""Infrastructure-free regressions for the third identity review."""

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.test import RequestFactory, override_settings

from parishkit.config import ConfigError
from parishkit.stewardship import service_boundaries
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.service_boundaries import Mount, _kernel_pseudo_mount
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.security import (
    FORWARDED,
    browser_settings,
    resolve_client,
)

from .test_container_isolation import _cleanup_probe


@pytest.mark.parametrize(
    "prefix", ["\ufeff \ufeff", "\u200b", "\u200e", "\x00", "\x00 \ufeff\t"]
)
@pytest.mark.parametrize("formula", ["=1", "+1", "-1", "@SUM(1)"])
def test_ignorable_prefixes_cannot_hide_spreadsheet_formulas(prefix, formula):
    """Mixed control, separator and whitespace prefixes retain safe quoting."""
    assert csv_cell(prefix + formula) == "'" + prefix + formula
    assert csv_cell(prefix + "ordinary") == prefix + "ordinary"


@pytest.mark.parametrize(
    "origin,host",
    [("http://[::1]:8000", "[::1]:8000"), ("https://[2001:db8::1]", "[2001:db8::1]")],
)
def test_ipv6_browser_hosts_keep_django_brackets(origin, host):
    """Valid IPv6 origins are usable through Django's actual Host validation."""
    profile = browser_settings(
        SimpleNamespace(profile=DeploymentProfile.DEVELOPMENT, public_origin=origin)
    )
    with override_settings(**profile):
        assert RequestFactory().get("/", HTTP_HOST=host).get_host() == host


@pytest.mark.parametrize("header", FORWARDED)
def test_untrusted_identity_headers_are_removed(header):
    """No enumerated alternate identity header survives for later middleware."""
    request = RequestFactory().get(
        "/", REMOTE_ADDR="192.0.2.1", **{header: "private-spoof"}
    )
    resolve_client(request)
    assert header not in request.META and str(request.client_address) == "192.0.2.1"
    assert not request.internal_request


@override_settings(
    STEWARDSHIP_PROXY_HOPS=1,
    STEWARDSHIP_TRUSTED_PROXY_NETWORKS=("10.0.0.0/24",),
    STEWARDSHIP_INTERNAL_NETWORKS=("10.0.0.0/24",),
)
def test_known_ingress_peer_without_headers_is_never_internal():
    """Even an overlapping network cannot turn public-proxy traffic internal."""
    request = RequestFactory().get("/metrics", REMOTE_ADDR="10.0.0.2")
    resolve_client(request)
    assert not request.internal_request


@pytest.mark.parametrize(
    "mount",
    [
        Mount(Path("/proc"), False, "proc", "/", "proc"),
        Mount(Path("/dev/pts"), False, "devpts", "/", "devpts"),
        Mount(Path("/sys/fs/cgroup"), True, "cgroup2", "/", "cgroup"),
        Mount(Path("/dev/shm"), False, "tmpfs", "/", "shm"),
        Mount(Path("/proc/sys"), True, "proc", "/sys", "proc"),
        Mount(Path("/proc/kcore"), False, "tmpfs", "/null", "tmpfs"),
        Mount(Path("/sys/firmware"), True, "tmpfs", "/", "tmpfs"),
        Mount(
            Path("/sys/devices/system/cpu/cpu12/thermal_throttle"),
            True,
            "tmpfs",
            "/",
            "tmpfs",
        ),
    ],
)
def test_kernel_mount_groups_have_exact_positive_and_negative_cases(mount):
    """Every group rejects altered filesystem, source and mount-root metadata."""
    assert _kernel_pseudo_mount(mount)
    for change in (
        {"filesystem": "foreign"},
        {"source": "foreign"},
        {"root": "/foreign"},
    ):
        assert not _kernel_pseudo_mount(replace(mount, **change))
    if str(mount.target) in {
        "/proc/sys",
        "/sys/firmware",
        "/sys/devices/system/cpu/cpu12/thermal_throttle",
    }:
        assert not _kernel_pseudo_mount(replace(mount, read_only=False))


def test_online_root_refused_before_mount_access(monkeypatch):
    """Root admission does not depend on a Docker opt-in or local host identity."""
    monkeypatch.setattr(service_boundaries.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        service_boundaries, "kernel_mounts", lambda: pytest.fail("read mounts")
    )
    with pytest.raises(ConfigError, match="root"):
        service_boundaries.admit_online_service(None)


@pytest.mark.parametrize("body_failed", [False, True])
def test_disposable_cleanup_preserves_existing_failure(monkeypatch, body_failed):
    """Cleanup still runs all three actions, but never masks the original failure."""
    runner = Mock(side_effect=subprocess.TimeoutExpired("synthetic", 15))
    monkeypatch.setattr(subprocess, "run", runner)
    if body_failed:
        with pytest.warns(UserWarning, match="cleanup failed"):
            _cleanup_probe("synthetic", "synthetic-volume", body_failed=True)
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            _cleanup_probe("synthetic", "synthetic-volume", body_failed=False)
    assert runner.call_count == 3
