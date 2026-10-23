"""Browser CI partitions preserve the complete suite and fail closed."""

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
import yaml

from parishkit.stewardship import quality_browser
from parishkit.stewardship.quality_ci import environment
from parishkit.stewardship.quality_pytest import (
    BROWSER_DISCOVERY,
    BrowserSelection,
    failures_path,
    running_path,
)
from parishkit.stewardship.quality_sharding import (
    BROWSER_ENGINE_SECONDS,
    BROWSER_ENGINES,
    BROWSER_FILE_EXTRA_SECONDS,
    BROWSER_JOBS,
    browser_estimate,
    browser_partition,
    parse_browser_partition,
    parse_browser_runs,
)

from .test_quality_paths import GATES, assert_gate_truth_table

ROOT = Path(__file__).resolve().parents[2]
BROWSER_DIRECTORY = "tests/stewardship/browser"
CASES = [(f"opaque-{index}", engine) for index, engine in enumerate(BROWSER_ENGINES)]


def test_disjoint_exhaustive_engine_ownership():
    """Fixture values, not misleading parameter IDs, determine ownership."""
    cases = CASES + [("webkit-chromium", "firefox")]
    groups = [browser_partition(cases, engine) for engine in BROWSER_ENGINES]
    assert sorted(node for group in groups for node in group) == sorted(
        node for node, _ in cases
    )
    assert groups == [browser_partition(cases[::-1], e) for e in BROWSER_ENGINES]
    assert "webkit-chromium" in groups[1]
    assert all(groups)


@pytest.mark.parametrize(
    "cases",
    [
        None,
        [],
        tuple(CASES),
        [None],
        [["node", "chromium"]],
        [("node",)],
        [(1, "chromium")],
        [("", "chromium")],
        [("node", None)],
        [("node", "unknown")],
        CASES + [CASES[0]],
        CASES[:2],
    ],
)
def test_invalid_collection_rejected(cases):
    """Empty, duplicate, unowned and incomplete-engine collections cannot pass."""
    with pytest.raises(ValueError):
        browser_partition(cases, "chromium")


@pytest.mark.parametrize("engine", [None, "unknown", "Chromium", 1])
def test_invalid_engine_rejected(engine):
    """Unsupported engine inputs cannot silently produce an empty partition."""
    with pytest.raises(ValueError):
        browser_partition(CASES, engine)


def split_cases(files):
    """Synthetic collection: FILES maps a test file to its WebKit case count."""
    cases = [("tests/x/test_other.py::test[c]", "chromium")]
    cases.append(("tests/x/test_other.py::test[f]", "firefox"))
    for name, size in files.items():
        cases.extend((f"tests/x/{name}::test[{n}]", "webkit") for n in range(size))
    return cases


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_engine_jobs_split_whole_files_disjointly_and_exhaustively(count):
    """Every job of an engine computes one assignment, independent of order."""
    files = {f"test_{n}.py": n % 4 + 1 for n in range(9)}
    files |= {"test_components.py": 3}
    cases = split_cases(files)
    jobs = [browser_partition(cases, "webkit", n, count) for n in range(1, count + 1)]
    assert all(jobs)
    assert sorted(node for job in jobs for node in job) == browser_partition(
        cases, "webkit"
    )
    assert jobs == [
        browser_partition(cases[::-1], "webkit", n, count) for n in range(1, count + 1)
    ]
    owners = {}
    for number, job in enumerate(jobs):
        for node in job:
            assert owners.setdefault(node.split("::")[0], number) == number


def test_engine_jobs_balance_recorded_file_seconds():
    """Recorded extra seconds, not case counts, decide where a slow file goes."""
    slow = max(BROWSER_FILE_EXTRA_SECONDS, key=BROWSER_FILE_EXTRA_SECONDS.get)
    # By case count the slow file's single case would weigh least; with its
    # extra seconds it outweighs everything else, so it takes job one alone.
    cases = split_cases({slow: 1, "test_a.py": 20, "test_b.py": 20})
    assert browser_partition(cases, "webkit", 1, 2) == [f"tests/x/{slow}::test[0]"]
    assert len(browser_partition(cases, "webkit", 2, 2)) == 40


def test_engine_jobs_count_each_files_fixed_cost():
    """Every file pays a fixed setup cost, so many small files outweigh cases.

    Before #1151 a file weighed only its cases, so ten one-case files looked
    lighter than one twelve-case file and all landed in one job.
    """
    small = {f"test_s{n}.py": 1 for n in range(10)}
    cases = split_cases(small | {"test_big.py": 12})
    jobs = [browser_partition(cases, "webkit", n, 2) for n in (1, 2)]
    estimates = [browser_estimate(job, "webkit") for job in jobs]
    # Without fixed costs the big file (12 cases) would face 10 one-case
    # files in the other job; with them the small files split between jobs.
    assert all(any("test_s" in node for node in job) for job in jobs)
    # Greedy placement leaves the jobs within one small file of each other.
    assert max(estimates) - min(estimates) <= sum(BROWSER_ENGINE_SECONDS["webkit"])


@pytest.mark.parametrize(
    "index,count", [(0, 2), (3, 2), (1, 0), (1, 9), ("1", 2), (1, 2.0), (3, 3)]
)
def test_invalid_or_empty_engine_job_rejected(index, count):
    """No job number may be out of range or receive no files to run."""
    with pytest.raises(ValueError):
        browser_partition(
            split_cases({"test_a.py": 2, "test_b.py": 2}), "webkit", index, count
        )


