"""Regression probes for a database CI gate that cannot pass by skipping work."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "profile,body,code,message,option",
    [
        ("test", "pass", 4, "not configured", "--require-postgresql-tests"),
        (
            "database_test",
            'pytest.skip("synthetic")',
            1,
            "verification was skipped",
            "--require-postgresql-tests",
        ),
        ("database_test", "pass", 0, "1 passed", "--require-postgresql-tests"),
        (
            "test",
            'pytest.skip("synthetic")',
            1,
            "verification was skipped",
            "--require-no-skips",
        ),
        ("test", "pass", 0, "1 passed", "--require-no-skips"),
    ],
)
def test_database_requirement_cannot_pass_without_execution(
    tmp_path, profile, body, code, message, option
):
    """Use actual pytest hooks in a disposable tree, without opening a database."""
    shutil.copyfile(ROOT / "tests/conftest.py", tmp_path / "conftest.py")
    directory = tmp_path / "stewardship/database"
    directory.mkdir(parents=True)
    (directory / "test_probe.py").write_text(
        f"import pytest\ndef test_probe():\n    {body}\n", encoding="utf-8"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_"))
        and key != "DJANGO_SETTINGS_MODULE"
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "stewardship/database",
            "-q",
            f"--ds=parishkit.stewardship.settings.{profile}",
            option,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert message in result.stdout + result.stderr


def test_ci_explicitly_requires_postgresql_verification():
    """A job-level environment edit must not silently remove the SQL test gate."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    commands = "\n".join(
        step.get("run", "")
        for step in workflow["jobs"]["stewardship-postgresql"]["steps"]
    )
    assert "parishkit.stewardship.quality --postgresql" in commands
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert (
        release["jobs"]["validate-build"]["services"]
        == workflow["jobs"]["stewardship-postgresql"]["services"]
    )


@pytest.mark.parametrize(
    "filename,job",
    [("ci.yml", "stewardship-compose"), ("release.yml", "validate-build")],
)
def test_ci_requires_every_operational_container_module(filename, job):
    """Runtime coverage cannot silently disappear from either required pipeline."""
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    step = next(
        item
        for item in workflow["jobs"][job]["steps"]
        if item.get("name") == "Validate operational runtime and provisioning"
    )
    assert step["env"]["PARISHKIT_RUN_RUNTIME_TESTS"] == "1"
    assert "--require-no-skips" in step["run"]
    for module in (
        "test_operational_compose",
        "test_database_provisioning_container",
        "test_runtime_provisioning_container",
        "test_runtime_ingress_container",
    ):
        assert f"tests/stewardship/{module}.py" in step["run"]
