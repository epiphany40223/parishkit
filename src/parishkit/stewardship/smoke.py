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

One target writes provider data: ``parishsoft_contact`` with ``--send``
writes a marker to one chosen test Member's contact record, reads it back
and restores the original (#528, ``responses.contact_smoke``).
"""

import json
import smtplib
import ssl
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from parishkit.config import ConfigError
from parishkit.parishsoft_http_worker import contact_put_admitted

from .accounts.integration_candidates import (
    GOOGLE_TOKEN_URI,
    slack_candidate,
    workspace_candidate,
)
from .accounts.key_files import read_private
from .accounts.policy_schema import normalized_email
from .accounts.provider_context import validated_context
from .deployment import ServiceRole, load_deployment
from .local import source_base_url
from .observability import Event, configure_logging, emit_failure
from .runtime_paths import RuntimeLayout

TARGETS = {
    "parishsoft",
    "parishsoft_contact",
    "google_workspace",
    "slack",
    "google_oauth",
    "backup_drive",
}
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
    return {"credential": _outcome("parishsoft", value, settings, configuration)}


def check_parishsoft_contact(
    configuration,
    *,
    organization_id,
    member_id,
    confirm_member_id,
    field=None,
    send=False,
):
    """Probe one test Member's contact read; with ``send``, round-trip one field.

    The census publication read's confirmation (#528, ``contact_smoke``).
    It refuses before reading anything unless the Member id is given twice,
    identically, and runs only in the worker, which holds the ParishSoft
    credential. The tenant check runs before the Member is read, so a key
    that sees another organization never reaches it. Before ``--send``
    restores the original, the value-free marker result goes to standard
    error, so it survives a process that dies during the restore.

    It may write, so census publication's one write rule decides first, in
    every profile (#1884): Production is admitted as before, a LOCAL
    deployment only while it reads the local fake (#1399). A LOCAL one
    reading the real API is refused before even the tenant read; the
    publication session and the helper would refuse its write again.
    """
    base_url = source_base_url(configuration.profile, configuration.parishsoft_base_url)
    if not contact_put_admitted(configuration.profile.value, base_url):
        raise ConfigError(
            "The Member contact check runs in the local profile only against "
            "the local fake ParishSoft."
        )
    if configuration.service_role is not ServiceRole.WORKER:
        raise ConfigError("Run the Member contact check in the worker.")
    if member_id is None or member_id != confirm_member_id:
        raise ConfigError("The Member id must be given and confirmed.")
    # Django first: the gateway module (through storage) defines models, so
    # it cannot even be imported before settings exist. The gateway also
    # refuses calls inside a transaction and records a helper killed at its
    # deadline durably, which both need the database configured.
    from .operator_commands import configure_operator_database

    configure_operator_database(configuration)
    from .responses import contact_smoke
    from .responses.publication_gateway import ParishSoftContactGateway, _member_id
    from .source.credentials import SourceCredential

    member = _member_id(member_id)
    result = check_parishsoft(configuration, organization_id=organization_id)
    if result["credential"] != "valid":
        return result
    gateway = ParishSoftContactGateway.open_operator(
        SourceCredential(_credential(configuration, "parishsoft")),
        profile=configuration.profile.value,
        base_url=base_url,
        on_timeout=_contact_timeout,
    )
    try:
        return result | contact_smoke.run(
            gateway,
            member,
            send=bool(send),
            field=field or contact_smoke.DEFAULT_FIELD,
            report=_contact_progress,
        )
    except contact_smoke.RoundTripAborted as error:
        error.result = result | error.result
        raise
    finally:
        gateway.close()


def _contact_progress(result):
    """Print the marker step's value-free result before the restore runs."""
    print(
        json.dumps({"target": "parishsoft_contact", "step": "marker", **result}),
        file=sys.stderr,
        flush=True,
    )


def _contact_timeout(limit, elapsed):
    """Log a contact smoke request killed at its deadline (what, limit, elapsed)."""
    from .audit.timeouts import record_timeout

    record_timeout(
        Event.HELPER_TIMED_OUT,
        what="source_helper",
        helper="parishsoft_http_worker",
        limit_seconds=limit,
        elapsed_seconds=elapsed,
        bind_task=False,
    )


def check_workspace(configuration, *, delegated_email, send_to=None):
    """Authenticate the mailbox; with an address, send one fixed message.

    The message goes over Gmail, so its one recipient is routed by the same
    rule as every other submission (``mail_catcher.envelope``, #1399), and
    first: a LOCAL ``mail_redirect`` replaces it, and LOCAL without one is
    refused before the key is read or any provider is contacted.
    """
    from .mail_catcher import MailTransport, envelope, is_mail_catcher

    route = dict(
        profile=configuration.profile,
        transport=MailTransport.GMAIL,
        redirect=configuration.mail_redirect,
    )
    if send_to is not None:
        # The route alone, before the key is read: an empty envelope raises
        # exactly when a real one would, and checks no address yet.
        envelope((), **route)
    # The installer's mailbox check reads only the delegated address; the
    # sender, reply-to and recipient fields belong to its later delivery check.
    settings = {"delegated_email": normalized_email(delegated_email)}
    value = _credential(configuration, "google_workspace")
    result = {
        "credential": _outcome("google_workspace", value, settings, configuration)
    }
    if send_to is not None and result["credential"] == "valid":
        if is_mail_catcher(value):
            # Valid only in LOCAL, where the check reached Mailpit; the send
            # goes over Gmail and needs a real key (#1878 review B3).
            raise ConfigError(
                "The smoke message needs a Google service account; the mail "
                "catcher is installed."
            )
        from parishkit.email.google_workspace import xoauth2_string

        from .provider_check_worker import CheckSession

        (recipient,) = envelope((normalized_email(send_to),), **route)
        credentials = workspace_candidate(value, delegated_email=delegated_email)
        credentials.refresh(_google_transport(CheckSession(configuration.profile)))
        message = EmailMessage()
        message["From"] = delegated_email
        message["To"] = recipient
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
            # Name the envelope explicitly instead of letting smtplib parse
            # it back out of the headers, as readiness_delivery does.
            smtp.send_message(message, from_addr=delegated_email, to_addrs=[recipient])
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
    result = {"credential": _outcome("slack", value, {}, configuration)}
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
    """Validate the client document as web would and name the redirect URI.

    The same admission web applies at start, including the fake-clock rule
    for a real client in LOCAL (#1399), so a refusal shows before a restart.
    """
    from .runtime_web import admit_google_clock, parse_google_client

    admit_google_clock(
        parse_google_client(
            _credential(configuration, "google_oauth"), profile=configuration.profile
        )
    )
    return {
        "credential": "valid",
        "redirect_uri": configuration.public_origin.rstrip("/")
        + "/admin/oauth/callback",
    }


def _deployment_tag(configuration, backups=None):
    """This deployment's Drive set tag, read as the profile's login (``--send``).

    A set folder written with the tag a real copy uses is found complete and
    reused by the next backup's copy, and retention prunes it like any other
    (#305 L3). Without it the folder kept the legacy tag: never pruned, and
    a real copy wrote a second folder of the same name beside it.

    The off-site uploader (#235) admits its own login instead, and passes
    its ``backups`` directory: it copies the newest local set that a backup
    run row binds (``uploader_commands.binds``), so a set a compromised
    backup image staged under a borrowed name is refused, not copied. It
    returns ``(tag, (directory, digest))`` then; the backup profile returns
    the tag.
    """
    from django.db import connection

    from .backup_offsite import set_tag
    from .operator_commands import configure_operator_database
    from .runtime_database import require_current_schema

    configure_operator_database(configuration)
    try:
        if configuration.service_role is ServiceRole.BACKUP_UPLOADER:
            from .uploader_boundaries import admit_uploader_identity

            admit_uploader_identity()
            require_current_schema()
            return set_tag(), _bound_newest(backups)
        from .backup_commands import _admit_backup_identity

        _admit_backup_identity()
        require_current_schema()
        return set_tag()
    finally:
        # The uploads that follow can take a long time; hold no connection.
        connection.close()


def _bound_newest(backups):
    """The newest complete local set and its digest, refused unless bound.

    Returns ``(directory, digest)``; the copy then uploads the set only if
    its manifest still has that digest (``upload_set``'s
    ``expected_digest``), so a set swapped on disk after this binding is
    refused rather than copied under the bound name (#235).
    """
    from .backup_offsite import _complete_sets, _digest
    from .jobs.backup_models import BackupRun
    from .uploader_commands import binds

    sets = _complete_sets(backups)
    if not sets:
        raise ConfigError("There is no complete backup set to copy.")
    newest = sets[-1]
    digest = _digest(newest)
    runs = (
        BackupRun.objects.filter(manifest_digest=digest).values_list(
            "started_at", "completed_at"
        )
        if digest is not None
        else ()
    )
    if not any(binds(newest.name, *times) for times in runs):
        raise ConfigError("The newest backup set is not bound to its backup run.")
    return newest, digest


def check_backup_drive(configuration, *, delegated_email, folder_link, send=False):
    """The backup or uploader profile can write to the off-site Drive folder.

    Runs in the backup-worker container, which reads the installed Google
    Workspace key through its read-only credentials tree, or in the off-site
    uploader's (#235), which mounts that key alone, after admitting its
    mounts. The check writes one small file and trashes it; ``--send`` also
    copies the newest complete local backup set, exactly as the backup
    command would, into a set folder tagged with this deployment's identity
    (so it reads the database too), under the host's copy lock. From the
    uploader profile ``--send`` admits the uploader's own login, takes the
    copy lock as its ``upload`` does (never creating the legacy file on its
    read-only mount) and copies only a set bound to its run row.
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
    from .mail_catcher import admit_real_workspace
    from .runtime_paths import explicit_path, private_directory

    role = configuration.service_role
    if role not in {ServiceRole.BACKUP_WORKER, ServiceRole.BACKUP_UPLOADER}:
        raise ConfigError("Run the off-site backup check in the backup profile.")
    if role is ServiceRole.BACKUP_UPLOADER:
        from .uploader_boundaries import admit_uploader_service

        admit_uploader_service(configuration)
    # Drive opens in LOCAL only where a real Workspace key may be used: with a
    # mail redirect, on the normal clock (#1399). Refused before the key or
    # the database is read.
    admit_real_workspace(configuration.profile, configuration.mail_redirect)
    folder = folder_id_from_url(folder_link or "")
    uploader = role is ServiceRole.BACKUP_UPLOADER
    backups = newest = digest = None
    if send:
        backups = private_directory(explicit_path(configuration.paths["backups"]))
    tag = {}
    if send and uploader:
        value, (newest, digest) = _deployment_tag(configuration, backups)
        tag = {"tag": value}
    elif send:
        tag = {"tag": _deployment_tag(configuration)}
    # The uploader mounts the key itself (its rotating directory, or the
    # overridden file); the backup profile reads it inside its tree.
    key = (
        configuration.secrets["google_workspace"]
        if role is ServiceRole.BACKUP_UPLOADER
        else RuntimeLayout(configuration).credential("google_workspace")
    )
    value = read_private(key)
    try:
        client = DriveClient(
            workspace_session(
                value,
                subject=normalized_email(delegated_email),
                profile=configuration.profile,
                redirect=configuration.mail_redirect,
            ),
            **tag,
        )
        probe(client, folder)
        copied = None
        if send:
            if newest is None:
                sets = _complete_sets(backups)
                if not sets:
                    raise ConfigError("There is no complete backup set to copy.")
                newest = sets[-1]
            # Another copy may be writing this set's folder; never replace
            # it mid-upload (upload_set trashes a mismatched folder).
            with _copy_lock(backups, create=not uploader) as held:
                if not held:
                    return {
                        "accepted": False,
                        "reason": "busy",
                        "message": "A backup copy is running; try again later.",
                    }
                # The uploader's set must still be the one its run row
                # bound: checked here, under the lock, before any upload.
                upload_set(client, folder, newest, SEALED_FILES, expected_digest=digest)
            copied = newest.name
    except DriveFailure as failure:
        return {"accepted": False, "reason": failure.kind, "message": failure.message}
    return {"accepted": True, "copied_set": copied}


def _outcome(target, value, settings, configuration):
    """The installer's own classification of one check, word for word."""
    from .provider_check_worker import classify

    return classify(
        target,
        value,
        settings,
        profile=configuration.profile,
        base_url=configuration.parishsoft_base_url,
        redirect=configuration.mail_redirect,
    )


def execute_smoke(args):
    """Console entry: one fixed JSON line, or one generic refusal."""
    configure_logging()
    try:
        if args.config is None or args.target not in TARGETS:
            raise ConfigError("Configuration and a known target are required.")
        # Every profile runs the same checks (#1399): in LOCAL they reach
        # whatever is installed there (the fake ParishSoft, the mail catcher,
        # a test Slack token), through the installer's own classification.
        configuration = load_deployment(Path(args.config))
        if configuration.service_role not in {
            ServiceRole.WEB,
            ServiceRole.WORKER,
            ServiceRole.MAIL_DISPATCH,
            ServiceRole.BACKUP_WORKER,
            ServiceRole.BACKUP_UPLOADER,
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
        if configuration.service_role in {
            ServiceRole.BACKUP_WORKER,
            ServiceRole.BACKUP_UPLOADER,
        }:
            raise ConfigError("Run the smoke check inside a deployed consumer.")
        if args.target == "parishsoft":
            result = check_parishsoft(
                configuration, organization_id=int(args.organization_id)
            )
        elif args.target == "parishsoft_contact":
            result = check_parishsoft_contact(
                configuration,
                organization_id=int(args.organization_id),
                member_id=args.member_id,
                confirm_member_id=args.confirm_member_id,
                field=args.field,
                send=bool(args.send),
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
        partial = getattr(error, "result", None)
        if args.target == "parishsoft_contact" and isinstance(partial, dict):
            # A contact round trip interrupted after a write: its value-free
            # partial result says whether the original was put back.
            print(json.dumps({"target": args.target, **partial}, sort_keys=True))
        from .runtime_web import GoogleClockRefused

        if isinstance(error, GoogleClockRefused):
            # Fixed text with nothing from the client document (#1399).
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        print(
            "ERROR: smoke check refused or failed; check the target, the "
            "consumer's credential mount and the operator options",
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"target": args.target, "sent": False, **result}, sort_keys=True))
    return 0