@pytest.mark.parametrize(
    "value", ["", "1", "1/", "a/2", "1/2/3", "0/1", "3/2", "1/9", None]
)
def test_invalid_partition_text_rejected(value):
    """The CLI/pytest INDEX/COUNT text is parsed strictly."""
    with pytest.raises(ValueError, match="Invalid browser partition"):
        parse_browser_partition(value)
    assert parse_browser_partition("2/2") == (2, 2)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        " ",
        "webkit",
        "webkit:",
        "other:1/1",
        "webkit:1/2 webkit:2/2",
        "webkit:0/2",
    ],
)
def test_invalid_browser_job_rejected(value):
    """A job names each supported engine at most once, each with a valid partition."""
    with pytest.raises(ValueError, match="Invalid browser"):
        parse_browser_runs(value)
    assert parse_browser_runs("webkit:1/2 chromium:1/1") == [
        ("webkit", 1, 2),
        ("chromium", 1, 1),
    ]


def test_browser_jobs_run_every_partition_exactly_once():
    """Together the jobs run each engine's partitions 1..COUNT once, one COUNT each."""
    runs = [run for job in BROWSER_JOBS for run in parse_browser_runs(job)]
    assert len(runs) == len(set(runs))
    counts = {engine: count for engine, _, count in runs}
    assert set(counts) == set(BROWSER_ENGINES)
    assert sorted(runs) == sorted(
        (engine, index, count)
        for engine, count in counts.items()
        for index in range(1, count + 1)
    )


@pytest.fixture
def selection_config(tmp_path, monkeypatch):
    """A complete, explicitly opted-in CI profile with isolated hook spies."""
    monkeypatch.setenv("PARISHKIT_RUN_BROWSER_TESTS", "1")
    config = Mock(rootpath=tmp_path, args=[str(tmp_path / BROWSER_DIRECTORY)])
    config.ini_values = dict(BROWSER_DISCOVERY)
    config.getini.side_effect = config.ini_values.get
    config.options = {
        "--ci-browser-engine": "chromium",
        "--require-no-skips": True,
    }
    config.getoption.side_effect = lambda name, default=False: config.options.get(
        name, default
    )
    return config


@pytest.mark.parametrize(
    "option,value",
    [
        ("--require-no-skips", False),
        ("--ci-shard", "1/3"),
        ("--ci-evidence", "receipt.json"),
        ("--require-postgresql-tests", True),
        ("keyword", "chromium"),
        ("markexpr", "browser"),
        ("deselect", ["case"]),
        ("lf", True),
        ("stepwise", True),
        ("stepwise_skip", True),
        ("stepwise_reset", True),
        ("ignore", ["test_other.py"]),
        ("ignore_glob", ["*other*"]),
        ("collectonly", True),
        ("setuponly", True),
        ("setupplan", True),
        ("inifilename", "other.ini"),
        ("markers", True),
        ("override_ini", ["python_files=test_other.py"]),
    ],
)
def test_partial_or_mixed_options_rejected(selection_config, option, value):
    """CI selection cannot be narrowed by other pytest selectors/profiles."""
    selection_config.options[option] = value
    with pytest.raises(pytest.UsageError):
        BrowserSelection(selection_config)


@pytest.mark.parametrize("args", [[], ["tests"], ["tests", "other"], ["case.py::case"]])
def test_partial_paths_rejected(selection_config, args):
    """Only one complete browser directory is an admissible CI source."""
    selection_config.args = args
    with pytest.raises(pytest.UsageError):
        BrowserSelection(selection_config)


def test_missing_opt_in_rejected(selection_config, monkeypatch):
    """Do not let the normal browser opt-out turn CI into a skipped success."""
    monkeypatch.delenv("PARISHKIT_RUN_BROWSER_TESTS")
    with pytest.raises(pytest.UsageError):
        BrowserSelection(selection_config)


@pytest.mark.parametrize(
    "name", ["python_files", "python_classes", "python_functions", "norecursedirs"]
)
def test_changed_ini_discovery_rejected(selection_config, name):
    """Even file-based discovery overrides must be reviewed before CI admits them."""
    selection_config.ini_values[name] = ["other"]
    with pytest.raises(pytest.UsageError):
        BrowserSelection(selection_config)


def items_for(config):
    """Use real parametrization metadata and neutral IDs like pytest items."""
    return [
        SimpleNamespace(
            path=config.rootpath / BROWSER_DIRECTORY / "test_probe.py",
            nodeid=node,
            callspec=SimpleNamespace(params={"browser_engine": engine}),
        )
        for node, engine in CASES
    ]


@pytest.mark.parametrize("value", ["2", "0/1", "2/1", "x/y"])
def test_invalid_partition_option_rejected(selection_config, value):
    """A malformed job selector fails before collection, never selects nothing."""
    selection_config.options["--ci-browser-partition"] = value
    with pytest.raises(pytest.UsageError, match="Invalid browser partition"):
        BrowserSelection(selection_config)


def test_collection_hook_keeps_exact_owner_and_reports(selection_config):
    """The public pytest deselection hook and visible count match retained items."""
    items = items_for(selection_config)
    removed = items[1:]
    BrowserSelection(selection_config).pytest_collection_modifyitems(items)
    assert [item.nodeid for item in items] == ["opaque-0"]
    selection_config.hook.pytest_deselected.assert_called_once_with(items=removed)
    reporter = selection_config.pluginmanager.get_plugin.return_value
    reporter.write_line.assert_called_once_with(
        "CI_BROWSER_PARTITION chromium: 1 out of 3 (33.3%) cases, estimated 8 s"
    )


