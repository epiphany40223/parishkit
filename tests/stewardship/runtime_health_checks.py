"""Failure injection exercises operator diagnostics without touching real services."""

import json
import subprocess
import time


def wait_for_consumer_cohort(file, project, probe, configuration, run):
    """A detached restart is not evidence that every new worker has finished booting."""
    deadline = time.monotonic() + 45
    while True:
        result = run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "python",
            "-c",
            probe,
            str(configuration),
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(
                "Synthetic worker cohort did not become ready: "
                + result.stdout
                + result.stderr
            )
        time.sleep(0.5)


def check_dependency_failure(file, project, web_config, run):
    """A stopped broker changes readiness, not process liveness or public detail."""
    run(file, project, "stop", "--timeout", "10", "valkey")
    try:
        result = run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "pk-stewardship",
            "health",
            "--config",
            str(web_config),
            check=False,
        )
        assert result.returncode == 1
        value = json.loads(result.stdout)
        assert value["ready"] is False
        assert value["checks"]["valkey"] is False
        assert all(type(item) is bool for item in value["checks"].values())
        # The worker remains alive so that useful unavailable/denial pages can
        # still be served; a business outage is not a reason to kill the process.
        run(file, project, "exec", "-T", "web", "pk-stewardship", "healthcheck")
        assert "Traceback" not in result.stderr
    finally:
        run(file, project, "up", "--detach", "valkey")
    deadline = time.monotonic() + 30
    while True:
        result = run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "pk-stewardship",
            "health",
            "--config",
            str(web_config),
            check=False,
        )
        if result.returncode == 0:
            assert json.loads(result.stdout)["ready"] is True
            return
        if time.monotonic() >= deadline:
            raise AssertionError("Synthetic broker recovery did not restore readiness")
        time.sleep(0.5)


def check_web_crash_recovery(file, project, run):
    """Kill only the synthetic web supervisor and prove Docker restarts the service."""
    container = run(file, project, "ps", "--quiet", "web").stdout.strip()
    assert container and len(container.splitlines()) == 1

    def restart_count():
        """Inspect only the exact container resolved from this UUID Compose project."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.RestartCount}}", container],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return int(result.stdout)

    before = restart_count()
    run(
        file,
        project,
        "exec",
        "-T",
        "web",
        "python",
        "-c",
        "import os, signal; "
        "from parishkit.stewardship.consumer_runtime import PIDFILE; "
        "os.kill(int(PIDFILE.read_text()), signal.SIGKILL)",
        check=False,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        health = run(
            file,
            project,
            "exec",
            "-T",
            "web",
            "pk-stewardship",
            "healthcheck",
            check=False,
        )
        if restart_count() > before and health.returncode == 0:
            return
        time.sleep(0.5)
    raise AssertionError(
        "Production restart policy did not recover the synthetic web service"
    )
