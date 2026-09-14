"""Parallel CI coverage with complete, same-source execution evidence.

Each shard runs against its own PostgreSQL/Valkey cluster, on separate CI
runners or isolated local disposable services. Never run shards concurrently
against a shared cluster: SQL roles and schemas are cluster-wide test fixtures.
The ordinary quality command remains the serial, all-in-one developer gate.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException

from .arguments import StewardshipArgumentParser
from .quality import FLOOR, coverage_percentages, load_scope
from .quality_sharding import partition, tree_digest

DATABASE_TESTS = "tests/stewardship/database"
SHARD_TIMEOUT = 20 * 60


def environment():
    """Exclude ambient pytest/coverage selection while flushing child output."""
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COVERAGE_", "COV_CORE_"))
    }
    result["PYTHONUNBUFFERED"] = "1"
    return result


def outside(root, path):
    """Require external artifacts so generated measurements cannot be committed."""
    result = path.resolve()
    if result.is_relative_to(root):
        raise ValueError("CI artifacts must be outside the repository")
    return result


def database_collection(root):
    """Independently collect the full database universe without starting services."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--ds=parishkit.stewardship.settings.test",
            "--collect-only",
            "--require-no-skips",
            "--collection-manifest",
            "-q",
            DATABASE_TESTS,
        ],
        cwd=root,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        # This is the credential-free test collector, not runtime/provider I/O.
        # Keep its import/collection failure visible without dumping full logs.
        print((result.stdout + result.stderr)[-12000:], file=sys.stderr)
        result.check_returncode()
    prefix = "PARISHKIT_TEST_NODEIDS="
    manifests = [
        line[len(prefix) :]
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    ]
    if len(manifests) != 1:
        raise ValueError("Missing unique collection manifest")
    nodes = json.loads(manifests[0])
    if (
        not isinstance(nodes, list)
        or not nodes
        or any(
            not isinstance(node, str) or not node.startswith(DATABASE_TESTS + "/")
            for node in nodes
        )
    ):
        raise ValueError("Invalid database collection")
    partition(nodes, 1, 1)
    return sorted(nodes)


