"""Shared command-line helpers for ParishKit tools.

Argument registration is re-exported from ``parishkit.cli_arguments``. Consumers
that need only parser flags can import that module without loading providers.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from parishkit.cli_arguments import (
    DEFAULT_PS_CACHE_LIMIT,
    DEFAULT_SLACK_LOG_LEVEL,
    add_common_arguments,
)
from parishkit.config import (
    ConfigData,
    ConfigError,
    load_yaml_config,
    reject_unknown_keys,
)
from parishkit.constant_contact import CCAPIError
from parishkit.google.auth import GoogleAPIError
from parishkit.logging import parse_log_level
from parishkit.parishsoft import ParishSoftAPIError
from parishkit.paths import runtime_root as _default_root
from parishkit.retry import RetryError

OPT_ROOT = _default_root()
DEFAULT_CONFIG_DIR = OPT_ROOT / "config"
DEFAULT_CREDENTIALS_DIR = OPT_ROOT / "credentials"
DEFAULT_CACHE_DIR = OPT_ROOT / "cache"
DEFAULT_LOG_DIR = OPT_ROOT / "logs"
DEFAULT_REPORTS_DIR = OPT_ROOT / "reports"
DEFAULT_RUN_DIR = OPT_ROOT / "run"
DEFAULT_PS_API_KEY_FILE = DEFAULT_CREDENTIALS_DIR / "parishsoft-api-key.txt"
DEFAULT_PS_CACHE_DIR = DEFAULT_CACHE_DIR / "parishsoft"
DEFAULT_TIMEZONE = "America/Kentucky/Louisville"
_CACHE_LIMIT_PATTERN = re.compile(r"^[1-9][0-9]*[smhd]$")


@dataclass(frozen=True)
class CommonOptions:
    """Resolved options shared by ParishKit command-line tools."""

    config: Path | None
    dry_run: bool
    dry_run_explicit: bool
    verbose: bool
    debug: bool
    log_file: Path | None
    log_dir: Path | None
    slack_token_file: Path | None
    slack_channel: str | None
    slack_log_level: str
    timezone: str
    ps_api_key_file: Path | None
    ps_cache_dir: Path | None
    ps_cache_limit: str


def _optional_path(value: str | None, *, base_dir: Path | None = None) -> Path | None:
    """Convert a string into an expanded Path, or None if empty.

    A relative path is resolved against ``base_dir`` when one is given, so that
    paths read from a config file can be interpreted relative to that file.
    """
    if value in (None, ""):
        return None
    path = Path(value).expanduser()
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return path


def _cli_path(value: Path | None) -> Path | None:
    """Expand a path supplied on the command line, leaving None untouched.

    CLI paths are never resolved against a config directory, so no base_dir is
    passed; they are interpreted relative to the current working directory.
    """
    if value is None:
        return None
    return _optional_path(str(value))


def _get_section(config: ConfigData, name: str) -> dict[str, object]:
    """Return a mapping config section."""
    value = config.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} configuration must be a mapping")
    return value


def _config_bool(section: dict[str, object], key: str, section_name: str) -> bool:
    """Read a boolean value from a config section."""
    value = section.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{section_name}.{key} must be a boolean")
    return value


def _config_str(
    section: dict[str, object],
    key: str,
    section_name: str,
) -> str | None:
    """Read a string value from a config section."""
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
    """Read a path value from a config section."""
    value = _config_str(section, key, section_name)
    return _optional_path(value, base_dir=base_dir)


def _validate_cache_limit(value: str) -> str:
    """Validate a configured cache size limit."""
    if not _CACHE_LIMIT_PATTERN.fullmatch(value):
        raise ConfigError(
            "parishsoft.cache_limit must be a duration like 30s, 14m, 12h, or 7d"
        )
    return value


def default_config_dir() -> Path:
    """Return the current default config directory."""
    return _default_root() / "config"


def default_credentials_dir() -> Path:
    """Return the current default credentials directory."""
    return _default_root() / "credentials"


def default_cache_dir() -> Path:
    """Return the current default cache directory."""
    return _default_root() / "cache"


def default_log_dir() -> Path:
    """Return the current default log directory."""
    return _default_root() / "logs"


def default_reports_dir() -> Path:
    """Return the current default reports directory."""
    return _default_root() / "reports"


def default_run_dir() -> Path:
    """Return the current default run-state directory."""
    return _default_root() / "run"


def default_ps_api_key_file() -> Path:
    """Return the current default ParishSoft API key path."""
    return default_credentials_dir() / "parishsoft-api-key.txt"


def default_ps_cache_dir() -> Path:
    """Return the current default ParishSoft cache directory."""
    return default_cache_dir() / "parishsoft"


def validate_timezone(value: str, *, name: str = "common.timezone") -> str:
    """Validate an IANA timezone name and return it unchanged."""
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"{name} is not a known IANA timezone: {value}") from exc
    return value


def parser_with_common_options(
    prog: str,
    *,
    description: str | None = None,
    common_options: Literal["all", "config", "none"] = "all",
) -> argparse.ArgumentParser:
    """Build an ArgumentParser pre-populated with the common flags.

    Convenience wrapper so each tool can create its parser and add only its
    own tool-specific arguments. Narrow consumers can select only ``config``
    or ``none``; existing callers keep the complete shared flag set.
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    add_common_arguments(parser, options=common_options)
    return parser


