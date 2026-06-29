from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from parishkit.config import ConfigError
from parishkit.google.auth import GoogleAPIError
from parishkit.parishsoft import ParishSoftData
from parishkit.pk_create_ps_ministry_rosters import (
    HEADER_BACKGROUND_COLOR,
    HEADER_TEXT_COLOR,
    ROSTER_COLUMN_WIDTHS,
    ROSTER_FROZEN_ROWS,
    ROSTER_TITLE_MERGE_COLUMNS,
    RosterMember,
    load_sheets_credentials,
    ministry_roster_members,
    roster_config_from_yaml,
    roster_format_requests,
    roster_role_matches,
    roster_values,
    sheet_name_from_a1_range,
    stale_row_clear_range,
    workgroup_roster_members,
)
from parishkit.pk_create_ps_ministry_rosters import (
    main as create_ministry_rosters_main,
)


class Request:
    """Fake Sheets API request whose execute() returns a canned response."""

    def __init__(self, response=None, exc: Exception | None = None):
        """Store either the canned response or exception to raise."""
        self.response = {} if response is None else response
        self.exc = exc

    def execute(self):
        """Return the canned response unless configured to raise an error."""
        if self.exc is not None:
            raise self.exc
        return self.response


class Values:
    """Fake spreadsheet values resource recording each clear/update call."""

    def __init__(self):
        """Initialize the call recorder and optional clear failure."""
        self.calls = []
        self.clear_error: Exception | None = None
        self.update_error: Exception | None = None

    def clear(self, **kwargs):
        """Record a clear call as ("clear", kwargs)."""
        self.calls.append(("clear", kwargs))
        return Request(exc=self.clear_error)

    def update(self, **kwargs):
        """Record an update call as ("update", kwargs)."""
        self.calls.append(("update", kwargs))
        return Request(exc=self.update_error)


class Spreadsheets:
    """Fake spreadsheets resource exposing values plus metadata/format calls."""

    def __init__(self):
        self._values = Values()
        self.get_calls = []
        self.batch_update_calls = []

    def values(self):
        """Return the fake values resource."""
        return self._values

    def get(self, **kwargs):
        """Record a metadata get call and return tab titles and sheet IDs."""
        self.get_calls.append(kwargs)
        return Request(
            {
                "sheets": [
                    {"properties": {"title": "Readers", "sheetId": 101}},
                    {"properties": {"title": "Leads", "sheetId": 102}},
                    {"properties": {"title": "Movers", "sheetId": 103}},
                ]
            }
        )

    def batchUpdate(self, **kwargs):
        """Record a formatting batchUpdate call."""
        self.batch_update_calls.append(kwargs)
        return Request()


class SheetsService:
    """Fake Sheets service exposing the recording spreadsheets resource."""

    def __init__(self):
        self._spreadsheets = Spreadsheets()

    def spreadsheets(self):
        return self._spreadsheets


def test_sheets_credentials_resolve_relative_paths(tmp_path, monkeypatch):
    """Relative Google Sheets credential paths resolve against the config directory."""
    calls = []

    def fake_load(path, *, scopes, subject):
        """Capture the resolved service account path for assertion."""
        calls.append((path, scopes, subject))
        return object()

    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.load_service_account_credentials",
        fake_load,
    )

    load_sheets_credentials(
        {
            "google": {
                "service_account_file": "credentials/google-service-account.json",
                "delegated_subject": "itadmin@example.org",
            }
        },
        base_dir=tmp_path,
    )

    assert calls[0][0] == tmp_path / "credentials" / "google-service-account.json"


