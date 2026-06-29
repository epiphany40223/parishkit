from __future__ import annotations

import datetime as dt
import json
import logging
import stat

import pytest
import requests

from parishkit.config import ConfigError
from parishkit.constant_contact import (
    CCAPIError,
    ConstantContactClient,
    ConstantContactConfig,
    create_contact_dict,
    get_access_token,
    link_cc_data,
    link_contacts_to_ps_members,
    load_access_token,
    load_client_id,
    run_device_oauth_flow,
    save_access_token,
    set_valid_from_to,
    sign_up_form_body,
    token_is_valid,
    update_contact_body,
)
from parishkit.retry import RetryPolicy


class Response:
    """Minimal stand-in for a requests Response used by the fake Session."""

    def __init__(
        self,
        payload,
        *,
        status_code=200,
        url="https://api.example/v3/items",
        json_error: Exception | None = None,
    ):
        """Capture the JSON payload, status, and URL the client will inspect."""
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.url = url
        self.json_error = json_error

    def json(self):
        """Return the decoded payload, mimicking Response.json()."""
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class Session:
    """Fake HTTP session that replays queued responses and records calls.

    Each request method pops the next prepared Response in order, so tests can
    script a sequence of replies and later assert on the recorded ``calls``.
    """

    def __init__(self, responses):
        """Queue the responses to be returned in order by later requests."""
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        """Record the GET and return the next queued response."""
        self.calls.append(("get", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        """Record the POST and return the next queued response."""
        self.calls.append(("post", url, kwargs))
        return self.responses.pop(0)

    def put(self, url, **kwargs):
        """Record the PUT and return the next queued response."""
        self.calls.append(("put", url, kwargs))
        return self.responses.pop(0)


def config():
    """Build a minimal valid ConstantContactConfig for client tests."""
    return ConstantContactConfig(
        client_id={"endpoints": {"api": "https://api.example"}},
        access_token={"access_token": "token"},
    )


def test_access_token_serialization(tmp_path):
    """A saved token round-trips, stays 0o600, and validates within its window."""
    token = {"access_token": "token"}
    set_valid_from_to(
        dt.datetime(2026, 1, 1, tzinfo=dt.UTC), {"expires_in": 60} | token
    )
    token = {
        "access_token": "token",
        "valid from": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "valid to": dt.datetime(2026, 1, 1, 0, 1, tzinfo=dt.UTC),
    }
    path = tmp_path / "token.json"

    save_access_token(path, token)
    loaded = load_access_token(path)

    assert loaded == token
    # Tokens are secrets, so the file must be owner-read/write only.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token_is_valid(token, now=dt.datetime(2026, 1, 1, 0, 0, 30, tzinfo=dt.UTC))


def test_api_pagination():
    """get_all follows ``_links.next`` and concatenates items across pages."""
    session = Session(
        [
            # First page advertises a next link; second page has none, ending paging.
            Response(
                {"items": [{"id": 1}], "_links": {"next": {"href": "/v3/items?page=2"}}}
            ),
            Response({"items": [{"id": 2}]}),
        ]
    )
    client = ConstantContactClient(config(), session=session)

    assert client.get_all("items", "items") == [{"id": 1}, {"id": 2}]
    assert len(session.calls) == 2
    # The client applies its default request timeout.
    assert session.calls[0][2]["timeout"] == 30.0


def test_api_error_raises_typed_exception():
    """A non-retryable 4xx response surfaces as a CCAPIError."""
    client = ConstantContactClient(
        config(), session=Session([Response({}, status_code=400)])
    )

    with pytest.raises(CCAPIError):
        client.post("items", {})


def test_api_error_logs_warning(caplog):
    """A failed Constant Contact REST call emits a warning."""
    client = ConstantContactClient(
        config(), session=Session([Response({}, status_code=400)])
    )
    caplog.set_level(logging.WARNING, logger="parishkit.constant_contact")

    with pytest.raises(CCAPIError):
        client.post("items", {})

    assert "Constant Contact API request failed" in caplog.text
    assert "HTTP 400" in caplog.text


def test_post_does_not_retry_transient_create_response():
    """POST creates are one-shot so a hidden success is not duplicated."""
    session = Session(
        [
            Response({}, status_code=503),
            Response({"id": "created"}),
        ]
    )
    client = ConstantContactClient(config(), session=session)

    with pytest.raises(CCAPIError, match="503"):
        client.post("items", {"email": "person@example.org"})

    assert len(session.calls) == 1


def test_exhausted_transient_response_raises_typed_exception():
    """A transient 429 raises CCAPIError once retry attempts are exhausted."""
    # Allow only a single attempt so the transient status is not retried away.
    retry_config = ConstantContactConfig(
        client_id={"endpoints": {"api": "https://api.example"}},
        access_token={"access_token": "token"},
        retry_policy=RetryPolicy(attempts=1, initial_delay=0),
    )
    client = ConstantContactClient(
        retry_config,
        session=Session([Response({}, status_code=429)]),
    )

    with pytest.raises(CCAPIError, match="429"):
        client.get_all("items", "items")


def test_malformed_api_json_raises_typed_exception():
    """A 2xx Constant Contact response with invalid JSON raises CCAPIError."""
    client = ConstantContactClient(
        config(),
        session=Session([Response("not json", json_error=ValueError("not json"))]),
    )

    with pytest.raises(CCAPIError, match="invalid JSON"):
        client.get_all("items", "items")


def test_config_validation_rejects_missing_keys():
    """Config construction rejects missing keys and wrong-typed fields."""
    with pytest.raises(ConfigError, match="endpoints.api"):
        ConstantContactConfig(client_id={}, access_token={"access_token": "token"})
    with pytest.raises(ConfigError, match="access_token"):
        ConstantContactConfig(
            client_id={"endpoints": {"api": "https://api.example"}},
            access_token={},
        )
    with pytest.raises(ConfigError, match="client_id must be a mapping"):
        ConstantContactConfig(client_id=[], access_token={"access_token": "token"})
    with pytest.raises(ConfigError, match="timeout must be a number"):
        ConstantContactConfig(
            client_id={"endpoints": {"api": "https://api.example"}},
            access_token={"access_token": "token"},
            timeout="30",
        )


def test_load_access_token_rejects_bad_shape(tmp_path):
    """Loading a token file missing required fields raises ConfigError."""
    path = tmp_path / "token.json"
    path.write_text('{"access_token": "token"}', encoding="utf-8")

    with pytest.raises(ConfigError, match="missing"):
        load_access_token(path)


def test_load_access_token_rejects_wrong_typed_timestamps(tmp_path):
    """Wrong-typed token timestamps raise ConfigError rather than TypeError."""
    path = tmp_path / "token.json"
    path.write_text(
        json.dumps(
            {
                "access_token": "token",
                "valid from": 123,
                "valid to": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="invalid timestamp"):
        load_access_token(path)


def test_load_client_id_rejects_malformed_json(tmp_path):
    """A malformed Constant Contact client ID file raises ConfigError."""
    path = tmp_path / "client-id.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid Constant Contact client ID file"):
        load_client_id(path)


def test_load_client_id_rejects_bad_shape(tmp_path):
    """A client ID file missing endpoints.api raises ConfigError."""
    path = tmp_path / "client-id.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError, match="endpoints.api"):
        load_client_id(path)


def test_refresh_access_token_rejects_non_json_response(tmp_path):
    """Refreshing against a non-JSON token endpoint reply raises ConfigError.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    # Seed an already-expired token so get_access_token attempts a refresh.
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )

    class BadJSONResponse(Response):
        """Response whose body cannot be decoded as JSON."""

        def json(self):
            """Simulate a body that is not valid JSON."""
            raise ValueError("not json")

    with pytest.raises(ConfigError, match="invalid JSON"):
        get_access_token(
            token_path,
            {
                "client id": "client",
                "endpoints": {
                    "api": "https://api.example",
                    "token": "https://auth.example/token",
                },
            },
            session=Session([BadJSONResponse("not json", status_code=400)]),
            now=start,
        )


def test_get_access_token_dry_run_rejects_expired_token_without_refresh(tmp_path):
    """Strict dry-run mode does not refresh or rewrite expired token files."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )
    before = token_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError, match="dry-run mode will not refresh"):
        get_access_token(
            token_path,
            {
                "client id": "client",
                "endpoints": {
                    "api": "https://api.example",
                    "token": "https://auth.example/token",
                },
            },
            now=start,
            allow_refresh=False,
        )

    assert token_path.read_text(encoding="utf-8") == before


def test_refresh_access_token_retries_transient_request_failure(tmp_path):
    """Refreshing retries transient request failures before succeeding."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )

    class FlakySession(Session):
        """Fake session that fails once with a timeout, then returns a response."""

        def post(self, url, **kwargs):
            """Record the POST and fail the first refresh attempt."""
            self.calls.append(("post", url, kwargs))
            if len(self.calls) == 1:
                raise requests.exceptions.Timeout("temporary timeout")
            return self.responses.pop(0)

    refreshed = get_access_token(
        token_path,
        {
            "client id": "client",
            "endpoints": {
                "api": "https://api.example",
                "token": "https://auth.example/token",
            },
        },
        session=FlakySession(
            [
                Response(
                    {
                        "access_token": "new",
                        "refresh_token": "refresh2",
                        "expires_in": 3600,
                    }
                )
            ]
        ),
        now=start,
    )

    assert refreshed["access_token"] == "new"


def test_refresh_access_token_retries_transient_http_response(tmp_path):
    """Refreshing retries token endpoint 429/5xx responses before succeeding."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )
    session = Session(
        [
            Response({"error": "temporarily unavailable"}, status_code=503),
            Response(
                {
                    "access_token": "new",
                    "refresh_token": "refresh2",
                    "expires_in": 3600,
                }
            ),
        ]
    )

    refreshed = get_access_token(
        token_path,
        {
            "client id": "client",
            "endpoints": {
                "api": "https://api.example",
                "token": "https://auth.example/token",
            },
        },
        session=session,
        now=start,
    )

    assert refreshed["access_token"] == "new"
    assert len(session.calls) == 2


def test_refresh_access_token_wraps_exhausted_request_failures(tmp_path, caplog):
    """Repeated transient request failures surface as a clean ConfigError."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )

    class BrokenSession(Session):
        """Fake session that always raises a retryable timeout."""

        def post(self, url, **kwargs):
            """Record the POST and raise a timeout."""
            self.calls.append(("post", url, kwargs))
            raise requests.exceptions.Timeout("network down")

    caplog.set_level(logging.WARNING, logger="parishkit.constant_contact")

    with pytest.raises(ConfigError, match="failed after retries"):
        get_access_token(
            token_path,
            {
                "client id": "client",
                "endpoints": {
                    "api": "https://api.example",
                    "token": "https://auth.example/token",
                },
            },
            session=BrokenSession([]),
            now=start,
        )
    assert "Constant Contact token refresh request failed" in caplog.text


def test_contact_body_helpers_strip_periods():
    """Body helpers strip periods from names while preserving email casing."""
    contact = {
        "contact_id": "id",
        "first_name": "T.J.",
        "email_address": {"address": "A@EXAMPLE.ORG"},
        "list_memberships": ["list"],
    }

    assert update_contact_body(contact)["first_name"] == "TJ"
    assert sign_up_form_body(contact)["email_address"] == "A@EXAMPLE.ORG"


def test_contact_linking_helpers():
    """Linking resolves list/custom-field names and cross-links contacts to members.

    link_cc_data attaches human-readable list and custom-field names, while
    link_contacts_to_ps_members joins a contact to its ParishSoft member by email.
    """
    members = {
        1: {
            "firstName": "Ann",
            "lastName": "Smith",
            "py emailAddresses": ["ann@example.org"],
        }
    }
    contact = create_contact_dict("ANN@EXAMPLE.ORG", [members[1]])
    contacts = [contact]
    lists = [{"list_id": "list", "name": "Newsletter"}]
    custom_fields = [{"custom_field_id": "field", "name": "Parish ID"}]
    contact["list_memberships"] = ["list"]
    contact["custom_fields"] = [{"custom_field_id": "field", "value": "1"}]

    link_cc_data(contacts, custom_fields, lists)
    link_contacts_to_ps_members(contacts, members)

    assert contact["first_name"] == "Ann"
    assert contact["LIST MEMBERSHIPS"] == ["Newsletter"]
    assert contact["CUSTOM FIELDS"]["Parish ID"]["value"] == "1"
    assert lists[0]["CONTACTS"]["ann@example.org"] is contact
    assert members[1]["CONTACT"] is contact


def test_get_access_token_refreshes_expired_token(tmp_path):
    """An expired token is refreshed via the token endpoint and persisted.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    # Stored token expired an hour ago, forcing a refresh round-trip.
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )
    session = Session(
        [
            Response(
                {
                    "access_token": "new",
                    "refresh_token": "refresh2",
                    "expires_in": 3600,
                }
            )
        ]
    )

    refreshed = get_access_token(
        token_path,
        {
            "client id": "client",
            "endpoints": {
                "api": "https://api.example",
                "token": "https://auth.example/token",
            },
        },
        session=session,
        now=start,
    )

    assert refreshed["access_token"] == "new"
    # The refreshed token is written back to disk, not just returned.
    assert load_access_token(token_path)["access_token"] == "new"
    assert session.calls[0][2]["timeout"] == 30.0
    assert (tmp_path / ".token.json.lock").exists()


def test_get_access_token_refresh_failure_requires_manual_reauth(tmp_path):
    """A failed refresh raises ConfigError signaling manual reauth is needed."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )

    with pytest.raises(ConfigError, match="token refresh failed"):
        get_access_token(
            token_path,
            {
                "client id": "client",
                "endpoints": {
                    "api": "https://api.example",
                    "token": "https://auth.example/token",
                },
            },
            session=Session([Response({"error": "invalid_grant"}, status_code=400)]),
            now=start,
        )


def test_get_access_token_rejects_malformed_refresh_success(tmp_path):
    """A 200 refresh reply missing required fields is treated as malformed."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    token_path = tmp_path / "token.json"
    save_access_token(
        token_path,
        {
            "access_token": "old",
            "refresh_token": "refresh",
            "valid from": start - dt.timedelta(hours=2),
            "valid to": start - dt.timedelta(hours=1),
        },
    )

    with pytest.raises(ConfigError, match="malformed token"):
        get_access_token(
            token_path,
            {
                "client id": "client",
                "endpoints": {
                    "api": "https://api.example",
                    "token": "https://auth.example/token",
                },
            },
            session=Session([Response({"access_token": "new"})]),
            now=start,
        )


def test_device_oauth_flow_saves_normalized_token():
    """The device flow stores a token with computed valid-from/valid-to bounds.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    session = Session(
        [
            # First the device authorization request, then the token grant.
            Response(
                {
                    "verification_uri_complete": "https://auth.example/device",
                    "device_code": "device",
                }
            ),
            Response(
                {
                    "access_token": "token",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                }
            ),
        ]
    )
    # Capture user-facing prompts/messages so the flow needs no real console.
    prompts = []
    prints = []

    token = run_device_oauth_flow(
        {
            "client id": "client",
            "endpoints": {
                "api": "https://api.example",
                "auth": "https://auth.example/device",
                "token": "https://auth.example/token",
            },
        },
        session=session,
        input_fn=lambda prompt: prompts.append(prompt),
        print_fn=lambda message: prints.append(message),
        now=start,
    )

    assert token["valid from"] == start
    assert token["valid to"] == start + dt.timedelta(seconds=3600)
    assert prompts
    # The verification URL must be shown to the user to complete authorization.
    assert any("https://auth.example/device" in message for message in prints)
    assert session.calls[0][2]["data"]["response_type"] == "code"


def test_device_oauth_flow_polls_pending_authorization():
    """The device flow polls past authorization_pending, honoring the interval.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    session = Session(
        [
            # Authorization request, then a pending poll, then the granted token.
            Response(
                {
                    "verification_uri_complete": "https://auth.example/device",
                    "device_code": "device",
                    "interval": 1,
                    "expires_in": 60,
                }
            ),
            Response({"error": "authorization_pending"}, status_code=400),
            Response(
                {
                    "access_token": "token",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                }
            ),
        ]
    )
    # Record sleep durations to confirm the polling interval is respected.
    sleeps = []

    token = run_device_oauth_flow(
        {
            "client id": "client",
            "endpoints": {
                "api": "https://api.example",
                "auth": "https://auth.example/device",
                "token": "https://auth.example/token",
            },
        },
        session=session,
        input_fn=lambda _prompt: None,
        print_fn=lambda _message: None,
        now=start,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    assert token["access_token"] == "token"
    # One sleep of the advertised interval between the pending poll and success.
    assert sleeps == [1]
    assert len(session.calls) == 3


def test_device_oauth_flow_rejects_invalid_poll_timing():
    """Wrong-typed interval/expires fields fail with a config error."""
    session = Session(
        [
            Response(
                {
                    "verification_uri_complete": "https://auth.example/device",
                    "device_code": "device",
                    "interval": "soon",
                    "expires_in": 60,
                }
            ),
        ]
    )

    with pytest.raises(ConfigError, match="poll timing"):
        run_device_oauth_flow(
            {
                "client id": "client",
                "endpoints": {
                    "api": "https://api.example",
                    "auth": "https://auth.example/device",
                    "token": "https://auth.example/token",
                },
            },
            session=session,
            input_fn=lambda _prompt: None,
            print_fn=lambda _message: None,
        )
