"""Google Drive helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from parishkit.config import ConfigError
from parishkit.google.auth import build_service, execute_google_request
from parishkit.retry import RetryPolicy

GOOGLE_SHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_WRITE_POLICY = RetryPolicy(attempts=1)


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


def update_file_with_xlsx(
    service: Any,
    file_id: str,
    xlsx_path: str | Path,
    *,
    name: str,
) -> dict[str, Any]:
    """Replace a Drive file with an XLSX upload converted to Google Sheets.

    The old Epiphany roster workflow generated a complete workbook locally and
    uploaded it over the existing Drive file ID, asking Drive to keep the file
    as a native Google spreadsheet. That is one Drive update per roster target,
    instead of several Sheets API calls for values and formatting. The
    ``supportsAllDrives`` flag keeps the helper usable with shared drives.
    """
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise ConfigError(
            "Google Drive media uploads require the optional google dependencies; "
            "install parishkit[google]"
        ) from exc

    media = MediaFileUpload(
        str(Path(xlsx_path)),
        mimetype=XLSX_MIME_TYPE,
        resumable=False,
    )
    request = service.files().update(
        fileId=file_id,
        body={"name": name, "mimeType": GOOGLE_SHEET_MIME_TYPE},
        media_body=media,
        supportsAllDrives=True,
        fields="id,name,mimeType",
    )
    return execute_google_request(request, policy=_WRITE_POLICY)
