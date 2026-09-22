"""The smoke command reuses the installer's checks and sends only when asked."""

import json
from types import SimpleNamespace

import pytest
import requests

from parishkit.config import ConfigError
from parishkit.parishsoft import ParishSoftAPIError
from parishkit.stewardship import provider_check_worker, smoke
from parishkit.stewardship.accounts.credential_errors import (
    CredentialValidationUnavailable,
)
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_paths import RuntimeLayout, private_directory
from parishkit.stewardship.runtime_topology import _service_config

from .test_runtime_topology import configuration_at


def consumer(tmp_path, role, provider_mode="configured", **credentials):
    """A deployed consumer's configuration with the named credentials installed."""
    configuration = _service_config(
        configuration_at(tmp_path), role, provider_mode=provider_mode
    )
    layout = RuntimeLayout(configuration)
    for target, value in credentials.items():
        directory = layout.credential_directory(target)
        directory.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        private_directory(directory, create=True)
        write_private(layout.credential(target), value)
    return configuration


def outcome(verdict):
    """An installer check that answers valid, invalid or unavailable."""

    def check(value, settings, session):
        if verdict == "unavailable":
            raise CredentialValidationUnavailable()
        return verdict == "valid"

    return check


def raising(error):
    """An installer check that fails the way the real one does."""

    def check(value, settings, session):
        raise error

    return check


# How the real checks signal each outcome: a rejected ParishSoft key is an
# API error, a malformed credential file a parser refusal, an outage a
# transport error or an unexplained failure.
REAL_FAILURES = [
    (ParishSoftAPIError(401, "/organizations/search", "denied"), "invalid"),
    (ParishSoftAPIError(403, "/organizations/search", "denied"), "invalid"),
    (ParishSoftAPIError(503, "/organizations/search", "down"), "unavailable"),
    (ConfigError("malformed credential"), "invalid"),
    (requests.ConnectionError("no route"), "unavailable"),
    (requests.Timeout("slow"), "unavailable"),
    (OSError("reset"), "unavailable"),
    (ValueError("organization differs"), "unavailable"),
    (CredentialValidationUnavailable(), "unavailable"),
]


@pytest.mark.parametrize("verdict", ["valid", "invalid", "unavailable"])
def test_parishsoft_check_maps_the_installer_outcome(tmp_path, monkeypatch, verdict):
    """The read-only tenant check reaches the same three words as installation."""
    configuration = consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key-bytes")
    seen = []
    monkeypatch.setattr(
        provider_check_worker,
        "_parishsoft",
        lambda value, settings, session: (
            seen.append((value, settings)) or outcome(verdict)(value, settings, session)
        ),
    )
    assert smoke.check_parishsoft(configuration, organization_id=42) == {
        "credential": verdict
    }
    assert seen == [(b"key-bytes", {"organization_id": 42})]
    with pytest.raises(ConfigError):
        smoke.check_parishsoft(configuration, organization_id=0)


@pytest.mark.parametrize("error, expected", REAL_FAILURES)
@pytest.mark.parametrize(
    "target, role, mode, check",
    [
        ("parishsoft", ServiceRole.WORKER, "configured", "_parishsoft"),
        ("google_workspace", ServiceRole.MAIL_DISPATCH, "configured", "_workspace"),
        ("slack", ServiceRole.WORKER, "configured-slack", "_slack"),
    ],
)
def test_real_failures_classify_as_installation_does(
    tmp_path, monkeypatch, error, expected, target, role, mode, check
):
    """Each target's real exception types reach the installer's own words."""
    configuration = consumer(tmp_path, role, mode, **{target: b"credential\n"})
    monkeypatch.setattr(provider_check_worker, check, raising(error))
    result = {
        "parishsoft": lambda: smoke.check_parishsoft(configuration, organization_id=7),
        "google_workspace": lambda: smoke.check_workspace(
            configuration, delegated_email="mail@parish.example", send_to="a@b.example"
        ),
        "slack": lambda: smoke.check_slack(configuration, channel_id="C1", send=True),
    }[target]()
    # A check that did not pass never sends.
    assert result == {"credential": expected}
    # The installer's own helper classifies the same failure the same way.
    assert provider_check_worker.classify(target, b"credential\n", {}) == expected


