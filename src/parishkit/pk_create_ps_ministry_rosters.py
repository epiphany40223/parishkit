"""Implementation for the pk-create-ps-ministry-rosters command."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from parishkit.cli import (
    parser_with_common_options,
    resolve_common_options,
    run_user_facing,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.google.auth import (
    GoogleAPIError,
    load_service_account_credentials,
    load_user_credentials,
)
from parishkit.google.sheets import (
    batch_update_spreadsheet,
    build_sheets_service,
    clear_values,
    get_spreadsheet,
    update_values,
)
from parishkit.logging import log_extra, setup_logging
from parishkit.parishsoft import (
    ParishSoftData,
    get_member_public_email,
    get_member_public_phones,
    load_families_and_members,
)
from parishkit.parishsoft_runtime import parishsoft_client_from_config

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DEFAULT_RANGE = "Roster!A1"
DEFAULT_CLEAR_RANGE = "Roster!A:Z"
DEFAULT_LEADER_SUFFIX = " Ldr"
HEADER_BACKGROUND_COLOR = {"red": 0.0, "green": 0.0, "blue": 1.0}
HEADER_TEXT_COLOR = {"red": 1.0, "green": 1.0, "blue": 0.0}
ROSTER_COLUMN_WIDTHS = (220, 280, 360, 160, 200)
ROSTER_TITLE_ROWS = 2
ROSTER_TITLE_MERGE_COLUMNS = 4
ROSTER_FROZEN_ROWS = 4
ROSTER_SPACER_ROW_INDEX = 2
ROSTER_COLUMN_HEADER_ROW_INDEX = 3


@dataclass(frozen=True)
class RoleRosterTarget:
    """A secondary sheet holding only members whose role is in ``roles``.

    Each role sheet is a filtered view of its parent ministry roster, written
    to its own ``spreadsheet_id``/``range_name`` and cleared via ``clear_range``.
    """

    name: str
    roles: tuple[str, ...]
    spreadsheet_id: str
    range_name: str
    clear_range: str


@dataclass(frozen=True)
class RosterTarget:
    """A configured roster to publish to a Google Sheet.

    ``source_type`` is either ``"ministry"`` or ``"workgroup"`` and
    ``source_names`` lists the ParishSoft sources whose members populate the
    roster. ``role_sheets`` are optional per-role breakout sheets derived from
    the same members.
    """

    name: str
    source_type: str
    source_names: tuple[str, ...]
    spreadsheet_id: str
    range_name: str
    clear_range: str
    include_birthday: bool
    role_sheets: tuple[RoleRosterTarget, ...] = ()


@dataclass(frozen=True)
class RosterConfig:
    """Parsed ``rosters`` configuration.

    ``workgroup_leader_suffix`` is appended to a workgroup name to recognize
    the companion leader workgroup in ParishSoft.
    """

    ministries: tuple[RosterTarget, ...]
    workgroups: tuple[RosterTarget, ...]
    workgroup_leader_suffix: str = DEFAULT_LEADER_SUFFIX


@dataclass(frozen=True)
class RosterMember:
    """A member paired with the role text to show on a roster row."""

    member: dict[str, Any]
    role: str


Loader = Callable[..., ParishSoftData]
SheetsFactory = Callable[[ConfigData], Any]


def _text_list(values: Sequence[str]) -> str:
    """Render a short list of strings for human-readable log messages."""
    return ", ".join(values) if values else "none"


def main(
    argv: Sequence[str] | None = None,
    *,
    loader: Loader = load_families_and_members,
    sheets_factory: SheetsFactory | None = None,
) -> int:
    """Run the command-line entry point."""
    parser = parser_with_common_options(
        "pk-create-ps-ministry-rosters",
        description="Write ParishSoft ministry rosters to Google Sheets.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-create-ps-ministry-rosters {version('parishkit')}")
        return 0
    return run_user_facing(lambda: _run(args, loader, sheets_factory))


def _run(
    args: argparse.Namespace,
    loader: Loader,
    sheets_factory: SheetsFactory | None,
) -> int:
    """Load config and data, then publish every configured roster.

    Resolves common CLI options, loads the YAML roster config, sets up
    logging (including optional Slack), builds the ParishSoft client and the
    Google Sheets service, fetches active members, and writes the rosters.
    The Sheets service can be injected via ``sheets_factory`` for testing;
    otherwise it is built from credentials in the config. Returns 0 on
    success. The steps are kept explicit so operational behavior remains easy
    to audit and test.
    """
    common = resolve_common_options(args)
    log = setup_logging(
        verbose=common.verbose,
        debug=common.debug,
        log_file=common.log_file,
        log_dir=common.log_dir,
        logger_name="parishkit.pk_create_ps_ministry_rosters",
        slack_token_file=common.slack_token_file,
        slack_channel=common.slack_channel,
        slack_level=common.slack_log_level,
    )
    try:
        config = load_yaml_config(common.config)
        roster_config = roster_config_from_yaml(config)
        log.info(
            "Configured %s ministry roster(s) and %s workgroup roster(s)",
            len(roster_config.ministries),
            len(roster_config.workgroups),
        )
        log.debug(
            "Ministry roster targets: %s",
            _text_list([target.name for target in roster_config.ministries]),
            extra=log_extra(roster_config.ministries),
        )
        log.debug(
            "Workgroup roster targets: %s",
            _text_list([target.name for target in roster_config.workgroups]),
            extra=log_extra(roster_config.workgroups),
        )
        client = parishsoft_client_from_config(common, config)
        log.info("Loading active ParishSoft families and members")
        data = loader(client, active_only=True, parishioners_only=False)
        log.info(
            "Loaded %s member(s), %s family/families, %s ministry membership(s), "
            "and %s workgroup membership(s)",
            len(data.members),
            len(data.families),
            len(data.ministry_type_memberships),
            len(data.member_workgroup_memberships),
        )
        log.debug("Dry-run mode is %s", "enabled" if common.dry_run else "disabled")
        sheets_service = (
            sheets_factory(config)
            if sheets_factory is not None
            else build_sheets_service(load_sheets_credentials(config))
        )
        write_configured_rosters(
            sheets_service,
            data,
            roster_config,
            timezone_name=common.timezone,
            dry_run=common.dry_run,
            log=log,
        )
    except ConfigError as exc:
        log.error("Configuration validation failed: %s", exc)
        raise
    log.info("Ministry roster operation completed successfully")
    return 0


def roster_config_from_yaml(config: ConfigData) -> RosterConfig:
    """Parse and validate the ``rosters`` config section.

    Reads optional top-level defaults (spreadsheet ID, range, clear range, and
    workgroup leader suffix) and applies them to each ministry and workgroup
    target. Raises ``ConfigError`` on malformed values or when neither any
    ministries nor any workgroups are configured. The steps are kept explicit so
    operational behavior remains easy to audit and test.
    """
    section = _mapping(
        config.get("rosters", {}),
        "rosters",
    )
    default_spreadsheet_id = section.get("spreadsheet_id")
    if default_spreadsheet_id is not None and not isinstance(
        default_spreadsheet_id, str
    ):
        raise ConfigError("rosters.spreadsheet_id must be a string")
    default_range = _optional_string(section.get("range"), "rosters.range")
    default_clear_range = _optional_string(
        section.get("clear_range"),
        "rosters.clear_range",
    )
    leader_suffix = section.get("workgroup_leader_suffix", DEFAULT_LEADER_SUFFIX)
    if not isinstance(leader_suffix, str):
        raise ConfigError("rosters.workgroup_leader_suffix must be a string")
    ministries = tuple(
        _target(
            item,
            f"rosters.ministries[{index}]",
            source_key="ministry",
            plural_source_key="ministries",
            source_type="ministry",
            default_spreadsheet_id=default_spreadsheet_id,
            default_range=default_range,
            default_clear_range=default_clear_range,
        )
        for index, item in enumerate(
            _list(section.get("ministries", []), "rosters.ministries")
        )
    )
    workgroups = tuple(
        _target(
            item,
            f"rosters.workgroups[{index}]",
            source_key="workgroup",
            plural_source_key=None,
            source_type="workgroup",
            default_spreadsheet_id=default_spreadsheet_id,
            default_range=default_range,
            default_clear_range=default_clear_range,
        )
        for index, item in enumerate(
            _list(section.get("workgroups", []), "rosters.workgroups")
        )
    )
    if not ministries and not workgroups:
        raise ConfigError("rosters must configure ministries or workgroups")
    return RosterConfig(
        ministries=ministries,
        workgroups=workgroups,
        workgroup_leader_suffix=leader_suffix,
    )


def load_sheets_credentials(config: ConfigData) -> Any:
    """Load credentials for Google Sheets access."""
    google = _mapping(config.get("google", {}), "google")
    service_account_file = google.get("service_account_file")
    user_token_file = google.get("user_token_file")
    delegated_subject = google.get("delegated_subject")
    if service_account_file and user_token_file:
        raise ConfigError(
            "google configuration must not set both service_account_file "
            "and user_token_file"
        )
    if delegated_subject is not None and not isinstance(delegated_subject, str):
        raise ConfigError("google.delegated_subject must be a string")
    if isinstance(service_account_file, str):
        return load_service_account_credentials(
            Path(service_account_file),
            scopes=[SHEETS_SCOPE],
            subject=delegated_subject,
        )
    if isinstance(user_token_file, str):
        return load_user_credentials(Path(user_token_file), scopes=[SHEETS_SCOPE])
    raise ConfigError(
        "google.service_account_file or google.user_token_file is required"
    )


def write_configured_rosters(
    sheets_service: Any,
    data: ParishSoftData,
    config: RosterConfig,
    *,
    timezone_name: str,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    """Write every ministry, role, and workgroup roster to Sheets.

    For each ministry target, collects its members and writes the main roster
    plus any per-role breakout sheets (filtered to that sheet's allowed roles).
    For each workgroup target, collects members tagging leaders separately and
    writes the roster. ``dry_run`` logs intended writes without touching
    Sheets. The steps are kept explicit so operational behavior remains easy
    to audit and test.
    """
    update_time = current_roster_time(timezone_name)
    for target in config.ministries:
        log.debug(
            "Preparing ministry roster %s from %s",
            target.name,
            _text_list(target.source_names),
            extra=log_extra(target),
        )
        members = ministry_roster_members(data, target.source_names)
        write_roster_target(
            sheets_service,
            target,
            members,
            update_time=update_time,
            dry_run=dry_run,
            log=log,
        )
        for role_target in target.role_sheets:
            allowed_roles = set(role_target.roles)
            log.debug(
                "Preparing role roster %s from %s role(s): %s",
                role_target.name,
                target.name,
                _text_list(role_target.roles),
                extra=log_extra(role_target),
            )
            role_members = [
                member
                for member in members
                if roster_role_matches(member.role, allowed_roles)
            ]
            write_values(
                sheets_service,
                role_target.spreadsheet_id,
                role_target.range_name,
                role_target.clear_range,
                roster_values(
                    role_target.name,
                    role_members,
                    include_birthday=target.include_birthday,
                    now=update_time,
                ),
                spreadsheet_title=roster_spreadsheet_title(
                    role_target.name,
                    update_time,
                ),
                dry_run=dry_run,
                log=log,
            )
    for target in config.workgroups:
        log.debug(
            "Preparing workgroup roster %s from %s",
            target.name,
            _text_list(target.source_names),
            extra=log_extra(target),
        )
        members = workgroup_roster_members(
            data,
            target.source_names[0],
            leader_suffix=config.workgroup_leader_suffix,
        )
        write_roster_target(
            sheets_service,
            target,
            members,
            update_time=update_time,
            dry_run=dry_run,
            log=log,
        )


def write_roster_target(
    sheets_service: Any,
    target: RosterTarget,
    members: Sequence[RosterMember],
    *,
    update_time: dt.datetime,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    """Write one configured roster target to Sheets."""
    write_values(
        sheets_service,
        target.spreadsheet_id,
        target.range_name,
        target.clear_range,
        roster_values(
            target.name,
            members,
            include_birthday=target.include_birthday,
            now=update_time,
        ),
        spreadsheet_title=roster_spreadsheet_title(target.name, update_time),
        dry_run=dry_run,
        log=log,
    )


def write_values(
    sheets_service: Any,
    spreadsheet_id: str,
    range_name: str,
    clear_range: str,
    values: list[list[Any]],
    *,
    spreadsheet_title: str,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    """Clear ``clear_range`` then write ``values`` to a Google Sheet range.

    The range is cleared first so stale rows from a previous, longer roster do
    not linger below the freshly written data. When ``dry_run`` is set, nothing
    is written; the intended write is only logged.
    """
    if dry_run:
        log.info(
            "dry-run: would write %s row(s) to spreadsheet %s range %s",
            len(values),
            spreadsheet_id,
            range_name,
        )
        return
    try:
        clear_values(sheets_service, spreadsheet_id, clear_range)
        update_values(sheets_service, spreadsheet_id, range_name, values)
        format_roster_sheet(
            sheets_service,
            spreadsheet_id,
            range_name,
            spreadsheet_title=spreadsheet_title,
            column_count=max(len(row) for row in values),
            row_count=len(values),
        )
    except GoogleAPIError as exc:
        config_error = sheet_range_config_error(
            exc,
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
            clear_range=clear_range,
        )
        if config_error is not None:
            raise config_error from exc
        raise


def sheet_range_config_error(
    exc: GoogleAPIError,
    *,
    spreadsheet_id: str,
    range_name: str,
    clear_range: str,
) -> ConfigError | None:
    """Return a clearer config error for common Sheets range failures."""
    if exc.status_code == 400 and "Unable to parse range" in str(exc):
        sheet_names = sorted(
            {
                sheet_name_from_a1_range(range_name),
                sheet_name_from_a1_range(clear_range),
            }
        )
        sheet_text = ", ".join(repr(name) for name in sheet_names)
        return ConfigError(
            "Google Sheets rejected a configured roster range for spreadsheet "
            f"{spreadsheet_id}: range={range_name!r}, clear_range={clear_range!r}. "
            f"Make sure the target spreadsheet contains worksheet/tab {sheet_text} "
            "and that rosters.*.range and rosters.*.clear_range use a valid "
            "sheet-qualified A1 range."
        )
    return None


def format_roster_sheet(
    sheets_service: Any,
    spreadsheet_id: str,
    range_name: str,
    *,
    spreadsheet_title: str,
    column_count: int,
    row_count: int,
) -> None:
    """Apply roster presentation formatting to a written Google Sheet.

    Sheets value updates do not affect layout, so formatting is a separate
    batchUpdate call. The formatting mirrors the old XLSX workflow: freeze the
    first four rows, make the title rows and column-header row yellow-on-blue,
    merge the title text across A-D, color the spacer row blue, widen columns,
    wrap text, and top-align all roster cells.
    """
    sheet_name = sheet_name_from_a1_range(range_name)
    sheet_id = sheet_id_for_title(sheets_service, spreadsheet_id, sheet_name)
    batch_update_spreadsheet(
        sheets_service,
        spreadsheet_id,
        roster_format_requests(
            sheet_id,
            spreadsheet_title=spreadsheet_title,
            column_count=column_count,
            row_count=row_count,
        ),
    )


def roster_format_requests(
    sheet_id: int,
    *,
    spreadsheet_title: str | None = None,
    column_count: int,
    row_count: int,
) -> list[dict[str, Any]]:
    """Build Sheets API requests for roster layout and visual styling."""
    widths = ROSTER_COLUMN_WIDTHS[:column_count]
    requests: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": ROSTER_FROZEN_ROWS},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        repeat_cell_request(
            sheet_id,
            start_row=0,
            end_row=max(row_count, ROSTER_COLUMN_HEADER_ROW_INDEX + 1),
            start_col=0,
            end_col=column_count,
            cell_format={
                "verticalAlignment": "TOP",
                "wrapStrategy": "WRAP",
            },
            fields="userEnteredFormat(verticalAlignment,wrapStrategy)",
        ),
        header_format_request(
            sheet_id,
            start_row=0,
            end_row=ROSTER_TITLE_ROWS,
            column_count=column_count,
            horizontal_alignment="LEFT",
        ),
        spacer_format_request(
            sheet_id,
            column_count=column_count,
        ),
        header_format_request(
            sheet_id,
            start_row=ROSTER_COLUMN_HEADER_ROW_INDEX,
            end_row=ROSTER_COLUMN_HEADER_ROW_INDEX + 1,
            column_count=column_count,
            horizontal_alignment="CENTER",
        ),
    ]
    for row_index in range(ROSTER_TITLE_ROWS):
        requests.extend(
            title_merge_requests(
                sheet_id,
                row_index=row_index,
                column_count=column_count,
            )
        )
    for index, pixel_size in enumerate(widths):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": index,
                        "endIndex": index + 1,
                    },
                    "properties": {"pixelSize": pixel_size},
                    "fields": "pixelSize",
                }
            }
        )
    if spreadsheet_title is not None:
        requests.append(spreadsheet_title_request(spreadsheet_title))
    return requests


def spreadsheet_title_request(spreadsheet_title: str) -> dict[str, Any]:
    """Return a request that renames the spreadsheet document."""
    return {
        "updateSpreadsheetProperties": {
            "properties": {"title": spreadsheet_title},
            "fields": "title",
        }
    }


def title_merge_requests(
    sheet_id: int,
    *,
    row_index: int,
    column_count: int,
) -> list[dict[str, Any]]:
    """Return idempotent unmerge/merge requests for one title row.

    Sheets rejects merging cells that are already merged, so each run first
    unmerges the title span and then re-applies the desired A-D merge.
    """
    end_column = min(ROSTER_TITLE_MERGE_COLUMNS, column_count)
    merge_range = {
        "sheetId": sheet_id,
        "startRowIndex": row_index,
        "endRowIndex": row_index + 1,
        "startColumnIndex": 0,
        "endColumnIndex": end_column,
    }
    return [
        {"unmergeCells": {"range": merge_range}},
        {"mergeCells": {"range": merge_range, "mergeType": "MERGE_ALL"}},
    ]


def spacer_format_request(
    sheet_id: int,
    *,
    column_count: int,
) -> dict[str, Any]:
    """Return blue background formatting for the spacer row above headings."""
    return repeat_cell_request(
        sheet_id,
        start_row=ROSTER_SPACER_ROW_INDEX,
        end_row=ROSTER_SPACER_ROW_INDEX + 1,
        start_col=0,
        end_col=column_count,
        cell_format={"backgroundColor": HEADER_BACKGROUND_COLOR},
        fields="userEnteredFormat.backgroundColor",
    )


def header_format_request(
    sheet_id: int,
    *,
    start_row: int,
    end_row: int,
    column_count: int,
    horizontal_alignment: str,
) -> dict[str, Any]:
    """Return a yellow-on-blue header-row formatting request."""
    return repeat_cell_request(
        sheet_id,
        start_row=start_row,
        end_row=end_row,
        start_col=0,
        end_col=column_count,
        cell_format={
            "backgroundColor": HEADER_BACKGROUND_COLOR,
            "horizontalAlignment": horizontal_alignment,
            "textFormat": {
                "foregroundColor": HEADER_TEXT_COLOR,
                "bold": True,
            },
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        },
        fields=(
            "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat,"
            "verticalAlignment,wrapStrategy)"
        ),
    )


def repeat_cell_request(
    sheet_id: int,
    *,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    cell_format: dict[str, Any],
    fields: str,
) -> dict[str, Any]:
    """Return a Sheets API repeatCell formatting request."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col,
            },
            "cell": {"userEnteredFormat": cell_format},
            "fields": fields,
        }
    }