def write_config(tmp_path: Path, *, dry_run: bool = False) -> Path:
    """Write a rosters config (plus API key file) and return it.

    The config covers a ministry with a role sheet and a separate workgroup so
    one run exercises every roster-writing path. The setup stays local to this
    test so fixtures remain easy to understand and change.
    """
    api_key = tmp_path / "parishsoft-api-key.txt"
    api_key.write_text("key", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
common:
  dry_run: {str(dry_run).lower()}
parishsoft:
  api_key_file: {api_key}
  cache_dir: {tmp_path / "cache"}
  cache_limit: 1d
google:
  user_token_file: {tmp_path / "google-user-token.json"}
rosters:
  spreadsheet_id: default-sheet
  ministries:
    - ministry: Readers
      range: Readers!A1
      clear_range: Readers!A:Z
      include_birthday: true
      role_sheets:
        - name: Reader Leads
          roles:
            - Lead
          spreadsheet_id: lead-sheet
          range: Leads!A1
          clear_range: Leads!A:Z
  workgroups:
    - workgroup: Movers
      spreadsheet_id: movers-sheet
      range: Movers!A1
      clear_range: Movers!A:Z
""",
        encoding="utf-8",
    )
    return config


def parishsoft_data() -> ParishSoftData:
    """Build a small ParishSoftData fixture with several roster members.

    Member 1 is a Readers Lead and Movers member with published contact info;
    member 2 is a Readers Member and a Movers leader (via the " Ldr" workgroup
    suffix) with unpublished contact info; member 3 has a blank ministry role;
    and member 4 has email but no phone. The setup stays local to this test so
    fixtures remain easy to understand and change.
    """
    family = {
        "familyDUID": 10,
        "primaryAddress1": "1 Main St",
        "primaryCity": "Louisville",
        "primaryState": "KY",
        "primaryPostalCode": "40202",
    }
    members = {
        1: {
            "memberDUID": 1,
            "familyDUID": 10,
            "firstName": "Ann",
            "lastName": "Smith",
            "py friendly name LF": "Smith, Ann",
            "py family": family,
            "family_PublishPhone": True,
            "family_PublishEMail": True,
            "mobilePhone": "502-555-1000",
            "py emailAddresses": ["ann@example.org"],
            "birthdate": dt.date(1980, 5, 4),
            "py ministries": {"Readers": {"role": "Lead"}},
            "py workgroups": {"Movers": {"name": "Movers"}},
        },
        2: {
            "memberDUID": 2,
            "familyDUID": 10,
            "firstName": "Bob",
            "lastName": "Adams",
            "py friendly name LF": "Adams, Bob",
            "py family": family,
            "family_PublishPhone": False,
            "family_PublishEMail": False,
            "py ministries": {"Readers": {"role": "Member"}},
            "py workgroups": {"Movers Ldr": {"name": "Movers Ldr"}},
        },
        3: {
            "memberDUID": 3,
            "familyDUID": 10,
            "firstName": "Chris",
            "lastName": "Role",
            "py friendly name LF": "Role, Chris",
            "py family": family,
            "family_PublishPhone": False,
            "family_PublishEMail": False,
            "py ministries": {"Readers": {"role": ""}},
            "py workgroups": {},
        },
        4: {
            "memberDUID": 4,
            "familyDUID": 10,
            "firstName": "Erin",
            "lastName": "Email",
            "py friendly name LF": "Email, Erin",
            "py family": family,
            "family_PublishPhone": True,
            "family_PublishEMail": True,
            "py emailAddresses": ["erin@example.org"],
            "py ministries": {"Readers": {"role": "Member"}},
            "py workgroups": {},
        },
    }
    return ParishSoftData(
        organization_id=7,
        families={10: family},
        members=members,
        family_groups={},
        family_workgroups={},
        family_workgroup_memberships={},
        member_contactinfos={},
        member_workgroups={},
        member_workgroup_memberships={},
        ministry_types={},
        ministry_type_memberships={},
        funds={},
        pledges={},
        contributions={},
    )


def test_roster_config_validation_and_role_sheets():
    """Config parsing keeps the ministry name, multiple source ministries, and
    nested role-sheet targets (including the legacy "role sheets" key)."""
    config = roster_config_from_yaml(
        {
            "rosters": {
                "spreadsheet_id": "default-sheet",
                "ministries": [
                    {
                        "ministries": ["Readers", "Greeters"],
                        "name": "Welcome Ministers",
                        "include_birthday": True,
                        "role sheets": [
                            {
                                "name": "Leads",
                                "roles": ["Lead"],
                                "spreadsheet_id": "lead-sheet",
                            }
                        ],
                    }
                ],
            }
        }
    )

    assert config.ministries[0].name == "Welcome Ministers"
    assert config.ministries[0].source_names == ("Readers", "Greeters")
    assert config.ministries[0].role_sheets[0].spreadsheet_id == "lead-sheet"


def test_roster_config_rejects_missing_targets():
    """Config with neither ministries nor workgroups raises ConfigError."""
    with pytest.raises(ConfigError, match="ministries or workgroups"):
        roster_config_from_yaml({"rosters": {"ministries": []}})


def test_roster_config_rejects_clear_range_on_different_sheet():
    """A clear_range must target the same worksheet tab as the write range."""
    with pytest.raises(ConfigError, match="same sheet"):
        roster_config_from_yaml(
            {
                "rosters": {
                    "spreadsheet_id": "default-sheet",
                    "ministries": [
                        {
                            "ministry": "Readers",
                            "range": "Readers!A1",
                            "clear_range": "Wrong!A:Z",
                        }
                    ],
                }
            }
        )


def test_roster_generation_for_ministries_and_workgroups():
    """Roster members are sorted by name, roles resolve from ministry data and
    the workgroup leader suffix, and role matching is case/list aware."""
    data = parishsoft_data()

    ministry_members = ministry_roster_members(data, ["Readers"])
    workgroup_members = workgroup_roster_members(
        data,
        "Movers",
        leader_suffix=" Ldr",
    )

    assert [(item.member["memberDUID"], item.role) for item in ministry_members] == [
        (2, "Member"),
        (4, "Member"),
        (3, ""),
        (1, "Lead"),
    ]
    assert [(item.member["memberDUID"], item.role) for item in workgroup_members] == [
        (2, "Leader"),
        (1, "Member"),
    ]
    assert roster_role_matches("Lead, Member", {"Lead"})


def test_roster_values_separate_phone_and_email_rows():
    """roster_values separates email from phone when both contact types exist."""
    data = parishsoft_data()
    update_time = dt.datetime(
        2026,
        1,
        2,
        3,
        4,
        tzinfo=ZoneInfo("America/Kentucky/Louisville"),
    )

    values = roster_values(
        "Readers",
        [
            RosterMember(member=data.members[1], role="Lead"),
            RosterMember(member=data.members[4], role="Member"),
        ],
        include_birthday=True,
        now=update_time,
    )

    assert values[:4] == [
        ["Ministry: Readers"],
        ["Last updated: 2026-01-02 03:04:00 EST"],
        [],
        ["Member name", "Address", "Phone / email", "Birthday", "Role"],
    ]
    assert values[4] == [
        "Email, Erin",
        "1 Main St\nLouisville, KY 40202",
        "erin@example.org",
        "",
        "Member",
    ]
    assert values[5] == [
        "Smith, Ann",
        "1 Main St\nLouisville, KY 40202",
        "502-555-1000 cell",
        "May 4",
        "Lead",
    ]
    assert values[6] == ["", "", "ann@example.org", "", ""]


def test_roster_format_requests_freeze_headers_and_size_columns():
    """Formatting requests freeze rows, style headers, top-align cells, and
    set the expected column widths."""
    requests = roster_format_requests(
        99,
        spreadsheet_title="Readers as of 2026-01-02 03:04:00 EST",
        column_count=5,
        row_count=8,
    )

    assert requests[0] == {
        "updateSheetProperties": {
            "properties": {
                "sheetId": 99,
                "gridProperties": {"frozenRowCount": ROSTER_FROZEN_ROWS},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    }
    assert requests[1]["repeatCell"]["cell"]["userEnteredFormat"] == {
        "verticalAlignment": "TOP",
        "wrapStrategy": "WRAP",
    }
    assert requests[2]["repeatCell"]["cell"]["userEnteredFormat"] == {
        "backgroundColor": HEADER_BACKGROUND_COLOR,
        "horizontalAlignment": "LEFT",
        "textFormat": {
            "foregroundColor": HEADER_TEXT_COLOR,
            "bold": True,
        },
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    }
    assert requests[3]["repeatCell"]["range"]["startRowIndex"] == 2
    assert requests[3]["repeatCell"]["cell"]["userEnteredFormat"] == {
        "backgroundColor": HEADER_BACKGROUND_COLOR
    }
    assert requests[4]["repeatCell"]["range"]["startRowIndex"] == 3
    assert (
        requests[4]["repeatCell"]["cell"]["userEnteredFormat"]["horizontalAlignment"]
        == "CENTER"
    )
    assert requests[5:9] == [
        {
            "unmergeCells": {
                "range": {
                    "sheetId": 99,
                    "startRowIndex": row_index,
                    "endRowIndex": row_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": ROSTER_TITLE_MERGE_COLUMNS,
                }
            }
        }
        if operation == "unmergeCells"
        else {
            "mergeCells": {
                "range": {
                    "sheetId": 99,
                    "startRowIndex": row_index,
                    "endRowIndex": row_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": ROSTER_TITLE_MERGE_COLUMNS,
                },
                "mergeType": "MERGE_ALL",
            }
        }
        for row_index in range(2)
        for operation in ("unmergeCells", "mergeCells")
    ]
    widths = [
        request["updateDimensionProperties"]["properties"]["pixelSize"]
        for request in requests[9:-1]
    ]
    assert widths == list(ROSTER_COLUMN_WIDTHS)
    assert requests[-1] == {
        "updateSpreadsheetProperties": {
            "properties": {"title": "Readers as of 2026-01-02 03:04:00 EST"},
            "fields": "title",
        }
    }


def test_sheet_name_from_a1_range_handles_quoted_and_default_ranges():
    """A1 sheet-name parsing supports regular, quoted, escaped, and bare ranges."""
    assert sheet_name_from_a1_range("Roster!A1") == "Roster"
    assert sheet_name_from_a1_range("'Sunday Roster'!A1:E20") == "Sunday Roster"
    assert sheet_name_from_a1_range("'Pastor''s Roster'!A1") == "Pastor's Roster"
    assert sheet_name_from_a1_range("A1:E20") == "Sheet1"


def test_create_ministry_rosters_main_writes_sheet_values(
    tmp_path,
    monkeypatch,
    capsys,
):
    """main updates then clears stale rows, routing role-sheet and
    workgroup rows to their own spreadsheet ids.

    The ParishSoft client is stubbed out so no real data is loaded, and the
    loader is replaced with a recorder to confirm the expected load options.
    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    service = SheetsService()
    loader_calls = []
    # Avoid building a real ParishSoft client; the loader returns canned data.
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    def loader(_client, **kwargs):
        """Record loader kwargs and return the canned ParishSoft fixture."""
        loader_calls.append(kwargs)
        return parishsoft_data()

    assert (
        create_ministry_rosters_main(
            ["--config", str(write_config(tmp_path)), "--debug"],
            loader=loader,
            sheets_factory=lambda _config: service,
        )
        == 0
    )

    error = capsys.readouterr().err
    assert "Ministry roster operation completed successfully" in error
    assert "Ministry roster targets: Readers" in error
    assert "Workgroup roster targets: Movers" in error
    assert "RosterTarget(" not in error
    assert loader_calls == [{"active_only": True, "parishioners_only": False}]
    # Each target is an update followed by a stale-row clear; ministry first,
    # then its role sheet, then the workgroup, on their respective spreadsheet
    # ids.
    calls = service._spreadsheets._values.calls
    assert calls[0][0] == "update"
    assert calls[0][1]["spreadsheetId"] == "default-sheet"
    assert calls[0][1]["range"] == "Readers!A1"
    assert calls[0][1]["body"]["values"][4][-1] == "Member"
    assert calls[0][1]["body"]["values"][6][-1] == ""
    assert calls[1] == (
        "clear",
        {
            "spreadsheetId": "default-sheet",
            "range": "Readers!A10:Z",
            "body": {},
        },
    )
    assert calls[2][0] == "update"
    assert calls[2][1]["spreadsheetId"] == "lead-sheet"
    assert calls[2][1]["body"]["values"][4][0] == "Smith, Ann"
    assert calls[4][0] == "update"
    assert calls[4][1]["spreadsheetId"] == "movers-sheet"
    assert service._spreadsheets.get_calls == [
        {"spreadsheetId": "default-sheet", "fields": "sheets.properties"},
        {"spreadsheetId": "lead-sheet", "fields": "sheets.properties"},
        {"spreadsheetId": "movers-sheet", "fields": "sheets.properties"},
    ]
    assert [
        call["body"]["requests"][0]["updateSheetProperties"]["properties"]["sheetId"]
        for call in service._spreadsheets.batch_update_calls
    ] == [101, 102, 103]
    assert all(
        call["body"]["requests"][0]["updateSheetProperties"]["properties"][
            "gridProperties"
        ]["frozenRowCount"]
        == 4
        for call in service._spreadsheets.batch_update_calls
    )
    assert [
        call["body"]["requests"][-1]["updateSpreadsheetProperties"]["properties"][
            "title"
        ].startswith(prefix)
        for call, prefix in zip(
            service._spreadsheets.batch_update_calls,
            [
                "Readers as of ",
                "Reader Leads as of ",
                "Movers as of ",
            ],
            strict=True,
        )
    ] == [True, True, True]


def test_create_ministry_rosters_dry_run_skips_sheet_writes(tmp_path, monkeypatch):
    """In dry-run mode main loads data but performs no clear/update sheet writes."""
    service = SheetsService()
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        create_ministry_rosters_main(
            ["--config", str(write_config(tmp_path, dry_run=True))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            sheets_factory=lambda _config: service,
        )
        == 0
    )

    assert service._spreadsheets._values.calls == []
    assert service._spreadsheets.get_calls == []
    assert service._spreadsheets.batch_update_calls == []


def test_stale_row_clear_range_starts_below_written_roster():
    """Stale-row cleanup preserves the rows that were just written."""
    assert stale_row_clear_range("Readers!A:Z", 7) == "Readers!A8:Z"
    assert stale_row_clear_range("'Sunday Readers'!A1:Z500", 7) == (
        "'Sunday Readers'!A8:Z"
    )


def test_create_ministry_rosters_update_failure_does_not_clear_existing_sheet(
    tmp_path,
    monkeypatch,
):
    """A failed values update must leave the old roster intact."""
    service = SheetsService()
    service._spreadsheets._values.update_error = GoogleAPIError(
        500,
        "temporary Sheets failure",
    )
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        create_ministry_rosters_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            sheets_factory=lambda _config: service,
        )
        == 2
    )

    calls = service._spreadsheets._values.calls
    assert calls[0][0] == "update"
    assert all(call[0] != "clear" for call in calls)


