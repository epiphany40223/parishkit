"""Google Drive helpers."""

from __future__ import annotations

from typing import Any

from parishkit.google.auth import build_service, execute_google_request


def build_drive_service(credentials: Any, *, build_fn: Any | None = None) -> Any:
    """Build a Drive API v3 service client from the given credentials."""
    return build_service("drive", "v3", credentials=credentials, build_fn=build_fn)


def get_file_metadata(
    service: Any,
    file_id: str,
    *,
    fields: str = "id,name,mimeType,modifiedTime",
) -> dict[str, Any]:
    """Fetch metadata for a single Drive file by ID.

    ``fields`` is a Drive partial-response field list controlling which metadata
    keys come back; the default keeps the response small (id, name, MIME type,
    modified time). Returns the parsed metadata mapping.
    """
    request = service.files().get(
        fileId=file_id,
        fields=fields,
        supportsAllDrives=True,
    )
    return execute_google_request(request)
