"""Isolated CI partitions must account for every test and combine real coverage."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from coverage import Coverage

from parishkit.stewardship import quality_ci as ci
from parishkit.stewardship import quality_sharding as sharding
from parishkit.stewardship.quality_sharding import partition, tree_digest

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_partition_count_matches_required_combiner():
    """The real matrix and strict coverage receipt count cannot silently drift."""
    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    shard = jobs["stewardship-postgresql-shard"]
    indexes = shard["strategy"]["matrix"]["shard"]
    assert 1 <= len(indexes) <= 32
    assert indexes == list(range(1, len(indexes) + 1))
    for job, operation in (
        (shard, "shard"),
        (jobs["stewardship-postgresql"], "combine"),
    ):
        commands = [step.get("run", "") for step in job["steps"]]
        command = next(line for line in commands if f"quality_ci {operation} " in line)
        assert f"--count {len(indexes)} " in command


def test_fast_feedback_precedes_full_candidate_suites():
    """Draft skips cannot become full-suite evidence when readiness changes."""
    import itertools

    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    fast = jobs["validate"]
    assert "if" not in fast and "needs" not in fast
    smoke = next(
        step
        for step in fast["steps"]
        if step.get("name") == "Fast application and CI contracts"
    )
    assert "test_build.py" in smoke["run"]
    assert "test_runtime_grants.py" in smoke["run"]
    assert "--require-no-skips" in smoke["run"]
    for name in (
        "stewardship-compose-core",
        "stewardship-operational",
        "stewardship-browser-engine",
        "stewardship-postgresql-shard",
    ):
        assert jobs[name]["needs"] == "validate"
        assert jobs[name]["if"] == (
            "${{ github.event_name == 'push' || "
            "github.event.pull_request.draft == false }}"
        )
    for name in ("stewardship-compose", "stewardship-postgresql"):
        gate = jobs[name]
        assert gate["if"] == "${{ always() }}"
        check = gate["steps"][0]
        for results in itertools.product(
            ("success", "failure", "cancelled", "skipped"), repeat=len(check["env"])
        ):
            completed = subprocess.run(
                ["sh", "-e", "-c", check["run"]],
                env=dict(zip(check["env"], results, strict=True)),
                capture_output=True,
                check=False,
                timeout=5,
            )
            assert (completed.returncode == 0) is all(
                result == "success" for result in results
            )


@pytest.mark.parametrize("count", [1, 2, 8, 12, 32])
def test_complete_disjoint_stable_partition(count):
    """Every newly collected test is assigned exactly once, without a file list."""
    nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(1000)]
    groups = [partition(nodes, index, count) for index in range(1, count + 1)]
    assert sorted(node for group in groups for node in group) == sorted(nodes)
    assert groups == [partition(nodes[::-1], i, count) for i in range(1, count + 1)]


@pytest.mark.parametrize("index,count", [(0, 8), (9, 8), (1, 0), (1, 33), (True, 8)])
def test_invalid_partition(index, count):
    """Invalid bounds never silently select a successful empty shard."""
    with pytest.raises(ValueError):
        partition([], index, count)


def test_duplicate_collection_rejected():
    """Duplicate IDs are not silently deduplicated."""
    with pytest.raises(ValueError):
        partition(["same", "same"], 1, 2)


def test_cost_balancing_keeps_slow_cases_separate(monkeypatch):
    """Real lease waits can be balanced without mocking clocks or omitting tests."""
    monkeypatch.setattr(sharding, "SLOW_TEST_SECONDS", {"slow": 60})
    nodes = [f"test_a.py::slow[{n}]" for n in range(8)]
    nodes += [f"test_new.py::unknown[{n}]" for n in range(80)]
    groups = [partition(nodes, index, 8) for index in range(1, 9)]
    assert all(sum("::slow[" in node for node in group) == 1 for group in groups)
    assert sorted(node for group in groups for node in group) == sorted(nodes)


def test_large_suite_reserves_baseline_time(monkeypatch):
    """The shard with the extra baseline receives less database work."""
    monkeypatch.setattr(sharding, "SLOW_TEST_SECONDS", {})
    nodes = [f"test_a.py::case[{n}]" for n in range(2000)]
    groups = [partition(nodes, index, 8) for index in range(1, 9)]
    assert max(map(len, groups[1:])) - len(groups[0]) == 240
    assert sorted(node for group in groups for node in group) == sorted(nodes)


def test_parameter_cost_override_preserves_all_cases(monkeypatch):
    """Different fixture waits change scheduling, never collection membership."""
    monkeypatch.setattr(sharding, "SLOW_TEST_SECONDS", {"case": 100})
    monkeypatch.setattr(sharding, "CASE_SECONDS", {"case[False]": 10})
    nodes = ["a.py::case[False]", "a.py::case[True]", "a.py::new"]
    assert [sharding.estimated_seconds(node) for node in nodes] == [10, 100, 1]
    groups = [partition(nodes, index, 2) for index in (1, 2)]
    assert sorted(node for group in groups for node in group) == sorted(nodes)
    assert groups == [partition(nodes[::-1], index, 2) for index in (1, 2)]


def test_module_fixture_cost_is_used_unless_exact_case_is_known(monkeypatch):
    """Setup-heavy new cases inherit measured module cost, not one second."""
    monkeypatch.setattr(sharding, "MODULE_SECONDS", {"test_slow.py": 20})
    monkeypatch.setattr(sharding, "SLOW_TEST_SECONDS", {"long": 100})
    monkeypatch.setattr(sharding, "CASE_SECONDS", {"long[short]": 5})
    nodes = [
        "a/test_slow.py::new",
        "a/test_slow.py::long[normal]",
        "a/test_slow.py::long[short]",
        "a/test_unknown.py::new",
    ]
    assert [sharding.estimated_seconds(node) for node in nodes] == [20, 100, 5, 1]
    assert sorted(partition(nodes, 1, 2) + partition(nodes, 2, 2)) == sorted(nodes)


@pytest.fixture
def repository(tmp_path):
    """Build two branch-bearing source files and the actual coverage manifest."""
    root = tmp_path / "checkout"
    package = root / "src/parishkit/stewardship"
    package.mkdir(parents=True)
    code = "if value:\n    answer = 1\nelse:\n    answer = 2\n"
    (package / "__init__.py").write_text(code)
    (root / "src/parishkit/shared.py").write_text(code)
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (root / "coverage-stewardship.toml").write_text(
        'schema_version = 1\nshared_modules = ["src/parishkit/shared.py"]\n'
    )
    return root


def artifacts(root, tmp_path, monkeypatch, *, nodes=None):
    """Produce genuine raw coverage for opposite branches in two separate jobs."""
    directory = tmp_path / "artifacts"
    if nodes is None:
        nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(20)]
    monkeypatch.setattr(ci, "database_collection", lambda root: sorted(nodes))
    for index in (1, 2):
        output = directory / str(index)
        output.mkdir(parents=True)
        data = output / "coverage.data"
        cov = Coverage(data_file=str(data), branch=True, config_file=False)
        cov.start()
        for path in sorted((root / "src").rglob("*.py")):
            exec(compile(path.read_text(), str(path), "exec"), {"value": index == 1})
        cov.stop()
        cov.save()
        selected = partition(nodes, index, 2)
        tests = output / "tests.json"
        tests.write_text(
            json.dumps(
                {
                    "universe": sorted(nodes),
                    "selected": selected,
                    "completed": selected,
                }
            )
        )
        (output / "receipt.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "index": index,
                    "count": 2,
                    "baseline": index == 1,
                    "root": str(root),
                    "tree": tree_digest(root),
                    "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                    "tests_sha256": hashlib.sha256(tests.read_bytes()).hexdigest(),
                }
            )
        )
    return directory


def test_real_coverage_union(repository, tmp_path, monkeypatch, capsys):
    """Opposite partial branches combine to 100%, rather than being averaged."""
    directory = artifacts(repository, tmp_path, monkeypatch)
    assert ci.combine(repository, directory, tmp_path / "combined.json", 2) == 0
    assert "lines 100.00%; branches 100.00%" in capsys.readouterr().out
    assert (directory / "1/coverage.data").is_file()


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "duplicate",
        "stale",
        "data",
        "tests",
        "selection",
        "universe",
        "count",
        "schema",
        "outside",
        "report_exists",
        "baseline",
    ],
)
def test_incomplete_evidence_fails(repository, tmp_path, monkeypatch, problem):
    """Reject incomplete/mismatched execution, source or raw coverage artifacts."""
    directory = artifacts(repository, tmp_path, monkeypatch)
    path = directory / "2/receipt.json"
    receipt = json.loads(path.read_text())
    report = tmp_path / "combined.json"
    if problem == "missing":
        path.unlink()
    elif problem == "duplicate":
        receipt["index"] = 1
    elif problem == "stale":
        (repository / "src/parishkit/shared.py").write_text("answer = 3\n")
    elif problem == "data":
        (directory / "2/coverage.data").write_bytes(b"invalid")
    elif problem == "tests":
        (directory / "2/tests.json").write_text("{}")
    elif problem in {"selection", "universe"}:
        tests = directory / "2/tests.json"
        evidence = json.loads(tests.read_text())
        evidence["selected" if problem == "selection" else "universe"].pop()
        tests.write_text(json.dumps(evidence))
        receipt["tests_sha256"] = hashlib.sha256(tests.read_bytes()).hexdigest()
    elif problem in {"count", "schema"}:
        receipt[problem] = 99
    elif problem == "outside":
        receipt["root"] = str(repository / "wrong")
    elif problem == "report_exists":
        report.write_text("old report")
    elif problem == "baseline":
        receipt["baseline"] = True
    if problem != "missing":
        path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        ci.combine(repository, directory, report, 2)


@pytest.mark.parametrize(
    "baseline,database", [(1, 0), (0, 1), (0, 5), (-11, 0), (0, -11)]
)
def test_failed_shard_never_publishes_receipt(
    repository, tmp_path, monkeypatch, capsys, baseline, database
):
    """A passing baseline cannot mask failed, empty or crashed database execution."""
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], baseline),
            subprocess.CompletedProcess([], database),
        ]
    )
    monkeypatch.setattr(ci.subprocess, "run", run)
    output = tmp_path / "shard"
    failed = baseline or database
    # A signalled child is a named, shell-safe failure at both call sites.
    assert ci.run_shard(repository, output, 1, 8) == (1 if failed < 0 else failed)
    assert ("SIGSEGV" in capsys.readouterr().err) is (failed < 0)
    assert not (output / "receipt.json").exists()
    assert run.call_count == (1 if baseline else 2)
    if not baseline:
        command = run.call_args.args[0]
        assert "--require-postgresql-tests" in command
        assert "--ci-shard=1/8" in command
        assert "--cov-append" in command
        assert f"faulthandler_timeout={ci.STACK_DUMP_SECONDS}" in command
        assert 0 < run.call_args.kwargs["timeout"] <= ci.SHARD_TIMEOUT
        assert "--require-no-skips" in command


def test_stack_dump_keeps_headroom_from_slow_tests_and_the_deadline():
    """A dump near a slow legitimate test kills passing shards; one near the
    deadline rarely fires before it. Every hint map counts, because an exact
    case hint overrides its test's. Hints come from completed runs, so this is
    a floor on real durations, not a measured ceiling."""
    slowest = max(
        *sharding.SLOW_TEST_SECONDS.values(),
        *sharding.CASE_SECONDS.values(),
        *sharding.MODULE_SECONDS.values(),
    )
    assert 2 * slowest <= ci.STACK_DUMP_SECONDS <= ci.SHARD_TIMEOUT // 2, (
        f"slowest hint {slowest}s needs STACK_DUMP_SECONDS reconsidered"
    )


@pytest.mark.parametrize(
    ("code", "status", "named"),
    [(-11, 1, "SIGSEGV"), (-9, 1, "SIGKILL"), (-250, 1, "signal 250"), (2, 2, "")],
)
def test_fatal_child_signal_is_named_not_an_opaque_status(capsys, code, status, named):
    """A crashed stack dump must read as a crash, not as unexplained exit 245."""
    assert ci.child_status(subprocess.CompletedProcess([], code)) == status
    message = capsys.readouterr().err
    assert (named in message and "hang diagnostic" in message) if named else not message


def test_hung_child_fails_without_receipt(repository, tmp_path, monkeypatch):
    """The finite subprocess deadline cannot create passing coverage evidence."""
    monkeypatch.setattr(
        ci.subprocess,
        "run",
        Mock(
            side_effect=subprocess.TimeoutExpired([], 1),
        ),
    )
    output = tmp_path / "shard"
    assert (
        ci.main(
            [
                "shard",
                "--root",
                str(repository),
                "--index",
                "1",
                "--count",
                "8",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not (output / "receipt.json").exists()


@pytest.mark.parametrize(
    "relative",
    [
        "src/parishkit/initial.sql",
        "src/parishkit/page.html",
        "tests/schema.json",
        "deploy/stewardship/Dockerfile",
        "deploy/stewardship/Dockerfile.dockerignore",
        "deploy/stewardship/compose.yaml",
        "deploy/stewardship/Caddyfile",
        ".dockerignore",
        "scripts/example/run.py",
        "tools/example.py",
        "install.py",
        "README.md",
        "docs/development/stewardship-acceptance.yaml",
    ],
)
def test_digest_tracks_non_python_inputs(repository, relative):
    """Changed SQL, templates or schema fixtures invalidate prior evidence."""
    first = tree_digest(repository)
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed")
    assert first != tree_digest(repository)


def test_digest_rejects_symlinked_parent(repository, tmp_path):
    """A source directory alias cannot hide outside-checkout measurement inputs."""
    external = tmp_path / "external"
    external.mkdir()
    (external / "hidden.py").write_text("answer = 1")
    (repository / "src/alias").symlink_to(external, target_is_directory=True)
    # Path.rglob does not traverse directory symlinks on every supported Python;
    # explicitly including a required input below an alias must still fail.
    original = repository / "pyproject.toml"
    original.unlink()
    original.symlink_to(external / "hidden.py")
    with pytest.raises(ValueError, match="real repository inputs"):
        tree_digest(repository)


@pytest.mark.parametrize("index", [1, 2])
def test_successful_shard_baseline_and_deadline(
    repository, tmp_path, monkeypatch, index
):
    """Only shard one measures baseline, sharing one deadline with database work."""
    output = tmp_path / "shard"
    nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(20)]
    clock = iter([10, 40])
    monkeypatch.setattr(ci.time, "monotonic", lambda: next(clock))

    def execute(command, **kwargs):
        """Stand in for successful children with complete execution artifacts."""
        if ci.DATABASE_TESTS in command:
            selected = partition(nodes, index, 2)
            (output / "coverage.data").write_bytes(b"raw data")
            (output / "tests.json").write_text(
                json.dumps(
                    {
                        "universe": nodes,
                        "selected": selected,
                        "completed": selected,
                    }
                )
            )
        return subprocess.CompletedProcess(command, 0)

    run = Mock(side_effect=execute)
    monkeypatch.setattr(ci.subprocess, "run", run)
    assert ci.run_shard(repository, output, index, 2) == 0
    assert run.call_count == (2 if index == 1 else 1)
    assert ("--cov-append" in run.call_args.args[0]) is (index == 1)
    assert run.call_args.kwargs["timeout"] == ci.SHARD_TIMEOUT - 30
    assert json.loads((output / "receipt.json").read_text())["baseline"] is (index == 1)


def test_baseline_exhausting_budget_prevents_database(
    repository, tmp_path, monkeypatch
):
    """A completed baseline cannot reset the shard's total deadline."""
    clock = iter([0, ci.SHARD_TIMEOUT + 1])
    monkeypatch.setattr(ci.time, "monotonic", lambda: next(clock))
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(ci.subprocess, "run", run)
    output = tmp_path / "shard"
    with pytest.raises(subprocess.TimeoutExpired):
        ci.run_shard(repository, output, 1, 8)
    assert run.call_count == 1
    assert not (output / "receipt.json").exists()