def test_create_ministry_rosters_dry_run_does_not_require_google_config(
    tmp_path,
    monkeypatch,
):
    """Dry-run roster generation does not build a Google Sheets service."""
    config = write_config(tmp_path, dry_run=True)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace(
            f"google:\n  user_token_file: {tmp_path / 'google-user-token.json'}\n",
            "",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        create_ministry_rosters_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
        )
        == 0
    )


def test_create_ministry_rosters_reports_missing_parishsoft_source(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Unknown roster ministry/workgroup source names abort before sheet writes."""
    service = SheetsService()
    config = write_config(tmp_path)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("Readers", "Missing Readers").replace(
            "Movers",
            "Missing Movers",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        create_ministry_rosters_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            sheets_factory=lambda _config: service,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_create_ps_ministry_rosters" in error
    assert "Configured ParishSoft ministry was not found" in error
    assert "rosters.ministries[].ministry" in error
    assert service._spreadsheets._values.calls == []


def test_create_ministry_rosters_validation_uses_custom_leader_suffix(
    tmp_path,
    monkeypatch,
):
    """A leader-only workgroup source can use the configured suffix."""
    service = SheetsService()
    config = write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "rosters:\n",
            'rosters:\n  workgroup_leader_suffix: " Captain"\n',
        ),
        encoding="utf-8",
    )
    data = parishsoft_data()
    data.members[1]["py workgroups"] = {}
    data.members[2]["py workgroups"] = {"Movers Captain": {"name": "Movers Captain"}}
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        create_ministry_rosters_main(
            ["--config", str(config)],
            loader=lambda _client, **_kwargs: data,
            sheets_factory=lambda _config: service,
        )
        == 0
    )

    movers_update = service._spreadsheets._values.calls[4]
    assert movers_update[0] == "update"
    assert movers_update[1]["body"]["values"][4][0] == "Adams, Bob"
    assert movers_update[1]["body"]["values"][4][-1] == "Leader"


def test_create_ministry_rosters_reports_invalid_yaml(tmp_path, capsys):
    """Invalid YAML exits cleanly with a repair-oriented error message."""
    config = tmp_path / "config.yaml"
    config.write_text("common:\n  dry_run: true\n  bad: [\n", encoding="utf-8")

    assert create_ministry_rosters_main(["--config", str(config)]) == 2

    error = capsys.readouterr().err
    assert "ERROR: could not parse YAML config file" in error
    assert "Check indentation" in error


def test_create_ministry_rosters_logs_config_validation_error(tmp_path, capsys):
    """Roster config validation failures are logged as ERROR before exit."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "common:\n  dry_run: true\nrosters:\n  ministries: []\n",
        encoding="utf-8",
    )

    assert create_ministry_rosters_main(["--config", str(config)]) == 2

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_create_ps_ministry_rosters" in error
    assert "Configuration validation failed" in error
    assert "rosters must configure ministries or workgroups" in error


def test_create_ministry_rosters_explains_unparseable_sheet_range(
    tmp_path,
    monkeypatch,
    capsys,
):
    """A Sheets range parse failure points operators at the configured ranges."""
    service = SheetsService()
    service._spreadsheets._values.clear_error = GoogleAPIError(
        400,
        'HTTP 400 returned "Unable to parse range: Sheet1!A:Z"',
    )
    monkeypatch.setattr(
        "parishkit.pk_create_ps_ministry_rosters.parishsoft_client_from_config",
        lambda _common, _config: SimpleNamespace(),
    )

    assert (
        create_ministry_rosters_main(
            ["--config", str(write_config(tmp_path))],
            loader=lambda _client, **_kwargs: parishsoft_data(),
            sheets_factory=lambda _config: service,
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "ERROR parishkit.pk_create_ps_ministry_rosters" in error
    assert "Google Sheets rejected a configured roster range" in error
    assert "spreadsheet default-sheet" in error
    assert "range='Readers!A1'" in error
    assert "clear_range='Readers!A:Z'" in error
    assert "worksheet/tab 'Readers'" in error
