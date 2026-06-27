"""Shared command-line helpers for ParishKit tools."""

import argparse
from collections.abc import Sequence
from importlib.metadata import version


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


def run_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-run", argv)


def print_member_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-print-member", argv)


def print_ministries_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-print-ministries", argv)


def calendar_reservations_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-calendar-reservations", argv)


def create_ministry_rosters_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-create-ministry-rosters", argv)


def sync_google_group_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-sync-google-group", argv)


def sync_ps_to_cc_main(argv: Sequence[str] | None = None) -> int:
    return _placeholder_main("parishkit-sync-ps-to-cc", argv)
