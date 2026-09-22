"""Human-run smoke checks of the installed provider credentials, redacted.

The operator runs these inside the deployed container that already holds the
credential, against the real provider, before the pre-launch gate and after a
credential changes. Each check reuses the installer's own validation code and
its endpoint-restricted session, then optionally does the one thing the
installer never does: sends a fixed message to an address or channel the
operator names. Output is a fixed JSON line naming the target, whether the
credential was accepted, and whether anything was sent; a failure is one
generic line. Nothing here reaches normal CI: the tests use fakes and the
real network path is the human's.
"""

import json
import smtplib
import ssl
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from parishkit.config import ConfigError

from .accounts.credential_errors import CredentialValidationUnavailable
from .accounts.integration_candidates import (
    GOOGLE_TOKEN_URI,
    slack_candidate,
    workspace_candidate,
)
from .accounts.key_files import read_private
from .accounts.policy_schema import normalized_email
from .accounts.provider_context import validated_context
from .deployment import ServiceRole, load_deployment
from .observability import Event, configure_logging, emit_failure
from .runtime_paths import RuntimeLayout

TARGETS = {"parishsoft", "google_workspace", "slack", "google_oauth"}
SUBJECT = "ParishKit Stewardship smoke test"
SLACK_POST = "https://slack.com/api/chat.postMessage"


def _text():
    """The fixed message body: the time and nothing about the deployment."""
    return (
        "This is a ParishKit Stewardship smoke test sent at "
        + datetime.now(UTC).isoformat(timespec="seconds")
        + ". No action is needed."
    )


def _credential(configuration, target):
    """The installed credential bytes of one target, from its mounted file."""
    if target not in configuration.secrets:
        raise ConfigError("This service does not hold that credential.")
    return read_private(RuntimeLayout(configuration).credential(target))


def check_parishsoft(configuration, *, organization_id):
    """Read-only: the exact organization the key sees is the configured one."""
    from .provider_check_worker import CheckSession, _parishsoft

    settings = validated_context("parishsoft", {"organization_id": organization_id})
    value = _credential(configuration, "parishsoft")
    return {"credential": _outcome(_parishsoft, value, settings, CheckSession())}


def check_workspace(configuration, *, delegated_email, send_to=None):
    """Authenticate the mailbox; with an address, send one fixed message."""
    from .provider_check_worker import CheckSession, _workspace

    # The installer's mailbox check reads only the delegated address; the
    # sender, reply-to and recipient fields belong to its later delivery check.
    settings = {"delegated_email": normalized_email(delegated_email)}
    value = _credential(configuration, "google_workspace")
    result = {"credential": _outcome(_workspace, value, settings, CheckSession())}
    if send_to is not None and result["credential"] == "valid":
        from parishkit.email.google_workspace import xoauth2_string

        credentials = workspace_candidate(value, delegated_email=delegated_email)
        credentials.refresh(_google_transport(CheckSession()))
        message = EmailMessage()
        message["From"] = delegated_email
        message["To"] = normalized_email(send_to)
        message["Subject"] = SUBJECT
        message.set_content(_text())
        with smtplib.SMTP_SSL(
            "smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()
        ) as smtp:
            smtp.ehlo()
            code, _ = smtp.docmd(
                "AUTH", "XOAUTH2 " + xoauth2_string(delegated_email, credentials.token)
            )
            if code != 235:
                raise ConfigError("The mailbox refused the smoke message.")
            smtp.send_message(message)
        result["sent"] = True
    return result


def _google_transport(session):
    """The installer's token exchange adapter, for one refresh before sending."""
    from types import SimpleNamespace

    def request(url, method="GET", body=None, headers=None, **kwargs):
        if url != GOOGLE_TOKEN_URI or method != "POST":
            raise ValueError("Unsupported token exchange.")
        response = session.request(method, url, data=body, headers=headers)
        return SimpleNamespace(
            status=response.status_code, data=response.content, headers=response.headers
        )

    return request


def check_slack(configuration, *, channel_id=None, send=False):
    """Authenticate the token; with a channel and --send, post one fixed message."""
    from .provider_check_worker import CheckSession, _slack

    value = _credential(configuration, "slack")
    result = {"credential": _outcome(_slack, value, {}, CheckSession())}
    if send and channel_id is not None and result["credential"] == "valid":
        import requests

        settings = validated_context("slack", {"channel_id": channel_id})
        response = requests.post(
            SLACK_POST,
            headers={"Authorization": "Bearer " + slack_candidate(value)},
            json={"channel": settings["channel_id"], "text": _text()},
            timeout=10,
            allow_redirects=False,
        )
        body = response.json() if response.status_code == 200 else {}
        if body.get("ok") is not True:
            raise ConfigError("Slack refused the smoke message.")
        result["sent"] = True
    return result


def check_google_oauth(configuration):
    """Validate the client document's shape and name the redirect URI to register."""
    from .runtime_web import parse_google_client

    parse_google_client(_credential(configuration, "google_oauth"))
    return {
        "credential": "valid",
        "redirect_uri": configuration.public_origin.rstrip("/")
        + "/admin/oauth/callback",
    }


def _outcome(check, value, settings, session):
    """Map the installer's tri-state (True/False/unavailable) to a word."""
    try:
        return "valid" if check(value, settings, session) else "invalid"
    except CredentialValidationUnavailable:
        return "unavailable"


def execute_smoke(args):
    """Console entry: one fixed JSON line, or one generic refusal."""
    configure_logging()
    try:
        if args.config is None or args.target not in TARGETS:
            raise ConfigError("Configuration and a known target are required.")
        configuration = load_deployment(Path(args.config))
        if configuration.service_role not in {
            ServiceRole.WEB,
            ServiceRole.WORKER,
            ServiceRole.MAIL_DISPATCH,
        }:
            raise ConfigError("Run the smoke check inside a deployed consumer.")
        if args.target == "parishsoft":
            result = check_parishsoft(
                configuration, organization_id=int(args.organization_id)
            )
        elif args.target == "google_workspace":
            result = check_workspace(
                configuration,
                delegated_email=args.delegated_email,
                send_to=args.send_to,
            )
        elif args.target == "slack":
            result = check_slack(
                configuration, channel_id=args.channel_id, send=bool(args.send)
            )
        else:
            result = check_google_oauth(configuration)
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: smoke check refused or failed; check the target, the "
            "consumer's credential mount and the operator options",
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"target": args.target, "sent": False, **result}, sort_keys=True))
    return 0