def run_shard(root, output, index, count):
    """Run one partition and, on shard one only, the baseline; require success."""
    partition([], index, count)
    scope = load_scope(root)
    digest = tree_digest(root)
    output = outside(root, output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    data = output / "coverage.data"
    env = environment()
    env["COVERAGE_FILE"] = str(data)
    arguments = [
        sys.executable,
        "-m",
        "pytest",
        "--cov-branch",
        *(f"--cov={module}" for module in scope.modules),
        "--cov-report=",
        "-p",
        "no:cacheprovider",
        "--durations=20",
        "-o",
        "faulthandler_timeout=120",
    ]
    deadline = time.monotonic() + SHARD_TIMEOUT
    if index == 1:
        result = subprocess.run(
            [*arguments, "--ds=parishkit.stewardship.settings.test", "tests"],
            cwd=root,
            env=env,
            check=False,
            timeout=SHARD_TIMEOUT,
        )
        if result.returncode:
            return result.returncode
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(arguments, SHARD_TIMEOUT)
    result = subprocess.run(
        [
            *arguments,
            *(["--cov-append"] if index == 1 else []),
            "--ds=parishkit.stewardship.settings.database_test",
            "--require-postgresql-tests",
            "--require-no-skips",
            f"--ci-shard={index}/{count}",
            f"--ci-evidence={output / 'tests.json'}",
            DATABASE_TESTS,
        ],
        cwd=root,
        env=env,
        check=False,
        timeout=remaining,
    )
    if result.returncode:
        return result.returncode
    if tree_digest(root) != digest:
        raise ValueError("Repository changed during CI measurement")
    evidence = json.loads((output / "tests.json").read_text())
    expected = partition(evidence["universe"], index, count)
    if (
        not expected
        or evidence["selected"] != expected
        or evidence["completed"] != expected
    ):
        raise ValueError("Incomplete CI partition")
    receipt = {
        "schema": 1,
        "index": index,
        "count": count,
        "baseline": index == 1,
        "root": str(root),
        "tree": digest,
        "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "tests_sha256": hashlib.sha256(
            (output / "tests.json").read_bytes()
        ).hexdigest(),
    }
    with (output / "receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream)
    print(
        f"CI shard {index}/{count}: {len(expected):,} database tests passed", flush=True
    )
    return 0


def remap(root, recorded_root, filename):
    """Rebase measured checkout files without admitting outside-source aliases."""
    original = Path(filename)
    origin = Path(recorded_root)
    relative = original.relative_to(origin) if original.is_absolute() else original
    if ".." in relative.parts:
        raise ValueError("Coverage path escapes its checkout")
    result = root / relative
    if not result.is_file() or result.resolve() != result:
        raise ValueError("Coverage references an absent or aliased source")
    return str(result)


def combine(root, directory, report, count):
    """Require every exact partition before combining raw line/branch evidence."""
    partition([], 1, count)
    scope = load_scope(root)
    digest = tree_digest(root)
    directory = outside(root, directory)
    report = outside(root, report)
    if report.exists() or Path(str(report) + ".data").exists():
        raise ValueError("Coverage report must be new")
    receipts = sorted(directory.glob("*/receipt.json"))
    if len(receipts) != count:
        raise ValueError("Missing or extra CI shard artifacts")
    universe = database_collection(root)
    combined = Coverage(branch=True, source=[str(root / "src")], config_file=False)
    combined.set_option("run:data_file", str(report) + ".data")
    seen = set()
    for path in receipts:
        receipt = json.loads(path.read_text())
        index = receipt["index"]
        if (
            type(index) is not int
            or index in seen
            or receipt["schema"] != 1
            or receipt["count"] != count
            or receipt["baseline"] is not (index == 1)
            or receipt["tree"] != digest
        ):
            raise ValueError("Duplicate, stale or incompatible CI shard")
        expected = partition(universe, index, count)
        data_path = path.parent / "coverage.data"
        tests_path = path.parent / "tests.json"
        if (
            hashlib.sha256(data_path.read_bytes()).hexdigest() != receipt["data_sha256"]
            or hashlib.sha256(tests_path.read_bytes()).hexdigest()
            != receipt["tests_sha256"]
        ):
            raise ValueError("CI shard artifact digest mismatch")
        tests = json.loads(tests_path.read_text())
        if tests != {"universe": universe, "selected": expected, "completed": expected}:
            raise ValueError("CI shard did not execute its complete test partition")
        data = CoverageData(basename=str(data_path))
        data.read()
        if not data.has_arcs() or not data.measured_files():
            raise ValueError("CI shard lacks branch coverage")
        # Validate before passing a callback into SQLite: callback exceptions
        # otherwise lose their useful type behind a generic database error.
        for filename in data.measured_files():
            remap(root, receipt["root"], filename)
        combined.get_data().update(data, map_path=partial(remap, root, receipt["root"]))
        seen.add(index)
    if seen != set(range(1, count + 1)) or tree_digest(root) != digest:
        raise ValueError("CI evidence is incomplete or source changed")
    combined.save()
    # Reserve the output exclusively, just like the serial quality runner.
    with report.open("x", encoding="utf-8"):
        pass
    combined.json_report(outfile=str(report))
    lines, branches = coverage_percentages(root, scope, report)
    print(f"All {len(universe):,} database tests accounted for across {count} shards")
    print(f"Stewardship scope: lines {lines:.2f}%; branches {branches:.2f}%")
    return 0 if lines >= FLOOR and branches >= FLOOR else 1


def main(argv=None):
    """Run isolated CI shards or the mandatory complete-coverage aggregation gate."""
    parser = StewardshipArgumentParser(
        "python -m parishkit.stewardship.quality_ci",
        description="Run isolated test shards and verify their combined coverage.",
        error_hints={},
    )
    parser.add_argument("operation", choices=("shard", "combine"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--index", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if args.operation == "shard":
            if args.index is None or args.output is None or args.input or args.report:
                parser.usage_error("shard requires --index/--output only")
            return run_shard(root, args.output, args.index, args.count)
        if args.input is None or args.report is None or args.index or args.output:
            parser.usage_error("combine requires --input/--report only")
        return combine(root, args.input, args.report, args.count)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        CoverageException,
        subprocess.SubprocessError,
    ):
        print(
            "ERROR: CI measurement incomplete or invalid; inspect test progress",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