def sheet_id_for_title(
    sheets_service: Any,
    spreadsheet_id: str,
    sheet_name: str,
) -> int:
    """Look up the numeric sheet ID for a worksheet/tab title."""
    metadata = get_spreadsheet(sheets_service, spreadsheet_id)
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == sheet_name:
            return int(properties["sheetId"])
    raise ConfigError(
        f"spreadsheet {spreadsheet_id} does not contain sheet {sheet_name!r}"
    )


def sheet_name_from_a1_range(range_name: str) -> str:
    """Extract the sheet/tab name from a sheet-qualified A1 range."""
    if "!" not in range_name:
        return "Sheet1"
    raw_name = range_name.split("!", 1)[0]
    if raw_name.startswith("'") and raw_name.endswith("'"):
        return raw_name[1:-1].replace("''", "'")
    return raw_name


def ministry_roster_members(
    data: ParishSoftData,
    ministry_names: Sequence[str],
) -> list[RosterMember]:
    """Return roster members belonging to any of ``ministry_names``.

    A member is included when they belong to at least one configured ministry.
    Their roles across the matching ministries are deduplicated, sorted, and
    joined into a single comma-separated role string. Empty roles do not appear
    in the joined text, but members with only blank roles are still included
    with an empty role cell because ParishSoft permits membership without a
    role. Results are sorted by the member sort key for stable output.
    """
    configured = set(ministry_names)
    members = []
    for member in data.members.values():
        ministries = member.get("py ministries", {})
        matching_entries = [
            entry for name, entry in ministries.items() if name in configured
        ]
        if matching_entries:
            roles = sorted(
                {str(entry.get("role") or "").strip() for entry in matching_entries}
            )
            members.append(
                RosterMember(
                    member=member,
                    role=", ".join(role for role in roles if role),
                )
            )
    return sorted(members, key=lambda item: member_sort_key(item.member))


