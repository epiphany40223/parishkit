"""Failure injection exercises operator diagnostics without touching real services."""

import json
import time


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
