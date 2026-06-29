from __future__ import annotations

import logging
import stat

import pytest

from parishkit.config import ConfigError
from parishkit.google.auth import (
    GoogleAPIError,
    build_service,
    execute_google_request,
    load_service_account_credentials,
    load_user_credentials,
    run_user_oauth_flow,
)
from parishkit.google.calendar import list_events, patch_attendee_response
from parishkit.google.drive import get_file_metadata
from parishkit.google.groups import (
    delete_group_member,
    get_group_posting_permissions,
    insert_group_member,
    list_group_members,
    update_group_member_role,
)
from parishkit.google.sheets import (
    batch_update_spreadsheet,
    clear_values,
    get_spreadsheet,
    update_values,
)
from parishkit.retry import RetryError, RetryPolicy, TransientRetryError


def test_build_service_uses_injected_builder():
    """build_service delegates to the injected builder with discovery disabled."""
    calls = []

    def fake_build(**kwargs):
        """Record builder kwargs and return a sentinel service object."""
        calls.append(kwargs)
        return "service"

    service = build_service(
        "drive",
        "v3",
        credentials="creds",
        build_fn=fake_build,
    )

    assert service == "service"
    assert calls == [
        {
            "serviceName": "drive",
            "version": "v3",
            "credentials": "creds",
            "cache_discovery": False,
        }
    ]


def test_service_account_credential_load_errors_are_config_errors(monkeypatch):
    """Malformed Google service-account files are reported as config errors."""

    class ServiceAccountCredentials:
        """Fake service-account credential loader that rejects the file."""

        @staticmethod
        def from_service_account_file(*_args, **_kwargs):
            """Raise like google-auth would for an invalid credential file."""
            raise ValueError("invalid service account")

    class ServiceAccountModule:
        """Fake google.oauth2.service_account module."""

        Credentials = ServiceAccountCredentials

    monkeypatch.setattr(
        "parishkit.google.auth._import_google_auth",
        lambda: (ServiceAccountModule, object()),
    )

    with pytest.raises(ConfigError, match="service-account credential file.*invalid"):
        load_service_account_credentials("bad.json", scopes=["scope"])


def test_user_credential_load_errors_are_config_errors(monkeypatch):
    """Malformed Google user token files are reported as config errors."""

    class UserCredentials:
        """Fake authorized-user credential loader that rejects the file."""

        @staticmethod
        def from_authorized_user_file(*_args, **_kwargs):
            """Raise like google-auth would for an invalid token file."""
            raise ValueError("invalid user token")

    class UserCredentialsModule:
        """Fake google.oauth2.credentials module."""

        Credentials = UserCredentials

    monkeypatch.setattr(
        "parishkit.google.auth._import_google_auth",
        lambda: (object(), UserCredentialsModule),
    )

    with pytest.raises(ConfigError, match="user credential file.*invalid"):
        load_user_credentials("bad-token.json", scopes=["scope"])


def test_execute_google_request_retries_transient_errors():
    """A TransientRetryError is retried and the eventual result is returned."""
    attempts = {"count": 0}

    class FakeRequest:
        """Request that fails transiently once, then succeeds."""

        def execute(self):
            """Raise a transient error on the first call, succeed afterward."""
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TransientRetryError("try again")
            return {"ok": True}

    result = execute_google_request(
        FakeRequest(),
        policy=RetryPolicy(attempts=2, initial_delay=0),
        sleep=lambda _seconds: None,
    )

    assert result == {"ok": True}


