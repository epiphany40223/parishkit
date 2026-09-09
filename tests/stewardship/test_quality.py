"""Coverage manifest validation and independent, non-blended quality floors."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from parishkit.stewardship import quality


@pytest.mark.parametrize(
    "arguments",
    [
        ["--dry-run"],
        ["--config", "unused.yaml"],
        ["--debug"],
        ["--slack-token-file", "unused"],
        ["--ps-api-key-file", "unused"],
        ["--rep", "unused.json"],
    ],
)
def test_runner_rejects_unused_flags_before_launch(tmp_path, monkeypatch, arguments):
    """Unconsumed flags cannot launch pytest or create a misleading report."""
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        quality.main(["--report", str(report), *arguments])
    assert exc.value.code == 2
    run.assert_not_called()
    assert not report.exists()


def test_runner_help_only_advertises_implemented_flags(capsys):
    """The quality runner has no shared runtime or provider option consumers."""
    with pytest.raises(SystemExit) as exc:
        quality.main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--repository-root" in help_text
    assert "--report" in help_text
    for flag in ("--config", "--dry-run", "--debug", "--slack", "--ps-", "--log-"):
        assert flag not in help_text


@pytest.fixture
def repository(tmp_path):
    """Build a tiny repository with one package file and one shared module."""
    root = tmp_path.resolve() / "repository"
    package = root / quality.PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("value = 1\n")
    (root / "src/parishkit/shared.py").write_text("value = 2\n")
    (root / "coverage-stewardship.toml").write_text(
        'schema_version = 1\nshared_modules = ["src/parishkit/shared.py"]\n'
    )
    return root


def test_real_manifest_requires_package_and_extended_shared_modules():
    """Stewardship cannot be removed, and its initial shared changes are owned."""
    from pathlib import Path

    scope = quality.load_scope(Path(__file__).resolve().parents[2])
    assert scope.modules == (
        "parishkit.stewardship",
        "parishkit.cli",
        "parishkit.cli_arguments",
        "parishkit.config",
        "parishkit.paths",
    )
    assert "src/parishkit/stewardship/quality.py" in scope.files


@pytest.mark.parametrize(
    "body",
    [
        "",
        "schema_version = true\nshared_modules = []",
        "schema_version = 2\nshared_modules = []",
        'schema_version = 1\nshared_modules = "file"',
        'schema_version = 1\nshared_modules = []\npackage = "elsewhere"',
    ],
)
def test_manifest_shape(repository, body):
    """Missing fields, types, unsupported versions, and package overrides fail."""
    (repository / "coverage-stewardship.toml").write_text(body)
    with pytest.raises(ValueError):
        quality.load_scope(repository)


@pytest.mark.parametrize(
    "paths",
    [
        ["src/parishkit/shared.py", "src/parishkit/shared.py"],
        ["src/parishkit/absent.py"],
        ["src/parishkit/shared.txt"],
        ["/tmp/outside.py"],
        ["src/parishkit/../outside.py"],
        ["src/parishkit/./shared.py"],
        ["src/parishkit//shared.py"],
        ["src/parishkit\\shared.py"],
        ["src/parishkit/stewardship/__init__.py"],
        [1],
        [True],
    ],
)
def test_invalid_shared_paths(repository, paths):
    """No missing, repeated, non-Python, aliased, or escaping source is accepted."""
    (repository / "coverage-stewardship.toml").write_text(
        "schema_version = 1\nshared_modules = " + json.dumps(paths)
    )
    with pytest.raises(ValueError):
        quality.load_scope(repository)


def test_missing_package(repository):
    """The package is mandatory even with an otherwise valid manifest."""
    (repository / quality.PACKAGE / "__init__.py").unlink()
    with pytest.raises(ValueError, match="package is required"):
        quality.load_scope(repository)


def test_symlinked_shared_module(repository, tmp_path):
    """Symlink aliases cannot redirect measurement outside the declared scope."""
    original = repository / "src/parishkit/shared.py"
    original.unlink()
    original.symlink_to(repository / quality.PACKAGE / "__init__.py")
    with pytest.raises(ValueError):
        quality.load_scope(repository)


def report_data(scope, lines=80, branches=80):
    """Create independent counters; the deliberately bogus blended total is ignored."""
    return {
        "meta": {"branch_coverage": True},
        "totals": {"percent_covered": 100},
        "files": {
            name: {
                "summary": {
                    "covered_lines": lines,
                    "num_statements": 100,
                    "covered_branches": branches,
                    "num_branches": 100,
                }
            }
            for name in scope.files
        },
    }


@pytest.mark.parametrize(
    ("lines", "branches", "expected"),
    [
        (80, 80, 0),
        (79, 100, 1),
        (100, 79, 1),
        (100, 100, 0),
    ],
)
def test_quality_runner_independent_floors(
    repository, monkeypatch, lines, branches, expected
):
    """Either floor can fail despite excellent coverage in the other dimension."""
    report = repository.parent / "report.json"
    scope = quality.load_scope(repository)

    def emit_report(*args, **kwargs):
        """Produce fresh measurement only when the simulated pytest is launched."""
        report.write_text(json.dumps(report_data(scope, lines, branches)))
        return subprocess.CompletedProcess([], 0)

    run = Mock(side_effect=emit_report)
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert (
        quality.main(["--repository-root", str(repository), "--report", str(report)])
        == expected
    )
    arguments = run.call_args.args[0]
    assert "--cov-branch" in arguments
    assert "--cov=parishkit.stewardship" in arguments
    assert "--cov=parishkit.shared" in arguments
    assert run.call_args.kwargs["cwd"] == repository


@pytest.mark.parametrize(
    "baseline_status,database_status,expected_calls", [(0, 0, 2), (1, 0, 1), (0, 5, 2)]
)
def test_postgresql_measurement_appends_required_tests(
    repository, monkeypatch, baseline_status, database_status, expected_calls
):
    """A passing baseline cannot hide a failing/empty/skipped database gate."""
    report = repository.parent / "combined.json"
    calls = []

    def measure(arguments, **kwargs):
        """Emit baseline then combined evidence into the exclusively reserved report."""
        calls.append((arguments, kwargs))
        report.write_text(json.dumps(report_data(quality.load_scope(repository))))
        return subprocess.CompletedProcess(
            [], baseline_status if len(calls) == 1 else database_status
        )

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "untrusted.ambient.profile")
    monkeypatch.setattr(quality.subprocess, "run", measure)
    result = quality.main(
        [
            "--repository-root",
            str(repository),
            "--report",
            str(report),
            "--postgresql",
        ]
    )
    assert result == (baseline_status or database_status)
    assert len(calls) == expected_calls
    assert "--ds=parishkit.stewardship.settings.test" in calls[0][0]
    assert "--cov-append" not in calls[0][0]
    if expected_calls == 2:
        assert "--ds=parishkit.stewardship.settings.database_test" in calls[1][0]
        assert "--require-postgresql-tests" in calls[1][0]
        assert calls[1][0][-1] == "tests/stewardship/database"
        assert "--cov-append" in calls[1][0]
        assert (
            calls[0][1]["env"]["COVERAGE_FILE"] == calls[1][1]["env"]["COVERAGE_FILE"]
        )


def test_failed_pytest_returns_status_without_reading_report(repository, monkeypatch):
    """Test failure returns immediately instead of evaluating incomplete output."""
    report = repository.parent / "report.json"
    monkeypatch.setattr(
        quality.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 5))
    )
    assert (
        quality.main(["--repository-root", str(repository), "--report", str(report)])
        == 5
    )


@pytest.mark.parametrize("ambient_coverage", [False, True])
def test_runner_isolates_test_selection_and_raw_coverage(
    repository, monkeypatch, ambient_coverage
):
    """Ambient selection cannot hide tests or redirect writes to existing data."""
    report = repository.parent / "report.json"
    original = repository / "important.toml"
    original.write_text("synthetic-original")
    existing_data = repository / ".coverage"
    existing_data.write_text("synthetic-existing-coverage")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--deselect=tests/test_important.py")
    monkeypatch.setenv("PYTEST_PLUGINS", "synthetic_plugin")
    monkeypatch.setenv("COVERAGE_RCFILE", "synthetic-config")
    monkeypatch.setenv("COV_CORE_DATAFILE", str(original))
    monkeypatch.setenv("PARISHKIT_TEST_SENTINEL", "preserved")
    if ambient_coverage:
        monkeypatch.setenv("COVERAGE_FILE", str(original))
    else:
        monkeypatch.delenv("COVERAGE_FILE", raising=False)

    def emit_report(*args, **kwargs):
        """Inspect the actual child environment before simulating its outputs."""
        environment = kwargs["env"]
        assert not any(
            name.startswith(("PYTEST_", "COV_CORE_")) for name in environment
        )
        assert "COVERAGE_RCFILE" not in environment
        assert environment["PARISHKIT_TEST_SENTINEL"] == "preserved"
        data = quality.Path(environment["COVERAGE_FILE"])
        assert data.is_absolute()
        assert data.parent.parent == report.parent
        assert not data.is_relative_to(repository)
        assert data.parent.is_dir()
        assert not data.exists()
        data.write_text("synthetic-new-coverage")
        report.write_text(json.dumps(report_data(quality.load_scope(repository))))
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(quality.subprocess, "run", Mock(side_effect=emit_report))
    assert (
        quality.main(["--repository-root", str(repository), "--report", str(report)])
        == 0
    )
    assert original.read_text() == "synthetic-original"
    assert existing_data.read_text() == "synthetic-existing-coverage"
    assert "--deselect" in quality.os.environ["PYTEST_ADDOPTS"]


def test_runner_redacts_coverage_directory_failure(repository, monkeypatch, capsys):
    """Failure to reserve raw coverage storage must prevent any test launch."""
    monkeypatch.setattr(
        quality.tempfile,
        "mkdtemp",
        Mock(side_effect=OSError("synthetic-private-directory")),
    )
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert (
        quality.main(
            [
                "--repository-root",
                str(repository),
                "--report",
                str(repository.parent / "report.json"),
            ]
        )
        == 2
    )
    run.assert_not_called()
    assert capsys.readouterr().err == "ERROR: invalid coverage report\n"


@pytest.mark.parametrize(
    "fault", ["branch_off", "missing", "outside", "alias", "negative", "bool", "excess"]
)
def test_invalid_reports_fail_closed(repository, fault):
    """Missing measurement, escaping files, and invalid counters cannot pass."""
    scope = quality.load_scope(repository)
    data = report_data(scope)
    name = next(iter(data["files"]))
    if fault == "branch_off":
        data["meta"]["branch_coverage"] = False
    elif fault == "missing":
        del data["files"][name]
    elif fault == "outside":
        data["files"]["../outside.py"] = data["files"][name]
    elif fault == "alias":
        data["files"]["./" + name] = data["files"][name]
    else:
        data["files"][name]["summary"]["covered_lines"] = {
            "negative": -1,
            "bool": True,
            "excess": 101,
        }[fault]
    report = repository / "report.json"
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        quality.coverage_percentages(repository, scope, report)


def test_empty_measured_files(repository):
    """Correctly measured empty modules have no uncovered lines or branches."""
    scope = quality.load_scope(repository)
    data = report_data(scope, 0, 0)
    for detail in data["files"].values():
        detail["summary"]["num_statements"] = 0
        detail["summary"]["num_branches"] = 0
    report = repository / "report.json"
    report.write_text(json.dumps(data))
    assert quality.coverage_percentages(repository, scope, report) == (100.0, 100.0)


def test_runner_manifest_failure_is_redacted(repository, monkeypatch, capsys):
    """Invalid manifest fails before running any tests or exposing source text."""
    (repository / "coverage-stewardship.toml").write_text("sensitive malformed input")
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert (
        quality.main(["--repository-root", str(repository), "--report", "ignored.json"])
        == 2
    )
    run.assert_not_called()
    assert capsys.readouterr().err == "ERROR: invalid coverage manifest\n"


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_runner_rejects_invalid_repository_before_launch(
    tmp_path, monkeypatch, capsys, kind
):
    """Missing and non-directory roots receive a path-free repository diagnostic."""
    root = tmp_path / "synthetic-private-root"
    if kind == "file":
        root.write_text("synthetic-private-content")
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert quality.main(["--repository-root", str(root), "--report", "unused"]) == 2
    run.assert_not_called()
    assert capsys.readouterr().err == "ERROR: invalid repository directory\n"


def test_runner_redacts_launch_failure(repository, monkeypatch, capsys):
    """An OS launch failure cannot be mislabeled as bad coverage input."""
    run = Mock(side_effect=OSError("synthetic-private-launch-details"))
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert (
        quality.main(
            [
                "--repository-root",
                str(repository),
                "--report",
                str(repository.parent / "unused.json"),
            ]
        )
        == 2
    )
    run.assert_called_once()
    assert capsys.readouterr().err == "ERROR: could not launch coverage tests\n"


def test_runner_redacts_report_path_failure(repository, monkeypatch, capsys):
    """Report path normalization failure is identified before launching tests."""
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    with monkeypatch.context() as patch:
        patch.setattr(quality.Path, "cwd", Mock(return_value=repository))
        patch.setattr(
            quality.Path,
            "absolute",
            Mock(side_effect=OSError("synthetic-private-path-details")),
        )
        assert (
            quality.main(["--repository-root", str(repository), "--report", "unused"])
            == 2
        )
    run.assert_not_called()
    assert capsys.readouterr().err == "ERROR: invalid coverage report\n"


@pytest.mark.parametrize(
    "body",
    [
        None,
        "synthetic-private-invalid-json",
        "[]",
        '{"meta": [], "files": {}}',
        '{"meta": {"branch_coverage": true}}',
        '{"meta": {"branch_coverage": true}, "files": []}',
        '{"meta": {"branch_coverage": true}, "files": {"synthetic-private": []}}',
        '{"meta": {"branch_coverage": true}, "files": {}}',
    ],
)
def test_runner_redacts_unreadable_or_malformed_report(
    repository, monkeypatch, capsys, body
):
    """Report IO, JSON, and schema errors have one stage-specific safe message."""
    report = repository.parent / "synthetic-private-report.json"

    def emit_report(*args, **kwargs):
        """Simulate a successful pytest that emits malformed or no measurement."""
        if body is not None:
            report.write_text(body)
        return subprocess.CompletedProcess([], 0)

    run = Mock(side_effect=emit_report)
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert (
        quality.main(["--repository-root", str(repository), "--report", str(report)])
        == 2
    )
    run.assert_called_once()
    assert capsys.readouterr().err == "ERROR: invalid coverage report\n"


@pytest.mark.parametrize("stage", ["load_scope", "run", "coverage_percentages"])
@pytest.mark.parametrize("error", [TypeError, KeyError])
def test_runner_does_not_relabel_programming_errors(
    repository, monkeypatch, stage, error
):
    """Unexpected implementation errors are not disguised as invalid user input."""
    monkeypatch.setattr(
        quality.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0))
    )
    target = quality.subprocess if stage == "run" else quality
    monkeypatch.setattr(target, stage, Mock(side_effect=error("synthetic bug")))
    with pytest.raises(error, match="synthetic bug"):
        quality.main(
            [
                "--repository-root",
                str(repository),
                "--report",
                str(repository.parent / "unused.json"),
            ]
        )


@pytest.mark.parametrize("kind", ["inside", "existing", "symlink_inside"])
def test_report_destination_cannot_overwrite_existing_data(
    repository, monkeypatch, capsys, kind
):
    """Reject repository paths and existing output before launching pytest."""
    original = repository / "important.toml"
    original.write_text("synthetic-original")
    if kind == "inside":
        report = original
    elif kind == "existing":
        report = repository.parent / "old-coverage.json"
        report.write_text(json.dumps(report_data(quality.load_scope(repository))))
    else:
        report = repository.parent / "alias.json"
        report.symlink_to(original)
    before = report.read_bytes()
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    assert (
        quality.main(["--repository-root", str(repository), "--report", str(report)])
        == 2
    )
    run.assert_not_called()
    assert report.read_bytes() == before
    assert original.read_text() == "synthetic-original"
    assert capsys.readouterr().err == "ERROR: invalid coverage report\n"


@pytest.mark.parametrize("kind", ["details", "summary", "counts"])
def test_report_schema_errors_are_explicit(repository, kind):
    """Malformed per-file data raises validation errors, not incidental exceptions."""
    scope = quality.load_scope(repository)
    data = report_data(scope)
    name = next(iter(data["files"]))
    if kind == "details":
        data["files"][name] = []
    elif kind == "summary":
        data["files"][name]["summary"] = []
    else:
        del data["files"][name]["summary"]["num_branches"]
    report = repository / "report.json"
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        quality.coverage_percentages(repository, scope, report)
