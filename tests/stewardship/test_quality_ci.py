"""Isolated CI partitions must account for every test and combine real coverage."""

import hashlib
import io
import json
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest
from coverage import Coverage

from parishkit.stewardship import quality_ci as ci
from parishkit.stewardship import quality_sharding as sharding
from parishkit.stewardship.quality_sharding import partition, tree_digest

ROOT = Path(__file__).resolve().parents[2]


def workflow_partitions(job, count):
    """Every partition the packed shard matrix runs, in matrix order."""
    return [
        index
        for number in job["strategy"]["matrix"]["job"]
        for index in ci.job_partitions(number, count)
    ]


def test_workflow_partition_count_matches_required_combiner():
    """The real matrix and strict coverage receipt count cannot silently drift."""
    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    shard = jobs["stewardship-postgresql-shard"]
    combine = jobs["stewardship-postgresql"]
    command = next(
        step["run"]
        for step in combine["steps"]
        if "quality_ci combine " in step.get("run", "")
    )
    count = int(command.split("--count ", 1)[1].split()[0])
    assert 1 <= count <= 32
    # The matrix is exactly the jobs PARTITIONS_PER_JOB implies, and together
    # they run every partition the combiner requires, each once.
    jobs_needed = ci.job_count(count)
    assert shard["strategy"]["matrix"]["job"] == list(range(1, jobs_needed + 1)), (
        f"PARTITIONS_PER_JOB={ci.PARTITIONS_PER_JOB} needs {jobs_needed} jobs"
    )
    assert workflow_partitions(shard, count) == list(range(1, count + 1))
    run = next(
        step["run"]
        for step in shard["steps"]
        if "quality_ci job " in step.get("run", "")
    )
    assert f"--count {count} " in run


@pytest.mark.parametrize("count", [1, 2, 3, 4, 8, 14, 32])
@pytest.mark.parametrize("per_job", [1, 2, 3, 4])
def test_packed_jobs_cover_every_partition_once(count, per_job):
    """Packing never drops or repeats a partition, and partition one, which
    also runs the non-database baseline, shares a runner with the fewest."""
    jobs = ci.job_count(count, per_job)
    packed = [ci.job_partitions(job, count, per_job) for job in range(1, jobs + 1)]
    assert [index for group in packed for index in group] == list(range(1, count + 1))
    assert all(1 <= len(group) <= per_job for group in packed)
    assert len(packed[0]) == min(len(group) for group in packed)
    for job in (0, jobs + 1, "1", True):
        with pytest.raises(ValueError):
            ci.job_partitions(job, count, per_job)


def test_slot_ports_are_distinct_and_skip_the_reserved_valkey_port():
    """Slot one keeps the default ports; no slot may claim 56380, which the
    unavailable-service tests require to stay closed."""
    slots = [ci.slot_ports(slot) for slot in range(8)]
    assert slots[0] == {
        "PARISHKIT_TEST_POSTGRES_PORT": "55432",
        "PARISHKIT_TEST_VALKEY_PORT": "56379",
    }
    for name in ("PARISHKIT_TEST_POSTGRES_PORT", "PARISHKIT_TEST_VALKEY_PORT"):
        assert len({slot[name] for slot in slots}) == len(slots)
    assert "56380" not in {slot["PARISHKIT_TEST_VALKEY_PORT"] for slot in slots}


def test_workflow_services_match_the_packed_slots():
    """One identical PostgreSQL/Valkey pair per packed slot, on its ports.

    Changing PARTITIONS_PER_JOB fails here until the workflow declares the
    matching pairs, so two partitions can never share a cluster.
    """
    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    services = jobs["stewardship-postgresql-shard"]["services"]
    slots = range(1, ci.PARTITIONS_PER_JOB + 1)
    assert set(services) == {
        f"{kind}-{slot}" for kind in ("postgres", "valkey") for slot in slots
    }, f"declare one postgres-N/valkey-N pair per slot 1..{ci.PARTITIONS_PER_JOB}"
    for slot in slots:
        ports = ci.slot_ports(slot - 1)
        postgres, valkey = services[f"postgres-{slot}"], services[f"valkey-{slot}"]
        assert postgres["ports"] == [f"{ports['PARISHKIT_TEST_POSTGRES_PORT']}:5432"]
        assert valkey["ports"] == [f"{ports['PARISHKIT_TEST_VALKEY_PORT']}:6379"]
        for kind, service in (("postgres", postgres), ("valkey", valkey)):
            first = services[f"{kind}-1"]
            assert {**service, "ports": None} == {**first, "ports": None}


