"""Off-site backup copies to Google Drive, against an in-memory Drive."""

import hashlib
import json
import logging

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import backup_drive, observability
from parishkit.stewardship.backup_drive import (
    DriveClient,
    DriveFailure,
    folder_id_from_url,
    probe,
    prune,
    upload_set,
    with_retries,
)
from parishkit.stewardship.backup_offsite import SEALED_FILES, destination_from

from .drive_fakes import FakeDrive

FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"


def timeouts(caplog):
    """The formatted timeout lines logged so far: their fields and level."""
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    return [
        {**line["extra"], "level": line["level"]}
        for line in lines
        if "timeout" in line.get("extra", {})
    ]


def make_set(root, name="20260927T020000Z", content=b"sealed"):
    """Write one complete local sealed set and return its directory."""
    directory = root / name
    directory.mkdir()
    for file_name in SEALED_FILES:
        (directory / file_name).write_bytes(content + file_name.encode())
    return directory


@pytest.mark.parametrize(
    "link",
    [
        FOLDER,
        f"https://drive.google.com/drive/folders/{FOLDER}",
        f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing",
        f"https://drive.google.com/drive/u/1/folders/{FOLDER}",
        f"https://drive.google.com/open?id={FOLDER}",
        f"  https://drive.google.com/drive/folders/{FOLDER}  ",
    ],
)
def test_folder_links_parse(link):
    assert folder_id_from_url(link) == FOLDER


@pytest.mark.parametrize(
    "link",
    [
        None,
        "",
        "short",
        f"http://drive.google.com/drive/folders/{FOLDER}",
        f"https://drive.evil.example/drive/folders/{FOLDER}",
        f"https://docs.google.com/document/d/{FOLDER}/edit",
        "https://drive.google.com/drive/folders/",
        "https://drive.google.com/drive/folders/bad'quote12345",
    ],
)
def test_other_links_are_refused(link):
    with pytest.raises(ValueError):
        folder_id_from_url(link)


def test_failure_text_is_only_the_category():
    failure = DriveFailure("permission")
    assert str(failure) == "permission"
    assert "Content manager" in failure.message
    assert DriveFailure("mystery").message == DriveFailure.MESSAGES["unexpected"]
    assert DriveFailure("unavailable").retryable
    assert not DriveFailure("authorization").retryable


def test_upload_set_copies_and_is_idempotent(tmp_path):
    drive = FakeDrive(FOLDER)
    directory = make_set(tmp_path)
    first = upload_set(drive, FOLDER, directory, SEALED_FILES)
    uploads = drive.calls.count("upload")
    assert upload_set(drive, FOLDER, directory, SEALED_FILES) == first
    assert drive.calls.count("upload") == uploads == len(SEALED_FILES)
    assert drive.sets() == [directory.name]
    stored = {item["name"]: item for item in drive.children(first)}
    expected = hashlib.md5(
        (directory / SEALED_FILES[0]).read_bytes(), usedforsecurity=False
    ).hexdigest()
    assert stored[SEALED_FILES[0]]["md5Checksum"] == expected


def test_partial_earlier_copy_is_replaced(tmp_path):
    drive = FakeDrive(FOLDER)
    directory = make_set(tmp_path)
    drive.fail["upload"] = ["unavailable"]
    # First attempt: the folder is created, then the first upload fails.
    with pytest.raises(DriveFailure):
        upload_set(drive, FOLDER, directory, SEALED_FILES)
    partial = drive.children(FOLDER, tagged=True, folders=True)[0]["id"]
    replacement = upload_set(drive, FOLDER, directory, SEALED_FILES)
    assert replacement != partial
    assert drive.items[partial]["trashed"]
    assert drive.sets() == [directory.name]


def test_mismatched_upload_fails_verification(tmp_path):
    drive = FakeDrive(FOLDER)
    drive.corrupt = True
    with pytest.raises(DriveFailure) as caught:
        upload_set(drive, FOLDER, make_set(tmp_path), SEALED_FILES)
    assert caught.value.kind == "verification"