def workgroup_roster_members(
    data: ParishSoftData,
    workgroup_name: str,
    *,
    leader_suffix: str,
) -> list[RosterMember]:
    """Return roster members for a single workgroup.

    ParishSoft models leaders as a separate companion workgroup named
    ``workgroup_name + leader_suffix``. Members in that companion group are
    labeled ``"Leader"``; members in the base workgroup are labeled
    ``"Member"``. Results are sorted by the member sort key.
    """
    leader_name = f"{workgroup_name}{leader_suffix}"
    members = []
    for member in data.members.values():
        workgroups = member.get("py workgroups", {})
        if leader_name in workgroups:
            members.append(RosterMember(member=member, role="Leader"))
        elif workgroup_name in workgroups:
            members.append(RosterMember(member=member, role="Member"))
    return sorted(members, key=lambda item: member_sort_key(item.member))


def roster_role_matches(role_text: str, allowed_roles: set[str]) -> bool:
    """Report whether any role in a comma-separated string is allowed.

    ``role_text`` is the joined role string produced for a roster member, so it
    is split on commas and each piece is stripped before comparison against
    ``allowed_roles``.
    """
    return any(role.strip() in allowed_roles for role in role_text.split(","))


def roster_values(
    title: str,
    members: Sequence[RosterMember],
    *,
    include_birthday: bool,
    now: dt.datetime | None = None,
) -> list[list[Any]]:
    """Build the 2-D cell grid for one roster sheet.

    The grid starts with a title row, a "Last updated" timestamp, a blank
    spacer row, and a header row, followed by one or more rows per member. The
    birthday column is included only when ``include_birthday`` is set. A member
    with both phone number(s) and email gets a second row so email stays
    visually separate from phone contact. ``now`` is injectable so the timestamp
    is deterministic in tests; it defaults to the current local time truncated
    to whole seconds. The steps are kept explicit so operational behavior
    remains easy to audit and test.
    """
    timestamp = format_update_timestamp(now or dt.datetime.now())
    headers = ["Member name", "Address", "Phone / email"]
    if include_birthday:
        headers.append("Birthday")
    headers.append("Role")
    rows: list[list[Any]] = [
        [f"Ministry: {title}"],
        [f"Last updated: {timestamp}"],
        [],
        headers,
    ]
    for roster_member in sorted(members, key=lambda item: member_sort_key(item.member)):
        rows.extend(
            roster_member_rows(roster_member, include_birthday=include_birthday)
        )
    return rows


