"""Implementation for the pk-print-ps-ministries command."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from importlib.metadata import version
from typing import Any

from parishkit.cli import (
    parser_with_common_options,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.logging import setup_logging
from parishkit.parishsoft import load_ministry_types
from parishkit.parishsoft_runtime import parishsoft_client_from_config

Loader = Callable[..., dict[int, dict[str, Any]]]
DEFAULT_INCLUDE_PATTERNS: list[str] = []
DEFAULT_INCLUDE_NAMES: list[str] = []


def main(
    argv: Sequence[str] | None = None,
    *,
    loader: Loader = load_ministry_types,
) -> int:
    """Run the command-line entry point."""
    parser = parser_with_common_options(
        "pk-print-ps-ministries",
        description="Print sorted ParishSoft ministry names.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-print-ps-ministries {version('parishkit')}")
        return 0
    return run_user_facing(lambda: _run(args, loader))


def _run(args: Any, loader: Loader) -> int:
    """Run the command after common CLI setup."""
    common = resolve_common_options(args)
    config = load_yaml_config(common.config)
    filters = ministry_filters(config)
    _validate_filters(filters)
    setup_logging(
        verbose=common.verbose,
        debug=common.debug,
        log_file=common.log_file,
        log_dir=common.log_dir,
        logger_name="parishkit",
        slack_token_file=common.slack_token_file,
        slack_channel=common.slack_channel,
        slack_level=common.slack_log_level,
    )
    client = parishsoft_client_from_config(common, config)
    client.validate_organization()
    ministry_types = loader(client)
    for name in sorted_ministry_names(ministry_types, **filters):
        print(name)
    return 0


def ministry_filters(config: ConfigData) -> dict[str, list[str]]:
    """Extract and validate the ministry-name filters from config.

    Reads the optional ``print_ministries`` section and returns the
    ``include_patterns``, ``include_names``, and ``exclude_patterns`` lists
    (each defaulting to empty), validating that every entry is a string.
    Raises :class:`ConfigError` if the section is present but not a mapping.
    """
    section = config.get("print_ministries", {})
    if not isinstance(section, dict):
        raise ConfigError("print_ministries configuration must be a mapping")
    return {
        "include_patterns": _string_list(
            section.get("include_patterns", DEFAULT_INCLUDE_PATTERNS),
            "print_ministries.include_patterns",
        ),
        "include_names": _string_list(
            section.get("include_names", DEFAULT_INCLUDE_NAMES),
            "print_ministries.include_names",
        ),
        "exclude_patterns": _string_list(
            section.get("exclude_patterns", []),
            "print_ministries.exclude_patterns",
        ),
    }


def sorted_ministry_names(
    ministry_types: dict[int, dict[str, Any]],
    *,
    include_patterns: list[str] | None = None,
    include_names: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """Return the de-duplicated, sorted ministry names that pass the filters.

    A name is excluded if it matches any ``exclude_patterns`` regex. Otherwise
    it is included when it matches an ``include_patterns`` regex, is listed in
    ``include_names``, or when no include filters are configured at all (in
    which case every non-excluded ministry is returned).
    """
    patterns = _compile_patterns(include_patterns or [], "include_patterns")
    excluded = _compile_patterns(exclude_patterns or [], "exclude_patterns")
    explicit_names = set(include_names or [])
    names = set()
    for ministry in ministry_types.values():
        name = ministry["name"]
        if any(pattern.search(name) for pattern in excluded):
            continue
        # Include when there are no include filters (keep everything), or the
        # name is explicitly listed, or it matches an include pattern. Exclusion
        # above always wins because it is checked first.
        if (
            not patterns
            and not explicit_names
            or name in explicit_names
            or any(pattern.search(name) for pattern in patterns)
        ):
            names.add(name)
    return sorted(names)


def _validate_filters(filters: dict[str, list[str]]) -> None:
    """Fail fast if any configured include/exclude regex is invalid.

    Compiles the patterns up front (discarding the result) so a bad regex is
    reported during startup rather than mid-run while iterating ministries.
    """
    _compile_patterns(filters["include_patterns"], "include_patterns")
    _compile_patterns(filters["exclude_patterns"], "exclude_patterns")


def _compile_patterns(patterns: list[str], name: str) -> list[re.Pattern[str]]:
    """Compile a list of regex strings, raising ConfigError on a bad pattern.

    ``name`` identifies the originating config key so the error message points
    the user at the offending setting.
    """
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.error as exc:
        raise ConfigError(f"print_ministries.{name} contains invalid regex") from exc


def _string_list(value: Any, name: str) -> list[str]:
    """Return ``value`` after asserting it is a list of strings.

    ``name`` is used in the error message to identify the config key. Raises
    :class:`ConfigError` if the value is not a list or contains a non-string.
    """
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return value