def test_execute_google_request_retries_transient_http_error(monkeypatch):
    """A retryable HTTP status (503) is retried and the result returned."""

    class FakeHttpError(Exception):
        """Stand-in for googleapiclient HttpError carrying a status code."""

        def __init__(self, status):
            """Build an error exposing ``resp.status`` like the real HttpError."""
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    attempts = {"count": 0}

    class FakeRequest:
        """Request that raises a transient 503 once, then succeeds."""

        def execute(self):
            """Raise a 503 on the first call, succeed afterward."""
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise FakeHttpError(503)
            return {"ok": True}

    # Point the helper at the fake error class so it recognizes our exception.
    monkeypatch.setattr(
        "parishkit.google.auth._import_google_http_error", lambda: FakeHttpError
    )

    assert execute_google_request(
        FakeRequest(),
        policy=RetryPolicy(attempts=2, initial_delay=0),
        sleep=lambda _seconds: None,
    ) == {"ok": True}


def test_execute_google_request_maps_permanent_http_error(monkeypatch):
    """A permanent HTTP status (403) maps to GoogleAPIError without retrying."""

    class FakeHttpError(Exception):
        """Stand-in for googleapiclient HttpError carrying a status code."""

        def __init__(self, status):
            """Build an error exposing ``resp.status`` like the real HttpError."""
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    class FakeRequest:
        """Request that always fails with a permanent 403."""

        def execute(self):
            """Always raise a non-retryable 403 error."""
            raise FakeHttpError(403)

    # Point the helper at the fake error class so it recognizes our exception.
    monkeypatch.setattr(
        "parishkit.google.auth._import_google_http_error", lambda: FakeHttpError
    )

    with pytest.raises(GoogleAPIError, match="403"):
        execute_google_request(FakeRequest(), policy=RetryPolicy(attempts=1))


def test_execute_google_request_logs_warning_on_http_failure(monkeypatch, caplog):
    """Google request failures emit a warning before raising."""

    class FakeHttpError(Exception):
        """Stand-in for googleapiclient HttpError carrying a status code."""

        def __init__(self, status):
            """Build an error exposing ``resp.status`` like the real HttpError."""
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    class FakeRequest:
        """Request that always fails with a permanent Google HTTP error."""

        uri = "https://google.example/request"

        def execute(self):
            """Raise a permanent HTTP error."""
            raise FakeHttpError(403)

    monkeypatch.setattr(
        "parishkit.google.auth._import_google_http_error", lambda: FakeHttpError
    )
    caplog.set_level(logging.WARNING, logger="parishkit.google.auth")

    with pytest.raises(GoogleAPIError):
        execute_google_request(FakeRequest(), policy=RetryPolicy(attempts=1))

    assert "Google API request failed for https://google.example/request" in caplog.text
    assert "HTTP 403" in caplog.text


def test_execute_google_request_exhausts_transient_http_error(monkeypatch):
    """A retryable 429 still raises GoogleAPIError once attempts are exhausted."""

    class FakeHttpError(Exception):
        """Stand-in for googleapiclient HttpError carrying a status code."""

        def __init__(self, status):
            """Build an error exposing ``resp.status`` like the real HttpError."""
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    class FakeRequest:
        """Request that always fails with a transient 429."""

        def execute(self):
            """Always raise a retryable 429 error."""
            raise FakeHttpError(429)

    # Point the helper at the fake error class so it recognizes our exception.
    monkeypatch.setattr(
        "parishkit.google.auth._import_google_http_error", lambda: FakeHttpError
    )

    with pytest.raises(GoogleAPIError, match="429"):
        execute_google_request(
            FakeRequest(),
            policy=RetryPolicy(attempts=1),
            sleep=lambda _seconds: None,
        )


def test_list_group_members_pages():
    """list_group_members follows nextPageToken and concatenates member pages.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """

    class Request:
        """Fake Directory API request returning a fixed response."""

        def __init__(self, response):
            self.response = response

        def execute(self):
            """Return the canned response body."""
            return self.response

    class Members:
        """Fake members resource that serves two pages of results."""

        def __init__(self):
            self.calls = 0

        def list(self, **kwargs):
            """Return page one with a next token, then the final page."""
            self.calls += 1
            assert kwargs["groupKey"] == "group@example.org"
            if self.calls == 1:
                return Request({"members": [{"email": "a"}], "nextPageToken": "next"})
            return Request({"members": [{"email": "b"}]})

    class Service:
        """Fake Directory API service exposing the members resource."""

        def __init__(self):
            self._members = Members()

        def members(self):
            """Return the fake members resource."""
            return self._members

    assert list_group_members(Service(), "group@example.org") == [
        {"email": "a"},
        {"email": "b"},
    ]


