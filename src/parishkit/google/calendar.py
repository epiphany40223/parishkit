"""Google Calendar helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request


def build_calendar_service(credentials: Any, *, build_fn: Any | None = None) -> Any:
    """Build a Calendar API v3 service client from the given credentials."""
    return build_service("calendar", "v3", credentials=credentials, build_fn=build_fn)


def list_events(
    service: Any,
    calendar_id: str,
    *,
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 2500,
) -> list[dict[str, Any]]:
    """Return all Calendar events in a time window, following pagination.

    ``time_min``/``time_max`` are RFC 3339 timestamps; recurring events are
    expanded into individual instances (``singleEvents=True``) and returned in
    start-time order. Pages are fetched until no ``nextPageToken`` remains, so
    the full result set is materialized into one list. ``max_results`` is the
    per-page cap, not an overall limit.
    """
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    # Loop until Google stops returning a nextPageToken, accumulating every page.
    while True:
        request = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
            maxResults=max_results,
        )
        response = execute_google_request(request)
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def patch_attendee_response(
    service: Any,
    calendar_id: str,
    event_id: str,
    response_status: str,
) -> None:
    """Set this account's RSVP (attendee response) on a calendar event.

    Patches only the attendee entry whose email matches ``calendar_id`` (the
    account itself), leaving other attendees untouched. ``response_status`` is a
    Calendar value such as ``accepted``, ``declined``, or ``tentative``;
    ``sendUpdates="all"`` notifies the other participants of the change.
    """
    request = service.events().patch(
        calendarId=calendar_id,
        sendUpdates="all",
        eventId=event_id,
        body={
            "attendees": [
                {
                    "email": calendar_id,
                    "responseStatus": response_status,
                }
            ]
        },
    )
    execute_google_request(request)
