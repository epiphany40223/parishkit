"""Google Workspace Groups/Admin SDK helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request


def build_admin_directory_service(
    credentials: Any,
    *,
    build_fn: Any | None = None,
) -> Any:
    """Build an Admin SDK Directory API service client.

    This is the API used to read and modify group membership; it requires
    credentials with directory scopes (typically a domain-wide-delegated service
    account acting as a Workspace admin).
    """
    return build_service(
        "admin", "directory_v1", credentials=credentials, build_fn=build_fn
    )


def build_groups_settings_service(
    credentials: Any,
    *,
    build_fn: Any | None = None,
) -> Any:
    """Build a Groups Settings API service client.

    Separate from the Directory API: this one exposes per-group settings such as
    posting permissions (``whoCanPostMessage``).
    """
    return build_service(
        "groupssettings", "v1", credentials=credentials, build_fn=build_fn
    )


def list_group_members(service: Any, group_key: str) -> list[dict[str, Any]]:
    """Return all members of a Google group, following pagination.

    ``group_key`` is the group's email address or unique ID. Every page is
    fetched and concatenated, so the complete membership is returned as one
    list (empty if the group has no members).
    """
    members: list[dict[str, Any]] = []
    page_token: str | None = None
    # Loop until Google stops returning a nextPageToken, accumulating each page.
    while True:
        request = service.members().list(groupKey=group_key, pageToken=page_token)
        response = execute_google_request(request)
        members.extend(response.get("members", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return members


def get_group_posting_permissions(service: Any, group_key: str) -> str | None:
    """Return a group's ``whoCanPostMessage`` setting, or ``None`` if absent.

    Uses the Groups Settings service (not the Directory service). Requests only
    the single ``whoCanPostMessage`` field to keep the response minimal.
    """
    request = service.groups().get(groupUniqueId=group_key, fields="whoCanPostMessage")
    return execute_google_request(request).get("whoCanPostMessage")


def insert_group_member(
    service: Any,
    group_key: str,
    email: str,
    role: str,
) -> None:
    """Add ``email`` to the group with the given ``role``.

    ``role`` is a Directory API value such as ``MEMBER``, ``MANAGER``, or
    ``OWNER``.
    """
    request = service.members().insert(
        groupKey=group_key,
        body={"email": email, "role": role},
    )
    execute_google_request(request)


def update_group_member_role(
    service: Any,
    group_key: str,
    member_key: str,
    role: str,
) -> None:
    """Change an existing member's role within the group.

    ``member_key`` identifies the member to update; ``role`` is the new
    Directory API role (e.g. ``MEMBER``, ``MANAGER``, ``OWNER``).
    """
    request = service.members().update(
        groupKey=group_key,
        memberKey=member_key,
        body={"role": role},
    )
    execute_google_request(request)


def delete_group_member(service: Any, group_key: str, member_key: str) -> None:
    """Remove a member from the group.

    ``member_key`` is the member's email address or unique ID.
    """
    request = service.members().delete(groupKey=group_key, memberKey=member_key)
    execute_google_request(request)