@pytest.mark.parametrize("problem", ["outside", "missing", "unsupported"])
def test_collection_hook_rejects_unowned_items(selection_config, problem):
    """A new case outside the ownership contract must fail, never disappear."""
    items = items_for(selection_config)
    if problem == "outside":
        items[0].path = selection_config.rootpath / "other.py"
    elif problem == "missing":
        del items[0].callspec
    else:
        items[0].callspec.params["browser_engine"] = "other"
    with pytest.raises(pytest.UsageError):
        BrowserSelection(selection_config).pytest_collection_modifyitems(items)
    assert len(items) == 3


@pytest.fixture
def probe(tmp_path):
    """Load actual repository pytest hooks in a tiny credential-free suite."""
    directory = tmp_path / BROWSER_DIRECTORY
    directory.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests/conftest.py", tmp_path / "tests/conftest.py")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nDJANGO_SETTINGS_MODULE = parishkit.stewardship.settings.test\n"
    )
    (directory / "test_probe.py").write_text(
        "import os, pytest\n"
        "@pytest.fixture\ndef browser_engine(request):\n    return request.param\n"
        "@pytest.mark.parametrize('n', range(2))\n"
        "@pytest.mark.parametrize('browser_engine', "
        "['chromium', 'firefox', 'webkit'], indirect=True, ids=['a', 'b', 'c'])\n"
        "def test_probe(browser_engine, n):\n"
        "    mode = os.environ.get('PROBE_MODE')\n"
        "    if mode == 'skip':\n        pytest.skip('synthetic')\n"
        "    if mode == 'exit':\n        pytest.exit('synthetic', returncode=0)\n"
        "    assert mode != 'fail'\n"
    )
    return tmp_path


def run_probe(probe, *options, mode="pass"):
    """Execute the real selection and no-skips hooks, not mocked outcomes."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            BROWSER_DIRECTORY,
            "--ds=parishkit.stewardship.settings.test",
            "--require-no-skips",
            "--collection-manifest",
            "--ci-progress",
            "-q",
            *options,
        ],
        cwd=probe,
        env=environment() | {"PARISHKIT_RUN_BROWSER_TESTS": "1", "PROBE_MODE": mode},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def manifest(result):
    """Read the exact selected node IDs emitted by the repository root hook."""
    return json.loads(
        next(
            line.removeprefix("PARISHKIT_TEST_NODEIDS=")
            for line in result.stdout.splitlines()
            if line.startswith("PARISHKIT_TEST_NODEIDS=")
        )
    )


def test_real_partitions_equal_serial_suite(probe):
    """Three actual pytest runs cover the serial suite once despite opaque IDs."""
    serial = run_probe(probe)
    assert serial.returncode == 0, serial.stdout + serial.stderr
    groups = []
    for engine in BROWSER_ENGINES:
        result = run_probe(probe, f"--ci-browser-engine={engine}")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "2 passed, 4 deselected" in result.stdout
        assert "2 out of 6 (33.3%)" in result.stdout
        assert "CI_PROGRESS" in result.stdout and "elapsed=" in result.stdout
        groups.extend(manifest(result))
    assert len(groups) == 6
    assert sorted(groups) == sorted(manifest(serial))


def test_real_engine_jobs_equal_the_engine_share(probe):
    """Two actual pytest jobs of one engine run its share once, by whole file."""
    (probe / BROWSER_DIRECTORY / "test_second.py").write_text(
        (probe / BROWSER_DIRECTORY / "test_probe.py").read_text()
    )
    whole = run_probe(probe, "--ci-browser-engine=webkit")
    assert whole.returncode == 0, whole.stdout + whole.stderr
    jobs = []
    for index in (1, 2):
        result = run_probe(
            probe, "--ci-browser-engine=webkit", f"--ci-browser-partition={index}/2"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"CI_BROWSER_PARTITION webkit {index}/2: 2 out of 12" in result.stdout
        assert len({node.split("::")[0] for node in manifest(result)}) == 1
        jobs.extend(manifest(result))
    assert sorted(jobs) == sorted(manifest(whole))
    # Each job logs its WebKit estimate against its measured execution time.
    estimate = f"{browser_estimate(manifest(result), 'webkit'):,.0f}"
    assert re.search(
        rf"CI_BROWSER_TIMING webkit 2/2: estimated {estimate} s, actual \d+ s",
        result.stdout,
    )
    result = run_probe(
        probe, "--ci-browser-engine=webkit", "--ci-browser-partition=1/3"
    )
    assert result.returncode == 4
    assert "would select no tests" in result.stderr


@pytest.mark.parametrize(
    "mode,code,marker",
    [
        ("fail", 1, "2 failed, 4 deselected"),
        ("skip", 1, "A required verification was skipped."),
        ("module_skip", 2, "A required verification was skipped during collection."),
        ("exit", 1, "did not execute every selected assertion body"),
    ],
)
def test_actual_partition_failures_propagate(probe, mode, code, marker):
    """Assertions and per-case/module skips cannot produce a successful job."""
    if mode == "module_skip":
        (probe / BROWSER_DIRECTORY / "test_skipped.py").write_text(
            "import pytest\npytest.skip('synthetic', allow_module_level=True)\n"
        )
    result = run_probe(probe, "--ci-browser-engine=chromium", mode=mode)
    assert result.returncode == code, result.stdout + result.stderr
    assert marker in result.stdout
    assert "CI_BROWSER_PARTITION chromium: 2 out of 6" in result.stdout


@pytest.mark.parametrize(
    "selector",
    [
        "-k=a",
        "--ignore-glob=*probe.py",
        "--lf",
        "--sw-skip",
        "--sw-reset",
        "--markers",
        "--collect-only",
        "--setup-only",
        "--setup-plan",
        "-opython_files=test_probe.py",
        "-opython_functions=test_probe",
    ],
)
def test_real_partial_selectors_fail_before_collection(probe, selector):
    """Pytest's actual option destinations reach the strict profile guard."""
    result = run_probe(probe, "--ci-browser-engine=chromium", selector)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "complete opted-in browser suite" in result.stderr


