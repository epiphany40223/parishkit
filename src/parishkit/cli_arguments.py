"""Lightweight shared argument registration without provider or runtime imports."""

import argparse
from pathlib import Path
from typing import Literal

DEFAULT_PS_CACHE_LIMIT = "14m"
DEFAULT_SLACK_LOG_LEVEL = "CRITICAL"


def add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    options: Literal["all", "config", "none"] = "all",
) -> None:
    """Register shared flags, optionally limiting them to config or none.

    The tri-state flags default to ``None`` (via BooleanOptionalAction) so that
    :func:`parishkit.cli.resolve_common_options` can distinguish an omitted flag
    from an explicit true/false and fall back to config in that case.
    Existing tools retain all flags by default. Diagnostics that do not resolve
    common runtime options can request a smaller set without advertising flags
    they cannot honor.
    """
    if options not in {"all", "config", "none"}:
        raise ValueError("unknown common option set")
    if options == "none":
        return
    parser.add_argument("--config", type=Path, help="YAML configuration file")
    if options == "config":
        return
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