def resolve_common_options(args: argparse.Namespace) -> CommonOptions:
    """Merge command-line flags and YAML config into CommonOptions.

    Precedence is: an explicit CLI value wins, otherwise the config file value,
    otherwise a built-in default. Relative paths from the config file are
    resolved against the config file's own directory so config stays portable.
    Validates the Slack log level and ParishSoft cache limit, raising
    ConfigError on bad values, and forces ``verbose`` on whenever ``debug`` is
    set.
    """
    config_arg = getattr(args, "config", None)
    config_path = Path(config_arg).expanduser().resolve() if config_arg else None
    config = load_yaml_config(config_path, required=config_path is not None)
    config_base_dir = config_path.parent if config_path is not None else None

    common = _get_section(config, "common")
    logging_config = _get_section(config, "logging")
    slack_config = _get_section(config, "slack")
    ps_config = _get_section(config, "parishsoft")
    reject_unknown_keys(common, {"debug", "verbose", "dry_run", "timezone"}, "common")
    reject_unknown_keys(logging_config, {"log_file", "log_dir"}, "logging")
    reject_unknown_keys(
        slack_config,
        {
            "token_file",
            "channel",
            "level",
            "notify_success",
            "context",
            "include_output",
        },
        "slack",
    )
    reject_unknown_keys(
        ps_config,
        {"api_key_file", "cache_dir", "cache_limit", "expected_organization"},
        "parishsoft",
    )

    config_debug = _config_bool(common, "debug", "common")
    config_verbose = _config_bool(common, "verbose", "common")
    config_dry_run = _config_bool(common, "dry_run", "common")
    config_dry_run_explicit = "dry_run" in common
    config_timezone = _config_str(common, "timezone", "common")
    if config_timezone is not None:
        validate_timezone(config_timezone)
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

    # A ``None`` CLI flag means "not given", so defer to config; otherwise the
    # explicit CLI boolean wins.
    cli_debug = getattr(args, "debug", None)
    cli_verbose = getattr(args, "verbose", None)
    cli_dry_run = getattr(args, "dry_run", None)
    dry_run_explicit = cli_dry_run is not None or config_dry_run_explicit
    debug = config_debug if cli_debug is None else cli_debug
    # Debug logging implies verbose, regardless of how verbose was resolved.
    verbose = debug or (config_verbose if cli_verbose is None else cli_verbose)
    # CLI overrides config, which overrides the built-in default. These are
    # strings (not tri-state booleans), so plain ``or`` short-circuiting works.
    slack_log_level = (
        getattr(args, "slack_log_level", None)
        or config_slack_log_level
        or DEFAULT_SLACK_LOG_LEVEL
    )
    # Re-validate the finally chosen level, which may have come from the CLI.
    try:
        parse_log_level(slack_log_level)
    except ValueError as exc:
        raise ConfigError(f"slack log level is invalid: {slack_log_level}") from exc

    return CommonOptions(
        config=config_path,
        dry_run=config_dry_run if cli_dry_run is None else cli_dry_run,
        dry_run_explicit=dry_run_explicit,
        verbose=verbose,
        debug=debug,
        log_file=_cli_path(getattr(args, "log_file", None)) or config_log_file,
        log_dir=_cli_path(getattr(args, "log_dir", None)) or config_log_dir,
        slack_token_file=_cli_path(getattr(args, "slack_token_file", None))
        or config_slack_token_file,
        slack_channel=getattr(args, "slack_channel", None) or config_slack_channel,
        slack_log_level=slack_log_level,
        timezone=config_timezone or DEFAULT_TIMEZONE,
        ps_api_key_file=_cli_path(getattr(args, "ps_api_key_file", None))
        or config_ps_api_key_file
        or default_ps_api_key_file(),
        ps_cache_dir=_cli_path(getattr(args, "ps_cache_dir", None))
        or config_ps_cache_dir
        or default_ps_cache_dir(),
        ps_cache_limit=(
            _validate_cache_limit(getattr(args, "ps_cache_limit", None))
            if getattr(args, "ps_cache_limit", None)
            else config_ps_cache_limit or DEFAULT_PS_CACHE_LIMIT
        ),
    )


