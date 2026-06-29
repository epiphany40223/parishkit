"""Google Calendar helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request
from parishkit.retry import RetryPolicy

_NOTIFICATION_WRITE_POLICY = RetryPolicy(attempts=1)


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
    *,
    attendee_email: str | None = None,
) -> None:
    """Set this account's RSVP (attendee response) on a calendar event.

    Patches only this resource attendee entry, leaving other attendees
    untouched. ``attendee_email`` preserves the exact attendee address returned
    by Google when it differs in case from the configured calendar ID.
    ``response_status`` is a Calendar value such as ``accepted``, ``declined``,
    or ``tentative``; ``sendUpdates="all"`` notifies the other participants.
    """
    response_email = attendee_email or calendar_id
    request = service.events().patch(
        calendarId=calendar_id,
        sendUpdates="all",
        eventId=event_id,
        body={
            # Without attendeesOmitted, Calendar treats the attendees array as
            # the full replacement list. This flag says "only update the
            # attendee entry supplied here" and preserves all other attendees.
            "attendeesOmitted": True,
            "attendees": [
                {
                    "email": response_email,
                    "responseStatus": response_status,
                }
            ],
        },
    )
    execute_google_request(request, policy=_NOTIFICATION_WRITE_POLICY)
