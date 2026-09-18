"""Browser CI partitions preserve the complete suite and fail closed."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from parishkit.stewardship import quality_browser
from parishkit.stewardship.quality_ci import environment
from parishkit.stewardship.quality_pytest import BROWSER_DISCOVERY, BrowserSelection
from parishkit.stewardship.quality_sharding import BROWSER_ENGINES, browser_partition

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


def test_collection_hook_keeps_exact_owner_and_reports(selection_config):
    """The public pytest deselection hook and visible count match retained items."""
    items = items_for(selection_config)
    removed = items[1:]
    BrowserSelection(selection_config).pytest_collection_modifyitems(items)
    assert [item.nodeid for item in items] == ["opaque-0"]
    selection_config.hook.pytest_deselected.assert_called_once_with(items=removed)
    reporter = selection_config.pluginmanager.get_plugin.return_value
    reporter.write_line.assert_called_once_with(
        "CI_BROWSER_PARTITION chromium: 1 out of 3 (33.3%) cases"
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
    assert job["strategy"] == {
        "fail-fast": False,
        "matrix": {"engine": list(BROWSER_ENGINES)},
    }
    assert job["timeout-minutes"] == 15
    assert job["env"]["BROWSER_ENGINE"] == "${{ matrix.engine }}"
    install, run = job["steps"][-2:]
    assert 'playwright install --with-deps "$BROWSER_ENGINE"' in install["run"]
    assert run["env"]["PARISHKIT_RUN_BROWSER_TESTS"] == "1"
    assert run["run"] == (
        'python -m parishkit.stewardship.quality_browser --engine "$BROWSER_ENGINE"'
    )
    gate = jobs["stewardship-browser"]
    assert gate["needs"] == "stewardship-browser-engine"
    assert gate["if"] == "${{ always() }}"
    assert gate["steps"] == [
        {
            "name": "Require every browser engine",
            "env": {"BROWSER_RESULT": "${{ needs.stewardship-browser-engine.result }}"},
            "run": 'test "$BROWSER_RESULT" = success',
        }
    ]
    for result in ("success", "failure", "cancelled", "skipped", ""):
        completed = subprocess.run(
            ["sh", "-c", gate["steps"][0]["run"]],
            env={"BROWSER_RESULT": result},
            check=False,
            timeout=5,
        )
        assert (completed.returncode == 0) is (result == "success")


def test_ci_cancels_only_superseded_pr_heads():
    """Keep main evidence independent and omit duplicate merge-queue runs."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAML's YAML 1.1 resolver treats the Actions `on` key as a boolean.
    assert workflow[True] == {
        "pull_request": None,
        "push": {"branches": ["main"]},
    }
    assert workflow["concurrency"] == {
        "group": (
            "${{ github.workflow }}-"
            "${{ github.event.pull_request.number || github.run_id }}"
        ),
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"engine": "firefox", "selected": ["a"], "executed": ["a"]},
        {"engine": "chromium", "selected": [], "executed": []},
        {"engine": "chromium", "selected": ["a"], "executed": []},
        {"engine": "chromium", "selected": ["a", "a"], "executed": ["a", "a"]},
        {"engine": "chromium", "selected": [1], "executed": [1]},
        {"engine": "chromium", "selected": [["a"]], "executed": [["a"]]},
        {"engine": "chromium", "selected": [""], "executed": [""]},
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
        json.dumps({"engine": "chromium", "selected": ["a"], "executed": ["a"]})
    )
    assert quality_browser.validate_receipt(receipt, "chromium") == 1


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


@pytest.mark.parametrize("option", ["--version", "--help", "--markers"])
def test_real_early_exit_cannot_pass_external_runner(probe, option, capfd, monkeypatch):
    """Real early pytest exits cannot bypass the parent completion gate."""
    if option == "--version":
        # Pytest handles version before reading addopts; inject it into the
        # real child argv to prove a future accidental command edit fails shut.
        original_run = subprocess.run
        monkeypatch.setattr(
            quality_browser.subprocess,
            "run",
            lambda command, **kwargs: original_run([*command, option], **kwargs),
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


def test_runner_environment_and_timeout(probe, monkeypatch):
    """Inherited pytest selectors are scrubbed and the child has a bounded runtime."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "--version")
    run = Mock(side_effect=subprocess.TimeoutExpired("pytest", 840))
    monkeypatch.setattr(quality_browser.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        quality_browser.run_engine(probe, "firefox")
    command = run.call_args.args[0]
    assert "--ci-browser-engine=firefox" in command
    assert "--require-no-skips" in command and "--ci-progress" in command
    receipt = Path(
        next(
            arg.split("=", 1)[1]
            for arg in command
            if arg.startswith("--ci-browser-evidence=")
        )
    )
    assert not receipt.exists() and not receipt.parent.exists()
    assert run.call_args.kwargs["timeout"] == 840
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


@pytest.mark.parametrize("name", list(BROWSER_DISCOVERY))
def test_real_ini_discovery_override_rejected(probe, name):
    """Public effective configuration catches narrowing stored in an INI file."""
    ini = probe / "pytest.ini"
    ini.write_text(ini.read_text() + f"{name} = partial\n")
    result = run_probe(probe, "--ci-browser-engine=chromium")
    assert result.returncode == 4
    assert "complete opted-in browser suite" in result.stderr