def test_mailbox_sends_one_fixed_message_only_after_a_valid_check(
    tmp_path, monkeypatch
):
    """The send uses the operator's address and the fixed subject and body."""
    configuration = consumer(
        tmp_path, ServiceRole.MAIL_DISPATCH, google_workspace=b"{}"
    )
    monkeypatch.setattr(provider_check_worker, "_workspace", outcome("valid"))
    monkeypatch.setattr(
        smoke,
        "workspace_candidate",
        lambda value, *, delegated_email: SimpleNamespace(
            token="secret-token", refresh=lambda request: None
        ),
    )
    sent, auth_code = [], [235]

    class Smtp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            return 250, b""

        def docmd(self, command, argument=""):
            assert command == "AUTH" and argument.startswith("XOAUTH2 ")
            # The token travels only inside the base64 SASL string.
            assert "secret-token" not in argument
            return auth_code[0], b""

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(smoke.smtplib, "SMTP_SSL", Smtp)
    assert smoke.check_workspace(
        configuration, delegated_email="mail@parish.example"
    ) == {"credential": "valid"}
    assert sent == []
    result = smoke.check_workspace(
        configuration,
        delegated_email="mail@parish.example",
        send_to="Operator@Parish.example",
    )
    assert result == {"credential": "valid", "sent": True}
    (message,) = sent
    assert message["To"] == "operator@parish.example"
    assert message["Subject"] == smoke.SUBJECT
    assert "smoke test" in message.get_content()
    # A mailbox that refuses the send-time authentication sends nothing.
    auth_code[0] = 535
    with pytest.raises(ConfigError, match="refused"):
        smoke.check_workspace(
            configuration, delegated_email="mail@parish.example", send_to="x@y.example"
        )
    assert len(sent) == 1
    # An invalid credential never sends.
    auth_code[0] = 235
    monkeypatch.setattr(provider_check_worker, "_workspace", outcome("invalid"))
    assert smoke.check_workspace(
        configuration, delegated_email="mail@parish.example", send_to="x@y.example"
    ) == {"credential": "invalid"}
    assert len(sent) == 1


def test_slack_posts_only_with_a_channel_and_send(tmp_path, monkeypatch):
    """auth.test alone by default; one fixed post when the operator asks."""
    configuration = consumer(
        tmp_path, ServiceRole.WORKER, "configured-slack", slack=b"xoxb-token\n"
    )
    monkeypatch.setattr(provider_check_worker, "_slack", outcome("valid"))
    posts, answers, sessions = [], [(200, b'{"ok": true}')], []

    class Response:
        """A streamed answer whose body is read with a bound."""

        def __init__(self, status, content):
            self.status_code = status
            self.raw = SimpleNamespace(read=lambda size, decode_content: content[:size])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Session:
        """Records the transport posture the post runs under."""

        def __init__(self):
            self.trust_env = True
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, *, headers, json, timeout, allow_redirects, stream):
            assert self.trust_env is False and allow_redirects is False
            assert stream is True and timeout == 10
            posts.append((url, headers, json))
            return Response(*answers[0])

    monkeypatch.setattr(requests, "Session", Session)
    assert smoke.check_slack(configuration) == {"credential": "valid"}
    assert smoke.check_slack(configuration, channel_id="C123") == {
        "credential": "valid"
    }
    assert smoke.check_slack(configuration, channel_id="C123", send=True) == {
        "credential": "valid",
        "sent": True,
    }
    (url, headers, body) = posts[0]
    assert url == smoke.SLACK_POST and headers["Authorization"] == "Bearer xoxb-token"
    assert body["channel"] == "C123" and "smoke test" in body["text"]
    with pytest.raises(ConfigError):
        smoke.check_slack(configuration, channel_id="bad channel", send=True)
    # Slack refusing the post, by status, by its own answer, with an answer
    # that is not JSON or one past the bound, is a refusal.
    oversized = b'{"ok": true, "pad": "' + b"x" * 70000 + b'"}'
    for answer in (
        (200, b'{"ok": false, "error": "not_in_channel"}'),
        (500, b"{}"),
        (200, b"<html>"),
        (200, b"[true]"),
        (200, oversized),
    ):
        answers[0] = answer
        with pytest.raises(ConfigError, match="refused"):
            smoke.check_slack(configuration, channel_id="C123", send=True)
    assert all(session.trust_env is False for session in sessions)


