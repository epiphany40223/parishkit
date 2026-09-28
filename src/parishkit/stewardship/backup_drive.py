"""Off-site copies of the sealed backup sets in a Google Drive folder.

The backup profile already writes each set sealed to the human-held recipient
key, so the copies here are ciphertext plus a plaintext manifest of sizes and
digests: nothing a Drive reader could open without the private key. Uploads
use the deployment's Google Workspace service account with domain-wide
delegation, impersonating the delegated mailbox user, with the Drive scope.
That user must be a Content manager of the target folder's shared drive (or
own the folder in My Drive).

Each set becomes one subfolder named like the host directory, tagged with an
app property so retention only ever touches folders this code created. A set
whose subfolder already holds all three files with matching sizes and MD5
digests is not uploaded again, so a retried run is idempotent.

Only small, typed failure categories leave this module; provider error text,
URLs with tokens and credential material never do.
"""

import hashlib
import json
import re
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from parishkit.config import ConfigError

from .backup import SET_NAME

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
# The tag marks folders this code created; retention never touches others.
TAG_KEY = "parishkitStewardshipBackup"
TAG_VALUE = "v1"
# Off-site sets kept in the Drive folder, matching the host's retention
# (backup.RETAINED_SETS); older tagged set folders go to the Drive trash.
RETAINED_SETS = 30
# Drive file and folder IDs are URL-safe base64-like tokens.
FOLDER_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
REQUEST_SECONDS = 60
# How long a queued "Test access" check may wait. The installer closes older
# ones unanswered without contacting Drive, and the page says so meanwhile.
PROBE_WAIT = timedelta(minutes=5)
# A sealed dump can be large; the single-request body upload streams the file.
UPLOAD_SECONDS = 3600


class DriveFailure(Exception):
    """A typed, plain-language failure; never carries provider text or secrets."""

    MESSAGES = {
        "authorization": (
            "Google refused the service account for Google Drive. In the Google "
            "Admin console, add the scope https://www.googleapis.com/auth/drive "
            "to the service account's domain-wide delegation."
        ),
        "api_disabled": (
            "The Google Drive API is not enabled in the Google Cloud project that "
            "owns the service account. Enable it, then try again."
        ),
        "not_found": (
            "The folder was not found, or the delegated mailbox user cannot see "
            "it. Check the folder link, and add that user to the shared drive."
        ),
        "permission": (
            "The delegated mailbox user can see the folder but cannot add files "
            "to it. Give that user Content manager access to the shared drive."
        ),
        "not_folder": "That link is not a Google Drive folder.",
        "credential": (
            "The Google Workspace key on the server could not be read. Check the "
            "Google Workspace integration."
        ),
        "verification": (
            "Google Drive stored a file that does not match the backup on the "
            "server. The copy will be tried again with the next backup."
        ),
        "unavailable": "Google Drive could not be reached. Try again later.",
        "unexpected": (
            "The copy failed for an unexpected reason. It will be tried again "
            "with the next backup; the server log names the category."
        ),
        "unanswered": (
            "The check was not answered. The Google Workspace installer service "
            "may not be running; ask the server administrator."
        ),
    }

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind

    def __str__(self):
        """Only the fixed category is ever shown or logged."""
        return self.kind

    @property
    def message(self):
        """The Admin-facing explanation for this category."""
        return self.MESSAGES.get(self.kind, self.MESSAGES["unexpected"])

    @property
    def retryable(self):
        """Transient failures are retried; configuration problems are not."""
        return self.kind in {"unavailable", "verification"}


def folder_id_from_url(value):
    """Return the folder ID from a Drive folder link (or a bare folder ID).

    Accepts the links Drive shows in the address bar and in "Copy link":
    ``/drive/folders/<id>``, ``/drive/u/<n>/folders/<id>`` and
    ``/open?id=<id>``, with or without query strings. Anything else is
    refused so a document link or another site cannot be saved by mistake.
    """
    if type(value) is not str:
        raise ValueError("A Google Drive folder link is required.")
    value = value.strip()
    if FOLDER_ID.fullmatch(value):
        return value
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname != "drive.google.com":
        raise ValueError("Enter a link to a folder on drive.google.com.")
    segments = [segment for segment in parts.path.split("/") if segment]
    candidate = None
    if "folders" in segments and segments.index("folders") + 1 < len(segments):
        candidate = segments[segments.index("folders") + 1]
    elif segments == ["open"]:
        candidate = (parse_qs(parts.query).get("id") or [None])[0]
    if candidate is None or not FOLDER_ID.fullmatch(candidate):
        raise ValueError("Enter a link to a Google Drive folder.")
    return candidate


