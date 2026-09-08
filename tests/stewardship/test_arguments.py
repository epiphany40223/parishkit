"""Selective syntax diagnostics never disclose untrusted tokens or values."""

import argparse
import subprocess
import sys
from textwrap import dedent
from unittest.mock import Mock

import pytest

from parishkit.cli import parser_with_common_options
from parishkit.stewardship import cli, quality
from parishkit.stewardship.arguments import StewardshipArgumentParser

PRIVATE = "synthetic-private-value"


@pytest.mark.parametrize("scenario", ["all", "config", "none", "healthcheck", "help"])
def test_argument_registration_and_health_cli_do_not_import_providers(scenario):
    """Fresh isolated processes expose eager imports hidden by pytest's module cache."""
    result = subprocess.run(
        [sys.executable, "-I", "-", scenario],
        input=dedent("""\
            import argparse
            import sys
            from contextlib import nullcontext
            from types import SimpleNamespace

            scenario = sys.argv[1]
            if scenario in {'all', 'config', 'none'}:
                from parishkit.cli_arguments import add_common_arguments
                parser = argparse.ArgumentParser()
                add_common_arguments(parser, options=scenario)
                parser.parse_args([])
            else:
                from parishkit.stewardship import cli, services
                # Exercise the real healthcheck without making a network request.
                response = SimpleNamespace(status=200, read=lambda count: b'ok\\n')
                opener = SimpleNamespace(open=lambda *a, **k: nullcontext(response))
                services.build_opener = lambda *handlers: opener
                arguments = ['healthcheck'] if scenario == 'healthcheck' else []
                assert cli.main(arguments) == (0 if scenario == 'healthcheck' else 2)

            blocked = (
                'parishkit.cli', 'parishkit.constant_contact', 'parishkit.google',
                'parishkit.parishsoft', 'requests', 'urllib3',
            )
            loaded = sorted(name for name in sys.modules if any(
                name == prefix or name.startswith(prefix + '.') for prefix in blocked
            ))
            assert not loaded, loaded
            """),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("arguments", "hint"),
    [
        (["healthcheck", "--slack-token-file", PRIVATE], "contents redacted"),
        (["healthcheck", f"--slack-token-file={PRIVATE}"], "contents redacted"),
        (["healthcheck", "--" + PRIVATE], "contents redacted"),
        (["healthcheck", PRIVATE], "contents redacted"),
        (["healthcheck", "--", PRIVATE], "contents redacted"),
        ([PRIVATE], "invalid command; choose config-check"),
        (
            ["--profile", PRIVATE],
            "--profile requires a valid value; choose development",
        ),
        (["--profile=" + PRIVATE], "--profile requires a valid value"),
        (["--profile"], "--profile requires a valid value"),
        (
            ["--service-role", PRIVATE],
            "--service-role requires a valid value; choose web",
        ),
        (["--config", "--" + PRIVATE], "--config requires a configuration file path"),
        (["--public-origin", "--" + PRIVATE], "--public-origin requires a URL"),
        (["--runtime-root", "--" + PRIVATE], "--runtime-root requires a path"),
        (["--version=" + PRIVATE], "--version does not accept a value"),
        (
            ["--bind-all-interfaces=" + PRIVATE],
            "--bind-all-interfaces does not accept a value",
        ),
        (["--bind-all=" + PRIVATE], "contents redacted"),
        (["--help=" + PRIVATE], "invalid argument (contents redacted)"),
        (
            ["healthcheck", "--config", PRIVATE],
            "options not supported by healthcheck: --config",
        ),
        (["--version", "--config", PRIVATE], "--version must be used alone"),
    ],
)
def test_cli_syntax_errors_preserve_only_authored_hints(
    arguments, hint, capsys, monkeypatch
):
    """Every parser rejection precedes filesystem, provider, and process effects."""
    operations = []
    for name in (
        "prepare_development",
        "run_service",
        "healthcheck",
        "load_deployment",
        "load_yaml_config",
    ):
        operation = Mock()
        monkeypatch.setattr(cli, name, operation)
        operations.append(operation)
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments)
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert not output.out and PRIVATE not in output.err
    assert "usage: pk-stewardship" in output.err
    assert hint in output.err.splitlines()[-1]
    for operation in operations:
        operation.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--report", PRIVATE, "--unknown=" + PRIVATE],
        ["--report", "--" + PRIVATE],
        ["--repository-root", "--" + PRIVATE],
        ["--report", PRIVATE, PRIVATE],
    ],
)
def test_coverage_cli_redacts_paths_before_execution(arguments, monkeypatch, capsys):
    """The second stewardship entry point uses the same private-value boundary."""
    run = Mock()
    monkeypatch.setattr(quality.subprocess, "run", run)
    with pytest.raises(SystemExit) as exc:
        quality.main(arguments)
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert not output.out and PRIVATE not in output.err
    run.assert_not_called()


def test_coverage_cli_retains_specific_required_report_hint(capsys):
    """A missing required option is public syntax, not a private input value."""
    with pytest.raises(SystemExit):
        quality.main([])
    assert capsys.readouterr().err.splitlines()[-1].endswith("--report is required")


def test_conversion_errors_discard_converter_exception_text(capsys):
    """Exception messages are not trustworthy even for recognized private fields."""
    parser = StewardshipArgumentParser(
        "synthetic",
        description="synthetic",
        error_hints={"--path": "--path is invalid"},
    )

    def invalid(value):
        """Simulate a converter that embeds the sensitive value in its error."""
        raise argparse.ArgumentTypeError(value)

    parser.add_argument("--path", type=invalid)
    with pytest.raises(SystemExit):
        parser.parse_args(["--path", PRIVATE])
    output = capsys.readouterr()
    assert PRIVATE not in output.err and "--path is invalid" in output.err


def test_error_fallback_does_not_scrape_unstructured_text(capsys):
    """Unfamiliar error formats and embedded newlines cannot bypass redaction."""
    parser = StewardshipArgumentParser(
        "synthetic", description="synthetic", error_hints={}
    )
    with pytest.raises(SystemExit):
        parser.error("unknown future error\n" + PRIVATE)
    assert PRIVATE not in capsys.readouterr().err


def test_other_parishkit_parsers_retain_existing_diagnostics(capsys):
    """Opt-in stewardship handling does not alter existing shared-tool behavior."""
    parser = parser_with_common_options("synthetic")
    with pytest.raises(SystemExit):
        parser.parse_args(["--unknown=" + PRIVATE])
    assert PRIVATE in capsys.readouterr().err
