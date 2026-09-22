"""The smoke command reuses the installer's checks and sends only when asked."""

import json
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
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
    sent = []

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
            assert "secret-token" not in argument.split(" ", 1)[1][:0]
            return 235, b""

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
    # An invalid credential never sends.
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
    posts = []

    def post(url, *, headers, json, timeout, allow_redirects):
        posts.append((url, headers, json))
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    import requests

    monkeypatch.setattr(requests, "post", post)
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
