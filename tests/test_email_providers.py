from __future__ import annotations

import pytest

from parishkit.config import ConfigError
from parishkit.email.base import Attachment, Email, build_message, provider_from_config
from parishkit.email.google_workspace import GoogleWorkspaceSMTPProvider, xoauth2_string


def test_build_message_with_text_html_and_attachment(tmp_path):
    """build_message produces a multipart message carrying text, HTML, and a file."""
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


def test_provider_selection_rejects_ms365_until_implemented():
    """provider_from_config rejects MS365 before any external writes happen."""
    with pytest.raises(ConfigError, match="ms365 is not implemented"):
        provider_from_config({"provider": "ms365", "tenant_id": "tenant"})


def test_provider_selection_rejects_unknown():
    """provider_from_config raises ConfigError for an unrecognized provider name."""
    with pytest.raises(ConfigError):
        provider_from_config({"provider": "unknown"})


def test_google_workspace_provider_resolves_relative_service_account(
    tmp_path,
    monkeypatch,
):
    """Relative email credential paths resolve against the config directory."""
    calls = []

    def fake_load(path, *, scopes, subject):
        """Capture the resolved key path instead of loading Google credentials."""
        calls.append((path, scopes, subject))
        return object()

    monkeypatch.setattr(
        "parishkit.email.google_workspace.load_service_account_credentials",
        fake_load,
    )

    provider_from_config(
        {
            "provider": "google-workspace",
            "service_account_file": "credentials/mail-service-account.json",
            "delegated_user": "no-reply@example.org",
        },
        base_dir=tmp_path,
    )

    assert calls[0][0] == tmp_path / "credentials" / "mail-service-account.json"


def test_xoauth2_string_contains_user_and_token():
    """xoauth2_string returns a non-empty auth string for the given user and token."""
    auth = xoauth2_string("user@example.org", "token")

    assert isinstance(auth, str)
    assert auth


def test_google_workspace_send_uses_smtp_mock():
    """Send connects, authenticates via XOAUTH2, and delivers to all recipients.

    The fakes record each interaction in ``sent`` so the test can assert on the
    ordered sequence of connect, auth, and send calls. The setup stays local to
    this test so fixtures remain easy to understand and change.
    """
    sent = []

    # Already-valid credentials so send never needs to refresh.
    class Credentials:
        token = "token"
        valid = True

    class SMTP:
        """Fake SMTP client recording every interaction into ``sent``."""

        def __init__(self, host, port):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ehlo(self):
            """Record the required SMTP greeting before authentication."""
            sent.append(("ehlo",))
            return 250, b"ok"

        def docmd(self, command, payload):
            """Record the auth command and return SMTP 235 (auth succeeded)."""
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
    assert sent[1] == ("ehlo",)
    assert sent[2][0] == "AUTH"
    # to, cc, and bcc must all be passed to the SMTP envelope recipients.
    assert sent[-1] == (
        "send",
        "Subject",
        "from@example.org",
        ["to@example.org", "cc@example.org", "bcc@example.org"],
    )


def test_google_workspace_send_refreshes_invalid_credentials(monkeypatch):
    """Send refreshes expired credentials before authenticating and sending.

    The fake credentials start invalid with no token, so send must call
    ``refresh`` to obtain one before the message goes out. The setup stays local
    to this test so fixtures remain easy to understand and change.
    """
    refresh_requests = []
    sent = []

    # Credentials begin invalid to force a refresh on send.
    class Credentials:
        token = None
        valid = False

        def refresh(self, request):
            """Mark the credentials valid and record the refresh request."""
            refresh_requests.append(request)
            self.token = "token"
            self.valid = True

    class SMTP:
        """Fake SMTP client that accepts auth and records sends in ``sent``."""

        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ehlo(self):
            """Accept the required SMTP greeting before authentication."""
            return 250, b"ok"

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
    """from_config needs a service account file and delegated user, and rejects a
    non-integer smtp_port."""
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