def current_roster_time(timezone_name: str) -> dt.datetime:
    """Return the current update time in the configured roster timezone."""
    return dt.datetime.now(ZoneInfo(timezone_name)).replace(microsecond=0)


def roster_spreadsheet_title(title: str, update_time: dt.datetime) -> str:
    """Return the spreadsheet document title for one roster update."""
    return f"{title} as of {format_update_timestamp(update_time)}"


def format_update_timestamp(update_time: dt.datetime) -> str:
    """Format an update timestamp, including timezone abbreviation when known."""
    value = update_time.replace(microsecond=0)
    timestamp = value.strftime("%Y-%m-%d %H:%M:%S")
    timezone = value.strftime("%Z")
    if timezone:
        return f"{timestamp} {timezone}"
    return timestamp


def roster_member_rows(
    roster_member: RosterMember,
    *,
    include_birthday: bool,
) -> list[list[Any]]:
    """Build one or two output rows for a roster member.

    Phone numbers stay on the row with the member name. If an email address is
    also present, it moves to a continuation row so phone and email are not
    mixed in the same cell. When no phone number is present, email remains on
    the main row.
    """
    member = roster_member.member
    phone_text = member_phone_contact(member)
    email = get_member_public_email(member) or ""
    first_contact = phone_text or email
    first_row = [
        member.get("py friendly name LF") or member_display_name(member),
        member_address(member),
        first_contact,
    ]
    if include_birthday:
        first_row.append(member_birthday(member))
    first_row.append(roster_member.role)
    rows = [first_row]
    if phone_text and email:
        continuation_row = ["", "", email]
        if include_birthday:
            continuation_row.append("")
        continuation_row.append("")
        rows.append(continuation_row)
    return rows


