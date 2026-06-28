"""Google Sheets helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request


def build_sheets_service(credentials: Any, *, build_fn: Any | None = None) -> Any:
    return build_service("sheets", "v4", credentials=credentials, build_fn=build_fn)


def get_values(
    service: Any,
    spreadsheet_id: str,
    range_name: str,
) -> list[list[Any]]:
    request = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
    )
    return execute_google_request(request).get("values", [])


def clear_values(service: Any, spreadsheet_id: str, range_name: str) -> None:
    request = (
        service.spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_name, body={})
    )
    execute_google_request(request)


def get_spreadsheet(
    service: Any,
    spreadsheet_id: str,
    *,
    fields: str = "sheets.properties",
) -> dict[str, Any]:
    """Fetch spreadsheet metadata for IDs, sheet titles, and grid properties.

    ``fields`` defaults to sheet properties only, which is enough to map a
    sheet/tab title from A1 notation to the numeric ``sheetId`` required by
    formatting requests. Returns the parsed spreadsheet metadata mapping.
    """
    request = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields=fields)
    return execute_google_request(request)


def batch_update_spreadsheet(
    service: Any,
    spreadsheet_id: str,
    requests: list[dict[str, Any]],
) -> None:
    """Apply a list of Sheets API batchUpdate requests to a spreadsheet."""
    if not requests:
        return
    request = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    )
    execute_google_request(request)


def update_values(
    service: Any,
    spreadsheet_id: str,
    range_name: str,
    values: list[list[Any]],
    *,
    value_input_option: str = "RAW",
) -> None:
    request = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=value_input_option,
            body={"values": values},
        )
    )
    execute_google_request(request)