def test_prune_keeps_newest_tagged_sets_only(tmp_path):
    drive = FakeDrive(FOLDER)
    for day in range(1, 6):
        drive.create_folder(f"202609{day:02d}T020000Z", FOLDER)
    drive._new(name="20260101T020000Z", mime=backup_drive.FOLDER_MIME, parent=FOLDER)
    drive.create_folder("notes", FOLDER)
    prune(drive, FOLDER, keep=2)
    assert drive.sets() == ["20260904T020000Z", "20260905T020000Z", "notes"]
    untagged = [
        item for item in drive.items.values() if item.get("name") == "20260101T020000Z"
    ]
    assert not untagged[0]["trashed"]


def test_prune_spares_another_deployments_sets():
    """Two deployments sharing one folder each prune only their own sets."""
    drive = FakeDrive(FOLDER, tag="deployment-a")
    for day in range(1, 4):
        drive.create_folder(f"202609{day:02d}T020000Z", FOLDER)
    drive.tag = "deployment-b"
    for day in range(1, 4):
        drive.create_folder(f"202609{day:02d}T120000Z", FOLDER)
    prune(drive, FOLDER, keep=1)
    assert drive.sets("deployment-b") == ["20260903T120000Z"]
    assert drive.sets("deployment-a") == [
        "20260901T020000Z",
        "20260902T020000Z",
        "20260903T020000Z",
    ]


def test_client_queries_and_writes_the_deployment_tag():
    """The tag value in list queries and new folders is the deployment's."""
    tag = backup_drive.deployment_tag("0f6d1c2e-1111-4222-8333-444455556666")
    session = FakeSession(
        FakeResponse(200, {"files": []}), FakeResponse(200, {"id": "n"})
    )
    client = DriveClient(session, tag=tag)
    client.children(FOLDER, tagged=True)
    client.create_folder("20260901T020000Z", FOLDER)
    query = session.requests[0][2]["params"]["q"]
    assert f"value='{tag}'" in query
    assert session.requests[1][2]["json"]["appProperties"] == {
        backup_drive.TAG_KEY: tag
    }
    for bad in ("", "v1' or 1=1", "x" * 101, None):
        with pytest.raises(ValueError):
            DriveClient(session, tag=bad)


def test_probe_writes_and_trashes(tmp_path):
    drive = FakeDrive(FOLDER)
    probe(drive, FOLDER)
    files = [item for item in drive.items.values() if item.get("parent") == FOLDER]
    assert len(files) == 1 and files[0]["trashed"]


@pytest.mark.parametrize(
    ("drive", "kind"),
    [
        (FakeDrive("elsewhere00000"), "not_found"),
        (FakeDrive(FOLDER, can_add=False), "permission"),
    ],
)
def test_probe_reports_categories(drive, kind):
    with pytest.raises(DriveFailure) as caught:
        probe(drive, FOLDER)
    assert caught.value.kind == kind


def test_retries_transient_failures_only():
    pauses = []
    outcomes = [DriveFailure("unavailable"), DriveFailure("unavailable"), "done"]

    def action():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert with_retries(action, sleep=pauses.append) == "done"
    assert pauses == [10, 60]

    def refused():
        raise DriveFailure("permission")

    with pytest.raises(DriveFailure):
        with_retries(refused, sleep=pauses.append)
    assert pauses == [10, 60]


def test_no_retry_starts_past_the_deadline():
    """A copy that has run out of time fails now instead of retrying later."""
    pauses = []

    def unavailable():
        raise DriveFailure("unavailable")

    with pytest.raises(DriveFailure):
        with_retries(unavailable, sleep=pauses.append, deadline=5, clock=lambda: 0)
    assert pauses == []