def test_fast_feedback_precedes_full_candidate_suites():
    """Draft skips cannot become full-suite evidence when readiness changes."""
    import yaml

    from .test_quality_paths import (
        GATES,
        HEAVY,
        assert_gate_truth_table,
        heavy_condition,
    )

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
    for name, group in HEAVY.items():
        assert jobs[name]["needs"] == "validate"
        assert jobs[name]["if"] == heavy_condition(group)
    for name in ("stewardship-compose", "stewardship-postgresql"):
        gate = jobs[name]
        assert gate["if"] == "${{ always() && github.event_name != 'push' }}"
        # A draft, failed, cancelled or unexpected skip still fails the gate;
        # only an intentional path skip (in a ready PR or an explicitly
        # "affected" dispatch) may pass without success.
        assert_gate_truth_table(gate["steps"][0], GATES[name])


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


@pytest.mark.parametrize(
    ("size", "reserved"),
    [
        # Large enough that the full baseline reservation applies.
        (8000, sharding.BASELINE_SECONDS),
        # Smaller: capped at three quarters of an average shard.
        (2000, 3 * 2000 // (4 * 8)),
    ],
)
def test_large_suite_reserves_baseline_time(monkeypatch, size, reserved):
    """The shard with the extra baseline receives less database work, but
    never so little that it empties."""
    monkeypatch.setattr(sharding, "SLOW_TEST_SECONDS", {})
    nodes = [f"test_a.py::case[{n}]" for n in range(size)]
    groups = [partition(nodes, index, 8) for index in range(1, 9)]
    assert max(map(len, groups[1:])) - len(groups[0]) == reserved
    assert len(groups[0]) >= size // 8 // 4
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


def artifacts(root, tmp_path, monkeypatch, *, nodes=None, contexts=False):
    """Produce genuine raw coverage for opposite branches in two separate jobs.

    With ``contexts``, job N's execution is recorded as test ``nodes[N]``.
    """
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
        if contexts:
            cov.switch_context(f"{nodes[index]}|run")
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


class Child:
    """Stand in for one shard child that prints a line and exits.

    ``failing`` names the partition that exits with status 2; ``launched``
    collects every child so a test can check each was waited for or stopped.
    """

    def __init__(self, command, launched, failing=None, **kwargs):
        self.index = int(command[command.index("--index") + 1])
        self.command, self.kwargs = command, kwargs
        self.code = 2 if self.index == failing else 0
        self.stdout = io.BytesIO(f"progress {self.index}\n".encode())
        self.waited = self.terminated = False
        launched.append(self)

    def poll(self):
        """A stand-in child is still running until waited for."""
        return self.code if self.waited else None

    def wait(self, timeout=None):
        """Report this child's exit status."""
        self.waited = True
        return self.code

    def terminate(self):
        """Record that run_job stopped this child."""
        self.terminated = True


@pytest.mark.parametrize("failing", [None, 3, 4, 5])
def test_packed_job_runs_isolated_partitions_and_fails_on_any(
    repository, tmp_path, monkeypatch, capsys, failing
):
    """Each packed partition is an ordinary shard child on its own service
    pair and temporary root; its output is relayed live, and one failure
    fails the whole job only after every partition has finished."""
    launched = []
    monkeypatch.setattr(
        ci.subprocess, "Popen", partial(Child, launched=launched, failing=failing)
    )
    output = tmp_path / "job"
    assert ci.run_job(repository, output, 2, 14) == (1 if failing else 0)
    assert output.is_dir()
    assert [child.index for child in launched] == ci.job_partitions(2, 14)
    for slot, child in enumerate(launched):
        command, index = child.command, child.index
        assert command[3] == "shard"
        assert command[command.index("--count") + 1] == "14"
        assert command[command.index("--output") + 1] == str(
            output / f"partition-{index}"
        )
        assert command[command.index("--basetemp") + 1] == str(
            tmp_path / "job-tmp" / f"partition-{index}"
        )
        assert child.kwargs["env"].items() >= ci.slot_ports(slot).items()
        assert not any(name.startswith("PYTEST_") for name in child.kwargs["env"])
        # Every partition finishes, even after an earlier one failed.
        assert child.waited and not child.terminated
    printed = capsys.readouterr().out
    for child in launched:
        assert f"[partition {child.index}] progress {child.index}" in printed
        assert (f"CI partition {child.index}/14 failed (status 2)" in printed) is (
            child.index == failing
        )
    assert ("partitions [3, 4, 5] passed" in printed) is (failing is None)


def test_packed_job_stops_started_children_when_a_launch_fails(
    repository, tmp_path, monkeypatch
):
    """A partition that cannot start must not leave its siblings orphaned."""
    launched = []

    def launch(command, **kwargs):
        """Start the first child, then fail to start the second."""
        if launched:
            raise OSError("no more processes")
        return Child(command, launched, **kwargs)

    monkeypatch.setattr(ci.subprocess, "Popen", launch)
    with pytest.raises(OSError):
        ci.run_job(repository, tmp_path / "job", 2, 14)
    assert [child.terminated for child in launched] == [True]


def test_shard_passes_its_private_temporary_root(repository, tmp_path, monkeypatch):
    """Both of a shard's pytest runs use the temporary root it was given."""
    run = Mock(return_value=subprocess.CompletedProcess([], 1))
    monkeypatch.setattr(ci.subprocess, "run", run)
    ci.run_shard(repository, tmp_path / "shard", 1, 8, tmp_path / "temporary")
    assert f"--basetemp={tmp_path / 'temporary'}" in run.call_args.args[0]


def test_packed_job_refuses_output_inside_the_checkout(repository, monkeypatch):
    """Packed evidence, like a single shard's, can never land in the tree."""
    monkeypatch.setattr(ci.subprocess, "Popen", Mock())
    assert (
        ci.main(
            [
                "job",
                "--root",
                str(repository),
                "--index",
                "1",
                "--count",
                "14",
                "--output",
                str(repository / "evidence"),
            ]
        )
        == 2
    )
    assert not ci.subprocess.Popen.called


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


def selected_shard(repository, tmp_path, monkeypatch, index, nodes, subset, **options):
    """Run one stand-in shard whose child records a selection's evidence."""
    output = tmp_path / f"shard-{index}"

    def execute(command, **kwargs):
        """Stand in for successful children; the database one writes evidence."""
        if ci.DATABASE_TESTS in command:
            selected = partition(subset, index, 14)
            (output / "tests.json").write_text(
                json.dumps(
                    {
                        "universe": nodes,
                        "selected": selected,
                        "completed": selected,
                        **({"subset": subset} if options.get("select") else {}),
                    }
                )
            )
        return subprocess.CompletedProcess(command, 0)

    run = Mock(side_effect=execute)
    monkeypatch.setattr(ci.subprocess, "run", run)
    assert ci.run_shard(repository, output, index, 14, **options) == 0
    return run, output


@pytest.mark.parametrize("index", [1, 2, 14])
def test_selected_shard_skips_coverage_and_may_be_empty(
    repository, tmp_path, monkeypatch, capsys, index
):
    """A selection runs unmeasured, accepts an empty partition and says how
    many tests it ran and skipped, in the log and the job summary."""
    nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(20)]
    subset = nodes[:3]
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    select = tmp_path / "selection.json"
    run, output = selected_shard(
        repository, tmp_path, monkeypatch, index, nodes, subset, select=select
    )
    for call in run.call_args_list:
        command = call.args[0]
        assert not any(argument.startswith("--cov") for argument in command)
        assert "COVERAGE_FILE" not in call.kwargs["env"]
    assert f"--ci-select={select.resolve()}" in run.call_args.args[0]
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["coverage"] is False and receipt["data_sha256"] is None
    line = "the selection runs 3 of 20 and skips 17"
    assert line in capsys.readouterr().out
    assert line in summary.read_text()