def test_group_write_helpers_use_directory_api():
    """Group write/read helpers issue the expected Directory API calls.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """

    class Request:
        """Fake Directory API request returning a fixed (default empty) response."""

        def __init__(self, response=None):
            self.response = response or {}

        def execute(self):
            """Return the canned response body."""
            return self.response

    class Members:
        """Fake members resource recording each insert/update/delete call."""

        def __init__(self):
            self.calls = []

        def insert(self, **kwargs):
            """Record an insert call and return an empty request."""
            self.calls.append(("insert", kwargs))
            return Request()

        def update(self, **kwargs):
            """Record an update call and return an empty request."""
            self.calls.append(("update", kwargs))
            return Request()

        def delete(self, **kwargs):
            """Record a delete call and return an empty request."""
            self.calls.append(("delete", kwargs))
            return Request()

    class Groups:
        """Fake groups resource recording get calls and returning permissions."""

        def __init__(self):
            self.calls = []

        def get(self, **kwargs):
            """Record a get call and return canned posting permissions."""
            self.calls.append(("get", kwargs))
            return Request({"whoCanPostMessage": "ALL_MEMBERS_CAN_POST"})

    class Service:
        """Fake Directory API service exposing members and groups resources."""

        def __init__(self):
            """Create the fake members and groups resources."""
            self._members = Members()
            self._groups = Groups()

        def members(self):
            """Return the fake members resource."""
            return self._members

        def groups(self):
            """Return the fake groups resource."""
            return self._groups

    service = Service()

    insert_group_member(service, "group@example.org", "a@example.org", "MEMBER")
    update_group_member_role(service, "group@example.org", "a@example.org", "OWNER")
    delete_group_member(service, "group@example.org", "member-id")
    permission = get_group_posting_permissions(service, "group@example.org")

    assert service._members.calls == [
        (
            "insert",
            {
                "groupKey": "group@example.org",
                "body": {"email": "a@example.org", "role": "MEMBER"},
            },
        ),
        (
            "update",
            {
                "groupKey": "group@example.org",
                "memberKey": "a@example.org",
                "body": {"role": "OWNER"},
            },
        ),
        ("delete", {"groupKey": "group@example.org", "memberKey": "member-id"}),
    ]
    assert service._groups.calls == [
        (
            "get",
            {
                "groupUniqueId": "group@example.org",
                "fields": "whoCanPostMessage",
            },
        )
    ]
    assert permission == "ALL_MEMBERS_CAN_POST"


def test_group_write_helpers_do_not_retry_transient_write_failures():
    """Non-idempotent group writes are attempted only once."""
    attempts = {"count": 0}

    class Request:
        """Fake write request that always fails transiently."""

        def execute(self):
            """Count the attempt and raise a retryable error."""
            attempts["count"] += 1
            raise TransientRetryError("applied but response failed")

    class Members:
        """Fake members resource returning the failing request."""

        def insert(self, **kwargs):
            """Return a request for the insert operation."""
            return Request()

    class Service:
        """Fake Directory API service exposing members."""

        def members(self):
            """Return the fake members resource."""
            return Members()

    with pytest.raises(RetryError):
        insert_group_member(Service(), "group@example.org", "a@example.org", "MEMBER")

    assert attempts["count"] == 1