def member_sort_key(member: Mapping[str, Any]) -> str:
    """Return a stable sort key for a roster member."""
    name = member.get("py friendly name LF") or member_display_name(member)
    return f"{name} {member.get('memberDUID', '')}"


def member_display_name(member: Mapping[str, Any]) -> str:
    """Return the roster display name for a member."""
    first = str(member.get("firstName") or "")
    last = str(member.get("lastName") or "")
    # Strip stray commas/spaces so a missing first or last name does not leave a
    # dangling separator (e.g. "Smith, " or ", John").
    return f"{last}, {first}".strip(", ")


def member_address(member: Mapping[str, Any]) -> str:
    """Return the roster mailing address for a member."""
    family = member.get("py family") or {}
    parts = [
        family.get("primaryAddress1"),
        family.get("primaryAddress2"),
        city_state_zip(family),
    ]
    return "\n".join(str(part) for part in parts if part)


def city_state_zip(family: Mapping[str, Any]) -> str:
    """Format a family's city, state, and postal code into one line.

    Missing parts are skipped so the result stays clean: city and state are
    comma-joined, then the postal code (if any) is appended after a space.
    """
    city = family.get("primaryCity")
    state = family.get("primaryState")
    postal_code = family.get("primaryPostalCode")
    city_state = ", ".join(str(part) for part in (city, state) if part)
    if postal_code:
        return f"{city_state} {postal_code}".strip()
    return city_state