def test_affected_whole_group_shard_skips_coverage_only(
    repository, tmp_path, monkeypatch
):
    """Without a selection an unmeasured shard still runs its full partition."""
    nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(20)]
    run, output = selected_shard(
        repository, tmp_path, monkeypatch, 1, nodes, nodes, coverage=False
    )
    command = run.call_args.args[0]
    assert not any(argument.startswith("--cov") for argument in command)
    assert not any(argument.startswith("--ci-select") for argument in command)
    assert json.loads((output / "receipt.json").read_text())["coverage"] is False


def test_unselected_shard_cannot_claim_an_empty_or_subset_partition(
    repository, tmp_path, monkeypatch
):
    """Only a selection may run fewer tests than its partition of the suite."""
    nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(20)]
    with pytest.raises(ValueError, match="Incomplete"):
        selected_shard(repository, tmp_path, monkeypatch, 2, nodes, nodes[:1])


def test_measured_shards_record_per_test_contexts(repository):
    """Full runs record which test executed each line, for the test map."""
    assert "--cov-context=test" in ci.coverage_arguments(ci.load_scope(repository))


@pytest.mark.parametrize("problem", ["unmeasured", "subset"])
def test_combine_refuses_affected_evidence(repository, tmp_path, monkeypatch, problem):
    """An unmeasured or selected partition can never become full-run evidence."""
    directory = artifacts(repository, tmp_path, monkeypatch)
    path = directory / "2/receipt.json"
    receipt = json.loads(path.read_text())
    if problem == "unmeasured":
        receipt["coverage"] = False
    else:
        tests = directory / "2/tests.json"
        evidence = json.loads(tests.read_text())
        tests.write_text(json.dumps({**evidence, "subset": evidence["universe"]}))
        receipt["tests_sha256"] = hashlib.sha256(tests.read_bytes()).hexdigest()
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        ci.combine(repository, directory, tmp_path / "combined.json", 2)