def test_real_diagnostic_timeout_override_allowed(probe):
    """The workflow's stack-dump setting does not alter collection or execution."""
    result = run_probe(
        probe, "--ci-browser-engine=webkit", "-ofaulthandler_timeout=120"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed, 4 deselected" in result.stdout


def test_browser_workflow_contract():
    """Parallel jobs preserve all engines and the always-running protected gate."""
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    job = jobs["stewardship-browser-engine"]
    # Exactly BROWSER_JOBS, whose coverage the test above proves.
    assert job["strategy"] == {
        "fail-fast": False,
        "matrix": {"runs": list(BROWSER_JOBS)},
    }
    assert job["timeout-minutes"] == 60
    assert job["env"] == {"BROWSER_RUNS": "${{ matrix.runs }}"}
    install, run = job["steps"][-2:]
    assert 'read -ra runs <<<"$BROWSER_RUNS"' in install["run"]
    # The container image has the OS packages, so nothing installs them (#2053).
    assert 'python -m playwright install "${runs[@]%%:*}"' in install["run"]
    assert "--with-deps" not in install["run"]
    assert run["env"]["PARISHKIT_RUN_BROWSER_TESTS"] == "1"
    assert run["run"] == (
        'python -m parishkit.stewardship.quality_browser --runs "$BROWSER_RUNS"'
    )
    gate = jobs["stewardship-browser"]
    assert gate["needs"] == ["validate", "stewardship-browser-engine"]
    assert gate["if"] == "${{ always() && github.event_name != 'push' }}"
    assert len(gate["steps"]) == 1
    assert gate["steps"][0]["env"] == {
        "BROWSER_RESULT": "${{ needs.stewardship-browser-engine.result }}",
        "VALIDATE_RESULT": "${{ needs.validate.result }}",
        "PATH_RUN": "${{ needs.validate.outputs.browser }}",
        "EVENT": "${{ github.event_name }}",
        "DRAFT": "${{ github.event.pull_request.draft }}",
        "JOBS": "${{ inputs.jobs }}",
    }
    assert_gate_truth_table(gate["steps"][0], GATES["stewardship-browser"])


def preflight_browser_image():
    """Return tools/stewardship-preflight.py's BROWSER_IMAGE_BASE value."""
    tree = ast.parse((ROOT / "tools/stewardship-preflight.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "BROWSER_IMAGE_BASE"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("BROWSER_IMAGE_BASE not found")


def test_browser_jobs_run_in_the_pinned_playwright_image():
    """Engine jobs use the preflight's digest-pinned image, never apt (#2053).

    The image's browsers must be the ones the playwright pin expects, or
    ``playwright install`` would quietly download them again.
    """
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = yaml.safe_load(workflow)["jobs"]["stewardship-browser-engine"]
    image = job["container"]["image"]
    assert image == preflight_browser_image()
    assert re.fullmatch(
        r"mcr\.microsoft\.com/playwright/python:v(\d+\.\d+\.\d+)-noble"
        r"@sha256:[0-9a-f]{64}",
        image,
    )
    version = re.search(r":v([\d.]+)-", image).group(1)
    pins = (ROOT / "requirements/stewardship-browser.txt").read_text()
    assert f"\nplaywright=={version}\n" in pins
    assert job["container"]["options"] == "--ipc=host"
    fonts = "/usr/share/fonts/truetype/dejavu"
    assert job["container"]["volumes"] == [f"{fonts}:{fonts}"]
    assert "--with-deps" not in workflow
    # Container steps default to sh, which rejects the steps' bash features.
    assert job["defaults"]["run"]["shell"] == "bash -e {0}"
    assert "sudo" not in yaml.safe_dump(job)
    names = [step.get("name") for step in job["steps"]]
    assert "Remove the Microsoft apt source" not in names
    font_check = job["steps"][names.index("Require the runner's DejaVu fonts")]
    assert 'match=$(fc-match "$family")' in font_check["run"]
    assert "for family in sans-serif serif monospace; do" in font_check["run"]
    # The font check precedes the tests it protects.
    assert names.index("Require the runner's DejaVu fonts") < len(names) - 2
    # setup-python needs lsb_release, which the image lacks, so the job uses
    # the image's Python in a virtualenv instead. setup-node stays: no Node.
    uses = [step.get("uses", "") for step in job["steps"]]
    assert not any(u.startswith("actions/setup-python") for u in uses)
    assert any(u.startswith("actions/setup-node") for u in uses)
    venv = job["steps"][names.index("Use the image's Python 3.12 in a virtualenv")]
    assert '"Python 3.12."*' in venv["run"]
    assert "virtualenv" in venv["run"]
    assert '>> "$GITHUB_PATH"' in venv["run"]
    assert names.index("Use the image's Python 3.12 in a virtualenv") < names.index(
        "Install component test dependencies"
    )


def test_ci_cancels_only_superseded_pr_heads():
    """Keep main evidence independent and omit duplicate merge-queue runs.

    Marking a draft ready starts no run (#624). A newer push to main cancels
    an older push's run, so only the last push of a train's merge burst runs
    main-tree-evidence (#158); a dispatched full run, including the backfill
    that job starts, has its own group and is never cancelled. No job may
    override the workflow's group, or that debounce would not hold.
    """
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAML's YAML 1.1 resolver treats the Actions `on` key as a boolean.
    assert workflow[True] == {
        "pull_request": {
            "types": [
                "opened",
                "synchronize",
                "reopened",
                "converted_to_draft",
            ]
        },
        "push": {"branches": ["main"]},
        # Its "jobs" input is pinned in test_quality_paths.py.
        "workflow_dispatch": {"inputs": {"jobs": ANY}},
    }
    assert workflow["concurrency"] == {
        "group": (
            "${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event.pull_request.number || "
            "(github.event_name == 'push' && github.ref) || github.run_id }}"
        ),
        "cancel-in-progress": "${{ github.event_name != 'workflow_dispatch' }}",
    }
    assert not [name for name, job in workflow["jobs"].items() if "concurrency" in job]
    # On a push, only main-tree-evidence runs: the browser engines need
    # validate, which a push skips, and the gate skips a push itself.
    assert workflow["jobs"]["validate"]["if"] == "${{ github.event_name != 'push' }}"
    assert workflow["jobs"]["stewardship-browser-engine"]["needs"] == "validate"


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"engine": "chromium", "selected": ["a"], "executed": ["a"]},
        {
            "engine": "firefox",
            "partition": [1, 1],
            "selected": ["a"],
            "executed": ["a"],
        },
        {
            "engine": "chromium",
            "partition": [1, 2],
            "selected": ["a"],
            "executed": ["a"],
        },
        {"engine": "chromium", "partition": [1, 1], "selected": [], "executed": []},
        {"engine": "chromium", "partition": [1, 1], "selected": ["a"], "executed": []},
        {
            "engine": "chromium",
            "partition": [1, 1],
            "selected": ["a", "a"],
            "executed": ["a", "a"],
        },
        {"engine": "chromium", "partition": [1, 1], "selected": [1], "executed": [1]},
        {
            "engine": "chromium",
            "partition": [1, 1],
            "selected": [["a"]],
            "executed": [["a"]],
        },
        {"engine": "chromium", "partition": [1, 1], "selected": [""], "executed": [""]},
    ],
)
def test_invalid_completion_receipt_rejected(tmp_path, value):
    """An unrelated or partial receipt cannot prove a complete engine run."""
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="valid completion evidence"):
        quality_browser.validate_receipt(receipt, "chromium")


