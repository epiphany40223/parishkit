"""Regression probes for a database CI gate that cannot pass by skipping work."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from parishkit.stewardship import quality_ci

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
    shards = workflow["jobs"]["stewardship-postgresql-shard"]
    gate = workflow["jobs"]["stewardship-postgresql"]
    # Packed jobs together run every partition exactly once (#625).
    indexes = [
        index
        for job in shards["strategy"]["matrix"]["job"]
        for index in quality_ci.job_partitions(job, 14)
    ]
    count = len(indexes)
    assert count == 14 and indexes == list(range(1, count + 1))
    assert f"parishkit.stewardship.quality_ci combine --count {count} " in commands
    assert shards["strategy"]["fail-fast"] is False
    assert gate["needs"] == ["validate", "stewardship-postgresql-shard"]
    assert gate["if"] == "${{ always() && github.event_name != 'push' }}"
    assert gate["steps"][0]["env"] == {
        "SHARD_RESULT": "${{ needs.stewardship-postgresql-shard.result }}",
        "VALIDATE_RESULT": "${{ needs.validate.result }}",
        "PATH_RUN": "${{ needs.validate.outputs.postgresql }}",
        "EVENT": "${{ github.event_name }}",
        "DRAFT": "${{ github.event.pull_request.draft }}",
        "JOBS": "${{ inputs.jobs }}",
    }
    # The behavioral gate test also executes failure/cancelled/skipped results;
    # explanatory output is not part of the protection contract.
    assert gate["steps"][0]["run"].strip().endswith('test "$SHARD_RESULT" = success')
    # The job limit covers setup, one partition deadline and the upload.
    assert shards["timeout-minutes"] == 40
    assert shards["timeout-minutes"] >= quality_ci.SHARD_TIMEOUT // 60 + 5
    assert gate["timeout-minutes"] == 10
    assert any(
        "quality_ci job --index ${{ matrix.job }} --count " + str(count) + " "
        in step.get("run", "")
        for step in shards["steps"]
    )
    download = next(
        step
        for step in gate["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    # Each job's artifact holds partition-N directories; the combiner needs
    # them merged side by side, not nested under per-artifact directories.
    assert download["with"]["merge-multiple"] is True
    for slot in range(1, quality_ci.PARTITIONS_PER_JOB + 1):
        postgres = shards["services"][f"postgres-{slot}"]
        assert postgres["env"]["POSTGRES_INITDB_ARGS"] == (
            "--set=log_min_error_statement=panic"
        )


def test_ci_does_not_duplicate_the_coverage_baseline_in_lint_job():
    """Preflight runs explicit fast modules, never a third complete baseline.

    The only complete non-database run in preflight replaces shard one's
    baseline when path classification skips the shards, so it never runs
    alongside that baseline.
    """
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = [
        step
        for step in workflow["jobs"]["validate"]["steps"]
        if "pytest" in step.get("run", "")
    ]
    replacement = [step for step in steps if "if" in step]
    assert [step["if"] for step in replacement] == [
        "${{ steps.paths.outputs.postgresql == 'false' }}"
    ]
    commands = [shlex.split(step["run"]) for step in steps if "if" not in step]
    assert len(commands) == 1
    command = commands[0]
    assert command[:3] == ["python", "-m", "pytest"]
    assert command[-2:] == ["--require-no-skips", "-q"]
    paths = command[3:-2]
    assert 1 <= len(paths) <= 12
    assert all(
        path.startswith("tests/stewardship/test_") and path.endswith(".py")
        for path in paths
    )
    assert "tests/stewardship/test_database_gate.py" in paths


def test_ci_requires_every_operational_container_module():
    """Runtime coverage cannot silently disappear from the required pipeline."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    step = next(
        item
        for item in workflow["jobs"]["stewardship-compose-core"]["steps"]
        if item.get("name") == "Validate operational runtime and provisioning"
    )
    assert step["env"]["PARISHKIT_RUN_RUNTIME_TESTS"] == "1"
    assert "--require-no-skips" in step["run"]
    for module in (
        "test_database_provisioning_container",
        "test_runtime_provisioning_container",
        "test_runtime_ingress_container",
    ):
        assert f"tests/stewardship/{module}.py" in step["run"]