def test_the_mailbox_token_exchange_uses_only_the_token_endpoint(monkeypatch):
    """The send-time refresh reaches Google's token URL through the session."""
    calls = []

    class Session:
        """A restricted session answering the token exchange."""

        def request(self, method, url, data=None, headers=None):
            calls.append((method, url, data, headers))
            return SimpleNamespace(
                status_code=200, content=b'{"access_token": "t"}', headers={"a": "b"}
            )

    request = smoke._google_transport(Session())
    answer = request(
        smoke.GOOGLE_TOKEN_URI, method="POST", body="grant", headers={"h": "v"}
    )
    assert (answer.status, answer.data, answer.headers) == (
        200,
        b'{"access_token": "t"}',
        {"a": "b"},
    )
    assert calls == [("POST", smoke.GOOGLE_TOKEN_URI, "grant", {"h": "v"})]
    for url, method in (
        ("https://example.invalid/token", "POST"),
        (smoke.GOOGLE_TOKEN_URI, "GET"),
    ):
        with pytest.raises(ValueError):
            request(url, method=method)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "role, target",
    [
        (ServiceRole.SCHEDULER, None),
        (ServiceRole.BACKUP_WORKER, None),
        (ServiceRole.CREDENTIAL_INSTALLER, "parishsoft"),
    ],
)
def test_a_non_consumer_profile_is_refused(tmp_path, monkeypatch, capsys, role, target):
    """Only a deployed web, worker or mail-dispatch service runs the check."""
    from parishkit.stewardship.deployment_documents import deployment_document

    configuration = _service_config(configuration_at(tmp_path), role, target=target)
    path = tmp_path / "service.yaml"
    path.write_text(json.dumps(deployment_document(configuration)))
    monkeypatch.setenv("PARISHKIT_ROOT", str(tmp_path))
    monkeypatch.setattr(smoke, "configure_logging", lambda: None)
    called = []
    monkeypatch.setattr(smoke, "check_parishsoft", lambda *a, **k: called.append(a))
    args = SimpleNamespace(
        config=str(path),
        target="parishsoft",
        organization_id="7",
        delegated_email=None,
        send_to=None,
        channel_id=None,
        send=None,
    )
    assert smoke.execute_smoke(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "smoke check refused" in captured.err
    assert called == []


def test_a_send_failure_reaches_the_console_as_one_generic_line(
    tmp_path, monkeypatch, capsys
):
    """A failure after a valid check echoes no address, token or provider text."""
    from parishkit.stewardship.deployment_documents import deployment_document

    configuration = consumer(
        tmp_path, ServiceRole.MAIL_DISPATCH, google_workspace=b"{}"
    )
    path = tmp_path / "mail-dispatch.yaml"
    path.write_text(json.dumps(deployment_document(configuration)))
    monkeypatch.setenv("PARISHKIT_ROOT", str(tmp_path))
    monkeypatch.setattr(smoke, "configure_logging", lambda: None)
    monkeypatch.setattr(provider_check_worker, "_workspace", outcome("valid"))

    def refused(*args, **kwargs):
        raise OSError("private-provider-text")

    monkeypatch.setattr(smoke, "workspace_candidate", refused)
    args = SimpleNamespace(
        config=str(path),
        target="google_workspace",
        organization_id=None,
        delegated_email="mail@parish.example",
        send_to="private-recipient@parish.example",
        channel_id=None,
        send=None,
    )
    assert smoke.execute_smoke(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "smoke check refused" in captured.err
    assert "private" not in captured.err


def test_oauth_document_shape_and_redirect_uri(tmp_path):
    """The client document is validated and the callback to register is named."""
    good = b'{"client_id": "id", "client_secret": "secret"}'
    configuration = consumer(tmp_path, ServiceRole.WEB, google_oauth=good)
    assert smoke.check_google_oauth(configuration) == {
        "credential": "valid",
        "redirect_uri": "http://localhost:8010/admin/oauth/callback",
    }
    bad = consumer(tmp_path / "bad", ServiceRole.WEB, google_oauth=b'{"client_id": 1}')
    with pytest.raises(ConfigError):
        smoke.check_google_oauth(bad)


def test_console_refuses_generically_and_prints_fixed_json(
    tmp_path, monkeypatch, capsys
):
    """Wrong profile, unknown target and a missing credential all refuse alike."""
    configuration = consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key")
    path = tmp_path / "worker.yaml"
    from parishkit.stewardship.deployment_documents import deployment_document

    path.write_text(json.dumps(deployment_document(configuration)))
    # The console loads the document under the process environment, as the
    # deployed consumer does; pin the root the fixture rendered with.
    monkeypatch.setenv("PARISHKIT_ROOT", str(tmp_path))
    monkeypatch.setattr(provider_check_worker, "_parishsoft", outcome("valid"))
    # CLI unit tests must not retain handlers bound to a finished capture
    # stream, or later tests' stderr carries a logging error.
    monkeypatch.setattr(smoke, "configure_logging", lambda: None)
    args = SimpleNamespace(
        config=str(path),
        target="parishsoft",
        organization_id="7",
        delegated_email=None,
        send_to=None,
        channel_id=None,
        send=None,
    )
    assert smoke.execute_smoke(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "target": "parishsoft",
        "credential": "valid",
        "sent": False,
    }
    for changed in (
        {"target": "unknown"},
        {"target": "slack"},
        {"organization_id": "private-value"},
        {"config": str(tmp_path / "private-missing.yaml")},
    ):
        assert smoke.execute_smoke(SimpleNamespace(**{**vars(args), **changed})) == 2
        captured = capsys.readouterr()
        assert "private" not in captured.err and not captured.out
        assert "smoke check refused" in captured.err


def test_the_parser_admits_only_the_smoke_options(monkeypatch, capsys):
    """Another command's option is refused by name before anything runs."""
    from parishkit.stewardship.cli import main

    called = []
    monkeypatch.setattr(smoke, "execute_smoke", lambda args: called.append(args) or 0)
    assert (
        main(
            [
                "smoke",
                "--config",
                "c.yaml",
                "--target",
                "slack",
                "--channel-id",
                "C1",
                "--send",
            ]
        )
        == 0
    )
    assert called[0].send is True and called[0].channel_id == "C1"
    with pytest.raises(SystemExit):
        main(["smoke", "--config", "c.yaml", "--image", "private-image"])
    assert "private-image" not in capsys.readouterr().err
