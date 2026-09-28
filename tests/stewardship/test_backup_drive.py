"""Off-site backup copies to Google Drive, against an in-memory Drive."""

import hashlib

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import backup_drive
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