def test_missing_malformed_and_valid_receipts(tmp_path):
    """Require a readable structured completion receipt, not subprocess status."""
    receipt = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="valid completion evidence"):
        quality_browser.validate_receipt(receipt, "chromium")
    receipt.write_text("not json")
    with pytest.raises(ValueError, match="valid completion evidence"):
        quality_browser.validate_receipt(receipt, "chromium")
    receipt.write_text(
        json.dumps(
            {
                "engine": "chromium",
                "partition": [1, 1],
                "selected": ["a"],
                "executed": ["a"],
            }
        )
    )
    assert quality_browser.validate_receipt(receipt, "chromium") == 1
    # A receipt from one job of a split engine cannot prove another job.
    with pytest.raises(ValueError, match="valid completion evidence"):
        quality_browser.validate_receipt(receipt, "chromium", 2, 2)


@pytest.mark.parametrize("mode", ["pass", "fail", "skip", "exit"])
def test_actual_external_runner_requires_completion(probe, monkeypatch, capfd, mode):
    """Use actual child pytest, fresh receipts and parent-side validation."""
    monkeypatch.setenv("PROBE_MODE", mode)
    if mode == "pass":
        quality_browser.run_engine(probe, "chromium")
        assert (
            "CI_BROWSER_COMPLETE chromium: 2 executed cases" in capfd.readouterr().out
        )
    else:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            quality_browser.run_engine(probe, "chromium")
        assert caught.value.returncode == 1
        # Both chromium cases fail in "fail" mode, and --require-no-skips
        # fails both skips; "exit" fails no test phase. The summary lists
        # these node ids (#2001).
        assert len(caught.value.failures) == (0 if mode == "exit" else 2)


@pytest.mark.parametrize("option", ["--version", "--help", "--markers"])
def test_real_early_exit_cannot_pass_external_runner(probe, option, capfd, monkeypatch):
    """Real early pytest exits cannot bypass the parent completion gate."""
    if option == "--version":
        # Pytest handles version before reading addopts; inject it into the
        # real child argv to prove a future accidental command edit fails shut.
        original_popen = subprocess.Popen
        monkeypatch.setattr(
            quality_browser.subprocess,
            "Popen",
            lambda command, **kwargs: original_popen([*command, option], **kwargs),
        )
    else:
        ini = probe / "pytest.ini"
        ini.write_text(ini.read_text() + f"addopts = {option}\n")
    if option == "--markers":
        with pytest.raises(subprocess.CalledProcessError) as caught:
            quality_browser.run_engine(probe, "chromium")
        assert caught.value.returncode == 4
    else:
        with pytest.raises(ValueError, match="valid completion evidence"):
            quality_browser.run_engine(probe, "chromium")
    assert "CI_BROWSER_COMPLETE" not in capfd.readouterr().out


class HungPytest:
    """A Popen stand-in whose bounded wait times out, like a hung child pytest.

    BEFORE_TIMEOUT(command), if given, runs first, as the child would.
    """

    def __init__(self, command, before_timeout=None):
        """Remember the command; nothing is launched."""
        self.args = command
        self.pid = 4242
        self.waits = []
        self.before_timeout = before_timeout

    def wait(self, timeout=None):
        """Time out on the bounded wait; the reap after the kill succeeds."""
        self.waits.append(timeout)
        if timeout is None:
            return -9
        if self.before_timeout:
            self.before_timeout(self.args)
        raise subprocess.TimeoutExpired("pytest", timeout)


