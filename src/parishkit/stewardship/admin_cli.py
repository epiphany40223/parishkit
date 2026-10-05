"""``pk-stewardship admin``: the Admin automation command line (ADM-11).

An operator, script or AI assistant on the deployment host runs Admin
commands as the named Administrator who approved an automation session in the
browser (see the Admin automation specification). Commands run inside the web
container, through the host wrapper ``tools/stewardship-ops/pk-admin``, which
keeps the session secret in an owner-only file on the host and sends it on
standard input as the first line (the preamble).

Every command:

- requires ``--config`` and ``--session-stdin`` (the wrapper supplies both);
- admits its process as the web profile on the web's own restricted SQL login,
  with the web's signing keyring, and refuses when that keyring differs from
  the one the running web loaded;
- prints exactly one JSON document (``pk-admin/1``) to standard output, and
  writes warnings and structured logs to standard error;
- exits with the specification's codes (0 done, 1 refused, 2 usage or
  admission, 3 unavailable, 4 confirmation not given, 5 no usable session or
  pairing not finished, 6 outcome unknown, 7 a watch stopped before its
  read finished: ``watch_timeout`` at ``--timeout``, ``watch_interrupted``
  on SIGINT).

With ``--watch SECONDS``, a progress read prints one document per poll
(newline-delimited JSON, ``final`` false until the last) and stops at a
terminal state (exit 0), at ``--timeout`` or SIGINT (exit 7, with the last
state), when the session ends (exit 5) or on an outage (exit 3). Through the
host wrapper, Ctrl-C ends only the client: ``docker exec`` forwards no
signal, so the watch in the container runs on (#598).

The session commands (``login start``, ``login wait``, ``logout``,
``whoami``, ``sessions`` and ``commands``) came first (PR 2); the read-only
status commands (``status``, ``task list``, ``task show``, ``send
progress``, ``send history``, ``schedule show``, ``go-live readiness`` and
``go-live progress``) follow (PR 3), built on the read models of
``admin_reads``. ``task show``, ``send progress`` and ``go-live progress``
take ``--watch``. Other areas join the same subparser tree in later pull
requests, each listed in the catalog with the pull request that added it.
"""

import argparse
import contextlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

from parishkit.config import ConfigError

SCHEMA = "pk-admin/1"
PREAMBLE_TAG = "pk-admin-session/1"
# The preamble is one line of at most 128 bytes plus the host digest.
PREAMBLE_LIMIT = 256
POLL_SECONDS = 2
PAIRING_WAIT_SECONDS = 600
# --watch: seconds between polls (the specification's minimum is two; at
# most five minutes, so a poll always lands well inside the command
# session's 60-minute idle limit), the default and maximum time a watch
# runs (the progress page's three-hour give-up limit), and how often a
# watch records activity on its command session.
WATCH_MINIMUM = 2
WATCH_MAXIMUM = 300
WATCH_TIMEOUT = 3 * 60 * 60
HEARTBEAT_SECONDS = 60


class UsageError(Exception):
    """The command line or its preamble is malformed; nothing ran (exit 2)."""


class CredentialMismatch(ConfigError):
    """The signing keyring differs from the running web's (exit 2)."""


class WatchTimeout(Exception):
    """A ``--watch`` reached its ``--timeout`` before a terminal state (exit 7)."""

    code = "watch_timeout"

    def __init__(self, model):
        """Keep the last read (None before the first), which the document shows."""
        super().__init__("The watch stopped.")
        self.model = model


class WatchInterrupted(WatchTimeout):
    """SIGINT stopped a ``--watch`` before a terminal state (exit 7).

    Run in the container directly, Ctrl-C delivers it; through the host
    wrapper it does not, since ``docker exec`` forwards no signal (#598).
    """

    code = "watch_interrupted"


class PairingNotFinished(Exception):
    """``login wait`` stopped before a usable session existed (exit 5)."""

    def __init__(self, code):
        """Keep the error code (``pairing_pending`` or ``pairing_expired``)."""
        super().__init__("The pairing is not finished.")
        self.code = code


# The message each error code shows; never exception text, values or paths.
MESSAGES = {
    "denied": "The command was refused; nothing changed.",
    "invalid": "The command's input is not valid; nothing changed.",
    "stale_version": "The record changed since it was read; read it again.",
    "not_available": "That is not available now; nothing changed.",
    "usage": "The command line is not valid; see --help.",
    "configuration": "The command could not start; check the web configuration.",
    "credential_mismatch": (
        "The web's signing key differs from this process's; retry after the "
        "web service is recreated."
    ),
    "unavailable": "A service is temporarily unavailable; nothing changed. Retry.",
    "busy": "Offline maintenance is in progress; nothing changed. Retry.",
    "internal": "An unexpected error stopped the read; retry once, then report it.",
    "confirmation_required": "The confirmation was not given; nothing changed.",
    "session_missing": "No automation session has this secret; pair again.",
    "session_ended": "This automation session has ended; pair again.",
    "pairing_pending": "The pairing is not approved yet; run login wait again.",
    "pairing_expired": "The pairing expired before it was approved; start again.",
    "outcome_unknown": "The outcome is unknown; read the status before retrying.",
    "partial": "The command was done in part; read the status before retrying.",
    "watch_timeout": "The watch timed out; the last state is shown.",
    "watch_interrupted": "The watch was stopped; the last state is shown.",
}
EXIT_CODES = {
    **dict.fromkeys(("denied", "invalid", "stale_version", "not_available"), 1),
    **dict.fromkeys(("usage", "configuration", "credential_mismatch"), 2),
    **dict.fromkeys(("unavailable", "busy", "internal"), 3),
    "confirmation_required": 4,
    **dict.fromkeys(
        ("session_missing", "session_ended", "pairing_pending", "pairing_expired"), 5
    ),
    **dict.fromkeys(("outcome_unknown", "partial"), 6),
    **dict.fromkeys(("watch_timeout", "watch_interrupted"), 7),
}