def test_list_calendar_events_pages():
    """list_events pages through the Calendar API, threading the pageToken."""

    class Request:
        """Fake Calendar API request returning a fixed response."""

        def __init__(self, response):
            self.response = response

        def execute(self):
            """Return the canned response body."""
            return self.response

    class Events:
        """Fake events resource serving two pages and asserting the page token."""

        def __init__(self):
            self.calls = 0

        def list(self, **kwargs):
            """Return page one (no token in) then page two (token threaded in)."""
            self.calls += 1
            assert kwargs["calendarId"] == "calendar"
            if self.calls == 1:
                # The first page request must not carry a page token.
                assert kwargs["pageToken"] is None
                return Request({"items": [{"id": "one"}], "nextPageToken": "next"})
            # The second page must pass back the token from the first response.
            assert kwargs["pageToken"] == "next"
            return Request({"items": [{"id": "two"}]})

    class Service:
        """Fake Calendar API service exposing the events resource."""

        def __init__(self):
            self._events = Events()

        def events(self):
            """Return the fake events resource."""
            return self._events

    assert list_events(Service(), "calendar") == [{"id": "one"}, {"id": "two"}]


def test_patch_attendee_response_uses_calendar_patch():
    """patch_attendee_response patches the event with the attendee's status.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """

    class Request:
        """Fake Calendar API request returning an empty response."""

        def execute(self):
            """Return an empty response body."""
            return {}

    class Events:
        """Fake events resource recording each patch call."""

        def __init__(self):
            self.patch_calls = []

        def patch(self, **kwargs):
            """Record a patch call and return an empty request."""
            self.patch_calls.append(kwargs)
            return Request()

    class Service:
        """Fake Calendar API service exposing the events resource."""

        def __init__(self):
            self._events = Events()

        def events(self):
            """Return the fake events resource."""
            return self._events

    service = Service()

    patch_attendee_response(service, "room@example.org", "event-1", "accepted")

    assert service._events.patch_calls == [
        {
            "calendarId": "room@example.org",
            "sendUpdates": "all",
            "eventId": "event-1",
            "body": {
                "attendeesOmitted": True,
                "attendees": [
                    {
                        "email": "room@example.org",
                        "responseStatus": "accepted",
                    }
                ],
            },
        }
    ]


def test_patch_attendee_response_does_not_retry_notification_write():
    """Calendar attendee patches are one-shot because they send notifications."""
    attempts = {"count": 0}

    class Request:
        """Fake Calendar request that always fails transiently."""

        def execute(self):
            """Raise a transient error and record the attempt count."""
            attempts["count"] += 1
            raise TransientRetryError("temporary")

    class Events:
        """Fake events resource returning the failing patch request."""

        def patch(self, **_kwargs):
            """Return a request that fails if executed."""
            return Request()

    class Service:
        """Fake Calendar API service exposing the events resource."""

        def events(self):
            """Return the fake events resource."""
            return Events()

    with pytest.raises(RetryError):
        patch_attendee_response(Service(), "room@example.org", "event-1", "accepted")

    assert attempts["count"] == 1


def test_google_optional_import_error_is_config_error(monkeypatch):
    """A missing optional Google dependency surfaces as a ConfigError."""

    def fail():
        """Simulate the optional google client library being absent."""
        raise ConfigError("install parishkit[google]")

    monkeypatch.setattr("parishkit.google.auth._import_google_build", fail)

    with pytest.raises(ConfigError, match="parishkit"):
        build_service("drive", "v3", credentials="creds")


