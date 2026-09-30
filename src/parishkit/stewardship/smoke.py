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

TARGETS = {"parishsoft", "google_workspace", "slack", "google_oauth", "backup_drive"}
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
    settings = validated_context("parishsoft", {"organization_id": organization_id})
    value = _credential(configuration, "parishsoft")
    return {"credential": _outcome("parishsoft", value, settings)}


def check_workspace(configuration, *, delegated_email, send_to=None):
    """Authenticate the mailbox; with an address, send one fixed message."""
    # The installer's mailbox check reads only the delegated address; the
    # sender, reply-to and recipient fields belong to its later delivery check.
    settings = {"delegated_email": normalized_email(delegated_email)}
    value = _credential(configuration, "google_workspace")
    result = {"credential": _outcome("google_workspace", value, settings)}
    if send_to is not None and result["credential"] == "valid":
        from parishkit.email.google_workspace import xoauth2_string

        from .provider_check_worker import CheckSession

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
    value = _credential(configuration, "slack")
    result = {"credential": _outcome("slack", value, {})}
    if send and channel_id is not None and result["credential"] == "valid":
        settings = validated_context("slack", {"channel_id": channel_id})
        if not _slack_post(slack_candidate(value), settings["channel_id"]):
            raise ConfigError("Slack refused the smoke message.")
        result["sent"] = True
    return result


def _slack_post(token, channel):
    """Post the fixed message with the installer's transport posture.

    Like the credential checks, the token-bearing request ignores proxy, CA
    bundle and netrc settings from the environment, follows no redirect and
    reads a bounded answer; anything but Slack's own `ok` is a refusal.
    """
    import requests

    from .provider_check_worker import MAX_RESPONSE

    with requests.Session() as session:
        session.trust_env = False
        with session.post(
            SLACK_POST,
            headers={"Authorization": "Bearer " + token},
            json={"channel": channel, "text": _text()},
            timeout=10,
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.status_code != 200:
                return False
            content = response.raw.read(MAX_RESPONSE + 1, decode_content=True)
    if len(content) > MAX_RESPONSE:
        return False
    try:
        body = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return False
    return type(body) is dict and body.get("ok") is True


def check_google_oauth(configuration):
    """Validate the client document's shape and name the redirect URI to register."""
    from .runtime_web import parse_google_client

    parse_google_client(_credential(configuration, "google_oauth"))
    return {
        "credential": "valid",
        "redirect_uri": configuration.public_origin.rstrip("/")
        + "/admin/oauth/callback",
    }


def _deployment_tag(configuration):
    """This deployment's Drive set tag, read as the backup login (``--send``).

    A set folder written with the tag a real copy uses is found complete and
    reused by the next backup's copy, and retention prunes it like any other
    (#305 L3). Without it the folder kept the legacy tag: never pruned, and
    a real copy wrote a second folder of the same name beside it.
    """
    from django.db import connection

    from .backup_commands import _admit_backup_identity
    from .backup_offsite import set_tag
    from .operator_commands import configure_operator_database
    from .runtime_database import require_current_schema

    configure_operator_database(configuration)
    try:
        _admit_backup_identity()
        require_current_schema()
        return set_tag()
    finally:
        # The uploads that follow can take a long time; hold no connection.
        connection.close()


def check_backup_drive(configuration, *, delegated_email, folder_link, send=False):
    """The backup profile can write to the off-site Drive folder.

    Runs in the backup-worker container, which reads the installed Google
    Workspace key through its read-only credentials tree. The check writes
    one small file and trashes it; ``--send`` also copies the newest complete
    local backup set, exactly as the backup command would, into a set folder
    tagged with this deployment's identity (so it reads the database too).
    """
    from .backup_drive import (
        DriveClient,
        DriveFailure,
        folder_id_from_url,
        probe,
        upload_set,
        workspace_session,
    )
    from .backup_offsite import SEALED_FILES, _complete_sets, _copy_lock
    from .runtime_paths import explicit_path, private_directory

    if configuration.service_role is not ServiceRole.BACKUP_WORKER:
        raise ConfigError("Run the off-site backup check in the backup profile.")
    folder = folder_id_from_url(folder_link or "")
    tag = {"tag": _deployment_tag(configuration)} if send else {}
    value = read_private(RuntimeLayout(configuration).credential("google_workspace"))
    try:
        client = DriveClient(
            workspace_session(value, subject=normalized_email(delegated_email)),
            **tag,
        )
        probe(client, folder)
        copied = None
        if send:
            backups = private_directory(explicit_path(configuration.paths["backups"]))
            sets = _complete_sets(backups)
            if not sets:
                raise ConfigError("There is no complete backup set to copy.")
            # The backup's own copy may be writing this set's folder; never
            # replace it mid-upload (upload_set trashes a mismatched folder).
            with _copy_lock(backups) as held:
                if not held:
                    return {
                        "accepted": False,
                        "reason": "busy",
                        "message": "A backup copy is running; try again later.",
                    }
                upload_set(client, folder, sets[-1], SEALED_FILES)
            copied = sets[-1].name
    except DriveFailure as failure:
        return {"accepted": False, "reason": failure.kind, "message": failure.message}
    return {"accepted": True, "copied_set": copied}


def _outcome(target, value, settings):
    """The installer's own classification of one check, word for word."""
    from .provider_check_worker import classify

    return classify(target, value, settings)


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
            ServiceRole.BACKUP_WORKER,
        }:
            raise ConfigError("Run the smoke check inside a deployed consumer.")
        if args.target == "backup_drive":
            result = check_backup_drive(
                configuration,
                delegated_email=args.delegated_email,
                folder_link=args.folder_link,
                send=bool(args.send),
            )
            print(json.dumps({"target": args.target, **result}, sort_keys=True))
            return 0
        if configuration.service_role is ServiceRole.BACKUP_WORKER:
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
