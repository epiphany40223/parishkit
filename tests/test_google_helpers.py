from __future__ import annotations

import stat

import pytest

from parishkit.config import ConfigError
from parishkit.google.auth import (
    GoogleAPIError,
    build_service,
    execute_google_request,
    run_user_oauth_flow,
)
from parishkit.google.calendar import list_events
from parishkit.google.drive import get_file_metadata
from parishkit.google.groups import list_group_members
from parishkit.retry import RetryPolicy, TransientRetryError


def test_build_service_uses_injected_builder():
    calls = []

    def fake_build(**kwargs):
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


def test_execute_google_request_retries_transient_errors():
    attempts = {"count": 0}

    class FakeRequest:
        def execute(self):
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
    class FakeHttpError(Exception):
        def __init__(self, status):
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    attempts = {"count": 0}

    class FakeRequest:
        def execute(self):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise FakeHttpError(503)
            return {"ok": True}

    monkeypatch.setattr(
        "parishkit.google.auth._import_google_http_error", lambda: FakeHttpError
    )

    assert execute_google_request(
        FakeRequest(),
        policy=RetryPolicy(attempts=2, initial_delay=0),
        sleep=lambda _seconds: None,
    ) == {"ok": True}


def test_execute_google_request_maps_permanent_http_error(monkeypatch):
    class FakeHttpError(Exception):
        def __init__(self, status):
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    class FakeRequest:
        def execute(self):
            raise FakeHttpError(403)

    monkeypatch.setattr(
        "parishkit.google.auth._import_google_http_error", lambda: FakeHttpError
    )

    with pytest.raises(GoogleAPIError, match="403"):
        execute_google_request(FakeRequest(), policy=RetryPolicy(attempts=1))


def test_execute_google_request_exhausts_transient_http_error(monkeypatch):
    class FakeHttpError(Exception):
        def __init__(self, status):
            self.resp = type("Response", (), {"status": status})()
            super().__init__(f"HTTP {status}")

    class FakeRequest:
        def execute(self):
            raise FakeHttpError(429)

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
    class Request:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class Members:
        def __init__(self):
            self.calls = 0

        def list(self, **kwargs):
            self.calls += 1
            assert kwargs["groupKey"] == "group@example.org"
            if self.calls == 1:
                return Request({"members": [{"email": "a"}], "nextPageToken": "next"})
            return Request({"members": [{"email": "b"}]})

    class Service:
        def __init__(self):
            self._members = Members()

        def members(self):
            return self._members

    assert list_group_members(Service(), "group@example.org") == [
        {"email": "a"},
        {"email": "b"},
    ]


def test_list_calendar_events_pages():
    class Request:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class Events:
        def __init__(self):
            self.calls = 0

        def list(self, **kwargs):
            self.calls += 1
            assert kwargs["calendarId"] == "calendar"
            if self.calls == 1:
                assert kwargs["pageToken"] is None
                return Request({"items": [{"id": "one"}], "nextPageToken": "next"})
            assert kwargs["pageToken"] == "next"
            return Request({"items": [{"id": "two"}]})

    class Service:
        def __init__(self):
            self._events = Events()

        def events(self):
            return self._events

    assert list_events(Service(), "calendar") == [{"id": "one"}, {"id": "two"}]


def test_google_optional_import_error_is_config_error(monkeypatch):
    def fail():
        raise ConfigError("install parishkit[google]")

    monkeypatch.setattr("parishkit.google.auth._import_google_build", fail)

    with pytest.raises(ConfigError, match="parishkit"):
        build_service("drive", "v3", credentials="creds")


def test_run_user_oauth_flow_saves_token(tmp_path):
    token_file = tmp_path / "user-token.json"

    class Credentials:
        def to_json(self):
            return '{"token": "value"}'

    class Flow:
        def run_local_server(self, *, port):
            assert port == 0
            return Credentials()

    def flow_factory(path, *, scopes):
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
