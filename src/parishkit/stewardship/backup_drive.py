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
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from parishkit.config import ConfigError

from .backup import (
    DUMP,
    FILES,
    MANIFEST,
    RECENT_WINDOW,
    retained,
    retention_paused,
    set_started,
)
from .observability import Event, FailureKind, emit

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
# The tag marks folders this code created; retention never touches others.
# Its value names the deployment (``deployment_tag``), so two deployments
# pointed at one folder never prune or replace each other's sets. ``TAG_VALUE``
# is the value used before that, and only by a client given no deployment.
TAG_KEY = "parishkitStewardshipBackup"
TAG_VALUE = "v1"
DEPLOYMENT_TAG = re.compile(r"^[A-Za-z0-9-]{1,100}$")
# Drive file and folder IDs are URL-safe base64-like tokens.
FOLDER_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
REQUEST_SECONDS = 60
# How long a queued "Test access" check may wait. The installer closes older
# ones unanswered without contacting Drive, and the page says so meanwhile.
PROBE_WAIT = timedelta(minutes=5)
# A sealed dump can be large; the single-request body upload streams the file.
UPLOAD_SECONDS = 3600


def log_timeout(what, *, limit_seconds, elapsed_seconds):
    """Log off-site backup work a time limit stopped: what, the limit, how long.

    This is the one place the off-site copy and the "Test access" check
    report a timeout. The process-log line carries the fields; the durable
    operational log entry (#293) is a WORK_BUDGET_REACHED warning for the two
    budgets, whose remaining work waits for the next run, and a
    TASK_TIMED_OUT warning otherwise. ``what`` is one of
    ``observability.TIMEOUT_LIMITS``.
    """
    from .audit.timeouts import record_timeout

    emit(
        Event.TASK_FAILED,
        level=logging.WARNING,
        timeout=what,
        limit_seconds=max(0, round(limit_seconds)),
        elapsed_seconds=max(0, round(elapsed_seconds)),
    )
    record_timeout(
        Event.WORK_BUDGET_REACHED if what.endswith("_budget") else Event.TASK_TIMED_OUT,
        what=what,
        level="WARNING",
        limit_seconds=limit_seconds,
        elapsed_seconds=elapsed_seconds,
    )


def deployment_tag(deployment_id):
    """The tag value for one deployment's set folders (its runtime row's ID)."""
    return f"deployment-{deployment_id}"


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

    def __init__(self, kind, *, retryable=None):
        super().__init__(kind)
        self.kind = kind
        # A failure that trying again cannot fix (a local set that no longer
        # matches its manifest) overrides the category's usual answer.
        self._retryable = retryable

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
        if self._retryable is not None:
            return self._retryable
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

    def __init__(self, session, *, tag=TAG_VALUE):
        # The tag goes into a Drive query string, so it must stay plain.
        if type(tag) is not str or not DEPLOYMENT_TAG.fullmatch(tag):
            raise ValueError("A Drive set tag is letters, digits and hyphens.")
        self.session = session
        self.tag = tag

    def _call(self, method, url, *, params=None, timeout=REQUEST_SECONDS, **kwargs):
        """Send one request and map every failure to a fixed category.

        A request stopped by its own timeout is logged with the limit and
        how long it ran, then counts as ``unavailable`` like any outage.
        """
        from google.auth.exceptions import RefreshError, TransportError
        from requests import RequestException, Timeout

        params = {"supportsAllDrives": "true", **(params or {})}
        started = time.monotonic()
        try:
            response = self.session.request(
                method, url, params=params, timeout=timeout, **kwargs
            )
        except RefreshError:
            # An unauthorized client means the delegation lacks the Drive scope.
            raise DriveFailure("authorization") from None
        except (Timeout, TimeoutError):
            log_timeout(
                "drive_request",
                limit_seconds=timeout,
                elapsed_seconds=time.monotonic() - started,
            )
            raise DriveFailure("unavailable") from None
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
                f"appProperties has {{ key='{TAG_KEY}' and value='{self.tag}' }}"
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
            "appProperties": {TAG_KEY: self.tag},
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
        """Upload one file with a resumable session; return id, size and MD5.

        Drive answers the final upload request with the file fields chosen
        when the session was started, so ``fields`` goes on that first
        request; without it the reply is only id, name and type, and every
        copy would fail verification. If a reply still lacks size or MD5,
        they are read back from the stored file.
        """
        fields = {"fields": "id,size,md5Checksum"}
        metadata = {"name": name, "parents": [parent]}
        start = self._call(
            "POST",
            UPLOAD,
            params={"uploadType": "resumable", **fields},
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
                params=fields,
                data=stream,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(size),
                },
                timeout=timeout,
            ).json()
        if data.get("size") is None or data.get("md5Checksum") is None:
            if not isinstance(data.get("id"), str) or not FOLDER_ID.match(data["id"]):
                raise DriveFailure("unavailable")
            data = self._call("GET", f"{API}/{data['id']}", params=fields).json()
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