def test_compose_matrix_and_required_gate_cover_all_scenarios():
    """One shared-build runner retains all eight isolated runtime scenarios."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    operational = jobs["stewardship-operational"]
    # A plain job, not a one-entry matrix, so its check name has no suffix.
    assert "strategy" not in operational
    (step,) = (
        item
        for item in operational["steps"]
        if item.get("name") == "Validate isolated development and production scenarios"
    )
    assert step["env"] == {
        "PARISHKIT_RUN_RUNTIME_TESTS": "1",
        "PROVIDER_MODES": "configured initial complete abort",
    }
    # The runner expands its modes into both production variants.
    for fragment in (
        "for mode in $PROVIDER_MODES; do",
        "for production in False True; do",
        "test_complete_foundation_bootstrap_and_online_exclusion[$mode-$production]",
        'python -m pytest "${cases[@]}" --require-no-skips --ci-progress',
    ):
        assert fragment in step["run"]
    assert operational["timeout-minutes"] == 35
    assert (
        sum("docker build" in item.get("run", "") for item in operational["steps"]) == 1
    )
    # Compare against pytest itself, not another copy of today's parameters.
    # A new parameter or second test must not silently disappear from PR CI.
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/stewardship/test_operational_compose.py",
            "--collect-only",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    actual = {
        line
        for line in collected.stdout.splitlines()
        if line.startswith("tests/stewardship/test_operational_compose.py::")
    }
    expected = {
        "tests/stewardship/test_operational_compose.py::"
        f"test_complete_foundation_bootstrap_and_online_exclusion[{provider}-{production}]"
        for provider in step["env"]["PROVIDER_MODES"].split()
        for production in ("False", "True")
    }
    assert actual == expected, collected.stdout + collected.stderr
    gate = jobs["stewardship-compose"]
    assert gate["needs"] == [
        "validate",
        "stewardship-compose-core",
        "stewardship-operational",
    ]
    assert gate["if"] == "${{ always() && github.event_name != 'push' }}"
    (step,) = gate["steps"]
    assert step["name"] == "Require all container scenarios"
    assert step["env"] == {
        "CORE_RESULT": "${{ needs.stewardship-compose-core.result }}",
        "OPERATIONAL_RESULT": "${{ needs.stewardship-operational.result }}",
        "VALIDATE_RESULT": "${{ needs.validate.result }}",
        "CORE_RUN": "${{ needs.validate.outputs.compose }}",
        "OPERATIONAL_RUN": "${{ needs.validate.outputs.operational }}",
        "EVENT": "${{ github.event_name }}",
        "DRAFT": "${{ github.event.pull_request.draft }}",
        "JOBS": "${{ inputs.jobs }}",
    }
    assert (
        step["run"]
        .strip()
        .endswith('test "$CORE_RESULT" = success\ntest "$OPERATIONAL_RESULT" = success')
    )


@pytest.mark.parametrize(
    "path,flag",
    [
        ("tests/stewardship/browser", "PARISHKIT_RUN_BROWSER_TESTS"),
        (
            "tests/stewardship/test_container_isolation.py",
            "PARISHKIT_RUN_ISOLATION_TESTS",
        ),
    ],
)
def test_ci_browser_and_isolation_cannot_pass_by_skipping(path, flag):
    """The required pipeline retains explicit opt-in and no-skip enforcement."""
    # Releases reuse main CI's run of the tagged commit, so CI is the one place.
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    wrapped = path == "tests/stewardship/browser"
    needle = "parishkit.stewardship.quality_browser" if wrapped else f"pytest {path}"
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if needle in step.get("run", "")
    ]
    assert steps
    for step in steps:
        assert step["env"][flag] == "1"
        command = next(line for line in step["run"].splitlines() if needle in line)
        if wrapped:
            # The runner's actual subprocess/skip tests own no-skips enforcement.
            assert (
                command == "python -m parishkit.stewardship.quality_browser "
                '--runs "$BROWSER_RUNS"'
            )
        else:
            assert "--require-no-skips" in command


@pytest.mark.parametrize(
    "empty", ["", 'import pytest\npytest.skip("synthetic", allow_module_level=True)\n']
)
def test_required_paths_cannot_disappear_during_collection(tmp_path, empty):
    """A passing sibling cannot conceal an empty or collection-skipped module."""
    shutil.copyfile(ROOT / "tests/conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_present.py").write_text("def test_present(): pass\n")
    (tmp_path / "test_empty.py").write_text(empty)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_present.py",
            "test_empty.py",
            "--require-no-skips",
            "-q",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert (
        "Required verification path collected no tests" in result.stdout + result.stderr
        or "skipped during collection" in result.stdout + result.stderr
    )