def member_phone_contact(member: dict[str, Any]) -> str:
    """Return the roster phone text for a member."""
    return "\n".join(
        f"{phone['number']} {phone['type']}"
        for phone in get_member_public_phones(member)
    )


def member_birthday(member: Mapping[str, Any]) -> str:
    """Return the roster birthday text for a member."""
    value = member.get("birthdate")
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return f"{value.strftime('%B')} {value.day}"
    return ""


def _target(
    value: Any,
    name: str,
    *,
    source_key: str,
    plural_source_key: str | None,
    source_type: str,
    default_spreadsheet_id: str | None,
    default_range: str | None,
    default_clear_range: str | None,
) -> RosterTarget:
    """Parse one ministry or workgroup target into a ``RosterTarget``.

    ``source_key``/``plural_source_key`` select the config keys that name the
    ParishSoft source(s); ``source_type`` records which kind this is. Per-target
    spreadsheet ID, range, and clear range fall back to the supplied defaults
    when omitted, and the display name defaults to the joined source names.
    Both ``include_birthday``/``birthday`` and ``role_sheets``/``role sheets``
    spellings are accepted. The steps are kept explicit so operational behavior
    remains easy to audit and test.
    """
    item = _mapping(value, name)
    source_names = _source_names(item, name, source_key, plural_source_key)
    target_name = _optional_string(item.get("name"), f"{name}.name") or ", ".join(
        source_names
    )
    spreadsheet_id = _target_spreadsheet_id(item, name, default_spreadsheet_id)
    range_name = _optional_string(item.get("range"), f"{name}.range") or (
        default_range or DEFAULT_RANGE
    )
    clear_range = _optional_string(item.get("clear_range"), f"{name}.clear_range") or (
        default_clear_range or DEFAULT_CLEAR_RANGE
    )
    include_birthday = _bool(
        item.get("include_birthday", item.get("birthday", False)),
        f"{name}.include_birthday",
    )
    role_sheets_value = item.get("role_sheets", item.get("role sheets", []))
    role_sheets = tuple(
        _role_target(
            role_sheet,
            f"{name}.role_sheets[{index}]",
            default_spreadsheet_id=spreadsheet_id,
            default_range=range_name,
            default_clear_range=clear_range,
        )
        for index, role_sheet in enumerate(
            _list(role_sheets_value, f"{name}.role_sheets")
        )
    )
    return RosterTarget(
        name=target_name,
        source_type=source_type,
        source_names=tuple(source_names),
        spreadsheet_id=spreadsheet_id,
        range_name=range_name,
        clear_range=clear_range,
        include_birthday=include_birthday,
        role_sheets=role_sheets,
    )


