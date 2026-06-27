"""Google Workspace Groups/Admin SDK helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request


def build_admin_directory_service(
    credentials: Any,
    *,
    build_fn: Any | None = None,
) -> Any:
    return build_service(
        "admin", "directory_v1", credentials=credentials, build_fn=build_fn
    )


def list_group_members(service: Any, group_key: str) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        request = service.members().list(groupKey=group_key, pageToken=page_token)
        response = execute_google_request(request)
        members.extend(response.get("members", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return members