def workspace_session(credential_value, *, subject):
    """Build an authorized HTTP session for the delegated user, Drive scope only."""
    from google.auth.transport.requests import AuthorizedSession

    from parishkit.google.auth import load_service_account_info

    from .accounts.integration_candidates import workspace_info
    from .accounts.policy_schema import normalized_email

    try:
        info = workspace_info(credential_value)
        credentials = load_service_account_info(
            info, scopes=[DRIVE_SCOPE], subject=normalized_email(subject)
        )
    except ConfigError:
        raise DriveFailure("credential") from None
    return AuthorizedSession(credentials)


class DriveClient:
    """The few Drive v3 calls the off-site copy needs, over one HTTP session.

    Every call names ``supportsAllDrives`` so shared drives work, and maps
    failures to :class:`DriveFailure` categories.
    """

    def __init__(self, session):
        self.session = session

    def _call(self, method, url, *, params=None, timeout=REQUEST_SECONDS, **kwargs):
        """Send one request and map every failure to a fixed category."""
        from google.auth.exceptions import RefreshError, TransportError
        from requests import RequestException

        params = {"supportsAllDrives": "true", **(params or {})}
        try:
            response = self.session.request(
                method, url, params=params, timeout=timeout, **kwargs
            )
        except RefreshError:
            # An unauthorized client means the delegation lacks the Drive scope.
            raise DriveFailure("authorization") from None
        except (TransportError, RequestException, OSError):
            raise DriveFailure("unavailable") from None
        if response.status_code < 300:
            return response
        raise DriveFailure(_classify(response))

    def folder(self, folder_id):
        """Return the target folder's metadata, or fail if it is unusable."""
        data = self._call(
            "GET",
            f"{API}/{folder_id}",
            params={
                "fields": "id,mimeType,trashed,capabilities(canAddChildren)",
            },
        ).json()
        if data.get("mimeType") != FOLDER_MIME or data.get("trashed"):
            raise DriveFailure("not_folder")
        if not (data.get("capabilities") or {}).get("canAddChildren"):
            raise DriveFailure("permission")
        return data

    def children(self, parent, *, tagged=False, folders=None):
        """List the non-trashed children of one folder, following pages."""
        query = [f"'{parent}' in parents", "trashed = false"]
        if tagged:
            query.append(
                f"appProperties has {{ key='{TAG_KEY}' and value='{TAG_VALUE}' }}"
            )
        if folders is True:
            query.append(f"mimeType = '{FOLDER_MIME}'")
        params = {
            "q": " and ".join(query),
            "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum)",
            "pageSize": "100",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }
        found = []
        while True:
            data = self._call("GET", API, params=params).json()
            found.extend(data.get("files", []))
            token = data.get("nextPageToken")
            if not token:
                return found
            params["pageToken"] = token

    def create_folder(self, name, parent):
        """Create one tagged subfolder and return its ID."""
        body = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent],
            "appProperties": {TAG_KEY: TAG_VALUE},
        }
        return self._call("POST", API, params={"fields": "id"}, json=body).json()["id"]

    def upload(
        self,
        path,
        name,
        parent,
        *,
        content_type="application/octet-stream",
        timeout=UPLOAD_SECONDS,
    ):
        """Upload one file with a resumable session; return id, size and MD5."""
        metadata = {"name": name, "parents": [parent]}
        start = self._call(
            "POST",
            UPLOAD,
            params={"uploadType": "resumable"},
            data=json.dumps(metadata),
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": content_type,
            },
        )
        location = start.headers.get("Location")
        if not location or not location.startswith(UPLOAD):
            raise DriveFailure("unavailable")
        size = path.stat().st_size
        with path.open("rb") as stream:
            data = self._call(
                "PUT",
                location,
                params={"fields": "id,size,md5Checksum"},
                data=stream,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(size),
                },
                timeout=timeout,
            ).json()
        return data

    def trash(self, file_id):
        """Move one file or folder to the trash (Content managers may trash)."""
        self._call(
            "PATCH", f"{API}/{file_id}", params={"fields": "id"}, json={"trashed": True}
        )


