"""Implementation for the pk-query-ps-memfam command."""

from __future__ import annotations

import argparse
import datetime as dt
import re
from collections.abc import Callable, Sequence
from importlib.metadata import version
from pprint import pformat
from typing import Any

from parishkit.cli import (
    parser_with_common_options,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import (
    ConfigData,
    ConfigError,
    load_yaml_config,
    reject_unknown_keys,
)
from parishkit.logging import setup_logging
from parishkit.parishsoft import (
    ParishSoftClient,
    ParishSoftData,
    load_families_and_members,
)
from parishkit.parishsoft_runtime import parishsoft_client_from_config

Loader = Callable[..., ParishSoftData]
OMITTED_MEMBERSHIP = "omitted for brevity"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add command-specific arguments to a parser."""
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--member-duid", type=int, help="ParishSoft member DUID")
    selector.add_argument("--family-duid", type=int, help="ParishSoft family DUID")
    selector.add_argument("--name", help="case-insensitive member name search")
    parser.add_argument(
        "--load-contributions",
        nargs="?",
        const=True,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "load contributions; optionally pass a start date, otherwise load the "
            "default ParishSoft contribution window"
        ),
    )
    parser.add_argument(
        "--no-load-contributions",
        action="store_const",
        const=False,
        dest="load_contributions",
        help="disable contribution loading even when enabled by config",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="print full membership lists instead of replacing them with a marker",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    loader: Loader | None = None,
) -> int:
    """Run the ``pk-query-ps-memfam`` command-line entry point.

    Parses ``argv`` (defaults to ``sys.argv``), handles the ``--version`` short
    circuit, and validates that exactly one usable selector was supplied before
    delegating to :func:`_run`. ``loader`` is injectable so tests can supply
    fake ParishSoft data; when omitted it defaults to :func:`load_lookup_data`.
    Returns the process exit code.
    """
    parser = parser_with_common_options(
        "pk-query-ps-memfam",
        description="Print ParishSoft member or family records.",
    )
    add_arguments(parser)
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-query-ps-memfam {version('parishkit')}")
        return 0
    if args.name is not None and not args.name.strip():
        parser.error("--name must not be blank")
    if args.member_duid is None and args.family_duid is None and args.name is None:
        parser.error("one of --member-duid, --family-duid, or --name is required")
    if loader is None:
        loader = load_lookup_data
    return run_user_facing(lambda: _run(args, loader))


def _run(args: argparse.Namespace, loader: Loader) -> int:
    """Resolve config and logging, load the records, and print the result.

    Wires up the shared CLI options, YAML config, logging, and ParishSoft
    client, then loads the selected member/family data and prints the rendered
    summary. ``--load-contributions`` is passed through to the data loader for
    all selector types so the loader can decide what additional data is needed.
    Always returns 0 on success; loader/config failures surface as exceptions.
    """
    common = resolve_common_options(args)
    config = load_yaml_config(common.config)
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
    load_contributions = _load_contributions_value(args, config)
    data = loader(
        client,
        active_only=False,
        parishioners_only=False,
        load_contributions=load_contributions,
        selector=_selector(args),
    )
    print(render_selection(data, args))
    return 0


def load_lookup_data(
    client: ParishSoftClient,
    *,
    load_contributions: bool | str = False,
    selector: tuple[str, int | str] | None = None,
    **_kwargs: Any,
) -> ParishSoftData:
    """Load the full cross-linked ParishSoft dataset for record inspection.

    This command is intended as a debugging/reference tool like the original
    Epiphany ``print-member.py`` script, so it loads the same rich structure
    that other ParishKit tools use instead of a reduced lookup-only subset.
    ``selector`` is accepted for compatibility with tests and future targeted
    loaders, but the shared loader currently fetches the full dataset.
    """

    return load_families_and_members(
        client,
        active_only=False,
        parishioners_only=False,
        load_contributions=load_contributions,
    )


def _selector(args: argparse.Namespace) -> tuple[str, int | str]:
    """Return the active record selector as a ``(kind, value)`` pair.

    Exactly one of the mutually exclusive ``--member-duid``, ``--family-duid``,
    or ``--name`` options is set by the time this runs, so the checks are
    ordered by precedence and the trailing ``name`` branch is the fallback.
    """
    if args.member_duid is not None:
        return "member", args.member_duid
    if args.family_duid is not None:
        return "family", args.family_duid
    return "name", args.name or ""


def render_selection(data: ParishSoftData, args: argparse.Namespace) -> str:
    """Render the selected raw ParishSoft record(s) as pretty-printed text.

    DUID selectors render the full cross-linked member or family object and
    raise :class:`ConfigError` when the DUID is unknown; a name selector renders
    every matching full member object. Unless ``--full`` was requested,
    recursive copies replace ``membership`` list values with a marker because
    those lists are often enormous. ``pformat`` handles remaining cyclic family
    <-> member references by emitting recursion markers, matching the debugging
    style of the old Epiphany utility.
    """
    if args.member_duid is not None:
        try:
            member = data.members[args.member_duid]
        except KeyError as exc:
            raise ConfigError(f"member DUID not found: {args.member_duid}") from exc
        return pformat(_display_value(member, full=args.full), width=200)
    if args.family_duid is not None:
        try:
            family = data.families[args.family_duid]
        except KeyError as exc:
            raise ConfigError(f"family DUID not found: {args.family_duid}") from exc
        return pformat(_display_value(family, full=args.full), width=200)
    matches = find_members_by_name(data.members, args.name or "")
    return pformat(_display_value(matches, full=args.full), width=200)


def _display_value(value: Any, *, full: bool) -> Any:
    """Return the object that should be sent to ``pformat``.

    ``--full`` preserves the original object graph exactly. The default path
    makes a display-only copy that replaces every dictionary entry named
    ``membership`` whose value is a list. A memo table preserves cycles and
    shared references so pretty-printing still represents cross-links without
    recursing forever.
    """
    if full:
        return value
    return _omit_membership_lists(value, {})


def _omit_membership_lists(value: Any, memo: dict[int, Any]) -> Any:
    """Copy ``value`` while replacing list-valued ``membership`` keys.

    The ParishSoft relationship dictionaries can contain very large
    ``membership`` lists. Replacing only those values keeps the surrounding
    workgroup/ministry metadata visible while avoiding pages of roster rows in
    the default debugging output.
    """
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        result: dict[Any, Any] = {}
        memo[value_id] = result
        for key, item in value.items():
            if key == "membership" and isinstance(item, list):
                result[key] = OMITTED_MEMBERSHIP
            else:
                result[key] = _omit_membership_lists(item, memo)
        return result
    if isinstance(value, list):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        result: list[Any] = []
        memo[value_id] = result
        result.extend(_omit_membership_lists(item, memo) for item in value)
        return result
    return value


def find_members_by_name(
    members: dict[int, dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Return members whose searchable text contains the query substring.

    Matching is case-insensitive (via casefold) and treats ``query`` as a plain
    substring across the member's name fields. A blank query matches nothing.
    """
    normalized = query.strip().casefold()
    if not normalized:
        return []
    return [
        member
        for member in members.values()
        if normalized in _member_search_text(member)
    ]


def _member_search_text(member: dict[str, Any]) -> str:
    """Return a casefolded blob of a member's name fields for substring search.

    Joins the various name variants (legal, preferred, and friendly forms) so a
    single substring check can match against any of them.
    """
    fields = (
        "firstName",
        "lastName",
        "middleName",
        "preferredName",
        "py friendly name FL",
        "py friendly name LF",
    )
    values = [str(member.get(field, "")) for field in fields]
    return " ".join(values).casefold()


def _load_contributions_value(
    args: argparse.Namespace,
    config: ConfigData,
) -> bool | str:
    """Resolve whether (and from when) to load contributions.

    A CLI value always wins over config. The result is either a boolean (load
    with the default window, or skip) or a ``YYYY-MM-DD`` start-date string. The
    config fallback lives under the ``print_member`` section, which must be a
    mapping.
    """
    if args.load_contributions is not None:
        return _normalize_load_contributions(args.load_contributions)
    section = config.get("print_member", {})
    if not isinstance(section, dict):
        raise ConfigError("print_member configuration must be a mapping")
    reject_unknown_keys(section, {"load_contributions"}, "print_member")
    value = section.get("load_contributions", False)
    return _normalize_load_contributions(value)


def _normalize_load_contributions(value: Any) -> bool | str:
    """Coerce a CLI/YAML contribution value to a bool or ISO date string.

    Accepts booleans as-is, converts ``date`` objects to ISO strings, and
    treats an empty string as "disabled". Non-empty strings must be
    ``YYYY-MM-DD`` and are validated/round-tripped through ``date.fromisoformat``
    so malformed dates raise :class:`ConfigError` early rather than at query
    time.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        if value == "":
            return False
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ConfigError("load_contributions date must use YYYY-MM-DD format")
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(
                "load_contributions date must use YYYY-MM-DD format"
            ) from exc
        return parsed.isoformat()
    raise ConfigError(
        "print_member.load_contributions must be a boolean or date string"
    )