def require_explicit_write_mode(options: CommonOptions, tool_name: str) -> None:
    """Require mutating tools to state dry-run or live-write intent explicitly."""
    if options.dry_run_explicit:
        return
    raise ConfigError(
        f"{tool_name} can modify external systems; set common.dry_run to true "
        "for a dry run or false for live writes, or pass --dry-run/--no-dry-run"
    )


def _placeholder_main(tool_name: str, argv: Sequence[str] | None = None) -> int:
    """Entry point for not-yet-implemented tools.

    Answers ``--version`` so packaging/install can be verified, but otherwise
    exits with an error explaining the command is unimplemented.
    """
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
    """Run a command body, turning expected failures into a clean exit.

    Returns the action's own exit code on success. Known operational errors
    (bad config, I/O, ParishSoft API, exhausted retries) are reported as a
    single ``ERROR:`` line on stderr and converted to exit code 2 instead of a
    traceback. Unexpected exceptions propagate so genuine bugs stay visible.
    """

    try:
        return action()
    except (
        ConfigError,
        OSError,
        ParishSoftAPIError,
        GoogleAPIError,
        CCAPIError,
        RetryError,
    ) as exc:
        logging.getLogger("parishkit").error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def run_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the runner tool.

    Imports the tool module lazily so the shared CLI package stays cheap to
    import for tools that do not need it, then delegates to its ``main``.
    """
    from parishkit.pk_cron_runner import main

    return main(list(argv) if argv is not None else None)


def print_member_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the print-member tool (lazy import + delegate)."""
    from parishkit.pk_query_ps_memfam import main

    return main(list(argv) if argv is not None else None)


def print_ministries_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the print-ministries tool (lazy import + delegate)."""
    from parishkit.pk_print_ps_ministries import main

    return main(list(argv) if argv is not None else None)


def calendar_reservations_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the calendar-reservations tool.

    Lazily imports the tool module (which pulls in Google client libraries)
    only when the command actually runs, then delegates to its ``main``.
    """
    from parishkit.pk_validate_gcalendar_reservations import main

    return main(list(argv) if argv is not None else None)


def create_ministry_rosters_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the create-ministry-rosters tool.

    Lazy import + delegate, so the shared CLI package does not pull in this
    tool's dependencies until it runs.
    """
    from parishkit.pk_create_ps_ministry_rosters import main

    return main(list(argv) if argv is not None else None)


def sync_google_group_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the sync-google-group tool (lazy import + delegate)."""
    from parishkit.pk_sync_ps_to_ggroup import main

    return main(list(argv) if argv is not None else None)


def sync_ps_to_cc_main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for the ParishSoft-to-Constant-Contact sync tool.

    Lazy import + delegate, keeping this tool's dependencies out of the shared
    CLI import path until the command runs.
    """
    from parishkit.pk_sync_ps_to_cc import main

    return main(list(argv) if argv is not None else None)