def _classify(response):
    """Map a Drive error response to a fixed category without keeping its text."""
    reason = ""
    try:
        errors = response.json().get("error", {}).get("errors") or []
        reason = str(errors[0].get("reason", "")) if errors else ""
    except (ValueError, AttributeError, IndexError, TypeError):
        pass
    if response.status_code == 401:
        return "authorization"
    if response.status_code == 403:
        if reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
            return "api_disabled"
        if reason in {"rateLimitExceeded", "userRateLimitExceeded"}:
            return "unavailable"
        return "permission"
    if response.status_code == 404:
        return "not_found"
    return "unavailable"


def _md5(path):
    """Digest one local file the way Drive reports md5Checksum."""
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def upload_set(client, folder_id, directory, names):
    """Copy one complete sealed set into a tagged subfolder named like it.

    An existing subfolder that already holds every file with the local size
    and MD5 is reused unchanged; an incomplete or mismatched one is trashed
    and written again, so a partial earlier attempt never counts as a copy.
    Returns the subfolder ID.
    """
    local = {
        name: (str((directory / name).stat().st_size), _md5(directory / name))
        for name in names
    }
    for existing in client.children(folder_id, tagged=True, folders=True):
        if existing.get("name") != directory.name:
            continue
        stored = {
            item.get("name"): (item.get("size"), item.get("md5Checksum"))
            for item in client.children(existing["id"])
        }
        if stored == local:
            return existing["id"]
        client.trash(existing["id"])
    subfolder = client.create_folder(directory.name, folder_id)
    for name in names:
        result = client.upload(directory / name, name, subfolder)
        if (result.get("size"), result.get("md5Checksum")) != local[name]:
            raise DriveFailure("verification")
    return subfolder


def prune(client, folder_id, *, keep=RETAINED_SETS):
    """Trash tagged set folders beyond the newest ``keep``; never touch others."""
    sets = sorted(
        (
            item
            for item in client.children(folder_id, tagged=True, folders=True)
            if SET_NAME.fullmatch(item.get("name", ""))
        ),
        key=lambda item: item["name"],
    )
    for item in sets[:-keep] if keep else sets:
        client.trash(item["id"])


def probe(client, folder_id):
    """Prove the delegated user can add to and trash in the folder.

    Writes one tiny, clearly named file and trashes it again, so the result
    reflects the same permissions a real copy needs.
    """
    import tempfile
    from pathlib import Path

    client.folder(folder_id)
    with tempfile.TemporaryDirectory() as scratch:
        marker = Path(scratch) / "probe.txt"
        marker.write_text(
            "ParishKit stewardship checked that it can store backups here. "
            "This file is removed immediately.\n",
            encoding="utf-8",
        )
        result = client.upload(
            marker,
            "parishkit-backup-access-check.txt",
            folder_id,
            content_type="text/plain",
            timeout=REQUEST_SECONDS,
        )
    client.trash(result["id"])


def with_retries(
    action,
    *,
    attempts=3,
    delays=(10, 60),
    sleep=time.sleep,
    deadline=None,
    clock=time.monotonic,
):
    """Run ``action`` again after transient failures, with growing pauses.

    No retry starts after ``deadline`` (a ``clock`` value), so a slow or
    failing Drive bounds the whole copy rather than only each request.
    """
    for attempt in range(attempts):
        try:
            return action()
        except DriveFailure as failure:
            delay = delays[min(attempt, len(delays) - 1)]
            if (
                not failure.retryable
                or attempt == attempts - 1
                or (deadline is not None and clock() + delay >= deadline)
            ):
                raise
            sleep(delay)
    raise AssertionError("unreachable")
