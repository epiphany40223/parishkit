import argparse
import os
import subprocess
import sys

import pytest

from parishkit import cli
from parishkit.config import ConfigError


def test_common_options_defaults():
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args([])

    options = cli.resolve_common_options(args)

    assert options.config is None
    assert not options.dry_run
    assert not options.verbose
    assert not options.debug
    assert options.slack_log_level == "CRITICAL"
    assert options.timezone == cli.DEFAULT_TIMEZONE
    assert options.ps_api_key_file == cli.DEFAULT_PS_API_KEY_FILE
    assert options.ps_cache_dir == cli.DEFAULT_PS_CACHE_DIR
    assert options.ps_cache_limit == "14m"


def test_parishkit_root_changes_default_runtime_root(tmp_path):
    code = """
import argparse
import os
from pathlib import Path

from parishkit import cli
import parishkit.pk_cron_runner as runner

root = Path(os.environ["PARISHKIT_ROOT"])
parser = argparse.ArgumentParser()
cli.add_common_arguments(parser)
args = parser.parse_args([])
options = cli.resolve_common_options(args)

assert root == cli.OPT_ROOT
assert root / "credentials/parishsoft-api-key.txt" == options.ps_api_key_file
assert root / "cache/parishsoft" == options.ps_cache_dir
assert root / "config/runner.yaml" == runner.DEFAULT_RUNNER_CONFIG
assert root / "run/runner.lock" == runner.DEFAULT_LOCK_FILE
"""

    env = os.environ.copy()
    env["PARISHKIT_ROOT"] = str(tmp_path)
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_common_options_debug_implies_verbose():
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--debug"])

    options = cli.resolve_common_options(args)

    assert options.debug
    assert options.verbose


def test_common_options_cli_overrides_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
common:
  dry_run: false
  timezone: America/New_York
logging:
  log_file: config.log
slack:
  token_file: config-slack-token.txt
  channel: "#from-config"
  level: ERROR
parishsoft:
  api_key_file: config-ps-key.txt
  cache_dir: config-cache
  cache_limit: 10m
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(
        [
            "--config",
            str(config_file),
            "--dry-run",
            "--log-file",
            "cli.log",
            "--slack-channel",
            "#from-cli",
            "--ps-cache-limit",
            "1h",
        ]
    )

    options = cli.resolve_common_options(args)

    assert options.dry_run
    assert str(options.log_file) == "cli.log"
    assert options.slack_channel == "#from-cli"
    assert options.slack_token_file == tmp_path / "config-slack-token.txt"
    assert options.timezone == "America/New_York"
    assert options.ps_api_key_file == tmp_path / "config-ps-key.txt"
    assert options.ps_cache_dir == tmp_path / "config-cache"
    assert options.ps_cache_limit == "1h"


def test_common_boolean_options_can_disable_config_values(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
common:
  dry_run: true
  verbose: true
  debug: true
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(
        [
            "--config",
            str(config_file),
            "--no-dry-run",
            "--no-debug",
            "--no-verbose",
        ]
    )

    options = cli.resolve_common_options(args)

    assert not options.dry_run
    assert not options.debug
    assert not options.verbose


def test_explicit_missing_config_file_fails(tmp_path):
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", str(tmp_path / "missing.yaml")])

    with pytest.raises(ConfigError, match="configuration file not found"):
        cli.resolve_common_options(args)


def test_config_bool_values_are_type_checked(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
common:
  debug: "false"
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", str(config_file)])

    with pytest.raises(ConfigError, match="common.debug must be a boolean"):
        cli.resolve_common_options(args)


def test_invalid_slack_log_level_fails_at_startup(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
slack:
  level: NOPE
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", str(config_file)])

    with pytest.raises(ConfigError, match="slack log level is invalid"):
        cli.resolve_common_options(args)


def test_invalid_cache_limit_fails_at_startup(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
parishsoft:
  cache_limit: soon
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", str(config_file)])

    with pytest.raises(ConfigError, match="parishsoft.cache_limit"):
        cli.resolve_common_options(args)


def test_invalid_common_timezone_fails_at_startup(tmp_path):
    """An unknown common.timezone in config is rejected at startup."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
common:
  timezone: Not/AZone
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", str(config_file)])

    with pytest.raises(ConfigError, match="common.timezone"):
        cli.resolve_common_options(args)


def test_invalid_config_is_validated_before_cli_override(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
common:
  dry_run: "false"
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", str(config_file), "--dry-run"])

    with pytest.raises(ConfigError, match="common.dry_run must be a boolean"):
        cli.resolve_common_options(args)


def test_invalid_config_slack_level_is_validated_before_cli_override(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
slack:
  level: NOPE
""",
        encoding="utf-8",
    )
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(
        ["--config", str(config_file), "--slack-log-level", "ERROR"]
    )

    with pytest.raises(ConfigError, match="slack log level is invalid"):
        cli.resolve_common_options(args)


def test_cli_paths_are_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--ps-api-key-file", "~/ps-key.txt"])

    options = cli.resolve_common_options(args)

    assert options.ps_api_key_file == tmp_path / "ps-key.txt"


def test_config_relative_paths_are_absolute(tmp_path, monkeypatch):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text(
        """
logging:
  log_file: logs/tool.log
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    parser = argparse.ArgumentParser()
    cli.add_common_arguments(parser)
    args = parser.parse_args(["--config", "configs/config.yaml"])

    options = cli.resolve_common_options(args)

    assert options.log_file == config_dir / "logs/tool.log"