@dataclass(frozen=True)
class Preamble:
    """The first standard-input line: the session secret and the host digest.

    The secret stays out of ``repr`` so no traceback or log can show it.
    """

    secret: str = field(repr=False)
    host_digest: str


@dataclass(frozen=True)
class CommandSpec:
    """One command's catalog entry and handler.

    ``scope`` is the session scope it needs: ``none`` before a session exists
    (pairing), ``read_only`` for any session, ``full`` for a full-scope one.
    """

    name: str
    help: str
    handler: object
    scope: str
    changes_state: bool
    result_fields: tuple
    pr: int
    options: tuple = ()
    fresh_gated: bool = False
    prompts: bool = False
    request_key: bool = False
    expected_version: bool = False
    # Whether --watch repeats this read until it reaches a terminal state.
    watch: bool = False
    # The audit event a state-changing command records: by default its own
    # admin_cmd_<area>_<verb> type; the session commands record the session
    # events instead, and pairing records none until approval.
    audit_event: str | None = "default"


@dataclass
class AdminRuntime:
    """What an admitted process holds: the authority store and pairing store.

    It also serves as the ``service`` the shared Admin functions take (as
    the web's ``AuthRuntime``): ``store`` and ``configured()``.
    """

    store: object
    pairing: object
    public_origin: str
    setup_complete: object = None

    def configured(self):
        """Whether first setup finished, by the web's own rule (``AuthRuntime``)."""
        from .accounts.authentication import AuthRuntime

        return AuthRuntime(self.store, None, self.setup_complete).configured()


def iso(value):
    """A UTC ISO 8601 instant, or None."""
    return None if value is None else value.astimezone(UTC).isoformat()


def read_preamble(stream):
    """Read exactly the first line of standard input and parse it.

    Nothing after it is read here; later prompts and inputs read the same
    stream. A malformed line is a usage error that names no part of it.
    """
    from .accounts.automation_tokens import SECRET_PATTERN, valid_digest

    raw = stream.readline(PREAMBLE_LIMIT + 1)
    if len(raw) > PREAMBLE_LIMIT or not raw.endswith(b"\n"):
        raise UsageError("The session preamble is missing or too long.")
    try:
        parts = raw.decode("ascii").removesuffix("\n").split(" ")
    except UnicodeDecodeError:
        raise UsageError("The session preamble is malformed.") from None
    if (
        len(parts) != 3
        or parts[0] != PREAMBLE_TAG
        or SECRET_PATTERN.fullmatch(parts[1]) is None
        or not valid_digest(parts[2])
    ):
        raise UsageError("The session preamble is malformed.")
    return Preamble(parts[1], parts[2])


def configure_admin_process(configuration):
    """Assemble Django for the command line as the web does, and nothing else.

    The web's ``django_signing`` keyring (active key and verify-only
    fallbacks) signs the command session's data, so it is the web's, not a
    random key. The database is the web's login only; no HTTP server,
    download pool, worker or provider client is assembled. Returns the
    receipt of the signing keyring this process loaded.
    """
    from importlib import import_module

    import django
    from django.conf import settings

    from .accounts.key_files import parse_keyring, read_private
    from .accounts.metrics_credentials import credential_receipt
    from .runtime_database import database_settings, profile_settings

    if settings.configured:
        raise ConfigError("The admin command requires a fresh process.")
    if "django_signing" not in configuration.secrets:
        raise ConfigError("The web signing keyring is not mounted.")
    loaded = read_private(configuration.secrets["django_signing"])
    ring = parse_keyring(loaded, "django_signing")
    base = import_module("parishkit.stewardship.settings.base")
    values = {name: getattr(base, name) for name in dir(base) if name.isupper()}
    values.update(ring.django_settings())
    values["DATABASES"] = {"default": database_settings(configuration)}
    values["STEWARDSHIP_OPERATIONAL_POLICY"] = configuration.operational_alerts
    values.update(profile_settings(configuration))
    settings.configure(**values)
    django.setup()
    return credential_receipt(loaded, "django_signing")


