"""Shared command-line helpers for ParishKit tools."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.logging import parse_log_level
from parishkit.parishsoft import ParishSoftAPIError
from parishkit.retry import RetryError

OPT_ROOT = Path("/opt/parishkit")
DEFAULT_CONFIG_DIR = OPT_ROOT / "config"
DEFAULT_CREDENTIALS_DIR = OPT_ROOT / "credentials"
DEFAULT_CACHE_DIR = OPT_ROOT / "cache"
DEFAULT_LOG_DIR = OPT_ROOT / "logs"
DEFAULT_REPORTS_DIR = OPT_ROOT / "reports"
DEFAULT_RUN_DIR = OPT_ROOT / "run"
DEFAULT_PS_API_KEY_FILE = DEFAULT_CREDENTIALS_DIR / "parishsoft-api-key.txt"
DEFAULT_PS_CACHE_DIR = DEFAULT_CACHE_DIR / "parishsoft"
DEFAULT_PS_CACHE_LIMIT = "14m"
DEFAULT_SLACK_LOG_LEVEL = "CRITICAL"
_CACHE_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*[smhd]$")


@dataclass(frozen=True)
class CommonOptions:
    config: Path | None
    dry_run: bool
    verbose: bool
    debug: bool
    log_file: Path | None
    log_dir: Path | None
    slack_token_file: Path | None
    slack_channel: str | None
    slack_log_level: str
    ps_api_key_file: Path | None
    ps_cache_dir: Path | None
    ps_cache_limit: str


def _optional_path(value: str | None, *, base_dir: Path | None = None) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(value).expanduser()
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return path


def _cli_path(value: Path | None) -> Path | None:
    if value is None:
        return None
    return _optional_path(str(value))


def _get_section(config: ConfigData, name: str) -> dict[str, object]:
    value = config.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} configuration must be a mapping")
    return value


def _config_bool(section: dict[str, object], key: str, section_name: str) -> bool:
    value = section.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{section_name}.{key} must be a boolean")
    return value


def _config_str(
    section: dict[str, object],
    key: str,
    section_name: str,
) -> str | None:
    value = section.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{section_name}.{key} must be a string")
    return value


def _config_path(
    section: dict[str, object],
    key: str,
    section_name: str,
    *,
    base_dir: Path | None,
) -> Path | None:
    value = _config_str(section, key, section_name)
    return _optional_path(value, base_dir=base_dir)


def _validate_cache_limit(value: str) -> str:
    if not _CACHE_LIMIT_PATTERN.fullmatch(value):
        raise ConfigError(
            "parishsoft.cache_limit must be a duration like 30s, 14m, 12h, or 7d"
        )
    return value


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="YAML configuration file")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="avoid external writes",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable verbose logging",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable debug logging",
    )
    parser.add_argument("--log-file", type=Path, help="write logs to this file")
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="write default logs under this dir",
    )
    parser.add_argument("--slack-token-file", type=Path, help="Slack token file")
    parser.add_argument(
        "--slack-channel",
        help="Slack channel for critical notifications",
    )
    parser.add_argument(
        "--slack-log-level",
        default=None,
        help=f"Slack logging threshold (default: {DEFAULT_SLACK_LOG_LEVEL})",
    )
    parser.add_argument(
        "--ps-api-key-file",
        type=Path,
        help="ParishSoft API key file",
    )
    parser.add_argument("--ps-cache-dir", type=Path, help="ParishSoft cache directory")
    parser.add_argument(
        "--ps-cache-limit",
        default=None,
        help=f"ParishSoft cache age limit (default: {DEFAULT_PS_CACHE_LIMIT})",
    )


def parser_with_common_options(
    prog: str,
    *,
    description: str | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    add_common_arguments(parser)
    return parser


def resolve_common_options(args: argparse.Namespace) -> CommonOptions:
    config_arg = getattr(args, "config", None)
    config_path = Path(config_arg).expanduser().resolve() if config_arg else None
    config = load_yaml_config(config_path, required=config_path is not None)
    config_base_dir = config_path.parent if config_path is not None else None

    common = _get_section(config, "common")
    logging_config = _get_section(config, "logging")
    slack_config = _get_section(config, "slack")
    ps_config = _get_section(config, "parishsoft")

    config_debug = _config_bool(common, "debug", "common")
    config_verbose = _config_bool(common, "verbose", "common")
    config_dry_run = _config_bool(common, "dry_run", "common")
    config_log_file = _config_path(
        logging_config,
        "log_file",
        "logging",
        base_dir=config_base_dir,
    )
    config_log_dir = _config_path(
        logging_config,
        "log_dir",
        "logging",
        base_dir=config_base_dir,
    )
    config_slack_token_file = _config_path(
        slack_config,
        "token_file",
        "slack",
        base_dir=config_base_dir,
    )
    config_slack_channel = _config_str(slack_config, "channel", "slack")
    config_slack_log_level = _config_str(slack_config, "level", "slack")
    if config_slack_log_level is not None:
        try:
            parse_log_level(config_slack_log_level)
        except ValueError as exc:
            raise ConfigError(
                f"slack log level is invalid: {config_slack_log_level}"
            ) from exc
    config_ps_api_key_file = _config_path(
        ps_config,
        "api_key_file",
        "parishsoft",
        base_dir=config_base_dir,
    )
    config_ps_cache_dir = _config_path(
        ps_config,
        "cache_dir",
        "parishsoft",
        base_dir=config_base_dir,
    )
    config_ps_cache_limit = _config_str(ps_config, "cache_limit", "parishsoft")
    if config_ps_cache_limit is not None:
        _validate_cache_limit(config_ps_cache_limit)

    cli_debug = getattr(args, "debug", None)
    cli_verbose = getattr(args, "verbose", None)
    cli_dry_run = getattr(args, "dry_run", None)
    debug = config_debug if cli_debug is None else cli_debug
    verbose = debug or (config_verbose if cli_verbose is None else cli_verbose)
    slack_log_level = (
        getattr(args, "slack_log_level", None)
        or config_slack_log_level
        or DEFAULT_SLACK_LOG_LEVEL
    )
    try:
        parse_log_level(slack_log_level)
    except ValueError as exc:
        raise ConfigError(f"slack log level is invalid: {slack_log_level}") from exc

    return CommonOptions(
        config=config_path,
        dry_run=config_dry_run if cli_dry_run is None else cli_dry_run,
        verbose=verbose,
        debug=debug,
        log_file=_cli_path(getattr(args, "log_file", None)) or config_log_file,
        log_dir=_cli_path(getattr(args, "log_dir", None)) or config_log_dir,
        slack_token_file=_cli_path(getattr(args, "slack_token_file", None))
        or config_slack_token_file,
        slack_channel=getattr(args, "slack_channel", None) or config_slack_channel,
        slack_log_level=slack_log_level,
        ps_api_key_file=_cli_path(getattr(args, "ps_api_key_file", None))
        or config_ps_api_key_file
        or DEFAULT_PS_API_KEY_FILE,
        ps_cache_dir=_cli_path(getattr(args, "ps_cache_dir", None))
        or config_ps_cache_dir
        or DEFAULT_PS_CACHE_DIR,
        ps_cache_limit=(
            _validate_cache_limit(getattr(args, "ps_cache_limit", None))
            if getattr(args, "ps_cache_limit", None)
            else config_ps_cache_limit or DEFAULT_PS_CACHE_LIMIT
        ),
    )


def _placeholder_main(tool_name: str, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=tool_name)
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"{tool_name} {version('parishkit')}")
        return 0

    parser.error("this command has not been implemented yet")
    return 2


def run_user_facing(action: Callable[[], int]) -> int:
    """Run a command body and print expected operational errors concisely."""

    try:
        return action()
    except (ConfigError, OSError, ParishSoftAPIError, RetryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def run_main(argv: Sequence[str] | None = None) -> int:
    from parishkit.runner import main

    return main(list(argv) if argv is not None else None)


def print_member_main(argv: Sequence[str] | None = None) -> int:
    from parishkit.print_member import main

    return main(list(argv) if argv is not None else None)


def print_ministries_main(argv: Sequence[str] | None = None) -> int:
    from parishkit.print_ministries import main

    return main(list(argv) if argv is not None else None)


def calendar_reservations_main(argv: Sequence[str] | None = None) -> int:
    from parishkit.calendar_reservations import main

    return main(list(argv) if argv is not None else None)


def create_ministry_rosters_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-create-ministry-rosters", argv)


def sync_google_group_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-sync-google-group", argv)


def sync_ps_to_cc_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-sync-ps-to-cc", argv)
