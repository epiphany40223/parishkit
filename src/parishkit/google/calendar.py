"""Google Calendar helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request


def build_calendar_service(credentials: Any, *, build_fn: Any | None = None) -> Any:
    return build_service("calendar", "v3", credentials=credentials, build_fn=build_fn)


def list_events(
    service: Any,
    calendar_id: str,
    *,
    time_min: str | None = None,
    time_max: str | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        request = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
        )
        response = execute_google_request(request)
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items