def _local_files(directory, names):
    """Each file's size and MD5 (as Drive reports them), checked against the manifest.

    The manifest records each sealed file's SHA-256 when the set was written.
    A sealed file changed on disk since then (a failing disk, a stray edit)
    would otherwise be uploaded, "verified" against itself and recorded as a
    good copy (#305 L1). Such a set is refused as ``verification`` without a
    retry, since trying again uploads the same bytes, and logged as an ERROR
    so the operator can find the damaged set; the copy moves on to the next.
    """
    try:
        manifest = json.loads((directory / MANIFEST).read_bytes())
        expected = {
            manifest[kind]["file"]: manifest[kind]["sealed_sha256"]
            for kind in ("database", "files")
        }
    except (ValueError, KeyError, TypeError):
        expected = None
    local, sealed = {}, {}
    for name in names:
        md5 = hashlib.md5(usedforsecurity=False)
        sha256 = hashlib.sha256()
        with (directory / name).open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                md5.update(chunk)
                sha256.update(chunk)
        local[name] = (str((directory / name).stat().st_size), md5.hexdigest())
        if name != MANIFEST:
            sealed[name] = sha256.hexdigest()
    if expected != sealed:
        emit(
            Event.TASK_FAILED,
            level=logging.ERROR,
            failure_kind=FailureKind.BACKUP_SET_MISMATCH,
        )
        raise DriveFailure("verification", retryable=False)
    return local


def upload_set(client, folder_id, directory, names):
    """Copy one complete sealed set into a tagged subfolder named like it.

    The local files must still match the set's manifest (``_local_files``).
    An existing subfolder that already holds every file with the local size
    and MD5 is reused unchanged; an incomplete or mismatched one is trashed
    and written again, so a partial earlier attempt never counts as a copy.
    Returns the subfolder ID.
    """
    local = _local_files(directory, names)
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


def _complete(client, folder):
    """True when a set folder holds all three files, each with size and MD5.

    A folder missing a file is a copy that failed or was interrupted. Having
    all three is not proof the copy verified (``upload_set`` stops at the
    first mismatched file but leaves it), which is why ``prune`` also needs
    the recorded ``uploaded`` outcome.
    """
    stored = {
        item.get("name")
        for item in client.children(folder["id"])
        if item.get("size") is not None and item.get("md5Checksum") is not None
    }
    return {DUMP, FILES, MANIFEST} <= stored


def prune(client, folder_id, *, verified, now=None):
    """Trash this deployment's set folders the host's retention would not keep.

    ``verified`` names the sets with a recorded ``uploaded`` outcome for this
    folder. The rules are ``backup.retained``'s, so the Drive copy keeps the
    same daily and monthly anchors as the host, chosen only among folders
    that are both verified and complete: a failed or partial copy can never
    displace a good one. Beyond those:

    - folders started within ``RECENT_WINDOW`` are never touched;
    - an older folder missing a file is trashed (the caller holds the copy
      lock, so it is not another run's copy in progress);
    - an older complete folder without a recorded outcome is left alone: it
      may be a good copy whose row a restored database lacks, or a copy that
      failed verification; the operator removes it by hand;
    - nothing is trashed while ``backup.retention_paused`` says the clock
      looks wrong, or if any listing fails;
    - folders without this deployment's tag, or not named like a set, are
      never touched.
    """
    now = now or datetime.now(UTC)
    folders = [
        item
        for item in client.children(folder_id, tagged=True, folders=True)
        if set_started(item.get("name", ""))
    ]
    if retention_paused([item["name"] for item in folders], now):
        return
    older = [
        item for item in folders if set_started(item["name"]) < now - RECENT_WINDOW
    ]
    # Every listing happens before the first trash, so a failed one trashes
    # nothing.
    complete = {item["id"] for item in older if _complete(client, item)}
    good = [
        item for item in older if item["id"] in complete and item["name"] in verified
    ]
    keep = retained([item["name"] for item in good], now)
    for item in older:
        if item["id"] not in complete or (
            item["name"] in verified and item["name"] not in keep
        ):
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
    budget_seconds=None,
):
    """Run ``action`` again after transient failures, with growing pauses.

    No retry starts after ``deadline`` (a ``clock`` value), so a slow or
    failing Drive bounds the whole copy rather than only each request. A
    retry refused for that reason is logged with ``budget_seconds`` (the
    whole budget ``deadline`` ends) and how much of it has passed.
    """
    for attempt in range(attempts):
        try:
            return action()
        except DriveFailure as failure:
            delay = delays[min(attempt, len(delays) - 1)]
            if not failure.retryable or attempt == attempts - 1:
                raise
            if deadline is not None and (now := clock()) + delay >= deadline:
                if budget_seconds is not None:
                    log_timeout(
                        "drive_retry_budget",
                        limit_seconds=budget_seconds,
                        elapsed_seconds=now - (deadline - budget_seconds),
                    )
                raise
            sleep(delay)
    raise AssertionError("unreachable")