def _role_target(
    value: Any,
    name: str,
    *,
    default_spreadsheet_id: str,
    default_range: str,
    default_clear_range: str,
) -> RoleRosterTarget:
    """Parse one role-specific roster target."""
    item = _mapping(value, name)
    role_name = _required_string(item.get("name"), f"{name}.name")
    roles = tuple(_string_list(item.get("roles"), f"{name}.roles"))
    spreadsheet_id = _target_spreadsheet_id(item, name, default_spreadsheet_id)
    range_name = _optional_string(item.get("range"), f"{name}.range") or default_range
    clear_range = (
        _optional_string(item.get("clear_range"), f"{name}.clear_range")
        or default_clear_range
    )
    return RoleRosterTarget(
        name=role_name,
        roles=roles,
        spreadsheet_id=spreadsheet_id,
        range_name=range_name,
        clear_range=clear_range,
    )


def _source_names(
    item: Mapping[str, Any],
    name: str,
    source_key: str,
    plural_source_key: str | None,
) -> list[str]:
    """Parse ministry or workgroup names from config."""
    singular = item.get(source_key)
    plural = item.get(plural_source_key) if plural_source_key else None
    if singular and plural:
        raise ConfigError(
            f"{name} must not set both {source_key} and {plural_source_key}"
        )
    if singular is not None:
        return [_required_string(singular, f"{name}.{source_key}")]
    if plural_source_key:
        return _string_list(plural, f"{name}.{plural_source_key}")
    raise ConfigError(f"{name}.{source_key} is required")


def _target_spreadsheet_id(
    item: Mapping[str, Any],
    name: str,
    default_spreadsheet_id: str | None,
) -> str:
    """Resolve the spreadsheet ID for a roster target."""
    value = item.get("spreadsheet_id", item.get("gsheet_id", default_spreadsheet_id))
    return _required_string(value, f"{name}.spreadsheet_id")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    """Read a mapping config value."""
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _list(value: Any, name: str) -> list[Any]:
    """Read a list config value."""
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    """Read a string list config value."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    if not value:
        raise ConfigError(f"{name} must not be empty")
    return value


def _required_string(value: Any, name: str) -> str:
    """Read a required string config value."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a string")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    """Read an optional string config value."""
    if value in (None, ""):
        return None
    return _required_string(value, name)


def _bool(value: Any, name: str) -> bool:
    """Read a boolean config value."""
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value
