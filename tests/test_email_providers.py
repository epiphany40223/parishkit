from __future__ import annotations

import pytest

from parishkit.config import ConfigError
from parishkit.email.base import Attachment, Email, build_message, provider_from_config
from parishkit.email.google_workspace import GoogleWorkspaceSMTPProvider, xoauth2_string


def test_build_message_with_text_html_and_attachment(tmp_path):
    attachment = tmp_path / "report.txt"
    attachment.write_text("report", encoding="utf-8")

    message = build_message(
        Email(
            subject="Subject",
            sender="from@example.org",
            to=["to@example.org"],
            text="Plain",
            html="<p>HTML</p>",
            attachments=[Attachment(path=attachment, mime_type="text/plain")],
        )
    )

    assert message["Subject"] == "Subject"
    assert message["To"] == "to@example.org"
    assert message.is_multipart()


def test_provider_selection_ms365_dry_run():
    provider = provider_from_config({"provider": "ms365", "tenant_id": "tenant"})
    built = provider.send(
        Email(
            subject="Subject",
            sender="from@example.org",
            to=["to@example.org"],
            text="Plain",
        ),
        dry_run=True,
    )

    assert built["Subject"] == "Subject"


def test_provider_selection_rejects_unknown():
    with pytest.raises(ConfigError):
        provider_from_config({"provider": "unknown"})


def test_xoauth2_string_contains_user_and_token():
    auth = xoauth2_string("user@example.org", "token")

    assert isinstance(auth, str)
    assert auth


def test_google_workspace_send_uses_smtp_mock():
    sent = []

    class Credentials:
        token = "token"
        valid = True

    class SMTP:
        def __init__(self, host, port):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def docmd(self, command, payload):
            sent.append((command, payload))
            return 235, b"ok"

        def send_message(self, message, *, from_addr, to_addrs):
            sent.append(("send", message["Subject"], from_addr, to_addrs))

    provider = GoogleWorkspaceSMTPProvider(
        smtp_host="smtp.example.org",
        smtp_port=465,
        user="user@example.org",
        credentials=Credentials(),
        smtp_factory=SMTP,
    )

    provider.send(
        Email(
            subject="Subject",
            sender="from@example.org",
            to=["to@example.org"],
            cc=["cc@example.org"],
            bcc=["bcc@example.org"],
            text="Plain",
        )
    )

    assert sent[0] == ("connect", "smtp.example.org", 465)
    assert sent[-1] == (
        "send",
        "Subject",
        "from@example.org",
        ["to@example.org", "cc@example.org", "bcc@example.org"],
    )


def test_google_workspace_send_refreshes_invalid_credentials(monkeypatch):
    refresh_requests = []
    sent = []

    class Credentials:
        token = None
        valid = False

        def refresh(self, request):
            refresh_requests.append(request)
            self.token = "token"
            self.valid = True

    class SMTP:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def docmd(self, *_args):
            return 235, b"ok"

        def send_message(self, *_args, **_kwargs):
            sent.append("sent")

    monkeypatch.setattr(
        "parishkit.email.google_workspace._google_auth_request",
        lambda: object(),
    )
    provider = GoogleWorkspaceSMTPProvider(
        smtp_host="smtp.example.org",
        smtp_port=465,
        user="user@example.org",
        credentials=Credentials(),
        smtp_factory=SMTP,
    )

    provider.send(
        Email(
            subject="Subject",
            sender="from@example.org",
            to=["to@example.org"],
            text="Plain",
        )
    )

    assert refresh_requests
    assert sent == ["sent"]


def test_google_workspace_config_requires_key_and_user(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "parishkit.email.google_workspace.load_service_account_credentials",
        lambda *_args, **_kwargs: object(),
    )
    provider = GoogleWorkspaceSMTPProvider.from_config(
        {
            "service_account_file": str(tmp_path / "service.json"),
            "delegated_user": "user@example.org",
        }
    )

    assert provider.user == "user@example.org"

    with pytest.raises(ConfigError):
        GoogleWorkspaceSMTPProvider.from_config({"service_account_file": "x"})

    with pytest.raises(ConfigError, match="smtp_port"):
        GoogleWorkspaceSMTPProvider.from_config(
            {
                "service_account_file": str(tmp_path / "service.json"),
                "delegated_user": "user@example.org",
                "smtp_port": "465",
            }
        )