def test_collection_error_is_visible(repository, monkeypatch, capsys):
    """Import/collection diagnostics survive the subprocess failure boundary."""
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [], 2, stdout="collection failed\n", stderr="missing test dependency\n"
        )
    )
    monkeypatch.setattr(ci.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        ci.database_collection(repository)
    assert "missing test dependency" in capsys.readouterr().err
    assert "--require-no-skips" in run.call_args.args[0]


@pytest.mark.parametrize(
    "body,code",
    [
        ("pass", 0),
        ("assert False", 1),
        ('pytest.skip("synthetic")', 1),
    ],
)
def test_actual_plugin_execution_receipt(repository, tmp_path, monkeypatch, body, code):
    """Run real pytest hooks without needing database fixtures or credentials."""
    root = tmp_path / "probe"
    directory = root / "tests/stewardship/database"
    directory.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests/conftest.py", root / "tests/conftest.py")
    (root / "pytest.ini").write_text("[pytest]\n")
    (directory / "test_probe.py").write_text(
        "import pytest\n@pytest.mark.parametrize('n', range(20))\n"
        f"def test_probe(n):\n    {body}\n"
    )
    evidence = tmp_path / "tests.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/stewardship/database",
            "-q",
            "--ds=parishkit.stewardship.settings.database_test",
            "--require-postgresql-tests",
            "--require-no-skips",
            "--ci-shard=1/2",
            f"--ci-evidence={evidence}",
        ],
        cwd=root,
        env=ci.environment(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert "CI_PROGRESS" in result.stdout and "START" in result.stdout
    assert "END" in result.stdout and "elapsed=" in result.stdout
    assert evidence.exists() is (code == 0)
    if code == 0:
        data = json.loads(evidence.read_text())
        assert len(data["universe"]) == 20
        assert (
            data["completed"] == data["selected"] == partition(data["universe"], 1, 2)
        )
        timings = json.loads(evidence.with_suffix(".timings.json").read_text())
        assert set(timings) == set(data["selected"])
        for phases in timings.values():
            assert set(phases) == {"setup", "call", "teardown"}
            assert all(
                isinstance(value, float) and value >= 0 for value in phases.values()
            )
        # Feed the real producer's bytes into the unchanged strict combiner,
        # rather than testing two separately invented compatible fixtures.
        directory = artifacts(repository, tmp_path, monkeypatch, nodes=data["universe"])
        delivered = directory / "1/tests.json"
        shutil.copyfile(evidence, delivered)
        receipt_path = directory / "1/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["tests_sha256"] = hashlib.sha256(delivered.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
        assert ci.combine(repository, directory, tmp_path / "combined.json", 2) == 0


@pytest.mark.parametrize("collect_only", [False, True])
def test_module_skip_rejected_with_surviving_tests(tmp_path, collect_only):
    """A whole skipped module cannot disappear from a passing shard/universe."""
    root = tmp_path / "probe"
    directory = root / ci.DATABASE_TESTS
    directory.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests/conftest.py", root / "tests/conftest.py")
    (root / "pytest.ini").write_text("[pytest]\n")
    (directory / "test_skipped.py").write_text(
        'import pytest\npytest.skip("synthetic", allow_module_level=True)\n'
    )
    (directory / "test_surviving.py").write_text("def test_surviving():\n    pass\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ci.DATABASE_TESTS,
            "--ds=parishkit.stewardship.settings.test",
            "--require-no-skips",
            *(["--collect-only", "--collection-manifest"] if collect_only else []),
        ],
        cwd=root,
        env=ci.environment(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