def test_run_user_oauth_flow_saves_token(tmp_path):
    """The user OAuth flow writes the credentials JSON with 0o600 permissions."""
    token_file = tmp_path / "user-token.json"

    class Credentials:
        """Fake credentials object serializable to JSON."""

        def to_json(self):
            """Return the serialized credentials body."""
            return '{"token": "value"}'

    class Flow:
        """Fake installed-app flow returning credentials from a local server."""

        def run_local_server(self, *, port):
            """Assert an ephemeral port is requested and return credentials."""
            assert port == 0
            return Credentials()

    def flow_factory(path, *, scopes):
        """Verify the client-secrets path and scopes, then return the fake flow."""
        assert path == str(tmp_path / "client.json")
        assert scopes == ["scope"]
        return Flow()

    credentials = run_user_oauth_flow(
        tmp_path / "client.json",
        token_file,
        scopes=["scope"],
        flow_factory=flow_factory,
    )

    assert isinstance(credentials, Credentials)
    assert token_file.read_text(encoding="utf-8") == '{"token": "value"}'
    # Saved credentials are secrets, so the file must be owner-read/write only.
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_drive_metadata_helper_supports_shared_drives():
    class Request:
        def execute(self):
            return {"id": "file-id", "name": "Roster"}

    class Files:
        def __init__(self):
            self.calls = []

        def get(self, **kwargs):
            self.calls.append(kwargs)
            return Request()

    class Service:
        def __init__(self):
            self._files = Files()

        def files(self):
            return self._files

    service = Service()

    assert get_file_metadata(service, "file-id") == {"id": "file-id", "name": "Roster"}
    assert service._files.calls == [
        {
            "fileId": "file-id",
            "fields": "id,name,mimeType,modifiedTime",
            "supportsAllDrives": True,
        }
    ]


def test_sheet_helpers_use_expected_spreadsheets_calls():
    """Sheet helpers issue value, metadata, and batchUpdate calls correctly.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """

    class Request:
        """Fake Sheets API request returning a canned response."""

        def __init__(self, response=None):
            """Store the response returned by execute."""
            self.response = {} if response is None else response

        def execute(self):
            """Return the canned response body."""
            return self.response

    class Values:
        """Fake values resource recording each clear/update call."""

        def __init__(self):
            self.calls = []

        def clear(self, **kwargs):
            """Record a clear call and return an empty request."""
            self.calls.append(("clear", kwargs))
            return Request()

        def update(self, **kwargs):
            """Record an update call and return an empty request."""
            self.calls.append(("update", kwargs))
            return Request()

    class Spreadsheets:
        """Fake spreadsheets resource exposing values and spreadsheet methods."""

        def __init__(self):
            self._values = Values()
            self.calls = []

        def values(self):
            """Return the fake values resource."""
            return self._values

        def get(self, **kwargs):
            """Record a metadata request and return one sheet property."""
            self.calls.append(("get", kwargs))
            return Request({"sheets": [{"properties": {"title": "Roster"}}]})

        def batchUpdate(self, **kwargs):
            """Record a batchUpdate request."""
            self.calls.append(("batchUpdate", kwargs))
            return Request()

    class Service:
        """Fake Sheets API service exposing the spreadsheets resource."""

        def __init__(self):
            self._spreadsheets = Spreadsheets()

        def spreadsheets(self):
            """Return the fake spreadsheets resource."""
            return self._spreadsheets

    service = Service()

    clear_values(service, "sheet-id", "Roster!A:Z")
    update_values(service, "sheet-id", "Roster!A1", [["Name"]])
    assert get_spreadsheet(service, "sheet-id") == {
        "sheets": [{"properties": {"title": "Roster"}}]
    }
    batch_update_spreadsheet(service, "sheet-id", [{"request": "value"}])
    batch_update_spreadsheet(service, "sheet-id", [])

    assert service._spreadsheets._values.calls == [
        (
            "clear",
            {
                "spreadsheetId": "sheet-id",
                "range": "Roster!A:Z",
                "body": {},
            },
        ),
        (
            "update",
            {
                "spreadsheetId": "sheet-id",
                "range": "Roster!A1",
                "valueInputOption": "RAW",
                "body": {"values": [["Name"]]},
            },
        ),
    ]
    assert service._spreadsheets.calls == [
        (
            "get",
            {
                "spreadsheetId": "sheet-id",
                "fields": "sheets.properties",
            },
        ),
        (
            "batchUpdate",
            {
                "spreadsheetId": "sheet-id",
                "body": {"requests": [{"request": "value"}]},
            },
        ),
    ]