def test_a_retry_refused_by_the_budget_is_logged(caplog):
    """The refused retry names the budget and how much of it had passed."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")

    def unavailable():
        raise DriveFailure("unavailable")

    with pytest.raises(DriveFailure):
        with_retries(
            unavailable,
            sleep=lambda seconds: None,
            deadline=100,
            clock=lambda: 95,
            budget_seconds=100,
        )
    [line] = timeouts(caplog)
    assert line["timeout"] == "drive_retry_budget"
    assert (line["limit_seconds"], line["elapsed_seconds"]) == (100, 95)
    assert line["level"] == "WARNING"


class FakeResponse:
    """A minimal ``requests`` response."""

    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self.body = body or {}
        self.headers = headers or {}

    def json(self):
        return self.body


class FakeSession:
    """Replays scripted responses and records each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def error(status, reason):
    """A Drive JSON error body with one reason."""
    return FakeResponse(status, {"error": {"errors": [{"reason": reason}]}})


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (error(401, "authError"), "authorization"),
        (error(403, "accessNotConfigured"), "api_disabled"),
        (error(403, "userRateLimitExceeded"), "unavailable"),
        (error(403, "insufficientFilePermissions"), "permission"),
        (error(404, "notFound"), "not_found"),
        (FakeResponse(500), "unavailable"),
    ],
)
def test_client_classifies_errors(response, kind):
    with pytest.raises(DriveFailure) as caught:
        DriveClient(FakeSession(response)).folder(FOLDER)
    assert caught.value.kind == kind


def test_a_request_timeout_is_logged_and_counts_as_unavailable(caplog):
    """A request stopped by its own timeout says so in the process log."""
    from requests import ReadTimeout

    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    with pytest.raises(DriveFailure) as caught:
        DriveClient(FakeSession(ReadTimeout("slow"))).folder(FOLDER)
    assert caught.value.kind == "unavailable"
    [line] = timeouts(caplog)
    assert line["timeout"] == "drive_request"
    assert line["limit_seconds"] == backup_drive.REQUEST_SECONDS
    assert line["elapsed_seconds"] >= 0
    assert "slow" not in json.dumps(line)


def test_timeout_and_drive_fields_are_closed_values():
    """Only reviewed words and whole seconds reach the process log."""
    from parishkit.stewardship.jobs.backup_models import FAILURE_KINDS

    assert set(FAILURE_KINDS) == observability.DRIVE_FAILURES
    for bad in (
        {"timeout": "free text"},
        {"drive_failure": "Google said no"},
        {"limit_seconds": -1},
        {"elapsed_seconds": 1.5},
        {"elapsed_seconds": True},
    ):
        with pytest.raises(ValueError):
            observability.emit(observability.Event.TASK_FAILED, **bad)


def test_client_maps_refused_delegation():
    from google.auth.exceptions import RefreshError

    with pytest.raises(DriveFailure) as caught:
        DriveClient(FakeSession(RefreshError("unauthorized_client"))).folder(FOLDER)
    assert caught.value.kind == "authorization"


def test_client_checks_folder_shape():
    folder = {"mimeType": backup_drive.FOLDER_MIME, "capabilities": {}}
    with pytest.raises(DriveFailure) as caught:
        DriveClient(FakeSession(FakeResponse(200, folder))).folder(FOLDER)
    assert caught.value.kind == "permission"
    with pytest.raises(DriveFailure) as caught:
        DriveClient(FakeSession(FakeResponse(200, {"mimeType": "text/plain"}))).folder(
            FOLDER
        )
    assert caught.value.kind == "not_folder"


def test_client_lists_all_pages_on_shared_drives():
    session = FakeSession(
        FakeResponse(200, {"files": [{"id": "a"}], "nextPageToken": "next"}),
        FakeResponse(200, {"files": [{"id": "b"}]}),
    )
    assert [item["id"] for item in DriveClient(session).children(FOLDER)] == ["a", "b"]
    params = session.requests[1][2]["params"]
    assert params["supportsAllDrives"] == "true"
    assert params["pageToken"] == "next"