@contextlib.contextmanager
def admitted(configuration):
    """Admit this process as ``engagement-backfill`` does, then yield its runtime.

    The admitted web profile, the lifecycle mounts, a non-offline startup
    lease and the web's own SQL login; every other profile and login is
    refused. The signing keyring must match the running web's published
    receipt, so a key rotation in progress refuses (exit 2). At most one
    database connection is held, and it is closed at exit.
    """
    # Only Django-free modules before configure_admin_process sets Django up;
    # a module that loads models is imported after it (a test checks this).
    from .accounts.authority import AuthorityStore
    from .accounts.configuration_schema import validate_sections
    from .deployment import ServiceRole
    from .runtime_paths import RuntimeLayout
    from .runtime_web import admit_lifecycle_mounts, valkey_client
    from .service_boundaries import admit_online_service
    from .startup_interlock import StartupLease

    if admit_online_service(configuration) is not ServiceRole.WEB:
        raise ConfigError("The admin command requires the admitted web profile.")
    admit_lifecycle_mounts(configuration)
    with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
        receipt = configure_admin_process(configuration)
        from django.db import connections

        from .accounts.automation_sessions import PairingStore
        from .accounts.setup_completion import setup_is_complete
        from .consumer_runtime import loaded_service_receipts
        from .runtime_grants import admit_runtime_database

        client = None
        try:
            admit_runtime_database(configuration)
            if loaded_service_receipts(configuration).get("django_signing") != receipt:
                raise CredentialMismatch("The web signing keyring differs.")
            client = valkey_client(configuration)
            yield AdminRuntime(
                store=AuthorityStore(
                    configuration.paths["authority"], validate_sections
                ),
                pairing=PairingStore(client),
                public_origin=configuration.public_origin,
                setup_complete=setup_is_complete,
            )
        finally:
            connections.close_all()
            if client is not None:
                client.connection_pool.disconnect()


def session_block(row):
    """The ``session`` member of a document: the session's id and deadline."""
    return (
        None if row is None else {"id": str(row.pk), "expires_at": iso(row.expires_at)}
    )


