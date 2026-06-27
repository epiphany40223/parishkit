from __future__ import annotations

import datetime as dt
import json
import stat

import pytest

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
    run_device_oauth_flow,
    save_access_token,
    set_valid_from_to,
    sign_up_form_body,
    token_is_valid,
    update_contact_body,
)
from parishkit.retry import RetryPolicy


class Response:
    def __init__(self, payload, *, status_code=200, url="https://api.example/v3/items"):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.url = url

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.responses.pop(0)

    def put(self, url, **kwargs):
        self.calls.append(("put", url, kwargs))
        return self.responses.pop(0)


def config():
    return ConstantContactConfig(
        client_id={"endpoints": {"api": "https://api.example"}},
        access_token={"access_token": "token"},
    )


def test_access_token_serialization(tmp_path):
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
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token_is_valid(token, now=dt.datetime(2026, 1, 1, 0, 0, 30, tzinfo=dt.UTC))


def test_api_pagination():
    session = Session(
        [
            Response(
                {"items": [{"id": 1}], "_links": {"next": {"href": "/v3/items?page=2"}}}
            ),
            Response({"items": [{"id": 2}]}),
        ]
    )
    client = ConstantContactClient(config(), session=session)

    assert client.get_all("items", "items") == [{"id": 1}, {"id": 2}]
    assert len(session.calls) == 2
    assert session.calls[0][2]["timeout"] == 30.0


def test_api_error_raises_typed_exception():
    client = ConstantContactClient(
        config(), session=Session([Response({}, status_code=400)])
    )

    with pytest.raises(CCAPIError):
        client.post("items", {})


def test_exhausted_transient_response_raises_typed_exception():
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


def test_config_validation_rejects_missing_keys():
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
    path = tmp_path / "token.json"
    path.write_text('{"access_token": "token"}', encoding="utf-8")

    with pytest.raises(ConfigError, match="missing"):
        load_access_token(path)


def test_refresh_access_token_rejects_non_json_response(tmp_path):
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

    class BadJSONResponse(Response):
        def json(self):
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
            session=Session([BadJSONResponse("not json", status_code=500)]),
            now=start,
        )


def test_contact_body_helpers_strip_periods():
    contact = {
        "contact_id": "id",
        "first_name": "T.J.",
        "email_address": {"address": "A@EXAMPLE.ORG"},
        "list_memberships": ["list"],
    }

    assert update_contact_body(contact)["first_name"] == "TJ"
    assert sign_up_form_body(contact)["email_address"] == "A@EXAMPLE.ORG"


def test_contact_linking_helpers():
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
    assert members[1]["CONTACT"] is contact


def test_get_access_token_refreshes_expired_token(tmp_path):
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
    assert load_access_token(token_path)["access_token"] == "new"
    assert session.calls[0][2]["timeout"] == 30.0


def test_get_access_token_refresh_failure_requires_manual_reauth(tmp_path):
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
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    session = Session(
        [
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
    assert any("https://auth.example/device" in message for message in prints)
    assert session.calls[0][2]["data"]["response_type"] == "code"


def test_device_oauth_flow_polls_pending_authorization():
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    session = Session(
        [
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
    assert sleeps == [1]
    assert len(session.calls) == 3