def test_runner_environment_and_timeout(probe, monkeypatch):
    """Inherited pytest selectors are scrubbed and the child has a bounded runtime.

    A budget kill hits the child's whole process group, then reaps pytest.
    """
    monkeypatch.setenv("PYTEST_ADDOPTS", "--version")
    limit = quality_browser.JOB_SECONDS
    child = []

    def popen(command, **kwargs):
        """Hand back one hung child."""
        child.append(HungPytest(command))
        return child[-1]

    run = Mock(side_effect=popen)
    killpg = Mock()
    monkeypatch.setattr(quality_browser.subprocess, "Popen", run)
    monkeypatch.setattr(quality_browser.os, "killpg", killpg)
    with pytest.raises(
        quality_browser.BudgetExhausted, match="no test had failed"
    ) as caught:
        quality_browser.run_engine(probe, "firefox")
    assert caught.value.failures == [] and caught.value.running is None
    assert caught.value.limit == limit
    killpg.assert_called_once_with(4242, signal.SIGKILL)
    assert child[0].waits == [limit, None]
    assert run.call_args.kwargs["start_new_session"] is True
    command = run.call_args.args[0]
    assert "--ci-browser-engine=firefox" in command
    assert "--ci-browser-partition=1/1" in command
    assert "--require-no-skips" in command and "--ci-progress" in command
    receipt = Path(
        next(
            arg.split("=", 1)[1]
            for arg in command
            if arg.startswith("--ci-browser-evidence=")
        )
    )
    assert not receipt.exists() and not receipt.parent.exists()
    assert "PYTEST_ADDOPTS" not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["env"]["PARISHKIT_RUN_BROWSER_TESTS"] == "1"
    with pytest.raises(ValueError, match="Unsupported browser engine"):
        quality_browser.run_engine(probe, "other")
    assert run.call_count == 1


@pytest.mark.parametrize("inside", [False, True])
def test_receipt_must_be_new_and_external(selection_config, tmp_path, inside):
    """Neither retained evidence nor a checkout file may be overwritten."""
    selection_config.rootpath = tmp_path / "checkout"
    selection_config.args = [str(selection_config.rootpath / BROWSER_DIRECTORY)]
    receipt = tmp_path / "existing.json"
    receipt.write_text("retained")
    if inside:
        receipt = selection_config.rootpath / "new.json"
    selection_config.options["--ci-browser-evidence"] = str(receipt)
    with pytest.raises(pytest.UsageError, match="new external path"):
        BrowserSelection(selection_config)


def test_orphan_browser_receipt_option_is_rejected(probe):
    """A receipt option without an engine cannot run unrelated tests as proof."""
    result = run_probe(probe, "--ci-browser-evidence=receipt.json")
    assert result.returncode == 4
    assert "Browser evidence requires an engine partition" in result.stderr
    result = run_probe(probe, "--ci-browser-partition=1/2")
    assert result.returncode == 4
    assert "A browser partition requires an engine" in result.stderr


def test_split_engine_runner_requires_its_own_job(probe, capfd):
    """The runner passes INDEX/COUNT through and labels the job it proved."""
    (probe / BROWSER_DIRECTORY / "test_second.py").write_text(
        (probe / BROWSER_DIRECTORY / "test_probe.py").read_text()
    )
    quality_browser.run_engine(probe, "webkit", 2, 2)
    assert "CI_BROWSER_COMPLETE webkit 2/2: 2 executed cases" in capfd.readouterr().out


@pytest.mark.parametrize("engine", BROWSER_ENGINES)
def test_browser_estimate_sums_engine_costs_and_file_extras(engine):
    """Each file costs its engine's fixed and per-case seconds plus any extra."""
    per_file, per_case = BROWSER_ENGINE_SECONDS[engine]
    known = next(iter(BROWSER_FILE_EXTRA_SECONDS))
    nodes = [f"tests/x/{known}::a", f"tests/x/{known}::b"]
    nodes += [f"tests/x/test_new.py::{n}" for n in "abc"]
    assert browser_estimate(nodes, engine) == pytest.approx(
        2 * per_file + 5 * per_case + BROWSER_FILE_EXTRA_SECONDS[known]
    )
    assert browser_estimate([], engine) == 0


def test_browser_estimates_are_engine_scale():
    """Chromium is the fastest engine and WebKit, about 1.6 times Chromium
    (#1151), the slowest; Firefox, refitted in #1380, falls between them."""
    nodes = [f"tests/x/test_{n}.py::case[{c}]" for n in range(20) for c in range(8)]
    chromium, firefox, webkit = (
        browser_estimate(nodes, engine) for engine in BROWSER_ENGINES
    )
    assert chromium < firefox < webkit
    assert 1.4 < webkit / chromium < 2


@pytest.mark.parametrize(
    "error",
    [subprocess.CalledProcessError(1, "pytest"), FileNotFoundError("python")],
)
def test_job_runs_every_partition_within_its_budget(
    monkeypatch, capfd, tmp_path, error
):
    """A failed or unlaunchable partition is reported, later ones still run.

    The first partition may not use the second's reserved half; the second
    gets its half plus whatever the first left unused.
    """
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    clock = iter([0, 100, 600])
    monkeypatch.setattr(quality_browser.time, "monotonic", lambda: next(clock))
    calls = []

    def run_engine(root, engine, index, count, timeout):
        """Record the call; the first partition fails."""
        calls.append((engine, index, count, timeout))
        if len(calls) == 1:
            raise error

    monkeypatch.setattr(quality_browser, "run_engine", run_engine)
    runs = [("chromium", 1, 2), ("webkit", 1, 2)]
    assert quality_browser.run_job(ROOT, runs) == ["chromium 1/2"]
    limit = quality_browser.JOB_SECONDS
    assert calls == [
        ("chromium", 1, 2, limit / 2 - 100),
        ("webkit", 1, 2, limit - 600),
    ]
    assert "CI_BROWSER_FAILED chromium 1/2" in capfd.readouterr().out
    assert summary.read_text() == (
        f"### Other failed browser partitions\n\n- chromium 1/2: {error}\n\n"
    )


