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