def test_client_upload_uses_a_resumable_session(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"ciphertext")
    location = f"{backup_drive.UPLOAD}?uploadType=resumable&upload_id=x"
    session = FakeSession(
        FakeResponse(200, headers={"Location": location}),
        FakeResponse(200, {"id": "f", "size": "10", "md5Checksum": "m"}),
    )
    assert DriveClient(session).upload(path, "file", FOLDER)["id"] == "f"
    assert session.requests[1][:2] == ("PUT", location)


def test_client_upload_verifies_against_a_shared_drive_reply(tmp_path):
    """Drive's real replies: fields go on the session start, then are read back.

    The final PUT of a resumable upload returns only the fields chosen when
    the session started; a shared drive with no ``fields`` there replies
    with just id, name and type. The client asks for size and MD5 up front,
    and reads them back if a reply still lacks them, so the copy verifies.
    """
    path = tmp_path / "database.pgdump.sealed"
    path.write_bytes(b"ciphertext")
    digest = hashlib.md5(b"ciphertext", usedforsecurity=False).hexdigest()
    location = f"{backup_drive.UPLOAD}?uploadType=resumable&upload_id=x"
    default_reply = {
        "kind": "drive#file",
        "id": "1FileIdOnSharedDrive0",
        "name": path.name,
        "mimeType": "application/octet-stream",
    }
    session = FakeSession(
        FakeResponse(200, headers={"Location": location}),
        FakeResponse(200, default_reply),
        FakeResponse(
            200, {"id": "1FileIdOnSharedDrive0", "size": "10", "md5Checksum": digest}
        ),
    )
    result = DriveClient(session).upload(path, path.name, FOLDER)
    assert (result["size"], result["md5Checksum"]) == ("10", digest)
    start, put, read_back = session.requests
    assert start[2]["params"]["fields"] == "id,size,md5Checksum"
    assert start[2]["params"]["supportsAllDrives"] == "true"
    assert put[:2] == ("PUT", location)
    assert read_back[:2] == ("GET", f"{backup_drive.API}/1FileIdOnSharedDrive0")
    assert read_back[2]["params"]["supportsAllDrives"] == "true"


def test_client_upload_reads_nothing_back_when_the_reply_is_complete(tmp_path):
    """A reply that already has size and MD5 needs no extra request."""
    path = tmp_path / "file"
    path.write_bytes(b"ciphertext")
    location = f"{backup_drive.UPLOAD}?uploadType=resumable&upload_id=x"
    session = FakeSession(
        FakeResponse(200, headers={"Location": location}),
        FakeResponse(200, {"id": "f", "size": "10", "md5Checksum": "m"}),
    )
    DriveClient(session).upload(path, "file", FOLDER)
    assert [request[0] for request in session.requests] == ["POST", "PUT"]


def test_client_refuses_an_unexpected_upload_location(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"ciphertext")
    session = FakeSession(
        FakeResponse(200, headers={"Location": "https://attacker.example/upload"})
    )
    with pytest.raises(DriveFailure):
        DriveClient(session).upload(path, "file", FOLDER)


def integration(kind, settings):
    """One canonical integration record."""
    return {"values": {"kind": kind, "settings": settings}}


def test_destination_needs_backup_and_workspace():
    workspace = integration("google_workspace", {"delegated_email": "mail@example.org"})
    backup = integration(
        "backup", {"target": f"https://drive.google.com/drive/folders/{FOLDER}"}
    )
    assert destination_from({"sections": {"integrations": [workspace]}}) is None
    assert destination_from({"sections": {"integrations": [backup]}}) is None
    assert destination_from({"sections": {"integrations": [workspace, backup]}}) == (
        FOLDER,
        "mail@example.org",
    )
    broken = integration("backup", {"target": "https://example.org/x"})
    with pytest.raises(ConfigError):
        destination_from({"sections": {"integrations": [workspace, broken]}})