@pytest.mark.parametrize(
    "remaining,later,count,expected",
    [
        (3000, 0, 1, 3000),  # A one-partition job gets the whole budget.
        (3000, 1, 2, 1500),  # The first of two keeps the second's half free.
        (2170, 0, 2, 2170),  # The last gets its half plus unused time.
        (1400, 1, 2, 1),  # Overrun reserves never yield a zero timeout.
        (3000, 2, 3, 1000),
    ],
)
def test_partition_limit_reserves_later_shares(remaining, later, count, expected):
    """A partition never eats into the budget reserved for later partitions."""
    assert quality_browser.JOB_SECONDS == 3000
    assert quality_browser.partition_limit(remaining, later, count) == expected


@pytest.mark.parametrize("job", BROWSER_JOBS)
def test_webkit_runs_last_in_its_job(job):
    """WebKit, the slowest engine, inherits what the faster partition leaves."""
    engines = [engine for engine, _, _ in parse_browser_runs(job)]
    assert "webkit" not in engines[:-1]


def run_job_timed(monkeypatch, seconds):
    """Run a chromium-then-webkit job where partition N needs SECONDS[N].

    A partition that would outrun its limit is killed at the limit. Return
    the limits run_job gave, and the labels it reported as failed.
    """
    now = [0.0]
    monkeypatch.setattr(quality_browser.time, "monotonic", lambda: now[0])
    calls = []

    def run_engine(root, engine, index, count, timeout):
        """Spend this partition's time, or its whole limit and fail."""
        calls.append(timeout)
        needed = seconds[len(calls) - 1]
        now[0] += min(needed, timeout)
        if needed > timeout:
            raise quality_browser.BudgetExhausted(
                "killed", (), "t.py::slow", timeout, timeout
            )

    monkeypatch.setattr(quality_browser, "run_engine", run_engine)
    runs = [("chromium", 1, 2), ("webkit", 1, 2)]
    return calls, quality_browser.run_job(ROOT, runs)


def test_slow_webkit_last_gets_what_chromium_left(monkeypatch):
    """The 2026-10-12 run: WebKit 1/2 needed 1,451 s on a slow runner.

    Running last after a Chromium half of about 830 s, it now has enough.
    """
    calls, failed = run_job_timed(monkeypatch, [830, 1451])
    assert failed == []
    assert calls[0] == 1500
    assert calls[1] == 3000 - 830 >= 1451


def test_slow_first_partition_cannot_starve_the_second(monkeypatch, capfd):
    """A hung first partition is killed at its share; the second keeps its own."""
    calls, failed = run_job_timed(monkeypatch, [99999, 900])
    assert failed == ["chromium 1/2"]
    assert calls == [1500, 1500]
    out = capfd.readouterr().out
    assert (
        "CI_BROWSER_BUDGET_KILLED chromium 1/2: running t.py::slow after 1,500 s "
        "(budget 1,500 s)\n"
    ) in out
    assert "CI_BROWSER_FAILED" not in out


def test_job_summary_puts_failed_tests_first_and_kills_apart(
    monkeypatch, capfd, tmp_path
):
    """A budget kill with no failed test is told apart from test failures.

    Failed node ids lead the summary; a clean budget kill names its running
    test; a kill after a failure stays a failure; every partition fails the
    job (#2001).
    """
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    failed = subprocess.CalledProcessError(1, "pytest")
    failed.failures = ["t.py::a", "t.py::a"]
    errors = iter(
        [
            quality_browser.BudgetExhausted("slow", (), "t.py::b", 1727.2, 1727),
            quality_browser.BudgetExhausted("hung", ["t.py::c"], "t.py::d", 900, 900),
            failed,
            quality_browser.BudgetExhausted("early", (), None, 5, 900),
        ]
    )

    def run_engine(root, engine, index, count, timeout):
        """Every partition fails, each in its own way."""
        raise next(errors)

    monkeypatch.setattr(quality_browser, "run_engine", run_engine)
    runs = [("webkit", n, 4) for n in range(1, 5)]
    assert quality_browser.run_job(ROOT, runs) == [
        f"webkit {n}/4" for n in (1, 2, 3, 4)
    ]
    out = capfd.readouterr().out
    assert (
        "CI_BROWSER_BUDGET_KILLED webkit 1/4: running t.py::b after 1,727 s "
        "(budget 1,727 s)\n"
    ) in out
    assert "CI_BROWSER_FAILED webkit 2/4: hung\n" in out
    assert "CI_BROWSER_FAILED webkit 3/4: " in out
    assert "CI_BROWSER_BUDGET_KILLED webkit 4/4: running no test yet after 5 s" in out
    assert summary.read_text() == (
        "### Failed browser tests\n\n"
        "- `t.py::c` (webkit 2/4)\n"
        "- `t.py::a` (webkit 3/4)\n\n"
        "### Browser partitions killed by their time budget with no failed test "
        "(likely a slow runner)\n\n"
        "- webkit 1/4: running t.py::b after 1,727 s (budget 1,727 s)\n"
        "- webkit 4/4: running no test yet after 5 s (budget 900 s)\n\n"
        "### Other failed browser partitions\n\n"
        "- webkit 2/4: hung\n"
        f"- webkit 3/4: {failed}\n\n"
    )