def test_combine_writes_the_test_map(repository, tmp_path, monkeypatch, capsys):
    """The combined contexts become a file-to-test map for affected runs."""
    monkeypatch.setenv("COVERAGE_CORE", "ctrace")
    nodes = [f"tests/stewardship/database/test_a.py::test_{n}" for n in range(20)]
    directory = artifacts(repository, tmp_path, monkeypatch, nodes=nodes, contexts=True)
    test_map = tmp_path / "map.json"
    report = tmp_path / "combined.json"
    assert ci.combine(repository, directory, report, 2, test_map) == 0
    written = json.loads(test_map.read_text())
    assert written["schema"] == 1 and written["count"] == 2
    assert written["tests"] == sorted(nodes)
    # Shard N's stand-in test ran every source file.
    expected = sorted(sorted(nodes).index(nodes[index]) for index in (1, 2))
    assert written["files"] == {
        "src/parishkit/shared.py": expected,
        "src/parishkit/stewardship/__init__.py": expected,
    }
    # The map is optional: failing to write it is logged, never fatal.
    assert ci.combine(repository, directory, tmp_path / "again.json", 2, test_map) == 0
    assert "database test map not written" in capsys.readouterr().err
    assert json.loads(test_map.read_text()) == written


def test_packed_job_passes_affected_options(repository, tmp_path, monkeypatch):
    """Every partition child gets the job's coverage and selection options."""
    launched = []
    monkeypatch.setattr(ci.subprocess, "Popen", partial(Child, launched=launched))
    select = tmp_path / "selection.json"
    assert ci.run_job(repository, tmp_path / "job", 2, 14, coverage=False) == 0
    assert all(child.command[-1] == "--no-coverage" for child in launched)
    launched.clear()
    assert ci.run_job(repository, tmp_path / "job2", 2, 14, select=select) == 0
    assert all(child.command[-2:] == ["--select", str(select)] for child in launched)


def test_actual_plugin_selection(tmp_path):
    """Real pytest hooks partition a selection, skip unflagged sql_rules
    tests and accept an empty partition with complete evidence."""
    root = tmp_path / "probe"
    directory = root / "tests/stewardship/database"
    directory.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests/conftest.py", root / "tests/conftest.py")
    (root / "pytest.ini").write_text("[pytest]\n")
    (directory / "test_probe.py").write_text(
        "import pytest\n"
        "def test_mapped():\n    pass\n"
        "def test_unmapped():\n    pass\n"
        "@pytest.mark.sql_rules\ndef test_rule():\n    pass\n"
    )
    probe = "tests/stewardship/database/test_probe.py"
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema": 1,
                # test_rule is new to the map and sql_rules, so it skips.
                "tests": [f"{probe}::test_mapped"],
                "known": [f"{probe}::test_mapped", f"{probe}::test_unmapped"],
                "files": [],
                "sql_rules": False,
            }
        )
    )
    for shard in ("1/1", "2/2"):
        evidence = tmp_path / f"tests-{shard.replace('/', '-')}.json"
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
                f"--ci-shard={shard}",
                f"--ci-evidence={evidence}",
                f"--ci-select={selection}",
            ],
            cwd=root,
            env=ci.environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(evidence.read_text())
        assert data["subset"] == [f"{probe}::test_mapped"]
        assert len(data["universe"]) == 3
        expected = [f"{probe}::test_mapped"] if shard == "1/1" else []
        assert data["selected"] == data["completed"] == expected