def warn_if_expiring(row, now, stderr):
    """Warn on standard error when the session has less than 72 hours left."""
    from .accounts.automation_sessions import WARNING_WINDOW

    if row is not None and row.expires_at - now < WARNING_WINDOW:
        hours = max(0, int((row.expires_at - now).total_seconds() // 3600))
        print(
            f"WARNING: this automation session expires in {hours} hours "
            f"({iso(row.expires_at)}); approve a new one before then.",
            file=stderr,
        )


# ---------------------------------------------------------------- commands


def login_start(args, preamble, runtime, context):
    """Store a pending pairing and print its user code at once (``final`` false).

    The secret's digest, the host digest and the operator's options go to
    Valkey for ten minutes; the secret itself never leaves this process.
    """
    from datetime import timedelta

    from django.urls import reverse

    from .accounts.automation_sessions import (
        PAIRING_SECONDS,
        database_now,
        display_code,
        pairing_request,
        secret_digest,
    )

    try:
        request = pairing_request(
            digest=secret_digest(preamble.secret),
            host_digest=preamble.host_digest,
            name=args.name,
            label=args.label,
            expect_email=args.expect_email,
            scope=args.scope,
            days=args.days,
        )
    except ValueError:
        raise UsageError("The pairing options are not valid.") from None
    # Everything that can fail (the database clock, the route) runs before
    # the pairing is stored, so a failed start never leaves a pairing that
    # an Administrator could approve with nobody waiting to collect it. The
    # deadline shown is therefore a moment early, never late.
    expires = database_now() + timedelta(seconds=PAIRING_SECONDS)
    approve_url = runtime.public_origin.rstrip("/") + reverse(
        "admin:automation_approval"
    )
    code = runtime.pairing.start(request)
    context["committed"] = True
    context["final"] = False
    return {
        "user_code": display_code(code),
        "approve_url": approve_url,
        "expires_at": iso(expires),
    }


def _session_document(row, *, name=None):
    """The session as ``login wait`` prints it: never the secret or a digest."""
    from .accounts.policy_models import PortalUser

    email = (
        PortalUser.objects.filter(pk=row.principal_id)
        .values_list("email", flat=True)
        .first()
    )
    document = {
        "id": str(row.pk),
        "label": row.label,
        "principal_email": email,
        "scope": row.scope,
        "expires_at": iso(row.expires_at),
    }
    if name is not None:
        document["name"] = name
    return document


def _collect(row, digest, preamble, runtime):
    """Collect an approved session once, under its row lock.

    The lock is taken before the pairing request is consumed, so two
    concurrent ``login wait`` runs with the same secret serialize: the first
    collects the session (``last_used_at`` set), the second then finds it
    collected and prints the same document. Returns ``(row, outcome, name)``:
    ``collected``, ``ended`` (revoked, expired, or refused because its
    principal is not the expected address or the host differs) or
    ``abandoned`` (approved, but the request expired before any wait took
    it, so the session is revoked as ``pairing_abandoned``).
    """
    from django.db import transaction
    from django.db.models import F

    from .accounts.automation_models import AutomationSession
    from .accounts.automation_sessions import database_now, end_session
    from .accounts.policy_models import PortalUser
    from .accounts.policy_schema import normalized_email

    with transaction.atomic():
        row = AutomationSession.objects.select_for_update().get(pk=row.pk)
        if row.revoked_at is not None or row.expires_at <= database_now():
            return row, "ended", None
        if row.last_used_at is not None:
            return row, "collected", None
        code = runtime.pairing.code_for(digest)
        request = None if code is None else runtime.pairing.request(code)
        if request is None or not runtime.pairing.consume(code, digest):
            end_session(row, reason="pairing_abandoned")
            return row, "abandoned", None
        email = (
            PortalUser.objects.filter(pk=row.principal_id)
            .values_list("email", flat=True)
            .first()
        )
        if (
            email is None
            or normalized_email(email) != request["expect_email"]
            or row.host_digest != preamble.host_digest
        ):
            end_session(row, reason="misused")
            return row, "ended", None
        AutomationSession.objects.filter(pk=row.pk).update(
            last_used_at=database_now(), version=F("version") + 1
        )
    row.refresh_from_db()
    return row, "collected", request["name"]


def login_wait(args, preamble, runtime, context):
    """Wait for the Administrator's approval, then collect the session.

    Checks every two seconds, up to ``--timeout`` (at most ten minutes, and no
    longer than the pending request lives). Once a session row with this
    secret's digest exists, ``_collect`` consumes both Valkey keys atomically
    and records the first use, or ends the session when it cannot be
    collected. While the request lives and is not approved, a timeout exits 5
    with ``pairing_pending`` so ``wait`` can run again, after logging what it
    waited for, the limit and the elapsed time; once the request has expired
    unapproved, the exit is ``pairing_expired``.
    """
    from django.db import connection

    from .accounts.automation_models import AutomationSession
    from .accounts.automation_sessions import SessionUnusable, secret_digest
    from .observability import Event, emit

    digest = secret_digest(preamble.secret)
    limit = args.timeout
    started = time.monotonic()
    while True:
        row = AutomationSession.objects.filter(secret_digest=digest).first()
        if row is not None:
            row, outcome, name = _collect(row, digest, preamble, runtime)
            # Collecting or ending the session is a durable change.
            context["committed"] = outcome != "collected" or name is not None
            if outcome == "abandoned":
                raise PairingNotFinished("pairing_expired")
            if outcome == "ended":
                raise SessionUnusable("session_ended")
            context["session"] = row
            return _session_document(row, name=name)
        code = runtime.pairing.code_for(digest)
        if code is None or runtime.pairing.request(code) is None:
            raise PairingNotFinished("pairing_expired")
        elapsed = time.monotonic() - started
        if elapsed >= limit:
            emit(
                Event.TASK_TIMED_OUT,
                level=logging.WARNING,
                timeout="automation_pairing_wait",
                limit_seconds=int(limit),
                elapsed_seconds=int(elapsed),
            )
            raise PairingNotFinished("pairing_pending")
        connection.close()
        time.sleep(min(POLL_SECONDS, max(0.0, limit - elapsed)))


def logout(args, preamble, runtime, context):
    """End this automation session (``logout``); the wrapper then deletes the file.

    The session was live at admission, but another ending (a revocation in
    the portal, for example) may commit before this one. Then nothing is
    changed, and the document says so with ``"ended": false``,
    ``"reason": "already_ended"`` and the ending that won. Either way the
    session is over, so the command succeeds and the file goes.
    """
    from django.db import transaction

    from .accounts.automation_models import AutomationSession
    from .accounts.automation_sessions import end_session

    caller = context["caller"]
    with transaction.atomic():
        ended = end_session(
            caller.automation_session,
            reason="logout",
            actor_id=caller.principal.identity,
        )
    context["committed"] = True
    if ended:
        return {"ended": True, "end_reason": "logout"}
    reason = (
        AutomationSession.objects.filter(pk=caller.automation_session.pk)
        .values_list("end_reason", flat=True)
        .first()
    )
    return {"ended": False, "reason": "already_ended", "end_reason": reason}


def whoami(args, preamble, runtime, context):
    """Print the session: its id, label, principal, current roles, scope and use."""
    caller = context["caller"]
    row = caller.automation_session
    document = _session_document(row)
    document.update(
        roles=sorted(caller.principal.roles),
        last_used_at=iso(row.last_used_at),
    )
    return document


def sessions(args, preamble, runtime, context):
    """List this Administrator's sessions, live and ended in the last 30 days."""
    from .accounts.automation_sessions import database_now, sessions_of

    caller = context["caller"]
    rows = sessions_of(caller.principal.identity, database_now())
    return {
        "sessions": [
            {
                "id": str(row["id"]),
                "label": row["label"],
                "scope": row["scope"],
                "approved_at": iso(row["created_at"]),
                "expires_at": iso(row["expires_at"]),
                "last_used_at": iso(row["last_used_at"]),
                "live": bool(row["live"]),
                "ended_at": iso(row["revoked_at"]),
                "end_reason": row["end_reason"],
                "current": row["id"] == caller.automation_session_id,
            }
            for row in rows
        ]
    }


def commands(args, preamble, runtime, context):
    """Print the machine-readable catalog of every command."""
    return {"commands": catalog()}


def status(args, preamble, runtime, context):
    """The Admin home summary with background and presence counts."""
    from .admin_reads import read_status

    return read_status(context["caller"], runtime, audit=context["audit"])


def task_list(args, preamble, runtime, context):
    """One page of Background work, filtered and sorted as the page."""
    from .admin_reads import query, read_task_list

    parameters = query(
        state=args.state,
        task_type=args.type,
        page=args.page,
        size=args.size,
        sort=args.sort,
    )
    return read_task_list(
        context["caller"], runtime, parameters, audit=context["audit"]
    )


def task_show(args, preamble, runtime, context):
    """One task and a page of its history."""
    from .admin_reads import query, read_task

    parameters = query(page=args.page, size=args.size, sort=args.sort)
    return read_task(
        context["caller"], runtime, args.task_id, parameters, audit=context["audit"]
    )


def send_progress(args, preamble, runtime, context):
    """The Family email send in progress, or the most recent one."""
    from .admin_reads import read_send_progress

    return read_send_progress(context["caller"], runtime, audit=context["audit"])


def send_history(args, preamble, runtime, context):
    """One page of the current campaign's Family email sends."""
    from .admin_reads import query, read_send_history

    parameters = query(page=args.page, size=args.size)
    return read_send_history(
        context["caller"], runtime, parameters, audit=context["audit"]
    )


def schedule_show(args, preamble, runtime, context):
    """The campaign's dates and mail schedules."""
    from .admin_reads import read_schedule

    return read_schedule(context["caller"], runtime, args.campaign)


def go_live_readiness(args, preamble, runtime, context):
    """Whether the current Testing draft is ready to go live."""
    from .admin_reads import read_go_live_readiness

    return read_go_live_readiness(context["caller"], runtime, args.campaign)


def go_live_progress(args, preamble, runtime, context):
    """The Production activation progress of the current campaign."""
    from .admin_reads import read_go_live_progress

    return read_go_live_progress(context["caller"], runtime, args.campaign)


def _uuid(value):
    """A canonical UUID option value; anything else is a usage error."""
    from uuid import UUID

    try:
        parsed = UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not a UUID") from None
    if str(parsed) != value:
        raise argparse.ArgumentTypeError("not a canonical UUID")
    return parsed


def _page_options(parser, *, sort=True):
    """The page's table window: page number, page size and, if any, sort."""
    parser.add_argument("--page", type=int, help="page number (default 1)")
    parser.add_argument("--size", type=int, help="rows per page, as the page offers")
    if sort:
        parser.add_argument("--sort", help="a column token as the page uses")


def _watch_options(parser):
    """--watch and --timeout for a progress read."""
    parser.add_argument(
        "--watch",
        type=int,
        metavar="SECONDS",
        help=f"repeat every SECONDS ({WATCH_MINIMUM} to {WATCH_MAXIMUM}) until done",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help=f"with --watch: stop after this many seconds (at most {WATCH_TIMEOUT})",
    )


# Background work's state filter: the page's two groupings and every task
# state. Spelled out because the parser is built before Django is set up,
# when ``jobs.models`` cannot be imported; a test keeps it equal to
# ``jobs.models.TASK_STATES``.
TASK_STATE_FILTERS = (
    "nonterminal",
    "all",
    "queued",
    "running",
    "retry_wait",
    "abandoned",
    "succeeded",
    "failed",
    "cancelled",
)


def _task_list_options(parser):
    """Options of ``task list``: the Background work page's filters."""
    parser.add_argument(
        "--state",
        choices=TASK_STATE_FILTERS,
        help="tasks in this state (default nonterminal)",
    )
    parser.add_argument("--type", help="tasks of this internal type")
    _page_options(parser)


def _task_show_options(parser):
    """Options of ``task show``: the task and its history window."""
    parser.add_argument("task_id", type=_uuid, metavar="TASK_ID")
    _page_options(parser)
    _watch_options(parser)


def _schedule_options(parser):
    """Options of ``schedule show`` and ``go-live readiness``: the campaign."""
    parser.add_argument(
        "--campaign", type=_uuid, help="the campaign (default: the current one)"
    )


def _go_live_progress_options(parser):
    """Options of ``go-live progress``: the campaign, and --watch."""
    _schedule_options(parser)
    _watch_options(parser)


def _pairing_options(parser):
    """Options of ``login start``; the wrapper passes the same ones."""
    parser.add_argument("--name", required=True, help="the session file's name")
    parser.add_argument("--label", required=True, help="what the session is for")
    parser.add_argument(
        "--expect-email", required=True, help="the approving Administrator's address"
    )
    parser.add_argument("--scope", required=True, choices=("read-only", "full"))
    parser.add_argument("--days", type=int, default=30, help="1 to 30 (default 30)")


def _wait_options(parser):
    """Options of ``login wait``."""
    parser.add_argument("--name", help="the session file's name (wrapper only)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=PAIRING_WAIT_SECONDS,
        help="seconds to wait, at most 600 (default 600)",
    )


COMMANDS = (
    CommandSpec(
        "login start",
        "Start pairing a new automation session.",
        login_start,
        "none",
        True,
        ("user_code", "approve_url", "expires_at"),
        2,
        options=(_pairing_options,),
        audit_event=None,
    ),
    CommandSpec(
        "login wait",
        "Wait for the Administrator to approve the pairing.",
        login_wait,
        "none",
        True,
        ("id", "name", "label", "principal_email", "scope", "expires_at"),
        2,
        options=(_wait_options,),
        audit_event=None,
    ),
    CommandSpec(
        "logout",
        "End this automation session.",
        logout,
        "read_only",
        True,
        ("ended", "end_reason"),
        2,
        audit_event="automation_session_ended",
    ),
    CommandSpec(
        "whoami",
        "Show this session and its Administrator's current roles.",
        whoami,
        "read_only",
        False,
        (
            "id",
            "label",
            "principal_email",
            "roles",
            "scope",
            "expires_at",
            "last_used_at",
        ),
        2,
    ),
    CommandSpec(
        "sessions",
        "List this Administrator's automation sessions.",
        sessions,
        "read_only",
        False,
        ("sessions",),
        2,
    ),
    CommandSpec(
        "commands",
        "Print the machine-readable command catalog.",
        commands,
        "none",
        False,
        ("commands",),
        2,
    ),
)


def _read_specs():
    """The read-only status commands (PR 3a and 3b): any scope, no state change."""
    from .admin_reads import (
        GoLiveProgress,
        GoLiveReadiness,
        ScheduleShow,
        SendHistory,
        SendProgressRead,
        Status,
        TaskList,
        TaskShow,
    )

    return (
        CommandSpec(
            "status",
            "Show the campaign, source, backup and background work status.",
            status,
            "read_only",
            False,
            Status.field_names(),
            3,
            audit_event="dashboard_viewed",
        ),
        CommandSpec(
            "task list",
            "List Background work tasks.",
            task_list,
            "read_only",
            False,
            TaskList.field_names(),
            3,
            options=(_task_list_options,),
            audit_event="background_viewed",
        ),
        CommandSpec(
            "task show",
            "Show one background task and its history.",
            task_show,
            "read_only",
            False,
            TaskShow.field_names(),
            3,
            options=(_task_show_options,),
            audit_event="background_viewed",
            watch=True,
        ),
        CommandSpec(
            "send progress",
            "Show the Family email send in progress, or the latest one.",
            send_progress,
            "read_only",
            False,
            SendProgressRead.field_names(),
            3,
            options=(_watch_options,),
            audit_event="delivery_viewed",
            watch=True,
        ),
        CommandSpec(
            "send history",
            "List the current campaign's Family email sends.",
            send_history,
            "read_only",
            False,
            SendHistory.field_names(),
            3,
            options=(lambda parser: _page_options(parser, sort=False),),
            audit_event="delivery_viewed",
        ),
        CommandSpec(
            "schedule show",
            "Show the campaign's dates and mail schedules.",
            schedule_show,
            "read_only",
            False,
            ScheduleShow.field_names(),
            3,
            options=(_schedule_options,),
            audit_event=None,
        ),
        # The go-live reads (PR 3b) matter again for the next campaign.
        CommandSpec(
            "go-live readiness",
            "Show whether the Testing draft is ready to go live.",
            go_live_readiness,
            "read_only",
            False,
            GoLiveReadiness.field_names(),
            3,
            options=(_schedule_options,),
            audit_event=None,
        ),
        CommandSpec(
            "go-live progress",
            "Show the Production activation progress.",
            go_live_progress,
            "read_only",
            False,
            GoLiveProgress.field_names(),
            3,
            options=(_go_live_progress_options,),
            audit_event=None,
            watch=True,
        ),
    )


COMMANDS = COMMANDS + _read_specs()
BY_NAME = {spec.name: spec for spec in COMMANDS}


class _Parser(argparse.ArgumentParser):
    """An argparse parser that never prints raw errors, which may echo values."""

    def __init__(self, *args, **kwargs):
        """Refuse abbreviated options, so a typo never selects another option."""
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        """Raise instead of printing argparse's message, which may hold input."""
        raise UsageError("The command line is not valid.")


def build_parser():
    """The subparser tree: ``<area> <verb>`` or a one-word command."""
    parser = _Parser(
        prog="pk-stewardship admin",
        description="Admin commands acting as an approved Administrator.",
    )
    areas = parser.add_subparsers(dest="area", parser_class=_Parser)
    groups = {}
    for spec in COMMANDS:
        words = spec.name.split(" ")
        if len(words) == 1:
            command = areas.add_parser(words[0], help=spec.help)
        else:
            if words[0] not in groups:
                group = areas.add_parser(words[0], help=f"{words[0]} commands")
                groups[words[0]] = group.add_subparsers(
                    dest="verb", parser_class=_Parser
                )
            command = groups[words[0]].add_parser(words[1], help=spec.help)
        command.set_defaults(command_name=spec.name)
        # load_deployment takes a Path: a plain string reads the YAML and then
        # fails on Path methods, which classify reports as "internal" (#609).
        command.add_argument(
            "--config", type=Path, required=True, help="web configuration file"
        )
        command.add_argument(
            "--session-stdin",
            action="store_true",
            required=True,
            help="read the session preamble from standard input",
        )
        for add in spec.options:
            add(command)
    return parser


def catalog():
    """Each command with its scope, flags, options and result fields.

    Generated from the subparser tree, so a command cannot be added without
    appearing here; a test keeps it complete.
    """
    parser = build_parser()
    entries = []
    for spec in COMMANDS:
        command = parser
        for word in spec.name.split(" "):
            action = next(
                item
                for item in command._actions
                if isinstance(item, argparse._SubParsersAction)
            )
            command = action.choices[word]
        options = [
            {
                "name": max(item.option_strings, key=len),
                "required": bool(item.required),
                "takes_value": item.nargs != 0,
                "choices": list(item.choices) if item.choices else None,
            }
            for item in command._actions
            if item.option_strings and item.dest != "help"
        ]
        arguments = [
            item.metavar or item.dest
            for item in command._actions
            if not item.option_strings
            and not isinstance(item, argparse._SubParsersAction)
        ]
        entries.append(
            {
                "name": spec.name,
                "scope": spec.scope,
                "changes_state": spec.changes_state,
                "fresh_gated": spec.fresh_gated,
                "prompts": spec.prompts,
                "request_key": spec.request_key,
                "expected_version": spec.expected_version,
                "audit_event": command_event_type(spec.name)
                if spec.audit_event == "default" and spec.changes_state
                else (None if spec.audit_event == "default" else spec.audit_event),
                "options": options,
                "arguments": arguments,
                "watch": spec.watch,
                "result_fields": list(spec.result_fields),
                "pr": spec.pr,
            }
        )
    return entries


def command_event_type(name):
    """The audit event type a state-changing command records (see the spec)."""
    from .accounts.automation_tokens import command_event_type as event_type

    return event_type(name)


# ------------------------------------------------------------------ running


def classify(error, *, admitted_process, changed, committed=False):
    """Map an exception to its error code, in the specification's order.

    ``admitted_process`` says whether admission finished (a ``ConfigError``
    before it is a configuration error, after it a domain refusal);
    ``changed`` whether a state-changing command may have committed;
    ``committed`` whether it did. Any unexpected error after a durable
    commit (an outage while closing the command session after ``logout``,
    for example) is ``outcome_unknown`` (exit 6): nothing guarantees that
    nothing changed.
    """
    from django.apps import apps
    from django.core.exceptions import ObjectDoesNotExist
    from django.db import DatabaseError, OperationalError
    from redis.exceptions import RedisError

    from .accounts.authority import AuthorityChanging
    from .accounts.automation_tokens import SessionUnusable
    from .accounts.limiting import LimiterUnavailable
    from .admin_reads import NotAvailable, Unavailable
    from .observability import _guard_refusal
    from .startup_interlock import StartupBusy

    # Before admission sets up Django, no domain code has run, so no stale
    # record can be refused; ``storage`` imports models and cannot load then.
    StaleRecordError = ()
    if apps.ready:
        from .storage import StaleRecordError

    if committed and not isinstance(error, (SessionUnusable, PairingNotFinished)):
        return "outcome_unknown"
    if isinstance(error, WatchTimeout):
        return error.code
    if isinstance(error, StartupBusy):
        return "busy"
    # A configuration change activating, or a restore under review, is as
    # temporary as an outage: nothing was read, retry.
    if isinstance(
        error,
        (
            LimiterUnavailable,
            RedisError,
            OperationalError,
            AuthorityChanging,
            Unavailable,
        ),
    ):
        return "unavailable"
    if isinstance(error, DatabaseError) and not _guard_refusal(error):
        return "unavailable"
    if isinstance(error, (UsageError, CredentialMismatch)):
        return "usage" if isinstance(error, UsageError) else "credential_mismatch"
    if isinstance(error, ConfigError) and not admitted_process:
        return "configuration"
    if isinstance(error, PairingNotFinished):
        return error.code
    if isinstance(error, SessionUnusable):
        return error.code
    if isinstance(error, StaleRecordError):
        return "stale_version"
    if isinstance(error, NotAvailable):
        return "not_available"
    # A missing row says nothing changed only for a command that cannot
    # change anything; a state-changing command falls through to the end.
    if isinstance(error, ObjectDoesNotExist) and not changed:
        return "not_available"
    if isinstance(error, (PermissionError, DatabaseError)):
        return "denied"
    if isinstance(error, (ValueError, ConfigError)):
        return "invalid"
    return "outcome_unknown" if changed else "internal"


def document(name, correlation_id, *, ok, final=True, session=None, result=None):
    """The one JSON document a command prints."""
    value = {
        "schema": SCHEMA,
        "command": name,
        "correlation_id": str(correlation_id),
        "ok": ok,
        "final": final,
        "session": session,
    }
    if ok:
        value["result"] = result if result is not None else {}
    return value


def run(args, *, stdin, stdout, stderr):
    """Admit the process and the session, run one command, print its document."""
    from .deployment import load_deployment
    from .observability import correlation

    spec = BY_NAME[args.command_name]
    with correlation() as correlation_id:

        def emit(result, *, final):
            """Print one successful document; a read model prints its projection."""
            if hasattr(result, "to_document"):
                result = result.to_document()
            output = document(
                spec.name,
                correlation_id,
                ok=True,
                final=final,
                session=session_block(context.get("session")),
                result=result,
            )
            print(json.dumps(output, sort_keys=True), file=stdout, flush=True)

        # "audit": only the first read of a --watch records the page's view.
        context = {"final": True, "audit": True}
        admitted_process = False
        caller = None
        try:
            preamble = read_preamble(stdin)
            if spec.name == "commands":
                result = commands(args, preamble, None, context)
                print(
                    json.dumps(
                        document(spec.name, correlation_id, ok=True, result=result),
                        sort_keys=True,
                    ),
                    file=stdout,
                    flush=True,
                )
                return 0
            configuration = load_deployment(args.config)
            with ADMISSION(configuration) as runtime:
                admitted_process = True
                if spec.scope != "none":
                    caller = admit_session(spec, preamble, runtime, stderr)
                    context["caller"] = caller
                    context["session"] = caller.automation_session
                try:
                    if getattr(args, "watch", None) is not None:
                        result = watch(spec, args, preamble, runtime, context, emit)
                    else:
                        result = spec.handler(args, preamble, runtime, context)
                finally:
                    if caller is not None:
                        from .accounts.automation_sessions import (
                            close_command_session,
                        )

                        close_command_session(caller.portal_session)
            emit(result, final=context["final"])
            return 0
        except Exception as error:
            code = classify(
                error,
                admitted_process=admitted_process,
                changed=spec.changes_state and caller is not None,
                committed=context.get("committed", False),
            )
            output = document(
                spec.name,
                correlation_id,
                ok=False,
                session=session_block(context.get("session")),
            )
            output["error"] = {"code": code, "message": MESSAGES[code]}
            if isinstance(error, WatchTimeout) and error.model is not None:
                # The last state read, as the specification asks.
                output["result"] = error.model.to_document()
            print(json.dumps(output, sort_keys=True), file=stdout, flush=True)
            return EXIT_CODES[code]


def watch(spec, args, preamble, runtime, context, emit):
    """Repeat a passive read every ``--watch`` seconds until it is terminal.

    Each poll admits the command again through its command session, so a
    session that ended, expired or changed role stops the watch (exit 5).
    Only the first read records the page's view event. Every document but
    the last is printed here with ``final`` false; the caller prints the
    last one. The database connection is closed between polls, and the
    command session gets a heartbeat at most once a minute so its idle limit
    holds. Past ``--timeout`` the watch logs what it waited for, the limit
    and the elapsed time, and stops with ``watch_timeout`` (exit 7) and the
    last state; SIGINT stops it the same way with ``watch_interrupted``.
    """
    from django.db import connection

    from .accounts.automation_sessions import heartbeat
    from .observability import Event
    from .observability import emit as log

    started = beat = time.monotonic()
    model = None
    try:
        while True:
            model = spec.handler(args, preamble, runtime, context)
            context["audit"] = False
            if model.terminal:
                return model
            elapsed = time.monotonic() - started
            if elapsed >= args.timeout:
                log(
                    Event.TASK_TIMED_OUT,
                    level=logging.WARNING,
                    timeout="automation_watch",
                    limit_seconds=int(args.timeout),
                    elapsed_seconds=int(elapsed),
                )
                raise WatchTimeout(model)
            emit(model, final=False)
            connection.close()
            time.sleep(min(args.watch, max(0.0, args.timeout - elapsed)))
            if time.monotonic() - beat >= HEARTBEAT_SECONDS:
                heartbeat(context["caller"].portal_session)
                beat = time.monotonic()
    except KeyboardInterrupt:
        # SIGINT ends a watch like a timeout: a final document with the last
        # state, no traceback; the command session is still closed.
        raise WatchInterrupted(model) from None


def admit_session(spec, preamble, runtime, stderr):
    """Admit the command through its automation session and check its scope."""
    from .accounts.admin_caller import AdminCaller
    from .accounts.automation_sessions import database_now

    caller = AdminCaller.from_automation(
        preamble.secret,
        preamble.host_digest,
        store=runtime.store,
        pairing=runtime.pairing,
    )
    if spec.scope == "full" and caller.read_only:
        from .accounts.automation_sessions import close_command_session

        close_command_session(caller.portal_session)
        raise PermissionError("This command needs a full-scope session.")
    warn_if_expiring(caller.automation_session, database_now(), stderr)
    return caller


# Replaced in tests, which assemble Django and Valkey themselves.
ADMISSION = admitted


def _limit_core_dumps():
    """Keep a crash from writing the session secret to a core file."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


def main(argv=None, *, stdin=None, stdout=None, stderr=None):
    """Console entry for ``pk-stewardship admin``: parse, run, exit with its code."""
    from .observability import configure_logging

    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    _limit_core_dumps()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if getattr(args, "command_name", None) is None:
            raise UsageError("A command is required.")
        if args.command_name == "login wait" and not 1 <= args.timeout <= 600:
            raise UsageError("The timeout is 1 to 600 seconds.")
        if hasattr(args, "watch"):
            # --timeout belongs to --watch; alone it would silently do nothing.
            if args.watch is None:
                if args.timeout is not None:
                    raise UsageError("--timeout needs --watch.")
            else:
                if args.timeout is None:
                    args.timeout = WATCH_TIMEOUT
                if not (
                    WATCH_MINIMUM <= args.watch <= WATCH_MAXIMUM
                    and 1 <= args.timeout <= WATCH_TIMEOUT
                ):
                    raise UsageError("The watch interval or timeout is out of range.")
    except UsageError:
        output = {
            "schema": SCHEMA,
            "command": None,
            "correlation_id": None,
            "ok": False,
            "final": True,
            "session": None,
            "error": {"code": "usage", "message": MESSAGES["usage"]},
        }
        print(json.dumps(output, sort_keys=True), file=stdout, flush=True)
        return 2
    configure_logging()
    return run(args, stdin=stdin, stdout=stdout, stderr=stderr)