def test_job_summary_survives_an_interrupt_in_a_later_partition(monkeypatch, tmp_path):
    """A cancel or job timeout mid-run keeps the earlier failures in the summary."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    outcomes = iter([quality_browser.BudgetExhausted("hung", ["t.py::c"], None, 9, 9)])

    def run_engine(root, engine, index, count, timeout):
        """The first partition fails; the second is interrupted (SIGINT)."""
        raise next(outcomes, KeyboardInterrupt())

    monkeypatch.setattr(quality_browser, "run_engine", run_engine)
    with pytest.raises(KeyboardInterrupt):
        quality_browser.run_job(ROOT, [("webkit", 1, 2), ("webkit", 2, 2)])
    assert "- `t.py::c` (webkit 1/2)\n" in summary.read_text()


@pytest.mark.parametrize(
    "failures,tail",
    [
        ([], "; no test had failed, so rerun the job"),
        (["a::b", "c::d"], "; 2 test phase(s) had already failed: a::b, c::d"),
    ],
)
def test_timeout_message_names_budget_and_failures(failures, tail):
    """The kill message names the budget, the elapsed time and any failures."""
    assert quality_browser.timeout_message(1350, 1350.4, failures) == (
        "killed after 1,350 s when its 1,350 s partition budget "
        "(its share of the 3,000 s job budget) ran out" + tail
    )


def test_budget_kill_reports_failures_recorded_before_it(probe, monkeypatch):
    """A child that failed a test and then hung is not reported as clean."""

    def fail_one(command):
        """Record a failure the way the child plugin does."""
        receipt = Path(
            next(a for a in command if a.startswith("--ci-browser-evidence=")).split(
                "=", 1
            )[1]
        )
        failures_path(receipt).write_text("tests/x.py::a\n")
        running_path(receipt).write_text("tests/x.py::b\n")

    monkeypatch.setattr(
        quality_browser.subprocess,
        "Popen",
        lambda command, **kwargs: HungPytest(command, fail_one),
    )
    monkeypatch.setattr(quality_browser.os, "killpg", Mock())
    with pytest.raises(quality_browser.BudgetExhausted) as caught:
        quality_browser.run_engine(probe, "webkit", 1, 2, 900)
    message = str(caught.value)
    assert "its 900 s partition budget" in message
    assert message.endswith("1 test phase(s) had already failed: tests/x.py::a")
    assert caught.value.failures == ["tests/x.py::a"]
    assert caught.value.running == "tests/x.py::b"
    assert caught.value.limit == 900


def test_plugin_records_failed_phases_beside_the_receipt(
    selection_config, tmp_path_factory
):
    """Failed phases are appended as they happen; passes are not recorded."""
    # Outside the fixture's checkout (tmp_path), like the runner's receipts.
    receipt = tmp_path_factory.mktemp("receipt") / "receipt.json"
    selection_config.options["--ci-browser-evidence"] = str(receipt)
    plugin = BrowserSelection(selection_config)
    for nodeid, when, outcome in [
        ("a", "call", "passed"),
        ("b", "setup", "failed"),
        ("c", "call", "failed"),
    ]:
        plugin.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=nodeid,
                when=when,
                passed=outcome == "passed",
                failed=outcome == "failed",
            )
        )
    assert failures_path(receipt).read_text() == "b\nc\n"
    assert plugin.executed == ["a"] and not receipt.exists()


def test_plugin_names_the_running_test_beside_the_receipt(
    selection_config, tmp_path_factory
):
    """Each test start overwrites the running sidecar with its node id."""
    receipt = tmp_path_factory.mktemp("receipt") / "receipt.json"
    selection_config.options["--ci-browser-evidence"] = str(receipt)
    plugin = BrowserSelection(selection_config)
    for nodeid in ("a", "b"):
        plugin.pytest_runtest_logstart(nodeid, None)
    assert running_path(receipt).read_text() == "b\n" and not receipt.exists()


@pytest.mark.parametrize("runs,code", [("webkit", 2), ("firefox:1/1", 1)])
def test_main_rejects_bad_jobs_and_failed_partitions(monkeypatch, runs, code):
    """A malformed job is a usage error; any failed partition fails the job."""
    monkeypatch.setattr(sys, "argv", ["quality_browser", "--runs", runs])
    monkeypatch.setattr(quality_browser, "run_job", lambda root, runs: ["firefox"])
    with pytest.raises(SystemExit) as caught:
        quality_browser.main()
    assert caught.value.code == code


@pytest.mark.parametrize("name", list(BROWSER_DISCOVERY))
def test_real_ini_discovery_override_rejected(probe, name):
    """Public effective configuration catches narrowing stored in an INI file."""
    ini = probe / "pytest.ini"
    ini.write_text(ini.read_text() + f"{name} = partial\n")
    result = run_probe(probe, "--ci-browser-engine=chromium")
    assert result.returncode == 4
    assert "complete opted-in browser suite" in result.stderr


def test_kill_group_reaches_grandchildren():
    """A budget kill also ends processes the child started, like browsers."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(60)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    grandchild = int(process.stdout.readline())
    quality_browser.kill_group(process)
    process.stdout.close()
    assert process.returncode == -signal.SIGKILL
    # The orphaned grandchild is reaped by init once killed; wait briefly.
    for _ in range(100):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("grandchild survived the process-group kill")


def test_kill_group_tolerates_an_exited_group(monkeypatch):
    """A group that exited between the timeout and the kill is not an error."""
    monkeypatch.setattr(
        quality_browser.os, "killpg", Mock(side_effect=ProcessLookupError)
    )
    process = Mock()
    quality_browser.kill_group(process)
    process.wait.assert_called_once_with()
